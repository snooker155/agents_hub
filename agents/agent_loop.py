"""
The model call inside the agent loop, with room for the loop's own policies.

LangChain's ``create_tool_calling_agent`` is one fixed chain: format the tool
trail into messages, fill the prompt, call the model with every tool bound,
parse the answer. Several things the hub needs happen exactly between those
steps and nowhere else:

* a message the user sent while the run was working has to reach the model
  before its next step (steering, ``agents/loop_ext/steering.py``);
* an old tool result the model no longer needs has to leave the context before
  the context fills up (compaction, ``agents/loop_ext/compaction.py``);
* an agent with a hundred tools should see a short list and a way to look the
  rest up (tool search, ``agents/loop_ext/tool_search.py``);
* the model call itself may need a fallback model behind it, or strict tool
  schemas (``agents/loop_ext/fallback.py``, ``agents/loop_ext/structured.py``);
* a tool that has done what the turn was for (a conversation handoff,
  tools/handoff.py) has to end the turn without another model call
  (:func:`end_turn`, and ``return_direct`` honoured through tool wrappers).

So the chain is rebuilt here with the same four stages and a hook between each.
Every hook is an optional method on a :class:`LoopExtension`; an extension
module decides for itself, from the agent it is handed, whether it applies
(``extension_for(agent)`` returns None when it does not), and with no extension
active the chain is exactly LangChain's: same messages, same bound tools, same
parser, so an agent that uses none of this behaves as it always has.

What one run learns on the way (which model answered, what was folded, which
messages were injected, which tools were loaded) lives on a :class:`LoopState`.
The state is per run, never per agent: a built agent is cached and reused
across runs (agents/agent_cache.py), so it travels in a context variable that
``StandardAgent.run`` sets for the length of one executor call. The executor
runs tools and model calls in the caller's context (``asyncio.to_thread`` and
LangChain's executor both copy it), so every hook sees the state of its own run.
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.runnables import Runnable, RunnableLambda

log = logging.getLogger(__name__)


@dataclass
class LoopState:
    """What one run of the agent loop has done so far, beyond its tool trail.

    Written by the loop extensions and read back by ``StandardAgent`` into
    ``AgentResult.loop`` when the run ends, so the run record can say which
    model answered, what was compacted and which user messages arrived while
    the run worked. Every field is plain data (JSON-serialisable) except
    ``scratch``, which extensions use for their own working objects.
    """

    run_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    workspace: str = ""
    started_at: float = field(default_factory=time.time)
    #: How many model calls this run has made through the loop.
    model_calls: int = 0
    #: Steering: ``[{"after_step": n, "text": str, "msg_id": str, "mode": str}]``.
    #: A message is placed after the tool result of step ``n`` (0 = before the
    #: first tool call) and stays there on every later model call.
    injections: List[Dict[str, Any]] = field(default_factory=list)
    #: Tool search: names of deferred tools the model has loaded so far.
    loaded_tools: List[str] = field(default_factory=list)
    #: Compaction: one entry per fold or clearing pass (what, how much, when).
    compactions: List[Dict[str, Any]] = field(default_factory=list)
    #: Fallback: the model that produced each answer, in call order, as
    #: ``{"provider", "model", "fallback": bool, "reason": str}``.
    answered_by: List[Dict[str, Any]] = field(default_factory=list)
    #: Guardrails: every check that ran on this run (``guardrails/runtime.py``).
    guardrails: List[Dict[str, Any]] = field(default_factory=list)
    #: Structured output: validation attempts and their result.
    structured: Dict[str, Any] = field(default_factory=dict)
    #: Tool permission policy: one entry per decided call
    #: (tools/permission_policy.py): ``{"tool", "mode", "decision", "reason",
    #: "by", "fingerprint"}``.
    tool_decisions: List[Dict[str, Any]] = field(default_factory=list)
    #: Model calls made on the run's behalf beside its own loop (the tool
    #: policy's classifier, a guardrail judge, a schema repair): one entry per
    #: call with its tokens, priced at its own model (common/aux_usage.py).
    aux_calls: List[Dict[str, Any]] = field(default_factory=list)
    #: A tool that finished what the turn was for ended it (:func:`end_turn`):
    #: ``{"tool", "output", "prefer_model_text"}``. The next pass of the loop
    #: answers with it instead of calling the model; None while the loop goes on.
    ended_by: Optional[Dict[str, Any]] = None
    #: Free-form per-extension working state, never serialised.
    scratch: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        """The part worth keeping on the run record (``runs.extra.loop``).

        Only non-empty sections are included, so a plain run adds nothing.
        """
        out: Dict[str, Any] = {}
        if self.injections:
            out["injections"] = [dict(i) for i in self.injections]
        if self.loaded_tools:
            out["loaded_tools"] = list(self.loaded_tools)
        if self.compactions:
            out["compactions"] = [dict(c) for c in self.compactions]
        if self.answered_by:
            out["answered_by"] = [dict(a) for a in self.answered_by]
            fell_back = [a for a in self.answered_by if a.get("fallback")]
            if fell_back:
                out["fallback_used"] = True
        if self.guardrails:
            out["guardrails"] = [dict(g) for g in self.guardrails]
        if self.structured:
            out["structured"] = dict(self.structured)
        if self.tool_decisions:
            out["tool_decisions"] = [dict(d) for d in self.tool_decisions]
        if self.aux_calls:
            out["aux_calls"] = [dict(a) for a in self.aux_calls]
        if self.ended_by:
            out["ended_by_tool"] = str(self.ended_by.get("tool") or "")
        return out


_CURRENT: contextvars.ContextVar[Optional[LoopState]] = contextvars.ContextVar(
    "agent_loop_state", default=None)


def current_state() -> Optional[LoopState]:
    """The state of the run executing in this context, or None outside a run."""
    return _CURRENT.get()


def set_state(state: Optional[LoopState]):
    """Install *state* for this context. Returns the token for :func:`reset_state`."""
    return _CURRENT.set(state)


def reset_state(token: Any) -> None:
    try:
        _CURRENT.reset(token)
    except (ValueError, RuntimeError):
        _CURRENT.set(None)


#: States of runs whose loop was cancelled mid-way (a chat turn the person
#: stopped), kept by run id so the caller that cancelled it can still store
#: what the loop did. Bounded: the oldest are dropped.
_CANCELLED: "OrderedDict[str, LoopState]" = OrderedDict()
_CANCELLED_LOCK = threading.Lock()
_CANCELLED_MAX = 64


def keep_cancelled(state: LoopState) -> None:
    """Remember a cancelled run's state for :func:`pop_cancelled_summary`."""
    if not state.run_id:
        return
    with _CANCELLED_LOCK:
        _CANCELLED[state.run_id] = state
        _CANCELLED.move_to_end(state.run_id)
        while len(_CANCELLED) > _CANCELLED_MAX:
            _CANCELLED.popitem(last=False)


def pop_cancelled_summary(run_id: str) -> Dict[str, Any]:
    """The loop summary of a run that was cancelled, once; {} when none."""
    with _CANCELLED_LOCK:
        state = _CANCELLED.pop(str(run_id or ""), None)
    return state.summary() if state is not None else {}


def end_turn(output: str, *, tool: str = "", prefer_model_text: bool = False) -> bool:
    """Called by a tool that has done what the turn was for: end the turn now.

    LangChain's ``return_direct`` is a fixed property of a tool, so a tool
    that sometimes succeeds and sometimes refuses (and wants the model to try
    again) cannot use it. This is the per-call form: the loop's next pass
    answers with *output* without calling the model again. With
    ``prefer_model_text`` the answer is what the model said to the user
    alongside the call when it said anything, and *output* only otherwise.

    Returns False when no loop is running in this context (the tool was
    called outside an agent run), True once the turn is set to end. The first
    call wins; a second tool ending the same turn changes nothing.
    """
    state = current_state()
    if state is None:
        return False
    if state.ended_by is None:
        state.ended_by = {"tool": str(tool or ""), "output": str(output or ""),
                          "prefer_model_text": bool(prefer_model_text)}
    return True


def _text_of(content: Any) -> str:
    """The plain text of a message's content (a string or a block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(b.get("text") or "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") in (None, "text", "output_text")
        )
    return ""


def _model_text_of_last_step(steps: Sequence[Any]) -> str:
    """What the model wrote to the user in the message that made the last
    tool calls, from the action's ``message_log``; empty when it wrote none."""
    if not steps:
        return ""
    action = steps[-1][0] if isinstance(steps[-1], (tuple, list)) else None
    for message in reversed(list(getattr(action, "message_log", None) or [])):
        text = _text_of(getattr(message, "content", "")).strip()
        if text:
            return text
    return ""


def _is_return_direct(tool: Any) -> bool:
    """Whether a tool, or the tool a wrapper holds (``.inner``: the approval
    guard and the think gate wrap tools without copying the flag), is
    ``return_direct``."""
    for _ in range(4):
        if tool is None:
            return False
        if getattr(tool, "return_direct", False):
            return True
        tool = getattr(tool, "inner", None)
    return False


def _finish_output(state: LoopState, steps: Sequence[Any], by_name: Dict[str, Any]) -> Optional[str]:
    """The text the turn ends with, when a tool ended it; None otherwise.

    Two ways a tool ends a turn: :func:`end_turn` from inside the call, and a
    ``return_direct`` tool. The executor honours ``return_direct`` by itself
    only when it was the single call of its step and the tool object carries
    the flag; a parallel call or a wrapped tool reaches the next pass of the
    loop instead, and ends the turn here.
    """
    ended = state.ended_by
    if ended is not None:
        output = str(ended.get("output") or "")
        if ended.get("prefer_model_text"):
            return _model_text_of_last_step(steps) or output
        return output
    for step in reversed(list(steps or [])):
        try:
            action, observation = step
        except (TypeError, ValueError):
            continue
        if _is_return_direct(by_name.get(getattr(action, "tool", ""))):
            return str(observation)
    return None


def new_state(agent: Any = None, **kwargs: Any) -> LoopState:
    """A fresh state for one run of *agent*.

    Ids come from the keyword arguments when the caller has them, else from the
    environment a launched run publishes (``AGENT_RUN_ID``, ``AGENT_TASK_ID``,
    ``AGENT_WORKSPACE``) and the task context variable.
    """
    # A run started inside another run's context (an in-process delegation)
    # shares the process environment with its parent, so the ids published
    # there name the parent. Only the outermost run may take them; a nested
    # one has its own ids only when the caller passes them.
    nested = current_state() is not None
    run_id = str(kwargs.get("run_id") or ("" if nested else os.environ.get("AGENT_RUN_ID")) or "")
    task_id = str(kwargs.get("task_id") or "")
    if not task_id:
        try:
            from common.agent_context import current_task_id
            task_id = str(current_task_id.get() or "")
        except Exception:  # noqa: BLE001 - no task context is a normal chat run
            task_id = ""
    if not nested:
        task_id = task_id or str(os.environ.get("AGENT_TASK_ID") or "")
    workspace = str(kwargs.get("workspace") or os.environ.get("AGENT_WORKSPACE") or "")
    return LoopState(
        run_id=run_id,
        task_id=task_id,
        agent_id=str(getattr(agent, "agent_id", "") or ""),
        workspace=workspace,
    )


class LoopExtension:
    """One policy applied inside the agent loop. Every hook is optional.

    Subclasses override the hooks they need; the defaults change nothing. An
    extension instance belongs to one built agent (it may hold the agent's
    configuration) and must keep per-run data on the :class:`LoopState` it is
    handed, never on itself, because a built agent serves many runs.
    """

    #: Short name, used in logs and in ``LoopState`` entries.
    name: str = "extension"

    def shape_messages(self, state: LoopState, inputs: Dict[str, Any],
                       scratchpad: List[Any]) -> List[Any]:
        """Change the tool-trail messages before the prompt is filled.

        ``scratchpad`` is what LangChain's ``format_to_tool_messages`` made of
        ``inputs["intermediate_steps"]`` (possibly already changed by an
        earlier extension). ``inputs`` also carries ``chat_history`` when the
        caller passed one; an extension that wants to shorten it returns the
        new scratchpad and sets ``inputs["chat_history"]`` itself.
        """
        return scratchpad

    def select_tools(self, state: LoopState, tools: List[Any]) -> List[Any]:
        """The tools to bind for this model call (default: all of them).

        Only what the model is *shown* changes. The executor keeps every tool,
        so a call to a tool the model learned about later still runs.
        """
        return tools

    def bind_kwargs(self, state: LoopState, llm: Any, tools: List[Any]) -> Dict[str, Any]:
        """Extra keyword arguments for ``llm.bind_tools`` (``strict`` etc.)."""
        return {}

    def model_kwargs(self, state: LoopState, llm: Any) -> Dict[str, Any]:
        """Extra request parameters bound onto the model (``llm.bind(**kw)``),
        e.g. Anthropic's ``context_management``."""
        return {}

    def wrap_model(self, state: LoopState, bound: Runnable,
                   rebind: Callable[[Any], Runnable]) -> Runnable:
        """Wrap the bound model runnable (fallbacks go here).

        ``rebind(other_llm)`` binds the same tools and parameters onto another
        chat model, so a fallback model is called exactly like the primary.
        """
        return bound


#: Extension modules, in the order their hooks run. Each exposes
#: ``extension_for(agent) -> Optional[LoopExtension]``. Steering runs before
#: compaction so a folded context still carries the user's latest words;
#: view focus resolves the active view's kind and marks its tools loaded
#: before tool search selects, and tool search selects before structured
#: decides on strict schemas; fallback wraps last, around everything the
#: others bound.
EXTENSION_MODULES: Sequence[str] = (
    "agents.loop_ext.steering",
    "agents.loop_ext.compaction",
    "agents.loop_ext.view_focus",
    "agents.loop_ext.tool_search",
    "agents.loop_ext.structured",
    "agents.loop_ext.fallback",
)


def load_extensions(agent: Any) -> List[LoopExtension]:
    """The extensions that apply to *agent*, in hook order.

    A module that is missing or raises is logged and skipped: a broken policy
    must not stop every agent from building.
    """
    import importlib

    out: List[LoopExtension] = []
    for mod_name in EXTENSION_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            factory = getattr(mod, "extension_for", None)
            ext = factory(agent) if callable(factory) else None
        except Exception:  # noqa: BLE001 - one broken extension must not break the build
            log.warning("agent_loop: extension %s failed to load for %s", mod_name,
                        getattr(agent, "agent_id", "?"), exc_info=True)
            ext = None
        if ext is not None:
            out.append(ext)
    return out


def _format_steps(steps: Any) -> List[Any]:
    from langchain.agents.format_scratchpad.tools import format_to_tool_messages
    return format_to_tool_messages(steps or [])


def build_agent_runnable(llm: Any, tools: List[Any], prompt: Any,
                         extensions: Optional[List[LoopExtension]] = None) -> Runnable:
    """The agent runnable for ``AgentExecutor``: LangChain's tool-calling chain
    with the extension hooks between its stages.

    Streaming is preserved: the per-call chain (prompt, bound model) is returned
    from a ``RunnableLambda``, which LangChain then streams with the same input
    and config, so token callbacks fire exactly as before.
    """
    from langchain.agents.output_parsers.tools import ToolsAgentOutputParser

    exts = list(extensions or [])
    all_tools = list(tools)
    by_name = {getattr(t, "name", ""): t for t in all_tools}

    def _bind(model: Any, state: LoopState, selected: List[Any]) -> Runnable:
        kwargs: Dict[str, Any] = {}
        for ext in exts:
            try:
                kwargs.update(ext.bind_kwargs(state, model, selected) or {})
            except Exception:  # noqa: BLE001 - a hook failure falls back to plain binding
                log.warning("agent_loop: %s.bind_kwargs failed", ext.name, exc_info=True)
        extra: Dict[str, Any] = {}
        for ext in exts:
            try:
                extra.update(ext.model_kwargs(state, model) or {})
            except Exception:  # noqa: BLE001
                log.warning("agent_loop: %s.model_kwargs failed", ext.name, exc_info=True)
        if selected:
            try:
                bound = model.bind_tools(selected, **kwargs)
            except TypeError:
                # A model class that does not take one of the extra arguments
                # (``strict`` on an older integration) still gets its tools.
                bound = model.bind_tools(selected)
        else:
            bound = model
        return bound.bind(**extra) if extra else bound

    def _route(inputs: Dict[str, Any]) -> Runnable:
        state = current_state() or LoopState()
        steps = inputs.get("intermediate_steps") or []
        # A tool ended the turn (end_turn, or a return_direct tool the
        # executor did not stop on by itself): answer with its text instead of
        # calling the model. The parser reads a message without tool calls as
        # the run's final answer.
        finished = _finish_output(state, steps, by_name)
        if finished is not None:
            from langchain_core.messages import AIMessage
            return RunnableLambda(lambda _x, _text=finished: AIMessage(content=_text))
        state.model_calls += 1
        scratchpad = _format_steps(steps)
        payload = dict(inputs)
        for ext in exts:
            try:
                scratchpad = ext.shape_messages(state, payload, list(scratchpad))
            except Exception:  # noqa: BLE001 - keep the unshaped trail rather than fail the step
                log.warning("agent_loop: %s.shape_messages failed", ext.name, exc_info=True)
        payload["agent_scratchpad"] = scratchpad

        selected = all_tools
        for ext in exts:
            try:
                selected = ext.select_tools(state, list(selected))
            except Exception:  # noqa: BLE001
                log.warning("agent_loop: %s.select_tools failed", ext.name, exc_info=True)
                selected = all_tools

        bound = _bind(llm, state, selected)
        for ext in exts:
            try:
                bound = ext.wrap_model(state, bound, lambda other: _bind(other, state, selected))
            except Exception:  # noqa: BLE001
                log.warning("agent_loop: %s.wrap_model failed", ext.name, exc_info=True)
        return RunnableLambda(lambda _x, _p=payload: _p) | prompt | bound

    return RunnableLambda(_route, name="agent_loop") | ToolsAgentOutputParser()


__all__ = [
    "EXTENSION_MODULES",
    "LoopExtension",
    "LoopState",
    "build_agent_runnable",
    "current_state",
    "end_turn",
    "load_extensions",
    "keep_cancelled",
    "new_state",
    "pop_cancelled_summary",
    "reset_state",
    "set_state",
]
