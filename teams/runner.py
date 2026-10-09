"""
The round loop: who acts, what they were told, and what they said back.

Three shapes of round, one board::

    centralized  the lead reads the board, assigns work, the assigned act
    autonomous   only members a colleague addressed act; the entry member takes
                 the incoming request and either does it or hands it on
    parallel     every member acts every round, no coordinator

The members are ordinary agents: they keep their own instructions, tools and
model, and gain a seat on the team on top of them (see :mod:`teams.prompts`).
That is what separates a team from a flow — nothing here builds a pipeline, and
no member's output is wired into another's input. They talk.

Termination differs by mode and is the part worth getting right, because a
group of agents left to itself will happily converse forever:

* centralized — the lead declares the goal met, or the round cap is reached;
* autonomous  — nothing is left waiting for anyone: the last member to act
  finished its part and handed nothing on;
* parallel    — every member has written ``TEAM_DONE``, or nobody said anything
  this round.

On top of that: round cap, wall clock, cost ceiling, the workspace budget and
an explicit stop. A stop is *not* a between-rounds check — see
:mod:`teams.control`: it interrupts the turns in flight, so pressing the button
ends the model calls the user is paying for rather than the round after them.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from concurrent.futures import (
    FIRST_COMPLETED, Future, ThreadPoolExecutor, wait as futures_wait,
)
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import NAMESPACE_URL, uuid4, uuid5

from teams import control, store
from teams.models import (
    BROADCAST, HANDOFF_MODES, MAX_MEMBERS, MAX_ROUNDS_CAP, MAX_WALL_SECONDS_CAP,
    Team, TeamMember, TeamMessage, TeamRun, utc_iso,
)
from teams.prompts import (
    board_block, entry_instruction, handoff_instruction, leader_turn_prompt,
    member_turn_prompt, parse_leader_plan, parse_member_reply, synthesis_prompt,
    team_system_prompt,
)

log = logging.getLogger(__name__)


class TeamStopped(Exception):
    """The run hit a limit or was asked to stop. ``reason`` is recorded on the run."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


@dataclass
class Turn:
    """One member's turn: what it said, what it cost, which run recorded it."""
    speaker: str = ""
    text: str = ""
    run_id: Optional[str] = None
    cost: float = 0.0
    tokens: int = 0
    error: str = ""
    stopped: bool = False
    #: Read back off the board by _resume_turn rather than produced by a call
    #: just now — _post_turns must never post this turn's message again.
    already_posted: bool = False


def _publish(team_run_id: str, event: Dict[str, Any]) -> None:
    """Push an event to the ``team:<id>`` channel. Never fatal."""
    try:
        from common.session_broker import broker
        broker.publish_threadsafe(f"team:{team_run_id}", event)
    except Exception:  # noqa: BLE001 - live publish is never fatal to the run
        log.debug("team event publish failed", exc_info=True)


def _turn_cost(provider: str, model: str, inbound: int, outbound: int) -> float:
    """Priced from token counts, before the member's run record exists.
    Delegates to managers.runs.groups.turn_cost, the one place that turns a
    (provider, model, tokens) triple into a catalog price."""
    from managers.runs.groups import turn_cost
    return turn_cost(provider, model, inbound, outbound)


# ── One agent's turn ─────────────────────────────────────────────────────────

from common.workspace_context import own_context  # noqa: E402 - the runs below set the workspace


@own_context
def _run_member(
    *, team: Team, member: Optional[TeamMember], agent_id: str, speaker: str,
    prompt: str, workspace: Optional[str], task_id: Optional[str],
    session_id: Optional[str], team_run_id: str, is_leader: bool = False,
    provider: Optional[str] = None, model: Optional[str] = None,
    run_id: Optional[str] = None, in_process: bool = True,
) -> Turn:
    """Build the agent with its team seat and run one turn. Never raises: a
    member that fails posts an error to the board and the team continues, the
    way a colleague who is out sick does not end the project."""
    from agents.agent_factory import create_agent, get_factory
    from agents.agent_invoke import invoke_agent
    from agents.callbacks import RunStopCallback
    from managers.run_manager import (
        close_run, close_run_from_result, open_run, run_log_path,
    )

    turn = Turn(speaker=speaker)
    if control.is_stopped(team_run_id):
        turn.stopped = True
        return turn
    # A pool thread starts with a fresh context, so the workspace the tools scope
    # to has to be set here rather than inherited from the run.
    try:
        from common.workspace_context import _workspace_ctx, workspace_name_from_path
        _workspace_ctx.set(workspace_name_from_path(workspace))
    except Exception:  # noqa: BLE001 - without the workspace scope the turn still runs on the default
        log.debug("setting turn workspace context failed", exc_info=True)
    try:
        # The team block is *appended* to the agent's own assembled prompt, not
        # substituted for it: the member keeps its expertise and gains a roster.
        base = get_factory().load_definition(agent_id).get("system_prompt", "")
        overrides: Dict[str, Any] = {
            "system_prompt": f"{base}\n\n---\n\n{team_system_prompt(team, member, is_leader=is_leader)}",
        }
        if provider:
            overrides["provider"] = provider
        if model:
            overrides["model"] = model
        agent = create_agent(agent_id, workspace=workspace, **overrides)
    except Exception as e:  # noqa: BLE001
        turn.error = f"{type(e).__name__}: {e}"
        return turn

    run_id = run_id or str(uuid4())
    log_path = run_log_path(run_id)
    try:
        # One instance per seat of this team run: the seat is the live copy,
        # each round it speaks is a run in that copy's journal.
        instance_id = None
        try:
            from instances import registry as instance_registry
            instance_id = instance_registry.ensure_instance(
                agent_id,
                instance_id=instance_registry.deterministic_id(team_run_id, speaker),
                kind="team_member",
                workspace=workspace,
                session_id=session_id,
                task_id=task_id,
                label=f"{team.name} · {speaker}",
                state="active",
                pid=os.getpid(),
                provider=agent.provider or None,
                model=agent.model or None,
            )["instance_id"]
        except Exception:  # noqa: BLE001 - the instance row is bookkeeping, the turn runs without it
            log.debug("opening instance for team turn failed", exc_info=True)

        open_run(
            run_id, agent_id, pid=os.getpid(), task_id=task_id,
            session_id=session_id, session_type="task", channel="team",
            log_file=str(log_path), workspace=workspace,
            title=f"{team.name}: {speaker}",
            team_id=team.team_id, team_member=speaker, link_to_session=True,
            parent_run_id=team_run_id,
            # A member turn shares whatever process is running it: this API
            # server's own pid when run_team minted its own record (chat, a
            # tool call, the old Teams-page thread), or a dedicated team
            # subprocess's pid when teams.launcher / runtime.team_run started
            # it (run_team's ``run=`` path). in_process=True is what keeps
            # managers.runs.lifecycle._stop_run_record from signalling a pid
            # the API shares with everything else it is doing; a subprocess's
            # pid belongs to this run alone, so a stop from the Messages page
            # may terminate it directly there, which ends the run exactly the
            # way the process dying any other way would (the watchdog reads
            # the stalled heartbeat).
            in_process=in_process,
            provider=agent.provider or "", model=agent.model or "",
            input=prompt, instance_id=instance_id,
        )
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"--- Team turn started at {utc_iso()} ---\n"
                    f"Team : {team.name}\nSeat : {speaker}\nAgent: {agent_id}\n\n"
                    f"=== PROMPT ===\n{prompt}\n\n=== EXECUTION ===\n")
    except Exception:  # noqa: BLE001 - run bookkeeping must never fail the turn
        log.debug("opening run record for team turn failed", exc_info=True)

    # Two guards, because a turn can be stopped from either side: the team page
    # (the event) or this member's own run record (the runs page, the API).
    control.track(team_run_id, run_id)
    try:
        inv = invoke_agent(
            agent, prompt, run_id=run_id,
            extra_callbacks=[control.TeamStopCallback(team_run_id),
                             RunStopCallback(run_id)],
        )
    finally:
        control.untrack(team_run_id, run_id)

    result = inv.result
    turn.run_id = run_id
    turn.text = str(getattr(result, "agent_output", "") or "").strip()
    if not getattr(result, "ok", False):
        turn.error = str(getattr(result, "error", "") or "agent run failed")
    turn.stopped = control.is_stopped(team_run_id)

    usage = (inv.process or {}).get("token_usage") or {}
    inbound, outbound = int(usage.get("inbound_tokens") or 0), int(usage.get("outbound_tokens") or 0)
    turn.tokens = inbound + outbound
    turn.cost = _turn_cost(agent.provider or "", agent.model or "", inbound, outbound)

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== OUTPUT ===\n{turn.text or turn.error}\n"
                    f"--- duration_ms={inv.duration_ms} ---\n")
        if turn.stopped and not getattr(result, "ok", False):
            # A turn cut off by the user is not a failed run, and recording it as
            # one would leave the runs page claiming the agent broke.
            close_run(run_id, status="stopped", exit_code=1,
                      error="stopped by user", process=inv.process)
        else:
            close_run_from_result(run_id, result, process=inv.process)
    except Exception:  # noqa: BLE001 - run bookkeeping must never fail the turn
        log.debug("closing run record for team turn failed", exc_info=True)
    return turn


def _turn_run_id(team_run_id: str, round_no: int, speaker: str) -> str:
    """A member turn's leaf run id, deterministic in the team run, the round
    and the speaker.

    A run started as a subprocess can die mid-round; a resume replays that
    round from the board it already has (see ``_already_spoken`` /
    ``_resume_turn`` below), and a member already skipped there never reaches
    this id at all. It is deterministic anyway, rather than a fresh
    ``uuid4()`` per attempt, because "the same call" should mean the same id
    whichever attempt makes it — the alternative is a random id that happens
    to only ever be used once, which is not a guarantee, just a coincidence.
    """
    return str(uuid5(NAMESPACE_URL, f"team:{team_run_id}:{round_no}:{speaker}"))


def _already_spoken(board: "_Board", round_no: int) -> set:
    """Members who already have their own reply on the board for this round —
    not the leader's instructions *about* them, which carry the same sender
    name but are not a member's own turn."""
    return {m.sender for m in board.messages
            if m.round == round_no and m.kind in ("message", "verdict", "error")}


def _resume_turn(board: "_Board", round_no: int, speaker: str) -> Optional["Turn"]:
    """A member's turn as it already happened this round, read back off the
    board rather than paid for twice.

    Only reachable right after a resume: a checkpoint is written once a round
    *finishes* (see run_team), so the round a resume replays can still hold
    some of its members' messages from before the process died — the crash
    landed between two turns, after the board write but before the round was
    done. Best-effort: the board keeps a message's posted content (with
    ``TEAM_DONE`` already stripped) and its cost, not the original completion,
    so a member found this way always reads as "still going" even when it had
    signalled done. That costs at most one extra round — never a wrong result
    — which is the price of not re-billing a call a crash already paid for.
    """
    for msg in board.messages:
        if msg.round != round_no or msg.sender != speaker:
            continue
        if msg.kind == "error":
            return Turn(speaker=speaker, run_id=msg.run_id, cost=msg.cost,
                       tokens=msg.tokens, error=msg.error or "not replayed on resume",
                       already_posted=True)
        if msg.kind in ("message", "verdict"):
            return Turn(speaker=speaker, text=msg.content, run_id=msg.run_id,
                       cost=msg.cost, tokens=msg.tokens, already_posted=True)
    return None


def _run_turns(
    jobs: List[Tuple[TeamMember, str]], *, team: Team, team_run_id: str,
    workspace: Optional[str], task_id: Optional[str], session_id: Optional[str],
    round_no: int, board: "_Board", member_in_process: bool = True,
) -> List[Turn]:
    """Run one round's member turns, bounded by ``max_concurrent``.

    Waits in short slices rather than blocking on each future in turn, so a stop
    is noticed while the round is still running rather than after it.

    The deadline is ``turn_timeout`` per wave of concurrent turns, and it is
    rolling: more members than ``max_concurrent`` means the later ones only
    start once a worker frees up, and a model server that handles one request
    at a time queues them however many workers we allow — either way a fixed
    round-wide deadline would cut a member off for someone else's slowness. So
    every completed turn is a sign the round is moving and buys the members
    still waiting a fresh budget. A member that overruns *that* forfeits the
    round rather than holding the whole team; the others' work is already paid
    for. On a stop the pool is abandoned rather than joined — waiting for the
    calls we just cancelled is exactly the delay the user pressed the button to
    avoid.

    A member already on the board for this round (see ``_already_spoken``) is
    read back rather than run again — the round a resume starts on can be one
    that was already partway done when the process died.
    """
    if not jobs or control.is_stopped(team_run_id):
        return []
    already = _already_spoken(board, round_no)
    reused = [t for t in (_resume_turn(board, round_no, member.display_name())
                          for member, _ in jobs if member.display_name() in already)
              if t is not None]
    jobs = [(member, prompt) for member, prompt in jobs
            if member.display_name() not in already]
    if not jobs:
        reused.sort(key=lambda t: t.speaker)
        return reused

    workers = max(1, min(int(team.max_concurrent or 1), len(jobs)))
    pool = ThreadPoolExecutor(max_workers=workers)
    futures: Dict[Future, TeamMember] = {
        pool.submit(
            _run_member, team=team, member=member, agent_id=member.agent_id,
            speaker=member.display_name(), prompt=prompt, workspace=workspace,
            task_id=task_id, session_id=session_id, team_run_id=team_run_id,
            provider=member.provider or team.default_provider,
            model=member.model or team.default_model,
            run_id=_turn_run_id(team_run_id, round_no, member.display_name()),
            in_process=member_in_process,
        ): member
        for member, prompt in jobs
    }

    turns: List[Turn] = list(reused)
    pending = set(futures)
    per_turn = max(1.0, float(team.turn_timeout or 300.0))

    def _budget(remaining: int) -> float:
        """How long the members still out have, counted in waves of workers."""
        return per_turn * max(1, -(-remaining // workers))

    deadline = time.monotonic() + _budget(len(jobs))
    try:
        while pending:
            if control.is_stopped(team_run_id):
                break
            done, pending = futures_wait(pending, timeout=0.5,
                                         return_when=FIRST_COMPLETED)
            for future in done:
                member = futures[future]
                try:
                    turns.append(future.result())
                except Exception as e:  # noqa: BLE001
                    turns.append(Turn(speaker=member.display_name(),
                                      error=f"{type(e).__name__}: {e}"))
            if done and pending:
                # Somebody answered, so the provider is working through the
                # round rather than hanging; whoever it serves next starts
                # their own clock instead of inheriting the wait.
                deadline = time.monotonic() + _budget(len(pending))
            if pending and time.monotonic() > deadline:
                for future in pending:
                    turns.append(Turn(
                        speaker=futures[future].display_name(),
                        error=f"turn timed out after {team.turn_timeout:.0f}s",
                    ))
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Stable order so the board reads the same way every time, whatever order
    # the pool happened to finish in.
    turns.sort(key=lambda t: t.speaker)
    return turns


# ── The run ──────────────────────────────────────────────────────────────────

#: A message a person sent to a running team (``_Board.take_steering``):
#: posted from ``user`` with its own kind; its ``run_id`` field carries the
#: steering message id, so a chat can match it to the bubble it drew.
STEER_SENDER = "user"
STEER_KIND = "steer"


class _Board:
    """The message bus: persists every entry, keeps it in memory for prompting,
    and streams it to whoever is watching."""

    def __init__(self, run: TeamRun, on_message: Optional[Callable[[TeamMessage], None]] = None):
        self.run = run
        self.messages: List[TeamMessage] = []
        self.on_message = on_message

    def post(
        self, *, sender: str, content: str, round_no: int, kind: str = "message",
        recipients: Optional[List[str]] = None, run_id: Optional[str] = None,
        cost: float = 0.0, tokens: int = 0, error: Optional[str] = None,
    ) -> TeamMessage:
        msg = TeamMessage(
            team_run_id=self.run.team_run_id, round=round_no, sender=sender,
            recipients=list(recipients or [BROADCAST]), kind=kind, content=content,
            run_id=run_id, cost=cost, tokens=tokens, error=error,
        )
        store.append_message(msg)
        self.messages.append(msg)
        _publish(self.run.team_run_id, {"type": "message", **msg.to_dict()})
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:  # noqa: BLE001 - a subscriber callback must not break the conversation
                log.debug("on_message callback failed", exc_info=True)
        return msg

    def view(self, viewer: str, *, see_all: bool = False) -> str:
        self.take_steering()
        return board_block(self.messages, viewer, see_all=see_all)

    def take_steering(self) -> None:
        """Post what a person sent to this team run while it worked
        (``POST /api/runs/{team_run_id}/steer``, common/steering.py) as a
        message from the user to everyone. Called whenever a member's prompt
        is built, so the next member to speak reads it."""
        try:
            from common import steering
            taken = steering.claim_pending(self.run.team_run_id, len(self.messages))
        except Exception:  # noqa: BLE001 - no steering table (an old database) is no messages
            log.debug("team steering claim failed for %s", self.run.team_run_id, exc_info=True)
            return
        for msg in taken:
            round_no = self.messages[-1].round if self.messages else 0
            self.post(sender=STEER_SENDER, content=str(msg.get("body") or ""),
                      round_no=round_no, kind=STEER_KIND, run_id=str(msg.get("msg_id") or "") or None)


def _validate_for_run(team: Team) -> None:
    """Every way of starting a team run needs the same guarantees: a roster
    under the member cap, teammates addressable by distinct names, and (for a
    centralized team) somebody to lead. Shared by run_team's own
    mint-a-record path and teams.launcher.start_team_run, so a launch fails
    the same way whichever door it came through.
    """
    if not team.members:
        raise ValueError("Team has no members — a team needs a roster")
    if len(team.members) > MAX_MEMBERS:
        raise ValueError(f"Team has {len(team.members)} members; the cap is {MAX_MEMBERS}")
    names = [m.display_name() for m in team.members]
    if len(set(names)) != len(names):
        raise ValueError("Two members share a display name — names address agents in this team")
    if team.mode == "centralized" and not team.leader_agent_id:
        raise ValueError("A centralized team needs a leader agent")


@own_context
def run_team(
    team_id: str,
    goal: str,
    *,
    workspace: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    on_message: Optional[Callable[[TeamMessage], None]] = None,
    run: Optional[TeamRun] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
) -> TeamRun:
    """Run a team against one goal until it finishes or hits a ceiling.

    Two ways in, both ending up in the same round loop below:

    * most callers (chat, the tools, a direct call) give only ``team_id`` and
      a goal. A record is minted here, marked ``running`` under *this
      thread's own pid* and given its own heartbeat, so a run started this
      way is exactly as visible to the watchdog and to a stop from another
      host as one that was launched as a subprocess — it only differs in
      whose process happens to be carrying it.
    * ``teams.launcher.start_team_run`` / ``resume_team_run`` and
      ``runtime/team_run.py`` give the pre-created ``run`` instead: the
      record, its task claim and (once the process starts) its
      ``mark_running`` already happened — the launcher's database half, then
      the shared envelope (runtime/entity_launch.py) — before this call.
      ``checkpoint`` is what a resume picks the round loop back up from.

    Synchronous and long-running either way: a caller that wants this off its
    own thread starts it on one (or lets the launcher spawn the subprocess)
    and follows the ``team:<team_run_id>`` channel or the board.
    """
    from common import entity_runs
    from runtime.entity_heartbeat import EntityHeartbeat

    # True only for the mint-your-own path: this thread's pid is the run's
    # process. Every member turn's leaf run record is tagged the same way
    # (see _run_member's in_process comment) — a run this API process carries
    # must never be signalled by a stop from the Messages page, but a run a
    # disposable team subprocess carries can be, because that pid is not
    # shared with anything else.
    member_in_process = run is None
    own_heartbeat: Optional[EntityHeartbeat] = None

    if run is None:
        team = store.get_team(team_id)
        if not team:
            raise ValueError(f"Team not found: {team_id}")
        _validate_for_run(team)

        goal = (goal or "").strip() or (team.description or "").strip()

        # The run id is minted before anything else because the task, the run
        # record and the stop event all key off it: a team that claims a task
        # has to be able to say which run holds it.
        run = TeamRun(team_id=team_id, mode=team.mode, goal=goal,
                      conversation_id=conversation_id)
        control.register(run.team_run_id)

        ws_name, ws_path, task_id, session_id = _prepare_context(
            team, workspace, task_id, session_id, conversation_id, goal,
        )
        run.workspace, run.task_id, run.session_id = ws_name, task_id, session_id

        # Publish the workspace on the context var the agent tools read, rather
        # than a process-wide env var that would race with concurrent requests.
        # Member turns run on a thread pool, which does not inherit context —
        # so each turn sets it again for itself (see _run_member).
        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ws_name)

        store.save_run(run)
        _claim_task(team, run)
        # No launcher stands between this call and the record, so this is
        # where the run becomes "running": under this thread's own pid, with
        # its own heartbeat thread, exactly what mark_running + EntityHeartbeat
        # give a launched subprocess (runtime/team_run.py).
        entity_runs.mark_running(run.team_run_id, pid=os.getpid(),
                                 host=socket.gethostname(), execution_mode="local")
        run.status = "running"
        own_heartbeat = EntityHeartbeat(
            run.team_run_id, on_stop=lambda: control.request_stop(run.team_run_id))
        own_heartbeat.start()
    else:
        team = store.get_team(run.team_id)
        if not team:
            raise ValueError(f"Team not found: {run.team_id}")
        _validate_for_run(team)
        team_id = run.team_id
        goal = (run.goal or goal or "").strip() or (team.description or "").strip()
        run.goal = goal
        control.register(run.team_run_id)
        ws_name, ws_path = _resolve_run_workspace(run)
        task_id, session_id = run.task_id, run.session_id

        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ws_name)

    _publish(run.team_run_id, {"type": "team_start", **run.to_dict(),
                               "team": team.to_dict()})

    board = _Board(run, on_message)
    state: Dict[str, Any] = {}
    spend = 0.0
    final_answer = ""
    resume_round = 0
    if checkpoint:
        # The board is already durable (every post went through store.
        # append_message as it happened), so a resume rebuilds it by reading
        # the messages back rather than replaying anything.
        resume_round = int(checkpoint.get("round") or 0)
        state = dict(checkpoint.get("state") or {})
        spend = float(checkpoint.get("spend") or 0.0)
        final_answer = str(checkpoint.get("final_answer") or "")
        board.messages.extend(store.list_messages(run.team_run_id))
    else:
        board.post(sender="(request)", content=goal, round_no=0, kind="goal")

    max_rounds = max(1, min(int(team.max_rounds or 1), MAX_ROUNDS_CAP))
    wall_cap = min(float(team.max_wall_seconds or MAX_WALL_SECONDS_CAP), MAX_WALL_SECONDS_CAP)
    started = time.monotonic()
    driver = _driver_for(team)

    try:
        try:
            for round_no in range(resume_round + 1, max_rounds + 1):
                _check_between_rounds(run.team_run_id, started, wall_cap, spend, team, ws_name)
                _publish(run.team_run_id, {"type": "round_start", "round": round_no})

                round_cost, finished, answer = driver(
                    team=team, run=run, board=board, goal=goal, round_no=round_no,
                    workspace=ws_path, task_id=task_id, session_id=session_id,
                    state=state, member_in_process=member_in_process,
                )
                spend = round(spend + round_cost, 6)
                run.rounds_done = round_no
                run.total_cost = spend
                store.update_progress(
                    run.team_run_id, rounds_done=run.rounds_done,
                    total_cost=run.total_cost,
                )
                # Written once the round has fully finished — see _run_turns and
                # _resume_turn for what a round that died before reaching here
                # means for the resume that follows it.
                entity_runs.save_checkpoint(run.team_run_id, {
                    "round": round_no, "state": state, "spend": spend,
                    "final_answer": final_answer,
                    "board_seq": board.messages[-1].seq if board.messages else 0,
                    "updated_at": utc_iso(),
                })

                # A stop that landed mid-round already cut the turns short; ending
                # here keeps a half-finished round from being read as a result.
                if control.is_stopped(run.team_run_id):
                    raise TeamStopped("stopped", "stopped by request")
                if finished:
                    final_answer = answer
                    raise TeamStopped("goal_met", "the team reported the goal met")

            raise TeamStopped("max_rounds", f"reached the cap of {max_rounds} rounds")

        except TeamStopped as e:
            run.stop_reason = e.reason
            run.status = "stopped" if e.reason == "stopped" else "completed"
            log.info("team run %s finished: %s (%s)", run.team_run_id, e.reason, e.detail)
        except Exception as e:  # noqa: BLE001
            log.exception("team run failed")
            run.status = "failed"
            run.stop_reason = "error"
            run.error = f"{type(e).__name__}: {e}"
    finally:
        # Whatever ends the rounds, the beat must not outlive the run: a
        # lingering thread would keep a finished run looking alive.
        if own_heartbeat is not None:
            own_heartbeat.stop()

    # One answer, not a transcript — unless the team was stopped mid-flight, in
    # which case another paid call to summarise an interrupted run is not what
    # the user asked for when they pressed stop.
    if team.synthesize and run.status != "stopped" and not final_answer:
        final_answer, synth_cost = _synthesize(
            team=team, run=run, board=board, goal=goal, workspace=ws_path,
            task_id=task_id, session_id=session_id, member_in_process=member_in_process,
        )
        spend = round(spend + synth_cost, 6)
        # Only a synthesised answer is posted: a lead that finished the run wrote
        # its answer as a verdict, which is already the last thing on the board.
        if final_answer:
            board.post(
                sender=team.leader_display_name() if team.leader_agent_id else "(team)",
                content=final_answer, round_no=run.rounds_done, kind="result",
            )

    run.result = final_answer or _last_substantive(board)
    run.total_cost = spend
    run.finished_at = utc_iso()
    store.save_run(run)
    _finalize_task(run)
    control.release(run.team_run_id)
    _publish(run.team_run_id, {"type": "team_done", **run.to_dict()})
    return run


# ── Modes ────────────────────────────────────────────────────────────────────

def _driver_for(team: Team) -> Callable[..., Tuple[float, bool, str]]:
    """Which shape of round this team runs. An unknown mode falls back to the
    parallel driver — every member acts, which is wrong but never silent."""
    if team.mode == "centralized":
        return _run_centralized
    if team.mode in HANDOFF_MODES:
        return _run_handoff
    return _run_parallel


def _run_centralized(
    *, team: Team, run: TeamRun, board: _Board, goal: str, round_no: int,
    workspace: str, task_id: str, session_id: str, state: Dict[str, Any],
    member_in_process: bool = True,
) -> Tuple[float, bool, str]:
    """One round with a coordinator: the lead assigns, the assigned members act."""
    leader_name = team.leader_display_name()
    lead_turn = _run_member(
        team=team, member=team.member_by_name(leader_name), agent_id=team.leader_agent_id,
        speaker=leader_name,
        prompt=leader_turn_prompt(
            team=team, goal=goal, board=board.view(leader_name, see_all=True), round_no=round_no,
        ),
        workspace=workspace, task_id=task_id, session_id=session_id,
        team_run_id=run.team_run_id, is_leader=True,
        provider=team.default_provider, model=team.default_model,
        in_process=member_in_process,
    )
    cost = lead_turn.cost
    if lead_turn.stopped:
        return cost, False, ""
    if lead_turn.error and not lead_turn.text:
        board.post(sender=leader_name, content=f"(the team lead could not run: {lead_turn.error})",
                   round_no=round_no, kind="error", run_id=lead_turn.run_id,
                   cost=lead_turn.cost, tokens=lead_turn.tokens, error=lead_turn.error)
        return cost, False, ""

    plan = parse_leader_plan(lead_turn.text, team)
    if plan.done:
        board.post(sender=leader_name, content=plan.final or lead_turn.text,
                   round_no=round_no, kind="verdict", run_id=lead_turn.run_id,
                   cost=lead_turn.cost, tokens=lead_turn.tokens)
        return cost, True, plan.final

    if not plan.assignments:
        board.post(sender=leader_name,
                   content="(the team lead assigned nobody this round)",
                   round_no=round_no, kind="error", run_id=lead_turn.run_id,
                   cost=lead_turn.cost, tokens=lead_turn.tokens, error=plan.error)
        return cost, False, ""

    jobs: List[Tuple[TeamMember, str]] = []
    for name, instruction in plan.assignments:
        member = team.member_by_name(name)
        if not member:
            continue
        board.post(sender=leader_name, content=instruction, round_no=round_no,
                   kind="instruction", recipients=[member.display_name()],
                   run_id=lead_turn.run_id)
        jobs.append((member, member_turn_prompt(
            team=team, member=member, goal=goal,
            board=board.view(member.display_name()), round_no=round_no,
            instruction=instruction,
        )))

    turns = _run_turns(
        jobs, team=team, team_run_id=run.team_run_id, workspace=workspace,
        task_id=task_id, session_id=session_id, round_no=round_no, board=board,
        member_in_process=member_in_process,
    )
    cost += _post_turns(team, board, turns, round_no)[0]
    return cost, False, ""


def _run_handoff(
    *, team: Team, run: TeamRun, board: _Board, goal: str, round_no: int,
    workspace: str, task_id: str, session_id: str, state: Dict[str, Any],
    member_in_process: bool = True,
) -> Tuple[float, bool, str]:
    """One round without a coordinator, driven by requests between members.

    Nobody acts on a hunch here. The request lands on the entry member, which
    either does the work or names the colleague whose it is; from then on a
    member gets a turn only in the round after someone addressed it. That is the
    difference from :func:`_run_parallel`, where every member acts every round
    whether or not anything is waiting on them — four agents, four calls, most
    of them with nothing to add.

    The run ends when nothing is left waiting for anyone: the last member to act
    finished its part and handed nothing on.
    """
    # queue: member display name → the instructions handed to it this round.
    queue: Dict[str, List[str]] = state.get("queue") or {}
    if round_no == 1 and not queue:
        entry = team.entry_member()
        if entry is None:
            return 0.0, True, ""
        queue = {entry.display_name(): [entry_instruction()]}
        board.post(
            sender="(request)", content=f"Handed to {entry.display_name()} to take or route.",
            round_no=round_no, kind="system", recipients=[entry.display_name()],
        )
    if not queue:
        return 0.0, True, ""

    jobs: List[Tuple[TeamMember, str]] = []
    for name, instructions in queue.items():
        member = team.member_by_name(name)
        if not member:
            continue
        jobs.append((member, member_turn_prompt(
            team=team, member=member, goal=goal,
            board=board.view(member.display_name()), round_no=round_no,
            # Two colleagues who asked the same member for something in one
            # round are two assignments, not one: both are handed over whole.
            instruction="\n\n---\n\n".join(instructions),
        )))

    turns = _run_turns(
        jobs, team=team, team_run_id=run.team_run_id, workspace=workspace,
        task_id=task_id, session_id=session_id, round_no=round_no, board=board,
        member_in_process=member_in_process,
    )
    cost, handoffs = _post_turns(team, board, turns, round_no, handoff=True)
    state["queue"] = handoffs
    # Nothing was handed on: the work has come to rest, and another round would
    # only be a team asking itself whether it is finished.
    return cost, not handoffs, ""


def _run_parallel(
    *, team: Team, run: TeamRun, board: _Board, goal: str, round_no: int,
    workspace: str, task_id: str, session_id: str, state: Dict[str, Any],
    member_in_process: bool = True,
) -> Tuple[float, bool, str]:
    """One round in which every member acts against the board.

    Peers acting simultaneously do not stop by themselves, so the round ends the
    team only when every member has declared itself finished, or when a whole
    round produced nothing at all.
    """
    jobs = [
        (m, member_turn_prompt(
            team=team, member=m, goal=goal, board=board.view(m.display_name()),
            round_no=round_no,
        ))
        for m in team.members
    ]
    turns = _run_turns(
        jobs, team=team, team_run_id=run.team_run_id, workspace=workspace,
        task_id=task_id, session_id=session_id, round_no=round_no, board=board,
        member_in_process=member_in_process,
    )
    cost, _ = _post_turns(team, board, turns, round_no)

    spoke = [t for t in turns if t.text.strip()]
    if not spoke:
        # A whole round in which nobody said anything is not a team thinking; it
        # is a team that has stopped working. Ending here beats paying for the
        # same silence again.
        return cost, True, ""
    everyone_done = all(
        parse_member_reply(t.text, team, allow_direct=team.allow_direct_messages)[2]
        for t in spoke
    )
    return cost, everyone_done, ""


def _post_turns(
    team: Team, board: _Board, turns: List[Turn], round_no: int,
    *, handoff: bool = False,
) -> Tuple[float, Dict[str, List[str]]]:
    """Put a round's replies on the board.

    Returns what the round cost and, in handoff mode, who was asked for what —
    the queue for the next round, keyed by the teammate addressed.
    """
    cost = 0.0
    handoffs: Dict[str, List[str]] = {}
    for turn in turns:
        cost += turn.cost
        if turn.already_posted:
            # Already on the board from before a resume (see _resume_turn) —
            # never reposted, but a handoff round still needs to know who it
            # asked for what, or a resumed round could wrongly conclude
            # nothing was handed on and end early.
            if handoff and turn.text:
                _, recipients, _ = parse_member_reply(
                    turn.text, team, allow_direct=team.allow_direct_messages or handoff,
                )
                for name in recipients:
                    if name == BROADCAST or name == turn.speaker:
                        continue
                    handoffs.setdefault(name, []).append(
                        handoff_instruction(turn.speaker, turn.text)
                    )
            continue
        if turn.stopped and not turn.text:
            continue  # the user stopped this turn; it is not a failure to report
        if turn.error and not turn.text:
            board.post(sender=turn.speaker, content=f"(no contribution: {turn.error})",
                       round_no=round_no, kind="error", run_id=turn.run_id,
                       cost=turn.cost, tokens=turn.tokens, error=turn.error)
            continue
        content, recipients, done = parse_member_reply(
            # Handoffs are how work moves in autonomous mode, so addressing is
            # always read there — the private-messages switch governs whether a
            # member may talk to one colleague out of the team's earshot, not
            # whether it may ask one for something.
            turn.text, team, allow_direct=team.allow_direct_messages or handoff,
        )
        board.post(
            sender=turn.speaker, content=content or "(nothing to add)",
            round_no=round_no, kind="message", recipients=recipients,
            run_id=turn.run_id, cost=turn.cost, tokens=turn.tokens,
        )
        if handoff:
            # A member that both asks for something and declares itself finished
            # has broken the protocol; the request is honoured anyway. Dropping
            # it would silently lose work a colleague was asked for, and the
            # round cap already bounds the cost of one extra exchange.
            for name in recipients:
                if name == BROADCAST or name == turn.speaker:
                    continue
                handoffs.setdefault(name, []).append(
                    handoff_instruction(turn.speaker, content)
                )
    return round(cost, 6), handoffs


def _synthesize(
    *, team: Team, run: TeamRun, board: _Board, goal: str, workspace: str,
    task_id: str, session_id: str, member_in_process: bool = True,
) -> Tuple[str, float]:
    """Closing pass: one member writes the team's answer from the board."""
    speaker_id = team.leader_agent_id or (team.members[0].agent_id if team.members else None)
    if not speaker_id:
        return "", 0.0
    member = next((m for m in team.members if m.agent_id == speaker_id), None)
    speaker = member.display_name() if member else team.leader_display_name()
    turn = _run_member(
        team=team, member=member, agent_id=speaker_id, speaker=speaker,
        prompt=synthesis_prompt(goal=goal, board=board.view(speaker, see_all=True)),
        workspace=workspace, task_id=task_id, session_id=session_id,
        team_run_id=run.team_run_id, is_leader=bool(team.leader_agent_id),
        provider=(member.provider if member else None) or team.default_provider,
        model=(member.model if member else None) or team.default_model,
        in_process=member_in_process,
    )
    return turn.text, turn.cost


def _last_substantive(board: _Board) -> str:
    """Fallback result: the last thing anyone actually said."""
    for msg in reversed(board.messages):
        if msg.kind in ("message", "result", "verdict") and msg.content.strip():
            return msg.content
    return ""


# ── Ceilings ─────────────────────────────────────────────────────────────────

def _check_between_rounds(
    team_run_id: str, started: float, wall_cap: float, spend: float,
    team: Team, workspace: Optional[str],
) -> None:
    if control.is_stopped(team_run_id) or store.stop_requested(team_run_id):
        raise TeamStopped("stopped", "stopped by request")
    elapsed = time.monotonic() - started
    if elapsed > wall_cap:
        raise TeamStopped("wall_clock", f"wall-clock cap reached ({elapsed:.0f}s of {wall_cap:.0f}s)")
    if team.cost_ceiling and spend >= float(team.cost_ceiling):
        raise TeamStopped(
            "cost_ceiling", f"cost ceiling reached (${spend:.4f} of ${float(team.cost_ceiling):.2f})"
        )
    try:
        from common.budget import check_budget
        check_budget(workspace)
    except TeamStopped:
        raise
    except Exception as e:  # noqa: BLE001
        raise TeamStopped("cost_ceiling", f"budget: {e}")


# ── Task / session plumbing ──────────────────────────────────────────────────

def _prepare_context(
    team: Team, workspace: Optional[str], task_id: Optional[str],
    session_id: Optional[str], conversation_id: Optional[str], goal: str,
) -> Tuple[Optional[str], str, Optional[str], Optional[str]]:
    """Resolve the workspace and give the run one session to live in.

    A run started from the Teams page or attached to a task is task-shaped: it
    gets (or reuses) a task, and every member's turn lands in that task's
    session. A run started from **chat** is not — a conversation turn is not a
    unit of work to track, and minting a task per message would bury the task
    board — so it uses the conversation's own chat session and no task at all.
    """
    from tasks import service as _ts
    from common.session_service import (
        get_or_create_chat_session, get_or_create_task_session,
    )
    from workspace import as_param_dict, create_workspace_folder, resolve_task_workspace

    ws_name = workspace or team.workspace
    ws_name = (create_workspace_folder(ws_name).name if ws_name
               else create_workspace_folder().name)

    task = None
    if task_id:
        task = _ts.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
    elif not conversation_id:
        # Created as running, not as todo: the team is already starting, and a
        # task that sits in todo — even for the moment before the run claims it —
        # is a task a polling orchestrator can pick up and route somewhere else.
        task = _ts.create_task(
            title=f"Team: {team.name}", description=goal, workspace=ws_name,
            status=_ts.TaskStatus.in_progress,
        )
        task_id = str(task.id)

    if task is not None:
        _, ws_path = resolve_task_workspace(task, as_param_dict({"workspace": ws_name}))
    else:
        ws_path = create_workspace_folder(ws_name)

    if not session_id:
        if task is not None:
            session_id = get_or_create_task_session(
                title=getattr(task, "title", None) or f"Team: {team.name}",
                workspace=ws_name, is_flow=True, task_id=str(task_id),
            )
            try:
                _ts.update_task(task_id, session_id=session_id)
            except Exception:  # noqa: BLE001 - linking the session is cosmetic, the run goes on
                log.debug("linking task to team session failed", exc_info=True)
        else:
            try:
                session_id = get_or_create_chat_session(
                    conversation_id=conversation_id,
                    title=f"Team: {team.name}", workspace=ws_name,
                    agent_id=f"team:{team.team_id}",
                )
            except Exception:  # noqa: BLE001 - no chat session just means the run is not shown in a chat
                log.debug("creating chat session for team run failed", exc_info=True)
                session_id = None
    return ws_name, str(ws_path), task_id, session_id


def _resolve_run_workspace(run: TeamRun) -> Tuple[str, str]:
    """The workspace name and the directory a run's turns operate in,
    recomputed rather than stored: only :func:`_prepare_context` resolves the
    project subfolder, once, when a run is first created, and a ``TeamRun``
    keeps just the workspace *name*. A subprocess that loads a pre-created
    record (runtime/team_run.py) or a resume
    (teams.launcher.resume_team_run) needs the same path back, so this finds
    exactly what ``_prepare_context`` would have found for the same task.
    """
    from tasks import service as _ts
    from workspace import as_param_dict, create_workspace_folder, resolve_task_workspace

    ws_name = run.workspace
    if run.task_id:
        task = _ts.get_task(run.task_id)
        if task is not None:
            _, ws_path = resolve_task_workspace(task, as_param_dict({"workspace": ws_name}))
            return ws_name or "", str(ws_path)
    folder = create_workspace_folder(ws_name) if ws_name else create_workspace_folder()
    return folder.name, str(folder)


def _claim_task(team: Team, run: TeamRun) -> None:
    """Hand the task to the team, the way assigning an agent hands it to an agent.

    Assignment and *execution* are one act here: the user picked this team and
    pressed run, so there is nobody left to approve the choice. That is why the
    team's own run record is opened first and its id given to ``assign_agent``:
    a task holding an assignment with no run reads as ``pending_approval``, and
    the board would sit in ``todo`` behind an approval prompt for a decision the
    user already made.
    """
    if not run.task_id:
        return
    from tasks import service as _ts
    from tasks.models import TaskStatus
    from managers import run_manager

    assignee = f"Team: {team.name or team.team_id}"
    try:
        run_manager.open_run(
            run.team_run_id, f"team:{team.team_id}", task_id=str(run.task_id),
            session_id=run.session_id, session_type="task", channel="team",
            workspace=run.workspace, title=assignee, pid=os.getpid(),
            team_id=team.team_id, is_team=True, link_to_session=True,
            input=run.goal,
        )
    except Exception:  # noqa: BLE001 - run bookkeeping must never fail the team run
        log.debug("opening team run record failed", exc_info=True)
    try:
        _ts.assign_executor(
            run.task_id, {"kind": "team", "id": team.team_id},
            {"team_id": team.team_id, "mode": team.mode, "workspace": run.workspace,
             "team_run_id": run.team_run_id, "goal": run.goal},
            run_id=run.team_run_id,
        )
        _ts.update_task(run.task_id, status=TaskStatus.in_progress)
    except Exception:
        log.exception("could not hand task %s to team %s", run.task_id, team.team_id)


def _finalize_task(run: TeamRun) -> None:
    """Record the team's answer on the task and close it out."""
    from managers import run_manager

    ok = run.status == "completed"
    try:
        run_manager.close_run(
            run.team_run_id,
            status="completed" if ok else ("stopped" if run.status == "stopped" else "failed"),
            exit_code=0 if ok else 1,
            error=run.error,
            output=run.result or "",
        )
    except Exception:  # noqa: BLE001 - closing the record must not stop the task update below
        log.debug("closing team run record failed", exc_info=True)
    if not run.task_id:
        return
    try:
        from tasks.context import persist_task_result
        if run.result:
            persist_task_result(run.task_id, run.team_run_id, run.result, agent_id="team")
    except Exception:  # noqa: BLE001 - persisting the result is best-effort, the run is already finished
        log.debug("persisting team result to task failed", exc_info=True)
    if run.status == "stopped":
        # Stopped is a decision, not a failure: the task keeps its history and
        # is left free to be picked up again rather than blocked with a reason.
        try:
            from tasks import service as _ts
            _ts.clear_agent(run.task_id)
            _ts.stop_task(run.task_id)
        except Exception:  # noqa: BLE001 - a stopped task that cannot be released must not raise out of finalize
            log.debug("releasing stopped team task failed", exc_info=True)
        return
    try:
        run_manager.finalize_flow_task(run.task_id, "completed" if ok else "failed",
                                       0 if ok else 1, error=run.error)
    except Exception:  # noqa: BLE001 - finalizing the task is best-effort, the run is already finished
        log.debug("finalizing team task failed", exc_info=True)


def stop_run(team_run_id: str) -> bool:
    """Stop a team run now.

    Delegates to :func:`teams.launcher.stop_team_run`, which marks the
    durable status (so a page that reloads, or another process, sees it) and
    sets the in-memory event (so the turns in flight stop calling the model
    instead of finishing the round the user cancelled). Kept here, under its
    original name, because the tools (tools/entity_runs.py) and the chat
    pipeline already call ``teams.runner.stop_run``.
    """
    from teams.launcher import stop_team_run
    return stop_team_run(team_run_id)


def estimate_cost(team: Team) -> Dict[str, Any]:
    """Upper-bound call count for a full run, before a single call is made."""
    members = len(team.members)
    rounds = max(1, min(int(team.max_rounds or 1), MAX_ROUNDS_CAP))
    per_round = members + (1 if team.mode == "centralized" else 0)
    notes = {
        "centralized": (
            "Upper bound: every member acting every round, plus the lead's "
            "decision and the closing synthesis. In practice a centralized team "
            "costs less, because the lead assigns only who is needed."
        ),
        "autonomous": (
            "Upper bound: it assumes every member is handed work every round. "
            "A handoff team usually costs far less — only the members a "
            "colleague addressed act at all, and often that is one per round."
        ),
        "parallel": (
            "This one is close to the real figure: in parallel mode every "
            "member acts every round, whether or not it has anything to add."
        ),
    }
    return {
        "members": members,
        "mode": team.mode,
        "max_rounds": rounds,
        "llm_calls_upper_bound": per_round * rounds + (1 if team.synthesize else 0),
        # `note` stays English for API consumers; `note_key` lets the UI
        # render the same sentence in the user's language.
        "note_key": (
            f"estimateNote{team.mode.capitalize()}"
            if team.mode in notes else "estimateNoteParallel"
        ),
        "note": notes.get(team.mode, notes["parallel"]),
    }


__all__ = ["run_team", "stop_run", "estimate_cost", "TeamStopped", "Turn"]
