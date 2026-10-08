"""One decision call: the beat, the model call, tool-call guards and the decision run record."""
from __future__ import annotations

import inspect
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler

from playground import control
from playground.models import (
    SIM_CHANNEL, AgentDecision, Role, Scenario, utc_iso,
)

log = logging.getLogger(__name__)

# ── One agent's turn ──────────────────────────────────────────────────────────

@dataclass
class Beat:
    """The liveness record of one decision, shared with the waiting loop.

    A timeout on a model call has to mean "it has gone silent", not "it is
    taking a while": a model that is still streaming tokens is still working,
    and cutting it off at sixty seconds throws away an answer that was on its
    way. So the decision thread stamps ``last`` on every sign of life and the
    loop in :func:`_run_tick` watches that stamp instead of the total elapsed
    time. ``cancel`` is how the loop tells an abandoned decision to stop
    calling the model — the thread cannot be killed, but it can be told.

    Waiting is not the same as working, and that distinction is the whole
    reason for ``begin``. A decision sits in two queues before it costs
    anything: the thread pool's, when the tick has more agents than
    ``max_concurrent``, and the provider's, when a single-threaded model server
    serves one request at a time. A clock started at submit time runs through
    both, so the agents behind the first one used to forfeit turns they had not
    yet been given. ``submitted`` is only for the record; the
    clocks that matter start at ``work_started``, stamped by the worker thread
    itself, and at ``first_token``, stamped when the provider stops queueing
    the request and starts answering it.
    """
    submitted: float = field(default_factory=time.monotonic)
    last: float = field(default_factory=time.monotonic)
    #: When the worker thread actually picked this decision up. ``None`` while
    #: it is still queued in the pool — and a queued decision never times out.
    work_started: Optional[float] = None
    #: True once the provider has actually sent something. Until then only the
    #: silence timeout applies; a provider that cannot stream at all never sets
    #: it and is bounded by the hard cap instead.
    streaming: bool = False
    #: When the first token arrived — the moment the provider stopped queueing
    #: this request and started answering it. The hard cap is measured from
    #: here so that time spent in a busy server's queue is not charged to the
    #: agent as slowness.
    first_token: Optional[float] = None
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def begin(self) -> None:
        """Called on the worker thread: the waiting is over, the work starts."""
        with self.lock:
            now = time.monotonic()
            self.work_started = now
            self.last = now

    def touch(self, streaming: bool = False) -> None:
        with self.lock:
            self.last = time.monotonic()
            if streaming:
                if not self.streaming:
                    self.first_token = self.last
                self.streaming = True

    def started_work(self) -> bool:
        with self.lock:
            return self.work_started is not None

    def last_sign_of_life(self) -> float:
        with self.lock:
            return self.last

    def silent_for(self) -> float:
        with self.lock:
            return time.monotonic() - self.last

    def streamed_for(self) -> float:
        """Seconds since the first token — how long the model has actually been
        answering, which is what the hard cap is about."""
        with self.lock:
            if self.first_token is None:
                return 0.0
            return time.monotonic() - self.first_token

    def has_streamed(self) -> bool:
        with self.lock:
            return self.streaming


class _Cancelled(Exception):
    """The waiting loop gave up on this decision (stall or stop)."""


def _text_of(message: Any) -> str:
    """Flatten a message's content, which may be a string or content blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    return str(content or "")


def _accepts_config(fn: Any) -> bool:
    """Whether a model's ``invoke``/``stream`` takes a LangChain ``config``.

    Test doubles and hand-rolled clients implement ``invoke(messages)`` and
    nothing else; probing the signature beats calling twice and catching the
    TypeError, which on a real client would mean paying for the call twice.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "config" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _call_model(llm: Any, messages: List[Any], beat: Beat,
                callbacks: List[Any],
                on_delta: Optional[Callable[[str], None]] = None,
                ) -> Tuple[str, Dict[str, int], bool]:
    """Run one model call, streaming when the client can.

    Returns ``(text, usage, streamed)``. Streaming earns its keep twice: it is
    what makes "the model went silent" observable at all — without it a
    decision is one opaque blocking call whose only timeout is a deadline on
    the finished answer — and, through ``on_delta``, it is what lets the page
    watch an agent think instead of waiting out the whole tick in silence.
    ``on_delta`` is handed the text accumulated so far and must never raise.
    """
    stream = getattr(llm, "stream", None)
    config = {"callbacks": callbacks} if callbacks else None

    if callable(stream):
        parts: List[str] = []
        aggregate = None
        try:
            kwargs = {"config": config} if (config and _accepts_config(stream)) else {}
            for chunk in stream(messages, **kwargs):
                if beat.cancel.is_set():
                    raise _Cancelled()
                beat.touch(streaming=True)
                parts.append(_text_of(chunk))
                if on_delta is not None:
                    on_delta("".join(parts))
                try:
                    aggregate = chunk if aggregate is None else aggregate + chunk
                except Exception:
                    aggregate = None
            return "".join(parts), _usage_of(aggregate), True
        except (_Cancelled, control.SimRunStopped):
            raise
        except NotImplementedError:
            # A client that advertises stream() but cannot do it. Nothing was
            # spent, so falling through to invoke() does not double-charge.
            pass

    beat.touch()
    kwargs = {"config": config} if (config and _accepts_config(llm.invoke)) else {}
    reply = llm.invoke(messages, **kwargs)
    beat.touch()
    return _text_of(reply), _usage_of(reply), False


def _usage_of(reply: Any) -> Dict[str, int]:
    usage = getattr(reply, "usage_metadata", None) or {}
    return {
        "inbound": int(usage.get("input_tokens") or 0),
        "outbound": int(usage.get("output_tokens") or 0),
    }


def _estimated(text: str) -> int:
    try:
        from agents.callbacks import estimate_tokens
        return estimate_tokens(text)
    except Exception:
        return max(0, len(text or "") // 4)



class ToolCallLimitError(RuntimeError):
    """Raised when an agents-mode decision calls more tools in one tick than
    its scenario's ``max_tool_calls_per_tick`` allows."""


class _ToolCallLimitGuard(BaseCallbackHandler):
    """Cuts an agents-mode decision off once it has made too many tool calls
    in one tick.

    Mirrors ``agents.callbacks.guards.ToolRepetitionGuard``: count on
    ``on_tool_start``, raise on ``on_tool_end`` so the call that crossed the
    limit is still recorded before the run stops, instead of being cut off
    mid call.
    """

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exception
        self.limit = max(1, int(limit))
        self.calls = 0
        self.tripped = False

    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        self.calls += 1

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self.calls > self.limit:
            self.tripped = True
            raise ToolCallLimitError(
                f"made {self.calls} tool call(s) this tick, over the "
                f"scenario's limit of {self.limit}"
            )


class _BeatTouchCallback(BaseCallbackHandler):
    """Keeps an agents-mode decision's :class:`Beat` alive across the agent's
    own model and tool calls.

    A bare model's call streams tokens, and every one of them touches the
    beat (see ``_call_model``). An agent invocation is one blocking call with
    no streaming visible here, so without this a working agent that is simply
    busy calling tools would look silent to the stall detection in
    ``_run_decisions`` and be cut off mid turn.
    """

    def __init__(self, beat: Beat) -> None:
        super().__init__()
        self.beat = beat

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self.beat.touch(streaming=True)

    def on_llm_end(self, *args: Any, **kwargs: Any) -> None:
        self.beat.touch(streaming=True)

    def on_tool_start(self, *args: Any, **kwargs: Any) -> None:
        self.beat.touch(streaming=True)

    def on_tool_end(self, *args: Any, **kwargs: Any) -> None:
        self.beat.touch(streaming=True)



def _enable_stream_usage(llm: Any) -> None:
    """Ask for token usage on streamed completions where the client offers it.

    ``build_chat_model`` deliberately leaves ``stream_usage`` alone for the
    agent path; here the alternative is an estimate, so it is worth asking.
    Any client that does not have the knob is left exactly as it was.
    """
    if not hasattr(llm, "stream_usage"):
        return
    try:
        llm.stream_usage = True
    except Exception:
        pass


# ── Decisions as runs of record ──────────────────────────────────────────────

def decision_run_id(sim_run_id: str, tick: int, agent: str) -> str:
    """The run id one agent's turn at one tick always gets.

    Deterministic (``uuid5`` over the sim, the tick and the agent's own
    in-world name) rather than random, so a tick that is ever attempted twice
    — the narrow crash window between :func:`playground.store.save_tick` and
    its checkpoint, see ``run_simulation`` — opens the *same* run record
    instead of leaving an orphaned one behind from the attempt that did not
    finish. ``open_run`` upserts by id, so reopening it is exactly resuming it.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"scenario:{sim_run_id}:{tick}:{agent}"))


def _open_decision_run(*, role: Role, scenario: Scenario, sim_run_id: str,
                       tick: int, workspace: Optional[str], provider: str,
                       model: str, prompt: str) -> str:
    """Open a run for one agent's turn and write its prompt to the log.

    Best-effort: a simulation that cannot write a run record still runs. It
    returns "" in that case, and the decision simply has no log to link to.
    """
    if not sim_run_id:
        return ""
    agent_id = role.agent_id or role.display_name()
    try:
        from managers.run_manager import open_run, run_log_path
    except Exception:
        return ""
    run_id = decision_run_id(sim_run_id, tick, role.display_name())
    try:
        instance_id = None
        try:
            from instances import registry as instance_registry
            instance_id = instance_registry.ensure_instance(
                agent_id,
                instance_id=instance_registry.deterministic_id(
                    sim_run_id, role.display_name()),
                kind="sim_role",
                workspace=workspace,
                label=f"{scenario.name or 'sim'} · {role.display_name()}",
                state="active",
                pid=os.getpid(),
                provider=provider or None,
                model=model or None,
            )["instance_id"]
        except Exception:
            pass

        log_path = run_log_path(run_id)
        open_run(
            run_id, agent_id, pid=os.getpid(), channel=SIM_CHANNEL,
            log_file=str(log_path), workspace=workspace,
            title=f"{scenario.name or 'sim'}: {role.display_name()} · tick {tick}",
            scenario_id=scenario.scenario_id, sim_run_id=sim_run_id,
            sim_role=role.display_name(), tick=tick,
            provider=provider or "", model=model or "", input=prompt,
            instance_id=instance_id, link_to_session=False,
            # A decision always runs on a thread of *whichever* process is
            # executing this simulation, so its pid is that process's own —
            # the API server for the old thread-based path (tools, tests), or
            # the scenario's own subprocess (runtime/scenario_run.py) for a
            # launched run. Either way, signalling that pid to stop one turn
            # would take the whole process down with it, so this flag keeps
            # managers.runs.lifecycle._stop_run_record from doing that: it
            # marks the run record stopped and lets ``SimStopCallback`` (which
            # reads ``playground.control``, not this run's own pid) abort the
            # model call at its next callback instead. A scenario-wide stop is
            # a different, durable path — entity_runs status plus
            # ``control.request_stop`` — and does not go through here at all.
            in_process=True,
        )
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"--- Simulation turn started at {utc_iso()} ---\n"
                    f"Scenario: {scenario.name}\nRole    : {role.display_name()}\n"
                    f"Agent   : {agent_id}\nTick    : {tick}\n\n"
                    f"=== PROMPT ===\n{prompt}\n\n=== EXECUTION ===\n")
        return run_id
    except Exception:
        return ""


def _close_decision_run(run_id: str, sim_run_id: str, decision: AgentDecision,
                        *, status: str) -> None:
    """Close the run for one decision and append its outcome to the log."""
    if not run_id:
        return
    control.untrack(sim_run_id, run_id)
    try:
        from managers.run_manager import close_run, run_log_path
        with open(run_log_path(run_id), "a", encoding="utf-8") as f:
            f.write(f"\n=== OUTPUT ===\n{decision.raw_output or decision.error or ''}\n"
                    f"--- duration_ms={decision.duration_ms} ---\n")
        close_run(
            run_id,
            status=status,
            exit_code=0 if status == "completed" else 1,
            error=decision.error,
            output=decision.raw_output,
            process={"token_usage": {
                "inbound_tokens": decision.inbound_tokens,
                "outbound_tokens": decision.outbound_tokens,
                "estimated": decision.tokens_estimated,
            }},
        )
    except Exception:
        pass
