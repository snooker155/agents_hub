"""
Workspace roles: which agent does a kind of work in a workspace.

A system agent that needs code written does not name ``swe_agent``; it names
the role, ``@coder``. Each workspace says which of its agents holds each role
(``roles`` in the workspace metadata), and an unbound role falls back to the
product's own agent for it. Swapping the built-in coder for an imported Claude
Code, Codex or Aider agent is then one setting on the workspace, not an edit of
every agent that delegates code work.

Where a reference is resolved:

* delegation and handoff targets (``delegates`` / ``handoffs`` may hold
  ``@role``), ``run_agent_tool``, ``delegate_task_tool``, ``assign_agent_tool``
  and ``handoff_to_agent`` accept ``@role`` as the target;
* ``AgentFactory.create_agent`` builds the holder for a reference, so anything
  that launches an agent by id launches the holder for ``@role``;
* the capability guard (tools/capabilities.py) cannot know the workspace at
  save time, so it judges a reference against every agent that may hold the
  role anywhere (:func:`expand_all`), and binding a role re-checks every agent
  that reaches it (:func:`binding_violations`).

Agent ids may not start with ``@`` (agents/registry.py), so a reference never
collides with an id.
"""
from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Prefix that marks a role reference in place of an agent id.
ROLE_PREFIX = "@"

#: Workspace metadata key holding ``{role: agent_id}`` for the bound roles.
META_KEY = "roles"


@dataclass(frozen=True)
class Role:
    id: str
    default: str
    #: What the role does, as the agents that call it read it.
    summary: str


#: The roles, in the order the settings page lists them. ``default`` is the
#: product's own agent for the role, used wherever the workspace binds none.
ROLES: Tuple[Role, ...] = (
    Role("coder", "swe_agent",
         "writes and changes code and files in the workspace's projects"),
    Role("reviewer", "code_reviewer",
         "reviews code changes for correctness, style and tests"),
    Role("planner", "planner",
         "turns a project's plans and structure into a task tree"),
    Role("researcher", "researcher",
         "answers questions that need the workspace, memory and the open web together"),
    Role("web_search", "web_searcher",
         "searches the open web and reads the pages it finds"),
    Role("analyst", "analyst",
         "answers questions from the workspace's data"),
    Role("writer", "writer",
         "turns notes and data into a finished document"),
    Role("verifier", "verifier",
         "checks reports, figures and texts independently"),
    Role("visualizer", "visualizer",
         "builds views: charts, diagrams, 3D scenes, slides, web pages"),
)

_BY_ID: Dict[str, Role] = {r.id: r for r in ROLES}

# A binding being considered (role, agent_id), seen by expand_all while
# binding_violations judges it, so the guard walks the graph as it would be
# with the binding in place without writing it first.
_PENDING: ContextVar[Optional[Tuple[str, str]]] = ContextVar("pending_role_binding", default=None)


# ── references ───────────────────────────────────────────────────────────────

def is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.strip().startswith(ROLE_PREFIX)


def ref(role_id: str) -> str:
    return f"{ROLE_PREFIX}{role_id}"


def role_of_ref(value: Any) -> Optional[str]:
    """The role id a reference names, or None when it is not a reference to a
    known role."""
    if not is_ref(value):
        return None
    rid = value.strip()[len(ROLE_PREFIX):].strip()
    return rid if rid in _BY_ID else None


def get_role(role_id: str) -> Optional[Role]:
    return _BY_ID.get(str(role_id or "").strip())


def role_ids() -> List[str]:
    return [r.id for r in ROLES]


# ── bindings ─────────────────────────────────────────────────────────────────

def _ws_name(workspace: Optional[str]) -> Optional[str]:
    if not workspace:
        return None
    try:
        from common.workspace_context import workspace_name_from_path
        return workspace_name_from_path(workspace) or None
    except Exception:  # noqa: BLE001 - a bare name is still a name
        return str(workspace)


def bindings(workspace: Optional[str]) -> Dict[str, str]:
    """The roles this workspace binds explicitly, ``{role: agent_id}``."""
    ws = _ws_name(workspace)
    if not ws:
        return {}
    try:
        from workspace import get_workspace_metadata
        raw = (get_workspace_metadata(ws) or {}).get(META_KEY)
    except Exception:  # noqa: BLE001 - unreadable metadata means "nothing bound"
        log.debug("roles: could not read workspace %s", ws, exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k in _BY_ID and isinstance(v, str) and v.strip()}


def _registered(agent_id: str) -> bool:
    try:
        from agents.registry import get_agent
        return get_agent(agent_id) is not None
    except Exception:  # noqa: BLE001 - an unreadable registry knows no agent
        return False


def _available(agent_id: str, workspace: Optional[str]) -> bool:
    ws = _ws_name(workspace)
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:  # noqa: BLE001 - an unreadable registry knows no agent
        return False
    if spec is None:
        return False
    if not ws:
        return True
    from common.workspace_context import filter_agents_for_workspace
    return bool(filter_agents_for_workspace([spec], ws))


def holder(role_id: str, workspace: Optional[str]) -> Optional[str]:
    """The agent that does ``role_id``'s work in ``workspace``: the bound one
    when it still exists and is available there, else the role's default.
    None for an unknown role or when neither exists."""
    role = get_role(role_id)
    if role is None:
        return None
    bound = bindings(workspace).get(role.id)
    if bound and _available(bound, workspace):
        return bound
    return role.default if _registered(role.default) else None


def resolve(agent_id: Any, workspace: Optional[str]) -> str:
    """An agent id for ``agent_id``: the holder for a role reference, the
    value itself otherwise. An unknown role or a role without a holder comes
    back unchanged, so the caller reports it as an agent it cannot find."""
    value = str(agent_id or "").strip()
    rid = role_of_ref(value)
    if rid is None:
        return value
    return holder(rid, workspace) or value


def expand(ids: Iterable[Any], workspace: Optional[str]) -> List[str]:
    """``ids`` with each role reference replaced by its holder in
    ``workspace``, de-duplicated, order kept."""
    out: List[str] = []
    for item in ids or []:
        value = resolve(item, workspace)
        if value and value not in out:
            out.append(value)
    return out


def _holders_anywhere(role_id: str) -> List[str]:
    """The role's default and every agent any workspace binds to it."""
    role = get_role(role_id)
    if role is None:
        return []
    out = [role.default]
    try:
        from workspace.storage import _workspaces_store
        records = _workspaces_store.all()
    except Exception:  # noqa: BLE001 - unreadable store: the default alone
        log.debug("roles: could not list workspace bindings", exc_info=True)
        records = {}
    for meta in (records or {}).values():
        raw = (meta or {}).get(META_KEY) if isinstance(meta, dict) else None
        if isinstance(raw, dict):
            bound = raw.get(role_id)
            if isinstance(bound, str) and bound.strip() and bound not in out:
                out.append(bound.strip())
    pending = _PENDING.get()
    if pending and pending[0] == role_id and pending[1] not in out:
        out.append(pending[1])
    return out


def expand_all(ids: Iterable[Any]) -> List[str]:
    """``ids`` with each role reference replaced by every agent that may hold
    the role in some workspace. For the capability guard, which judges an
    agent without a workspace and must not miss a path any workspace opens."""
    out: List[str] = []
    for item in ids or []:
        value = str(item or "").strip()
        rid = role_of_ref(value)
        targets = _holders_anywhere(rid) if rid else [value]
        for target in targets:
            if target and target not in out:
                out.append(target)
    return out


def roles_held(agent_id: str, workspace: Optional[str]) -> List[str]:
    """The roles ``agent_id`` holds in ``workspace``."""
    return [r.id for r in ROLES if holder(r.id, workspace) == agent_id]


def callers_of(role_id: str, workspace: Optional[str] = None) -> List[str]:
    """Agents whose ``delegates`` or ``handoffs`` name ``@role_id``, limited
    to those available in ``workspace`` when one is given."""
    want = ref(role_id)
    try:
        from agents.registry import list_agents
        specs = list_agents()
    except Exception:  # noqa: BLE001 - an unreadable registry has no callers
        return []
    found = [s for s in specs
             if want in (getattr(s, "delegates", None) or []) or want in (getattr(s, "handoffs", None) or [])]
    ws = _ws_name(workspace)
    if ws:
        from common.workspace_context import filter_agents_for_workspace
        found = filter_agents_for_workspace(found, ws)
    return [s.id for s in found]


# ── changing a binding ───────────────────────────────────────────────────────

class RoleError(ValueError):
    """A binding that cannot be made; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@contextlib.contextmanager
def _pending(role_id: str, agent_id: str) -> Iterator[None]:
    token = _PENDING.set((role_id, agent_id))
    try:
        yield
    finally:
        _PENDING.reset(token)


def binding_violations(role_id: str, agent_id: str) -> List[str]:
    """Why binding ``agent_id`` to ``role_id`` would break the capability
    guard, one message per agent that reaches the role; empty when it would
    not. Every agent that names ``@role_id`` is judged with the new holder
    among its delegation targets, the way the guard judges it at build."""
    messages: List[str] = []
    try:
        from agents.capability_guard import _effective_violation
        from agents.registry import list_agents
    except Exception:  # noqa: BLE001 - no guard importable: nothing to judge with
        log.debug("roles: capability guard unavailable", exc_info=True)
        return messages
    want = ref(role_id)
    with _pending(role_id, agent_id):
        for spec in list_agents():
            if want not in (getattr(spec, "delegates", None) or []):
                continue
            if getattr(spec, "capability_override", False):
                continue
            violation = _effective_violation(spec.id, list(spec.tools or []),
                                             delegates=list(spec.delegates or []))
            if violation is not None and violation.blocking:
                messages.append(f"{spec.id}: {violation.title}.")
    return messages


def set_binding(workspace: str, role_id: str, agent_id: Optional[str]) -> Dict[str, str]:
    """Bind ``role_id`` to ``agent_id`` in ``workspace``, or return it to its
    default when ``agent_id`` is empty or is the default. Returns the
    workspace's bindings afterwards. Raises :class:`RoleError`."""
    role = get_role(role_id)
    if role is None:
        raise RoleError(f"Unknown role '{role_id}'. Roles: {', '.join(role_ids())}.", 404)
    ws = _ws_name(workspace)
    if not ws:
        raise RoleError("A role is bound in a workspace; none was given.")
    target = str(agent_id or "").strip()
    current = bindings(ws)
    if not target or target == role.default:
        current.pop(role.id, None)
    else:
        if is_ref(target):
            raise RoleError("A role is held by an agent, not by another role.")
        if not _registered(target):
            raise RoleError(f"Agent '{target}' does not exist.", 404)
        if not _available(target, ws):
            raise RoleError(
                f"Agent '{target}' is not available in workspace '{ws}'. Add it to the "
                "workspace first, then give it the role.")
        problems = binding_violations(role.id, target)
        if problems:
            raise RoleError(
                f"Giving '{target}' the {role.id} role would let an agent that calls "
                f"@{role.id} combine capabilities the guard refuses: " + " ".join(problems))
        current[role.id] = target
    from workspace import update_workspace_metadata
    update_workspace_metadata(ws, {META_KEY: current})
    return current


def describe(workspace: Optional[str]) -> List[Dict[str, Any]]:
    """Every role with its default, its holder in ``workspace`` and the agents
    that call it there, for the settings page and the API."""
    bound = bindings(workspace)
    try:
        from agents.registry import get_agent
    except Exception:  # noqa: BLE001 - names fall back to ids
        get_agent = None  # type: ignore[assignment]

    def _name(aid: Optional[str]) -> Optional[str]:
        if not aid or get_agent is None:
            return aid
        spec = get_agent(aid)
        return (spec.name or aid) if spec is not None else aid

    out: List[Dict[str, Any]] = []
    for role in ROLES:
        current = holder(role.id, workspace)
        configured = bound.get(role.id)
        out.append({
            "role": role.id,
            "ref": ref(role.id),
            "summary": role.summary,
            "default": role.default,
            "default_name": _name(role.default),
            "agent": current,
            "agent_name": _name(current),
            "bound": configured,
            # A binding whose agent was removed or left the workspace: the
            # default stands in, and the page says so.
            "stale": bool(configured and configured != current),
            "callers": callers_of(role.id, workspace),
        })
    return out


# ── what an agent is told ────────────────────────────────────────────────────

def prompt_section(spec: Any, workspace: Optional[str]) -> str:
    """The roles an agent reaches, for its system prompt: which agent holds
    each one here and how to call it. Empty when it reaches none."""
    if spec is None:
        return ""
    named = [*(getattr(spec, "delegates", None) or []), *(getattr(spec, "handoffs", None) or [])]
    rids = [r for r in (role_of_ref(x) for x in named) if r]
    if not rids:
        return ""
    lines: List[str] = []
    seen: set = set()
    for rid in rids:
        if rid in seen:
            continue
        seen.add(rid)
        aid = holder(rid, workspace)
        if not aid:
            continue
        role = _BY_ID[rid]
        lines.append(f"- `@{rid}` ({role.summary}): `{aid}` in this workspace")
    if not lines:
        return ""
    return (
        "## Roles in this workspace\n"
        "Some of the agents you hand work to are named by role. The workspace decides which "
        "agent holds each role, so call the role, not a particular agent: pass `@<role>` as "
        "the agent id (run_agent_tool, delegate_task_tool, assign_agent_tool, "
        "handoff_to_agent). Where your instructions name the product's own agent for a "
        "role (for example swe_agent for code), use the role instead.\n" + "\n".join(lines)
    )


__all__ = [
    "ROLES", "ROLE_PREFIX", "META_KEY", "Role", "RoleError",
    "is_ref", "ref", "role_of_ref", "get_role", "role_ids",
    "bindings", "holder", "resolve", "expand", "expand_all", "roles_held", "callers_of",
    "binding_violations", "set_binding", "describe", "prompt_section",
]
