"""
Schedule management tools.

Let agents schedule future user notifications, agent tasks, agent runs, flows,
loops and memory consolidations (Plan page), and run those jobs as deployments (Deployments
page: resources, auto pause, the firing journal). Jobs are written to the
shared plans store; the backend scheduler fires them when due.

These tools belong to the Planner (agents/definitions/planner): the main
agent delegates scheduling to it rather than holding them. Secrets are the one
deployment field no tool sets: a secret is a private-data grant to another
agent's runs, which the person gives on the Deployments page.

Named schedule_* (not plan_*) to avoid colliding with the reasoning layer's
auto-injected plan-store tools (save_plan / get_plan / list_plans / ...).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from common.entity_sink import record_entity
from common.workspace_context import filter_agents_for_workspace, resolve_active_workspace
from common.auto_mode import approved as _approved
from common.workspace_scope import check_record, current_agent, is_service_wide
from plans.models import JobKind, JobStatus, Recurrence
from plans import service as plan_service
from plans.service import job_to_dict
from tools._json import json_err as _json_err, json_ok as _json_ok


# -------------------- helpers --------------------

def _record_job(job, action: str) -> None:
    """Report a scheduled job this run touched, so the reply can link to it."""
    if job is None:
        return
    record_entity("job", str(job.id), action, (job.title or "").strip())


def _job_hidden(job) -> bool:
    """Whether ``job`` belongs to another workspace than this run's: answered
    like a missing job (``common.workspace_scope.check_record``). A job with
    no workspace is the default one's; the service's own agents reach every
    job, and a call with no workspace (the CLI) is left as it was."""
    return check_record((getattr(job, "workspace", None) or "").strip() or None,
                        what="job", workspace=resolve_active_workspace()) is not None


def _agent_unavailable(agent_id: str) -> bool:
    """Whether ``agent_id`` is unknown, or not available in this run's
    workspace (the notion the workspace's agent list uses). A job that would
    run such an agent here is refused like an unknown agent."""
    from agents.registry import get_agent as reg_get_agent
    spec = reg_get_agent(agent_id)
    if spec is None:
        return True
    ws = resolve_active_workspace()
    if not ws or is_service_wide(current_agent()):
        return False
    return not filter_agents_for_workspace([spec], ws)


def _criterion_note(agent_id: Optional[str], use_agent_outcome: bool) -> str:
    """What the reply tells the person about the agent's default criterion:
    whether the agent has one, and whether the job's tasks use it."""
    if not agent_id:
        return (" Tasks will be graded against the default criterion of the agent the orchestrator picks."
                if use_agent_outcome else "")
    try:
        from agents.registry import get_agent as reg_get_agent
        spec = reg_get_agent(agent_id)
    except Exception:  # noqa: BLE001 - the note is a courtesy, the job is created either way
        return ""
    rubric = str(((getattr(spec, "default_outcome", None) or {}).get("rubric")) or "").strip()
    if not rubric:
        return (f" Agent '{agent_id}' has no default criterion, so its tasks are not graded."
                if use_agent_outcome else "")
    if use_agent_outcome:
        return (f" Every task will be graded against agent '{agent_id}'s default criterion and sent back "
                "for another attempt while it falls short. Tell the person.")
    return (f" Agent '{agent_id}' has a default criterion; these tasks do not use it. Tell the person "
            "in one sentence that they can turn it on (use_agent_outcome).")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_from_str(value: str) -> UUID:
    s = str(value).strip()
    try:
        return UUID(s)
    except ValueError:
        hex_only = s.replace("-", "").replace(" ", "")
        if len(hex_only) == 32 and all(c in "0123456789abcdefABCDEF" for c in hex_only):
            return UUID(f"{hex_only[:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:]}")
        raise ValueError(
            f"Invalid job ID '{s}'. Use the exact job ID from list_scheduled without modification."
        )


def _resolve_run_at(
    run_at: Optional[str],
    delay_minutes: Optional[int],
) -> datetime:
    """Resolve the firing time from an ISO string or a relative delay."""
    if run_at:
        dt = datetime.fromisoformat(run_at.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    if delay_minutes is not None:
        return _now() + timedelta(minutes=int(delay_minutes))
    raise ValueError("Provide run_at (ISO datetime) or delay_minutes")


def _effective_recurrence(recurrence: Optional[Recurrence], cron: Optional[str]) -> Optional[Recurrence]:
    """A cron expression means a cron schedule whatever ``recurrence`` says:
    the model often sends only ``cron``, or ``cron`` beside 'weekly'."""
    if (cron or "").strip():
        return Recurrence.cron
    return recurrence


_INTERVALS = {
    Recurrence.hourly: timedelta(hours=1),
    Recurrence.daily: timedelta(days=1),
    Recurrence.weekly: timedelta(weeks=1),
}


def _next_cron_time(cron: Optional[str], tz: Optional[str], not_before: Optional[datetime] = None) -> datetime:
    """The first cron occurrence at or after ``not_before`` (default now),
    computed by the scheduler's own ``_next_run`` via ``upcoming_runs``."""
    start = (not_before or _now()) - timedelta(seconds=1)
    return datetime.fromisoformat(plan_service.upcoming_runs(cron, tz, count=1, start=start)[0])


def _resolve_first_run(
    run_at: Optional[str],
    delay_minutes: Optional[int],
    recurrence: Recurrence,
    cron: Optional[str] = None,
    tz: Optional[str] = None,
) -> tuple[datetime, Recurrence]:
    """The first firing time and the recurrence to store.

    A cron schedule needs no start time: its first run is the next cron
    occurrence, or the first one at or after ``run_at``/``delay_minutes``
    when given, so the job never fires off its grid. Hourly, daily and weekly
    without a time start one interval from now; a one-off job needs
    ``run_at`` or ``delay_minutes``.
    """
    recurrence = _effective_recurrence(recurrence, cron) or Recurrence.none
    if recurrence == Recurrence.cron:
        given = _resolve_run_at(run_at, delay_minutes) if (run_at or delay_minutes is not None) else None
        return _next_cron_time(cron, tz, given), recurrence
    if not run_at and delay_minutes is None and recurrence in _INTERVALS:
        return _now() + _INTERVALS[recurrence], recurrence
    return _resolve_run_at(run_at, delay_minutes), recurrence


def _check_when(model: BaseModel) -> BaseModel:
    """Shared schema check: only a one-off job must say when it runs."""
    recurrence = _effective_recurrence(getattr(model, "recurrence", None), getattr(model, "cron", None))
    if recurrence == Recurrence.cron:
        if not (getattr(model, "cron", None) or "").strip():
            raise ValueError("recurrence='cron' requires a cron expression, e.g. '0 9 * * 1-5'")
        return model
    if recurrence in (None, Recurrence.none) and not model.run_at and model.delay_minutes is None:
        raise ValueError(
            "Provide run_at or delay_minutes for a one-off job (a recurring one needs "
            "neither: cron starts at the next cron time, hourly/daily/weekly one interval from now)"
        )
    return model


_TIME_HELP = (
    "Timing: pass either `run_at` (ISO 8601, e.g. '2026-06-13T09:00:00+00:00'; "
    "naive values are treated as UTC) or `delay_minutes` (relative to now). "
    "A recurring job needs neither. A `cron` expression always makes the job a "
    "cron schedule (whatever `recurrence` says): the first run is the next cron "
    "time, or the first one at or after run_at when given. hourly/daily/weekly "
    "without a time start one interval from now; for a set day or hour, use cron. "
    "Prefer delay_minutes for relative requests like 'in 2 hours' (=120). "
    "The response includes `current_time` and the resolved `run_at` — verify it "
    "matches the user's intent. If you need the current date/time first, call "
    "list_scheduled: its response also includes `current_time`."
)


# -------------------- Tool schemas --------------------

class ScheduleNotificationInput(BaseModel):
    title: str = Field(..., min_length=1, description="Short notification title shown to the user")
    message: str = Field("", description="Notification body text")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime when to notify (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(None, description="Cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="IANA timezone name, e.g. 'Europe/Berlin'. Defaults to UTC.")
    telegram: bool = Field(
        False,
        description="When it fires, also deliver to the user's bound Telegram chat(s). Default false = inbox/bell only.",
    )

    @model_validator(mode="after")
    def _check_when(self):
        return _check_when(self)


class ScheduleTaskInput(BaseModel):
    title: str = Field(..., min_length=1, description="Title of the task to create when the time comes")
    description: str = Field("", description="Detailed description of the work the agent should do")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime when to start the task (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(
        None,
        description="Cron expression, required when recurrence='cron' (e.g. '0 9 * * 1-5' for weekdays at 9am)",
    )
    timezone: Optional[str] = Field(
        None,
        description="IANA timezone name for recurrence timing, e.g. 'Europe/Berlin'. Defaults to UTC.",
    )
    agent_id: Optional[str] = Field(
        None,
        description="Id of the agent to run the task. Always set it when the person named an agent; "
                    "omit only when they named none, and the orchestrator picks one at fire time.",
    )
    telegram: bool = Field(
        False,
        description="Also send the 'task started' notification to the user's bound Telegram chat(s). Default false = inbox/bell only.",
    )
    agent_version: Optional[int] = Field(
        None, ge=1, description="Pin the runs to a stored version of agent_id (requires agent_id).",
    )
    project_id: Optional[str] = Field(None, description="Project the created tasks belong to.")
    file_ids: Optional[List[str]] = Field(
        None, description="Workspace file ids (file_...) every run finds in its working directory.",
    )
    memory_pool_ids: Optional[List[str]] = Field(
        None, description="Memory pools (ids or names) bound for these runs instead of the agent's own binding.",
    )
    memory_access: Optional[str] = Field(
        None, description="How the runs may use memory_pool_ids: 'read' (default) or 'write'.",
    )
    use_agent_outcome: bool = Field(
        False,
        description="Grade every created task against the agent's default criterion. Only when the "
                    "person asked for it; off by default.",
    )
    auto_pause_after: int = Field(
        3, ge=0, description="Consecutive failed firings that pause the job; 0 turns auto pause off.",
    )

    @model_validator(mode="after")
    def _check_when(self):
        return _check_when(self)


class ScheduleAgentRunInput(BaseModel):
    title: str = Field(..., min_length=1, description="Name of the job, shown on the Plan page and as the conversation title")
    agent_id: str = Field(..., description="Id of the agent to run (list_agents_tool)")
    message: str = Field(..., min_length=1, description="What the agent is asked to do, as if the person wrote it in a chat")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime of the first run (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(None, description="Cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="IANA timezone name, e.g. 'Europe/Berlin'. Defaults to UTC.")
    agent_version: Optional[int] = Field(None, ge=1, description="Pin the runs to a stored version of the agent.")
    project_id: Optional[str] = Field(None, description="Project each run's conversation belongs to.")
    file_ids: Optional[List[str]] = Field(
        None, description="Workspace file ids (file_...) attached to the message of every run.",
    )
    memory_pool_ids: Optional[List[str]] = Field(
        None, description="Memory pools (ids or names) bound for these runs instead of the agent's own binding.",
    )
    memory_access: Optional[str] = Field(
        None, description="How the runs may use memory_pool_ids: 'read' (default) or 'write'.",
    )
    auto_pause_after: int = Field(
        3, ge=0, description="Consecutive failed firings that pause the job; 0 turns auto pause off.",
    )

    @model_validator(mode="after")
    def _check_when(self):
        return _check_when(self)


class ScheduleFlowInput(BaseModel):
    title: str = Field(..., min_length=1, description="Name of the job, shown on the Plan and Deployments pages")
    flow_id: str = Field(..., description="Id of the flow to run (list_flows_tool)")
    message: str = Field("", description="What the run is for; the flow gets it as its request")
    seed: Optional[Dict[str, Any]] = Field(
        None, description="JSON object merged into the flow's initial state on every run",
    )
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime of the first run (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(None, description="Cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="IANA timezone name, e.g. 'Europe/Berlin'. Defaults to UTC.")
    max_concurrent: int = Field(1, ge=0, description="Runs of this job allowed at once; 0 means no limit")
    auto_pause_after: int = Field(
        3, ge=0, description="Consecutive failed firings that pause the job; 0 turns auto pause off.",
    )

    @model_validator(mode="after")
    def _check_when(self):
        return _check_when(self)


class ScheduleLoopInput(BaseModel):
    title: str = Field(..., min_length=1, description="Name of the job, shown on the Plan and Deployments pages")
    loop_id: str = Field(..., description="Id of the loop to start (list_loops_tool)")
    message: str = Field("", description="What the run is for; the loop gets it as its goal")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime of the first run (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(None, description="Cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="IANA timezone name, e.g. 'Europe/Berlin'. Defaults to UTC.")
    auto_pause_after: int = Field(
        3, ge=0, description="Consecutive failed firings that pause the job; 0 turns auto pause off.",
    )
    user_approved: bool = Field(
        False,
        description=(
            "Must be True. Set only after you have shown the person the loop's name, "
            "the schedule and the cost per run that a call without it returns, and "
            "they have said yes."
        ),
    )

    @model_validator(mode="after")
    def _check_when(self):
        return _check_when(self)


class ScheduleConsolidationInput(BaseModel):
    title: str = Field(..., min_length=1, description="Name of the job")
    pool_id: str = Field(..., description="Id or name of the memory pool to consolidate (a name is looked up in the active workspace)")
    session_limit: int = Field(10, ge=1, le=100, description="How many recent sessions of the pool to fold in")
    run_at: Optional[str] = Field(None, description="ISO 8601 datetime of the first run (UTC if no offset)")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Recurrence = Field(Recurrence.none, description="none, hourly, daily, weekly, or cron")
    cron: Optional[str] = Field(None, description="Cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="IANA timezone name, e.g. 'Europe/Berlin'. Defaults to UTC.")

    @model_validator(mode="after")
    def _check_when(self):
        return _check_when(self)


class GetScheduledInput(BaseModel):
    id: str = Field(..., description="UUID of the scheduled job")
    fires: int = Field(10, ge=0, le=50, description="How many of its latest firings to include")


class NotifyUserInput(BaseModel):
    title: str = Field(..., min_length=1, description="Short notification title shown to the user")
    message: str = Field("", description="Notification body text")
    severity: str = Field("info", description="One of: info, success, warning, error")
    channels: Optional[List[str]] = Field(
        None,
        description="Extra delivery channels besides the inbox: any of telegram, slack, discord, teams, mail, webhook. "
                    "A chat channel reaches the chats bound in this workspace on the Connectors page.",
    )
    telegram: bool = Field(
        False,
        description="Also deliver to the user's bound Telegram chat(s). Default false = inbox/bell only.",
    )

    @model_validator(mode="after")
    def _check_severity(self):
        if self.severity not in ("info", "success", "warning", "error"):
            raise ValueError("severity must be one of: info, success, warning, error")
        return self


class ListScheduledInput(BaseModel):
    include_finished: bool = Field(
        False, description="Also include fired/cancelled/failed jobs (default: only scheduled and paused)"
    )


class JobIdInput(BaseModel):
    id: str = Field(..., description="UUID of the scheduled job")


class UpdateScheduledInput(BaseModel):
    id: str = Field(..., description="UUID of the scheduled job to update")
    title: Optional[str] = None
    message: Optional[str] = Field(None, description="New notification body / task description")
    run_at: Optional[str] = Field(None, description="New ISO 8601 firing time")
    delay_minutes: Optional[int] = Field(None, ge=1, description="Alternative to run_at: minutes from now")
    recurrence: Optional[Recurrence] = None
    cron: Optional[str] = Field(None, description="New cron expression, required when recurrence='cron'")
    timezone: Optional[str] = Field(None, description="New IANA timezone name, e.g. 'Europe/Berlin'")
    agent_id: Optional[str] = Field(None, description="New agent for agent_task and agent_run jobs; empty string clears it (agent_task only)")
    agent_version: Optional[int] = Field(None, ge=0, description="agent_task, agent_run: pin to this version; 0 clears the pin")
    project_id: Optional[str] = Field(None, description="agent_task, agent_run: new project; empty string clears it")
    file_ids: Optional[List[str]] = Field(None, description="agent_task, agent_run: replaces the file list")
    memory_pool_ids: Optional[List[str]] = Field(None, description="agent_task, agent_run: replaces the bound pools")
    memory_access: Optional[str] = Field(None, description="agent_task, agent_run: 'read' or 'write'")
    use_agent_outcome: Optional[bool] = Field(
        None, description="agent_task: grade created tasks against the agent's default criterion (true/false)",
    )
    auto_pause_after: Optional[int] = Field(None, ge=0, description="Failed firings before auto pause; 0 = off")
    seed: Optional[Dict[str, Any]] = Field(None, description="flow: replaces the JSON seed")
    max_concurrent: Optional[int] = Field(None, ge=0, description="flow: runs allowed at once; 0 = no limit")


# -------------------- Tools --------------------

@tool("schedule_notification", args_schema=ScheduleNotificationInput)
def schedule_notification(
    title: str,
    message: str = "",
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    telegram: bool = False,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
) -> str:
    """Schedule a future notification (reminder) for the user.

    At the scheduled time the user gets an entry in their notification inbox
    (and the dashboard bell). Use for reminders like "remind me tomorrow to
    review the report". Set telegram=true to also deliver to the user's bound
    Telegram chat(s) when it fires. Returns JSON with the created job.
    """
    try:
        when, recurrence = _resolve_first_run(run_at, delay_minutes, recurrence, cron, timezone)
        job = plan_service.create_job(
            kind=JobKind.notification,
            title=title,
            message=message,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            channels=["dashboard", "telegram"] if telegram else ["dashboard"],
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Notification scheduled. Confirm the time to the user.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to schedule notification: {e}")


schedule_notification.description += "\n\n" + _TIME_HELP


@tool("schedule_task", args_schema=ScheduleTaskInput)
def schedule_task(
    title: str,
    description: str = "",
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    agent_id: Optional[str] = None,
    telegram: bool = False,
    agent_version: Optional[int] = None,
    project_id: Optional[str] = None,
    file_ids: Optional[List[str]] = None,
    memory_pool_ids: Optional[List[str]] = None,
    memory_access: Optional[str] = None,
    use_agent_outcome: bool = False,
    auto_pause_after: int = 3,
) -> str:
    """Schedule agent work for the future as a task on the tracker.

    At the scheduled time a regular task is created and started: if agent_id
    is set, that agent runs it; otherwise the orchestrator routes it. The user
    is notified when the work starts. Use it when the person wants a task
    ("поставь задачу", "a task every Monday"), or when no agent was named.
    To just run an agent later, with no task, use schedule_agent_run. A task
    gets no criterion unless use_agent_outcome is set. For recurrence
    'cron', pass a cron expression; timezone (IANA name, default UTC) applies
    to cron and keeps hourly/daily/weekly at the same local time across a DST
    change. Set telegram=true to also send that start notification to the
    user's bound Telegram chat(s). A recurring job is a deployment: it may carry a project, files and
    memory pools copied onto every task it creates, an agent version pin, and
    how many failed firings pause it. Returns JSON with the created job.
    """
    try:
        if agent_id and _agent_unavailable(agent_id):
            return _json_err(f"Agent '{agent_id}' not found", code="not_found")
        when, recurrence = _resolve_first_run(run_at, delay_minutes, recurrence, cron, timezone)
        job = plan_service.create_job(
            kind=JobKind.agent_task,
            title=title,
            message=description,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            agent_id=agent_id or None,
            channels=["dashboard", "telegram"] if telegram else ["dashboard"],
            agent_version=agent_version,
            auto_pause_after=auto_pause_after,
            project_id=project_id or None,
            file_ids=file_ids,
            memory_pool_ids=memory_pool_ids,
            memory_access=memory_access,
            use_agent_outcome=use_agent_outcome,
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Task scheduled. Confirm the time and agent to the user."
            + _criterion_note(agent_id, use_agent_outcome) + (
                "" if agent_id else
                " No agent_id was set: the orchestrator picks an agent when it fires. If the person"
                " named an agent, set it now with update_scheduled (agent_id) and do not ask them again."
            ),
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to schedule task: {e}")


schedule_task.description += "\n\n" + _TIME_HELP


@tool("schedule_agent_run", args_schema=ScheduleAgentRunInput)
def schedule_agent_run(
    title: str,
    agent_id: str,
    message: str,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    agent_version: Optional[int] = None,
    project_id: Optional[str] = None,
    file_ids: Optional[List[str]] = None,
    memory_pool_ids: Optional[List[str]] = None,
    memory_access: Optional[str] = None,
    auto_pause_after: int = 3,
) -> str:
    """Run an agent at a set time, once or on a schedule, with no task.

    Each firing opens a new conversation with the agent in the Chat list and
    sends it `message`, as if the person had written it then: the answer
    stays in that conversation, nothing lands on the task tracker. Use it for
    "every morning ask the researcher for the news" or "in an hour have the
    agent check the build". Use schedule_task only when the person wants a
    task. A recurring run is a deployment like a scheduled task: it may carry
    a project, files attached to every run's message and memory pools bound
    for those runs. Returns JSON with the created job.
    """
    try:
        if _agent_unavailable(agent_id):
            return _json_err(f"Agent '{agent_id}' not found", code="not_found")
        when, recurrence = _resolve_first_run(run_at, delay_minutes, recurrence, cron, timezone)
        job = plan_service.create_job(
            kind=JobKind.agent_run,
            title=title,
            message=message,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            agent_id=agent_id,
            channels=["dashboard"],
            agent_version=agent_version,
            auto_pause_after=auto_pause_after,
            project_id=project_id or None,
            file_ids=file_ids,
            memory_pool_ids=memory_pool_ids,
            memory_access=memory_access,
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Agent run scheduled. Confirm the time and the agent to the user; "
                       "each run's answer will be in a new conversation in the Chat list.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to schedule agent run: {e}")


schedule_agent_run.description += "\n\n" + _TIME_HELP


@tool("notify_user", args_schema=NotifyUserInput)
def notify_user(title: str, message: str = "", severity: str = "info", telegram: bool = False,
                channels: Optional[List[str]] = None) -> str:
    """Send the user an immediate notification (inbox + dashboard bell), right now.

    Use this when something happens that the user should see even when they are
    not watching this conversation — e.g. you discovered a critical problem,
    finished a long piece of work with a noteworthy outcome, or are blocked and
    need their input. Do NOT use it for routine progress updates: run results
    are already reported to the user automatically.

    Set telegram=true to also push the message to the user's bound Telegram
    chat(s) for this workspace, or name other channels (slack, discord, teams,
    mail, webhook) in ``channels``; otherwise it is inbox/bell only.
    """
    try:
        extra = [str(c).strip().lower() for c in (channels or []) if str(c).strip()]
        if telegram:
            extra.append("telegram")
        channels = ["dashboard", *dict.fromkeys(extra)]
        n = plan_service.create_notification(
            title=title,
            body=message,
            severity=severity,
            source={"origin": "agent"},
            workspace=resolve_active_workspace(),
            channels=channels,
        )
        return _json_ok({
            "message": "Notification delivered to the user.",
            "notification_id": str(n.id),
            "channels": channels,
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to send notification: {e}")


class WakeAgentInput(BaseModel):
    agent_id: str = Field(..., min_length=1, description="The proactive agent to wake")
    message: str = Field(..., min_length=1,
                         description="What happened and why it should look now, one or two sentences")


def _pulse_elsewhere(agent_id: str) -> bool:
    """Whether ``agent_id``'s pulse ticks in another workspace than this
    run's (proactive/service.py ``_job_workspace``): waking it would be a
    side effect there, so it is answered like an unknown agent."""
    from agents.registry import get_agent as reg_get_agent
    spec = reg_get_agent(agent_id)
    if spec is None:
        return False
    try:
        from proactive.service import _job_workspace, profile_of
        pulse_ws = _job_workspace(spec, profile_of(spec))
    except Exception:  # noqa: BLE001 - no profile: agent_wake reports it
        return False
    return check_record(pulse_ws, what="agent", workspace=resolve_active_workspace()) is not None


@tool("wake_agent", args_schema=WakeAgentInput)
def wake_agent(agent_id: str, message: str) -> str:
    """Wake another agent's pulse now, with a message saying why.

    Only for an agent whose proactive profile is on and lists an ``agent``
    trigger that accepts you (docs/proactive.md, "Triggers"). The message
    joins the events of its next tick; nothing comes back to you. Use it when
    you noticed something another agent watches for, not to delegate work:
    for that, use the delegation tools.
    """
    try:
        from common.agent_context import current_agent_id
        from proactive.events import agent_wake
        if _pulse_elsewhere(agent_id):
            raise LookupError(f"Agent '{agent_id}' not found")
        caller = current_agent_id.get()
        result = agent_wake(caller, agent_id, message)
    except LookupError as e:
        return _json_err(str(e), code="not_found")
    except PermissionError as e:
        return _json_err(str(e), code="forbidden")
    except Exception as e:  # noqa: BLE001 - the tool reports, never raises into the loop
        return _json_err(f"Failed to wake agent: {e}")
    if not result.get("ok"):
        return _json_err(f"The pulse of '{agent_id}' is not accepting wakes ({result.get('reason')}).",
                         code="unavailable")
    return _json_ok({
        "message": f"Agent '{agent_id}' will tick within its batching window.",
        "pending_events": result.get("pending"), "run_at": result.get("run_at"),
        "paused": result.get("paused", False),
    })


@tool("list_scheduled", args_schema=ListScheduledInput)
def list_scheduled(include_finished: bool = False) -> str:
    """List scheduled jobs (notifications, agent tasks, flows, loops, memory consolidations) in the active workspace.

    By default returns only pending jobs (scheduled/paused). The response
    includes `current_time` (UTC) — also call this when you need the current
    date/time to compute a run_at for scheduling.
    """
    try:
        ws = resolve_active_workspace()
        jobs = plan_service.list_jobs(workspace=ws)
        if not include_finished:
            jobs = [j for j in jobs if j.status in (JobStatus.scheduled, JobStatus.paused)]
        return _json_ok({
            "current_time": _now().isoformat(),
            "workspace": ws or "default",
            "count": len(jobs),
            "jobs": [job_to_dict(j) for j in jobs],
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to list scheduled jobs: {e}")


@tool("cancel_scheduled", args_schema=JobIdInput)
def cancel_scheduled(id: str) -> str:
    """Cancel a scheduled job so it never fires. Returns JSON with the cancelled job."""
    try:
        jid = _uuid_from_str(id)
        job = plan_service.get_job(jid)
        if not job:
            return _json_err("Job not found", code="not_found", extra={"id": id})
        if _job_hidden(job):
            return _json_err("Job not found", code="not_found", extra={"id": id})
        if job.status not in (JobStatus.scheduled, JobStatus.paused):
            return _json_err(f"Cannot cancel a job in status '{job.status.value}'", code="invalid_state")
        updated = plan_service.cancel_job(jid)
        _record_job(updated, "cancelled")
        return _json_ok({"job": job_to_dict(updated)})
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to cancel job: {e}")


@tool("update_scheduled", args_schema=UpdateScheduledInput)
def update_scheduled(
    id: str,
    title: Optional[str] = None,
    message: Optional[str] = None,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Optional[Recurrence] = None,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_version: Optional[int] = None,
    project_id: Optional[str] = None,
    file_ids: Optional[List[str]] = None,
    memory_pool_ids: Optional[List[str]] = None,
    memory_access: Optional[str] = None,
    use_agent_outcome: Optional[bool] = None,
    auto_pause_after: Optional[int] = None,
    seed: Optional[Dict[str, Any]] = None,
    max_concurrent: Optional[int] = None,
) -> str:
    """Update a pending scheduled job: time, title, message, recurrence, cron,
    timezone, agent, and a deployment's resources, version pin, auto pause,
    flow seed and concurrency.

    Only scheduled/paused jobs can be edited. Returns JSON with the updated job.
    """
    try:
        jid = _uuid_from_str(id)
        job = plan_service.get_job(jid)
        if not job:
            return _json_err("Job not found", code="not_found", extra={"id": id})
        if _job_hidden(job):
            return _json_err("Job not found", code="not_found", extra={"id": id})
        if job.status not in (JobStatus.scheduled, JobStatus.paused):
            return _json_err(f"Cannot edit a job in status '{job.status.value}'", code="invalid_state")

        fields: Dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if message is not None:
            fields["message"] = message
        recurrence = _effective_recurrence(recurrence, cron)
        time_given = bool(run_at) or delay_minutes is not None
        will_be_cron = (recurrence or job.recurrence) == Recurrence.cron
        if will_be_cron and (time_given or recurrence is not None or cron is not None or timezone is not None):
            # Keep a cron job on its grid: the next occurrence of the new
            # schedule, or the first at or after the time given.
            given = _resolve_run_at(run_at, delay_minutes) if time_given else None
            fields["run_at"] = _next_cron_time(cron or job.cron, timezone or job.timezone, given)
        elif time_given:
            fields["run_at"] = _resolve_run_at(run_at, delay_minutes)
        if recurrence is not None:
            fields["recurrence"] = recurrence
        if cron is not None:
            fields["cron"] = cron
        if timezone is not None:
            fields["timezone"] = timezone
        if agent_id is not None:
            if agent_id.strip():
                if _agent_unavailable(agent_id.strip()):
                    return _json_err(f"Agent '{agent_id}' not found", code="not_found")
                fields["agent_id"] = agent_id.strip()
            else:
                fields["agent_id"] = None
        if agent_version is not None:
            fields["agent_version"] = agent_version or None
        if project_id is not None:
            fields["project_id"] = project_id.strip() or None
        for key, value in (("file_ids", file_ids), ("memory_pool_ids", memory_pool_ids),
                           ("memory_access", memory_access), ("auto_pause_after", auto_pause_after),
                           ("use_agent_outcome", use_agent_outcome),
                           ("seed", seed), ("max_concurrent", max_concurrent)):
            if value is not None:
                fields[key] = value
        if not fields:
            return _json_err("No fields to update provided")

        updated = plan_service.update_job(jid, **fields)
        _record_job(updated, "updated")
        return _json_ok({
            "current_time": _now().isoformat(),
            "job": job_to_dict(updated),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to update job: {e}")


update_scheduled.description += "\n\n" + _TIME_HELP


def _flow_unavailable(flow_id: str) -> bool:
    """Whether ``flow_id`` is unknown or another workspace's flow."""
    from flow import store as flow_store
    try:
        flow = flow_store.get_flow(flow_id)
    except Exception:  # noqa: BLE001 - a flow that does not parse cannot be scheduled either
        return True
    if not flow:
        return True
    return check_record((flow.get("workspace") or "").strip() or None,
                        what="flow", workspace=resolve_active_workspace()) is not None


@tool("schedule_flow", args_schema=ScheduleFlowInput)
def schedule_flow(
    title: str,
    flow_id: str,
    message: str = "",
    seed: Optional[Dict[str, Any]] = None,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    max_concurrent: int = 1,
    auto_pause_after: int = 3,
) -> str:
    """Run a flow at a set time, once or on a schedule (a flow deployment).

    Each firing starts one run of the flow with `message` as its request and
    `seed` merged into its initial state; `max_concurrent` caps how many runs
    of this job may be going at once, so a slow flow does not pile up. Returns
    JSON with the created job.
    """
    try:
        if _flow_unavailable(flow_id):
            return _json_err(f"Flow '{flow_id}' not found", code="not_found")
        when, recurrence = _resolve_first_run(run_at, delay_minutes, recurrence, cron, timezone)
        job = plan_service.create_job(
            kind=JobKind.flow,
            title=title,
            message=message,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            flow_id=flow_id,
            seed=seed,
            max_concurrent=max_concurrent,
            channels=["dashboard"],
            auto_pause_after=auto_pause_after,
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Flow scheduled. Confirm the time and the flow to the user.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to schedule flow: {e}")


schedule_flow.description += "\n\n" + _TIME_HELP


@tool("schedule_loop", args_schema=ScheduleLoopInput)
def schedule_loop(
    title: str,
    loop_id: str,
    message: str = "",
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    auto_pause_after: int = 3,
    user_approved: bool = False,
) -> str:
    """Start a loop at a set time, once or on a schedule (a loop deployment),
    AFTER the person has approved it.

    Each firing starts one run of the loop with `message` as its goal. A
    firing while the previous run of the same loop is still going is skipped
    and counts as a failed firing. Every run is billed for each iteration, so
    this refuses unless `user_approved` is True, and the refusal hands you the
    loop's real name, the first run and the cost ceiling of one run to show
    the person. Returns JSON with the created job.
    """
    try:
        from loops import store as loop_store
        loop = loop_store.get_loop(loop_id)
        if loop is None or check_record((loop.workspace or "").strip() or None, what="loop",
                                        workspace=resolve_active_workspace()) is not None:
            return _json_err(f"Loop '{loop_id}' not found. Call list_loops_tool and ask the "
                             "person which loop they mean.", code="not_found")
        when, recurrence = _resolve_first_run(run_at, delay_minutes, recurrence, cron, timezone)
        if not _approved("schedule_loop", user_approved, {"loop_id": loop_id, "title": title}):
            try:
                from loops.runner import estimate_cost
                estimate = estimate_cost(loop)
            except Exception:  # noqa: BLE001 - the confirmation still goes out without a number
                estimate = None
            extra: Dict[str, Any] = {
                "loop_id": loop.loop_id, "loop_name": loop.name, "requested_title": title,
                "first_run_at": when.isoformat(), "recurrence": recurrence.value,
                "cron": cron, "timezone": timezone or "UTC",
            }
            if estimate:
                extra["estimate_per_run"] = estimate
            return _json_err(
                f"Not scheduled yet. Tell the person which loop this is ('{loop.name}'), "
                "when it first runs and how often, and what one run may cost, then ask them "
                "to confirm. If they named a loop that is not this one, say no loop has that "
                "name and list the loops instead. Call schedule_loop again with "
                "user_approved=True only after they say yes.",
                code="approval_required", extra=extra,
            )
        job = plan_service.create_job(
            kind=JobKind.loop,
            title=title,
            message=message,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            loop_id=loop_id,
            channels=["dashboard"],
            auto_pause_after=auto_pause_after,
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Loop scheduled. Confirm the time and the loop to the user.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to schedule loop: {e}")


schedule_loop.description += "\n\n" + _TIME_HELP


@tool("schedule_memory_consolidation", args_schema=ScheduleConsolidationInput)
def schedule_memory_consolidation(
    title: str,
    pool_id: str,
    session_limit: int = 10,
    run_at: Optional[str] = None,
    delay_minutes: Optional[int] = None,
    recurrence: Recurrence = Recurrence.none,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
) -> str:
    """Consolidate a memory pool at a set time, once or on a schedule.

    Each firing folds the pool and its recent sessions into a new pool:
    duplicates merged, outdated facts replaced. The source pool is never
    changed. Returns JSON with the created job.
    """
    try:
        when, recurrence = _resolve_first_run(run_at, delay_minutes, recurrence, cron, timezone)
        job = plan_service.create_job(
            kind=JobKind.memory_consolidate,
            title=title,
            run_at=when,
            recurrence=recurrence,
            cron=cron,
            timezone=timezone,
            workspace=resolve_active_workspace(),
            created_by="agent",
            channels=["dashboard"],
            consolidate_pool_id=pool_id,
            consolidate_session_limit=session_limit,
        )
        _record_job(job, "scheduled")
        return _json_ok({
            "message": "Consolidation scheduled. Confirm the time and the pool to the user.",
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to schedule memory consolidation: {e}")


schedule_memory_consolidation.description += "\n\n" + _TIME_HELP


def _visible_job(id: str):
    """The job ``id`` names in this run's workspace, or a JSON error string."""
    jid = _uuid_from_str(id)
    job = plan_service.get_job(jid)
    if not job or _job_hidden(job):
        return None, _json_err("Job not found", code="not_found", extra={"id": id})
    return job, None


@tool("get_scheduled", args_schema=GetScheduledInput)
def get_scheduled(id: str, fires: int = 10) -> str:
    """Show one scheduled job with its next runs and its firing journal.

    A firing's ``ok`` says only that the work was started (or the error when
    it could not be). Its ``result`` says what that work has come to now:
    ``state`` running, waiting (on a person), completed, failed, stopped or
    missing, the task or run status, and the start of what it produced. A job
    has "done its work" only when the firing's result is completed. Use it
    to answer "did the nightly job run, and what happened"."""
    try:
        job, err = _visible_job(id)
        if err:
            return err
        journal = plan_service.list_job_fires(job.id, limit=fires) if fires else []
        return _json_ok({
            "current_time": _now().isoformat(),
            "job": job_to_dict(job),
            "fires": [{**plan_service.fire_to_dict(f), "result": plan_service.fire_result(f)}
                      for f in journal],
        })
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to read job: {e}")


@tool("pause_scheduled", args_schema=JobIdInput)
def pause_scheduled(id: str) -> str:
    """Pause a scheduled job: it keeps its settings and does not fire until
    resumed. Returns JSON with the job."""
    try:
        job, err = _visible_job(id)
        if err:
            return err
        updated = plan_service.pause_job(job.id)
        if updated is None:
            return _json_err(f"Cannot pause a job in status '{job.status.value}'", code="invalid_state")
        _record_job(updated, "paused")
        return _json_ok({"job": job_to_dict(updated)})
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to pause job: {e}")


@tool("resume_scheduled", args_schema=JobIdInput)
def resume_scheduled(id: str) -> str:
    """Resume a paused job, including one paused after repeated failures (its
    error count starts again from zero). Returns JSON with the job."""
    try:
        job, err = _visible_job(id)
        if err:
            return err
        updated = plan_service.resume_job(job.id)
        if updated is None:
            return _json_err(f"Cannot resume a job in status '{job.status.value}'", code="invalid_state")
        _record_job(updated, "resumed")
        return _json_ok({"current_time": _now().isoformat(), "job": job_to_dict(updated)})
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to resume job: {e}")


@tool("run_scheduled_now", args_schema=JobIdInput)
def run_scheduled_now(id: str) -> str:
    """Fire a scheduled or paused job right now, without changing its schedule
    (a paused job stays paused). Use it to test a deployment. The firing is
    done by the hub, like the Run now button, and lands in the job's journal."""
    try:
        job, err = _visible_job(id)
        if err:
            return err
        if job.status not in (JobStatus.scheduled, JobStatus.paused):
            return _json_err(f"Cannot run a job in status '{job.status.value}'", code="invalid_state")
        import requests
        from common.auth import auth_headers
        from common.hostnet import hub_base_url
        resp = requests.post(f"{hub_base_url()}/api/plan/jobs/{job.id}/run-now",
                             headers=auth_headers(), timeout=(2.0, 60.0))
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not resp.ok or not isinstance(data, dict):
            detail = data.get("detail") if isinstance(data, dict) else resp.text[:300]
            return _json_err(f"The hub did not run the job: {detail}")
        if data.get("job"):
            _record_job(job, "fired")
        return _json_ok(data)
    except Exception as e:  # noqa: BLE001 - the error goes back to the caller as a message
        return _json_err(f"Failed to run job: {e}")


__all__ = [
    "schedule_notification",
    "schedule_task",
    "schedule_agent_run",
    "schedule_flow",
    "schedule_loop",
    "schedule_memory_consolidation",
    "notify_user",
    "list_scheduled",
    "get_scheduled",
    "cancel_scheduled",
    "update_scheduled",
    "pause_scheduled",
    "resume_scheduled",
    "run_scheduled_now",
]
