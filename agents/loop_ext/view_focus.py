"""
Loop extension: view focus (see agents/loop_ext/__init__.py).

A view agent holds the tools of every view kind: four for graphs, twenty for
3D geometry, three for slides, and so on. On any one turn it works on one
view, of one kind, and the other kinds' tools are noise it has to read past
(and, called by mistake, answer "no active view" or act on the wrong kind).
This extension shows the model the tools of the view it is on:

- **no active view yet**: every kind-specific tool is hidden; the common
  view tools (``create_view``, ``view_get``, ``view_apply_ops``, controls,
  snapshots, assets) and the agent's other tools stay;
- **a view is active** (the Studio binding, or the view the run created or
  named first, ``views.focus.active_view_id``): the kind's own tools appear
  and the other kinds' are hidden.

The kind is read from the view store once per view per run (cached on the
run's :class:`LoopState`), in ``shape_messages`` so that it is known before
tool search (``agents.loop_ext.tool_search``) selects: the kind's tools and
the common view tools are put on ``state.loaded_tools``, which tool search
treats as loaded, so a visualizer above the tool-search threshold sees the
slides tools the moment its slides view exists instead of having to search
for them (on Anthropic's deferred-loading path they are declared undeferred
from that call on). Hiding is what the model is *shown*: the executor keeps
every tool, so a hidden tool the model calls by name still runs, and a run
that builds a simulation and a linked chart can still address both.

The written guide for a kind travels separately, as part of the tool result
that made the view the run's target (``create_view``, ``view_get``) or of the
Studio's scene note (views/studio.py), see ``views.focus.guide_on_bind``.

Off with the loop setting ``view_focus: false`` (agent, workspace or
``AGENTS_HUB_LOOP_VIEW_FOCUS``), and absent on an agent that holds no
kind-specific view tool.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.agent_loop import LoopExtension, LoopState

log = logging.getLogger(__name__)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


class ViewFocusExtension(LoopExtension):
    """The tools of the active view's kind (module docstring)."""

    name = "view_focus"

    def __init__(self, held: frozenset) -> None:
        #: The view tool names this agent holds (common and kind-specific).
        self.held = frozenset(held)

    def _state(self, state: LoopState) -> Dict[str, Any]:
        vf = state.scratch.get("view_focus")
        if not isinstance(vf, dict):
            vf = {"kinds": {}, "kind": None}
            state.scratch["view_focus"] = vf
        return vf

    def resolve(self, state: LoopState) -> Optional[str]:
        """The active view's kind for this model call, remembered on *state*."""
        from views import focus
        vf = self._state(state)
        kind = focus.active_view_kind(vf["kinds"])
        if kind != vf.get("kind"):
            log.debug("view_focus: run %s now on a %s view", state.run_id, kind or "(no)")
        vf["kind"] = kind
        vf["resolved_call"] = state.model_calls
        if kind:
            # Shown, not searched for: tool search reads loaded_tools as "the
            # model may see these now".
            for name in sorted(focus.tools_for_kind(kind) & self.held):
                if name not in state.loaded_tools:
                    state.loaded_tools.append(name)
        return kind

    def shape_messages(self, state: LoopState, inputs: Dict[str, Any],
                       scratchpad: List[Any]) -> List[Any]:
        self.resolve(state)
        return scratchpad

    def select_tools(self, state: LoopState, tools: List[Any]) -> List[Any]:
        from views import focus
        vf = self._state(state)
        # shape_messages resolved the kind for this model call already; a
        # caller using this hook on its own gets it resolved here.
        if vf.get("resolved_call") != state.model_calls:
            self.resolve(state)
        hidden = focus.hidden_for_kind(vf.get("kind"))
        selected = [t for t in tools if _tool_name(t) not in hidden]
        vf["hidden"] = len(tools) - len(selected)
        return selected


def extension_for(agent: Any) -> Optional[ViewFocusExtension]:
    """View focus for *agent*, or None when it holds no kind-specific view
    tool or switched the policy off."""
    from agents.loop_ext.settings import loop_setting
    from views import focus

    tools = list(getattr(agent, "_tools", None) or [])
    held = focus.view_tool_names(tools)
    if not (held & focus.KIND_SPECIFIC_TOOLS):
        return None
    if not loop_setting(agent, "view_focus", True):
        return None
    return ViewFocusExtension(held)


__all__ = ["ViewFocusExtension", "extension_for"]
