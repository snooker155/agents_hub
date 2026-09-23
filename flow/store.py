"""
Centralized flow storage, in the ``flows`` document collection
(:class:`common.docstore.DocStore`, one row per flow, keyed by flow id).

A flow's document is the combined dict the rest of the codebase already
expects: nodes carry ``data`` (label/description, agent_id/entity_id,
config, ...) plus their visual ``position``/``style``, and edges carry
``source``/``target`` plus their visual fields. Nothing is split on write or
merged on read any more — ``save_flow``/``get_flow`` pass the document
straight through the store.

The YAML *logic* skeleton (meta, state, node logic, edges, with no ids and no
visual fields) still exists as an export/import format: ``export_flow_yaml``
derives it from the stored document via ``_split``, and ``import_flow_yaml``
turns pasted-in YAML text back into a combined document via ``_merge``. Those
two functions are what used to be the on-disk YAML/JSON pair, kept only as a
serialization the API surfaces.

Legacy migration, once per database: an installation that still has the old
per-flow ``<flow_id>.yaml`` + ``<flow_id>.json`` file pairs under
``.agents_hub/flows/`` (or, older still, a single ``.agents_hub/flows.json``
array) has every pair read with the same logic ``_read_pair`` always used,
loaded into the store keyed by id, and the source renamed to ``.migrated``
(the directory becomes ``flows.migrated``, the old file becomes
``flows.json.migrated``). A store that already has rows never imports.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from common import db
from common.docstore import DocStore
from common.paths import AGENTS_HUB_ROOT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Legacy locations only: read during the one-time import, never written again.
FLOWS_DIR = AGENTS_HUB_ROOT / "flows"
LEGACY_FLOWS_FILE = AGENTS_HUB_ROOT / "flows.json"

_FLOWS = DocStore("flows")


class FlowParseError(ValueError):
    """A flow file is structurally readable but references something invalid —
    e.g. a node whose ``entity`` is not a registered flow entity."""


# Flow-level keys that are NOT written to the YAML skeleton. ``id`` is the
# store key; ``task_id`` is per-run state owned by the flow-run records
# (``flow.run_store``), not part of the flow's logic definition.
_NON_YAML_FLOW_FIELDS = {"id", "task_id"}
# Per-node logic fields carried in the YAML skeleton (besides ``type`` and the
# entity reference, which get their own dedicated keys). ``id`` and ``nodeTask``
# are dropped: ids live only in the visual side, nodeTask is obsolete.
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
    The visual half is the id ledger — it holds the concrete node ids (keyed
    back to YAML by ``key``), positions, styles and edge ids. Used by
    ``export_flow_yaml`` (to derive the YAML half of a stored document) and by
    the one-time legacy import (to read the old on-disk pairs).
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
    # visual ledger alongside the ids, never in the YAML skeleton.
    if flow.get("task_id"):
        visual["task_id"] = flow["task_id"]
    visual["nodes"] = visual_nodes
    visual["edges"] = visual_edges
    return logic, visual


def _gen_node_id(key: str) -> str:
    """Deterministic node id for a key when the visual ledger has none (e.g. a
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

    Lets old-format YAML/JSON pairs load through the new merge path unchanged
    during the one-time legacy import.
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

    Reconstructs concrete node ids (from the visual ledger, regenerated when
    absent) and rebuilds edges' ``source``/``target`` and ``entry_point`` from
    the YAML node keys. Used by ``import_flow_yaml`` and by the one-time legacy
    import of old on-disk pairs.
    """
    if _is_legacy_logic(logic):
        logic, visual = _normalize_legacy_logic(logic, visual)
    logic_nodes = logic.get("nodes", [])
    keys = _node_keys(_logic_to_nodelike(logic_nodes))
    visual_by_key = {n.get("key"): n for n in (visual.get("nodes") or [])}
    # key → concrete id: prefer the visual ledger, else regenerate deterministically.
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
    # Restore the attached task (run-scoped, stored in the visual ledger).
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

    # Edges: rebuild source/target from keys; restore visual fields from the ledger.
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

    Every node in a flow must name a registered flow entity (agent, processor,
    condition, ...). A reference to an unknown entity — a typo, or an agent
    that was since deleted/renamed — is a parse-time error, not a
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


# ── legacy file paths (read-only: the one-time import only) ─────────────────

def _yaml_path(flow_id: str) -> Path:
    return FLOWS_DIR / f"{flow_id}.yaml"


def _json_path(flow_id: str) -> Path:
    return FLOWS_DIR / f"{flow_id}.json"


def _read_pair(flow_id: str) -> Optional[Dict[str, Any]]:
    """Read one legacy ``<flow_id>.yaml`` + ``<flow_id>.json`` pair off disk and
    merge it into a combined flow dict. Used only by the one-time legacy
    import — the store holds the combined dict directly from then on."""
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


# ── legacy import (once per database) ────────────────────────────────────────

_legacy_imported_for: Optional[str] = None


def _ensure_legacy_imported() -> None:
    """Import the old on-disk flows into the store, at most once per database
    (like :meth:`common.docstore.DocStore._ensure_imported`, whose per-store
    marker this mirrors: a fresh/rewritten database — a test swapping
    databases, a process reopening one — makes the check run again)."""
    global _legacy_imported_for
    db.get_conn()
    marker = f"{db._generation}:{db.dialect()}:{db.DB_FILE}:{db.database_url()}"
    if _legacy_imported_for == marker:
        return
    _legacy_imported_for = marker
    _import_legacy_flows()


def _import_legacy_flows() -> None:
    """Load every flow found on disk into the store and rename the source out
    of the way. The per-flow YAML/JSON directory, when it has any pairs, is
    authoritative (mirrors the old migration's own precedence); the older
    single ``flows.json`` array is only used when there is no such directory."""
    dir_pairs = sorted(FLOWS_DIR.glob("*.yaml")) if FLOWS_DIR.exists() else []
    if dir_pairs:
        docs: Dict[str, Any] = {}
        for yp in dir_pairs:
            flow_id = yp.stem
            try:
                merged = _read_pair(flow_id)
            except FlowParseError as e:
                print(f"[flow_store] legacy flow '{flow_id}' skipped: {e}")
                continue
            if merged:
                docs[flow_id] = merged
        _FLOWS.import_legacy(docs, source=FLOWS_DIR)
        return
    if LEGACY_FLOWS_FILE.exists():
        try:
            flows = json.loads(LEGACY_FLOWS_FILE.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[flow_store] could not read legacy flows.json: {e}")
            return
        docs = {}
        if isinstance(flows, list):
            for flow in flows:
                if isinstance(flow, dict) and flow.get("id"):
                    docs[str(flow["id"])] = flow
        _FLOWS.import_legacy(docs, source=LEGACY_FLOWS_FILE)


# ── public API ───────────────────────────────────────────────────────────────

def list_flows(limit: Optional[int] = None, offset: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return every flow as a combined dict, ordered by id (deterministic,
    matching the old file-name ordering when ids were also the filenames).

    ``limit``/``offset`` slice that ordered list; ``count_flows`` gives the
    matching total. One broken flow (an unknown entity reference) does not
    break the rest of the listing — it is skipped and logged; fetching it
    directly through :func:`get_flow` still raises.
    """
    _ensure_legacy_imported()
    flows = sorted(_FLOWS.values(), key=lambda f: str((f or {}).get("id") or ""))
    if limit is not None or offset is not None:
        start = offset or 0
        flows = flows[start: start + limit] if limit is not None else flows[start:]
    out: List[Dict[str, Any]] = []
    for flow in flows:
        flow_id = flow.get("id", "")
        try:
            _check_entities_exist(flow_id, flow)
        except FlowParseError as e:
            print(f"[flow_store] skip {flow_id}: {e}")
            continue
        out.append(flow)
    return out


def count_flows() -> int:
    """Total number of flows in the store, for a caller paging with
    :func:`list_flows` that needs ``total`` without loading every document."""
    _ensure_legacy_imported()
    return _FLOWS.count()


def get_flow(flow_id: str) -> Optional[Dict[str, Any]]:
    """Return a single combined flow dict, or None."""
    _ensure_legacy_imported()
    flow = _FLOWS.get(flow_id)
    if flow is None:
        return None
    _check_entities_exist(flow_id, flow)  # raises FlowParseError on unknown entity
    return flow


def _notify_flows_changed(flow_id: str | None = None) -> None:
    try:
        from common.session_broker import notify_change
        notify_change("flows", flow_id=flow_id)
    except Exception:
        pass


def save_flow(flow: Dict[str, Any]) -> None:
    """Persist a combined flow dict as the store's document for its id."""
    if not flow.get("id"):
        raise ValueError("flow must have an 'id'")
    _ensure_legacy_imported()
    _FLOWS.put(flow["id"], flow)
    _notify_flows_changed(flow.get("id"))


def delete_flow(flow_id: str) -> bool:
    """Delete a flow from the store. Returns True if anything was removed."""
    _ensure_legacy_imported()
    removed = _FLOWS.delete(flow_id)
    if removed:
        _notify_flows_changed(flow_id)
    return removed


def export_flow_yaml(flow_id: str) -> Optional[str]:
    """Return a flow's logic skeleton serialized as YAML text, or None if absent.

    This is the logic-only view of the stored document (meta, state, node
    logic, edges) — the portable definition of the flow, without the visual
    layout. ``id``/``task_id`` are run/store-scoped and excluded by ``_split``.
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
