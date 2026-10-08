"""Lookup kinds: catalog (see chat/lookup_kinds/__init__.py).

Connections, skills, MCP servers, the embeddable widget, the registry
(published agents, flows, skills and the MCP allowlist, with their review
state) and the person's own account. The registry and the marketplace are
the one place this module treats something as visible beyond the person's
own workspaces: an item that has been shared is, by the app's own design,
meant to be seen from anywhere, exactly like the Marketplace page already
shows it to everyone.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from chat import lookup
from chat.actions import HubAction, register_action


# ── connections ──────────────────────────────────────────────────────────────

def _list_connections(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from connections import store as connection_store
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for record in connection_store.list_connections(ws):
            if not lookup.matches(query, record.get("name"), record.get("id"), record.get("kind")):
                continue
            state = "disabled" if record.get("disabled") else "enabled"
            rows.append(lookup.row(
                record["id"], record.get("name") or record["id"],
                " · ".join(x for x in [record.get("kind"), state,
                                       lookup.short_time(record.get("last_seen"))] if x),
                f"/connections/{record['id']}", workspace=ws if ctx.workspace is None else None,
                at=record.get("last_seen") or record.get("created_at"),
            ))
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _connection_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    from connections import store as connection_store
    from managers.run_manager import query_runs
    record = connection_store.get_connection(entity_id)
    if record is None or not lookup.visible(ctx, record.get("workspace")):
        return None
    reported = query_runs(agent_id=entity_id, limit=1)
    fields = {
        "id": record["id"], "name": record.get("name"), "kind": record.get("kind"),
        "description": record.get("description"), "workspace": record.get("workspace") or "default",
        "disabled": bool(record.get("disabled")), "created_at": record.get("created_at"),
        "last_seen": record.get("last_seen"), "reported_runs": reported.get("total", 0),
        "retention_runs": record.get("retention_runs"),
    }
    return {"title": record.get("name") or entity_id, "fields": fields,
            "url": f"/connections/{entity_id}"}


def _connection_target(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    from connections import store as connection_store
    record = connection_store.get_connection(entity_id)
    if record is None or not lookup.visible(ctx, record.get("workspace")):
        return None
    return {"workspace": record.get("workspace") or "default", "label": record.get("name") or entity_id,
            "url": f"/connections/{entity_id}", "disabled": bool(record.get("disabled"))}


def _connection_enable(ctx: SimpleNamespace, entity_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from connections import store as connection_store
    if not target.get("disabled"):
        raise lookup.LookupError_(f"Connection {target.get('label')} is already enabled.", code="conflict")
    connection_store.update_connection(entity_id, {"disabled": False})
    return {}


def _connection_disable(ctx: SimpleNamespace, entity_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from connections import store as connection_store
    if target.get("disabled"):
        raise lookup.LookupError_(f"Connection {target.get('label')} is already disabled.", code="conflict")
    connection_store.update_connection(entity_id, {"disabled": True})
    return {}


lookup.register(lookup.LookupKind(
    "connection", "connections reporting runs into this hub, their volume and whether they are disabled",
    _list_connections, _connection_card, ("/connections",)))

register_action(HubAction(
    "connection", "enable", "accept a connection's reporter again",
    _connection_target, _connection_enable,
    "Enable connection {label} in {workspace}: its token is accepted again."))
register_action(HubAction(
    "connection", "disable", "stop accepting a connection's reporter",
    _connection_target, _connection_disable,
    "Disable connection {label} in {workspace}: its token stops being accepted until re-enabled."))


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


# ── catalog installations of the Slack and Teams apps (docs/distribution.md) ──

def _install_stores(ctx: SimpleNamespace):
    """``(channel, bot workspace, store)`` for every bot whose installations
    this person may see: the default's when default is visible, and each
    visible workspace's own bot."""
    from connectors.channels import registry
    for channel in ("slack", "teams"):
        spec = registry.get(channel)
        if spec is None:
            continue
        for ws in ctx.workspaces:
            if ws == "default" or spec.store.defines(ws):
                yield channel, ws, spec.store.for_workspace(ws)


def _install_id(channel: str, bot_ws: str, org_id: str) -> str:
    return f"{channel}:{bot_ws}:{org_id}"


def _list_installs(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for channel, bot_ws, store in _install_stores(ctx):
        for inst in store.public_installs():
            if not lookup.matches(query, inst.get("name"), inst.get("org_id"), channel,
                                  inst.get("status")):
                continue
            rows.append(lookup.row(
                _install_id(channel, bot_ws, inst["org_id"]), inst.get("name") or inst["org_id"],
                " · ".join(x for x in [channel, inst.get("status"),
                                       f"agent {inst['agent_id']}" if inst.get("agent_id") else "",
                                       lookup.short_time(inst.get("installed_at"))] if x),
                "/distribution", workspace=bot_ws if ctx.workspace is None else None,
                at=inst.get("installed_at")))
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _install_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    channel, _, rest = entity_id.partition(":")
    bot_ws, _, org_id = rest.partition(":")
    for ch, ws, store in _install_stores(ctx):
        if ch != channel or ws != bot_ws:
            continue
        inst = next((i for i in store.public_installs() if i.get("org_id") == org_id), None)
        if inst is None:
            return None
        fields = {"channel": channel, "bot_workspace": bot_ws, **inst}
        return {"title": inst.get("name") or org_id, "fields": fields, "url": "/distribution"}
    return None


lookup.register(lookup.LookupKind(
    "distribution_install",
    "organisations that installed the hub's Slack or Teams app from a catalog: pending or "
    "approved, and the workspace and agent their chats run in",
    _list_installs, _install_card, ("/distribution",), aliases=("slack_install", "teams_install")))


# ── the embeddable widget ────────────────────────────────────────────────────

def _list_widgets(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from widgets import store
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for w in store.list_widgets(ws):
            if not lookup.matches(query, w.get("name"), w.get("agent_id")):
                continue
            rows.append(lookup.row(
                w["widget_id"], w.get("name") or w["widget_id"],
                f"{'on' if w.get('enabled') else 'off'} · agent {w.get('agent_id')}", "/widgets",
                workspace=ws if ctx.workspace is None else None, at=w.get("created_at"),
            ))
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _widget_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    from widgets import store
    w = store.get_widget(entity_id)
    if w is None or not lookup.visible(ctx, w.get("workspace")):
        return None
    fields = {
        "id": w["widget_id"], "name": w.get("name"), "workspace": w.get("workspace") or "default",
        "agent_id": w.get("agent_id"), "enabled": bool(w.get("enabled")),
        "allowed_origins": list(w.get("allowed_origins") or []),
        "accent": w.get("accent"), "language": w.get("language"),
        "thread_count": store.count_threads(w["widget_id"]),
        "created_at": w.get("created_at"), "updated_at": w.get("updated_at"),
    }
    return {"title": w.get("name") or entity_id, "fields": fields, "url": "/widgets"}


def _widget_target(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    from widgets import store
    w = store.get_widget(entity_id)
    if w is None or not lookup.visible(ctx, w.get("workspace")):
        return None
    return {"workspace": w.get("workspace") or "default", "label": w.get("name") or entity_id,
            "url": "/widgets", "enabled": bool(w.get("enabled"))}


def _widget_enable(ctx: SimpleNamespace, entity_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from widgets import service, store
    if target.get("enabled"):
        raise lookup.LookupError_(f"Widget {target.get('label')} is already enabled.", code="conflict")
    widget = store.get_widget(entity_id)
    if widget is None:
        raise lookup.LookupError_(f"No widget '{entity_id}' here.", code="not_found")
    service.update_widget(widget, {"enabled": True})
    return {}


def _widget_disable(ctx: SimpleNamespace, entity_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from widgets import service, store
    if not target.get("enabled"):
        raise lookup.LookupError_(f"Widget {target.get('label')} is already disabled.", code="conflict")
    widget = store.get_widget(entity_id)
    if widget is None:
        raise lookup.LookupError_(f"No widget '{entity_id}' here.", code="not_found")
    service.update_widget(widget, {"enabled": False})
    return {}


lookup.register(lookup.LookupKind(
    "widget", "the embeddable chat widget: its agent, allowed origins and whether it is on",
    _list_widgets, _widget_card, ("/widgets",)))

register_action(HubAction(
    "widget", "enable", "let a widget's visitors talk to it again",
    _widget_target, _widget_enable,
    "Enable widget {label} in {workspace}: visitors can talk to it again."))
register_action(HubAction(
    "widget", "disable", "stop a widget's visitors from talking to it",
    _widget_target, _widget_disable,
    "Disable widget {label} in {workspace}: visitors can no longer open it until it is re-enabled."))


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
                               "/registry"))
    for flow in flow_store.list_flows():
        ws, shared = flow.get("workspace"), bool(flow.get("shared"))
        if not _reg_visible(ctx, ws, shared):
            continue
        name = flow.get("name") or flow.get("id")
        if not lookup.matches(query, name, ws, flow.get("review_status")):
            continue
        rows.append(lookup.row(f"flow:{flow.get('id')}", name,
                               f"flow · {flow.get('review_status') or 'draft'} · {ws or 'global'}",
                               "/registry"))
    for p in all_procedures():
        if not _reg_visible(ctx, p.workspace, p.shared):
            continue
        if not lookup.matches(query, p.name, p.workspace, p.review_status):
            continue
        rows.append(lookup.row(f"skill:{p.id}", p.name,
                               f"skill · {p.review_status or 'draft'} · {p.workspace or 'global'}",
                               "/registry"))
    for entry in mcp_catalog.list_all():
        if not lookup.matches(query, entry.get("name"), entry.get("status")):
            continue
        rows.append(lookup.row(f"mcp_catalog:{entry['id']}", entry.get("name") or entry["id"],
                               f"MCP allowlist · {entry.get('status')}", "/registry"))
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
        return {"title": spec.name, "fields": fields, "url": "/registry"}

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
        return {"title": name, "fields": fields, "url": "/registry"}

    if kind_prefix == "skill":
        from memory.procedural import find_procedure
        p = find_procedure(raw)
        if p is None or not _reg_visible(ctx, p.workspace, p.shared):
            return None
        fields = {"id": str(p.id), "type": "skill", "name": p.name,
                  "owner_workspace": p.workspace, "shared": bool(p.shared),
                  "review_status": p.review_status, "review_note": p.review_note,
                  "reviewed_by": p.reviewed_by, "reviewed_at": p.reviewed_at}
        return {"title": p.name, "fields": fields, "url": "/registry"}

    if kind_prefix == "mcp_catalog":
        from mcp_client import catalog as mcp_catalog
        entry = mcp_catalog.get(raw)
        if entry is None:
            return None
        fields = {"id": entry["id"], "type": "mcp_catalog", "name": entry.get("name"),
                  "transport": entry.get("transport"), "status": entry.get("status"),
                  "owner_user": entry.get("owner_user"), "note": entry.get("note"),
                  "reviewed_by": entry.get("reviewed_by"), "created_at": entry.get("created_at")}
        return {"title": entry.get("name") or entry["id"], "fields": fields, "url": "/registry"}

    return None


lookup.register(lookup.LookupKind(
    "registry", "published agents, flows and skills with their review state, and the MCP allowlist",
    _list_registry, _registry_card, ("/registry", "/agent-registry", "/marketplace")))


# ── the person's own account ─────────────────────────────────────────────────
# Never another person's: the id is always "me", and a card ignores anything
# else. Outside multi mode there is one operator and nothing personal to
# separate out, so the card says only that.

def _list_account(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    label = "you"
    if ctx.principal is not None:
        from common import identity
        user = identity.get_user(ctx.user_id) or {}
        label = user.get("display_name") or user.get("username") or "you"
    return [lookup.row("me", label, "your own account", "/account")]


def _account_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    if str(entity_id or "").strip().lower() != "me":
        return None
    from common import identity
    if ctx.principal is None:
        fields = {"mode": identity.current_mode(), "workspaces_reachable": len(ctx.reachable),
                  "note": "Outside multi mode there is one operator, who holds every role."}
        return {"title": "Your account", "fields": fields, "url": "/account"}

    user = identity.get_user(ctx.user_id)
    if user is None:
        return None
    from common import api_keys, user_budget
    spend = user_budget.user_budget_status(ctx.user_id)
    keys = [
        {"id": k.get("id"), "name": k.get("name") or None, "created_at": k.get("created_at"),
         "last_used_at": k.get("last_used_at"), "scope": k.get("workspaces") or "every reachable workspace"}
        for k in api_keys.list_keys(ctx.user_id)
    ]
    fields = {
        "username": user.get("username"), "role": user.get("role"), "email": user.get("email") or None,
        "workspaces": identity.workspace_roles_for_user(ctx.user_id),
        "monthly_spend_usd": round(spend.get("spend") or 0, 4), "monthly_limit_usd": spend.get("limit_usd"),
        "limit_source": spend.get("source"), "limit_used_up": spend.get("exceeded"),
        "api_keys": keys, "active_sessions": len(identity.list_sessions(ctx.user_id)),
        "preferences": identity.get_preferences(ctx.user_id),
    }
    return {"title": "Your account", "fields": fields, "url": "/account"}


lookup.register(lookup.LookupKind(
    "account", "the person's own account: role, workspaces, spend, personal API keys, sessions, preferences",
    _list_account, _account_card, ("/account",)))
