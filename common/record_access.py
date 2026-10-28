"""
Record-level access for the routes that name a record by id.

``common.auth.authorize`` decides on the workspace a request *names*: the path
``/api/workspaces/{name}/…``, ``?workspace=`` or ``X-Workspace``. The routes
of agents, flows, teams and loops name a record instead (``/api/agents/{id}``,
``/api/flows/{id}/run``), and the workspace they pass along, when they pass
one, is the page the browser happens to be on, not the record's own. A member
of workspace A could otherwise read or change B's agent by sending
``?workspace=A``, or no workspace at all.

This module closes that: :func:`target` says which record a path names,
:func:`resolve` finds the workspace the record lives in, and :func:`decide`
checks the caller's membership there. A system agent is readable everywhere
and only an administrator changes it; the sub-paths in
:data:`AGENT_WORKSPACE_SEGMENTS` change how an agent behaves in the workspace
the request names (its memory there, its skills there, whether it is that
workspace's chat agent), not the agent, so they are checked against that
workspace instead.

Only ``AUTH_MODE=multi`` gets here, and only for a person who is not an
administrator (``common.identity.authorize_request``). A record that cannot be
found is let through: the route answers 404 itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from common.auth import SAFE_METHODS, WS_EDITOR, WS_VIEWER, role_satisfies

DEFAULT_WORKSPACE = "default"

#: Sub-paths of ``/api/agents/{id}`` that act on the agent *in the workspace the
#: request names* rather than on the agent's record: per-workspace memory,
#: personal memory, skills and the workspace's chat agent. A member may do
#: these to a system agent too.
AGENT_WORKSPACE_SEGMENTS = frozenset({
    "set-default-chat", "personal-memory", "memory", "skills",
})

#: Sub-paths whose route checks the caller itself, against a workspace of its
#: own: the proactive profile, against the pulse's workspace
#: (routes/agent_proactive.py).
AGENT_SELF_CHECKED_SEGMENTS = frozenset({"proactive"})

#: Sub-paths that only read the agent, whatever the method: a copy into a
#: workspace needs the agent readable here, and ``editor`` in the target
#: (checked by routes/agents/lifecycle.py).
AGENT_READ_SEGMENTS = frozenset({"clone-to-workspace"})

#: Words directly under a collection that are routes, not record ids.
_NOT_AN_ID = {
    "agents": frozenset({"create", "tools", "workspace-capacities", "capability-check"}),
    "flows": frozenset({"import", "n8n", "generate"}),
    "teams": frozenset({"manifest"}),
    "loops": frozenset(),
}


@dataclass(frozen=True)
class Record:
    """Where a record lives, as far as access is concerned."""
    #: The workspace it belongs to; one that names none is the default one's.
    workspace: str
    #: Readable from every workspace: a system agent, a shared one, or an
    #: agent that predates workspace ownership.
    everywhere: bool = False
    #: A system agent: only an administrator changes it.
    system: bool = False
    #: An agent that reads its prompt from a system agent's definition folder
    #: (a copy made with clone-to-workspace): editing that prompt edits the
    #: system agent's, so only an administrator does it.
    system_definition: bool = False


#: Sub-paths of ``/api/agents/{id}`` that write the agent's definition folder
#: (its instructions, capabilities and usage), which a copy shares.
AGENT_DEFINITION_SEGMENTS = frozenset({"definition"})


def target(path: str) -> Optional[Tuple[str, str, List[str]]]:
    """``(kind, id, rest)`` for a path that names a record, else None.

    ``kind`` is one of agent, flow, flow_run, team, team_run, loop, loop_run;
    ``rest`` is what follows the id (``["memory"]`` for ``/api/agents/x/memory``).
    """
    segments = [s for s in (path or "").split("/") if s]
    if len(segments) < 3 or segments[0] != "api":
        return None
    collection, first = segments[1], segments[2]
    if collection not in _NOT_AN_ID:
        return None
    if first == "runs":
        if collection == "agents" or len(segments) < 4:
            return None
        return f"{collection[:-1]}_run", segments[3], segments[4:]
    if first in _NOT_AN_ID[collection]:
        return None
    return collection[:-1], first, segments[3:]


def _workspace(value) -> str:
    text = str(value or "").strip()
    return text or DEFAULT_WORKSPACE


def _agent(agent_id: str) -> Optional[Record]:
    from agents.registry import get_agent
    from workspace import system_agent_ids
    spec = get_agent(agent_id)
    if spec is None:
        return None
    system_ids = system_agent_ids()
    system = bool(spec.system) or spec.id in system_ids
    owner = (spec.owner_workspace or "").strip()
    folder = spec.definition_id or spec.id
    return Record(workspace=_workspace(owner),
                  everywhere=system or bool(spec.shared) or not owner,
                  system=system,
                  system_definition=folder != spec.id and folder in system_ids)


def _flow(flow_id: str) -> Optional[Record]:
    from flow import store as flow_store
    try:
        flow = flow_store.get_flow(flow_id)
    except Exception:  # noqa: BLE001 - an unreadable flow is the route's 4xx to give
        return None
    if not flow:
        return None
    return Record(workspace=_workspace(flow.get("workspace")),
                  everywhere=bool(flow.get("shared")))


def _flow_run(run_id: str) -> Optional[Record]:
    from flow import run_store
    rec = run_store.get_flow_run(run_id)
    return _flow(str(rec.get("flow_id") or "")) if rec else None


def _team(team_id: str) -> Optional[Record]:
    from teams import store
    team = store.get_team(team_id)
    return Record(workspace=_workspace(team.workspace)) if team else None


def _team_run(run_id: str) -> Optional[Record]:
    from teams import store
    run = store.get_run(run_id)
    if run is None:
        return None
    if run.workspace:
        return Record(workspace=_workspace(run.workspace))
    return _team(run.team_id)


def _loop(loop_id: str) -> Optional[Record]:
    from loops import store
    loop = store.get_loop(loop_id)
    return Record(workspace=_workspace(loop.workspace)) if loop else None


def _loop_run(run_id: str) -> Optional[Record]:
    from loops import store
    run = store.get_run(run_id)
    if run is None:
        return None
    if run.workspace:
        return Record(workspace=_workspace(run.workspace))
    return _loop(run.loop_id)


_RESOLVERS: dict = {
    "agent": _agent, "flow": _flow, "flow_run": _flow_run,
    "team": _team, "team_run": _team_run, "loop": _loop, "loop_run": _loop_run,
}


def resolve(kind: str, record_id: str) -> Optional[Record]:
    """The record ``kind``/``record_id`` names, or None when there is none."""
    resolver = _RESOLVERS.get(kind)
    return resolver(record_id) if resolver else None


def decide(*, method: str, kind: str, rest: List[str], record: Optional[Record],
           named_workspace: Optional[str],
           role_in: Callable[[str], Optional[str]]) -> Optional[str]:
    """None when the request may go on, else why not.

    Pure: ``role_in(workspace)`` is the caller's membership role there (None
    for none), looked up by the caller.
    """
    write = (method or "").upper() not in SAFE_METHODS
    if kind == "agent" and rest:
        if rest[0] in AGENT_SELF_CHECKED_SEGMENTS:
            return None
        if rest[0] in AGENT_READ_SEGMENTS:
            write = False
    need = WS_EDITOR if write else WS_VIEWER
    if kind == "agent" and rest and rest[0] in AGENT_WORKSPACE_SEGMENTS:
        workspace = _workspace(named_workspace)
        if role_satisfies(role_in(workspace), need):
            return None
        return f"'{need}' access to workspace '{workspace}' is required"
    if record is None:
        return None
    if write and record.system:
        return "Only an administrator can change a system agent"
    if write and record.system_definition and rest and rest[0] in AGENT_DEFINITION_SEGMENTS:
        return "This agent shares a system agent's prompt; only an administrator can change it"
    if not write and record.everywhere:
        return None
    if role_satisfies(role_in(record.workspace), need):
        return None
    return f"'{need}' access to workspace '{record.workspace}' is required"


def check(*, method: str, path: str, named_workspace: Optional[str],
          role_in: Callable[[str], Optional[str]]) -> Optional[str]:
    """:func:`decide` for one request, with the record looked up. None when
    the path names no record or the caller may proceed."""
    found = target(path)
    if found is None:
        return None
    kind, record_id, rest = found
    needs_record = not (kind == "agent" and rest and rest[0] in (
        AGENT_WORKSPACE_SEGMENTS | AGENT_SELF_CHECKED_SEGMENTS))
    record = resolve(kind, record_id) if needs_record else None
    return decide(method=method, kind=kind, rest=rest, record=record,
                  named_workspace=named_workspace, role_in=role_in)


# ── For routes ───────────────────────────────────────────────────────────────
# What the middleware cannot see: a workspace named in a request body (a new
# flow's, a chat turn's), and a list asked for with no workspace at all.

def restricted(principal) -> bool:
    """Whether ``principal`` is held to its memberships: a person who is not
    an administrator, in ``multi`` mode. Everybody else sees everything."""
    from common import identity
    from common.auth import MULTI
    if identity.current_mode() != MULTI or principal is None:
        return False
    return principal.kind == "user" and not principal.is_admin


def require_in_workspace(request, workspace: Optional[str], role: str = WS_EDITOR) -> None:
    """403 unless the caller holds ``role`` in ``workspace`` (none = the default
    one). For a workspace named in a request body."""
    from common import identity
    identity.require_role(identity.request_principal(request),
                          workspace=_workspace(workspace), role=role)


def require_chat_target(request, *, workspace: Optional[str], agent_id: Optional[str] = None,
                        flow_id: Optional[str] = None, team_id: Optional[str] = None) -> None:
    """403 unless the caller may run a turn in ``workspace`` (editor there)
    and what the turn talks to lives there or everywhere."""
    from common import identity
    from fastapi import HTTPException
    principal = identity.request_principal(request)
    if not restricted(principal):
        return
    ws = _workspace(workspace)
    identity.require_role(principal, workspace=ws, role=WS_EDITOR)
    for kind, record_id in (("agent", agent_id), ("flow", flow_id), ("team", team_id)):
        if not record_id:
            continue
        record = resolve(kind, str(record_id))
        if record is None or record.everywhere or record.workspace == ws:
            continue
        raise HTTPException(status_code=403,
                            detail=f"this {kind} is not in workspace '{ws}'")


def visible_names(request) -> Optional[set]:
    """The workspaces the caller may list records from; None for all."""
    from common import access, identity
    principal = identity.request_principal(request)
    if not restricted(principal):
        return None
    return access.visible_workspaces(principal) or set()


def visible(names: Optional[set], workspace) -> bool:
    """Whether a record filed under ``workspace`` (none = the default one) is
    in ``names`` (None = every workspace)."""
    return names is None or _workspace(workspace) in names


__all__ = ["AGENT_READ_SEGMENTS", "AGENT_SELF_CHECKED_SEGMENTS", "AGENT_WORKSPACE_SEGMENTS", "Record", "check", "decide", "require_chat_target",
           "require_in_workspace", "resolve", "restricted", "target", "visible",
           "visible_names"]
