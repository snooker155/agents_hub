"""
Loop extension: steering (see agents/loop_ext/__init__.py, docs/steering.md).

A person who sends a message while a run works (``POST /api/runs/{id}/steer``,
mode ``inject``) should not have to wait for the run to end: the words should
reach the model before its next step. This extension does that from inside
the loop. Before every model call it claims the run's pending messages
(``common.steering.claim_pending``, one indexed SELECT when there are none),
records each on the run's :class:`~agents.agent_loop.LoopState` as an
injection placed after the tool results the run had at that moment, and puts
every injection into the messages the model is sent.

The scratchpad is rebuilt from ``intermediate_steps`` on every model call, so
an injection is placed again each time, at the same point: right after the
tool results of step ``after_step`` (0 is right after the person's own turn).
That keeps a valid sequence for every provider: the tool results still follow
the assistant message that asked for them, and a user message after tool
results is allowed by OpenAI and merged into the same user turn by Anthropic.

A message in mode ``system`` (an operator's addition to the instructions, see
common/steering.py) is claimed the same way but never enters the conversation.
It is kept on ``LoopState.system_messages`` and appended to the prompt's
leading system message on every later model call (:meth:`SteeringExtension.
shape_prompt`). The append form is the one every provider in providers/
accepts: Anthropic and Gemini take a single system instruction ahead of the
conversation and refuse (or silently merge) a system message in the middle of
it, while OpenAI and the local servers take either. A system message whose
content is a block list (Anthropic's cached prefix) gets one more text block
after the cached ones, so the cache prefix stays as it was.

A message in mode ``switch_model`` names a catalog model (``provider/model``).
It is claimed the same way and kept on ``LoopState.model_switches``; from the
next model call on, the run's model is that one (:meth:`SteeringExtension.
wrap_model` binds the same tools and parameters onto it through the loop's
``rebind``, exactly as a fallback model is bound), and the rest of the run
goes on with its whole trail. The history is LangChain messages, which every
provider integration converts to its own shape, so a run can move between
providers; what one provider adds that another refuses (Anthropic's
``cache_control`` markers on the system prompt) is taken out of the prompt
for a model that does not take it. Each call the new model answers is
recorded on ``LoopState.answered_by`` with its own tokens and catalog model,
the entry agents/loop_ext/fallback.py writes for a fallback, so the run's
cost prices those calls at the new model's rates (common/pricing.py).

Where the claim goes depends on where the run executes. A run launched as its
own process (``runtime/agent_run.py`` publishes ``AGENT_RUN_ID``) claims
through ``common.state_transport``, over HTTP when its container cannot open
the database. Anything else runs inside the backend (a chat turn passes its
``run_id`` to ``arun``) and reads the database directly.

A delegated agent that runs inside a task's process inherits that process's
``AGENT_RUN_ID``, so without care it would take its parent's messages. The
first loop state to claim for a run id owns it for as long as that state is
alive; any other state with the same id defers to it.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import weakref
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler

from agents.agent_loop import LoopExtension, LoopState

log = logging.getLogger(__name__)

#: Opening line of every injected message. The model reads it as a message
#: from the person, written while it worked; ``is_steering_text`` recognises
#: it so a stored prompt split can tell it from the turn's own message.
STEER_HEADER = "[Message from the user, sent while you were working]"

_STEER_FOOTER = (
    "The user wrote this while you were working on their request. Take it into "
    "account from this step on: change course if it changes what they want, "
    "otherwise carry on with the work."
)

#: After this many transport failures in a row a run stops asking: an
#: unreachable backend must not slow every later model call down.
MAX_TRANSPORT_FAILURES = 3

#: Heading of the section the system-mode messages are appended under.
SYSTEM_HEADER = "## Operator instructions added during this run"

_SYSTEM_INTRO = (
    "The operator running you added the following while you worked. They carry "
    "the same authority as the rest of these instructions and apply from now on; "
    "where they conflict with something above, they win."
)

#: Steering mode that moves the run to another model.
MODE_SWITCH_MODEL = "switch_model"
#: ``state.scratch`` key of the model the run was switched to (built once).
SWITCH_SCRATCH = "model_switch"
#: Reason recorded on ``answered_by`` for a call the switched model answered.
SWITCH_REASON = "switch_model"

_OWNERS: Dict[str, "weakref.ref[LoopState]"] = {}
_OWNERS_LOCK = threading.Lock()


def format_injection(text: str) -> str:
    """The content of the message the model is sent for one steering message."""
    return f"{STEER_HEADER}\n\n{str(text or '').strip()}\n\n{_STEER_FOOTER}"


def is_steering_text(text: Any) -> bool:
    """Whether ``text`` is an injected steering message (see :func:`format_injection`)."""
    return isinstance(text, str) and text.lstrip().startswith(STEER_HEADER)


def format_system_addition(items: List[Dict[str, Any]]) -> str:
    """The section appended to the system prompt for the run's system-mode
    messages, oldest first."""
    lines = [SYSTEM_HEADER, "", _SYSTEM_INTRO, ""]
    for n, item in enumerate(items, 1):
        lines.append(f"{n}. {str(item.get('text') or '').strip()}")
    return "\n".join(lines).strip()


def append_system_addition(messages: List[Any], items: List[Dict[str, Any]]) -> List[Any]:
    """``messages`` with the system-mode section appended to the leading
    system message (a new one in front when there is none). A block-list
    content keeps its blocks, cache markers included, and gets one more."""
    if not items:
        return messages
    from langchain_core.messages import SystemMessage
    addition = format_system_addition(items)
    out = list(messages)
    for idx, msg in enumerate(out):
        if not isinstance(msg, SystemMessage):
            continue
        content = msg.content
        if isinstance(content, list):
            new_content: Any = [*content, {"type": "text", "text": addition}]
        else:
            base = str(content or "")
            new_content = f"{base}\n\n---\n\n{addition}" if base.strip() else addition
        out[idx] = msg.model_copy(update={"content": new_content})
        return out
    return [SystemMessage(content=addition), *out]


def injection_message(injection: Dict[str, Any]) -> Any:
    from langchain_core.messages import HumanMessage
    msg_id = str(injection.get("msg_id") or "")
    return HumanMessage(content=format_injection(str(injection.get("text") or "")),
                        id=f"steer:{msg_id}" if msg_id else None)


def _format_steps(steps: Any) -> List[Any]:
    from agents.agent_loop import format_steps
    return format_steps(list(steps or []))


def place_injections(scratchpad: List[Any], steps: List[Any],
                     injections: List[Dict[str, Any]]) -> List[Any]:
    """``scratchpad`` with one user message per injection, each right after
    the tool results of its ``after_step``.

    ``scratchpad`` is what ``format_steps(steps)`` made; its
    messages for the first ``k`` steps are exactly
    ``format_steps(steps[:k])`` (it only appends), which is how the
    position after step ``k`` is found. A step past the end (never expected)
    lands at the end.
    """
    if not injections:
        return scratchpad
    steps = list(steps or [])
    out = list(scratchpad)
    offsets: Dict[int, int] = {}
    placed: List[tuple] = []
    for order, inj in enumerate(injections):
        after = max(0, min(int(inj.get("after_step") or 0), len(steps)))
        if after not in offsets:
            offsets[after] = len(out) if after >= len(steps) else len(_format_steps(steps[:after]))
        placed.append((offsets[after], order, inj))
    # Insert from the back so every earlier position stays valid; at one
    # position the earlier message goes first.
    for pos, _order, inj in sorted(placed, key=lambda p: (p[0], p[1]), reverse=True):
        out.insert(min(pos, len(out)), injection_message(inj))
    return out


def _owns(state: LoopState) -> bool:
    """Whether ``state`` is the loop that takes messages for its run id."""
    run_id = state.run_id
    with _OWNERS_LOCK:
        ref = _OWNERS.get(run_id)
        owner = ref() if ref is not None else None
        if owner is not None and owner is not state:
            return False
        if owner is None:
            def _drop(dead: Any, rid: str = run_id) -> None:
                with _OWNERS_LOCK:
                    if _OWNERS.get(rid) is dead:
                        _OWNERS.pop(rid, None)
            _OWNERS[run_id] = weakref.ref(state, _drop)
        return True


def _transport(state: LoopState) -> Any:
    """The steering transport for this run, resolved once per run."""
    cached = state.scratch.get("steering_transport")
    if cached is not None:
        return cached
    from common.state_transport import DirectStateTransport, get_state_transport
    if os.environ.get("AGENT_RUN_ID", "") == state.run_id:
        try:
            transport = get_state_transport()
        except Exception:  # noqa: BLE001 - an unreadable setting falls back to direct access
            transport = DirectStateTransport()
    else:
        transport = DirectStateTransport()
    state.scratch["steering_transport"] = transport
    return transport


def _emit_delivered(state: LoopState, items: List[Dict[str, Any]]) -> None:
    """Tell a streaming chat turn that its messages reached the model."""
    try:
        from common import stream_sink
        emitter = stream_sink.get_emitter()
        if emitter is None or stream_sink.current_depth() != 0:
            return
        for inj in items:
            event = {"type": "steer_delivered", "msg_id": inj.get("msg_id"),
                     "after_step": inj.get("after_step"), "run_id": state.run_id}
            mode = str(inj.get("mode") or "inject")
            if mode != "inject":
                event["mode"] = mode
            if mode == MODE_SWITCH_MODEL:
                event["model"] = inj.get("to")
            emitter(event)
    except Exception:  # noqa: BLE001 - the notice is cosmetic, the message is already in the context
        log.debug("steering: delivered notice failed for %s", state.run_id, exc_info=True)


def _as_injection(msg: Dict[str, Any], after_step: int) -> Dict[str, Any]:
    item = {
        "after_step": int(after_step),
        "text": str(msg.get("body") or ""),
        "msg_id": str(msg.get("msg_id") or ""),
        "mode": str(msg.get("mode") or "inject"),
        "at": str(msg.get("delivered_at") or "") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if item["mode"] == MODE_SWITCH_MODEL:
        item["to"] = item["text"].strip()
        item["by"] = str(msg.get("author_name") or "")
    return item


def _is_system(item: Dict[str, Any]) -> bool:
    return str(item.get("mode") or "") == "system"


def _is_switch(item: Dict[str, Any]) -> bool:
    return str(item.get("mode") or "") == MODE_SWITCH_MODEL


def _known_ids(state: LoopState) -> set:
    return {i.get("msg_id") for i in [*state.injections, *state.system_messages,
                                      *state.model_switches]}


def _record(state: LoopState, item: Dict[str, Any]) -> None:
    """Keep a claimed message where its mode puts it: the conversation for
    an inject, the system prompt for a system message, the run's model for a
    switch."""
    if _is_switch(item):
        state.model_switches.append(item)
        return
    (state.system_messages if _is_system(item) else state.injections).append(item)


def _model_name(llm: Any) -> str:
    for attr in ("model", "model_name"):
        val = getattr(llm, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def strip_cache_markers(messages: List[Any]) -> List[Any]:
    """``messages`` without Anthropic's ``cache_control`` block markers, for
    a model whose provider does not take them (OpenAI passes unknown keys of
    a content block through to its API, which refuses them)."""
    out = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, list) and any(isinstance(b, dict) and "cache_control" in b for b in content):
            blocks = [{k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
                      for b in content]
            msg = msg.model_copy(update={"content": blocks})
        out.append(msg)
    return out


class _SwitchedCallback(BaseCallbackHandler):
    """Attached to the switched model: records each call it answered on
    ``state.answered_by``, with its own tokens and catalog model, in the
    shape a fallback's answer has, which is what prices it at its own rate.

    A refusal is left to the fallback chain when the agent has one (its
    primary callback raises on it and the next model answers instead)."""

    def __init__(self, state: LoopState, ref: Dict[str, str]) -> None:
        super().__init__()
        self.state = state
        self.ref = dict(ref)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        from agents.loop_ext.fallback import _call_tokens, _response_is_refusal, _response_model_name
        if self.state.scratch.get("fallback_active") and _response_is_refusal(response):
            return
        entry = {
            "provider": self.ref.get("provider") or "",
            "model": _response_model_name(response) or self.ref.get("model") or "",
            # ``fallback`` is the flag common/pricing.py prices a call by on
            # its own model; ``switched`` tells the run page why.
            "fallback": True,
            "switched": True,
            "reason": SWITCH_REASON,
            **_call_tokens(response),
            "price_model": self.ref.get("model") or "",
        }
        try:
            self.state.answered_by.append(entry)
        except Exception:  # noqa: BLE001 - a state we cannot annotate still returns the answer
            log.debug("steering: could not record the switched model's answer", exc_info=True)


class SteeringExtension(LoopExtension):
    """Places the messages a person sent mid-run into the model's context,
    and moves the run to another model when one asks for it."""

    name = "steering"

    def __init__(self, agent: Any = None) -> None:
        # A weak reference: the extension belongs to the built agent, which
        # must not be kept alive by it. Only build settings are read from it.
        self._agent = weakref.ref(agent) if agent is not None else None

    def _agent_obj(self) -> Any:
        return self._agent() if self._agent is not None else None

    def _primary_ref(self) -> Dict[str, str]:
        agent = self._agent_obj()
        llm = getattr(agent, "_llm", None)
        try:
            provider = (agent.effective_provider(llm) or "").strip().lower() if agent is not None else ""
        except Exception:  # noqa: BLE001 - an unreadable provider is reported as unknown
            provider = str(getattr(agent, "provider", "") or "")
        return {"provider": provider,
                "model": _model_name(llm) or str(getattr(agent, "model", "") or "")}

    def _build(self, provider: str, model: str) -> Any:
        """The chat model a switch moves the run to, built with the agent's
        own sampling settings (not its key or base URL, which belong to its
        own provider), like a fallback model."""
        from agents.agent_utils import build_chat_model
        agent = self._agent_obj()
        return build_chat_model(
            provider=provider, model=model,
            temperature=getattr(agent, "temperature", None),
            max_tokens=getattr(agent, "max_tokens", None),
            streaming=getattr(agent, "streaming", False),
            thinking_level=getattr(agent, "thinking_level", None),
        )

    def _apply_switch(self, state: LoopState) -> None:
        """Make the latest model switch the run's model, built once. A switch
        that cannot be honoured (the model was disabled since, or it does not
        build) keeps an ``error`` and the run stays on the model it had."""
        usable = [s for s in state.model_switches if not s.get("error")]
        if not usable:
            return
        latest = usable[-1]
        current = state.scratch.get(SWITCH_SCRATCH)
        if current and current.get("msg_id") == latest.get("msg_id"):
            return
        try:
            from tools.delegation import resolve_model
            provider, model = resolve_model(str(latest.get("to") or latest.get("text") or ""))
            llm = self._build(provider, model)
        except Exception as exc:  # noqa: BLE001 - a bad switch is reported on the run, never fails it
            latest["error"] = str(exc)[:300]
            log.warning("steering: run %s could not switch to %s: %s", state.run_id,
                        latest.get("to"), exc)
            self._apply_switch(state)
            return
        before = ({"provider": current["provider"], "model": current["model"]}
                  if current else self._primary_ref())
        latest["from"] = "/".join(p for p in (before.get("provider"), before.get("model")) if p)
        latest["to"] = f"{provider}/{model}"
        state.scratch[SWITCH_SCRATCH] = {"msg_id": latest.get("msg_id"), "provider": provider,
                                         "model": model, "llm": llm}
        log.info("steering: run %s switched to %s/%s at step %s", state.run_id, provider, model,
                 latest.get("after_step"))

    def _restore(self, state: LoopState, transport: Any, step: int) -> None:
        """On a run's first model call, the messages it already took under the
        same id (a checkpoint resume, the chat's retry after folding). Each
        keeps its step when the run has come that far again, else it goes
        where the run is now, so it never moves on a later call."""
        state.scratch["steering_restored"] = True
        try:
            earlier = transport.delivered_steering(state.run_id) or []
        except Exception:  # noqa: BLE001 - nothing restored is the pre-steering behaviour
            earlier = []
        known = _known_ids(state)
        for msg in earlier:
            if msg.get("msg_id") in known:
                continue
            _record(state, _as_injection(msg, min(int(msg.get("delivered_step") or 0), step)))

    def _claim(self, state: LoopState, step: int) -> None:
        failures = int(state.scratch.get("steering_failures") or 0)
        if failures >= MAX_TRANSPORT_FAILURES:
            return
        transport = _transport(state)
        if not state.scratch.get("steering_restored"):
            self._restore(state, transport, step)
        try:
            claimed = transport.claim_steering(state.run_id, step)
        except Exception:  # noqa: BLE001 - counted as a failed call below
            claimed = None
        if claimed is None:
            state.scratch["steering_failures"] = failures + 1
            return
        state.scratch["steering_failures"] = 0
        if not claimed:
            return
        known = _known_ids(state)
        fresh = []
        for msg in claimed:
            if msg.get("msg_id") in known:
                continue
            inj = _as_injection(msg, step)
            _record(state, inj)
            fresh.append(inj)
        if fresh:
            log.info("steering: %d message(s) delivered to run %s at step %d",
                     len(fresh), state.run_id, step)
            _emit_delivered(state, fresh)

    def shape_messages(self, state: LoopState, inputs: Dict[str, Any],
                       scratchpad: List[Any]) -> List[Any]:
        steps = list(inputs.get("intermediate_steps") or [])
        if state.run_id and _owns(state):
            self._claim(state, len(steps))
        if state.model_switches:
            self._apply_switch(state)
        if not state.injections:
            return scratchpad
        return place_injections(scratchpad, steps, state.injections)

    def shape_prompt(self, state: LoopState, messages: List[Any]) -> List[Any]:
        messages = append_system_addition(messages, state.system_messages)
        switch = state.scratch.get(SWITCH_SCRATCH)
        if switch:
            from providers.adapters import supports_prompt_cache_control
            if not supports_prompt_cache_control(str(switch.get("provider") or "")):
                messages = strip_cache_markers(messages)
        return messages

    def wrap_model(self, state: LoopState, bound: Any, rebind: Any) -> Any:
        switch = state.scratch.get(SWITCH_SCRATCH)
        if not switch:
            return bound
        ref = {"provider": switch["provider"], "model": switch["model"]}
        return rebind(switch["llm"]).with_config(callbacks=[_SwitchedCallback(state, ref)])


def claim_after_answer(state: LoopState, step: int) -> List[Dict[str, Any]]:
    """Messages that arrived while the model wrote its final answer.

    The loop only looks for messages before a model call, so one sent during
    the last call would never be seen. ``StandardAgent`` asks here once the
    executor has answered; the messages are marked delivered at ``step`` and
    returned as injections (not yet on ``state.injections``: the caller runs
    one more pass with them and records them itself). A system-mode message
    is recorded on ``state.system_messages`` here already, so the extra pass
    reads it in its system prompt; it is returned too, marked by its mode, so
    the caller still makes that pass.
    """
    if not state.run_id or not _owns(state):
        return []
    try:
        claimed = _transport(state).claim_steering(state.run_id, step)
    except Exception:  # noqa: BLE001 - no follow-up pass is the old behaviour
        log.debug("steering: after-answer claim failed for %s", state.run_id, exc_info=True)
        return []
    known = _known_ids(state)
    fresh = [_as_injection(m, step) for m in (claimed or []) if m.get("msg_id") not in known]
    for item in fresh:
        if _is_system(item):
            state.system_messages.append(item)
        elif _is_switch(item):
            # Recorded, not returned: a model switch alone is no reason for
            # another pass. If one is made for other messages, it runs there.
            state.model_switches.append(item)
    if fresh:
        _emit_delivered(state, fresh)
    return [item for item in fresh if not _is_switch(item)]


def extension_for(agent: Any) -> Optional[LoopExtension]:
    """Every standard agent can be steered: the per-call check is one indexed
    lookup, and with nothing written the messages are left as they are."""
    try:
        from agents.standard_agent import StandardAgent
    except ImportError:
        return None
    return SteeringExtension(agent) if isinstance(agent, StandardAgent) else None


__all__ = [
    "MAX_TRANSPORT_FAILURES",
    "MODE_SWITCH_MODEL",
    "SWITCH_SCRATCH",
    "claim_after_answer",
    "STEER_HEADER",
    "SYSTEM_HEADER",
    "SteeringExtension",
    "append_system_addition",
    "format_system_addition",
    "extension_for",
    "format_injection",
    "injection_message",
    "is_steering_text",
    "place_injections",
    "strip_cache_markers",
]
