"""
Resuming a team run: the checkpoint a round loop leaves behind, and what a
process that reads it back does differently from one starting fresh.

teams.runner.run_team gained two doors: ``run=`` (a pre-created record —
what runtime/team_run.py and the in-process mint-your-own path both end up
giving it) and ``checkpoint=`` (what a resume picks the round loop back up
from). What matters here is the contract those doors keep: a checkpoint is
written once a round finishes, a resumed run rebuilds the board from the
durable messages rather than replaying anything, a member already on the
board for the round being resumed is not billed twice, and a stop written by
another process (the durable status, not the in-memory event) still ends a
subprocess-driven run between rounds.
"""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from common import entity_runs
from teams import store
import teams.runner as runner
from teams.models import Team, TeamMember, TeamMessage, TeamRun
from teams.runner import Turn, run_team


def _team(**kw):
    kw.setdefault("name", "Resume crew")
    kw.setdefault("mode", "parallel")
    kw.setdefault("synthesize", False)
    kw.setdefault("members", [
        TeamMember(agent_id="a", name="Sam", manifest="writes code"),
        TeamMember(agent_id="b", name="Rae", manifest="reviews it"),
    ])
    return store.save_team(Team(**kw))


def _running_record(team, **kw) -> TeamRun:
    """A TeamRun already ``running`` under some other process's pid — the
    shape teams.launcher / runtime.team_run hand to run_team(run=...)."""
    run = TeamRun(team_id=team.team_id, mode=team.mode, goal="ship the page",
                 workspace="ws", **kw)
    store.save_run(run)
    entity_runs.mark_running(run.team_run_id, pid=1, host="test-host")
    return run


def _stub(monkeypatch, calls, reply="still going"):
    def _fake(*, team, member, agent_id, speaker, prompt, **kwargs):
        calls.append({"speaker": speaker, "run_id": kwargs.get("run_id"),
                      "in_process": kwargs.get("in_process")})
        return Turn(speaker=speaker, text=reply, run_id=f"run-{len(calls)}", cost=0.01)
    monkeypatch.setattr(runner, "_run_member", _fake)


def _no_filesystem(monkeypatch):
    """Keep the round loop off the real workspace/task machinery — the same
    reason tests/test_teams.py's stub_members fixture stubs _prepare_context
    for the mint-your-own path; this is its equivalent for the run=... path."""
    monkeypatch.setattr(runner, "_resolve_run_workspace", lambda run: ("ws", "/tmp/ws"))
    monkeypatch.setattr(runner, "_finalize_task", lambda run: None)
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)


# ── Checkpointing ────────────────────────────────────────────────────────────

def test_a_checkpoint_is_written_after_every_finished_round(monkeypatch):
    team = _team(max_rounds=2)
    run = _running_record(team)
    _no_filesystem(monkeypatch)
    calls: list = []
    _stub(monkeypatch, calls, reply="still thinking")

    finished = run_team(team.team_id, "ship the page", run=run)

    assert finished.rounds_done == 2
    checkpoint = entity_runs.load_checkpoint(run.team_run_id)
    assert checkpoint["round"] == 2
    assert "updated_at" in checkpoint
    assert checkpoint["spend"] > 0


def test_the_checkpoint_carries_the_round_state_and_spend(monkeypatch):
    """Autonomous mode's handoff queue lives in ``state`` — a resume that
    forgets it would lose track of who was asked to do what."""
    team = _team(mode="autonomous", max_rounds=3, synthesize=False)
    run = _running_record(team)
    _no_filesystem(monkeypatch)
    calls: list = []
    _stub(monkeypatch, calls, reply="I did it myself; nothing to hand on.")

    run_team(team.team_id, "ship the page", run=run)
    checkpoint = entity_runs.load_checkpoint(run.team_run_id)
    # The entry member acted alone and asked nobody for anything — the queue
    # is empty and the run ends round 1, but the checkpoint still records it.
    assert checkpoint["round"] == 1
    assert checkpoint["state"] == {"queue": {}}


# ── Resuming a round that was already partway done ──────────────────────────

def test_a_resumed_run_skips_a_member_already_on_the_board_and_bills_only_the_rest(monkeypatch):
    team = _team(max_rounds=2)
    run = _running_record(team)

    # Pre-crash board: the goal, a full round 1, and a partial round 2 — Sam
    # already replied, Rae had not been asked yet when the process died.
    store.append_message(TeamMessage(team_run_id=run.team_run_id, round=0,
                                     sender="(request)", content="ship the page", kind="goal"))
    store.append_message(TeamMessage(team_run_id=run.team_run_id, round=1,
                                     sender="Sam", content="working on it", kind="message"))
    store.append_message(TeamMessage(team_run_id=run.team_run_id, round=1,
                                     sender="Rae", content="reviewing", kind="message"))
    store.append_message(TeamMessage(team_run_id=run.team_run_id, round=2,
                                     sender="Sam", content="pushed the fix", kind="message"))
    checkpoint = {"round": 1, "state": {}, "spend": 0.02, "final_answer": ""}
    entity_runs.save_checkpoint(run.team_run_id, checkpoint)

    _no_filesystem(monkeypatch)
    calls: list = []
    _stub(monkeypatch, calls, reply="looks fine")

    finished = run_team(team.team_id, "ship the page", run=run, checkpoint=checkpoint)

    # Only Rae was actually run — Sam's round-2 turn was already on the board.
    assert [c["speaker"] for c in calls] == ["Rae"]
    assert finished.rounds_done == 2
    assert finished.stop_reason == "max_rounds"
    assert finished.status == "completed"

    # Sam's pre-crash message was not reposted: still exactly one round-2
    # message from Sam, one from Rae.
    round2 = [m for m in store.list_messages(run.team_run_id) if m.round == 2]
    assert sorted(m.sender for m in round2) == ["Rae", "Sam"]


def test_a_resumed_handoff_round_still_reads_the_recipient_off_a_skipped_turn(monkeypatch):
    """A member already on the board for the resumed round is not re-run, but
    the round driver still has to see who it asked for something — otherwise
    a resumed autonomous round could wrongly conclude nothing was handed on."""
    team = _team(mode="autonomous", max_rounds=3, synthesize=False)
    run = _running_record(team)

    store.append_message(TeamMessage(team_run_id=run.team_run_id, round=0,
                                     sender="(request)", content="ship the page", kind="goal"))
    store.append_message(TeamMessage(team_run_id=run.team_run_id, round=1,
                                     sender="Sam", content="@Rae: please review this",
                                     kind="message", recipients=["Rae"]))
    checkpoint = {"round": 1, "state": {"queue": {"Rae": ["Sam asked: please review this"]}},
                 "spend": 0.0, "final_answer": ""}
    entity_runs.save_checkpoint(run.team_run_id, checkpoint)

    _no_filesystem(monkeypatch)
    calls: list = []
    _stub(monkeypatch, calls, reply="reviewed, looks fine")

    finished = run_team(team.team_id, "ship the page", run=run, checkpoint=checkpoint)

    # Sam is not asked again — round 1 is already on the board — only Rae,
    # who the checkpointed queue says was waiting on a turn.
    assert [c["speaker"] for c in calls] == ["Rae"]
    assert finished.rounds_done == 2


# ── Deterministic turn ids ───────────────────────────────────────────────────

def test_a_member_turns_run_id_is_deterministic_in_the_team_run_round_and_speaker():
    a = runner._turn_run_id("trun_1", 3, "Sam")
    b = runner._turn_run_id("trun_1", 3, "Sam")
    assert a == b
    assert a == str(uuid5(NAMESPACE_URL, "team:trun_1:3:Sam"))
    assert a != runner._turn_run_id("trun_1", 4, "Sam")
    assert a != runner._turn_run_id("trun_1", 3, "Rae")
    assert a != runner._turn_run_id("trun_2", 3, "Sam")


def test_a_members_turn_is_opened_under_its_deterministic_run_id(monkeypatch):
    team = _team(max_rounds=1)
    run = _running_record(team)
    _no_filesystem(monkeypatch)
    calls: list = []
    _stub(monkeypatch, calls, reply="done")

    run_team(team.team_id, "ship the page", run=run)

    expected = {runner._turn_run_id(run.team_run_id, 1, "Sam"),
               runner._turn_run_id(run.team_run_id, 1, "Rae")}
    assert {c["run_id"] for c in calls} == expected


def test_a_subprocess_run_marks_member_turns_not_in_process(monkeypatch):
    """run_team(run=...) is the subprocess door: the pid on each member's leaf
    run record belongs to the team subprocess alone, so a stop from the
    Messages page may signal it directly (managers.runs.lifecycle) instead of
    being refused the way a shared API-process pid must be."""
    team = _team(max_rounds=1)
    run = _running_record(team)
    _no_filesystem(monkeypatch)
    calls: list = []
    _stub(monkeypatch, calls, reply="done")

    run_team(team.team_id, "ship the page", run=run)
    assert calls and all(c["in_process"] is False for c in calls)


def test_the_in_process_door_marks_member_turns_in_process(monkeypatch):
    team = _team(max_rounds=1)
    monkeypatch.setattr(runner, "_prepare_context",
                        lambda *a, **k: ("ws", "/tmp/ws", "task-1", "sess-1"))
    monkeypatch.setattr(runner, "_finalize_task", lambda run: None)
    monkeypatch.setattr(runner, "_claim_task", lambda team, run: None)
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)
    calls: list = []
    _stub(monkeypatch, calls, reply="done")

    run_team(team.team_id, "ship the page")
    assert calls and all(c["in_process"] is True for c in calls)


# ── A durable stop ends a subprocess-driven run between rounds ──────────────

def test_a_durably_requested_stop_ends_a_subprocess_run_between_rounds(monkeypatch):
    team = _team(max_rounds=5)
    run = _running_record(team)
    _no_filesystem(monkeypatch)

    def _fake(*, team, member, agent_id, speaker, prompt, **kwargs):
        # Simulate a stop landing on the durable record from another process
        # (a Messages-page stop, or teams.launcher.stop_team_run on another
        # host) partway through round 1 — no in-memory event was ever set
        # here, only the row.
        store.request_stop(run.team_run_id)
        return Turn(speaker=speaker, text="still going")

    monkeypatch.setattr(runner, "_run_member", _fake)
    finished = run_team(team.team_id, "ship the page", run=run)

    assert finished.status == "stopped"
    assert finished.stop_reason == "stopped"
    # Caught at the next round's _check_between_rounds, which reads
    # store.stop_requested() — not just the in-memory event this process
    # never had set for it.
    assert finished.rounds_done == 1
