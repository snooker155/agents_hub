"""Record kinds: runs (see common/record_kinds/__init__.py).

Agent runs (routes/messages.py, the /api/runs routes of messages.py,
steering.py, replay.py and tool_approvals.py, /api/logs in stats.py), run
groups (routes/run_groups.py), sessions, chats, instances, services, tasks
(tasks.py, outcomes.py), tool approvals and connection proposals, the browser
routes and the web access log.

Each resolver reads the workspace the way the route's own check does, so the
middleware and the route agree: a record that names none is the default
workspace's. The routes keep their own checks on top (a chat's owner, a tool
call's answerer, a task's 404).
"""
from __future__ import annotations

from typing import Optional, Tuple

from common.record_access import Record
from common.record_kinds import in_workspace


def _run_group(params: dict) -> Optional[Tuple[str, str]]:
    """``/api/runs/groups/{kind}/{group_id}``: the group's kind travels in the
    id, since a resolver is handed one string."""
    kind = str(params.get("kind") or "").strip()
    group_id = str(params.get("group_id") or "").strip()
    if not kind or not group_id:
        return None
    return "run_group", f"{kind}:{group_id}"


ROUTES: dict = {
    ("/api/messages/", "run_id"): "agent_run",
    ("/api/logs/", "run_id"): "agent_run",
    # The browser session an agent run uses: the run's workspace, the one its
    # session was opened under.
    ("/api/browser/runs/", "run_id"): "agent_run",
    # Steering takes a team run's id too (routes/steering.py).
    ("/api/runs/", "run_id"): "agent_or_team_run",
    ("/api/runs/groups/", "kind"): _run_group,
    ("/api/sessions/", "session_id"): "run_session",
    ("/api/chats/", "chat_id"): "chat",
    ("/api/instances/", "instance_id"): "instance",
    ("/api/services/", "service_id"): "service",
    ("/api/tasks/", "task_id"): "task",
    ("/api/tool-approvals/", "approval_id"): "tool_approval",
    # A proposal is a waiting tool call (connectors/proposals.py).
    ("/api/connection-proposals/", "approval_id"): "tool_approval",
    ("/api/web-logs/", "entry_id"): "web_log",
}

EXEMPT: dict = {
    ("/api/ingest/runs/", "run_id"): (
        "open path (common.auth.is_open_path, self-authenticating): the connection "
        "token in the request names the reporter, no person's principal"),
    ("/api/browser/sessions/", "session_id"): (
        "the session lives in the browser service, a network call away; every route "
        "checks the session's own workspace (routes/browser.py _check: visible to "
        "read, editor to drive or close)"),
    ("/api/browser/service/jobs/", "job_id"): (
        "a hub-wide browser service job, not workspace data; the route requires an "
        "administrator (routes/browser.py _require_admin)"),
}

#: None: every call of a route here changes the record or spends on it
#: (a stop, a replay, a steer, an answer), so editor.
READ_ROUTES: set = set()


def _agent_run(run_id: str) -> Optional[Record]:
    """A row of ``runs``, by its own workspace (routes/messages.py
    ``_require_run_visible``)."""
    from managers import run_manager
    try:
        run = run_manager.get_run_by_id(run_id)
    except Exception:  # noqa: BLE001 - an unreadable run is the route's 4xx to give
        return None
    return in_workspace(run.get("workspace")) if run else None


def _agent_or_team_run(run_id: str) -> Optional[Record]:
    """An agent run, else a team run, the order steering looks them up in."""
    found = _agent_run(run_id)
    if found is not None:
        return found
    from common.record_access import _team_run
    try:
        return _team_run(run_id)
    except Exception:  # noqa: BLE001 - no teams store is no team run
        return None


def _scenario_run(sim_run_id: str) -> Optional[Record]:
    """A simulation run's own workspace, else its scenario's."""
    from playground import store
    run = store.get_sim_run(sim_run_id)
    if run is None:
        return None
    workspace = run.workspace
    if not workspace:
        scenario = store.get_scenario(run.scenario_id)
        workspace = scenario.workspace if scenario else None
    return in_workspace(workspace)


def _group(value: str) -> Optional[Record]:
    """A run group (managers/runs/groups.py KINDS) named ``kind:id``. A flow,
    loop or team run reads as its own stop route does (a run naming no
    workspace inherits its flow's, loop's or team's), as routes/run_groups.py
    ``_group_workspace`` does; a container is its parent task."""
    from common import record_access
    kind, _, group_id = value.partition(":")
    try:
        if kind == "flow":
            return record_access._flow_run(group_id)
        if kind == "loop":
            return record_access._loop_run(group_id)
        if kind == "team":
            return record_access._team_run(group_id)
        if kind == "container":
            return _task(group_id)
        if kind == "scenario":
            return _scenario_run(group_id)
    except Exception:  # noqa: BLE001 - an unreadable group is the route's 4xx to give
        return None
    return None


def _run_session(session_id: str) -> Optional[Record]:
    from common import session_service
    try:
        ctx = session_service.get_context_by_id(session_id)
    except Exception:  # noqa: BLE001 - an unreadable session is the route's 4xx to give
        return None
    return in_workspace(ctx.get("workspace")) if ctx else None


def _chat(chat_id: str) -> Optional[Record]:
    """A stored chat by its workspace; the route adds its owner rule
    (routes/chats.py ``_chat_visible``). An assistant thread is not stored
    here: None, and the route answers for it."""
    from common import chat_store
    try:
        chat = chat_store.get_chat(chat_id)
    except Exception:  # noqa: BLE001 - an unreadable chat is the route's 4xx to give
        return None
    return in_workspace(chat.get("workspace")) if chat else None


def _instance(instance_id: str) -> Optional[Record]:
    from instances import store
    try:
        instance = store.get(instance_id)
    except Exception:  # noqa: BLE001 - an unreadable instance is the route's 4xx to give
        return None
    return in_workspace(instance.get("workspace")) if instance else None


def _service(service_id: str) -> Optional[Record]:
    from services import store
    try:
        service = store.get(service_id)
    except Exception:  # noqa: BLE001 - an unreadable service is the route's 4xx to give
        return None
    return in_workspace(service.get("workspace")) if service else None


def _task(task_id: str) -> Optional[Record]:
    """A task by its id, a UUID; anything else is no task (the route's 422)."""
    from uuid import UUID

    from tasks import service as tasks_service
    try:
        task = tasks_service.get_task(UUID(str(task_id)))
    except Exception:  # noqa: BLE001 - a malformed id or an unreadable task is the route's 4xx to give
        return None
    return in_workspace(task.workspace) if task else None


def _tool_approval(approval_id: str) -> Optional[Record]:
    """The waiting call's run's workspace, else its own
    (routes/tool_approvals.py ``_workspace``)."""
    from common import tool_approvals
    from managers import run_manager
    try:
        approval = tool_approvals.get(approval_id)
        if approval is None:
            return None
        run = run_manager.get_run_by_id(str(approval.get("run_id") or "")) or {}
    except Exception:  # noqa: BLE001 - an unreadable approval is the route's 4xx to give
        return None
    return in_workspace(run.get("workspace") or approval.get("workspace"))


def _web_log(entry_id: str) -> Optional[Record]:
    from tools import web_log
    try:
        entry = web_log.get(entry_id)
    except Exception:  # noqa: BLE001 - an unreadable entry is the route's 4xx to give
        return None
    return in_workspace(entry.get("workspace")) if entry else None


RESOLVERS: dict = {
    "agent_run": _agent_run,
    "agent_or_team_run": _agent_or_team_run,
    "run_group": _group,
    "run_session": _run_session,
    "chat": _chat,
    "instance": _instance,
    "service": _service,
    "task": _task,
    "tool_approval": _tool_approval,
    "web_log": _web_log,
}
