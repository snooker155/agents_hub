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

_OWNERS: Dict[str, "weakref.ref[LoopState]"] = {}
_OWNERS_LOCK = threading.Lock()


def format_injection(text: str) -> str:
    """The content of the message the model is sent for one steering message."""
    return f"{STEER_HEADER}\n\n{str(text or '').strip()}\n\n{_STEER_FOOTER}"


def is_steering_text(text: Any) -> bool:
    """Whether ``text`` is an injected steering message (see :func:`format_injection`)."""
    return isinstance(text, str) and text.lstrip().startswith(STEER_HEADER)


def injection_message(injection: Dict[str, Any]) -> Any:
    from langchain_core.messages import HumanMessage
    msg_id = str(injection.get("msg_id") or "")
    return HumanMessage(content=format_injection(str(injection.get("text") or "")),
                        id=f"steer:{msg_id}" if msg_id else None)


def _format_steps(steps: Any) -> List[Any]:
    from langchain.agents.format_scratchpad.tools import format_to_tool_messages
    return format_to_tool_messages(list(steps or []))


def place_injections(scratchpad: List[Any], steps: List[Any],
                     injections: List[Dict[str, Any]]) -> List[Any]:
    """``scratchpad`` with one user message per injection, each right after
    the tool results of its ``after_step``.

    ``scratchpad`` is what ``format_to_tool_messages(steps)`` made; its
    messages for the first ``k`` steps are exactly
    ``format_to_tool_messages(steps[:k])`` (it only appends), which is how the
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
            emitter({"type": "steer_delivered", "msg_id": inj.get("msg_id"),
                     "after_step": inj.get("after_step"), "run_id": state.run_id})
    except Exception:  # noqa: BLE001 - the notice is cosmetic, the message is already in the context
        log.debug("steering: delivered notice failed for %s", state.run_id, exc_info=True)


def _as_injection(msg: Dict[str, Any], after_step: int) -> Dict[str, Any]:
    return {
        "after_step": int(after_step),
        "text": str(msg.get("body") or ""),
        "msg_id": str(msg.get("msg_id") or ""),
        "mode": str(msg.get("mode") or "inject"),
        "at": str(msg.get("delivered_at") or "") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


class SteeringExtension(LoopExtension):
    """Places the messages a person sent mid-run into the model's context."""

    name = "steering"

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
        known = {i.get("msg_id") for i in state.injections}
        for msg in earlier:
            if msg.get("msg_id") in known:
                continue
            state.injections.append(_as_injection(msg, min(int(msg.get("delivered_step") or 0), step)))

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
        known = {i.get("msg_id") for i in state.injections}
        fresh = []
        for msg in claimed:
            if msg.get("msg_id") in known:
                continue
            inj = _as_injection(msg, step)
            state.injections.append(inj)
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
        if not state.injections:
            return scratchpad
        return place_injections(scratchpad, steps, state.injections)


def extension_for(agent: Any) -> Optional[LoopExtension]:
    """Every standard agent can be steered: the per-call check is one indexed
    lookup, and with nothing written the messages are left as they are."""
    try:
        from agents.standard_agent import StandardAgent
    except ImportError:
        return None
    return SteeringExtension() if isinstance(agent, StandardAgent) else None


__all__ = [
    "MAX_TRANSPORT_FAILURES",
    "STEER_HEADER",
    "SteeringExtension",
    "extension_for",
    "format_injection",
    "injection_message",
    "is_steering_text",
    "place_injections",
]
