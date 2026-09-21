from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple
from uuid import UUID, uuid4

from filelock import FileLock
from pydantic import BaseModel, Field

from common.paths import GRAPHS_DIR, pool_graph_file


Direction = Literal["out", "in", "both"]

# Retention caps. When exceeded, prune the lowest-degree nodes first (orphans
# go before well-connected hubs); cascading edge removal keeps the graph valid.
MAX_NODES_PER_POOL = 500
MAX_EDGES_PER_POOL = 1500


def _normalize(s: str) -> str:
    return s.strip().lower()


def _canon(s: str) -> str:
    """Separator-insensitive form for twin detection: 'test_project' == 'test project'."""
    return " ".join(_normalize(s).replace("_", " ").replace("-", " ").split())


class Node(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    type: str
    name: str
    properties: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        object.__setattr__(self, "updated_at", datetime.now(timezone.utc))


class Edge(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    target_id: UUID
    relation: str
    properties: dict = Field(default_factory=dict)
    weight: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        object.__setattr__(self, "updated_at", datetime.now(timezone.utc))


def _json_default(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o)!r} is not JSON serializable")


class GraphStore:
    """File-based store for a knowledge graph, scoped to one shared-memory pool.

    Storage shape: {"nodes": [...], "edges": [...]}.
    Node identity = (type, name) lowercased — `upsert_node` merges by that key.
    Edge identity = (source_id, target_id, relation) — `add_edge` merges by that key.
    """

    def __init__(self, pool_id: str):
        self.pool_id = str(pool_id)
        self.path: Path = pool_graph_file(self.pool_id)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"nodes": [], "edges": []})

    # ── Load / save ───────────────────────────────────────────────────────────

    def load(self, timeout: float = 10.0) -> Tuple[List[Node], List[Edge]]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def _load_unlocked(self) -> Tuple[List[Node], List[Edge]]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return [], []
            data = json.loads(text)
            nodes = [Node(**n) for n in data.get("nodes", [])]
            edges = [Edge(**e) for e in data.get("edges", [])]
            return nodes, edges
        except Exception:
            return [], []

    def _atomic_write(self, payload: dict) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def _save_unlocked(self, nodes: List[Node], edges: List[Edge]) -> None:
        nodes, edges = _apply_retention(nodes, edges)
        self._atomic_write({
            "nodes": [n.model_dump() for n in nodes],
            "edges": [e.model_dump() for e in edges],
        })

    # ── Read helpers ──────────────────────────────────────────────────────────

    def get_node_by_id(self, node_id: UUID | str, timeout: float = 10.0) -> Optional[Node]:
        nid = str(node_id)
        nodes, _ = self.load(timeout=timeout)
        for n in nodes:
            if str(n.id) == nid:
                return n
        return None

    def get_node(self, type: str, name: str, timeout: float = 10.0) -> Optional[Node]:
        t, nm = _normalize(type), _normalize(name)
        nodes, _ = self.load(timeout=timeout)
        for n in nodes:
            if _normalize(n.type) == t and _normalize(n.name) == nm:
                return n
        return None

    def find_nodes_by_name(self, name: str, timeout: float = 10.0) -> List[Node]:
        """All nodes whose normalized name matches, regardless of type."""
        nm = _normalize(name)
        nodes, _ = self.load(timeout=timeout)
        return [n for n in nodes if _normalize(n.name) == nm]

    def find_twins(self, name: str, timeout: float = 10.0) -> List[Node]:
        """All nodes matching `name` under separator-insensitive comparison
        ('emberglass_wand' matches 'emberglass wand'), regardless of type."""
        cn = _canon(name)
        nodes, _ = self.load(timeout=timeout)
        return [n for n in nodes if _canon(n.name) == cn]

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert_node(
        self,
        type: str,
        name: str,
        properties: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> Tuple[Node, str]:
        """Create or merge a node by (type, name). Returns (node, mode) where
        mode is 'created' or 'merged'."""
        t, nm = _normalize(type), _normalize(name)
        with FileLock(str(self.lock_path), timeout=timeout):
            nodes, edges = self._load_unlocked()
            for i, n in enumerate(nodes):
                if _normalize(n.type) == t and _normalize(n.name) == nm:
                    if properties:
                        n.properties = {**n.properties, **properties}
                    n.touch()
                    nodes[i] = n
                    self._save_unlocked(nodes, edges)
                    return n, "merged"
            new_node = Node(type=t, name=nm, properties=properties or {})
            nodes.append(new_node)
            self._save_unlocked(nodes, edges)
            return new_node, "created"

    def add_edge(
        self,
        source_id: UUID | str,
        target_id: UUID | str,
        relation: str,
        properties: Optional[dict] = None,
        weight: Optional[float] = None,
        timeout: float = 10.0,
    ) -> Tuple[Edge, str]:
        """Create or merge an edge by (source_id, target_id, relation).
        Returns (edge, mode)."""
        rel = _normalize(relation)
        sid, tid = str(source_id), str(target_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            nodes, edges = self._load_unlocked()
            node_ids = {str(n.id) for n in nodes}
            if sid not in node_ids:
                raise ValueError(f"source_id {sid} not found")
            if tid not in node_ids:
                raise ValueError(f"target_id {tid} not found")
            for i, e in enumerate(edges):
                if (
                    str(e.source_id) == sid
                    and str(e.target_id) == tid
                    and _normalize(e.relation) == rel
                ):
                    if properties:
                        e.properties = {**e.properties, **properties}
                    if weight is not None:
                        e.weight = weight
                    e.touch()
                    edges[i] = e
                    self._save_unlocked(nodes, edges)
                    return e, "merged"
            new_edge = Edge(
                source_id=UUID(sid),
                target_id=UUID(tid),
                relation=rel,
                properties=properties or {},
                weight=weight,
            )
            edges.append(new_edge)
            self._save_unlocked(nodes, edges)
            return new_edge, "created"

    def merge_nodes(self, keep_id: UUID | str, drop_id: UUID | str, timeout: float = 10.0) -> Optional[Node]:
        """Merge node `drop_id` into node `keep_id` and delete it.

        Properties: keep's values win on conflict; drop fills in missing keys.
        Edges touching drop are re-pointed to keep; self-loops produced by the
        re-point are removed and duplicate (source, target, relation) edges are
        collapsed. Returns the kept node, or None if either id is missing.
        """
        kid, did = str(keep_id), str(drop_id)
        if kid == did:
            return None
        with FileLock(str(self.lock_path), timeout=timeout):
            nodes, edges = self._load_unlocked()
            by_id = {str(n.id): n for n in nodes}
            keep, drop = by_id.get(kid), by_id.get(did)
            if keep is None or drop is None:
                return None

            keep.properties = {**drop.properties, **keep.properties}
            keep.touch()

            seen: set[tuple] = set()
            new_edges: List[Edge] = []
            for e in edges:
                sid = kid if str(e.source_id) == did else str(e.source_id)
                tid = kid if str(e.target_id) == did else str(e.target_id)
                if sid == tid:
                    continue  # self-loop created by the re-point
                key = (sid, tid, _normalize(e.relation))
                if key in seen:
                    continue
                seen.add(key)
                e.source_id = UUID(sid)
                e.target_id = UUID(tid)
                new_edges.append(e)

            new_nodes = [n for n in nodes if str(n.id) != did]
            self._save_unlocked(new_nodes, new_edges)
            return keep

    def delete_node(self, node_id: UUID | str, timeout: float = 10.0) -> bool:
        """Delete a node and any edges that touch it."""
        nid = str(node_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            nodes, edges = self._load_unlocked()
            new_nodes = [n for n in nodes if str(n.id) != nid]
            if len(new_nodes) == len(nodes):
                return False
            new_edges = [
                e for e in edges
                if str(e.source_id) != nid and str(e.target_id) != nid
            ]
            self._save_unlocked(new_nodes, new_edges)
            return True

    def delete_edge(self, edge_id: UUID | str, timeout: float = 10.0) -> bool:
        eid = str(edge_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            nodes, edges = self._load_unlocked()
            new_edges = [e for e in edges if str(e.id) != eid]
            if len(new_edges) == len(edges):
                return False
            self._save_unlocked(nodes, new_edges)
            return True

    # ── Traversal ─────────────────────────────────────────────────────────────

    def neighbors(
        self,
        node_id: UUID | str,
        *,
        relation: Optional[str] = None,
        direction: Direction = "out",
        limit: int = 50,
        timeout: float = 10.0,
    ) -> List[Tuple[Edge, Node]]:
        """Return (edge, neighbor) pairs from this node."""
        nid = str(node_id)
        rel = _normalize(relation) if relation else None
        nodes, edges = self.load(timeout=timeout)
        by_id = {str(n.id): n for n in nodes}
        out: List[Tuple[Edge, Node]] = []
        for e in edges:
            if rel and _normalize(e.relation) != rel:
                continue
            if direction in ("out", "both") and str(e.source_id) == nid:
                neighbor = by_id.get(str(e.target_id))
                if neighbor:
                    out.append((e, neighbor))
            if direction in ("in", "both") and str(e.target_id) == nid:
                neighbor = by_id.get(str(e.source_id))
                if neighbor:
                    out.append((e, neighbor))
            if len(out) >= limit:
                break
        return out

    def traverse(
        self,
        start_node_id: UUID | str,
        *,
        max_depth: int = 2,
        relation_filter: Optional[List[str]] = None,
        direction: Direction = "out",
        limit_per_level: int = 20,
        timeout: float = 10.0,
    ) -> Tuple[List[Node], List[Edge]]:
        """Breadth-first traversal. Returns (visited_nodes, traversed_edges).

        max_depth is capped at 3 to bound output size.
        """
        max_depth = max(1, min(int(max_depth), 3))
        rel_set = {_normalize(r) for r in relation_filter} if relation_filter else None
        sid = str(start_node_id)

        nodes, edges = self.load(timeout=timeout)
        by_id = {str(n.id): n for n in nodes}
        if sid not in by_id:
            return [], []

        # Adjacency map
        adj: dict[str, list[Edge]] = {}
        for e in edges:
            adj.setdefault(str(e.source_id), []).append(e)
            adj.setdefault(str(e.target_id), []).append(e)

        visited_nodes: dict[str, Node] = {sid: by_id[sid]}
        traversed_edges: dict[str, Edge] = {}
        frontier: deque[Tuple[str, int]] = deque([(sid, 0)])

        while frontier:
            current_id, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            level_count = 0
            for e in adj.get(current_id, []):
                if rel_set and _normalize(e.relation) not in rel_set:
                    continue
                next_id: Optional[str] = None
                if direction in ("out", "both") and str(e.source_id) == current_id:
                    next_id = str(e.target_id)
                elif direction in ("in", "both") and str(e.target_id) == current_id:
                    next_id = str(e.source_id)
                if next_id is None:
                    continue
                if str(e.id) not in traversed_edges:
                    traversed_edges[str(e.id)] = e
                if next_id not in visited_nodes and next_id in by_id:
                    visited_nodes[next_id] = by_id[next_id]
                    frontier.append((next_id, depth + 1))
                    level_count += 1
                    if level_count >= limit_per_level:
                        break

        return list(visited_nodes.values()), list(traversed_edges.values())

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self, timeout: float = 10.0) -> dict:
        nodes, edges = self.load(timeout=timeout)
        types: dict[str, int] = {}
        relations: dict[str, int] = {}
        for n in nodes:
            types[n.type] = types.get(n.type, 0) + 1
        for e in edges:
            relations[e.relation] = relations.get(e.relation, 0) + 1
        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_cap": MAX_NODES_PER_POOL,
            "edge_cap": MAX_EDGES_PER_POOL,
            "types": types,
            "relations": relations,
        }


def merge_slot_duplicates(pool_id: str) -> dict:
    """Merge generic `slot`-typed mirror nodes into same-name entity nodes.

    For every node of type "slot" that shares its name with a node of any other
    type (e.g. ("slot", "emberglass_wand") next to ("item", "emberglass_wand")),
    the slot node is merged into the typed entity node — properties combined,
    edges re-pointed — and removed. Returns a summary; never raises.
    """
    summary: dict = {"merged": [], "errors": []}
    try:
        store = GraphStore(pool_id)
        nodes, _ = store.load()
        slot_nodes = [n for n in nodes if n.type == "slot"]
        for sn in slot_nodes:
            try:
                twins = [
                    n for n in store.find_twins(sn.name)
                    if n.type != "slot" and str(n.id) != str(sn.id)
                ]
                if not twins:
                    continue
                keep = twins[0]
                if store.merge_nodes(keep.id, sn.id):
                    summary["merged"].append({"name": sn.name, "into_type": keep.type})
            except Exception as e:
                summary["errors"].append(f"merge failed for slot '{sn.name}': {e}")
    except Exception as e:
        summary["errors"].append(f"merge_slot_duplicates crashed: {e}")
    return summary


def _apply_retention(nodes: List[Node], edges: List[Edge]) -> Tuple[List[Node], List[Edge]]:
    """Enforce MAX_NODES_PER_POOL / MAX_EDGES_PER_POOL.

    Strategy:
      - Edges over cap → drop oldest by `created_at`.
      - Nodes over cap → drop lowest-degree nodes first (oldest as tiebreaker).
        Cascade: any edges touching dropped nodes also go.
    """
    # Edge cap first (cheap, no cascading).
    if len(edges) > MAX_EDGES_PER_POOL:
        edges = sorted(edges, key=lambda e: e.created_at)[-MAX_EDGES_PER_POOL:]

    # Node cap with cascading edge removal.
    if len(nodes) > MAX_NODES_PER_POOL:
        degree: dict[str, int] = {str(n.id): 0 for n in nodes}
        for e in edges:
            sid, tid = str(e.source_id), str(e.target_id)
            if sid in degree:
                degree[sid] += 1
            if tid in degree:
                degree[tid] += 1

        nodes_sorted = sorted(
            nodes,
            key=lambda n: (degree.get(str(n.id), 0), n.created_at),
        )
        keep_count = MAX_NODES_PER_POOL
        kept = nodes_sorted[-keep_count:]
        kept_ids = {str(n.id) for n in kept}
        edges = [
            e for e in edges
            if str(e.source_id) in kept_ids and str(e.target_id) in kept_ids
        ]
        nodes = kept

    return nodes, edges
