"""Lookup kinds: catalog (see chat/lookup_kinds/__init__.py).

Skills, MCP servers and the registry (published agents, flows, skills and
the MCP allowlist). The registry is the one place this module treats
something as visible beyond the current workspace: an item that has been
shared is meant to be seen from anywhere, exactly like the Marketplace page
shows it.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from chat import lookup
from chat.actions import HubAction, register_action


# ── skills ───────────────────────────────────────────────────────────────────

def _list_skills(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from memory.procedural import ProcedureStore
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for p in ProcedureStore(ws).load():
            if not lookup.matches(query, p.name, p.description, p.agent_id):
                continue
            rows.append(lookup.row(
                str(p.id), p.name,
                " · ".join(x for x in [f"attached: {p.agent_id}" if p.agent_id else "catalog",
                                       "shared" if p.shared else "private", p.review_status] if x),
                "/skills", workspace=ws if ctx.workspace is None else None,
                at=p.updated_at.isoformat() if p.updated_at else None,
            ))
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _skill_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    from memory.procedural import find_procedure
    p = find_procedure(entity_id)
    if p is None or not lookup.visible(ctx, p.workspace):
        return None
    fields = {
        "id": str(p.id), "name": p.name, "workspace": p.workspace,
        "agent_id": p.agent_id or None, "shared": bool(p.shared), "review_status": p.review_status,
        "license": p.license or None, "source": p.source, "tags": list(p.tags),
        "steps_count": len(p.steps), "has_instructions": bool(p.body),
        "version": p.version or None, "pinned_version": p.pinned_version,
        "use_count": p.use_count, "success_rate": p.success_rate,
        "origin_skill_id": p.origin_skill_id,
        "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
    }
    return {"title": p.name, "fields": fields, "url": "/skills"}


lookup.register(lookup.LookupKind(
    "skill", "skills, catalog entries and ones attached to an agent, with their review state",
    _list_skills, _skill_card, ("/skills",)))


# ── MCP servers ──────────────────────────────────────────────────────────────
# A server's id is unique only within its workspace, so a row's id is
# "<workspace>/<server id>" (the same trick chat/lookup.py uses for a model's
# "<provider>/<model>"); the card and the action both parse it back apart.

def _list_mcp(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from mcp_client.client import cached_tool_names
    from mcp_client import store as mcp_store
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for record in mcp_store.list_servers(ws):
            if not lookup.matches(query, record.get("name"), record.get("id"), record.get("transport")):
                continue
            cached = cached_tool_names(ws, record["id"])
            tools = f"{len(cached)} tools" if cached is not None else "tools not loaded"
            rows.append(lookup.row(
                f"{ws}/{record['id']}", record.get("name") or record["id"],
                f"{'enabled' if record.get('enabled') else 'disabled'} · {record.get('transport')} · {tools}",
                "/mcp", workspace=ws if ctx.workspace is None else None,
            ))
    return rows[:limit]


def _split_mcp_id(entity_id: str) -> tuple:
    ws, _, sid = str(entity_id or "").partition("/")
    return ws, sid


def _mcp_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    ws, sid = _split_mcp_id(entity_id)
    if not sid or not lookup.visible(ctx, ws):
        return None
    from mcp_client import catalog as mcp_catalog
    from mcp_client.client import cached_tool_names
    from mcp_client import store as mcp_store
    record = mcp_store.get_server(ws, sid)
    if record is None:
        return None
    cached = cached_tool_names(ws, sid)
    fields = {
        "id": f"{ws}/{sid}", "name": record.get("name") or sid, "workspace": ws,
        "transport": record.get("transport"), "enabled": bool(record.get("enabled")),
        "approval": record.get("approval"), "tool_allowlist": list(record.get("tool_allowlist") or []),
        "tool_count": len(cached) if cached is not None else None,
        "capabilities": dict(record.get("capabilities") or {}),
        "approved": mcp_catalog.matches(record),
        # The text is the remote server's own words: whether there is one,
        # and the page for what it says.
        "has_error": bool(record.get("last_error")),
    }
    return {"title": record.get("name") or sid, "fields": fields, "url": "/mcp"}


def _mcp_target(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    ws, sid = _split_mcp_id(entity_id)
    if not sid or not lookup.visible(ctx, ws):
        return None
    from mcp_client import store as mcp_store
    record = mcp_store.get_server(ws, sid)
    if record is None:
        return None
    return {"workspace": ws, "label": record.get("name") or sid, "url": "/mcp",
            "enabled": bool(record.get("enabled"))}


def _mcp_enable(ctx: SimpleNamespace, entity_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from mcp_client.client import refresh
    from mcp_client import store as mcp_store
    ws, sid = _split_mcp_id(entity_id)
    if target.get("enabled"):
        raise lookup.LookupError_(f"MCP server {target.get('label')} is already enabled.", code="conflict")
    mcp_store.update_server(ws, sid, {"enabled": True})
    refresh(ws, sid)
    return {}


def _mcp_disable(ctx: SimpleNamespace, entity_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from mcp_client.client import refresh
    from mcp_client import store as mcp_store
    ws, sid = _split_mcp_id(entity_id)
    if not target.get("enabled"):
        raise lookup.LookupError_(f"MCP server {target.get('label')} is already disabled.", code="conflict")
    mcp_store.update_server(ws, sid, {"enabled": False})
    refresh(ws, sid)
    return {}


lookup.register(lookup.LookupKind(
    "mcp", "MCP servers attached to a workspace: status, tool count, approval setting",
    _list_mcp, _mcp_card, ("/mcp",), aliases=("mcp_server",)))

register_action(HubAction(
    "mcp", "enable", "let agents use an MCP server's tools again",
    _mcp_target, _mcp_enable,
    "Enable MCP server {label} in {workspace}: agents can use its tools again."))
register_action(HubAction(
    "mcp", "disable", "stop agents from using an MCP server's tools",
    _mcp_target, _mcp_disable,
    "Disable MCP server {label} in {workspace}: agents stop seeing its tools until it is re-enabled."))


# ── the registry and the marketplace ─────────────────────────────────────────
# Agents, flows, skills and the MCP allowlist all carry the same review state
# (common/review.py). A row's id is "<type>:<id>" (agent, flow, skill or
# mcp_catalog), so one kind can answer for all four. A shared item is, by the
# app's own design, meant to be seen from any workspace — the Marketplace
# page already shows it to everyone — so it is visible here too; an item
# nobody has shared yet stays inside the workspace that owns it.

def _reg_visible(ctx: SimpleNamespace, workspace: Optional[str], shared: bool) -> bool:
    return bool(shared) or (bool(workspace) and lookup.visible(ctx, workspace))


def _list_registry(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from agents import registry as agent_registry
    from flow import store as flow_store
    from mcp_client import catalog as mcp_catalog
    from memory.procedural import all_procedures
    from workspace import is_system_agent
    rows: List[Dict[str, Any]] = []
    for spec in agent_registry.list_agents():
        if is_system_agent(spec.id) or not _reg_visible(ctx, spec.owner_workspace, spec.shared):
            continue
        if not lookup.matches(query, spec.name, spec.owner_workspace, spec.review_status):
            continue
        rows.append(lookup.row(f"agent:{spec.id}", spec.name,
                               f"agent · {spec.review_status or 'draft'} · {spec.owner_workspace or 'global'}",
                               "/marketplace"))
    for flow in flow_store.list_flows():
        ws, shared = flow.get("workspace"), bool(flow.get("shared"))
        if not _reg_visible(ctx, ws, shared):
            continue
        name = flow.get("name") or flow.get("id")
        if not lookup.matches(query, name, ws, flow.get("review_status")):
            continue
        rows.append(lookup.row(f"flow:{flow.get('id')}", name,
                               f"flow · {flow.get('review_status') or 'draft'} · {ws or 'global'}",
                               "/marketplace"))
    for p in all_procedures():
        if not _reg_visible(ctx, p.workspace, p.shared):
            continue
        if not lookup.matches(query, p.name, p.workspace, p.review_status):
            continue
        rows.append(lookup.row(f"skill:{p.id}", p.name,
                               f"skill · {p.review_status or 'draft'} · {p.workspace or 'global'}",
                               "/marketplace"))
    for entry in mcp_catalog.list_all():
        if not lookup.matches(query, entry.get("name"), entry.get("status")):
            continue
        rows.append(lookup.row(f"mcp_catalog:{entry['id']}", entry.get("name") or entry["id"],
                               f"MCP allowlist · {entry.get('status')}", "/marketplace"))
    rows.sort(key=lambda r: str(r.get("label") or "").lower())
    return rows[:limit]


def _registry_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    kind_prefix, sep, raw = str(entity_id or "").partition(":")
    if not sep or not raw:
        return None

    if kind_prefix == "agent":
        from agents import registry as agent_registry
        from workspace import is_system_agent
        spec = agent_registry.get_agent(raw)
        if (spec is None or is_system_agent(spec.id)
                or not _reg_visible(ctx, spec.owner_workspace, spec.shared)):
            return None
        fields = {"id": spec.id, "type": "agent", "name": spec.name, "domain": spec.domain,
                  "owner_workspace": spec.owner_workspace, "shared": bool(spec.shared),
                  "review_status": spec.review_status, "review_note": spec.review_note,
                  "reviewed_by": spec.reviewed_by, "reviewed_at": spec.reviewed_at}
        return {"title": spec.name, "fields": fields, "url": "/marketplace"}

    if kind_prefix == "flow":
        from flow import store as flow_store
        flow = flow_store.get_flow(raw)
        if flow is None or not _reg_visible(ctx, flow.get("workspace"), bool(flow.get("shared"))):
            return None
        name = flow.get("name") or flow.get("id")
        fields = {"id": flow.get("id"), "type": "flow", "name": name,
                  "owner_workspace": flow.get("workspace"), "shared": bool(flow.get("shared")),
                  "review_status": flow.get("review_status"), "review_note": flow.get("review_note"),
                  "reviewed_by": flow.get("reviewed_by"), "reviewed_at": flow.get("reviewed_at")}
        return {"title": name, "fields": fields, "url": "/marketplace"}

    if kind_prefix == "skill":
        from memory.procedural import find_procedure
        p = find_procedure(raw)
        if p is None or not _reg_visible(ctx, p.workspace, p.shared):
            return None
        fields = {"id": str(p.id), "type": "skill", "name": p.name,
                  "owner_workspace": p.workspace, "shared": bool(p.shared),
                  "review_status": p.review_status, "review_note": p.review_note,
                  "reviewed_by": p.reviewed_by, "reviewed_at": p.reviewed_at}
        return {"title": p.name, "fields": fields, "url": "/marketplace"}

    if kind_prefix == "mcp_catalog":
        from mcp_client import catalog as mcp_catalog
        entry = mcp_catalog.get(raw)
        if entry is None:
            return None
        fields = {"id": entry["id"], "type": "mcp_catalog", "name": entry.get("name"),
                  "transport": entry.get("transport"), "status": entry.get("status"),
                  "owner_user": entry.get("owner_user"), "note": entry.get("note"),
                  "reviewed_by": entry.get("reviewed_by"), "created_at": entry.get("created_at")}
        return {"title": entry.get("name") or entry["id"], "fields": fields, "url": "/marketplace"}

    return None


lookup.register(lookup.LookupKind(
    "registry", "published agents, flows and skills with their review state, and the MCP allowlist",
    _list_registry, _registry_card, ("/marketplace",)))


