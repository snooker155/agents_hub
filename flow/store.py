"""
Centralized flow storage with a YAML (logic) + JSON (visual) split.

Each flow is persisted as a pair of files under ``.agents_hub/flows/``::

    <flow_id>.yaml   # source of truth: meta, state, node logic, edges
    <flow_id>.json   # visual layer: per-node position/style/label (React Flow)

The two are linked by node ``id``. ``load_flow`` merges them back into the
single combined dict the rest of the codebase already expects (nodes with
``data`` holding label/description, plus position/style), so downstream readers
need no changes beyond calling this module.

Legacy migration: the old single ``.agents_hub/flows.json`` array is split
into per-flow file pairs on first access, then renamed to ``flows.json.bak``.

All reads/writes funnel through here so the six historical readers of
``flows.json`` stay consistent. A file lock guards concurrent writers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from common.paths import AGENTS_HUB_ROOT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLOWS_DIR = AGENTS_HUB_ROOT / "flows"
LEGACY_FLOWS_FILE = AGENTS_HUB_ROOT / "flows.json"


class FlowParseError(ValueError):
    """A flow file is structurally readable but references something invalid —
    e.g. a node whose ``entity`` is not a registered flow entity."""

try:
    from filelock import FileLock

    def _lock(path: Path):
        return FileLock(str(path) + ".lock")
except ImportError:  # pragma: no cover - filelock is optional
    import contextlib

    def _lock(path: Path):
        return contextlib.nullcontext()


# Flow-level keys that are NOT written to the YAML skeleton. ``id`` is the
# filename; ``task_id`` is per-run state owned by the flow-run records
# (``flow.run_store``), not part of the flow's logic definition.
_NON_YAML_FLOW_FIELDS = {"id", "task_id"}
# Per-node logic fields carried in the YAML skeleton (besides ``type`` and the
# entity reference, which get their own dedicated keys). ``id`` and ``nodeTask``
# are dropped: ids live only in the visual JSON, nodeTask is obsolete.
_LOGIC_NODE_FIELDS = {
    "input", "inputs", "output", "outputs", "config",
    # Execution policy the engine reads per node (see flow.engine): how many
    # times a failed attempt is repeated and how long one attempt may take.
    "retry", "timeout_seconds",
}
# Per-node ``data`` keys that are purely visual.
_VISUAL_DATA_FIELDS = {"label", "description", "domain"}
# React Flow's per-node render type (visual), distinct from the logical node
# ``type`` (the entity category) we write to YAML.
_RENDER_NODE_TYPE = "flowNode"


# ── node identity ────────────────────────────────────────────────────────────

def _entity_ref(node: Dict[str, Any]) -> str:
    """The entity spec/class a node references: entity_id (preferred) or agent_id."""
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    for src in (node, data):
        for f in ("entity_id", "agent_id"):
            v = src.get(f)
            if v:
                return str(v)
    return ""


def _node_type(node: Dict[str, Any]) -> str:
    """The logical node type = the entity category. Falls back to 'agent' when a
    node carries an agent_id but no explicit category, else 'node'."""
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    cat = node.get("category") or data.get("category")
    if cat:
        return str(cat)
    if node.get("agent_id") or data.get("agent_id"):
        return "agent"
    return "node"


def _node_keys(nodes: List[Dict[str, Any]]) -> List[str]:
    """Stable per-node key derived from the entity ref, in node order.

    Duplicates of the same entity are disambiguated with a ``#N`` suffix
    (``narrator``, ``narrator#2``, ...). This key — not a synthetic id — is how
    YAML edges and entry_point reference nodes.
    """
    seen: Dict[str, int] = {}
    keys: List[str] = []
    for n in nodes:
        ref = _entity_ref(n) or "node"
        seen[ref] = seen.get(ref, 0) + 1
        keys.append(ref if seen[ref] == 1 else f"{ref}#{seen[ref]}")
    return keys


# ── split / merge ──────────────────────────────────────────────────────────

def _split(flow: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Split a combined flow dict into (logic_yaml, visual_json).

    YAML is a pure logic skeleton: nodes are ``{type, entity, ...io}`` with no
    ids; edges are ``{from, to}`` referencing node keys; entry_point is a key.
    JSON is the visual realization and the id ledger — it holds the concrete
    node ids (keyed back to YAML by ``key``), positions, styles and edge ids.
    """
    src_nodes = flow.get("nodes", [])
    src_edges = flow.get("edges", [])
    keys = _node_keys(src_nodes)
    id_to_key = {n.get("id"): k for n, k in zip(src_nodes, keys)}

    logic: Dict[str, Any] = {}
    for k, v in flow.items():
        if k in ("nodes", "edges") or k in _NON_YAML_FLOW_FIELDS:
            continue
        if k == "entry_point" and v:
            logic[k] = id_to_key.get(v, v)  # store entry_point as a node key
        else:
            logic[k] = v

    logic_nodes: List[Dict[str, Any]] = []
    visual_nodes: List[Dict[str, Any]] = []
    for node, key in zip(src_nodes, keys):
        data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}

        ln: Dict[str, Any] = {"type": _node_type(node), "entity": _entity_ref(node)}
        for f in _LOGIC_NODE_FIELDS:
            if f in node:
                ln[f] = node[f]
            elif f in data:
                ln[f] = data[f]
        logic_nodes.append(ln)

        vn: Dict[str, Any] = {"key": key, "id": node.get("id")}
        for f in ("position", "style", "type", "width", "height"):
            if f in node:
                vn[f] = node[f]
        vdata = {k: data[k] for k in _VISUAL_DATA_FIELDS if k in data}
        if vdata:
            vn["data"] = vdata
        visual_nodes.append(vn)

    # Edges: YAML keeps only from/to (as keys); every other edge field (id, type,
    # sourceHandle, style, ...) is visual and lives in the JSON, keyed by from|to.
    logic_edges: List[Dict[str, Any]] = []
    visual_edges: List[Dict[str, Any]] = []
    for e in src_edges:
        fk = id_to_key.get(e.get("source"), e.get("source"))
        tk = id_to_key.get(e.get("target"), e.get("target"))
        logic_edges.append({"from": fk, "to": tk})
        ve = {k: v for k, v in e.items() if k not in ("source", "target")}
        ve["from"], ve["to"] = fk, tk
        visual_edges.append(ve)

    logic["nodes"] = logic_nodes
    logic["edges"] = logic_edges
    visual: Dict[str, Any] = {"id": flow.get("id")}
    # The currently attached task is run-scoped, not flow logic → it rides in the
    # JSON ledger alongside the ids, never in the YAML skeleton.
    if flow.get("task_id"):
        visual["task_id"] = flow["task_id"]
    visual["nodes"] = visual_nodes
    visual["edges"] = visual_edges
    return logic, visual


def _gen_node_id(key: str) -> str:
    """Deterministic node id for a key when the visual JSON has none (e.g. a
    YAML authored by hand). Strips the ``#N`` disambiguator into a suffix."""
    base, _, n = key.partition("#")
    return base if not n else f"{base}-{n}"


def _is_legacy_logic(logic: Dict[str, Any]) -> bool:
    """True for pre-skeleton YAML: nodes carry their own ``id`` (and edges use
    ``source``/``target``) rather than the new ``type``/``entity`` + ``from``/``to``."""
    for n in logic.get("nodes", []) or []:
        if isinstance(n, dict) and ("id" in n) and "entity" not in n and "type" not in n:
            return True
    return False


def _normalize_legacy_logic(logic: Dict[str, Any], visual: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Convert an old-format (id-bearing) logic+visual pair to the new shape.

    Lets existing flow files load through the new merge path unchanged; the file
    on disk is rewritten in skeleton form the next time it is saved.
    """
    visual_by_id = {n.get("id"): n for n in (visual.get("nodes") or [])}
    combined_nodes: List[Dict[str, Any]] = []
    for ln in logic.get("nodes", []) or []:
        nid = ln.get("id")
        vn = visual_by_id.get(nid, {})
        data: Dict[str, Any] = {}
        data.update(vn.get("data", {}) if isinstance(vn.get("data"), dict) else {})
        for f in ("agent_id", "entity_id", "category", "input", "inputs",
                  "output", "outputs", "config"):
            if f in ln:
                data[f] = ln[f]
        node: Dict[str, Any] = {"id": nid, "data": data}
        for f in ("position", "style", "type", "width", "height"):
            if f in vn:
                node[f] = vn[f]
        combined_nodes.append(node)
    combined: Dict[str, Any] = {k: v for k, v in logic.items() if k not in ("nodes", "edges")}
    combined["id"] = combined.get("id") or visual.get("id")
    combined["nodes"] = combined_nodes
    combined["edges"] = logic.get("edges", [])
    # Re-split through the new path to get clean skeleton logic + visual ledger.
    return _split(combined)


def _merge(logic: Dict[str, Any], visual: Dict[str, Any]) -> Dict[str, Any]:
    """Recombine a logic+visual pair into the combined dict callers expect.

    Reconstructs concrete node ids (from the JSON ledger, regenerated when
    absent) and rebuilds edges' ``source``/``target`` and ``entry_point`` from
    the YAML node keys, so the in-memory shape matches what the runtime expects.
    """
    if _is_legacy_logic(logic):
        logic, visual = _normalize_legacy_logic(logic, visual)
    logic_nodes = logic.get("nodes", [])
    keys = _node_keys(_logic_to_nodelike(logic_nodes))
    visual_by_key = {n.get("key"): n for n in (visual.get("nodes") or [])}
    # key → concrete id: prefer the JSON ledger, else regenerate deterministically.
    key_to_id: Dict[str, str] = {}
    for k in keys:
        vn = visual_by_key.get(k, {})
        key_to_id[k] = vn.get("id") or _gen_node_id(k)

    merged: Dict[str, Any] = {}
    for k, v in logic.items():
        if k in ("nodes", "edges"):
            continue
        if k == "entry_point" and v:
            merged[k] = key_to_id.get(v, v)
        else:
            merged[k] = v
    if visual.get("id"):
        merged.setdefault("id", visual["id"])
    # Restore the attached task (run-scoped, stored in the JSON ledger).
    merged["task_id"] = visual.get("task_id")

    nodes: List[Dict[str, Any]] = []
    for ln, key in zip(logic_nodes, keys):
        vn = visual_by_key.get(key, {})
        nid = key_to_id[key]
        entity = ln.get("entity") or ""
        ntype = ln.get("type") or "node"

        data: Dict[str, Any] = {}
        data.update(vn.get("data", {}) if isinstance(vn.get("data"), dict) else {})
        # Re-expand the entity ref + category onto the node data the way the rest
        # of the code reads it (agent nodes use agent_id; others use entity_id).
        if ntype == "agent":
            data["agent_id"] = entity
        else:
            data["entity_id"] = entity
        data["category"] = ntype
        for f in _LOGIC_NODE_FIELDS:
            if f in ln:
                data[f] = ln[f]

        node: Dict[str, Any] = {"id": nid, "data": data}
        for f in ("position", "style", "type", "width", "height"):
            if f in vn:
                node[f] = vn[f]
        node.setdefault("type", _RENDER_NODE_TYPE)
        nodes.append(node)
    merged["nodes"] = nodes

    # Edges: rebuild source/target from keys; restore visual fields from JSON.
    visual_edges_by_pair = {
        (e.get("from"), e.get("to")): e for e in (visual.get("edges") or [])
    }
    edges: List[Dict[str, Any]] = []
    for le in logic.get("edges", []):
        fk, tk = le.get("from"), le.get("to")
        ve = visual_edges_by_pair.get((fk, tk), {})
        edge: Dict[str, Any] = {k: v for k, v in ve.items() if k not in ("from", "to")}
        sid, tid = key_to_id.get(fk, fk), key_to_id.get(tk, tk)
        edge["source"], edge["target"] = sid, tid
        edge.setdefault("id", f"reactflow__edge-{sid}-{tid}")
        edge.setdefault("type", "smoothstep")
        edges.append(edge)
    merged["edges"] = edges
    return merged


def _logic_to_nodelike(logic_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt YAML logic nodes ({type, entity}) to the shape _node_keys reads."""
    out: List[Dict[str, Any]] = []
    for ln in logic_nodes:
        out.append({"entity_id": ln.get("entity"), "category": ln.get("type")})
    return out


def _check_entities_exist(flow_id: str, merged: Dict[str, Any]) -> None:
    """Fail loudly when a node references an entity missing from the registry.

    Every node in a YAML flow must name a registered flow entity (agent,
    processor, condition, ...). A reference to an unknown entity — a typo, or an
    agent that was since deleted/renamed — is a parse-time error, not a
    silently-skipped node. Raises :class:`FlowParseError` listing every offender.
    """
    from flow import dispatch as flow_dispatch

    bad: List[str] = []
    for node in merged.get("nodes", []):
        if flow_dispatch.FlowEntity.for_node(node) is not None:
            continue
        ref = (
            flow_dispatch.node_field(node, "entity_id")
            or flow_dispatch.node_field(node, "agent_id")
            or "(none)"
        )
        label = flow_dispatch.node_field(node, "label") or node.get("id") or ref
        bad.append(f"node '{label}' references unknown entity '{ref}'")
    if bad:
        raise FlowParseError(
            f"flow '{flow_id}' references {len(bad)} unknown "
            f"entit{'y' if len(bad) == 1 else 'ies'}: " + "; ".join(bad)
        )


# ── file paths ───────────────────────────────────────────────────────────────

def _yaml_path(flow_id: str) -> Path:
    return FLOWS_DIR / f"{flow_id}.yaml"


def _json_path(flow_id: str) -> Path:
    return FLOWS_DIR / f"{flow_id}.json"


# ── migration ──────────────────────────────────────────────────────────────

def _migrate_legacy_if_needed() -> None:
    """Split the legacy flows.json array into per-flow pairs, once.

    Runs when the legacy file exists and the per-flow folder has no YAML files
    yet. The legacy file is renamed to flows.json.bak afterwards.
    """
    if not LEGACY_FLOWS_FILE.exists():
        return
    FLOWS_DIR.mkdir(parents=True, exist_ok=True)
    if any(FLOWS_DIR.glob("*.yaml")):
        return  # already migrated
    try:
        flows = json.loads(LEGACY_FLOWS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[flow_store] could not read legacy flows.json: {e}")
        return
    if not isinstance(flows, list):
        return
    for flow in flows:
        if isinstance(flow, dict) and flow.get("id"):
            _write_pair(flow)
    try:
        LEGACY_FLOWS_FILE.rename(LEGACY_FLOWS_FILE.with_suffix(".json.bak"))
    except OSError:
        pass
    print(f"[flow_store] migrated {len(flows)} flow(s) to per-flow YAML/JSON pairs")


# ── low-level read/write ──────────────────────────────────────────────────────

def _write_pair(flow: Dict[str, Any]) -> None:
    flow_id = flow["id"]
    FLOWS_DIR.mkdir(parents=True, exist_ok=True)
    logic, visual = _split(flow)
    with _lock(_yaml_path(flow_id)):
        _yaml_path(flow_id).write_text(
            yaml.safe_dump(logic, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        _json_path(flow_id).write_text(
            json.dumps(visual, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _read_pair(flow_id: str) -> Optional[Dict[str, Any]]:
    yp, jp = _yaml_path(flow_id), _json_path(flow_id)
    if not yp.exists():
        return None
    try:
        logic = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        print(f"[flow_store] invalid YAML for {flow_id}: {e}")
        return None
    visual: Dict[str, Any] = {}
    if jp.exists():
        try:
            visual = json.loads(jp.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            print(f"[flow_store] invalid JSON for {flow_id}: {e}")
    merged = _merge(logic, visual)
    _check_entities_exist(flow_id, merged)  # raises FlowParseError on unknown entity
    return merged


# ── public API ───────────────────────────────────────────────────────────────

def list_flows(limit: Optional[int] = None, offset: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return flows as combined dicts (logic+visual merged), file-name order.

    ``limit``/``offset`` page the file list *before* anything is read off disk:
    each flow is a YAML+JSON pair, so a page of N flows costs N parses rather
    than "parse the whole catalog, then slice". With neither given, every flow
    is returned, exactly as before. See :func:`count_flows` for the matching
    total that does not parse anything either.
    """
    _migrate_legacy_if_needed()
    if not FLOWS_DIR.exists():
        return []
    paths = sorted(FLOWS_DIR.glob("*.yaml"))
    if limit is not None or offset is not None:
        start = offset or 0
        paths = paths[start: start + limit] if limit is not None else paths[start:]
    out: List[Dict[str, Any]] = []
    for yp in paths:
        try:
            flow = _read_pair(yp.stem)
        except FlowParseError as e:
            # One broken flow must not break the whole listing; skip + log so the
            # rest of the catalog still loads. Opening it directly still errors.
            print(f"[flow_store] skip {yp.stem}: {e}")
            continue
        if flow:
            out.append(flow)
    return out


def count_flows() -> int:
    """Total number of flows on disk, for a caller paging with
    :func:`list_flows` that needs ``total`` without parsing every file."""
    _migrate_legacy_if_needed()
    if not FLOWS_DIR.exists():
        return 0
    return sum(1 for _ in FLOWS_DIR.glob("*.yaml"))


def get_flow(flow_id: str) -> Optional[Dict[str, Any]]:
    """Return a single combined flow dict, or None."""
    _migrate_legacy_if_needed()
    return _read_pair(flow_id)


def _notify_flows_changed(flow_id: str | None = None) -> None:
    try:
        from common.session_broker import notify_change
        notify_change("flows", flow_id=flow_id)
    except Exception:
        pass


def save_flow(flow: Dict[str, Any]) -> None:
    """Persist a combined flow dict as its YAML/JSON pair."""
    if not flow.get("id"):
        raise ValueError("flow must have an 'id'")
    _migrate_legacy_if_needed()
    _write_pair(flow)
    _notify_flows_changed(flow.get("id"))


def delete_flow(flow_id: str) -> bool:
    """Delete a flow's file pair. Returns True if anything was removed."""
    _migrate_legacy_if_needed()
    removed = False
    for p in (_yaml_path(flow_id), _json_path(flow_id)):
        if p.exists():
            p.unlink()
            removed = True
    if removed:
        _notify_flows_changed(flow_id)
    return removed


def export_flow_yaml(flow_id: str) -> Optional[str]:
    """Return a flow's logic skeleton serialized as YAML text, or None if absent.

    This is the same logic-only YAML written to ``<flow_id>.yaml`` (meta, state,
    node logic, edges) — the portable definition of the flow, without the visual
    layout. ``id``/``task_id`` are run/file-scoped and excluded by ``_split``.
    """
    flow = get_flow(flow_id)
    if flow is None:
        return None
    logic, _ = _split(flow)
    return yaml.safe_dump(logic, sort_keys=False, allow_unicode=True)


def import_flow_yaml(yaml_text: str, flow_id: str) -> Dict[str, Any]:
    """Parse a flow YAML skeleton into a combined flow dict under a new ``flow_id``.

    Validates that the YAML is structurally a flow and that every node references
    a registered entity (raises :class:`FlowParseError` otherwise), then returns
    the merged dict ready for :func:`save_flow`. Does not persist anything itself.
    """
    try:
        logic = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")
    if not isinstance(logic, dict):
        raise ValueError("Flow YAML must be a mapping at the top level")
    if not isinstance(logic.get("nodes", []), list):
        raise ValueError("Flow 'nodes' must be a list")
    if not isinstance(logic.get("edges", []), list):
        raise ValueError("Flow 'edges' must be a list")

    # Merge with an empty visual ledger → node ids/positions are regenerated.
    merged = _merge(logic, {"id": flow_id})
    merged["id"] = flow_id
    # Strip identity/run-scoped fields that must not be inherited from the source.
    merged.pop("task_id", None)
    _check_entities_exist(flow_id, merged)  # raises FlowParseError on unknown entity
    return merged


def flow_agent_ids(flow: Dict[str, Any]) -> List[str]:
    """Unique agent ids referenced by a flow's agent nodes, in node order.

    Non-agent entities (processors, conditions, ...) are excluded.
    """
    ids: List[str] = []
    for node in flow.get("nodes", []) or []:
        data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
        category = node.get("category") or data.get("category")
        agent_id = node.get("agent_id") or data.get("agent_id")
        if agent_id and (not category or category == "agent") and agent_id not in ids:
            ids.append(str(agent_id))
    return ids


def set_running(flow_id: str, running: bool) -> None:
    """Toggle a flow's ``running`` flag — a coarse "any instance active?" marker
    for the flow list UI.

    Per-instance execution state (pid, status, stop signal) lives in the
    flow-run records (``flow.run_store``), since one flow can run in parallel.
    """
    flow = get_flow(flow_id)
    if flow is None:
        return
    flow["running"] = running
    save_flow(flow)
