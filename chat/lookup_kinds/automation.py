"""
Lookup kinds: automation (see chat/lookup_kinds/__init__.py).

Guardrails and their recent events (guardrails/, /guardrails), and the
tool catalog with its recent policy decisions (tools/, /tools).

**Metadata, not content**, is stricter here than elsewhere: a guardrail's
event can hold the text it matched. None of that leaves this
module; every card below is built from named fields, never from spreading a
record's own ``to_dict``.

A guardrail may belong to no workspace at all (``workspace``
None): the global ones every workspace's page shows alongside its own
(guardrails/service.list_guardrails). That is a
different shape of "no workspace" than a run's or a session's (which simply
means "the default workspace"), so :func:`_global_visible` is used for
it instead of ``lookup.visible``.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from chat import actions, lookup
from chat.lookup import LookupError_


def _global_visible(ctx: SimpleNamespace, workspace: Optional[str]) -> bool:
    """A record with no workspace is global, visible from anywhere this
    person can reach; one with a workspace follows the ordinary rule."""
    if not workspace:
        return bool(ctx.reachable)
    return lookup.visible(ctx, workspace)


# ── guardrails ───────────────────────────────────────────────────────────────

def _list_guardrails(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from guardrails import service as guardrails_service
    seen: Dict[str, Dict[str, Any]] = {}
    for ws in ctx.workspaces:
        for g in guardrails_service.list_guardrails(ws):
            if g.id in seen or not lookup.matches(query, g.name, g.kind, g.stage):
                continue
            seen[g.id] = lookup.row(
                g.id, g.name,
                " · ".join(x for x in [g.kind, g.stage, g.action, "enabled" if g.enabled else "disabled"] if x),
                "/guardrails", workspace=(g.workspace or "global") if ctx.workspace is None else None,
                at=g.updated_at,
            )
    rows = sorted(seen.values(), key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _guardrail_event_counts(guardrail_id: str) -> Dict[str, int]:
    from guardrails import store as guardrails_store
    counts = Counter(e.get("action") or "unknown" for e in guardrails_store.list_events(
        guardrail_id=guardrail_id, limit=500))
    return dict(counts)


def _guardrail_card(ctx: SimpleNamespace, guardrail_id: str) -> Optional[Dict[str, Any]]:
    from guardrails import service as guardrails_service
    g = guardrails_service.get_guardrail(guardrail_id)
    if g is None or not _global_visible(ctx, g.workspace):
        return None
    fields = {
        "name": g.name, "workspace": g.workspace or "global", "stage": g.stage, "kind": g.kind,
        "action": g.action, "applies_to": g.applies_to, "enabled": g.enabled,
        "fail_closed": g.fail_closed, "archived": g.archived,
        "created": g.created_at, "updated": g.updated_at,
        "recent_events_by_action": _guardrail_event_counts(guardrail_id),
    }
    return {"title": g.name, "fields": fields,
            "note": "What a guardrail matched never leaves its own event row.", "url": "/guardrails"}


def _guardrail_target(ctx: SimpleNamespace, guardrail_id: str) -> Optional[Dict[str, Any]]:
    """Toggling a global guardrail needs a platform administrator
    (guardrails route's own rule), which this action's single workspace role
    cannot express, so only a workspace's own guardrail is offered here."""
    from guardrails import service as guardrails_service
    g = guardrails_service.get_guardrail(guardrail_id)
    if g is None or not g.workspace or not lookup.visible(ctx, g.workspace):
        return None
    return {"workspace": g.workspace, "label": g.name, "url": "/guardrails"}


def _guardrail_enable(ctx: SimpleNamespace, guardrail_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from guardrails import service as guardrails_service
    g = guardrails_service.require_guardrail(guardrail_id)
    if g.enabled:
        raise LookupError_(f"Guardrail '{g.name}' is already enabled.", code="conflict")
    try:
        g = guardrails_service.update_guardrail(guardrail_id, {"enabled": True})
    except guardrails_service.GuardrailServiceError as exc:
        raise LookupError_(str(exc), code="conflict") from exc
    return {"enabled": g.enabled}


def _guardrail_disable(ctx: SimpleNamespace, guardrail_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from guardrails import service as guardrails_service
    g = guardrails_service.require_guardrail(guardrail_id)
    if not g.enabled:
        raise LookupError_(f"Guardrail '{g.name}' is already disabled.", code="conflict")
    try:
        g = guardrails_service.update_guardrail(guardrail_id, {"enabled": False})
    except guardrails_service.GuardrailServiceError as exc:
        raise LookupError_(str(exc), code="conflict") from exc
    return {"enabled": g.enabled}


lookup.register(lookup.LookupKind(
    "guardrail", "guardrails and, per one, recent event counts by action (never the matched text)",
    _list_guardrails, _guardrail_card, ("/guardrails",),
))
actions.register_action(actions.HubAction(
    "guardrail", "enable", "turn a workspace's own guardrail back on",
    _guardrail_target, _guardrail_enable,
    "Enable guardrail {label} in {workspace}: runs are checked against it again.",
))
actions.register_action(actions.HubAction(
    "guardrail", "disable", "turn off a workspace's own guardrail",
    _guardrail_target, _guardrail_disable,
    "Disable guardrail {label} in {workspace}: runs stop being checked against it until enabled again.",
))


# ── the tool catalog and its policy ───────────────────────────────────────────

def _approval_note(tool_id: str) -> str:
    """Whether a call asks a person first: always, or once a workspace turns
    the approval gate on (tools/approval.py)."""
    from tools.approval import ALWAYS_GATED, needs_approval
    if tool_id in ALWAYS_GATED:
        return "always needs approval"
    return "needs approval when the gate is on" if needs_approval(tool_id) else ""


def _list_tools(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from tools.capabilities import grants_of
    from tools.registry import get_all_tools
    rows = []
    for spec in get_all_tools():
        approval = _approval_note(spec.id)
        if not lookup.matches(query, spec.id, spec.category, spec.description, approval):
            continue
        n = len(grants_of(spec.id))
        rows.append(lookup.row(spec.id, spec.id, " · ".join(x for x in [
            spec.category, f"{n} capabilit{'y' if n == 1 else 'ies'}", approval] if x), "/tools"))
    return rows[:limit]


def _tool_decision_counts(ctx: SimpleNamespace, tool_id: str) -> Dict[str, int]:
    from tools import permission_policy as policy
    counts: Counter = Counter()
    for ws in ctx.workspaces:
        for d in policy.list_decisions(workspace=ws, limit=200):
            if d.get("tool") == tool_id:
                counts[str(d.get("decision") or "unknown")] += 1
    return dict(counts)


def _tool_card(ctx: SimpleNamespace, tool_id: str) -> Optional[Dict[str, Any]]:
    from agents.registry import list_agents
    from common.workspace_context import filter_agents_for_workspace
    from tools import permission_policy as policy
    from tools.approval import ALWAYS_GATED, needs_approval
    from tools.capabilities import grants_of
    from tools.registry import get_tool_by_id
    spec = get_tool_by_id(tool_id)
    if spec is None:
        return None
    held_by: Dict[str, List[str]] = {}
    for ws in ([ctx.workspace] if ctx.workspace else ctx.workspaces):
        agents = filter_agents_for_workspace(list_agents(), ws)
        ids = sorted(a.id for a in agents if tool_id in (getattr(a, "tools", None) or []))
        if ids:
            held_by[ws] = ids
    default_mode = default_source = None
    if ctx.workspace:
        default_mode, default_source = policy.resolve_mode(tool_id, None, ctx.workspace)
    fields = {
        "tool_id": spec.id, "category": spec.category, "description": spec.description,
        "requires_workspace": spec.requires_workspace, "capabilities": sorted(grants_of(tool_id)),
        "always_needs_approval": tool_id in ALWAYS_GATED,
        "needs_approval_when_gate_on": needs_approval(tool_id),
        "default_mode": default_mode, "default_mode_source": default_source,
        "held_by": held_by,
        "recent_decisions_by_outcome": _tool_decision_counts(ctx, tool_id),
    }
    return {"title": spec.id, "fields": fields, "url": "/tools"}


lookup.register(lookup.LookupKind(
    "tool", "the tool catalog: category, capabilities, approval (search 'approval'), who holds it, "
            "recent decisions",
    _list_tools, _tool_card, ("/tools",),
))


__all__: List[str] = []
