"""
Lookup kinds: automation (see chat/lookup_kinds/__init__.py).

Watchers (watchers/, /watchers), pulses (proactive agents, proactive/,
dashboard/backend/routes/agent_proactive.py), eval sets and their runs
(evals/, /evals), guardrails and their recent events (guardrails/,
/guardrails), and the tool catalog with its recent policy decisions
(tools/, /tools).

**Metadata, not content**, is stricter here than elsewhere: a watcher's state
can hold a fetched email's sender and subject or an HTTP body's preview, a
pulse's journal row can hold the agent's own account of what it did, and a
guardrail's event can hold the text it matched. None of that leaves this
module; every card below is built from named fields, never from spreading a
record's own ``to_dict``.

A guardrail or an eval set may belong to no workspace at all (``workspace``
None): the global ones every workspace's page shows alongside its own
(guardrails/service.list_guardrails, evals/store.list_eval_sets). That is a
different shape of "no workspace" than a run's or a session's (which simply
means "the default workspace"), so :func:`_global_visible` is used for these
two kinds instead of ``lookup.visible``.
"""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from chat import actions, lookup
from chat.lookup import LookupError_


def _global_visible(ctx: SimpleNamespace, workspace: Optional[str]) -> bool:
    """A record with no workspace is global, visible from anywhere this
    person can reach; one with a workspace follows the ordinary rule."""
    if not workspace:
        return bool(ctx.reachable)
    return lookup.visible(ctx, workspace)


# ── watchers ─────────────────────────────────────────────────────────────────

def _watcher_target_summary(w: Any) -> str:
    """Where a watcher looks, as a host or a mailbox name: never the full
    URL (a query string can carry a token) and never the config's secret
    names' values."""
    cfg = w.config or {}
    if w.kind == "imap":
        folder = cfg.get("folder") or "INBOX"
        who = cfg.get("username") or ("a Gmail account" if cfg.get("use_google") else "")
        host = cfg.get("host") or ("imap.gmail.com" if cfg.get("use_google") else "")
        return f"{who or host} ({folder})" if (who or host) else ""
    if w.kind == "http":
        return urlparse(cfg.get("url") or "").hostname or ""
    return ""


def _watcher_result(w: Any) -> str:
    if w.last_error:
        return "error"
    if w.last_checked_at is None:
        return "never checked"
    return "ok"


def _list_watchers(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from watchers import service as watchers_service
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for w in watchers_service.list_watchers(ws):
            if not lookup.matches(query, w.name, w.kind):
                continue
            rows.append(lookup.row(
                w.id, w.name,
                " · ".join(x for x in [w.kind, "active" if w.active else (w.paused_reason or "disabled"),
                                       _watcher_result(w)] if x),
                "/watchers", workspace=ws if ctx.workspace is None else None,
                at=w.updated_at,
            ))
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _watcher_card(ctx: SimpleNamespace, watcher_id: str) -> Optional[Dict[str, Any]]:
    from watchers import service as watchers_service
    w = watchers_service.get(watcher_id)
    if w is None or not lookup.visible(ctx, w.workspace):
        return None
    fields = {
        "name": w.name, "workspace": w.workspace, "kind": w.kind,
        "target": _watcher_target_summary(w),
        "interval_seconds": w.interval_seconds,
        "state": "active" if w.active else (f"paused ({w.paused_reason})" if w.paused_reason else "disabled"),
        "last_checked_at": w.last_checked_at.isoformat() if w.last_checked_at else None,
        "last_changed_at": w.last_changed_at.isoformat() if w.last_changed_at else None,
        "result": _watcher_result(w),
        "error": lookup.first_line(w.last_error) if w.last_error else None,
        "consecutive_errors": w.consecutive_errors,
        "fired": w.fired,
    }
    return {"title": w.name, "fields": fields, "url": "/watchers"}


def _watcher_target(ctx: SimpleNamespace, watcher_id: str) -> Optional[Dict[str, Any]]:
    from watchers import service as watchers_service
    w = watchers_service.get(watcher_id)
    if w is None or not lookup.visible(ctx, w.workspace):
        return None
    return {"workspace": w.workspace, "label": w.name, "url": "/watchers"}


def _watcher_pause(ctx: SimpleNamespace, watcher_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from watchers import service as watchers_service
    w = watchers_service.require(watcher_id)
    if w.paused_reason:
        raise LookupError_(f"Watcher '{w.name}' is already paused.", code="conflict")
    watchers_service.pause(watcher_id)
    return {"state": "paused"}


def _watcher_resume(ctx: SimpleNamespace, watcher_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from watchers import service as watchers_service
    w = watchers_service.require(watcher_id)
    if w.enabled and not w.paused_reason:
        raise LookupError_(f"Watcher '{w.name}' is not paused.", code="conflict")
    watchers_service.resume(watcher_id)
    return {"state": "active"}


lookup.register(lookup.LookupKind(
    "watcher", "watchers over a mailbox or an HTTP resource: state, last check, error",
    _list_watchers, _watcher_card, ("/watchers",),
))
actions.register_action(actions.HubAction(
    "watcher", "pause", "pause a watcher: it stops checking until resumed",
    _watcher_target, _watcher_pause,
    "Pause watcher {label} in {workspace}: it stops checking until resumed.",
))
actions.register_action(actions.HubAction(
    "watcher", "resume", "resume a paused watcher",
    _watcher_target, _watcher_resume,
    "Resume watcher {label} in {workspace}: it checks again on its usual interval.",
))


# ── pulses (proactive agents) ─────────────────────────────────────────────────

def _pulse_workspace(spec: Any, profile: Dict[str, Any]) -> str:
    return str(profile.get("workspace") or getattr(spec, "owner_workspace", None) or "default")


def _list_pulses(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from agents.registry import list_agents
    from plans import service as plans
    from proactive import service as proactive_service
    rows: List[Dict[str, Any]] = []
    for spec in list_agents():
        profile = proactive_service.profile_of(spec)
        if not profile.get("enabled") or not profile.get("job_id"):
            continue
        ws = _pulse_workspace(spec, profile)
        if ws not in ctx.workspaces:
            continue
        name = getattr(spec, "name", None) or spec.id
        if not lookup.matches(query, spec.id, name):
            continue
        job = plans.get_job(profile["job_id"])
        status = job.status.value if job else "unknown"
        rows.append(lookup.row(
            spec.id, name, f"{status} · {proactive_service.describe_schedule(profile)}",
            f"/agents/{quote(spec.id)}", workspace=ws if ctx.workspace is None else None,
            at=str(getattr(job, "updated_at", "") or ""),
        ))
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _pulse_card(ctx: SimpleNamespace, agent_id: str) -> Optional[Dict[str, Any]]:
    from agents.registry import get_agent
    from plans import service as plans
    from proactive import service as proactive_service
    spec = get_agent(agent_id)
    if spec is None:
        return None
    profile = proactive_service.profile_of(spec)
    ws = _pulse_workspace(spec, profile)
    if not lookup.visible(ctx, ws):
        return None
    job = plans.get_job(profile["job_id"]) if profile.get("job_id") else None
    usage = proactive_service.daily_usage(job, profile) if job is not None else None
    ticks = []
    if job is not None:
        for f in plans.fire_store.list_for_job(job.id, limit=10):
            ticks.append({
                "at": f.at.isoformat() if f.at else None, "trigger": f.trigger, "ok": f.ok,
                "outcome": f.outcome, "error": lookup.first_line(f.error) if f.error else None,
                "cost_usd": f.cost_usd, "duration_ms": f.duration_ms,
            })
    fields = {
        "agent_id": spec.id, "name": getattr(spec, "name", None) or spec.id, "workspace": ws,
        "enabled": bool(profile.get("enabled")),
        "schedule": proactive_service.describe_schedule(profile),
        "timezone": profile.get("timezone"), "quiet_hours": profile.get("quiet_hours"),
        "trigger_kinds": sorted({str(t.get("kind")) for t in (profile.get("triggers") or [])
                                 if isinstance(t, dict) and t.get("kind")}),
        "notify": profile.get("notify"),
        "daily_budget_usd": profile.get("daily_budget_usd") or None,
        "tick_budget_usd": profile.get("tick_budget_usd"),
        "max_runs_per_day": profile.get("max_runs_per_day") or None,
        "job_status": job.status.value if job else None,
        "paused_reason": job.paused_reason if job else None,
        "next_run_at": job.run_at.isoformat() if job and job.status.value == "scheduled" and job.run_at else None,
        "pending_events": len(job.pending_events or []) if job else 0,
        "fire_count": job.fire_count if job else 0,
        "today": usage,
        "recent_ticks": ticks,
    }
    return {"title": getattr(spec, "name", None) or agent_id, "fields": fields,
            "note": "Each tick's own account of what it did is on the agent's Pulse tab.",
            "url": f"/agents/{quote(agent_id)}"}


def _pulse_target(ctx: SimpleNamespace, agent_id: str) -> Optional[Dict[str, Any]]:
    from agents.registry import get_agent
    from proactive import service as proactive_service
    spec = get_agent(agent_id)
    if spec is None:
        return None
    profile = proactive_service.profile_of(spec)
    ws = _pulse_workspace(spec, profile)
    if not lookup.visible(ctx, ws):
        return None
    return {"workspace": ws, "label": getattr(spec, "name", None) or spec.id,
            "url": f"/agents/{quote(agent_id)}"}


def _pulse_pause(ctx: SimpleNamespace, agent_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from proactive import service as proactive_service
    try:
        job = proactive_service.pause(agent_id)
    except (LookupError, ValueError) as exc:
        raise LookupError_(str(exc), code="conflict") from exc
    return {"job_status": job.get("status")}


def _pulse_resume(ctx: SimpleNamespace, agent_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from proactive import service as proactive_service
    try:
        job = proactive_service.resume(agent_id)
    except (LookupError, ValueError) as exc:
        raise LookupError_(str(exc), code="conflict") from exc
    return {"job_status": job.get("status")}


lookup.register(lookup.LookupKind(
    "pulse", "proactive agents: their schedule, budget and recent ticks' outcomes",
    _list_pulses, _pulse_card, (),
))
actions.register_action(actions.HubAction(
    "pulse", "pause", "pause an agent's pulse: it stops ticking until resumed",
    _pulse_target, _pulse_pause,
    "Pause the pulse of {label} in {workspace}: it stops ticking until resumed.",
))
actions.register_action(actions.HubAction(
    "pulse", "resume", "resume a paused pulse",
    _pulse_target, _pulse_resume,
    "Resume the pulse of {label} in {workspace}: it ticks again on its usual schedule.",
))


# ── evals (sets and runs) ─────────────────────────────────────────────────────

def _eval_set_row(e: Any, *, ctx: SimpleNamespace) -> Dict[str, Any]:
    from evals import store as eval_store
    runs = eval_store.list_eval_runs(e.eval_set_id, limit=1)
    latest = f"last run {lookup.short_time(runs[0].started_at)} ({runs[0].status})" if runs else "never run"
    return lookup.row(
        e.eval_set_id, e.name or e.eval_set_id, f"{len(e.cases)} cases · {latest}",
        "/evals", workspace=(e.workspace or "global") if ctx.workspace is None else None,
        at=e.updated_at,
    )


def _list_evals(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from evals import store as eval_store
    seen: Dict[str, Dict[str, Any]] = {}
    for ws in ctx.workspaces:
        for e in eval_store.list_eval_sets(ws):
            if e.eval_set_id in seen or not lookup.matches(query, e.name, e.target_kind, e.target_id):
                continue
            seen[e.eval_set_id] = _eval_set_row(e, ctx=ctx)
    rows = sorted(seen.values(), key=lambda r: str(r.get("at") or ""), reverse=True)
    return rows[:limit]


def _eval_set_card(ctx: SimpleNamespace, e: Any) -> Dict[str, Any]:
    from evals import store as eval_store
    runs = eval_store.list_eval_runs(e.eval_set_id, limit=5)
    fields = {
        "eval_set_id": e.eval_set_id, "name": e.name, "workspace": e.workspace or "global",
        "target_kind": e.target_kind, "target_id": e.target_id,
        "case_count": len(e.cases), "graders": [{"kind": g.kind, "weight": g.weight} for g in e.graders],
        "suggest_on_failure": e.suggest_on_failure,
        "created": e.created_at, "updated": e.updated_at,
        "recent_runs": [{"eval_run_id": r.eval_run_id, "status": r.status, "mode": r.mode,
                         "started": r.started_at, "total_cost_usd": r.total_cost} for r in runs],
    }
    return {"title": e.name or e.eval_set_id, "fields": fields,
            "note": "Cases and scores are on the eval set's page, not here.", "url": "/evals"}


def _eval_run_card(ctx: SimpleNamespace, r: Any) -> Dict[str, Any]:
    from evals import store as eval_store
    e = eval_store.get_eval_set(r.eval_set_id)
    scores = {label: {k: v for k, v in (s or {}).items() if k in ("score", "passed", "total", "cost")}
             for label, s in (r.summary or {}).items()}
    fields = {
        "eval_run_id": r.eval_run_id, "eval_set_id": r.eval_set_id,
        "eval_set_name": e.name if e else None, "workspace": r.workspace or "global",
        "status": r.status, "mode": r.mode, "started": r.started_at, "finished": r.finished_at,
        "error": lookup.first_line(r.error) if r.error else None,
        "total_cost_usd": r.total_cost, "scores": scores,
    }
    return {"title": f"Eval run of {e.name}" if e else f"Eval run {r.eval_run_id}", "fields": fields,
            "note": "The score matrix and each case's output are on the run's page.", "url": "/evals"}


def _eval_card(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
    from evals import store as eval_store
    e = eval_store.get_eval_set(entity_id)
    if e is not None:
        if not _global_visible(ctx, e.workspace):
            return None
        return _eval_set_card(ctx, e)
    r = eval_store.get_eval_run(entity_id)
    if r is not None:
        if not _global_visible(ctx, r.workspace):
            return None
        return _eval_run_card(ctx, r)
    return None


def _eval_run_target(ctx: SimpleNamespace, eval_run_id: str) -> Optional[Dict[str, Any]]:
    from evals import store as eval_store
    r = eval_store.get_eval_run(eval_run_id)
    if r is None or not _global_visible(ctx, r.workspace):
        return None
    e = eval_store.get_eval_set(r.eval_set_id)
    label = f"of {e.name}" if e else eval_run_id[:8]
    return {"workspace": r.workspace or "default", "label": label, "url": "/evals"}


def _eval_run_cancel(ctx: SimpleNamespace, eval_run_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from evals.batch import cancel_run
    try:
        r = cancel_run(eval_run_id)
    except ValueError as exc:
        raise LookupError_(str(exc), code="conflict") from exc
    return {"status": r.status}


lookup.register(lookup.LookupKind(
    "eval", "eval sets and their runs (an eval run's id works here too)",
    _list_evals, _eval_card, ("/evals",),
))
actions.register_action(actions.HubAction(
    "eval", "cancel", "cancel a batch eval run's open provider batches",
    _eval_run_target, _eval_run_cancel,
    "Cancel eval run {label} in {workspace}: scoring stops where it is; cells already answered are kept.",
))


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
