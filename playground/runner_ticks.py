"""Tick loop helpers: stop signal, role setup, activation planning, journal lines and delivery."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import fields as dataclass_fields
from typing import Any, Dict, List, Optional


from playground import control, store
from playground.environments import create_environment
from playground.environments.base import Environment
from playground.models import (
    AGENTS, TRIGGERED, TRIGGER_CONTINUE,
    TRIGGER_HEARTBEAT, TRIGGER_OPENING, TRIGGER_SYNC, ActionResult,
    AgentDecision, Role, Scenario, SimRun, TickRecord, utc_iso,
)

log = logging.getLogger(__name__)


MAX_AGENTS = 24

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


def roles_from_team(scenario: Scenario) -> List[Role]:
    """The cast a scenario gets from its team, when it names one.

    ``Scenario.team_id`` lets an existing team play a world without anybody
    retyping its roster: each member becomes a role with the member's agent,
    name, role and goal (the member's own goal, else its manifest, which is
    what it committed to doing for that team). The team's leader opens the
    scene in triggered mode. Display names are made unique with a suffix,
    because names address agents in world and two members may share an
    agent. An unknown team, or a scenario with no team, gives ``[]``.
    """
    if not getattr(scenario, "team_id", None):
        return []
    try:
        from teams.store import get_team
        team = get_team(scenario.team_id)
    except Exception:  # noqa: BLE001 - a missing teams table reads as no team
        log.exception("could not load team %s", scenario.team_id)
        return []
    if team is None:
        return []
    roles: List[Role] = []
    seen: Dict[str, int] = {}
    for member in team.members:
        base = member.display_name() or member.agent_id or "member"
        seen[base] = seen.get(base, 0) + 1
        name = base if seen[base] == 1 else f"{base} {seen[base]}"
        roles.append(Role(
            agent_id=member.agent_id, name=name, role=member.role,
            goal=member.goal or member.manifest,
            provider=member.provider, model=member.model,
            starts=bool(team.leader_agent_id and member.agent_id == team.leader_agent_id),
        ))
    return roles


def _fill_roles_from_team(scenario: Scenario) -> None:
    """Give a team backed scenario its cast, in place, when it has none."""
    if not scenario.roles and getattr(scenario, "team_id", None):
        scenario.roles = roles_from_team(scenario)


def validate_scenario_for_run(scenario: Scenario) -> None:
    """Every check a scenario must pass before a run exists for it.

    A scenario with a ``team_id`` and no roles of its own gets its cast from
    the team here (see :func:`roles_from_team`), in place: the launcher
    freezes the scenario into the run's config right after this call, so the
    run carries the roster it started with even if the team changes later.

    Shared by ``playground.launcher.start_scenario_run``, which checks before
    it ever writes a run record, and this module's own head below — the tools'
    direct call (``tools/entity_runs.py``, not this run's launcher) and the
    test suite still go straight through ``run_simulation`` with no launcher
    in front of it. Both fail the same way for the same scenario.
    """
    _fill_roles_from_team(scenario)
    if not scenario.roles:
        raise ValueError("Scenario has no roles — a society needs participants")
    if len(scenario.roles) > MAX_AGENTS:
        raise ValueError(f"Scenario has {len(scenario.roles)} roles; the cap is {MAX_AGENTS}")
    names = [r.display_name() for r in scenario.roles]
    if len(set(names)) != len(names):
        raise ValueError("Two roles share a display name — names address agents in-world")
    if create_environment(scenario.environment, scenario.env_params, seed=scenario.seed) is None:
        raise ValueError(f"Unknown environment: {scenario.environment}")
    if scenario.mode == AGENTS:
        # Agents mode gives a role real tools, however small the allowlist.
        # That is only safe in a container: local execution runs those tools
        # against the host with no isolation at all, so it is refused here,
        # before a run record even exists, not discovered mid tick.
        from runtime.entity_launch import execution_mode_for
        if execution_mode_for(scenario.workspace) != "docker":
            raise ValueError(
                "Agents mode scenarios must run in docker. Set this "
                "workspace's execution mode to docker before running this "
                "scenario, or switch it back to personas mode."
            )


_DECISION_FIELDS = {f.name for f in dataclass_fields(AgentDecision)}
_RESULT_FIELDS = {f.name for f in dataclass_fields(ActionResult)}


def _tick_record_from_row(row: Dict[str, Any]) -> TickRecord:
    """Rebuild a :class:`TickRecord` from ``store.get_tick``'s stored shape.

    Used only when a resumed run finds a tick it already wrote to
    ``sim_ticks`` (see ``run_simulation``): the stored outcome is replayed
    into history and ``carry`` without asking the model again for a turn that
    already happened.
    """
    return TickRecord(
        sim_run_id=str(row.get("sim_run_id") or ""),
        tick=int(row.get("tick") or 0),
        decisions=[AgentDecision(**{k: v for k, v in d.items() if k in _DECISION_FIELDS})
                   for d in (row.get("decisions") or []) if isinstance(d, dict)],
        resolutions=[ActionResult(**{k: v for k, v in r.items() if k in _RESULT_FIELDS})
                     for r in (row.get("resolutions") or []) if isinstance(r, dict)],
        frame=dict(row.get("frame") or {}),
        events=list(row.get("events") or []),
        idle=list(row.get("idle") or []),
        cost=float(row.get("cost") or 0.0),
        ts=str(row.get("ts") or utc_iso()),
    )


def _sync_env_views(env: Environment, run: SimRun,
                    view_ids: Optional[Dict[str, str]] = None,
                    last_specs: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Publish the environment's views for this run (never raises).

    See ``playground.lab_views.sync_env_views``: a key seen for the first
    time creates a view owned by this scenario run, a key seen again updates
    it in place. Most environments declare no views and this costs one call.
    """
    try:
        from playground.lab_views import sync_env_views
        return sync_env_views(env, run, view_ids, last_specs)
    except Exception:  # noqa: BLE001 - views are a by-product, never a reason to fail a run
        log.exception("sim %s: publishing environment views failed", run.sim_run_id)
        return view_ids if view_ids is not None else {}


def _task_result_text(run: SimRun) -> str:
    """A short, human-readable summary of a finished run, for the task's own
    result text. Never the whole tick log: the tick log is already the
    artifact of record, reachable from the run itself."""
    bits = [f"Scenario run finished: {run.stop_reason or run.status}."]
    if run.ticks_done:
        bits.append(f"{run.ticks_done} tick(s) completed.")
    if run.total_cost:
        bits.append(f"Total cost: ${run.total_cost:.4f}.")
    if run.scores:
        bits.append("Scores: " + json.dumps(run.scores, ensure_ascii=False, default=str))
    return " ".join(bits)


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

    Drained from both queues, every tick: ``control``'s in-memory one (a poke
    that arrived while this process itself was running the sim) and
    ``store``'s durable one (a poke that arrived through the database — the
    only door open to a scenario running in another process). A run started
    before either queue existed for it reads back an empty list from each, so
    this is always safe to call.
    """
    for item in control.drain_triggers(sim_run_id) + store.drain_triggers(sim_run_id):
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
