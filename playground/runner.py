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

import logging
import os
import socket
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait as futures_wait
from typing import Any, Callable, Dict, List, Optional, Tuple


from playground import control, store
from playground.environments import create_environment
from playground.environments.base import Environment
from playground.models import (
    AGENTS, TRIGGERED, TRIGGER_SYNC, ActionResult,
    AgentDecision, Role, Scenario, SimRun, TickRecord, optional_seconds, utc_iso,
)

from playground.runner_decision import (  # noqa: F401
    Beat, ToolCallLimitError, _BeatTouchCallback, _Cancelled, _ToolCallLimitGuard,
    _accepts_config, _call_model, _close_decision_run, _enable_stream_usage,
    _estimated, _open_decision_run, _text_of, _usage_of, decision_run_id,
)
from playground.runner_prompts import (  # noqa: F401
    build_system_prompt, build_tick_prompt, parse_decision, partial_decision,
)
from playground.runner_ticks import (  # noqa: F401
    MAX_AGENTS, SimStopped, _STUCK_REPEATS, _activation_plan, _check_between_ticks,
    _continuations, _deliver_external, _fill_roles_from_team, _heard_lines, _recent,
    _said_line, _sync_env_views, _task_result_text, _tick_record_from_row,
    roles_from_team, validate_scenario_for_run,
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

# How long the loop will sit in one wait before looking at the world again.
# Short enough that a stop, a wall-clock cap or an arriving trigger is noticed
# promptly; long enough that an idle sandbox is not a spin loop.
_WAIT_SLICE = 0.5

#: Which end states count as the user (or a ceiling) cutting the run short, and
#: which count as the simulation having run its course.
_STOPPED_REASONS = ("stopped", "cost_ceiling", "wall_clock", "budget")





# ── Model resolution ──────────────────────────────────────────────────────────

def _agent_model(agent_id: str) -> Tuple[str, str]:
    """The (provider, model) an agent definition carries, if it names one."""
    if not agent_id:
        return "", ""
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:  # noqa: BLE001 - an unknown agent just has no model
        log.debug("agent model lookup failed", exc_info=True)
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
    except Exception:  # noqa: BLE001 - no workspace default means no model name, the run proceeds
        log.debug("workspace model lookup failed", exc_info=True)
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
    except Exception:  # noqa: BLE001 - a missing price must not fail the run, cost shows as zero
        log.debug("run cost lookup failed", exc_info=True)
        return 0.0



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
    system_prompt = build_system_prompt(role, env, scenario.activation,
                                        documents=scenario.documents,
                                        workspace=workspace)
    tick_prompt = build_tick_prompt(observation, tick, history, decision.triggers)
    combined_prompt = f"{system_prompt}\n\n---\n\n{tick_prompt}"

    run_id = _open_decision_run(
        role=role, scenario=scenario, sim_run_id=sim_run_id, tick=tick,
        workspace=workspace, provider=provider, model=model,
        prompt=combined_prompt,
    )
    decision.run_id = run_id
    if run_id:
        control.track(sim_run_id, run_id)

    if scenario.mode == AGENTS:
        # Agents mode: the role is played by the real agent behind
        # role.agent_id rather than a bare model. See _decide_with_agent.
        _decide_with_agent(
            role=role, scenario=scenario, env=env, prompt=combined_prompt,
            workspace=workspace, beat=beat, sim_run_id=sim_run_id,
            run_id=run_id, decision=decision, started=started,
            provider=provider, model=model,
        )
        return decision

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
            except Exception:  # noqa: BLE001 - the stop hook is optional, the turn still runs without it
                log.debug("run stop callback unavailable", exc_info=True)
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



def _decide_with_agent(*, role: Role, scenario: Scenario, env: Environment,
                       prompt: str, workspace: Optional[str], beat: Beat,
                       sim_run_id: str, run_id: str, decision: AgentDecision,
                       started: float, provider: str, model: str) -> None:
    """Agents mode: play this role with the real agent behind
    ``role.agent_id`` instead of a bare model. Mutates ``decision`` in place
    and closes its run record; never raises.

    The agent is built through ``agents.agent_factory.create_agent`` exactly
    as any agent build is, capability guard included, with its tool list
    overridden to the environment's ``TOOL_ALLOWLIST`` — empty by default, so
    a role gets no hub tools at all unless the environment declares some. The
    combined system and tick prompt (the same text a bare model would see) is
    handed to the agent as its instruction; the agent's own persona
    (instructions.md) stays intact underneath it. Its final answer still has
    to parse as a decision (``parse_decision``), the same contract personas
    mode holds.
    """
    if control.is_stopped(sim_run_id) or beat.cancel.is_set():
        decision.error = "stopped"
        decision.duration_ms = int((time.monotonic() - started) * 1000)
        _close_decision_run(run_id, sim_run_id, decision, status="stopped")
        return

    allowlist = list(getattr(env, "TOOL_ALLOWLIST", ()) or ())
    overrides: Dict[str, Any] = {"tools": allowlist}
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["model"] = model

    try:
        from agents.agent_factory import create_agent
        agent = create_agent(role.agent_id, workspace, **overrides)
    except Exception as e:  # noqa: BLE001
        decision.error = f"could not build agent {role.agent_id!r}: {type(e).__name__}: {e}"
        decision.duration_ms = int((time.monotonic() - started) * 1000)
        _close_decision_run(run_id, sim_run_id, decision, status="failed")
        return

    limit = max(1, int(scenario.max_tool_calls_per_tick or 8))
    call_guard = _ToolCallLimitGuard(limit)
    callbacks: List[Any] = [_BeatTouchCallback(beat), call_guard]
    if sim_run_id:
        callbacks.append(control.SimStopCallback(sim_run_id))
    if run_id:
        try:
            from agents.callbacks import RunStopCallback
            callbacks.append(RunStopCallback(run_id))
        except Exception:  # noqa: BLE001 - the stop hook is optional, the turn still runs without it
            log.debug("run stop callback unavailable", exc_info=True)

    from agents.agent_invoke import invoke_agent
    invocation = invoke_agent(agent, prompt, extra_callbacks=callbacks,
                              run_id=run_id or None)
    result = invocation.result

    decision.duration_ms = int((time.monotonic() - started) * 1000)
    usage = (invocation.process or {}).get("token_usage") or {}
    decision.inbound_tokens = int(usage.get("inbound_tokens") or 0)
    decision.outbound_tokens = int(usage.get("outbound_tokens") or 0)
    decision.cost = _run_cost(
        provider, model, decision.inbound_tokens, decision.outbound_tokens
    )

    output = str(getattr(result, "agent_output", "") or "")
    decision.raw_output = output

    if not getattr(result, "ok", False):
        if call_guard.tripped:
            decision.error = (
                f"exceeded {limit} tool call(s) this tick and was cut off"
            )
        elif control.is_stopped(sim_run_id):
            decision.error = "stopped"
        else:
            decision.error = str(getattr(result, "error", "") or "agent run failed")
        _close_decision_run(
            run_id, sim_run_id, decision,
            status="stopped" if decision.error == "stopped" else "failed",
        )
        return

    parsed = parse_decision(output)
    if "error" in parsed:
        decision.error = parsed["error"]
    else:
        decision.reasoning = parsed["reasoning"]
        decision.action = {"action": parsed["action"], "args": parsed["args"]}

    _close_decision_run(run_id, sim_run_id, decision,
                        status="failed" if decision.error else "completed")



# ── The loop ──────────────────────────────────────────────────────────────────

def _publish(sim_run_id: str, event: Dict[str, Any]) -> None:
    """Stream an event to the ``sim:<id>`` channel. Never fatal."""
    try:
        from common.session_broker import broker
        broker.publish_threadsafe(f"sim:{sim_run_id}", event)
    except Exception:  # noqa: BLE001 - streaming to the live view is best effort and never fatal
        log.debug("sim event publish failed", exc_info=True)


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
    """Turn a pending run into a running one — what its first sign of life
    means, whether that is a tick or a world that went straight to waiting.

    The row is only promoted while it still says ``pending``, so a stop that
    landed during the first tick survives the tick that finished after it.
    """
    if run.status != "pending":
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
    if run is None or run.status not in ("pending", "running", "stopping"):
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

    Pushed two ways. ``store.push_trigger`` is durable and reaches a run
    executing in *any* process — a launched scenario runs in its own
    subprocess (runtime/scenario_run.py) — and is what the return value
    reports. ``control.push_trigger`` additionally wakes an idle loop sitting
    in *this* process without waiting for its next poll of the durable queue
    (see ``_wait_out_idle``); it is a no-op, harmlessly, when the run is not
    registered here.
    """
    run = store.get_sim_run(sim_run_id)
    if run is None or run.status not in ("pending", "running", "stopping"):
        return False
    delivered = store.push_trigger(sim_run_id, agent, text, sender)
    control.push_trigger(sim_run_id, agent, text, sender)
    return delivered








def run_simulation(
    scenario_id: str,
    *,
    workspace: Optional[str] = None,
    on_start: Optional[Callable[[SimRun], None]] = None,
    on_tick: Optional[Callable[[TickRecord], None]] = None,
    run: Optional[SimRun] = None,
    checkpoint: Optional[dict] = None,
) -> SimRun:
    """Run a scenario to completion (or to whichever limit it hits first).

    Two callers, two shapes of the same loop:

    * **No ``run``** — the path every tool and most of the test suite still
      uses: a fresh run is minted and started here, on whatever thread called
      this function. Nothing upstream recorded a process for it, so this path
      also stamps one itself (pid, host) and beats its own heartbeat for as
      long as the loop runs — otherwise the watchdog (which now judges every
      kind of run by its heartbeat, not by guessing) would eventually reap a
      sim that is still very much going.
    * **``run`` given** — the launched path (``playground.launcher``,
      ``runtime/scenario_run.py``): the run record, its process and its own
      heartbeat thread already exist before this function is ever called.
      ``checkpoint`` (when given) is what a resume restores from. The
      scenario itself is read from ``run.config`` — the copy frozen when the
      run was launched — rather than the live scenario, so editing the
      scenario while this run is going does not change what it is running.
    """
    own_process = run is None
    if run is not None:
        scenario = Scenario.from_dict(run.config) if run.config else store.get_scenario(scenario_id)
    else:
        scenario = store.get_scenario(scenario_id)
    if not scenario:
        raise ValueError(f"Scenario not found: {scenario_id}")
    validate_scenario_for_run(scenario)

    env = create_environment(scenario.environment, scenario.env_params, seed=scenario.seed)
    if env is None:
        raise ValueError(f"Unknown environment: {scenario.environment}")

    names = [r.display_name() for r in scenario.roles]
    # The key -> view id mapping for the views this world publishes
    # (playground.lab_views), carried by the checkpoint so a resume updates
    # the views it already made instead of minting new ones.
    env_views: Dict[str, str] = dict(((checkpoint or {}).get("env_views")) or {})
    env_view_specs: Dict[str, str] = {}
    # Names *and* the roles they were cast in: an authored world places
    # characters by role and decides by role what each may do, and the role
    # string is a scenario's, not the environment's.
    env.register_cast([
        {"name": r.display_name(), "role": r.role, "agent_id": r.agent_id}
        for r in scenario.roles
    ])
    if checkpoint and checkpoint.get("env"):
        env.restore(checkpoint["env"])

    ws = workspace or scenario.workspace
    if run is None:
        run = SimRun(
            scenario_id=scenario_id, workspace=ws, environment=scenario.environment,
            activation=scenario.activation,
            # Freeze the scenario here: everything past this line reads from
            # the live row, which the user is free to edit while the sim runs
            # and after it finishes.
            config=scenario.to_dict(),
            # A scenario built for one task runs against it by default, the
            # same way the launcher sets it (playground.launcher
            # .start_scenario_run) — this is the door every direct caller
            # (a tool, a test) still goes through with no launcher in front.
            task_id=scenario.task_id,
        )

    heartbeat = None
    if own_process:
        run.pid = os.getpid()
        run.host = socket.gethostname()
        from runtime.entity_heartbeat import EntityHeartbeat
        heartbeat = EntityHeartbeat(
            run.sim_run_id, on_stop=lambda: control.request_stop(run.sim_run_id),
        )

    control.register(run.sim_run_id)
    store.save_sim_run(run)
    if heartbeat is not None:
        # Registered before the beat starts: its ``on_stop`` reads
        # ``playground.control``, which only knows about this run from the
        # ``register`` call just above.
        heartbeat.start()
    _publish(run.sim_run_id, {"type": "sim_start", **run.to_dict(),
                              "scenario": scenario.to_dict()})
    # The row exists, so the caller can be handed the run before a single model
    # has been called — the first tick is minutes away, and until it lands the
    # only honest thing to show is that this run is starting.
    if on_start:
        try:
            on_start(run)
        except Exception:  # noqa: BLE001 - a failing start hook must not abort the simulation
            log.debug("on_start hook failed", exc_info=True)

    max_ticks = max(1, min(int(scenario.max_ticks), MAX_TICKS))
    wall_cap = optional_seconds(scenario.max_wall_seconds)
    started = time.monotonic()
    # A resume picks every one of these up from the checkpoint instead of
    # starting cold; an empty (or absent) checkpoint leaves them exactly as a
    # fresh run always had them.
    cp = checkpoint or {}
    spend = float(cp.get("spend") or 0.0)
    # Per-agent rolling summary of its own past actions — the memory_horizon
    # knob. Without it, tick 200's prompt carries 199 ticks of transcript.
    history: Dict[str, List[str]] = {n: list(v) for n, v in (cp.get("history") or {}).items()}
    for n in names:
        history.setdefault(n, [])
    # The last tick this run completed — 0 for a fresh run. The loop below
    # always works on ``tick + 1``, so a resume's first tick is exactly the
    # one after the checkpoint's.
    tick = int(cp.get("tick") or 0)
    # Triggered mode only: who the last tick left mid-action, and what each
    # agent has been repeating, so a character that is out of ideas stops
    # being handed turns. Both are rebuilt every tick from the record.
    carry: Dict[str, List[str]] = dict(cp.get("carry") or {})
    streaks: Dict[str, List[Any]] = {k: list(v) for k, v in (cp.get("streaks") or {}).items()}

    from common import entity_runs

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
            # A resume can land on a tick this run already wrote to
            # ``sim_ticks`` before it died — the narrow window between
            # ``store.save_tick`` and the checkpoint write a few lines below,
            # which are two separate writes. Redoing it would call the model
            # again for a turn that is already the artifact of record;
            # replaying its stored outcome into history and carry costs
            # nothing and repeats nothing.
            stored = store.get_tick(run.sim_run_id, tick)
            if stored is not None:
                record = _tick_record_from_row(stored)
                for d in record.decisions:
                    if not d.error:
                        history.setdefault(d.agent, []).extend(_heard_lines(tick, d.observation))
                for res in record.resolutions:
                    history.setdefault(res.agent, []).append(_said_line(tick, res))
            else:
                record = _run_tick(env, scenario, run.sim_run_id, tick, history, ws, plan)
                store.save_tick(record)
            carry = _continuations(env, scenario, record, streaks)
            _sync_env_views(env, run, env_views, env_view_specs)
            spend += record.cost
            run.ticks_done = tick
            run.total_cost = round(spend, 6)
            _promote_to_running(run)
            # What a resume starts from. Written after every tick — the same
            # iteration that just wrote (or found) the tick itself — so the
            # two stay in lockstep except across the narrow crash window the
            # comment above already accounts for.
            entity_runs.save_checkpoint(run.sim_run_id, {
                "tick": tick, "env": env.snapshot(), "history": history,
                "carry": carry, "streaks": streaks, "spend": spend,
                "env_views": env_views, "updated_at": utc_iso(),
            })
            # The run's own counters ride with the tick: the page's meter reads
            # them, and without them it is stale until the next poll — which,
            # on a world that ticks faster than the poll, is never.
            _publish(run.sim_run_id, {"type": "tick", **record.to_dict(),
                                      "ticks_done": run.ticks_done,
                                      "total_cost": run.total_cost})
            if on_tick:
                try:
                    on_tick(record)
                except Exception:  # noqa: BLE001 - a failing tick hook must not abort the simulation
                    log.debug("on_tick hook failed", exc_info=True)

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
                # A world that knows *why* it ended (the lab: hypotheses
                # decided, or budget exhausted) says so in its frame, and that
                # is the more useful stop reason than "terminal".
                reason = ""
                try:
                    reason = str((env.frame() or {}).get("stop_reason") or "").strip()
                except Exception:  # noqa: BLE001 - a frame bug must not hide the ending
                    reason = ""
                raise SimStopped(reason or "terminal",
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
    finally:
        if heartbeat is not None:
            heartbeat.stop()

    run.scores = env.score()
    run.final_state = env.state()
    _sync_env_views(env, run, env_views)
    if env_views:
        run.final_state = {**run.final_state, "views": dict(env_views)}
    run.finished_at = utc_iso()
    store.save_sim_run(run)
    _finalize_task(run)
    control.release(run.sim_run_id)
    _publish(run.sim_run_id, {"type": "done", **run.to_dict()})
    return run




def _finalize_task(run: SimRun) -> None:
    """Advance the task this run worked on, when it has one.

    Shared by every caller of :func:`run_simulation` (the launched subprocess,
    through ``runtime/scenario_run.py``, and any in process caller), the same
    way ``loops.runner.run_loop`` and ``teams.runner.run_team`` finalize their
    own task on their own runner rather than in the subprocess entrypoint.
    Best effort throughout: a task that cannot be advanced must never turn a
    finished simulation into a reported failure.
    """
    if not run.task_id:
        return
    try:
        from tasks.context import persist_task_result
        persist_task_result(run.task_id, run.sim_run_id, _task_result_text(run),
                            agent_id="scenario")
    except Exception:  # noqa: BLE001 - best effort, see docstring
        log.debug("task result persist failed", exc_info=True)
    try:
        from managers.runs.task_finalize import finalize_task
        from tasks.models import Executor
        ok = run.status == "completed"
        finalize_task(
            run.task_id, "completed" if ok else "failed", 0 if ok else 1,
            error=run.error, run_id=run.sim_run_id,
            executor=Executor(kind="scenario", id=run.scenario_id),
        )
    except Exception:  # noqa: BLE001 - best effort, see docstring
        log.debug("task finalize failed", exc_info=True)






#: How often an idle wait polls the durable trigger queue. Coarser than
#: ``_WAIT_SLICE``: the in-memory queue wakes this loop the instant something
#: local arrives (``control.wait_for_trigger``'s event), so this poll only
#: exists to catch a trigger pushed from *another* process, and a grace period
#: can run to minutes — checking the database twice a second for that whole
#: stretch would be a needless hammer on it.
_DURABLE_POLL_SECONDS = 3.0


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
    last_poll = 0.0
    while time.monotonic() < deadline:
        if control.is_stopped(sim_run_id) or store.stop_requested(sim_run_id):
            raise SimStopped("stopped", "stopped by request")
        if wall_cap is not None and time.monotonic() - started > wall_cap:
            raise SimStopped("wall_clock", "wall-clock cap reached while idle")
        control.wait_for_trigger(sim_run_id, min(_WAIT_SLICE, deadline - time.monotonic()))
        if control.pending_trigger_count(sim_run_id):
            return True
        now = time.monotonic()
        if now - last_poll >= _DURABLE_POLL_SECONDS:
            last_poll = now
            # A durable trigger pushed from another process never sets the
            # in-memory arrival event above, so this is the only way this loop
            # ever learns about one.
            if store.has_pending_triggers(sim_run_id):
                return True
    return False




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
    """Projected spend for a full run, before a single call is made.

    A team backed scenario with no roles of its own is estimated with the
    cast it would get from its team (on a copy: estimating changes nothing).
    """
    if not scenario.roles and getattr(scenario, "team_id", None):
        scenario = Scenario.from_dict({**scenario.to_dict(), "roles": [
            r.to_dict() for r in roles_from_team(scenario)]})
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
    "build_tick_prompt", "validate_scenario_for_run", "decision_run_id",
    "roles_from_team",
    "SimStopped", "Beat", "ToolCallLimitError",
    "MAX_TICKS", "MAX_AGENTS",
]
