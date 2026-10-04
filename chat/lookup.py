"""
What the assistant can read about the hub: one catalog of record kinds behind
one tool (``hub_lookup``, tools/hub_lookup.py), as the person sees them.

The assistant answers questions about any page without a tool per page. A
question is a kind ("run", "cost", "approval", ...), an optional search text
and an optional id: without an id the kind lists matching records, with one
it describes that record. Every row and every card carries ``url``, the
dashboard page that shows it, which the assistant links as "show on screen".
New pages are covered by adding a kind here, so the assistant's tool list does
not grow with the dashboard.

The kinds of chat/references.py (task, view, project, scenario, loop, flow,
team, agent, scheduled job) are reused as they are; this module adds what
the reference picker has no use for: runs, sessions, spend, budgets, models,
notifications and the approvals waiting for someone (wave 1 of the assistant
plan).

**As the person.** A lookup reads only the workspaces the person behind the
turn can see (``common.access``), never another person's personal workspace,
and a record from elsewhere answers "not found" rather than "forbidden", so
its existence is not confirmed either.

**Metadata, not content.** A run's answer, a log, a session's messages hold
whatever that run handled, including pages fetched from the web, so they
would make this tool ingest untrusted text; the assistant can also send
messages, and that combination is what the capability guard refuses
(tools/capabilities.py). So cards say what happened (status, timing, cost,
the error's first line) and link the page for the content itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

log = logging.getLogger(__name__)

#: Rows one listing returns at most.
MAX_LIMIT = 30
DEFAULT_LIMIT = 10
#: Longest error text a run card repeats.
MAX_ERROR_CHARS = 300
#: ``workspace`` value that means every workspace the person can see.
ALL_WORKSPACES = "all"


class LookupError_(Exception):
    """A lookup that cannot be answered: the message is for the agent."""

    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


# ── who is asking ────────────────────────────────────────────────────────────

def principal_of(user_id: Optional[str]):
    """The person behind a turn as a principal, or None outside ``multi``
    mode (one operator, who sees everything)."""
    from common import identity
    from common.auth import MULTI, Principal
    if identity.current_mode() != MULTI or not user_id:
        return None
    user = identity.get_user(str(user_id))
    if user is None:
        return None
    return Principal(id=user["id"], username=user["username"], role=user["role"],
                     kind="user", via="assistant")


def reachable_workspaces(principal: Any) -> List[str]:
    """The workspaces this person can work in through the assistant: what
    they can see, without other people's personal workspaces. Their own
    personal workspace first, then ``default``, then the rest by name."""
    from common import access, identity, personal_workspace
    from common.auth import MULTI
    from workspace import list_workspace_folders
    own = None
    if identity.current_mode() == MULTI and principal is not None and getattr(principal, "kind", "") == "user":
        own = personal_workspace.name_for(principal.id)
    out: List[str] = []
    for folder in list_workspace_folders():
        name = folder.name
        if not access.can_see_workspace(principal, name):
            continue
        if personal_workspace.is_reserved_name(name) and name != own:
            continue
        out.append(name)
    return sorted(out, key=lambda n: (n != own, n != "default", n))


def context_for(user_id: Optional[str], workspace: Optional[str], current: Optional[str],
                *, cross_workspace: bool = True) -> SimpleNamespace:
    """Who asks, and where: ``workspace`` (a name, ``all``, or empty for the
    turn's own ``current``) resolved against what the person can reach.

    ``cross_workspace`` False keeps the lookup in ``current`` alone: an agent
    other than the assistant reads its own workspace only
    (docs/workspaces.md), whatever else the person could see."""
    principal = principal_of(user_id)
    reachable = reachable_workspaces(principal)
    if not cross_workspace:
        reachable = [w for w in reachable if w == (current or "default")]
    wanted = str(workspace or "").strip()
    if wanted.lower() == ALL_WORKSPACES:
        targets = list(reachable)
        name = None
    else:
        name = wanted or current or (reachable[0] if reachable else "default")
        if name not in reachable:
            raise LookupError_(
                f"Workspace '{name}' is not one this person can reach. Reachable: "
                + (", ".join(reachable) or "none"), code="not_found")
        targets = [name]
    return SimpleNamespace(principal=principal, user_id=user_id, workspace=name,
                           workspaces=targets, reachable=reachable)


def _visible(ctx: SimpleNamespace, workspace: Optional[str]) -> bool:
    """Whether a record filed under ``workspace`` is one this lookup may show."""
    ws = (workspace or "").strip() or "default"
    return ws in ctx.reachable and (ctx.workspace is None or ws == ctx.workspace)


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(entity_id: Any, label: str, subtitle: str = "", url: Optional[str] = None, **extra) -> Dict[str, Any]:
    out = {"id": str(entity_id), "label": label or str(entity_id), "subtitle": subtitle}
    if url:
        out["url"] = url
    out.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    return out


def _matches(query: str, *values: Any) -> bool:
    q = (query or "").strip().lower()
    return not q or any(q in str(v or "").lower() for v in values)


def _short_time(iso: Any) -> str:
    text = str(iso or "")
    return text[:16].replace("T", " ") if text else ""


def _seconds(start: Any, end: Any) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return round((b - a).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _first_line(text: Any, limit: int = MAX_ERROR_CHARS) -> str:
    line = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return line[:limit]


# ── runs (the Messages page) ─────────────────────────────────────────────────

def _list_runs(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from managers.run_manager import query_runs
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        page = query_runs(workspace=ws, q=query or None, limit=limit + 5)
        for r in page.get("items", []):
            # Bookkeeping (a transcription's price) and the assistant's own
            # turns (this conversation) are not what a person means by "that run".
            if r.get("channel") == "voice" or r.get("message_origin") == "assistant-chat":
                continue
            rows.append(_row(
                r.get("run_id"), r.get("title") or r.get("agent_id") or "run",
                " · ".join(x for x in [r.get("status"), r.get("agent_id"),
                                       _short_time(r.get("started_at") or r.get("created_at"))] if x),
                f"/messages/{quote(str(r.get('run_id')))}", workspace=ws if ctx.workspace is None else None,
                at=r.get("started_at") or r.get("created_at"),
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _run_card(ctx: SimpleNamespace, run_id: str) -> Optional[Dict[str, Any]]:
    from common.pricing import load_price_map, run_cost_usd, run_tokens
    from managers.run_manager import get_run_by_id
    run = get_run_by_id(run_id)
    if run is None or not _visible(ctx, run.get("workspace")):
        return None
    inbound, outbound = run_tokens(run)
    process = run.get("process") or {}
    tools = process.get("tool_calls") if isinstance(process, dict) else None
    fields = {
        "run_id": run.get("run_id"), "title": run.get("title"), "agent": run.get("agent_id"),
        "status": run.get("status"), "workspace": run.get("workspace") or "default",
        "started": run.get("started_at") or run.get("created_at"), "finished": run.get("finished_at"),
        "duration_seconds": _seconds(run.get("started_at") or run.get("created_at"), run.get("finished_at")),
        "model": "/".join(x for x in [run.get("provider"), run.get("model")] if x),
        "tokens": inbound + outbound,
        "cost_usd": round(run_cost_usd(run, load_price_map()), 4),
        "tool_calls": len(tools) if isinstance(tools, list) else None,
        "error": _first_line(run.get("error")),
        "task_id": run.get("task_id") if run.get("session_type") == "task" else None,
        "session_id": run.get("session_id"),
        "launched_by": run.get("launched_by"),
    }
    return {"title": run.get("title") or f"Run {run_id[:8]}", "fields": fields,
            "note": "The run's answer and log are on its page, not here.",
            "url": f"/messages/{quote(run_id)}"}


# ── sessions ─────────────────────────────────────────────────────────────────

def _list_sessions(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from common.session_service import query_contexts
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for s in query_contexts(workspace=ws, limit=max(limit * 3, 30)).get("items", []):
            if not _matches(query, s.get("title"), s.get("agent_id"), s.get("session_id")):
                continue
            rows.append(_row(
                s.get("session_id"), s.get("title") or "session",
                " · ".join(x for x in [s.get("agent_id"), _short_time(s.get("updated_at") or s.get("created_at"))] if x),
                f"/sessions/{quote(str(s.get('session_id')))}", workspace=ws if ctx.workspace is None else None,
                at=s.get("updated_at") or s.get("created_at"),
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _session_card(ctx: SimpleNamespace, session_id: str) -> Optional[Dict[str, Any]]:
    from common.session_service import get_context_by_id
    from managers.run_manager import query_runs
    s = get_context_by_id(session_id)
    if s is None or not _visible(ctx, s.get("workspace")):
        return None
    runs = query_runs(session_id=session_id, limit=5)
    return {"title": s.get("title") or f"Session {session_id[:8]}", "fields": {
        "session_id": session_id, "title": s.get("title"), "agent": s.get("agent_id"),
        "workspace": s.get("workspace") or "default", "created": s.get("created_at"),
        "updated": s.get("updated_at"), "runs": runs.get("total"),
        "latest_runs": [{"run_id": r.get("run_id"), "status": r.get("status"),
                         "at": r.get("started_at") or r.get("created_at")} for r in runs.get("items", [])],
    }, "note": "The messages themselves are on the session's page.",
        "url": f"/sessions/{quote(session_id)}"}


# ── spend ────────────────────────────────────────────────────────────────────

PERIODS = ("today", "week", "month")


def _period_since(period: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


def _person_limit(ctx: SimpleNamespace) -> Optional[Dict[str, Any]]:
    from common import identity
    from common.auth import MULTI
    if identity.current_mode() != MULTI or not ctx.user_id or ctx.principal is None:
        return None
    from common.user_budget import user_budget_status
    status = user_budget_status(str(ctx.user_id))
    return {"spent_this_month_usd": round(status["spend"], 4),
            "limit_usd": status["limit_usd"] or None, "limit_source": status["source"],
            "used_up": status["exceeded"]}


def _list_costs(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    out = []
    for period in PERIODS:
        card = _cost_card(ctx, period)
        total = (card or {}).get("fields", {}).get("total_usd")
        out.append(_row(period, f"Spend {period}", _money(total), "/costs"))
    return out


def _cost_card(ctx: SimpleNamespace, period: str) -> Optional[Dict[str, Any]]:
    from common.costs_report import costs_breakdown
    period = (period or "month").strip().lower()
    if period not in PERIODS:
        raise LookupError_("A cost id is a period: today, week or month.")
    since = _period_since(period)
    report = costs_breakdown(since=since, workspaces=ctx.workspaces)
    totals = report["totals"]
    fields: Dict[str, Any] = {
        "period": period, "since": since,
        "workspaces": ctx.workspaces if ctx.workspace is None else ctx.workspace,
        "total_usd": totals["cost"], "runs": totals["runs"], "tokens": totals["total_tokens"],
        "top_agents": [{"agent": r["label"], "usd": r["cost"], "runs": r["runs"]} for r in report["by_agent"][:5]],
        "top_models": [{"model": r["label"], "usd": r["cost"]} for r in report["by_model"][:3]],
    }
    if ctx.workspace is None:
        fields["by_workspace"] = [{"workspace": r["label"], "usd": r["cost"]} for r in report["by_workspace"]]
    if ctx.user_id and ctx.principal is not None:
        mine = costs_breakdown(since=since, workspaces=ctx.reachable, launched_by=str(ctx.user_id))
        fields["yours_usd"] = mine["totals"]["cost"]
    person = _person_limit(ctx)
    if person:
        fields["your_monthly_limit"] = person
    return {"title": f"Spend this {period}" if period != "today" else "Spend today", "fields": fields,
            "note": "Estimates from the Models page prices, the same numbers as the Costs page.",
            "url": "/costs"}


def _list_budgets(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from common.budget import budget_status
    rows = []
    for ws in ctx.workspaces:
        if not _matches(query, ws):
            continue
        b = budget_status(ws)
        cap = b.get("hard_limit_usd") or 0
        rows.append(_row(ws, ws, f"{_money(b.get('spend'))} of {_money(cap)} ({b.get('period')})"
                         if cap else f"{_money(b.get('spend'))}, no cap", "/costs"))
    return rows[:limit]


def _budget_card(ctx: SimpleNamespace, workspace: str) -> Optional[Dict[str, Any]]:
    from common.budget import budget_status
    ws = workspace or ctx.workspace
    if not ws or ws not in ctx.reachable:
        return None
    b = budget_status(ws)
    fields = {k: b.get(k) for k in ("workspace", "period", "spend", "hard_limit_usd", "soft_limit_usd",
                                     "run_limit_usd", "hard_exceeded", "soft_exceeded")}
    person = _person_limit(ctx)
    if person:
        fields["your_monthly_limit"] = person
    return {"title": f"Budget of {ws}", "fields": fields,
            "note": "Caps are set by an administrator on the Costs page.", "url": "/costs"}


# ── models ───────────────────────────────────────────────────────────────────

def _catalog() -> Dict[str, Any]:
    from providers.catalog import load_catalog_raw
    raw = load_catalog_raw() or {}
    return raw.get("providers", raw) if isinstance(raw, dict) else {}


def _model_url(provider: str, model: str) -> str:
    return f"/models/{quote(provider)}/{quote(model, safe='')}"


def _list_models(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    default = _workspace_model(ctx.workspace) if ctx.workspace else {}
    for provider, entry in _catalog().items():
        for m in (entry or {}).get("models") or []:
            if not m.get("enabled") or not _matches(query, provider, m.get("id")):
                continue
            is_default = default.get("provider") == provider and default.get("model") == m.get("id")
            rows.append(_row(f"{provider}/{m.get('id')}", f"{provider}/{m.get('id')}",
                             ("default here · " if is_default else "")
                             + f"in ${m.get('input_price') or 0}/M, out ${m.get('output_price') or 0}/M",
                             _model_url(provider, str(m.get("id")))))
    return rows[:limit]


def _workspace_model(workspace: Optional[str]) -> Dict[str, str]:
    try:
        from workspace import get_workspace_metadata
        from workspace.storage import get_workspace_default_model_config
        return get_workspace_default_model_config(get_workspace_metadata(workspace)) or {}
    except Exception:  # noqa: BLE001 - no default reads as none
        log.debug("lookup: default model of %s unavailable", workspace, exc_info=True)
        return {}


def _model_card(ctx: SimpleNamespace, model_id: str) -> Optional[Dict[str, Any]]:
    provider, _, model = str(model_id or "").partition("/")
    entry = (_catalog().get(provider) or {})
    found = next((m for m in entry.get("models") or [] if m.get("id") == model), None)
    if found is None:
        return None
    default = _workspace_model(ctx.workspace) if ctx.workspace else {}
    fields = {
        "provider": provider, "model": model, "enabled": bool(found.get("enabled")),
        "input_usd_per_million": found.get("input_price"), "output_usd_per_million": found.get("output_price"),
        "cached_input_usd_per_million": found.get("cached_input_price"),
        "context_window": found.get("context_window"),
        "default_in_this_workspace": default.get("provider") == provider and default.get("model") == model,
    }
    if ctx.workspace:
        from providers import special
        fields["special_models_here"] = {
            purpose: f"{e.get('provider')}/{e.get('model')}" + (f" (from {e['inherited_from']})"
                                                                  if e.get("inherited_from") else "")
            for purpose, e in special.effective(ctx.workspace).items() if purpose != "custom" and e
        }
    return {"title": f"{provider}/{model}", "fields": fields, "url": _model_url(provider, model)}


# ── agents (a fuller card than the reference picker's) ───────────────────────

def _agent_card(ctx: SimpleNamespace, agent_id: str) -> Optional[Dict[str, Any]]:
    from agents.registry import get_agent
    from common.workspace_context import filter_agents_for_workspace
    spec = get_agent(agent_id)
    if spec is None:
        return None
    if ctx.workspace and not filter_agents_for_workspace([spec], ctx.workspace):
        return None
    fields = {
        "agent_id": spec.id, "name": getattr(spec, "name", ""),
        "description": getattr(spec, "description", ""),
        "extends": getattr(spec, "extends", None),
        "model": "/".join(x for x in [getattr(spec, "provider", ""), getattr(spec, "model", "")] if x),
        "tools": list(getattr(spec, "tools", None) or []),
        "delegates_to": list(getattr(spec, "delegates", None) or []),
        "system": bool(getattr(spec, "system", False)),
    }
    return {"title": getattr(spec, "name", "") or agent_id, "fields": fields,
            "url": f"/agents/{quote(agent_id)}"}


# ── what waits for the person ────────────────────────────────────────────────

def _list_notifications(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    from plans.service import list_notifications
    rows = []
    for ws in ctx.workspaces:
        for n in list_notifications(workspace=ws if ws != "default" else None, unread_only=True,
                                    limit=max(limit * 2, 20)):
            n_ws = (getattr(n, "workspace", None) or "").strip() or "default"
            if n_ws != ws or not _matches(query, n.title, n.body):
                continue
            rows.append(_row(n.id, n.title, f"{n.severity} · {_short_time(n.created_at)}",
                             None, workspace=ws if ctx.workspace is None else None,
                             at=str(n.created_at)))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _notification_card(ctx: SimpleNamespace, notification_id: str) -> Optional[Dict[str, Any]]:
    from plans.service import list_notifications, notification_to_dict
    for n in list_notifications(limit=500):
        if str(n.id) != str(notification_id):
            continue
        if not _visible(ctx, getattr(n, "workspace", None)):
            return None
        data = notification_to_dict(n)
        return {"title": data.get("title") or "Notification",
                "fields": {k: data.get(k) for k in ("title", "body", "severity", "workspace", "created_at",
                                                     "read", "source") if k in data}}
    return None


def _list_approvals(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
    """Calls held for a person's answer in a chat, and tasks parked for
    approval, in the workspaces this lookup reads; for a member, only the ones
    they may answer."""
    from common import tool_approvals
    from tasks.models import TaskStatus
    from tasks.service import list_tasks
    rows: List[Dict[str, Any]] = []
    admin = ctx.principal is None or getattr(ctx.principal, "is_admin", False)
    for row in tool_approvals.list_pending(limit=200):
        if tool_approvals.is_expired(row) or not _visible(ctx, row.get("workspace")):
            continue
        if not admin and row.get("owner") and str(row.get("owner")) != str(ctx.user_id):
            continue
        if not _matches(query, row.get("tool"), row.get("reason")):
            continue
        rows.append(_row(row["approval_id"], f"Approve {row.get('tool')}",
                         _first_line(row.get("reason"), 120) or _short_time(row.get("created_at")),
                         f"/messages/{quote(str(row.get('run_id')))}", kind="tool_call",
                         at=row.get("created_at")))
    for task in list_tasks():
        if getattr(task, "status", None) != TaskStatus.awaiting_approval:
            continue
        if not _visible(ctx, getattr(task, "workspace", None)) or not _matches(query, task.title):
            continue
        rows.append(_row(task.id, f"Task waits for approval: {task.title}",
                         _short_time(getattr(task, "updated_at", None)), f"/tasks/{quote(str(task.id))}",
                         kind="task", at=str(getattr(task, "updated_at", "") or "")))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _approval_card(ctx: SimpleNamespace, approval_id: str) -> Optional[Dict[str, Any]]:
    from common import tool_approvals
    row = tool_approvals.get(approval_id)
    if row is None or not _visible(ctx, row.get("workspace")):
        return None
    return {"title": f"Approve {row.get('tool')}", "fields": {
        "approval_id": row.get("approval_id"), "tool": row.get("tool"), "status": row.get("status"),
        "reason": row.get("reason"), "asked_by": row.get("by"), "agent": row.get("agent_id"),
        "run_id": row.get("run_id"), "created": row.get("created_at"), "expires": row.get("expires_at"),
    }, "note": "Answered on its card, by the person whose turn it is or an administrator.",
        "url": f"/messages/{quote(str(row.get('run_id')))}"}


# ── the reference kinds, as the person sees them ─────────────────────────────

def _reference_list(kind: str) -> Callable[[SimpleNamespace, str, int], List[Dict[str, Any]]]:
    def list_fn(ctx: SimpleNamespace, query: str, limit: int) -> List[Dict[str, Any]]:
        from chat.references import list_entities
        rows = []
        for ws in ctx.workspaces:
            for item in list_entities(kind, workspace=ws, query=query, limit=limit):
                rows.append(_row(item["id"], item.get("label", ""), item.get("subtitle", ""), item.get("url"),
                                 workspace=ws if ctx.workspace is None else None))
        return rows[:limit]
    return list_fn


def _reference_card(kind: str) -> Callable[[SimpleNamespace, str], Optional[Dict[str, Any]]]:
    def card_fn(ctx: SimpleNamespace, entity_id: str) -> Optional[Dict[str, Any]]:
        from chat.references import KINDS, render_entity
        rendered = render_entity(kind, entity_id)
        if rendered is None:
            return None
        if rendered.get("workspace") and not _visible(ctx, rendered["workspace"]):
            return None
        return {"title": rendered["title"], "text": rendered["body"], "url": KINDS[kind].path_fn(entity_id)}
    return card_fn


# ── the catalog ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LookupKind:
    kind: str
    #: One line for the tool's description.
    summary: str
    list_fn: Callable[[SimpleNamespace, str, int], List[Dict[str, Any]]]
    card_fn: Callable[[SimpleNamespace, str], Optional[Dict[str, Any]]]
    #: Dashboard routes this kind answers for (the coverage report,
    #: tests/test_hub_lookup.py).
    pages: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = field(default_factory=tuple)


_REFERENCE_SUMMARIES = {
    "task": ("tasks, their status and result", ("/tasks", "/plan")),
    "view": ("views the agents built", ("/views", "/artifacts", "/studio")),
    "project": ("projects", ("/projects",)),
    "scenario": ("playground scenarios", ("/playground",)),
    "loop": ("loops", ("/loops",)),
    "flow": ("flows", ("/flows",)),
    "team": ("teams", ("/teams",)),
    "job": ("scheduled jobs", ("/plan",)),
}

KINDS: Dict[str, LookupKind] = {}


def _register(kind: LookupKind) -> None:
    KINDS[kind.kind] = kind
    for alias in kind.aliases:
        KINDS[alias] = kind


for _kind, (_summary, _pages) in _REFERENCE_SUMMARIES.items():
    _register(LookupKind(_kind, _summary, _reference_list(_kind), _reference_card(_kind), _pages))

_register(LookupKind("agent", "agents, their description, tools and who they delegate to",
                     _reference_list("agent"), _agent_card, ("/agents", "/orchestrator")))
_register(LookupKind("run", "runs (the Messages page): status, timing, cost, error",
                     _list_runs, _run_card, ("/messages", "/run-groups"), aliases=("message",)))
_register(LookupKind("session", "sessions: conversations and the runs in them",
                     _list_sessions, _session_card, ("/sessions", "/chat")))
_register(LookupKind("cost", "spend for a period (id: today, week or month) and the person's limit",
                     _list_costs, _cost_card, ("/costs",)))
_register(LookupKind("budget", "workspace budgets and the person's monthly limit",
                     _list_budgets, _budget_card, ("/costs",)))
_register(LookupKind("model", "enabled models with prices, the default here and the special models",
                     _list_models, _model_card, ("/models",)))
_register(LookupKind("notification", "unread notifications",
                     _list_notifications, _notification_card, ("/dashboard",)))
_register(LookupKind("approval", "tool calls and tasks waiting for someone's approval",
                     _list_approvals, _approval_card, ()))


def kind_names() -> List[str]:
    """The canonical kinds, without aliases."""
    return sorted({k.kind for k in KINDS.values()})


#: Pages the assistant already answers for with a tool of its own rather than
#: a lookup kind (the plan's "ready" rows): route prefix -> tool.
TOOL_PAGES: Dict[str, str] = {
    "/workspaces": "get_workspace",
    "/connectors": "connection_options",
    "/docs": "search_docs",
    "/memory": "search_memory",
    "/files": "read_workspace_file",
}


def covered_pages() -> Dict[str, str]:
    """Dashboard route prefix -> the kind (or ``tool:<name>``) that answers
    for it."""
    out: Dict[str, str] = {}
    for k in KINDS.values():
        for page in k.pages:
            out.setdefault(page, k.kind)
    for page, tool_name in TOOL_PAGES.items():
        out.setdefault(page, f"tool:{tool_name}")
    return out


def lookup(kind: str, *, query: str = "", entity_id: str = "", workspace: str = "",
           limit: int = DEFAULT_LIMIT, user_id: Optional[str] = None,
           current: Optional[str] = None, cross_workspace: bool = True) -> Dict[str, Any]:
    """List records of ``kind`` (no ``entity_id``) or describe one, as the
    person ``user_id`` sees them. ``current`` is the workspace the turn runs
    in, used when ``workspace`` is empty. Raises :class:`LookupError_`."""
    spec = KINDS.get(str(kind or "").strip().lower())
    if spec is None:
        raise LookupError_(f"Unknown kind '{kind}'. Kinds: {', '.join(kind_names())}.")
    ctx = context_for(user_id, workspace, current, cross_workspace=cross_workspace)
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    where = ctx.workspace or ALL_WORKSPACES
    if entity_id:
        card = spec.card_fn(ctx, str(entity_id).strip())
        if card is None:
            raise LookupError_(f"No {spec.kind} '{entity_id}' in {where} for this person.", code="not_found")
        return {"kind": spec.kind, "id": str(entity_id), "workspace": where, **card}
    items = spec.list_fn(ctx, query or "", limit)
    for item in items:
        item.pop("at", None)
    return {"kind": spec.kind, "workspace": where, "query": query or "", "count": len(items), "items": items}


__all__ = ["ALL_WORKSPACES", "KINDS", "TOOL_PAGES", "LookupError_", "LookupKind", "context_for", "covered_pages",
           "kind_names", "lookup", "principal_of", "reachable_workspaces"]
