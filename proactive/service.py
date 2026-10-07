"""
The pulse itself: the job a profile owns, the gates a tick passes, the
tick's prompt, and what happens when its run finishes (docs/proactive.md).

Three entry points, in the order a tick goes through them:

* :func:`save_profile` keeps the agent record and its ``heartbeat`` job in
  step: switching the pulse on creates the job (one per agent), editing the
  schedule updates it, switching it off cancels it. The job id is kept on
  the profile either way, so the tick feed survives an off-and-on.
* :func:`fire_heartbeat` is what ``plans.service.fire_job`` calls for a
  heartbeat job. It checks the quiet hours, the day's budget and tick
  count, and whether the previous tick is still running; a tick that fails
  a gate is a *skipped* tick with that outcome on its journal row, not a
  failure. One that passes becomes an ordinary task of the agent, with the
  tick's prompt as its text and the tick's answer schema on its run.
* :func:`on_task_run_finished` is called from the run finalizer for every
  finished task run. For a tick's task it reads the structured answer off
  the task result, prices the run, writes outcome, summary, next check and
  cost onto the journal row, notifies the person when the agent acted (or is
  blocked, once per reason), keeps the agent's ``heartbeat`` memory block
  current, and feeds the scheduler's consecutive-error counter so a pulse
  whose runs keep failing pauses itself.

Everything here is best-effort around the agent's run: a journal, memory or
notification hiccup is logged and never turns a finished run into a failure.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from proactive.profile import (
    EDITABLE_KEYS,
    ERROR_OUTCOME,
    RUN_OUTCOMES,
    TICK_SCHEMA,
    describe_schedule,
    local_day_bounds,
    normalize_profile,
    profile_tz,
    quiet_window_end,
    schedule_cron,
    validate_profile,
)

log = logging.getLogger(__name__)

#: The activity-log entry a tick's task is created with; the finalizer
#: recognises a tick's task by it (plans.service._fire_agent_task).
TICK_ACTIVITY = "heartbeat_fire"

#: The core memory block the pulse keeps in the agent's primary pool.
MEMORY_BLOCK = "heartbeat"
MEMORY_BLOCK_DESCRIPTION = (
    "The state of your pulse: what you watch, what you last saw, and what to "
    "check on the next tick. Rewritten after every tick; keep it short."
)

#: Task statuses that mean the previous tick is still going (a new one would
#: run beside it, which is what the ``busy`` gate prevents).
_ACTIVE_TASK_STATUSES = frozenset({
    "todo", "ready", "pending", "in_progress", "awaiting_input", "awaiting_approval",
})

#: How many journal rows back the daily sums and the "last tick" look. A
#: five-minute pulse makes 288 rows a day; the window covers a day of those
#: with room to spare.
_JOURNAL_WINDOW = 600

#: Events waiting on a job at most; older ones are dropped first.
MAX_PENDING_EVENTS = 50
#: How long a wake waits for more events before the tick starts, so ten file
#: saves in a row become one tick with ten events.
DEFAULT_EVENT_WINDOW_SECONDS = 30
EVENT_WINDOW_ENV = "AGENTS_HUB_HEARTBEAT_EVENT_WINDOW_SECONDS"

#: Outcomes the journal compaction folds into counted rows: the ticks that
#: did nothing. Acted, blocked and error rows are kept one by one.
QUIET_LIKE_OUTCOMES = frozenset({"quiet", "quiet_hours", "busy", "rate", "budget", "disabled"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── Profile ──────────────────────────────────────────────────────────────────

def profile_of(spec: Any) -> Dict[str, Any]:
    """The agent's normalized profile (defaults filled in)."""
    return normalize_profile(getattr(spec, "proactive", None) or {})


def get_profile(agent_id: str) -> Optional[Dict[str, Any]]:
    """The agent's profile, or None for an unknown agent."""
    from agents import registry
    spec = registry.get_agent(agent_id)
    return profile_of(spec) if spec else None


def save_profile(agent_id: str, patch: Dict[str, Any], *, actor: Optional[str] = None) -> Dict[str, Any]:
    """Merge *patch* into the agent's profile, validate, sync the job, save.

    Only :data:`proactive.profile.EDITABLE_KEYS` are taken from the patch;
    ``job_id`` is the service's. Raises ``LookupError`` for an unknown agent
    and ``ValueError`` for a profile that does not validate (the route turns
    both into their HTTP codes). Returns the saved profile.
    """
    from agents import registry
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise LookupError(f"Agent '{agent_id}' not found")
    current = profile_of(spec)
    merged = dict(current)
    for key in EDITABLE_KEYS:
        if key in patch:
            merged[key] = patch[key]
    profile = validate_profile(merged)
    guard_profile(spec, profile)
    profile = sync_job(spec, profile)
    registry.add_agent(replace(spec, proactive=profile), actor=actor,
                       note="proactive profile changed")
    return profile


def untrusted_trigger_kinds(profile: Dict[str, Any]) -> List[str]:
    """The profile's trigger kinds that carry text from outside the hub."""
    from proactive.events import UNTRUSTED_TRIGGER_KINDS
    kinds = {str(t.get("kind")) for t in (profile.get("triggers") or []) if isinstance(t, dict)}
    return sorted(k for k in kinds if k in UNTRUSTED_TRIGGER_KINDS)


def guard_profile(spec: Any, profile: Dict[str, Any]) -> None:
    """The capability guard's view of a profile (docs/proactive.md, "Security").

    A webhook, Telegram or file trigger feeds the agent text nobody here
    wrote, so it counts as ``ingests_untrusted``; a delivery channel other
    than the inbox carries an agent-written summary out, so it counts as
    ``can_exfiltrate``. Folded into the same trifecta check the tool list
    goes through at save time (``tools.capabilities.check_combination``):
    a blocking combination is refused in ``block`` mode unless the operator
    set ``capability_override``. Raises ``ValueError``.
    """
    untrusted = untrusted_trigger_kinds(profile)
    if not untrusted:
        return
    from agents.capability_guard import guard_mode
    from tools.capabilities import CAN_EXFILTRATE, INGESTS_UNTRUSTED, check_combination, secret_grant_ids
    extra = {INGESTS_UNTRUSTED: "trigger:" + ",".join(untrusted)}
    outbound = [c for c in (profile.get("notify") or []) if c != "dashboard"]
    if outbound:
        extra[CAN_EXFILTRATE] = "notify:" + ",".join(outbound)
    tools = list(getattr(spec, "tools", None) or []) + secret_grant_ids(getattr(spec, "secrets", None))
    violation = check_combination(tools, extra)
    if violation is None or not violation.blocking:
        return
    if guard_mode() != "block" or getattr(spec, "capability_override", False):
        log.warning("proactive: agent %r keeps a blocked combination through its profile: %s",
                    spec.id, violation.message)
        return
    raise ValueError(f"the capability guard refuses this profile: {violation.message}")


def tick_tool_policy(spec: Any, profile: Dict[str, Any]) -> Dict[str, str]:
    """The tool policy a tick runs under: for a profile with an untrusted
    trigger, every outbound tool of the agent is set to ``always_ask``, so
    the tick prepares the action and a person approves it from the inbox
    (tools/permission_policy.py). Empty for every other profile."""
    if not untrusted_trigger_kinds(profile):
        return {}
    from tools.capabilities import CAN_EXFILTRATE, grants_of
    return {str(t): "always_ask" for t in (getattr(spec, "tools", None) or [])
            if CAN_EXFILTRATE in grants_of(str(t))}


def _job_workspace(spec: Any, profile: Dict[str, Any]) -> str:
    return str(profile.get("workspace") or getattr(spec, "owner_workspace", None) or "default")


def workspace_of(agent_id: str) -> Optional[str]:
    """The workspace the agent's pulse runs in, or None for an unknown agent:
    the one the routes check a person's role against."""
    from agents import registry
    spec = registry.get_agent(agent_id)
    return _job_workspace(spec, profile_of(spec)) if spec else None


def _first_run_at(cron: str, tz_name: str) -> datetime:
    from croniter import croniter
    tz = profile_tz({"timezone": tz_name})
    nxt = croniter(cron, datetime.now(tz)).get_next(datetime)
    return nxt.astimezone(timezone.utc)


def sync_job(spec: Any, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Make the plans store match the profile; returns the profile with
    ``job_id`` set.

    Enabled: create the heartbeat job, or update the existing one (schedule,
    brief, channels, budget, environment) and bring a cancelled one back to
    ``scheduled``. A manual pause survives an edit: only ``cancelled`` is
    reset. Disabled: cancel the job if there is one, keep its id.
    """
    from plans import service as plans
    from plans.models import JobKind, JobStatus, Recurrence

    out = dict(profile)
    job = plans.get_job(out["job_id"]) if out.get("job_id") else None
    if job is not None and job.kind != JobKind.heartbeat:
        job = None  # a stale id pointing at somebody else's job

    if not out["enabled"]:
        if job is not None and job.status != JobStatus.cancelled:
            plans.cancel_job(job.id)
        return out

    cron = schedule_cron(out)
    fields: Dict[str, Any] = dict(
        title=f"{getattr(spec, 'name', None) or spec.id}: heartbeat",
        message=out["brief"],
        recurrence=Recurrence.cron,
        cron=cron,
        timezone=out["timezone"],
        agent_id=spec.id,
        channels=list(out["notify"] or ["dashboard"]),
        environment_id=out["environment_id"],
        budget_usd=out["tick_budget_usd"],
        auto_pause_after=int(out["auto_pause_after"]),
    )
    workspace = _job_workspace(spec, out)
    if job is None:
        job = plans.create_job(
            kind=JobKind.heartbeat, run_at=_first_run_at(cron, out["timezone"]),
            workspace=workspace, created_by="system", **fields,
        )
        out["job_id"] = str(job.id)
        return out

    schedule_changed = job.cron != cron or (job.timezone or "UTC") != out["timezone"]
    if job.status == JobStatus.cancelled:
        fields.update(status=JobStatus.scheduled, paused_reason=None, consecutive_errors=0,
                      run_at=_first_run_at(cron, out["timezone"]))
    elif schedule_changed:
        fields["run_at"] = _first_run_at(cron, out["timezone"])
    if job.workspace != workspace:
        fields["workspace"] = workspace
    plans.update_job(job.id, **fields)
    out["job_id"] = str(job.id)
    return out


# ── Journal reads ────────────────────────────────────────────────────────────

def _journal(job_id: Any, limit: int = _JOURNAL_WINDOW) -> List[Any]:
    from plans import service as plans
    try:
        return plans.fire_store.list_for_job(job_id, limit=limit)
    except Exception:  # noqa: BLE001 - an unreadable journal means "no history", not a stuck pulse
        log.debug("heartbeat: journal read failed for job %s", job_id, exc_info=True)
        return []


def daily_usage(job: Any, profile: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Today's ticks and spend, over the profile's local calendar day.

    Counts the ticks that started a task (a skipped one costs nothing and
    does not use up the day's allowance) and sums their ``cost_usd``, which
    :func:`on_task_run_finished` wrote when each run finished. A tick still
    running counts as a run and as zero spend until then.
    """
    now = now or _now()
    start, end = local_day_bounds(profile, now)
    rows = _journal(job.id)
    today = [r for r in rows if r.task_id and start <= (_aware(r.at) or start) < end]
    spent = sum(float(r.cost_usd or 0.0) for r in today)
    return {
        "day": start.astimezone(profile_tz(profile)).date().isoformat(),
        "runs": len(today),
        "spent_usd": round(spent, 6),
        "max_runs_per_day": int(profile.get("max_runs_per_day") or 0),
        "daily_budget_usd": float(profile.get("daily_budget_usd") or 0.0),
    }


def _last_run_tick(rows: List[Any], exclude_id: Any = None) -> Optional[Any]:
    """The newest journal row that started a task, other than *exclude_id*."""
    for r in rows:
        if exclude_id is not None and str(r.id) == str(exclude_id):
            continue
        if r.task_id:
            return r
    return None


# ── The tick ─────────────────────────────────────────────────────────────────

def _format_ago(then: Optional[datetime], now: datetime) -> str:
    then = _aware(then)
    if then is None:
        return "unknown"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 90:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 36:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def tick_prompt(spec: Any, profile: Dict[str, Any], *, last: Optional[Any], usage: Dict[str, Any],
                now: datetime, events: Optional[List[Dict[str, Any]]] = None) -> str:
    """The task text of one tick: the brief, what the last tick left, the
    day's allowance, the rules and the answer format."""
    lines: List[str] = [
        "[HEARTBEAT] You are waking up on your own schedule. Nobody asked you a question; "
        "this tick is yours to decide.",
        "",
        "## Your brief",
        profile.get("brief") or "(no brief given: check your usual sources and act only when clearly warranted)",
        "",
        "## Since the last tick",
    ]
    if last is None:
        lines.append("This is your first tick.")
    else:
        when = _format_ago(last.at, now)
        outcome = last.outcome or "still running"
        lines.append(f"Last tick: {when}, outcome {outcome}.")
        if last.summary:
            lines.append(f"What you said then: {last.summary}")
        if last.next_check:
            lines.append(f"What you asked yourself to check now: {last.next_check}")
    if events:
        lines.append("")
        lines.append("## What woke you")
        for ev in events:
            lines.append(f"- {ev.get('kind', 'event')}: {ev.get('summary') or json.dumps(ev, ensure_ascii=False)[:300]}")
    else:
        lines.append("")
        lines.append(f"Reason for this tick: the schedule ({describe_schedule(profile)}).")
    budget = usage.get("daily_budget_usd") or 0
    max_runs = usage.get("max_runs_per_day") or 0
    allowance = []
    if budget:
        allowance.append(f"${usage.get('spent_usd', 0):.2f} of ${budget:.2f} spent today")
    if max_runs:
        allowance.append(f"tick {usage.get('runs', 0) + 1} of {max_runs} today")
    if allowance:
        lines.append("Allowance: " + "; ".join(allowance) + ".")
    lines += [
        "",
        "## Rules",
        "- Look at your sources first. Act only when the brief says to act and something "
        "changed since the last tick.",
        "- Doing nothing is a normal outcome. Do not report the same thing twice: if an "
        "earlier tick already said it, stay quiet unless it changed.",
        "- Your summary is what the person reads when you acted; put the message to them "
        "there. Do not send separate notifications for what the summary already says.",
        "- When you need a person's decision, or a permission you do not have, answer "
        "\"blocked\" and say exactly what is missing.",
        "- Write down in next_check what to look at next time and any state worth carrying "
        "over, so the next tick does not start from nothing.",
        "- If you have a memory block named \"heartbeat\", it is yours: it holds what you "
        "watch and last saw. It is rewritten from your answer after each tick.",
        "",
        "## Answer",
        "End with a single JSON object and nothing after it:",
        json.dumps({"outcome": "acted | quiet | blocked", "summary": "...", "next_check": "..."}),
    ]
    return "\n".join(lines)


def _task_status(task_id: str) -> Optional[str]:
    try:
        from tasks import service as tasks_service
        task = tasks_service.get_task(UUID(str(task_id)))
    except Exception:  # noqa: BLE001 - an unreadable task is treated as gone
        return None
    if task is None:
        return None
    status = getattr(task, "status", None)
    return getattr(status, "value", status)


def fire_heartbeat(job: Any, *, trigger: str = "schedule",
                   events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """One tick: the gates, then the task. Called by ``plans.service.fire_job``.

    Returns ``{"task_id": ...}`` when a task was started, or
    ``{"outcome": <skip>, "summary": ..., "resume_at"?: ...}`` for a tick that
    a gate stopped: ``disabled`` (the profile is off), ``quiet_hours`` (with
    the window's end as ``resume_at``), ``busy`` (the previous tick's task is
    still active), ``rate`` and ``budget`` (the day's allowance is spent). A
    manual wake (``trigger="manual"``) passes the quiet hours, since a person
    asked, but still obeys the budget, the tick limit and the busy gate.

    Raises for a missing agent, which the scheduler classifies as
    ``agent_missing`` and pauses the job on.
    """
    from agents import registry
    from plans import service as plans

    if not job.agent_id:
        raise ValueError("heartbeat job has no agent_id")
    spec = registry.get_agent(job.agent_id)
    if spec is None:
        raise ValueError(f"Unknown agent_id: {job.agent_id}")
    profile = profile_of(spec)
    now = _now()

    if not profile["enabled"]:
        return {"outcome": "disabled", "summary": "the proactive profile is switched off"}

    if trigger != "manual":
        end = quiet_window_end(profile, now)
        if end is not None:
            local_end = end.astimezone(profile_tz(profile)).strftime("%H:%M")
            return {"outcome": "quiet_hours", "resume_at": end,
                    "summary": f"inside quiet hours until {local_end} {profile['timezone']}"}

    if job.created_task_ids:
        previous = str(job.created_task_ids[-1])
        status = _task_status(previous)
        if status in _ACTIVE_TASK_STATUSES:
            return {"outcome": "busy", "summary": f"the previous tick's task {previous} is still {status}"}

    usage = daily_usage(job, profile, now)
    max_runs = int(profile.get("max_runs_per_day") or 0)
    if max_runs and usage["runs"] >= max_runs:
        return {"outcome": "rate", "summary": f"{usage['runs']} of {max_runs} ticks already ran today"}
    budget = float(profile.get("daily_budget_usd") or 0.0)
    if budget and usage["spent_usd"] >= budget:
        return {"outcome": "budget",
                "summary": f"${usage['spent_usd']:.2f} of today's ${budget:.2f} already spent"}

    fresh = plans.get_job(job.id) or job
    pending = list(fresh.pending_events or [])
    events = [*pending, *(events or [])] or None
    last = _last_run_tick(_journal(job.id))
    prompt = tick_prompt(spec, profile, last=last, usage=usage, now=now, events=events)
    launch_params: Dict[str, Any] = {"output_schema": TICK_SCHEMA}
    policy = tick_tool_policy(spec, profile)
    if policy:
        launch_params["tool_policy"] = policy
    task_id = plans._fire_agent_task(
        job, description=prompt, notify=False, launch_params=launch_params, activity=TICK_ACTIVITY,
    )
    if pending:
        # Handed to the tick; a wake arriving from here on waits for the next one.
        plans.plan_store.update(job.id, pending_events=[])
    return {"task_id": task_id, "events": len(pending)}


# ── Waking outside the schedule ──────────────────────────────────────────────

def event_window_seconds() -> int:
    try:
        value = int(os.environ.get(EVENT_WINDOW_ENV, "") or DEFAULT_EVENT_WINDOW_SECONDS)
    except ValueError:
        value = DEFAULT_EVENT_WINDOW_SECONDS
    return max(0, value)


def wake_agent(agent_id: str, event: Dict[str, Any], *, window_seconds: Optional[int] = None) -> Dict[str, Any]:
    """Hand *event* to the agent's pulse and pull its next tick close.

    The event joins the job's ``pending_events``; the job's ``run_at`` moves
    to now plus the batching window unless it is already sooner, so several
    events within the window become one tick that sees them all. The tick
    that then fires goes through the usual gates: inside the quiet hours it
    waits for the window's end with the events kept, a busy pulse keeps them
    for its next slot. A paused pulse keeps the events too and ticks when it
    is resumed. Returns ``{"ok": bool, ...}`` and never raises for a pulse
    that is off (``reason: "disabled"``).
    """
    from agents import registry
    from plans import service as plans
    from plans.models import JobKind, JobStatus

    spec = registry.get_agent(agent_id)
    if spec is None:
        return {"ok": False, "reason": "unknown_agent"}
    profile = profile_of(spec)
    job = plans.get_job(profile["job_id"]) if profile.get("enabled") and profile.get("job_id") else None
    if job is None or job.kind != JobKind.heartbeat or job.status == JobStatus.cancelled:
        return {"ok": False, "reason": "disabled"}

    events = [*(job.pending_events or []), dict(event)][-MAX_PENDING_EVENTS:]
    fields: Dict[str, Any] = {"pending_events": events}
    now = _now()
    window = event_window_seconds() if window_seconds is None else max(0, int(window_seconds))
    target = now + timedelta(seconds=window)
    if job.status == JobStatus.scheduled and _aware(job.run_at) > target:
        fields["run_at"] = target
    updated = plans.update_job(job.id, **fields) or job
    return {
        "ok": True, "job_id": str(job.id), "pending": len(events),
        "run_at": _aware(updated.run_at).isoformat() if updated.run_at else None,
        "paused": updated.status == JobStatus.paused,
    }


# ── When the run finishes ────────────────────────────────────────────────────

def _tick_job_id(task_id: str) -> Optional[str]:
    """The heartbeat job that created *task_id*, from the task's activity
    log, or None for any other task. One read per finished task run."""
    try:
        from tasks import service as tasks_service
        entries = tasks_service.get_task_activity_log(UUID(str(task_id)))
    except Exception:  # noqa: BLE001 - no log means not a tick
        return None
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("type") == TICK_ACTIVITY and entry.get("job_id"):
            return str(entry["job_id"])
    return None


def _parse_answer(text: Optional[str]) -> Dict[str, Any]:
    """``{outcome, summary, next_check}`` from the agent's answer.

    The run's structured-output extension already validated the JSON when
    the schema reached the run; an answer that still is not one (a node-mode
    run, an older child) is read as ``acted`` with the text as its summary,
    since an agent that wrote prose most likely did something.
    """
    from agents.loop_ext.structured import _extract_json
    value = _extract_json(text or "")
    if isinstance(value, dict) and str(value.get("outcome") or "").strip().lower() in RUN_OUTCOMES:
        return {
            "outcome": str(value["outcome"]).strip().lower(),
            "summary": str(value.get("summary") or "").strip(),
            "next_check": str(value.get("next_check") or "").strip() or None,
        }
    plain = (text or "").strip()
    return {"outcome": "acted" if plain else "quiet", "summary": plain[:1000], "next_check": None}


def _task_cost(task_id: str) -> float:
    try:
        from common.pricing import load_price_map, run_cost_usd
        from tasks import service as tasks_service
        prices = load_price_map()
        runs = tasks_service.get_task_runs(UUID(str(task_id)))
        return round(sum(run_cost_usd(r, prices) for r in runs), 6)
    except Exception:  # noqa: BLE001 - an unpriced tick costs nothing rather than breaking the finalizer
        log.debug("heartbeat: could not price task %s", task_id, exc_info=True)
        return 0.0


def _write_memory_block(spec: Any, workspace: Optional[str], text: str) -> None:
    """Rewrite the agent's ``heartbeat`` core block in its primary pool.
    Nothing happens for an agent without a pool."""
    try:
        from memory.binding import effective_memory_pools
        from memory.store import MemoryStore
        pools = effective_memory_pools(spec, workspace)
        if not pools:
            return
        store = MemoryStore()
        pool = store.get(pools[0])
        if pool is None:
            return
        block = pool.get_block(MEMORY_BLOCK)
        limit = block.limit_chars if block else 2000
        pool.upsert_block(MEMORY_BLOCK, value=text[:limit], description=MEMORY_BLOCK_DESCRIPTION)
        pool.touch()
        store.add(pool)
    except Exception:  # noqa: BLE001 - memory is a convenience for the next tick, never a reason to fail this one
        log.debug("heartbeat: memory block write failed for %s", getattr(spec, "id", None), exc_info=True)


def _notify(spec: Any, job: Any, profile: Dict[str, Any], *, task_id: str, outcome: str, summary: str) -> None:
    from plans import service as plans
    name = getattr(spec, "name", None) or spec.id
    first_line = (summary.strip().splitlines() or [""])[0]
    if outcome == "blocked":
        title = f"{name} is blocked"
        severity = "warning"
    else:
        title = f"{name}: {first_line[:80]}" if first_line else f"{name} acted"
        severity = "info"
    try:
        plans.create_notification(
            title=title, body=summary, severity=severity,
            source={"job_id": str(job.id), "task_id": str(task_id), "agent_id": spec.id,
                    "heartbeat": outcome},
            workspace=job.workspace,
            channels=list(profile.get("notify") or ["dashboard"]),
        )
    except Exception:  # noqa: BLE001 - the inbox is a side channel of the journal row, which is already written
        log.debug("heartbeat: notification failed for job %s", job.id, exc_info=True)


def _count_run_error(job: Any, profile: Dict[str, Any], error: Optional[str]) -> None:
    """Feed the scheduler's consecutive-error counter with a failed run.

    The firing itself succeeded, so ``fire_job`` never counts a run that
    fails afterwards; this is the other half, with the same threshold and
    the same pause the scheduler applies (docs/scheduling.md, "Auto pause").
    """
    from plans import service as plans
    from plans.models import JobStatus
    fresh = plans.get_job(job.id) or job
    count = int(fresh.consecutive_errors or 0) + 1
    fields: Dict[str, Any] = {"consecutive_errors": count, "last_error": error}
    threshold = int(profile.get("auto_pause_after") or 0)
    if threshold and count >= threshold and fresh.status == JobStatus.scheduled:
        fields.update(status=JobStatus.paused, paused_reason="errors")
        plans._notify_job_paused(fresh, error, count)
    plans.update_job(job.id, **fields)


def on_task_run_finished(task_id: str, run: Dict[str, Any], status: str) -> None:
    """Close a tick whose task run just finished; a no-op for any other task.

    Called from ``managers.runs.task_finalize.finalize_task_from_run`` after
    the task result is stored. Idempotent: a journal row that already has an
    outcome is left alone, so a run finalized twice (a watchdog and the
    child racing) writes once.
    """
    job_id = _tick_job_id(task_id)
    if not job_id:
        return
    from agents import registry
    from common import audit
    from plans import service as plans
    from plans.models import JobKind

    job = plans.get_job(job_id)
    if job is None or job.kind != JobKind.heartbeat:
        return
    rows = _journal(job.id)
    row = next((r for r in rows if str(r.task_id or "") == str(task_id)), None)
    if row is None or row.outcome:
        return
    spec = registry.get_agent(job.agent_id or "")
    profile = profile_of(spec) if spec else normalize_profile({})

    if status == "completed":
        try:
            from tasks import service as tasks_service
            text = tasks_service.get_task_result(UUID(str(task_id)))
        except Exception:  # noqa: BLE001 - an unreadable result reads as an empty answer
            text = None
        answer = _parse_answer(text)
    else:
        error = str(run.get("error") or "") or f"run {status}"
        answer = {"outcome": ERROR_OUTCOME, "summary": error[:1000], "next_check": None}

    cost = _task_cost(task_id)
    try:
        plans.fire_store.update(
            row.id, outcome=answer["outcome"], summary=answer["summary"] or None,
            next_check=answer["next_check"], cost_usd=cost,
        )
    except Exception:  # noqa: BLE001 - the row is the record of the tick; log loudly but do not raise
        log.warning("heartbeat: could not write the outcome of tick %s", row.id, exc_info=True)

    outcome = answer["outcome"]
    if outcome == ERROR_OUTCOME:
        try:
            _count_run_error(job, profile, answer["summary"])
        except Exception:  # noqa: BLE001
            log.debug("heartbeat: error counter failed for job %s", job.id, exc_info=True)
    else:
        try:
            if int(job.consecutive_errors or 0):
                plans.update_job(job.id, consecutive_errors=0, last_error=None)
        except Exception:  # noqa: BLE001
            log.debug("heartbeat: error counter reset failed for job %s", job.id, exc_info=True)

    if spec is not None:
        if outcome == "acted":
            _notify(spec, job, profile, task_id=task_id, outcome=outcome, summary=answer["summary"])
        elif outcome == "blocked":
            # Once per reason: the previous run tick already said this.
            previous = _last_run_tick(rows, exclude_id=row.id)
            same = (previous is not None and previous.outcome == "blocked"
                    and (previous.summary or "").strip() == (answer["summary"] or "").strip())
            if not same:
                _notify(spec, job, profile, task_id=task_id, outcome=outcome, summary=answer["summary"])

        when = _now().strftime("%Y-%m-%d %H:%M UTC")
        block = [f"Last tick {when}: {outcome}."]
        if answer["summary"]:
            block.append(answer["summary"])
        if answer["next_check"]:
            block.append(f"Next check: {answer['next_check']}")
        _write_memory_block(spec, job.workspace, "\n".join(block))

        if outcome == "acted":
            audit.record(
                "agent.heartbeat.acted",
                actor={"actor_id": spec.id, "actor_kind": "agent", "actor_name": getattr(spec, "name", None)},
                object_type="agent", object_id=spec.id, workspace=job.workspace,
                details={"job_id": str(job.id), "task_id": str(task_id), "cost_usd": cost,
                         "summary": (answer["summary"] or "")[:500]},
            )

    try:
        from common.session_broker import notify_change
        notify_change("plan", job_id=str(job.id))
    except Exception:  # noqa: BLE001 - a missed UI refresh is all that is lost
        log.debug("heartbeat: plan change notice failed", exc_info=True)


# ── Journal compaction (common/maintenance.py) ──────────────────────────────

def compact_journal(days: int, *, now: Optional[datetime] = None) -> int:
    """Fold a heartbeat's quiet and skipped ticks older than *days* into one
    row per UTC day and outcome, carrying their ``count`` and summed cost.
    Acted, blocked and error rows stay as they are. Returns the rows removed;
    ``0`` days turns it off."""
    if days <= 0:
        return 0
    from plans import service as plans
    from plans.models import JobKind
    now = now or _now()
    cutoff = now - timedelta(days=days)
    removed = 0
    for job in plans.list_jobs():
        if job.kind != JobKind.heartbeat:
            continue
        rows = plans.fire_store.list_for_job(job.id, limit=100000)
        groups: Dict[Any, List[Any]] = {}
        for r in rows:
            at = _aware(r.at)
            if at is None or at >= cutoff or r.outcome not in QUIET_LIKE_OUTCOMES or r.error:
                continue
            groups.setdefault((at.date().isoformat(), r.outcome), []).append(r)
        for group in groups.values():
            if len(group) < 2:
                continue
            keep, rest = group[0], group[1:]  # newest first
            count = sum(int(r.count or 1) for r in group)
            cost = sum(float(r.cost_usd or 0.0) for r in group)
            try:
                plans.fire_store.update(keep.id, count=count, cost_usd=round(cost, 6) if cost else keep.cost_usd,
                                        task_id=None, summary=None, next_check=None)
                for r in rest:
                    removed += plans.fire_store.delete(r.id)
            except Exception:  # noqa: BLE001 - a group that cannot be folded is left for the next sweep
                log.debug("heartbeat: compaction of job %s failed", job.id, exc_info=True)
    return removed


# ── The Dashboard widget ─────────────────────────────────────────────────────

def summary(workspace: Optional[str] = None, *, hours: int = 24) -> Dict[str, Any]:
    """Every agent with a pulse on, and what its ticks of the last *hours*
    came to: acted, quiet, blocked, error, skipped (by budget, tick limit,
    busy or quiet hours), with the budget skips counted on their own."""
    from agents import registry
    from plans import service as plans
    from plans.models import JobKind, JobStatus
    since = _now() - timedelta(hours=hours)
    agents: List[Dict[str, Any]] = []
    totals = {"agents": 0, "acted": 0, "quiet": 0, "blocked": 0, "error": 0, "skipped": 0,
              "budget": 0, "running": 0, "paused": 0}
    for spec in registry.list_agents():
        profile = profile_of(spec)
        if not profile.get("enabled") or not profile.get("job_id"):
            continue
        job = plans.get_job(profile["job_id"])
        if job is None or job.kind != JobKind.heartbeat or job.status == JobStatus.cancelled:
            continue
        if workspace and _job_workspace(spec, profile) != workspace:
            continue
        counts = {"acted": 0, "quiet": 0, "blocked": 0, "error": 0, "skipped": 0, "budget": 0, "running": 0}
        for r in _journal(job.id):
            at = _aware(r.at)
            if at is None or at < since:
                continue
            n = int(r.count or 1)
            if r.outcome in ("acted", "quiet", "blocked", "error"):
                counts[r.outcome] += n
            elif r.outcome in QUIET_LIKE_OUTCOMES:
                counts["skipped"] += n
                if r.outcome == "budget":
                    counts["budget"] += n
            elif r.task_id and not r.outcome:
                counts["running"] += 1
        row = {
            "agent_id": spec.id, "name": getattr(spec, "name", None) or spec.id,
            "workspace": _job_workspace(spec, profile), "status": job.status.value,
            "paused_reason": job.paused_reason, "schedule": describe_schedule(profile),
            "next_run_at": _aware(job.run_at).isoformat() if job.run_at and job.status == JobStatus.scheduled else None,
            "pending_events": len(job.pending_events or []),
            **counts,
        }
        agents.append(row)
        totals["agents"] += 1
        totals["paused"] += 1 if job.status == JobStatus.paused else 0
        for key in counts:
            totals[key] += counts[key]
    agents.sort(key=lambda a: (a["status"] != "scheduled", a["name"].lower()))
    return {"hours": hours, "workspace": workspace, "agents": agents, "totals": totals}


# ── Status, pause, resume, wake (the Pulse tab) ──────────────────────────────

def status(agent_id: str, *, limit: int = 50) -> Optional[Dict[str, Any]]:
    """Everything the agent's Pulse tab shows, or None for an unknown agent:
    the profile, the job (next run, status), today's usage and the newest
    ticks."""
    from agents import registry
    from plans import service as plans

    spec = registry.get_agent(agent_id)
    if spec is None:
        return None
    profile = profile_of(spec)
    job = plans.get_job(profile["job_id"]) if profile.get("job_id") else None
    ticks: List[Dict[str, Any]] = []
    usage: Optional[Dict[str, Any]] = None
    if job is not None:
        usage = daily_usage(job, profile)
        ticks = [plans.fire_to_dict(r) for r in _journal(job.id, limit=max(1, limit))]
    return {
        "agent_id": spec.id,
        "profile": profile,
        "schedule": describe_schedule(profile),
        "job": plans.job_to_dict(job) if job is not None else None,
        "usage": usage,
        "ticks": ticks,
    }


def _job_for(agent_id: str) -> Any:
    from plans import service as plans
    profile = get_profile(agent_id)
    if profile is None:
        raise LookupError(f"Agent '{agent_id}' not found")
    job = plans.get_job(profile["job_id"]) if profile.get("job_id") else None
    if job is None:
        raise ValueError("this agent has no pulse yet; enable the proactive profile first")
    return job


def pause(agent_id: str) -> Dict[str, Any]:
    from plans import service as plans
    job = _job_for(agent_id)
    updated = plans.pause_job(job.id)
    if updated is None:
        raise ValueError(f"the pulse cannot be paused from status '{job.status.value}'")
    return plans.job_to_dict(updated)


def resume(agent_id: str) -> Dict[str, Any]:
    from plans import service as plans
    job = _job_for(agent_id)
    updated = plans.resume_job(job.id)
    if updated is None:
        raise ValueError(f"the pulse is not paused (status '{job.status.value}')")
    return plans.job_to_dict(updated)


def wake(agent_id: str) -> Dict[str, Any]:
    """Tick now, outside the schedule. Passes the quiet hours (a person
    asked), still obeys the budget, the tick limit and the busy gate."""
    from plans import service as plans
    from plans.models import JobStatus
    job = _job_for(agent_id)
    if job.status not in (JobStatus.scheduled, JobStatus.paused):
        raise ValueError(f"the pulse cannot be woken from status '{job.status.value}'")
    claimed = plans.claim_job_now(job.id)
    if claimed is None:
        raise RuntimeError("the pulse is being fired elsewhere right now; try again shortly")
    return plans.fire_job(claimed, trigger="manual")


__all__ = [
    "TICK_ACTIVITY", "MEMORY_BLOCK", "MAX_PENDING_EVENTS", "QUIET_LIKE_OUTCOMES",
    "profile_of", "get_profile", "save_profile", "sync_job", "guard_profile", "tick_tool_policy",
    "daily_usage", "tick_prompt", "fire_heartbeat", "wake_agent", "on_task_run_finished",
    "compact_journal", "summary", "status", "pause", "resume", "wake",
]
