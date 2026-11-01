"""Record kinds: core (see common/record_kinds/__init__.py).

Routes outside the four collections common/record_access.py ``check`` reads
from the path that still name an agent, a flow, a team, a loop or one of
their runs: A2A, imported agents, consent, containers, the marketplace, the
registry's review routes, webhooks, the tool approval lists of flow and team
runs, and the flow entity catalog.

The old resolvers are registered here once, under kind names of their own:
``agent_record``, ``flow_record``, ``flow_run_record``, ``team_record``,
``team_run_record``, ``loop_record``, ``loop_run_record``, and
``skill_record`` (a skill, from the procedures store). runs.py, data.py,
evals.py and hub.py may point their routes at these kinds rather than
register a second resolver for the same record.
"""
from __future__ import annotations

from typing import Callable, Optional, Tuple

from common.record_access import Record
from common.record_kinds import in_workspace


def _old(name: str) -> Callable[[str], Optional[Record]]:
    """The resolver common/record_access.py has for this record, looked up
    when called and made to answer None instead of raising."""
    def run(record_id: str) -> Optional[Record]:
        from common import record_access
        try:
            return getattr(record_access, name)(record_id)
        except Exception:  # noqa: BLE001 - an unreadable record is the route's 4xx to give
            return None
    run.__name__ = name
    return run


def _skill(skill_id: str) -> Optional[Record]:
    """A skill (memory/procedural.py) by id: one document of the procedures
    store, its own workspace; a published (shared) one reads everywhere."""
    from common.docstore import DocStore
    try:
        doc = DocStore("procedures").get(str(skill_id))
    except Exception:  # noqa: BLE001 - an unreadable skill is the route's 4xx to give
        return None
    if not isinstance(doc, dict):
        return None
    return in_workspace(doc.get("workspace"), everywhere=bool(doc.get("shared")))


def _consent_grant(request_id: str) -> Optional[Record]:
    """A consent request or grant (connectors/consent/store.py), filed under
    the workspace it was asked for."""
    from connectors.consent import store
    try:
        row = store.get_request(request_id)
    except Exception:  # noqa: BLE001 - an unreadable grant is the route's 4xx to give
        return None
    return in_workspace(row.get("workspace")) if row else None


def _container(name: str) -> Optional[Record]:
    """A container the hub started, by its Docker name: a run's (the run's
    workspace, from the ``containers`` row the launcher registers), an
    imported agent's per-workspace one (the workspace in that row's
    ``extra``), or an instance carrier's (the instance's workspace). A
    container the hub registered nowhere (a shell, the egress gateway) is the
    hub's own: an administrator's alone to read or stop. The Docker name of
    one that does not exist at all is the route's 404 to give."""
    from common import db
    try:
        conn = db.get_conn()
        row = conn.execute("SELECT run_id, extra FROM containers WHERE name = ?", (str(name),)).fetchone()
        if row is not None:
            if row["run_id"]:
                run = conn.execute("SELECT workspace FROM runs WHERE run_id = ?",
                                   (str(row["run_id"]),)).fetchone()
                if run is not None:
                    return in_workspace(run["workspace"])
            extra = db.loads(row["extra"], {}) or {}
            if isinstance(extra, dict) and "workspace" in extra:
                return in_workspace(extra.get("workspace"))
        carrier = conn.execute(
            "SELECT i.workspace FROM instance_carriers c JOIN instances i ON i.instance_id = c.instance_id "
            "WHERE c.container_name = ? LIMIT 1", (str(name),)).fetchone()
    except Exception:  # noqa: BLE001 - an unreadable registry is the route's 4xx to give
        return None
    if carrier is not None:
        return in_workspace(carrier["workspace"])
    return Record(workspace="default", admin_only=True)


def _flow_entity(params: dict) -> Optional[Tuple[str, str]]:
    """An entity of the flow palette that is an agent is that agent; the
    others (built in, or user defined in flow_entities.json) are hub-wide
    catalog entries that name no workspace."""
    entity_id = str(params.get("entity_id") or "")
    if not entity_id:
        return None
    try:
        from agents.registry import get_agent
        if get_agent(entity_id) is not None:
            return "agent_record", entity_id
    except Exception:  # noqa: BLE001 - an unreadable registry names no record here
        return None
    return None


ROUTES: dict = {
    ("/api/a2a/agents/", "agent_id"): "agent_record",
    ("/api/agent-import/", "agent_id"): "agent_record",
    ("/api/consent/agents/", "agent_id"): "agent_record",
    ("/api/consent/grants/", "request_id"): "consent_grant",
    ("/api/containers/build/", "agent_id"): "agent_record",
    ("/api/containers/dockerfile/", "agent_id"): "agent_record",
    ("/api/containers/", "name"): "container",
    ("/api/marketplace/agents/", "agent_id"): "agent_record",
    ("/api/marketplace/flows/", "flow_id"): "flow_record",
    ("/api/marketplace/teams/", "team_id"): "team_record",
    ("/api/marketplace/loops/", "loop_id"): "loop_record",
    ("/api/registry/agents/", "agent_id"): "agent_record",
    ("/api/registry/flows/", "flow_id"): "flow_record",
    ("/api/registry/skills/", "skill_id"): "skill_record",
    ("/api/teams/manifest/", "agent_id"): "agent_record",
    ("/api/webhooks/agents/", "agent_id"): "agent_record",
    ("/api/flow-runs/", "flow_run_id"): "flow_run_record",
    ("/api/team-runs/", "team_run_id"): "team_run_record",
    ("/api/flow-entities/", "entity_id"): _flow_entity,
}

#: Routes that need the agent readable, not editable, whatever the method.
READ_ROUTES = {
    # JSON-RPC: message/send launches a task, tasks/get and tasks/cancel act
    # on a task by id. routes/a2a.py checks editor in the workspace the task
    # lands in (or lives in) itself; the agent only has to be visible, and a
    # system agent (an admin's to change) must stay callable.
    "/api/a2a/agents/{agent_id}",
    # Signed with the workspace's inbound secret; routes/notify.py checks the
    # pulse runs in that workspace. Waking an agent does not change it.
    "/api/webhooks/agents/{agent_id}/wake",
}

RESOLVERS: dict = {
    "agent_record": _old("_agent"),
    "flow_record": _old("_flow"),
    "flow_run_record": _old("_flow_run"),
    "team_record": _old("_team"),
    "team_run_record": _old("_team_run"),
    "loop_record": _old("_loop"),
    "loop_run_record": _old("_loop_run"),
    "skill_record": _skill,
    "consent_grant": _consent_grant,
    "container": _container,
}

EXEMPT: dict = {
    ("/api/agent-templates/", "template_id"):
        "a built-in template (agents/templates/), the same for everyone; create checks editor in its target workspace",
    ("/api/flow-templates/", "template_id"):
        "a built-in template (flow/templates/), the same for everyone; create checks editor in its target workspace",
    ("/api/registry/mcp/", "catalog_id"):
        "the hub-wide MCP catalog, not workspace data; approve, block and delete are admin only (_require_admin)",
}
