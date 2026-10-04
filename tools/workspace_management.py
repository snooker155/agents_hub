"""
Workspace management tools: list, inspect, create, change and delete
workspaces from the chat (docs/workspaces.md "Managing workspaces from the
chat").

They belong to the main agent in a run of the ``default`` workspace
(``common.workspace_scope.WORKSPACE_ADMIN_TOOLS``): the agent factory takes
them off any other agent and any other workspace's run, and each tool checks
the same rule again when it is called. A workspace is named by ``name``, never
by ``workspace``: a ``workspace`` argument is pinned to the run's own
workspace (agents/isolation_guard.py).

Each tool applies what the matching route of
``dashboard/backend/routes/workspaces.py`` applies (a safe name, ``default``
undeletable, system agents kept, ``default_workspace_only`` agents only in
``default``, another workspace's own agents only when shared), and allows the
person the run acts for (``common.attribution.launching_user``) only what the
dashboard would allow them: in ``AUTH_MODE=multi`` creating needs a signed in
account, changing a workspace needs an editor of it and deleting it its owner, or an
administrator. With one operator (``single``, ``token``) that operator does
everything.

What a tool never touches: isolation, secrets, hooks and the tool policy,
members, environment variables, the web domain policy and connectors. Those
are fences (isolation, approval) or grants of access, and an agent able to
change them could switch off the fence it runs behind or hand itself access.
A person changes them on the workspace's settings page.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

log = logging.getLogger(__name__)

#: How much of a workspace's instructions get_workspace returns.
INSTRUCTIONS_PREVIEW_CHARS = 2000

#: Said wherever an agent might reach for a setting it cannot change.
SETTINGS_PAGE_ONLY = (
    "Isolation, secrets, hooks and the tool policy, members, environment variables, "
    "the web domain policy and connectors are changed by a person on the workspace's "
    "settings page."
)


# ── who and where ────────────────────────────────────────────────────────────

def _system_workspace() -> str:
    try:
        from common.system_workspace import WORKSPACE
        return WORKSPACE
    except Exception:  # noqa: BLE001 - the name is fixed; the module is optional
        return "system"


def _run_workspace() -> Optional[str]:
    """The workspace of the running call, from the run itself only (never the
    workspace the dashboard has selected)."""
    from common.workspace_context import _workspace_ctx, normalize_workspace_name
    try:
        from agents.agent_loop import current_state
        state = current_state()
        if state is not None and getattr(state, "workspace", None):
            return normalize_workspace_name(state.workspace)
    except Exception:  # noqa: BLE001 - outside the loop: the context variable below
        log.debug("workspace tools: no loop state, using the context variable", exc_info=True)
    return normalize_workspace_name(_workspace_ctx.get() or os.environ.get("AGENT_WORKSPACE"))


def _out_of_scope() -> Optional[str]:
    """The refusal when the calling run is not the main agent's in
    ``default``; None when it is, or when nothing says (a direct call)."""
    from common import workspace_scope as scope
    agent = scope.current_agent()
    ws = _run_workspace()
    if (agent and agent != scope.WORKSPACE_ADMIN_AGENT) or (ws and ws != scope.WORKSPACE_ADMIN_HOME):
        return json_err(
            f"workspace management belongs to {scope.WORKSPACE_ADMIN_AGENT} in the "
            f"'{scope.WORKSPACE_ADMIN_HOME}' workspace", code="out_of_scope")
    return None


def _acting_user() -> str:
    from common.attribution import launching_user
    return launching_user()


def _multi() -> bool:
    from common import identity
    from common.auth import MULTI
    return identity.current_mode() == MULTI


def _named_user(user: str) -> Optional[Dict[str, Any]]:
    from common import identity
    from common.auth import LOCAL_OPERATOR_ID
    if not user or user == LOCAL_OPERATOR_ID:
        return None
    try:
        account = identity.get_user(user)
    except Exception:  # noqa: BLE001 - an unreadable account is no account
        log.debug("workspace tools: user %s unreadable", user, exc_info=True)
        return None
    if not account or account.get("disabled"):
        return None
    return account


def _is_admin(user: str) -> bool:
    from common.auth import ROLE_ADMIN
    return (_named_user(user) or {}).get("role") == ROLE_ADMIN


def _role(name: str, user: str) -> Optional[str]:
    from common import identity
    try:
        return identity.membership_role(name, user)
    except Exception:  # noqa: BLE001 - an unreadable membership is none
        log.debug("workspace tools: membership of %s in %s unreadable", user, name, exc_info=True)
        return None


def _may_see(name: str, user: str) -> bool:
    if not _multi():
        return True
    if _named_user(user) is None:
        return False
    return _is_admin(user) or _role(name, user) is not None


def _may_manage(name: str, user: str, role: Optional[str] = None) -> bool:
    """The bar the dashboard's routes set (common/auth.py
    ``required_workspace_role``): an editor of ``name`` to change its
    description, instructions, model or agents, its owner to delete it; an
    administrator always. Everybody outside ``multi``."""
    if not _multi():
        return True
    if _named_user(user) is None:
        return False
    from common.auth import WS_OWNER, role_satisfies
    return _is_admin(user) or role_satisfies(_role(name, user), role or WS_OWNER)


# ── lookups ──────────────────────────────────────────────────────────────────

def _clean_name(name: Any) -> str:
    return str(name or "").strip()


def _existing(name: str):
    """The folder of an existing workspace, or None. A name that is not one
    ordinary path component is never a workspace (routes/workspaces.py
    ``_require_workspace_folder``)."""
    from workspace import get_workspace_folder, is_valid_workspace_name
    from workspace import storage as ws_storage
    if not is_valid_workspace_name(name):
        return None
    root = ws_storage.WORKSPACES_ROOT.resolve()
    if os.path.dirname(os.path.normpath(str(root / name))) != str(root):
        return None
    return get_workspace_folder(name)


def _not_found(name: str) -> str:
    return json_err(f"workspace '{name}' does not exist", code="not_found")


def _find(name: str, user: str) -> Optional[str]:
    """None when ``name`` exists and ``user`` may see it, else the refusal.
    A workspace the user may not see answers like a missing one."""
    if not name:
        return json_err("name is required")
    if _existing(name) is None or not _may_see(name, user):
        return _not_found(name)
    return None


def _manage_refusal(name: str, user: str, what: str, *, owner: bool = False) -> Optional[str]:
    from common.auth import WS_EDITOR, WS_OWNER
    if _may_manage(name, user, WS_OWNER if owner else WS_EDITOR):
        return None
    who = "its owner" if owner else "an editor of it"
    return json_err(f"{what} '{name}' needs {who} or an administrator", code="forbidden")


def _tasks_by_workspace() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    try:
        from tasks import service as tasks_service
        for task in tasks_service.list_tasks():
            ws = (getattr(task, "workspace", None) or "").strip()
            counts[ws] = counts.get(ws, 0) + 1
    except Exception:  # noqa: BLE001 - counts are informative only
        log.debug("workspace tools: tasks unreadable", exc_info=True)
    return counts


def _is_isolated(name: str) -> bool:
    from common import isolation
    return isolation.is_isolated(name)


def _model_id(meta: Dict[str, Any]) -> Optional[str]:
    from workspace import get_workspace_default_model_config
    chosen = get_workspace_default_model_config(meta)
    provider, model = chosen.get("provider") or "", chosen.get("model") or ""
    if not provider:
        return None
    return f"{provider}/{model}" if model else provider


def _notify() -> None:
    try:
        from common.session_broker import notify_change
        notify_change("workspaces")
    except Exception:  # noqa: BLE001 - the page refreshes on its own next time
        log.debug("workspace tools: change notification failed", exc_info=True)


def _audit(action: str, name: str, user: str, details: Optional[Dict[str, Any]] = None) -> None:
    from common import audit
    from common.workspace_scope import current_agent
    audit.record(action, actor={"actor_id": user, "actor_kind": "agent",
                                "actor_name": current_agent() or "main-agent"},
                 object_type="workspace", object_id=name, workspace=name,
                 details={"via": "chat", **(details or {})})


# ── adding an agent: the route's rules ───────────────────────────────────────

def _agent_refusal(name: str, agent_id: str) -> Optional[str]:
    """None when ``agent_id`` may be added to workspace ``name``, else why not."""
    from agents.registry import get_agent
    from workspace import is_system_agent
    spec = get_agent(agent_id)
    if spec is None:
        return f"agent '{agent_id}' does not exist"
    if name != "default" and spec.default_workspace_only:
        return (f"agent '{agent_id}' is restricted to the default workspace and cannot be "
                "added to other workspaces")
    owner = getattr(spec, "owner_workspace", None)
    if owner and not spec.shared and owner != name and not is_system_agent(agent_id):
        return (f"agent '{agent_id}' belongs to workspace '{owner}' and is not shared; a person "
                "can expose it from the agent's details page")
    # An isolated workspace refuses an agent of its own that holds tools
    # reaching outside (common/isolation.py check_agent_tools). Any other
    # agent runs there with those tools filtered off at build time.
    if owner == name and _is_isolated(name):
        from common import isolation
        bad = isolation.offenders(list(getattr(spec, "tools", None) or []))
        if bad:
            return (f"workspace '{name}' is isolated and agent '{agent_id}' holds tools that reach "
                    f"outside its containers: {', '.join(bad)}")
    return None


# ── schemas ──────────────────────────────────────────────────────────────────

class _NoArgs(BaseModel):
    pass


class _NameArgs(BaseModel):
    name: str = Field(..., description="The workspace's name")


class _CreateArgs(BaseModel):
    name: str = Field(..., description="Name of the new workspace: one word or path-free "
                                       "name, no slashes, not '.' or '..'")
    description: Optional[str] = Field(None, description="One or two sentences on what the "
                                                         "workspace is for")
    instructions: Optional[str] = Field(None, description="The workspace instructions every "
                                                          "agent there reads (markdown)")
    agents: Optional[List[str]] = Field(None, description="Ids of agents to add besides the "
                                                          "system agents, which are always there")


class _UpdateArgs(BaseModel):
    name: str = Field(..., description="The workspace's name")
    description: Optional[str] = Field(None, description="New description; empty clears it")
    instructions: Optional[str] = Field(None, description="New instructions (markdown), "
                                                          "replacing the old ones; empty clears them")
    default_model: Optional[str] = Field(None, description="Default model as provider/model "
                                                           "(e.g. anthropic/claude-sonnet-4-5); "
                                                           "empty falls back to the global default")


class _AgentArgs(BaseModel):
    name: str = Field(..., description="The workspace's name")
    agent_id: str = Field(..., description="The agent's id")


# ── the tools ────────────────────────────────────────────────────────────────

@tool("list_workspaces", args_schema=_NoArgs)
def list_workspaces() -> str:
    """List the workspaces the user can see: name, description, how many
    agents and tasks each has, and whether it is isolated."""
    refused = _out_of_scope()
    if refused:
        return refused
    from workspace import get_workspace_metadata, is_system_agent, list_workspace_folders
    user = _acting_user()
    counts = _tasks_by_workspace()
    items: List[Dict[str, Any]] = []
    for folder in sorted(list_workspace_folders(), key=lambda p: p.name):
        name = folder.name
        if name.startswith(".") or not _may_see(name, user):
            continue
        meta = get_workspace_metadata(name)
        allowed = list(meta.get("allowed_agents") or [])
        item: Dict[str, Any] = {
            "name": name,
            "agents_count": len(allowed),
            "custom_agents_count": len([a for a in allowed if not is_system_agent(a)]),
            "tasks_count": counts.get(name, 0),
            "isolated": _is_isolated(name),
        }
        if meta.get("description"):
            item["description"] = meta["description"]
        if name == "default":
            item["default"] = True
        if name == _system_workspace():
            item["system"] = True
        items.append(item)
    return json_ok({"workspaces": items, "count": len(items)})


@tool("get_workspace", args_schema=_NameArgs)
def get_workspace(name: str) -> str:
    """Describe one workspace: description, instructions (the start of them),
    default model, the agents it allows, whether it is isolated and from which
    sites it may read, and whether its folder is an attached directory."""
    refused = _out_of_scope()
    if refused:
        return refused
    name = _clean_name(name)
    user = _acting_user()
    missing = _find(name, user)
    if missing:
        return missing
    from common import isolation
    from workspace import (get_workspace_instructions, get_workspace_metadata, is_attached_workspace,
                           is_system_agent, workspace_target)
    meta = get_workspace_metadata(name)
    instructions = get_workspace_instructions(name) or ""
    allowed = list(meta.get("allowed_agents") or [])
    attached = is_attached_workspace(name)
    return json_ok({
        "name": name,
        "description": meta.get("description") or "",
        "instructions": instructions[:INSTRUCTIONS_PREVIEW_CHARS],
        "instructions_truncated": len(instructions) > INSTRUCTIONS_PREVIEW_CHARS,
        "default_model": _model_id(meta),
        "agents": [{"agent_id": a, "system": is_system_agent(a)} for a in allowed],
        "isolation": {"isolated": isolation.is_isolated(name),
                      "allow_domains": isolation.allow_domains(name)},
        "attached": attached,
        "attached_target": str(workspace_target(name)) if attached else None,
        "tasks_count": _tasks_by_workspace().get(name, 0),
        "default": name == "default",
        "system": name == _system_workspace(),
        "settings_page_only": SETTINGS_PAGE_ONLY,
    })


@tool("create_workspace", args_schema=_CreateArgs)
def create_workspace(name: str, description: Optional[str] = None,
                     instructions: Optional[str] = None,
                     agents: Optional[List[str]] = None) -> str:
    """Create a new workspace. The system agents are in it from the start;
    `agents` adds more, each under the same rules as adding one later. The
    user becomes the workspace's owner. Isolation, secrets, members and
    connectors are set by a person on the new workspace's settings page."""
    refused = _out_of_scope()
    if refused:
        return refused
    from common.auth import LOCAL_OPERATOR_ID
    from workspace import (create_workspace_folder, get_workspace_metadata, is_valid_workspace_name,
                           set_workspace_instructions, update_workspace_metadata)
    from workspace import storage as ws_storage
    name = _clean_name(name)
    user = _acting_user()
    if _multi() and _named_user(user) is None:
        return json_err("creating a workspace needs a signed in account", code="forbidden")
    if not is_valid_workspace_name(name) or name.startswith("."):
        return json_err(f"'{name}' is not a valid workspace name: use one name without slashes",
                        code="invalid_name")
    if name == _system_workspace():
        return json_err(f"'{name}' is reserved for the system workspace", code="invalid_name")
    entry = ws_storage.WORKSPACES_ROOT / name
    if entry.exists() or entry.is_symlink():
        return json_err(f"workspace '{name}' already exists", code="exists")
    wanted = [str(a).strip() for a in (agents or []) if str(a or "").strip()]
    problems = [p for p in (_agent_refusal(name, a) for a in wanted) if p]
    if problems:
        return json_err("cannot add these agents: " + "; ".join(problems), code="agent_refused")
    try:
        folder = create_workspace_folder(name)
    except ValueError as exc:
        return json_err(str(exc), code="invalid_name")
    if _multi() and user != LOCAL_OPERATOR_ID:
        from common import identity
        try:
            identity.claim_workspace(name, user)
        except Exception as exc:  # noqa: BLE001 - reported: without it the user cannot reach the workspace
            log.warning("workspace tools: %s not made owner of %s", user, name, exc_info=True)
            return json_err(f"workspace '{name}' was created but '{user}' could not be made its "
                            f"owner: {exc}", code="owner_failed")
    updates: Dict[str, Any] = {"owner": user}
    if description is not None and str(description).strip():
        updates["description"] = str(description).strip()
    allowed = list(get_workspace_metadata(name).get("allowed_agents") or [])
    for agent_id in wanted:
        if agent_id not in allowed:
            allowed.append(agent_id)
    updates["allowed_agents"] = allowed
    update_workspace_metadata(name, updates)
    if instructions is not None and str(instructions).strip():
        set_workspace_instructions(name, str(instructions))
    _notify()
    _audit("workspace.create", name, user, {"agents": wanted})
    return json_ok({
        "name": name, "path": str(folder), "owner": user,
        "description": updates.get("description", ""),
        "agents": allowed,
        "next": SETTINGS_PAGE_ONLY,
    })


@tool("update_workspace", args_schema=_UpdateArgs)
def update_workspace(name: str, description: Optional[str] = None,
                     instructions: Optional[str] = None,
                     default_model: Optional[str] = None) -> str:
    """Change a workspace's description, instructions or default model. Only
    these. Isolation, secrets, hooks and the tool policy, members, environment
    variables, the web domain policy and connectors are never changed here:
    an agent changing them could switch off a fence (isolation, approval) or
    grant itself access, so a person changes them on the workspace's settings
    page."""
    refused = _out_of_scope()
    if refused:
        return refused
    from workspace import set_workspace_instructions, update_workspace_metadata
    name = _clean_name(name)
    user = _acting_user()
    missing = _find(name, user)
    if missing:
        return missing
    if name == _system_workspace():
        return json_err("the system workspace is configured by the service", code="forbidden")
    forbidden = _manage_refusal(name, user, "changing workspace")
    if forbidden:
        return forbidden
    if description is None and instructions is None and default_model is None:
        return json_err("nothing to change: give description, instructions or default_model. "
                        + SETTINGS_PAGE_ONLY)
    updates: Dict[str, Any] = {}
    changed: List[str] = []
    if default_model is not None:
        chosen = str(default_model).strip()
        if not chosen or chosen in ("global", "workspace_default"):
            updates.update({"model_default": {}, "model_override": {}})
        else:
            from tools.permission_policy import split_model_id
            provider, model = split_model_id(chosen)
            if not provider or not model:
                return json_err("default_model must be provider/model, e.g. "
                                "anthropic/claude-sonnet-4-5", code="invalid_model")
            updates.update({"model_default": {"provider": provider, "model": model},
                            "model_override": {}})
        changed.append("default_model")
    if description is not None:
        updates["description"] = str(description).strip()
        changed.append("description")
    if updates:
        update_workspace_metadata(name, updates)
    if instructions is not None:
        set_workspace_instructions(name, str(instructions))
        changed.append("instructions")
    _notify()
    _audit("workspace.update", name, user, {"changed": changed})
    return json_ok({"name": name, "changed": changed, "not_changeable_here": SETTINGS_PAGE_ONLY})


@tool("add_workspace_agent", args_schema=_AgentArgs)
def add_workspace_agent(name: str, agent_id: str) -> str:
    """Make an agent available in a workspace. Refused for an agent kept to
    the default workspace, for another workspace's own agent that is not
    shared, and, in an isolated workspace, for its own agent holding tools
    that reach outside."""
    refused = _out_of_scope()
    if refused:
        return refused
    from workspace import get_workspace_metadata, update_workspace_metadata
    name, agent_id = _clean_name(name), _clean_name(agent_id)
    user = _acting_user()
    missing = _find(name, user)
    if missing:
        return missing
    if name == _system_workspace():
        return json_err("the system workspace is configured by the service", code="forbidden")
    forbidden = _manage_refusal(name, user, "changing workspace")
    if forbidden:
        return forbidden
    if not agent_id:
        return json_err("agent_id is required")
    problem = _agent_refusal(name, agent_id)
    if problem:
        return json_err(problem, code="agent_refused")
    allowed = list(get_workspace_metadata(name).get("allowed_agents") or [])
    already = agent_id in allowed
    if not already:
        allowed.append(agent_id)
        update_workspace_metadata(name, {"allowed_agents": allowed})
        _notify()
        _audit("workspace.agent_add", name, user, {"agent_id": agent_id})
    return json_ok({"name": name, "agent_id": agent_id, "already_present": already,
                    "agents": allowed})


@tool("remove_workspace_agent", args_schema=_AgentArgs)
def remove_workspace_agent(name: str, agent_id: str) -> str:
    """Take an agent out of a workspace. The agent itself stays; system
    agents cannot be removed."""
    refused = _out_of_scope()
    if refused:
        return refused
    from workspace import get_workspace_metadata, is_system_agent, update_workspace_metadata
    name, agent_id = _clean_name(name), _clean_name(agent_id)
    user = _acting_user()
    missing = _find(name, user)
    if missing:
        return missing
    if name == _system_workspace():
        return json_err("the system workspace is configured by the service", code="forbidden")
    forbidden = _manage_refusal(name, user, "changing workspace")
    if forbidden:
        return forbidden
    if is_system_agent(agent_id):
        return json_err(f"agent '{agent_id}' is a system agent and cannot be removed from a "
                        "workspace", code="system_agent")
    meta = get_workspace_metadata(name)
    allowed = list(meta.get("allowed_agents") or [])
    updates: Dict[str, Any] = {}
    removed = agent_id in allowed
    if removed:
        allowed.remove(agent_id)
        updates["allowed_agents"] = allowed
    # This workspace's memory assignment for the agent goes with it, as on the page.
    memory = dict(meta.get("agent_memory_overrides") or {})
    if agent_id in memory:
        memory.pop(agent_id)
        updates["agent_memory_overrides"] = memory
    if updates:
        update_workspace_metadata(name, updates)
        _notify()
        _audit("workspace.agent_remove", name, user, {"agent_id": agent_id})
    return json_ok({"name": name, "agent_id": agent_id, "removed": removed, "agents": allowed})


@tool("delete_workspace", args_schema=_NameArgs)
def delete_workspace(name: str) -> str:
    """Delete a workspace with its folder, tasks' home and settings. Always
    waits for a person's yes. The default and the system workspace cannot be
    deleted. An attached workspace is only detached: the directory it points
    at is kept."""
    refused = _out_of_scope()
    if refused:
        return refused
    from workspace import delete_workspace_folder, is_attached_workspace, workspace_target
    name = _clean_name(name)
    if name == "default":
        return json_err("the default workspace cannot be deleted", code="forbidden")
    if name == _system_workspace():
        return json_err("the system workspace cannot be deleted", code="forbidden")
    user = _acting_user()
    missing = _find(name, user)
    if missing:
        return missing
    forbidden = _manage_refusal(name, user, "deleting workspace", owner=True)
    if forbidden:
        return forbidden
    was_attached = is_attached_workspace(name)
    target = str(workspace_target(name)) if was_attached else None
    if not delete_workspace_folder(name):
        return _not_found(name)
    _notify()
    _audit("workspace.delete", name, user, {"detached": was_attached})
    return json_ok({"name": name, "deleted": True, "detached": was_attached, "target_kept": target})


WORKSPACE_MANAGEMENT_TOOLS = [
    list_workspaces,
    get_workspace,
    create_workspace,
    update_workspace,
    delete_workspace,
    add_workspace_agent,
    remove_workspace_agent,
]

# Read by tools/connector_tools.py, which gathers the tool modules the agent
# factory and the catalog pick up without naming each one.
CONNECTOR_TOOLS = WORKSPACE_MANAGEMENT_TOOLS

__all__ = [
    "WORKSPACE_MANAGEMENT_TOOLS", "list_workspaces", "get_workspace", "create_workspace",
    "update_workspace", "delete_workspace", "add_workspace_agent", "remove_workspace_agent",
]
