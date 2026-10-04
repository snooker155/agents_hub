"""
Tools that take a ``workspace`` argument (``create_task``, ``update_task``,
``run_team_tool``, ``create_flow_tool`` and the like) may name only the
workspace the run belongs to (common/workspace_scope.py).

Without this an agent could create a task in a neighbouring workspace and put
its data in the description, an agent there would act on it, and an isolated
workspace (common/isolation.py) would have a way out through the hub itself.
Naming a workspace that does not exist would even create it. The wrapper
refuses a call that names another workspace and leaves every other call
untouched. The service's own agents are not wrapped.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel


def _takes_workspace(tool: Any) -> bool:
    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", None) or {}
    return "workspace" in fields


def _same_workspace(named: Any, workspace: str) -> bool:
    if named in (None, ""):
        return True
    from common.workspace_context import normalize_workspace_name
    return (normalize_workspace_name(str(named)) or "") == (normalize_workspace_name(workspace) or "")


def _refusal(tool_name: str, named: Any, workspace: str) -> str:
    return json.dumps({
        "ok": False, "code": "other_workspace",
        "error": (f"{tool_name} may act only in this workspace ('{workspace}'); an agent works "
                  f"in its own workspace and cannot reach '{named}'."),
    }, ensure_ascii=False)


class PinnedWorkspaceTool(BaseTool):
    """A tool whose ``workspace`` argument must name the run's workspace."""

    inner: BaseTool
    pinned: str

    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    def __init__(self, inner: BaseTool, pinned: str, **kwargs: Any) -> None:
        super().__init__(inner=inner, pinned=pinned, name=inner.name, description=inner.description,
                         args_schema=inner.args_schema, **kwargs)

    def _input(self, args: Any, kwargs: dict) -> dict:
        kwargs.pop("run_manager", None)
        if args and isinstance(args[0], dict):
            return {**args[0], **kwargs}
        return dict(kwargs)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        tool_input = self._input(args, kwargs)
        if not _same_workspace(tool_input.get("workspace"), self.pinned):
            return _refusal(self.name, tool_input.get("workspace"), self.pinned)
        return self.inner.run(tool_input)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        tool_input = self._input(args, kwargs)
        if not _same_workspace(tool_input.get("workspace"), self.pinned):
            return _refusal(self.name, tool_input.get("workspace"), self.pinned)
        return await self.inner.arun(tool_input)


#: Read-only tools that scope themselves: ``hub_lookup`` (chat/lookup.py)
#: reads the workspaces the person can see for the assistant, whose reach is
#: the person's, and the turn's own workspace for any other agent.
SELF_SCOPED_TOOLS = frozenset({"hub_lookup"})


def pin_workspace(tools: List[Any], workspace: str) -> List[Any]:
    """``tools`` with every one that takes a ``workspace`` argument pinned to
    ``workspace``. Tools without that argument, and the self-scoped readers,
    are returned as they are."""
    out: List[Any] = []
    for tool in tools:
        if (isinstance(tool, BaseTool) and _takes_workspace(tool)
                and getattr(tool, "name", "") not in SELF_SCOPED_TOOLS):
            out.append(PinnedWorkspaceTool(tool, workspace))
        else:
            out.append(tool)
    return out


__all__ = ["PinnedWorkspaceTool", "SELF_SCOPED_TOOLS", "pin_workspace"]
