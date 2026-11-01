"""Record kinds: data (see common/record_kinds/__init__.py).

Views, memory pools and their consolidations, projects (and their tracker
settings), workspace files, external and database connections,
environments, guardrails, watchers, skills, scheduled jobs and notifications,
and chat widgets. Each resolver reads the workspace the way the route's own
check does, so the middleware and the route agree: a record that names none
is the default workspace's.

Some collections keep their records inside a workspace, under an id or name
that is only unique there (MCP servers, notification endpoints and rules,
skill sources, channel and connector configuration). Those routes read the
record in the workspace ``?workspace=`` names, which the middleware checks
like any other request, so they are exempt here.
"""
from __future__ import annotations

from typing import Optional

from common.record_access import Record
from common.record_kinds import in_workspace

ROUTES: dict = {
    ("/api/views/", "view_id"): "view",
    ("/api/shared-memory/", "memory_id"): "memory_pool",
    ("/api/memory/", "memory_id"): "memory_pool",
    ("/api/memory/consolidations/", "job_id"): "memory_consolidation",
    ("/api/projects/", "project_id"): "project",
    ("/api/trackers/projects/", "project_id"): "project",
    ("/api/files/", "file_id"): "workspace_file",
    ("/api/connections/", "connection_id"): "connection",
    ("/api/databases/connections/", "connection_id"): "database_connection",
    ("/api/environments/", "env_id"): "environment",
    ("/api/guardrails/", "guardrail_id"): "guardrail",
    ("/api/watchers/", "watcher_id"): "watcher",
    ("/api/skills/", "skill_id"): "skill",
    ("/api/plan/jobs/", "job_id"): "plan_job",
    ("/api/plan/notifications/", "notification_id"): "plan_notification",
    ("/api/widgets/", "widget_id"): "widget",
}

#: Calls that only read the record, though they are POSTs, or that the route
#: opens to a viewer by design.
READ_ROUTES = {
    # Marking one's own notification read changes nothing anyone else sees.
    "/api/plan/notifications/{notification_id}/read",
    # A search of the pool's indexed passages (the agents' recall).
    "/api/shared-memory/{memory_id}/rag/search",
    # Lays out the nodes it is sent and returns them; nothing is saved.
    "/api/projects/{project_id}/graph/relayout",
    # Opens the connection and runs SELECT 1, or one statement the read-only
    # guard lets through (connectors/databases), the same as the db_query tool.
    "/api/databases/connections/{connection_id}/test",
    "/api/databases/connections/{connection_id}/query",
    # A dry run of the check: no event and no audit row (visibility only, as
    # routes/guardrails.py says).
    "/api/guardrails/{guardrail_id}/test",
    # Copies the skill into the workspace the body names: the skill is only
    # read (a published one from anywhere), the copy lands in the target.
    "/api/skills/{skill_id}/install",
}

SCOPED: dict = {
    # Ids that mean something only inside a workspace: the route reads them in
    # the workspace ?workspace= names, so the caller's role is checked there.
    # MCP and notify fall back to the hub's active workspace when it is left
    # out (routes/mcp.py, routes/notify.py ``_workspace``), so a member names it.
    ("/api/skills/sources/", "source_id"): (
        "required", "a skill source id is per workspace (memory/skill_sources.get_source)"),
    ("/api/mcp/servers/", "server_id"): (
        "required", "an MCP server id is per workspace, kept in its metadata (mcp_client/store)"),
    ("/api/notify/endpoints/", "endpoint_id"): (
        "required", "an endpoint id is per workspace, kept in its metadata (notify/store)"),
    ("/api/notify/rules/", "rule_id"): (
        "required", "a rule id is per workspace, kept in its metadata (notify/store)"),
    # Types, not records: configured per workspace, default when it is left out.
    ("/api/channels/", "name"): ("default", "a channel type (slack, discord, ...) and its bot"),
    ("/api/connectors/", "name"): ("default", "a connector type (jira, google, ...) and its configuration"),
    ("/api/trackers/", "provider"): ("default", "a tracker provider (jira, linear) and its credentials"),
}


EXEMPT: dict = {
    ("/api/widgets/public/", "widget_id"): (
        "the widget's public embed routes: an open path (common.auth.is_open_path) a visitor "
        "reaches with the widget's publishable key and a visitor token, not an account"),
}


def _view(view_id: str) -> Optional[Record]:
    from views.store import view_index_row
    try:
        row = view_index_row(view_id)
    except Exception:  # noqa: BLE001 - an unreadable view is the route's 4xx to give
        return None
    return in_workspace(row.get("workspace")) if row else None


def _pool_record(mem) -> Optional[Record]:
    """A pool's workspace, as routes/memory.py ``_pool_visible`` reads it.

    A personal pool with a real owner (memory/personal.py) is left to the
    route: it lets that owner, and nobody else but an administrator, read
    and write it in a workspace they can see, whatever their role there, so
    the middleware's editor rule for a write would refuse the owner's own
    pool to a viewer."""
    from common.auth import LOCAL_OPERATOR_ID
    from memory import personal
    if personal.is_personal(mem) and mem.owner_user and mem.owner_user != LOCAL_OPERATOR_ID:
        return None
    return in_workspace(mem.workspace)


def _memory_pool(memory_id: str) -> Optional[Record]:
    from memory.store import MemoryStore
    try:
        mem = MemoryStore().get(memory_id)
    except Exception:  # noqa: BLE001 - an unreadable pool is the route's 4xx to give
        return None
    return _pool_record(mem) if mem is not None else None


def _memory_consolidation(job_id: str) -> Optional[Record]:
    """Through its source pool, as routes/memory_consolidation.py
    ``_require_job_access`` checks it. A job whose pool is gone is refused by
    the route itself (404) to anyone held to their memberships."""
    from memory import consolidation
    from memory.store import MemoryStore
    try:
        row = consolidation.get(job_id)
        mem = MemoryStore().get(row["memory_id"]) if row else None
    except Exception:  # noqa: BLE001 - an unreadable job is the route's 4xx to give
        return None
    return _pool_record(mem) if mem is not None else None


def _project(project_id: str) -> Optional[Record]:
    from projects.storage import ProjectStore
    try:
        project = ProjectStore().get(project_id)
    except Exception:  # noqa: BLE001 - an unreadable project is the route's 4xx to give
        return None
    return in_workspace(project.workspace) if project else None


def _workspace_file(file_id: str) -> Optional[Record]:
    """A deleted file's tombstone too: its usage route reads it."""
    from files import service
    try:
        record = service.get_file(file_id, include_deleted=True)
    except Exception:  # noqa: BLE001 - an unreadable file is the route's 4xx to give
        return None
    return in_workspace(record.get("workspace")) if record else None


def _connection(connection_id: str) -> Optional[Record]:
    from connections import store
    try:
        record = store.get_connection(connection_id)
    except Exception:  # noqa: BLE001 - an unreadable connection is the route's 4xx to give
        return None
    return in_workspace(record.get("workspace")) if record else None


def _database_connection(connection_id: str) -> Optional[Record]:
    """The default workspace's connections are usable from every workspace
    (connectors/databases/store.usable_connections), so they read
    everywhere, as routes/databases.py lets them; one that names no
    workspace is the default's only."""
    from connectors.databases import store
    try:
        record = store.get_connection(connection_id)
    except Exception:  # noqa: BLE001 - an unreadable connection is the route's 4xx to give
        return None
    if record is None:
        return None
    workspace = record.get("workspace")
    return in_workspace(workspace, everywhere=workspace == "default")


def _environment(env_id: str) -> Optional[Record]:
    """One with no workspace is global by design, usable by every workspace,
    so it reads everywhere; the route lets only an administrator change it."""
    from environments import service
    try:
        env = service.get_environment(env_id)
    except Exception:  # noqa: BLE001 - an unreadable environment is the route's 4xx to give
        return None
    if env is None:
        return None
    return in_workspace(env.workspace, everywhere=not (env.workspace or "").strip())


def _guardrail(guardrail_id: str) -> Optional[Record]:
    """A global guardrail (no workspace) is read as the default workspace's,
    as routes/guardrails.py ``_load_visible`` reads it; only an administrator
    changes it (the route's check)."""
    from guardrails import service
    try:
        g = service.get_guardrail(guardrail_id)
    except Exception:  # noqa: BLE001 - an unreadable guardrail is the route's 4xx to give
        return None
    return in_workspace(g.workspace) if g else None


def _watcher(watcher_id: str) -> Optional[Record]:
    from watchers import service
    try:
        watcher = service.get(watcher_id)
    except Exception:  # noqa: BLE001 - an unreadable watcher is the route's 4xx to give
        return None
    return in_workspace(watcher.workspace) if watcher else None


def _skill(skill_id: str) -> Optional[Record]:
    """A published skill is installable from any workspace, so it reads
    everywhere (routes/skills.py ``_require_skill_visible``). The lookup is
    the route's own: skills are kept per workspace, with no index by id."""
    from memory.procedural import find_procedure
    try:
        procedure = find_procedure(skill_id)
    except Exception:  # noqa: BLE001 - an unreadable skill is the route's 4xx to give
        return None
    if procedure is None:
        return None
    return in_workspace(procedure.workspace, everywhere=bool(procedure.shared))


def _plan_job(job_id: str) -> Optional[Record]:
    from plans import service
    try:
        job = service.get_job(job_id)
    except Exception:  # noqa: BLE001 - an unreadable job is the route's 4xx to give
        return None
    return in_workspace(job.workspace) if job else None


def _plan_notification(notification_id: str) -> Optional[Record]:
    from plans import service
    try:
        notification = service.notification_store.get(notification_id)
    except Exception:  # noqa: BLE001 - an unreadable notification is the route's 4xx to give
        return None
    return in_workspace(notification.workspace) if notification else None


def _widget(widget_id: str) -> Optional[Record]:
    from widgets import store
    try:
        widget = store.get_widget(widget_id)
    except Exception:  # noqa: BLE001 - an unreadable widget is the route's 4xx to give
        return None
    return in_workspace(widget.get("workspace")) if widget else None


RESOLVERS: dict = {
    "view": _view,
    "memory_pool": _memory_pool,
    "memory_consolidation": _memory_consolidation,
    "project": _project,
    "workspace_file": _workspace_file,
    "connection": _connection,
    "database_connection": _database_connection,
    "environment": _environment,
    "guardrail": _guardrail,
    "watcher": _watcher,
    "skill": _skill,
    "plan_job": _plan_job,
    "plan_notification": _plan_notification,
    "widget": _widget,
}
