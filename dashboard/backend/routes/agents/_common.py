"""Shared helpers for the agents route modules."""

from typing import Any, Dict, Optional

from workspace import system_agent_ids


import sys


def package():
    """The ``routes.agents`` package module, whichever dotted path loaded it.

    ``get_factory`` is looked up on it at call time so a patched
    ``routes.agents.get_factory`` reaches every route module.
    """
    return sys.modules[__name__.rsplit(".", 1)[0]]


def get_factory():
    return package().get_factory()


def _workspace_for_chat_default(workspace: Optional[str]) -> str:
    return (workspace or "default").strip() or "default"


def _get_workspace_default_chat_agent(workspace: Optional[str]) -> Optional[str]:
    metadata = package().get_workspace_metadata(_workspace_for_chat_default(workspace))
    value = metadata.get("default_chat_agent")
    return str(value).strip() if value else None


def _set_workspace_default_chat_agent(workspace: Optional[str], agent_id: Optional[str]) -> None:
    from memory import personal
    ws_name = _workspace_for_chat_default(workspace)
    package().create_workspace_folder(ws_name)
    # A new main agent gets personal memory on; the old one keeps what it had.
    personal.main_agent_changed(ws_name, personal.main_agent(ws_name), agent_id or None)
    package().update_workspace_metadata(ws_name, {"default_chat_agent": agent_id or None})


def _agent_visible_in_workspace(agent: dict, workspace: Optional[str]) -> bool:
    """Return True if the agent should be visible in the given workspace.

    Rules:
    - System agents are visible everywhere.
    - Shared agents (exposed across workspaces) are visible everywhere.
    - An agent owned by a workspace is visible only in that workspace, unless
      it is shared.
    - Agents with no owner_workspace (legacy / globally available) are visible
      everywhere.
    """
    if agent.get("system") or agent.get("id") in system_agent_ids():
        return True
    if agent.get("shared"):
        return True
    owner = agent.get("owner_workspace")
    if not owner:
        return True
    return owner == (workspace or "default")


def _apply_workspace_memory(agent: dict, workspace: str, mem_overrides: dict) -> None:
    """Patch an agent dict's memory fields with the workspace-effective assignment.

    The record-level assignment applies only in the agent's home workspace
    (owner_workspace, or 'default'); elsewhere the workspace metadata override
    applies — absent means no shared memory there.
    """
    home = agent.get("owner_workspace") or "default"
    if workspace == home:
        return
    entry = mem_overrides.get(agent.get("id"))
    if isinstance(entry, dict):
        agent["memory_type"] = entry.get("memory_type") or "none"
        agent["memory_data"] = entry.get("memory_data")
    else:
        agent["memory_type"] = "none"
        agent["memory_data"] = None


def _workspace_memory_overrides(workspace: str) -> dict:
    return package().get_workspace_metadata(workspace).get("agent_memory_overrides") or {}


def _light_registry_dict(spec) -> Dict[str, Any]:
    """Just the fields workspace-visibility filtering reads.

    Filtering runs over every agent (visibility has to, to compute ``total``
    correctly); the full ``spec.to_dict()`` (tools, commands, remote
    descriptor, everything) does not, so it is deferred to the page slice.
    """
    return {
        "id": spec.id,
        "system": spec.system,
        "shared": spec.shared,
        "owner_workspace": spec.owner_workspace,
        "default_workspace_only": spec.default_workspace_only,
    }

