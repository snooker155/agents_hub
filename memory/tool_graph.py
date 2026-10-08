"""Tool builders for the link and traverse tools, bound to one memory binding."""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool


log = logging.getLogger(__name__)


def build_graph_tools(ctx):
    _pool_name = ctx._pool_name
    _refuse_write = ctx._refuse_write
    _ro_ids = ctx._ro_ids
    multi = ctx.multi
    pool_id = ctx.pool_id
    pool_ids = ctx.pool_ids

    # -----------------------------------------------------------------------
    # link — create or merge an edge between two entities in the knowledge graph
    # -----------------------------------------------------------------------

    class _NodeRef(BaseModel):
        type: str = Field(..., description="Entity type, e.g. 'person', 'project', 'task', 'file', 'concept'.")
        name: str = Field(..., description="Human-readable name; unique per type within this pool.")
        properties: Optional[dict] = Field(None, description="Optional extra fields stored on the node (merged on upsert).")

    class _LinkInput(BaseModel):
        source: _NodeRef = Field(..., description="Source entity. Created if it doesn't exist; properties merged if it does.")
        target: _NodeRef = Field(..., description="Target entity. Same upsert semantics as source.")
        relation: str = Field(..., description="Directed relation, e.g. 'owns', 'depends_on', 'blocks', 'mentions', 'assigned_to'.")
        edge_properties: Optional[dict] = Field(None, description="Optional fields stored on the edge (merged if the edge already exists).")
        weight: Optional[float] = Field(None, description="Optional numeric weight on the edge.")

    def _link_impl(
        source,
        target,
        relation: str,
        edge_properties: Optional[dict] = None,
        weight: Optional[float] = None,
    ) -> str:
        if pool_id in _ro_ids:
            return _refuse_write(pool_id)
        try:
            from memory.graph import GraphStore

            def _coerce(ref) -> dict:
                if hasattr(ref, "model_dump"):
                    return ref.model_dump()
                if isinstance(ref, dict):
                    return ref
                raise ValueError(f"node ref must be dict or _NodeRef, got {type(ref).__name__}")

            src = _coerce(source)
            tgt = _coerce(target)
            store = GraphStore(pool_id)
            src_node, src_mode = store.upsert_node(
                type=src["type"],
                name=src["name"],
                properties=src.get("properties"),
            )
            tgt_node, tgt_mode = store.upsert_node(
                type=tgt["type"],
                name=tgt["name"],
                properties=tgt.get("properties"),
            )
            edge, edge_mode = store.add_edge(
                source_id=src_node.id,
                target_id=tgt_node.id,
                relation=relation,
                properties=edge_properties,
                weight=weight,
            )
            return json.dumps({
                "ok": True,
                "source": {"id": str(src_node.id), "type": src_node.type, "name": src_node.name, "mode": src_mode},
                "target": {"id": str(tgt_node.id), "type": tgt_node.type, "name": tgt_node.name, "mode": tgt_mode},
                "edge": {"id": str(edge.id), "relation": edge.relation, "mode": edge_mode},
            })
        except KeyError as e:
            return json.dumps({"ok": False, "error": f"missing required field: {e}"})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"link failed: {e}"})

    link_tool = StructuredTool.from_function(
        name="link",
        description=(
            "Record a relationship between two entities in the knowledge graph. "
            "Both entities are upserted by (type, name) — created if missing, properties merged if present. "
            "The edge is directed: source -[relation]-> target. "
            "Use for facts that connect things: who owns what, what depends on what, what mentions what. "
            "Pick consistent type and relation names (lowercase, snake_case) — they're matched case-insensitively but stored as given."
            + (" Edges are written to the primary pool's graph." if multi else "")
        ),
        func=_link_impl,
        args_schema=_LinkInput,
    )

    # -----------------------------------------------------------------------
    # traverse — walk the knowledge graph from a starting entity
    # -----------------------------------------------------------------------

    from typing import List as _ListT  # local alias to avoid clash with outer List import

    class _TraverseInput(BaseModel):
        type: str = Field(..., description="Type of the starting entity (e.g. 'person').")
        name: str = Field(..., description="Name of the starting entity (e.g. 'alice').")
        max_depth: int = Field(2, description="How many hops to follow (capped at 3).")
        direction: str = Field("out", description="One of: 'out' (follow outgoing edges), 'in' (incoming), 'both'.")
        relation_filter: Optional[_ListT[str]] = Field(None, description="Only follow edges with these relation names.")
        limit_per_level: int = Field(20, description="Max neighbors expanded per node per level.")

    def _traverse_impl(
        type: str,
        name: str,
        max_depth: int = 2,
        direction: str = "out",
        relation_filter: Optional[list] = None,
        limit_per_level: int = 20,
    ) -> str:
        try:
            from memory.graph import GraphStore
            if direction not in ("out", "in", "both"):
                return json.dumps({"ok": False, "error": "direction must be 'out', 'in', or 'both'"})
            # Start from the first attached pool whose graph has the node;
            # traversal stays within that pool's graph.
            store = None
            start = None
            start_pid = pool_ids[0]
            for pid in pool_ids:
                candidate = GraphStore(pid)
                node = candidate.get_node(type=type, name=name)
                if node is not None:
                    store, start, start_pid = candidate, node, pid
                    break
            if start is None:
                return json.dumps({
                    "ok": False,
                    "error": f"start node not found: type={type!r}, name={name!r}",
                })
            nodes, edges = store.traverse(
                start_node_id=start.id,
                max_depth=max_depth,
                relation_filter=relation_filter,
                direction=direction,  # type: ignore[arg-type]
                limit_per_level=limit_per_level,
            )
            by_id = {str(n.id): n for n in nodes}
            edge_lines = []
            for e in edges:
                src = by_id.get(str(e.source_id))
                tgt = by_id.get(str(e.target_id))
                if src and tgt:
                    edge_lines.append(f"({src.type}:{src.name}) -[{e.relation}]-> ({tgt.type}:{tgt.name})")
            return json.dumps({
                "ok": True,
                "start": {
                    "id": str(start.id), "type": start.type, "name": start.name,
                    **({"pool": _pool_name(start_pid)} if multi else {}),
                },
                "nodes": [
                    {"id": str(n.id), "type": n.type, "name": n.name, "properties": n.properties}
                    for n in nodes
                ],
                "edges": [
                    {
                        "id": str(e.id),
                        "source_id": str(e.source_id),
                        "target_id": str(e.target_id),
                        "relation": e.relation,
                        "properties": e.properties,
                        "weight": e.weight,
                    }
                    for e in edges
                ],
                "rendered": edge_lines,
            }, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"traverse failed: {e}"})

    traverse_tool = StructuredTool.from_function(
        name="traverse",
        description=(
            "Walk the knowledge graph from a starting entity. "
            "Returns visited nodes, traversed edges, and a `rendered` list of `(type:name) -[relation]-> (type:name)` lines for quick scanning. "
            "Use to answer relational questions: 'what does X own?', 'what depends on Y?', 'who is connected to Z?'. "
            "Set direction='in' to follow edges pointing INTO the start node, 'both' to ignore direction. "
            "Filter relation_filter to a subset of relations to keep traversal focused."
        ),
        func=_traverse_impl,
        args_schema=_TraverseInput,
    )

    return link_tool, traverse_tool
