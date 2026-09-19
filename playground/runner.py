"""
The tick loop: observe -> decide -> resolve.

::

    tick:
      1. observe  - the env builds each agent's private view      (cheap, parallel)
      2. decide   - every woken agent's LLM call fires concurrently (expensive)
      3. resolve  - the env collects all actions and applies them atomically in
                    one deterministic pass, breaking ties by its own rules

Two ways an agent gets a turn, chosen per scenario:

* ``synchronous`` — everybody acts every tick and the world resolves them
  simultaneously. No agent sees another's move within the tick, which is what
  makes markets and social dynamics interesting instead of a turn-based chat.
* ``triggered``   — an agent acts only when something reached it: a message, an
  interaction the world reports (``Environment.poke``), its own ``wake_every``
  heartbeat, an external poke injected through the API — or its own last move,
  because an agent that is doing something keeps its turn until it says it is
  done. Nobody pays for a turn with nothing to react to, and the run can sit
  idle waiting to be poked from outside. This mirrors the autonomous mode of
  :mod:`teams.runner`.

  Two things follow from "keeps its turn", and both are the difference between
  a sandbox and a run that ends on tick one. **A failed action is not an
  ending**: the character that searched a room and found nothing has learned
  something and is still looking, so it gets the next turn to try something
  else, and only a world where *nobody* did anything goes quiet. And a role
  marked ``npc`` opts out of the whole mechanism: it never opens the scene and
  never wakes itself, so a villain with two moves and nothing to do waits in
  its temple until somebody walks in, instead of being given busywork to keep
  the run alive.

Three things this module refuses to do, on purpose:

* **It never lets a model adjudicate the world.** The LLM returns a proposed
  action; the environment decides what actually happened.
* **It never runs unbounded.** Tick cap, wall-clock cap, cost ceiling and the
  workspace budget are all checked between ticks, and any of them stops the
  *simulation*, not just one agent's turn.
* **It never makes "stop" mean "later".** A stop interrupts the decisions in
  flight (see :mod:`playground.control`) rather than letting the current tick
  finish the calls the user just cancelled.

Every decision is also a run of record: it opens a run, writes a log file and
closes it like any other agent execution, so one agent's work in a simulation
is inspectable from the Messages page instead of living only inside a tick blob.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait as futures_wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from playground import control, store
from playground.environments import create_environment
from playground.environments.base import Environment
from playground.models import (
    SIM_CHANNEL, SYNCHRONOUS, TRIGGERED, TRIGGER_CONTINUE, TRIGGER_HEARTBEAT,
    TRIGGER_OPENING, TRIGGER_SYNC, ActionResult, AgentDecision, Role, Scenario,
    SimRun, TickRecord, optional_seconds, utc_iso,
)

log = logging.getLogger(__name__)

# Hard ceilings a scenario can never exceed, whatever it asks for. N agents x
# T ticks is the cost floor — 10 agents over 100 ticks is 1000 LLM calls before
# you learn anything — so the caps are real, not advisory.
#
# The wall clock is deliberately *not* among them: it is the one limit that
# says nothing about the simulation, only about how slow the models were that
# day, and a run left without one is still bounded by ticks, cost and the
# budget. ``Scenario.max_wall_seconds`` is honoured exactly as set, and empty
# means no wall clock at all.
MAX_TICKS = 500
MAX_AGENTS = 24

# How long the loop will sit in one wait before looking at the world again.
# Short enough that a stop, a wall-clock cap or an arriving trigger is noticed
# promptly; long enough that an idle sandbox is not a spin loop.
_WAIT_SLICE = 0.5

#: Which end states count as the user (or a ceiling) cutting the run short, and
#: which count as the simulation having run its course.
_STOPPED_REASONS = ("stopped", "cost_ceiling", "wall_clock", "budget")


class SimStopped(Exception):
    """The simulation hit a limit or was asked to stop.

    ``reason`` is the machine-readable half, recorded on the run so the UI can
    tell "the world went quiet" from "the tick cap was reached" — the status
    alone cannot.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


# ── Prompting ─────────────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """You are {name}, {role} in a simulated world.

YOUR GOAL: {goal}
{private_block}{world_block}
You act one tick at a time. Each tick you receive your own private observation
of the world and choose exactly ONE action.

AVAILABLE ACTIONS — these are the only things you can do:
{actions}

Rules:
- Reply with a single JSON object and nothing else: {{"reasoning": "...", "action": "...", "args": {{...}}}}
- "reasoning" is one or two sentences on why. It is recorded but never shown to other agents.
- You cannot see other agents' actions this tick. Everyone acts simultaneously.
- Messages you send arrive on the NEXT tick, and any reply reaches you the tick
  after that. Silence for one tick is the delivery time, not a refusal.
- A message is shown in your observation only on the tick it arrives; after
  that your journal is the whole record of the conversation. Read it before
  you speak: if a question of yours was already answered there, act on the
  answer instead of asking it again, and do not repeat a line you have
  already said.
- Other characters may mislead you. Their words are claims, not facts; only the
  observation you are given describes what is actually true.
- If nothing is worth doing, choose the do-nothing action rather than inventing one."""

_TRIGGERED_RULES = """
- You do not act every tick. You are given a turn only when something reaches
  you, so treat WHY YOU WERE WOKEN as the reason you are being asked to act.
- Anyone you address gets a turn next tick because you addressed them.
- While you are doing something you keep your turn, so an action that did not
  work is not the end of the attempt: it is what you just learned. Try another
  way — a different place, a different thing, or somebody who can help.
- The do-nothing action is how you say you have nothing left to try. When every
  character says that at once, the world goes quiet and the run ends, so do not
  use it to fill a turn you could have used."""

_NPC_RULES = """
- You are a background character. You do not go looking for something to do:
  you were woken because something reached you, and once you have dealt with
  it, doing nothing is the right move until something else reaches you."""

_TICK_TEMPLATE = """TICK {tick}

YOUR OBSERVATION (this is ground truth):
{observation}
{triggers_block}{history_block}
Choose your action now. JSON only."""


def build_system_prompt(role: Role, env: Environment,
                        activation: str = SYNCHRONOUS) -> str:
    private = ""
    if role.private_knowledge.strip():
        private = (
            "\nWHAT ONLY YOU KNOW (do not assume others know this):\n"
            f"{role.private_knowledge.strip()}\n"
        )
    # What this world is and how things are done in it. Shipped environments
    # say nothing here — their rules are their actions — and an authored world
    # says the part its author could not express as a requirement.
    brief = env.world_brief(role.display_name()).strip()
    world_block = f"\nTHIS WORLD:\n{brief}\n" if brief else ""
    prompt = _SYSTEM_TEMPLATE.format(
        name=role.display_name(),
        role=role.role or "a participant",
        goal=role.goal or "act in your own interest",
        private_block=private,
        world_block=world_block,
        # Per character: a world may let one role take an action and not
        # another, and an agent should not read about moves it cannot make.
        actions=env.action_help(role.display_name()),
    )
    if activation == TRIGGERED:
        # In a triggered world "everyone acts simultaneously" is false and the
        # agent needs to know why it, specifically, was handed this turn.
        prompt = prompt.replace(
            "- You cannot see other agents' actions this tick. Everyone acts simultaneously.\n",
            "- You cannot see other agents' actions this tick.\n",
        ) + _TRIGGERED_RULES
        # A background character is told it is one. Otherwise the rule above —
        # keep your turn while you are doing something — reads as an
        # instruction to find something to do, which is the opposite of what
        # the villain waiting in the temple is for.
        if role.npc:
            prompt += _NPC_RULES
    return prompt


def build_tick_prompt(observation: Dict[str, Any], tick: int,
                      history: List[str],
                      triggers: Optional[List[str]] = None) -> str:
    history_block = ""
    if history:
        history_block = ("\nYOUR JOURNAL — what you did and what was said to you:\n"
                         + "\n".join(history) + "\n")
    triggers_block = ""
    reasons = [r for r in (triggers or []) if r and r != TRIGGER_SYNC]
    if reasons:
        triggers_block = ("\nWHY YOU WERE WOKEN:\n"
                          + "\n".join(f"- {r}" for r in reasons) + "\n")
    return _TICK_TEMPLATE.format(
        tick=tick,
        observation=json.dumps(observation, indent=2, ensure_ascii=False, default=str),
        triggers_block=triggers_block,
        history_block=history_block,
    )


def parse_decision(text: str) -> Dict[str, Any]:
    """Pull ``{reasoning, action, args}`` out of a model reply.

    Models wrap JSON in prose and code fences no matter how firmly you ask them
    not to, so this is forgiving about the wrapper and strict about the content.
    """
    raw = (text or "").strip()
    if not raw:
        return {"error": "empty response"}

    candidate = raw
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        candidate = fence.group(1).strip()

    parsed = None
    try:
        parsed = json.loads(candidate)
    except Exception:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
            except Exception:
                parsed = None

    if not isinstance(parsed, dict):
        return {"error": "no JSON object in response"}
    action = str(parsed.get("action") or "").strip()
    if not action:
        return {"error": "response has no 'action'"}
    args = parsed.get("args")
    return {
        "reasoning": str(parsed.get("reasoning") or ""),
        "action": action,
        "args": args if isinstance(args, dict) else {},
    }


_PARTIAL_REASONING = re.compile(r'"reasoning"\s*:\s*"')
_PARTIAL_ACTION = re.compile(r'"action"\s*:\s*"([^"]*)"')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "", "f": ""}


def partial_decision(text: str) -> Dict[str, str]:
    """What can be read out of a *half-written* reply.

    The finished answer is JSON, and JSON cannot be parsed until its last
    brace arrives — which is exactly the moment the watching is over. So the
    reasoning string is read out by hand, character by character, and whatever
    has been written of it so far is what the page shows. Nothing here is used
    to decide anything: it is display only, which is why it is forgiving where
    ``parse_decision`` is strict.
    """
    raw = text or ""
    out = {"reasoning": "", "action": ""}
    m = _PARTIAL_REASONING.search(raw)
    if m:
        buf: List[str] = []
        i, n = m.end(), len(raw)
        while i < n:
            c = raw[i]
            if c == "\\":
                nxt = raw[i + 1] if i + 1 < n else ""
                if nxt == "u" and i + 6 <= n - 1 + 1:
                    try:
                        buf.append(chr(int(raw[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                buf.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                break
            buf.append(c)
            i += 1
        out["reasoning"] = "".join(buf)
    a = _PARTIAL_ACTION.search(raw)
    if a:
        out["action"] = a.group(1)
    return out


# ── Model resolution ──────────────────────────────────────────────────────────

def _agent_model(agent_id: str) -> Tuple[str, str]:
    """The (provider, model) an agent definition carries, if it names one."""
    if not agent_id:
        return "", ""
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:
        return "", ""
    if spec is None:
        return "", ""
    provider = (spec.provider or "").strip()
    return ("" if provider == "inherit" else provider), (spec.model or "").strip()


def _workspace_model(workspace: Optional[str]) -> Tuple[str, str]:
    """Where the header's model picker currently points for this workspace.

    Mirrors the picker's own precedence: an explicit override wins, otherwise
    the workspace default. ``global`` means "no workspace opinion" — fall
    through to .env.
    """
    if not workspace:
        return "", ""
    try:
        from common.workspace_context import workspace_name_from_path
        from workspace import get_workspace_metadata, get_workspace_default_model_config
        name = workspace_name_from_path(workspace) or workspace
        meta = get_workspace_metadata(name) or {}
        override = meta.get("model_override") or {}
        provider = (override.get("provider") or "").strip()
        if provider == "global":
            return "", ""
        eff = override if provider and provider != "workspace_default" else (
            get_workspace_default_model_config(meta) or {}
        )
        return (eff.get("provider") or "").strip(), (eff.get("model") or "").strip()
    except Exception:
        return "", ""


def resolve_model(role: Role, scenario: Scenario,
                  workspace: Optional[str] = None) -> Tuple[str, str]:
    """Which model answers for one role — the most specific layer wins:

    role override → the agent's own model → the scenario default → the
    workspace picker in the header → whatever ``build_chat_model`` falls back
    to (``DEFAULT_PROVIDER`` in .env).

    The layer that names the *model* also supplies the provider, so a model
    can never be handed to a client that does not serve it — the failure mode
    of picking the two independently.
    """
    layers = [
        ((role.provider or "").strip(), (role.model or "").strip()),
        _agent_model(role.agent_id),
        ((scenario.default_provider or "").strip(), (scenario.default_model or "").strip()),
        _workspace_model(workspace or scenario.workspace),
    ]
    for provider, model in layers:
        if model:
            return provider, model
    # No layer names a model: a provider alone still steers the fallback.
    for provider, _ in layers:
        if provider:
            return provider, ""
    return "", ""


def _run_cost(provider: str, model: str, inbound: int, outbound: int) -> float:
    try:
        from common.pricing import load_price_map, run_cost_usd
        return round(run_cost_usd(
            {"provider": provider, "model": model,
             "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound}}},
            load_price_map(),
        ), 6)
    except Exception:
        return 0.0


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


def decide(role: Role, observation: Dict[str, Any], env: Environment, tick: int,
           history: List[str], scenario: Scenario,
           workspace: Optional[str] = None, *,
           sim_run_id: str = "", triggers: Optional[List[str]] = None,
           beat: Optional[Beat] = None) -> AgentDecision:
    """Ask one agent's model for its action. Never raises.

    The call is recorded as a run of its own — open, log file, close — so an
    agent's work inside a simulation is inspectable from the Messages page the
    same way every other agent execution is, instead of being buried in a tick
    blob that only the playground can read.
    """
    name = role.display_name()
    decision = AgentDecision(agent=name, observation=observation,
                             triggers=list(triggers or []))
    beat = beat or Beat()
    # The clock starts here, on the worker thread, not when the tick submitted
    # this decision: everything before this line was queueing, and queueing is
    # not an agent being slow.
    beat.begin()
    started = time.monotonic()

    provider, model = resolve_model(role, scenario, workspace)
    system_prompt = build_system_prompt(role, env, scenario.activation)
    tick_prompt = build_tick_prompt(observation, tick, history, decision.triggers)

    run_id = _open_decision_run(
        role=role, scenario=scenario, sim_run_id=sim_run_id, tick=tick,
        workspace=workspace, provider=provider, model=model,
        prompt=f"{system_prompt}\n\n---\n\n{tick_prompt}",
    )
    decision.run_id = run_id
    if run_id:
        control.track(sim_run_id, run_id)

    text, usage, streamed = "", {"inbound": 0, "outbound": 0}, False
    try:
        if control.is_stopped(sim_run_id) or beat.cancel.is_set():
            raise _Cancelled()
        from agents.agent_utils import build_chat_model
        llm = build_chat_model(provider=provider or None, model=model or None,
                               temperature=0.7, streaming=True)
        _enable_stream_usage(llm)
        callbacks: List[Any] = []
        if sim_run_id:
            callbacks.append(control.SimStopCallback(sim_run_id))
        if run_id:
            # Lets the runs UI stop one agent's turn, the same contract every
            # other run honours.
            try:
                from agents.callbacks import RunStopCallback
                callbacks.append(RunStopCallback(run_id))
            except Exception:
                pass
        text, usage, streamed = _call_model(llm, [
            ("system", system_prompt), ("human", tick_prompt),
        ], beat, callbacks, _progress_reporter(sim_run_id, tick, name))
    except (_Cancelled, control.SimRunStopped):
        decision.error = "stopped"
        decision.duration_ms = int((time.monotonic() - started) * 1000)
        _close_decision_run(run_id, sim_run_id, decision, status="stopped")
        return decision
    except Exception as e:  # noqa: BLE001
        decision.error = f"{type(e).__name__}: {e}"
        decision.duration_ms = int((time.monotonic() - started) * 1000)
        _close_decision_run(run_id, sim_run_id, decision, status="failed")
        return decision

    decision.raw_output = text
    decision.duration_ms = int((time.monotonic() - started) * 1000)
    decision.inbound_tokens = usage["inbound"]
    decision.outbound_tokens = usage["outbound"]
    if streamed and not (usage["inbound"] or usage["outbound"]):
        # Not every provider reports usage on a streamed completion. An
        # unpriced decision would silently exempt the run from its own cost
        # ceiling, so estimate rather than record zero — and say that it is an
        # estimate, which is what ``tokens_estimated`` is for.
        decision.inbound_tokens = _estimated(system_prompt) + _estimated(tick_prompt)
        decision.outbound_tokens = _estimated(text)
        decision.tokens_estimated = True
    decision.cost = _run_cost(
        provider, model, decision.inbound_tokens, decision.outbound_tokens
    )

    parsed = parse_decision(decision.raw_output)
    if "error" in parsed:
        decision.error = parsed["error"]
    else:
        decision.reasoning = parsed["reasoning"]
        decision.action = {"action": parsed["action"], "args": parsed["args"]}

    _close_decision_run(run_id, sim_run_id, decision,
                        status="failed" if decision.error else "completed")
    return decision


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
    run_id = str(uuid4())
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
            # This turn runs on a thread of the server process, so its pid is
            # the server's. Without this flag a stop from the Messages page
            # would signal that pid — see run_manager._stop_run_record.
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


# ── The loop ──────────────────────────────────────────────────────────────────

def _publish(sim_run_id: str, event: Dict[str, Any]) -> None:
    """Stream an event to the ``sim:<id>`` channel. Never fatal."""
    try:
        from common.session_broker import broker
        broker.publish_threadsafe(f"sim:{sim_run_id}", event)
    except Exception:
        pass


#: How often a decision in progress reports back. Every chunk would be a
#: message per token; a beat under a second is fast enough to read as live and
#: slow enough that a tick with eight agents is not a firehose.
_PROGRESS_EVERY = 0.6


def _progress_reporter(sim_run_id: str, tick: int, agent: str):
    """A callback that streams one agent's half-written answer to the page.

    Throttled and deduplicated: the transcript is being read by a human, so
    the only thing that matters is that the text visibly grows. Failures are
    swallowed — an agent's turn must not depend on anyone watching it.
    """
    if not sim_run_id:
        return None
    state = {"at": 0.0, "reasoning": "", "action": ""}

    def report(text: str) -> None:
        now = time.monotonic()
        if now - state["at"] < _PROGRESS_EVERY:
            return
        state["at"] = now
        try:
            seen = partial_decision(text)
        except Exception:  # noqa: BLE001
            return
        if seen["reasoning"] == state["reasoning"] and seen["action"] == state["action"]:
            return
        state.update(seen)
        _publish(sim_run_id, {"type": "agent_stream", "tick": tick, "agent": agent,
                              "reasoning": seen["reasoning"], "action": seen["action"]})

    return report


def _promote_to_running(run: SimRun) -> None:
    """Turn a starting run into a running one — what its first sign of life
    means, whether that is a tick or a world that went straight to waiting.

    The row is only promoted while it still says ``starting``, so a stop that
    landed during the first tick survives the tick that finished after it.
    """
    if run.status != "starting":
        return
    run.status = "running"
    if store.mark_running(run.sim_run_id):
        _publish(run.sim_run_id, {"type": "status", "sim_run_id": run.sim_run_id,
                                  "status": "running"})


def stop_simulation(sim_run_id: str) -> bool:
    """Stop a simulation now.

    Both halves matter: the durable status (so a reloaded page, or another
    process, sees it) and the in-memory event (so the decisions in flight stop
    calling the model instead of finishing the tick the user cancelled).
    """
    run = store.get_sim_run(sim_run_id)
    if run is None or run.status not in ("starting", "running", "stopping"):
        return False
    store.request_stop(sim_run_id)
    control.request_stop(sim_run_id)
    _publish(sim_run_id, {"type": "stopping", "sim_run_id": sim_run_id,
                          "status": "stopping"})
    return True


def trigger_agent(sim_run_id: str, agent: str, text: str,
                  sender: str = "(external)") -> bool:
    """Poke one agent in a running simulation from outside the world.

    Delivered through the environment's own inbox on the next tick, so an
    injected message is indistinguishable from one an agent sent: it lands in
    the recipient's observation, and in triggered mode it wakes them.
    """
    run = store.get_sim_run(sim_run_id)
    if run is None or run.status not in ("starting", "running", "stopping"):
        return False
    return control.push_trigger(sim_run_id, agent, text, sender)


def run_simulation(
    scenario_id: str,
    *,
    workspace: Optional[str] = None,
    on_start: Optional[Callable[[SimRun], None]] = None,
    on_tick: Optional[Callable[[TickRecord], None]] = None,
) -> SimRun:
    """Run a scenario to completion (or to whichever limit it hits first)."""
    scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise ValueError(f"Scenario not found: {scenario_id}")
    if not scenario.roles:
        raise ValueError("Scenario has no roles — a society needs participants")
    if len(scenario.roles) > MAX_AGENTS:
        raise ValueError(f"Scenario has {len(scenario.roles)} roles; the cap is {MAX_AGENTS}")

    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    if env is None:
        raise ValueError(f"Unknown environment: {scenario.environment}")

    names = [r.display_name() for r in scenario.roles]
    if len(set(names)) != len(names):
        raise ValueError("Two roles share a display name — names address agents in-world")
    # Names *and* the roles they were cast in: an authored world places
    # characters by role and decides by role what each may do, and the role
    # string is a scenario's, not the environment's.
    env.register_cast([
        {"name": r.display_name(), "role": r.role, "agent_id": r.agent_id}
        for r in scenario.roles
    ])

    ws = workspace or scenario.workspace
    run = SimRun(
        scenario_id=scenario_id, workspace=ws, environment=scenario.environment,
        activation=scenario.activation,
        # Freeze the scenario here: everything past this line reads from the
        # live row, which the user is free to edit while the sim runs and after
        # it finishes.
        config=scenario.to_dict(),
    )
    control.register(run.sim_run_id)
    store.save_sim_run(run)
    _publish(run.sim_run_id, {"type": "sim_start", **run.to_dict(),
                              "scenario": scenario.to_dict()})
    # The row exists, so the caller can be handed the run before a single model
    # has been called — the first tick is minutes away, and until it lands the
    # only honest thing to show is that this run is starting.
    if on_start:
        try:
            on_start(run)
        except Exception:
            pass

    max_ticks = max(1, min(int(scenario.max_ticks), MAX_TICKS))
    wall_cap = optional_seconds(scenario.max_wall_seconds)
    started = time.monotonic()
    spend = 0.0
    # Per-agent rolling summary of its own past actions — the memory_horizon
    # knob. Without it, tick 200's prompt carries 199 ticks of transcript.
    history: Dict[str, List[str]] = {n: [] for n in names}
    tick = 0
    # Triggered mode only: who the last tick left mid-action, and what each
    # agent has been repeating, so a character that is out of ideas stops
    # being handed turns. Both are rebuilt every tick from the record.
    carry: Dict[str, List[str]] = {}
    streaks: Dict[str, List[Any]] = {}

    try:
        while tick < max_ticks:
            _check_between_ticks(run.sim_run_id, started, wall_cap, spend, scenario, ws)
            _deliver_external(env, run.sim_run_id, names)

            plan = _activation_plan(env, scenario, tick + 1, carry)
            if not plan:
                # Triggered mode with nothing to react to — and, since an agent
                # keeps its turn while it is acting, that now means every
                # character that had one did nothing with it. Waiting beats
                # both alternatives: ending a live sandbox the moment it goes
                # quiet, and burning ticks on agents with nothing to do. A
                # world that is up and waiting to be poked is running, not
                # starting.
                _promote_to_running(run)
                if not _wait_out_idle(env, run.sim_run_id, scenario, names,
                                      started, wall_cap):
                    raise SimStopped(
                        "idle",
                        "every agent has run out of things to do and nothing "
                        "reached them",
                    )
                continue

            tick += 1
            record = _run_tick(env, scenario, run.sim_run_id, tick, history, ws, plan)
            carry = _continuations(env, scenario, record, streaks)
            spend += record.cost
            run.ticks_done = tick
            run.total_cost = round(spend, 6)
            store.save_tick(record)
            _promote_to_running(run)
            # The run's own counters ride with the tick: the page's meter reads
            # them, and without them it is stale until the next poll — which,
            # on a world that ticks faster than the poll, is never.
            _publish(run.sim_run_id, {"type": "tick", **record.to_dict(),
                                      "ticks_done": run.ticks_done,
                                      "total_cost": run.total_cost})
            if on_tick:
                try:
                    on_tick(record)
                except Exception:
                    pass

            # A stop that landed mid-tick already cut the decisions short;
            # ending here keeps a half-finished tick from being run again.
            if control.is_stopped(run.sim_run_id) or store.stop_requested(run.sim_run_id):
                raise SimStopped("stopped", "stopped by request")

            if env.is_done():
                # A world that authored its own ending says so; the shipped
                # ones only ever reach one, so "terminal state" is all there is
                # to report for them.
                ending = str(getattr(env, "ending", "") or "").strip()
                log.info("sim %s: environment reached a terminal state at tick %d",
                         run.sim_run_id, tick)
                raise SimStopped("terminal",
                                 ending or "the world reached a terminal state")

        raise SimStopped("max_ticks", f"reached the cap of {max_ticks} ticks")

    except SimStopped as e:
        run.stop_reason = e.reason
        run.status = "stopped" if e.reason in _STOPPED_REASONS else "completed"
        run.error = e.detail if e.reason in _STOPPED_REASONS else None
        log.info("sim %s finished: %s (%s)", run.sim_run_id, e.reason, e.detail)
    except Exception as e:  # noqa: BLE001
        log.exception("simulation failed")
        run.status = "failed"
        run.stop_reason = "error"
        run.error = f"{type(e).__name__}: {e}"

    run.scores = env.score()
    run.final_state = env.state()
    run.finished_at = utc_iso()
    store.save_sim_run(run)
    control.release(run.sim_run_id)
    _publish(run.sim_run_id, {"type": "done", **run.to_dict()})
    return run


def _check_between_ticks(sim_run_id: str, started: float,
                         wall_cap: Optional[float],
                         spend: float, scenario: Scenario,
                         workspace: Optional[str]) -> None:
    """Every ceiling that ends the simulation rather than one agent's turn.

    ``wall_cap`` of ``None`` is a scenario with no wall clock: the run then
    ends on ticks, cost, the budget or the button, and not because the models
    were having a slow afternoon.
    """
    if control.is_stopped(sim_run_id) or store.stop_requested(sim_run_id):
        raise SimStopped("stopped", "stopped by request")
    elapsed = time.monotonic() - started
    if wall_cap is not None and elapsed > wall_cap:
        raise SimStopped("wall_clock",
                         f"wall-clock cap reached ({elapsed:.0f}s of {wall_cap:.0f}s)")
    if scenario.cost_ceiling and spend >= scenario.cost_ceiling:
        raise SimStopped(
            "cost_ceiling",
            f"cost ceiling reached (${spend:.4f} of ${scenario.cost_ceiling:.2f})",
        )
    try:
        from common.budget import check_budget
        check_budget(workspace)
    except SimStopped:
        raise
    except Exception as e:  # noqa: BLE001
        raise SimStopped("budget", f"budget: {e}")


def _deliver_external(env: Environment, sim_run_id: str, names: List[str]) -> None:
    """Hand any externally injected triggers to the world.

    They go in through ``queue_message`` rather than a side channel: the point
    of an external trigger is that the agent cannot tell it apart from a
    colleague's message, and the environment already knows how to deliver one.
    """
    for item in control.drain_triggers(sim_run_id):
        agent = str(item.get("agent") or "")
        text = str(item.get("text") or "")
        if agent not in names:
            env.log_event(f"external trigger for unknown agent {agent!r} dropped")
            continue
        # No line of our own: queue_message logs the delivery, and what was
        # said is worth more in the log than the fact that something was.
        env.queue_message(str(item.get("sender") or "(external)"), agent, text)


#: How many ticks running an agent may repeat the same move, to the same
#: effect, before the loop stops handing it turns on its own account. Three is
#: "it tried, it tried again, it tried once more": enough to get past a lock
#: that needs a key fetched in between, short of a character that will hammer
#: the same door until the tick cap.
_STUCK_REPEATS = 3


def _activation_plan(env: Environment, scenario: Scenario, tick: int,
                     carry: Optional[Dict[str, List[str]]] = None,
                     ) -> Dict[str, List[str]]:
    """Who acts this tick, and why.

    Synchronous scenarios wake everyone, which is the whole point of them. A
    triggered scenario wakes an agent for five reasons: something is in its
    inbox, the world did something to it, its own heartbeat came round, it is
    the opening tick and somebody has to start — or ``carry``, the agents that
    were in the middle of something when the last tick resolved (see
    :func:`_continuations`).

    ``npc`` roles are left out of the opening: the point of a background
    character is that it waits to be reached. An explicit ``wake_every`` is
    still honoured for one, because an author who typed a heartbeat onto a
    patrolling guard meant it.
    """
    if scenario.activation != TRIGGERED:
        return {r.display_name(): [TRIGGER_SYNC] for r in scenario.roles}

    openers = {r.display_name() for r in scenario.roles if r.starts}
    if not openers:
        # Nobody claimed the first move; the active cast opens the scene rather
        # than a world that starts asleep and never wakes. A cast of nothing
        # but NPCs opens nothing — such a world is waiting for an external
        # poke, and the idle grace is what keeps it up for one.
        openers = {r.display_name() for r in scenario.roles if not r.npc}

    plan: Dict[str, List[str]] = {}
    for role in scenario.roles:
        name = role.display_name()
        reasons = list(env.pending_triggers(name))
        if tick == 1 and name in openers:
            reasons.append(TRIGGER_OPENING)
        if role.wake_every > 0 and tick % role.wake_every == 0:
            reasons.append(TRIGGER_HEARTBEAT)
        reasons.extend((carry or {}).get(name) or ())
        if reasons:
            plan[name] = reasons
    return plan


def _continuations(env: Environment, scenario: Scenario, record: TickRecord,
                   streaks: Dict[str, List[Any]]) -> Dict[str, List[str]]:
    """Who is still in the middle of something, after the tick just resolved.

    A triggered world used to end the moment its opening move touched nobody:
    the hero searched the tavern, found nothing, nothing was addressed to
    anyone, and the run was over on tick one with "no agent has anything to
    react to". But a search that found nothing is not an ending — it is the
    reason to look somewhere else. So an agent that *did* something keeps the
    next turn, whether or not it worked, and the world only goes quiet when
    every agent that had a turn chose to do nothing with it.

    Four things are deliberately not a continuation:

    * **The do-nothing action** (``Environment.IDLE_ACTIONS``). It is the one
      way a character says the scene is over as far as it is concerned, and a
      loop that woke it again anyway would take that answer away.
    * **A move that landed on somebody else.** Speaking to a character, handing
      it something, doing something to it — that *is* the handover, and the
      recipient is woken by it. Keeping the turn as well would have both ends
      of every conversation talking at once.
    * **An NPC.** It gets turns from the world, never from itself.
    * **The same move, three ticks running, to the same effect.** A character
      that cannot open the door is out of ideas, not mid-action; it goes quiet
      and waits for the world — or another character — to change something.
      Without this the anti-idle rule is just a budget leak with a plot.
    """
    if scenario.activation != TRIGGERED:
        return {}
    idle_actions = set(getattr(env, "IDLE_ACTIONS", ()) or ())
    active = {r.display_name() for r in scenario.roles if not r.npc}
    cast = {r.display_name() for r in scenario.roles}
    carry: Dict[str, List[str]] = {}
    for res in record.resolutions:
        name = res.agent
        if name not in active:
            continue
        if res.ok and (res.action in idle_actions or _handed_over(res, cast)):
            streaks.pop(name, None)      # it held, or the turn is somebody else's
            continue
        signature = json.dumps([res.action, res.args, res.ok],
                               sort_keys=True, default=str)
        last, count = streaks.get(name) or ["", 0]
        count = count + 1 if signature == last else 1
        streaks[name] = [signature, count]
        if count >= _STUCK_REPEATS:
            continue
        carry[name] = [_continue_reason(res)]
    return carry


def _handed_over(res: ActionResult, cast: set) -> bool:
    """Did this move pass the turn to another character?

    Read off the arguments rather than from a flag the environment sets,
    because "who did you do it to" is already written there — ``speak_to``'s
    ``agent``, ``give_item``'s ``agent``, an authored attack's ``target`` — and
    every one of those wakes the character it names. A world could instead
    report causality from ``poke``, which would also catch "you walked into the
    room I was standing in"; that is a bigger change than the question needs,
    and being woken alongside somebody you interrupted is not a bug.
    """
    return any(str(value) in cast and str(value) != res.agent
               for value in (res.args or {}).values())


def _continue_reason(res: ActionResult) -> str:
    """Why an agent is being handed another turn, in the words it will read.

    The wake reasons land in the agent's prompt under "WHY YOU WERE WOKEN", so
    this is not a log line: "your last action failed, try something else" is a
    turn an agent can use, and ``continuing`` on its own is a turn it has to
    guess the point of.
    """
    if res.action == "(none)":
        return (f"{TRIGGER_CONTINUE}: your last turn produced no action "
                f"({res.message}) — take one now")
    if res.ok:
        return (f"{TRIGGER_CONTINUE}: nothing interrupted you after "
                f"{res.action} — the next move is still yours")
    return (f"{TRIGGER_CONTINUE}: {res.action} did not work ({res.message}) "
            "— you still have the turn, so try another way")


def _wait_out_idle(env: Environment, sim_run_id: str, scenario: Scenario,
                   names: List[str], started: float,
                   wall_cap: Optional[float]) -> bool:
    """Sit out an idle world, waiting to be poked. True if something arrived.

    ``idle_grace_seconds`` is what separates a batch run from a live sandbox:
    at 0 a quiet world ends immediately, and above it the run stays open for
    external triggers while still honouring the stop button and the wall clock.
    """
    grace = max(0.0, float(scenario.idle_grace_seconds or 0.0))
    if grace <= 0:
        return False
    _publish(sim_run_id, {"type": "idle", "waiting_seconds": grace})
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if control.is_stopped(sim_run_id) or store.stop_requested(sim_run_id):
            raise SimStopped("stopped", "stopped by request")
        if wall_cap is not None and time.monotonic() - started > wall_cap:
            raise SimStopped("wall_clock", "wall-clock cap reached while idle")
        control.wait_for_trigger(sim_run_id, min(_WAIT_SLICE, deadline - time.monotonic()))
        if control.pending_trigger_count(sim_run_id):
            return True
    return False


def _recent(history: Dict[str, List[str]], name: str, horizon: int) -> List[str]:
    """The last *horizon* journal lines for one agent — and none at all for 0.

    Written out rather than sliced inline because ``lines[-0:]`` is the whole
    list: a horizon of 0 would hand an agent its entire history, the most
    expensive prompt there is, from the knob that reads like "no memory".
    """
    if horizon <= 0:
        return []
    return history.get(name, [])[-horizon:]


def _clip(text: str, limit: int = 400) -> str:
    """One journal line is one line: long speeches are quoted, not replayed."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _heard_lines(tick: int, observation: Dict[str, Any]) -> List[str]:
    """What an agent was told this tick, as journal lines.

    Messages are delivered once, inside the observation of the tick they
    arrive. Without a record of them the agent's own past is a list of things
    it did with nothing anyone said back, which reads exactly like nobody ever
    answered — so it asks again, and again.
    """
    lines: List[str] = []
    for msg in (observation.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("from") or "someone")
        lines.append(f'tick {tick}: {sender} said to you: "{_clip(msg.get("text"))}"')
    return lines


def _said_line(tick: int, res: ActionResult) -> str:
    """One resolution as a journal line — quoting the agent's own words.

    ``speak_to -> message queued for Bob`` records that a message left, not
    what it said. An agent that cannot see what it asked cannot tell a reply
    from a non-reply, and has no way to know it is repeating itself.
    """
    args = res.args or {}
    text = str(args.get("text") or "").strip()
    if not text:
        return f"tick {tick}: {res.action} -> {res.message}"
    target = str(args.get("agent") or "").strip()
    who = f"to {target}" if target else "to everyone present"
    quote = _clip(text)
    if res.ok:
        return f'tick {tick}: you said {who}: "{quote}"'
    return f'tick {tick}: you tried to say {who}: "{quote}" -> {res.message}'


def _run_tick(env: Environment, scenario: Scenario, sim_run_id: str,
              tick: int, history: Dict[str, List[str]],
              workspace: Optional[str] = None,
              plan: Optional[Dict[str, List[str]]] = None) -> TickRecord:
    """One tick: observe (cheap), decide (concurrent), resolve (atomic)."""
    env.begin_tick(tick)
    record = TickRecord(sim_run_id=sim_run_id, tick=tick)
    plan = plan if plan is not None else {r.display_name(): [TRIGGER_SYNC]
                                          for r in scenario.roles}
    acting = [r for r in scenario.roles if r.display_name() in plan]
    record.idle = sorted(r.display_name() for r in scenario.roles
                         if r.display_name() not in plan)

    # 1. observe — cheap and pure, so just do it inline. Observing is also what
    # drains an agent's inbox, which is why only the agents that act observe:
    # mail nobody was woken for waits rather than being read into the void.
    observations = {r.display_name(): env.observe(r.display_name()) for r in acting}

    # Say who was woken before anybody has thought a word. A tick is as slow as
    # its slowest agent, and a page that shows nothing until the whole tick is
    # written cannot tell "three agents are working" from "the run is stuck" —
    # so each one gets its place in the transcript up front, with the mail it
    # was woken to read, and fills in as it goes.
    for role in acting:
        name = role.display_name()
        _publish(sim_run_id, {
            "type": "agent_start", "tick": tick, "agent": name,
            "triggers": list(plan.get(name) or []),
            "messages": [m for m in (observations[name].get("messages") or [])
                         if isinstance(m, dict)],
        })

    # 2. decide — the expensive part, and the only part worth parallelising.
    decisions = _run_decisions(env, scenario, sim_run_id, tick, history,
                               workspace, acting, plan, observations)

    # Stable order so the log reads the same way every time, whatever order the
    # thread pool happened to finish in.
    decisions.sort(key=lambda d: d.agent)
    record.decisions = decisions
    record.cost = round(sum(d.cost for d in decisions), 6)

    # A turn that produced nothing never read its mail. Give it back — and
    # leave its wake reasons standing — so the agent gets the same turn again
    # instead of losing the answer it was waiting for. Only the agents that
    # actually decided have their triggers cleared and their mail journalled.
    for d in decisions:
        observation = observations.get(d.agent) or {}
        if d.error:
            env.restore_inbox(d.agent, list(observation.get("messages") or []))
            continue
        env.clear_triggers(d.agent)
        history.setdefault(d.agent, []).extend(_heard_lines(tick, observation))

    # 3. resolve — one atomic pass, the environment's own tie-breaking rules.
    submissions = [
        {"agent": d.agent, "action": d.action["action"], "args": d.action["args"]}
        for d in decisions if d.action
    ]
    resolutions = env.resolve(submissions)
    for d in decisions:
        if d.error:
            resolutions.append(ActionResult(
                agent=d.agent, action="(none)", ok=False,
                message=f"no action taken: {d.error}",
            ))
    record.resolutions = resolutions

    env.end_tick()
    record.frame = env.frame()
    record.events = env.drain_events()

    for res in resolutions:
        history.setdefault(res.agent, []).append(_said_line(tick, res))
    return record


def _run_decisions(env: Environment, scenario: Scenario, sim_run_id: str,
                   tick: int, history: Dict[str, List[str]],
                   workspace: Optional[str], acting: List[Role],
                   plan: Dict[str, List[str]],
                   observations: Dict[str, Dict[str, Any]]) -> List[AgentDecision]:
    """Run this tick's decisions concurrently, bounded by three things.

    * ``max_concurrent`` — how many model calls are in flight at once;
    * ``stall_timeout``  — how long the tick may go *without a sign of life
      from anyone*. A model that is streaming is working; a model that has
      sent nothing while another agent's call is streaming is not stalled, it
      is queued behind it;
    * ``max_turn_seconds`` — the backstop for a model that dribbles a token a
      second forever, which no silence timeout can catch.

    **Only one decision is ever waiting on its own account.** Agents queue in
    two places before a single token is spent — the thread pool, when the tick
    has more agents than workers, and the provider itself, when the model
    server handles one request at a time. Timing each decision from submission
    made a single-threaded server look like N stalled models: the first agent
    answered and every agent behind it forfeited a turn it had not been given
    yet. So a decision is only a timeout candidate when it is actually being
    served — it is streaming, or it is the oldest one still waiting for its
    first token — and even then the silence clock is the whole tick's, reset
    by any agent's token or any agent's finished answer.

    The loop waits in short slices rather than blocking on each future, so a
    stop is noticed while the tick is still running. On a stop the pool is
    abandoned rather than joined: waiting for the calls we just cancelled is
    exactly the delay the user pressed the button to avoid.
    """
    if not acting:
        return []
    workers = max(1, min(int(scenario.max_concurrent), len(acting)))
    stall = max(1.0, float(scenario.stall_timeout or 60.0))
    hard_cap = max(0.0, float(scenario.max_turn_seconds or 0.0))

    pool = ThreadPoolExecutor(max_workers=workers)
    beats: Dict[Future, Beat] = {}
    futures: Dict[Future, Role] = {}
    for role in acting:
        name = role.display_name()
        beat = Beat()
        future = pool.submit(
            decide, role, observations[name], env, tick,
            _recent(history, name, role.memory_horizon), scenario, workspace,
            sim_run_id=sim_run_id, triggers=plan.get(name) or [], beat=beat,
        )
        futures[future] = role
        beats[future] = beat

    decisions: List[AgentDecision] = []
    pending = set(futures)
    # The last time anything in this tick moved: a token from any agent, or an
    # agent finishing. While this keeps advancing the provider is alive and
    # nobody is stalled, whatever their own call looks like from here.
    progress = time.monotonic()
    # Which decision is currently first in line for its first token, and since
    # when. A decision that inherits the front of the queue inherits a fresh
    # clock — it has been waiting, not working.
    head: Optional[Future] = None
    head_since = time.monotonic()
    try:
        while pending:
            if control.is_stopped(sim_run_id):
                for future in pending:
                    beats[future].cancel.set()
                break
            done, pending = futures_wait(pending, timeout=_WAIT_SLICE,
                                         return_when=FIRST_COMPLETED)
            for future in done:
                role = futures[future]
                try:
                    decision = future.result()
                except Exception as e:  # noqa: BLE001
                    decision = AgentDecision(
                        agent=role.display_name(),
                        observation=observations[role.display_name()],
                        triggers=list(plan.get(role.display_name()) or []),
                        error=f"{type(e).__name__}: {e}",
                    )
                decisions.append(decision)
                # Errors are published as well: an agent whose turn failed has
                # stopped working, and leaving its bubble spinning until the
                # tick lands says the opposite of what happened.
                _publish(sim_run_id, {"type": "decision", "tick": tick,
                                      **decision.to_dict()})
            now = time.monotonic()
            if done:
                progress = now
            # A token from anyone is a sign the provider is serving this tick.
            for future in pending:
                progress = max(progress, beats[future].last_sign_of_life())

            # Whoever is first in line for a first token: the oldest decision
            # that the pool has started and the provider has not answered yet.
            waiting = sorted(
                (f for f in pending
                 if beats[f].started_work() and not beats[f].has_streamed()),
                key=lambda f: (beats[f].work_started or 0.0,
                               futures[f].display_name()),
            )
            if waiting and waiting[0] is not head:
                head, head_since = waiting[0], now
            elif not waiting:
                head = None

            # A decision that has gone quiet forfeits the tick; the ones still
            # streaming keep their turn, however long they have been at it, and
            # the ones queued behind the provider are not charged for waiting.
            for future in sorted(pending, key=lambda f: futures[f].display_name()):
                beat = beats[future]
                if not beat.started_work():
                    continue                       # still queued in the pool
                if beat.has_streamed():
                    silent, waited = beat.silent_for() > stall, beat.streamed_for()
                    overrun = bool(hard_cap and waited > hard_cap)
                elif future is head:
                    # The tick's clock, not this decision's: if any other agent
                    # is streaming, the server is busy rather than hung.
                    waited = now - head_since
                    silent = (now - progress) > stall and waited > stall
                    overrun = bool(hard_cap and waited > hard_cap)
                else:
                    continue                       # queued behind the head
                if not (overrun or silent):
                    continue
                pending.discard(future)
                beat.cancel.set()
                future.cancel()
                if future is head:
                    head = None
                progress = now
                name = futures[future].display_name()
                abandoned = AgentDecision(
                    agent=name, observation=observations[name],
                    triggers=list(plan.get(name) or []),
                    error=(f"gave up after {waited:.0f}s of work "
                           f"(cap {hard_cap:.0f}s)") if overrun else
                          (f"model went silent for {stall:.0f}s "
                           f"(the turn had run {waited:.0f}s)"),
                )
                decisions.append(abandoned)
                _publish(sim_run_id, {"type": "decision", "tick": tick,
                                      **abandoned.to_dict()})
    finally:
        # The threads for abandoned decisions cannot be killed, only told to
        # stop at their next chunk; not joining them is the point.
        pool.shutdown(wait=False, cancel_futures=True)
    return decisions


def estimate_cost(scenario: Scenario, avg_inbound: int = 1200,
                  avg_outbound: int = 150) -> Dict[str, Any]:
    """Projected spend for a full run, before a single call is made."""
    calls = len(scenario.roles) * max(1, int(scenario.max_ticks))
    per_call = 0.0
    for role in scenario.roles:
        provider, model = resolve_model(role, scenario)
        per_call += _run_cost(provider, model, avg_inbound, avg_outbound)
    per_tick = round(per_call, 6)
    triggered = scenario.activation == TRIGGERED
    return {
        "agents": len(scenario.roles),
        "max_ticks": scenario.max_ticks,
        "activation": scenario.activation,
        "llm_calls": calls,
        "estimated_cost_per_tick": per_tick,
        "estimated_total_cost": round(per_tick * max(1, int(scenario.max_ticks)), 4),
        # `note` stays English for API consumers; `note_key` lets the UI
        # render the same sentence in the user's language.
        "note_key": (
            "estimateNoteTriggered" if triggered else "estimateNoteSynchronous"
        ),
        "note": (
            "An upper bound: in triggered mode only the agents something "
            "reached act, so a real run costs less — how much less depends on "
            "how talkative they are, and on how long they keep trying before "
            "they run out of ideas. Set a cost ceiling."
            if triggered else
            "N agents x T ticks is the floor, not the estimate — an agent that "
            "reasons at length costs more. Set a cost ceiling."
        ),
    }


__all__ = [
    "run_simulation", "stop_simulation", "trigger_agent", "estimate_cost",
    "decide", "resolve_model", "parse_decision", "build_system_prompt",
    "build_tick_prompt", "SimStopped", "Beat",
    "MAX_TICKS", "MAX_AGENTS",
]
