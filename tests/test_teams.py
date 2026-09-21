"""
Teams: the roster every member carries, how they address each other, and how a
run of them terminates.

The agents are stubbed. What is worth testing about a team is not that an agent
can be called, but that each one is told who its colleagues are, that a message
aimed at one teammate does not land on everyone's desk, that work moves between
peers only when one of them asks, and that a group with no coordinator stops.
"""
import pytest

from teams import control, store
from teams.models import BROADCAST, DONE_TOKEN, Team, TeamMember, TeamMessage, TeamRun
from teams.prompts import (
    board_block, parse_leader_plan,
    parse_member_reply, roster_block, team_system_prompt,
)
from teams.runner import Turn, run_team, stop_run


def _team(**kw):
    kw.setdefault("name", "Launch crew")
    kw.setdefault("charter", "Ship the landing page.")
    kw.setdefault("members", [
        TeamMember(agent_id="swe", name="Sam", role="engineer",
                   manifest="Writes and fixes the page code."),
        TeamMember(agent_id="rev", name="Rae", role="reviewer",
                   manifest="Reviews diffs before they ship."),
    ])
    return Team(**kw)


# ── What a member is told ─────────────────────────────────────────────────────

def test_every_member_carries_the_whole_roster_with_manifests():
    team = _team()
    prompt = team_system_prompt(team, team.members[0])
    assert "Ship the landing page." in prompt          # the charter
    assert "Sam" in prompt and "Rae" in prompt          # the roster
    assert "Reviews diffs before they ship." in prompt  # the colleague's manifest
    assert "← this is you" in prompt                    # which seat is theirs


def test_the_lead_is_named_in_the_roster_of_a_centralized_team():
    team = _team(mode="centralized", leader_agent_id="orch", leader_name="Lead")
    assert "**Lead** — team lead" in roster_block(team)


def test_the_lead_is_told_it_delegates_rather_than_works():
    team = _team(mode="centralized", leader_agent_id="orch", leader_name="Lead")
    prompt = team_system_prompt(team, None, is_leader=True)
    assert "You do not do the work yourself" in prompt
    assert DONE_TOKEN not in prompt   # the done token is a member's signal, not the lead's


def test_a_team_without_direct_messages_says_so():
    team = _team(allow_direct_messages=False)
    prompt = team_system_prompt(team, team.members[0])
    assert "there are no private messages" in prompt


# ── How members address each other ───────────────────────────────────────────

def test_a_leading_mention_addresses_one_teammate():
    team = _team()
    content, recipients, done = parse_member_reply("@Rae: take a look at the diff", team)
    assert recipients == ["Rae"] and not done
    # The reply is posted whole — splitting it apart loses the reasoning.
    assert content == "@Rae: take a look at the diff"


def test_an_unaddressed_reply_is_a_broadcast():
    _, recipients, _ = parse_member_reply("I pushed the header.", _team())
    assert recipients == [BROADCAST]


def test_a_mention_of_nobody_on_the_team_is_just_prose():
    _, recipients, _ = parse_member_reply("@nobody: hello", _team())
    assert recipients == [BROADCAST]


def test_an_agent_id_addresses_the_member_that_carries_it():
    """Models mix up display names and agent ids constantly."""
    _, recipients, _ = parse_member_reply("@swe: your turn", _team())
    assert recipients == ["Sam"]


def test_direct_addressing_is_ignored_when_the_team_forbids_it():
    _, recipients, _ = parse_member_reply("@Rae: psst", _team(), allow_direct=False)
    assert recipients == [BROADCAST]


def test_the_done_token_is_read_and_stripped_from_the_message():
    content, _, done = parse_member_reply(f"Nothing left on my side.\n{DONE_TOKEN}", _team())
    assert done and DONE_TOKEN not in content


# ── The board ────────────────────────────────────────────────────────────────

def _board():
    return [
        TeamMessage(sender="Sam", recipients=["Rae"], content="a private note", round=1),
        TeamMessage(sender="Rae", recipients=[BROADCAST], content="everyone sees this", round=1),
    ]


def test_a_directed_message_reaches_its_recipient_and_its_sender_only():
    msgs = _board()
    assert "a private note" in board_block(msgs, "Rae")
    assert "a private note" in board_block(msgs, "Sam")
    assert "a private note" not in board_block(msgs, "Kim")
    assert "everyone sees this" in board_block(msgs, "Kim")


def test_the_lead_reads_everything():
    """A coordinator that cannot see the work it assigned cannot coordinate."""
    assert "a private note" in board_block(_board(), "Lead", see_all=True)


def test_the_board_is_bounded_so_a_long_run_cannot_grow_without_limit():
    msgs = [TeamMessage(sender="Sam", content="x" * 500, round=i) for i in range(200)]
    rendered = board_block(msgs, "Sam", limit=10, char_budget=2000)
    assert len(rendered) < 4000
    assert "earlier messages omitted" in rendered


# ── The lead's decision ──────────────────────────────────────────────────────

def test_a_json_plan_is_read_into_assignments():
    plan = parse_leader_plan(
        '{"assignments": [{"agent": "Sam", "instruction": "build the header"}], '
        '"done": false, "final": ""}', _team())
    assert plan.assignments == [("Sam", "build the header")] and not plan.done


def test_a_fenced_plan_still_parses():
    plan = parse_leader_plan(
        '```json\n{"assignments": [], "done": true, "final": "shipped"}\n```', _team())
    assert plan.done and plan.final == "shipped"


def test_an_assignment_to_someone_not_on_the_team_is_dropped():
    plan = parse_leader_plan(
        '{"assignments": [{"agent": "Ghost", "instruction": "do it"}]}', _team())
    assert plan.assignments == []


def test_a_lead_answering_in_prose_still_moves_the_team():
    """A stalled team is a worse failure than a slightly over-broad assignment."""
    plan = parse_leader_plan("Sam should build the header now.", _team())
    assert plan.assignments == [("Sam", "Sam should build the header now.")]
    assert plan.error


def test_a_lead_naming_nobody_falls_back_to_the_whole_team():
    plan = parse_leader_plan("Let's get moving on this.", _team())
    assert [name for name, _ in plan.assignments] == ["Sam", "Rae"]


# ── Running a team ───────────────────────────────────────────────────────────

@pytest.fixture
def stub_members(monkeypatch):
    """Replace every agent call with a scripted reply; record who was asked what."""
    import teams.runner as runner

    monkeypatch.setattr(runner, "_prepare_context",
                        lambda *a, **k: ("ws", "/tmp/ws", "task-1", "sess-1"))
    monkeypatch.setattr(runner, "_finalize_task", lambda run: None)
    monkeypatch.setattr(runner, "_claim_task", lambda team, run: None)
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)

    calls: list = []

    def _install(script):
        """``script`` maps a speaker name to a list of replies, consumed in order."""
        def _fake(*, team, member, agent_id, speaker, prompt, **kwargs):
            calls.append({"speaker": speaker, "prompt": prompt})
            replies = script.get(speaker) or script.get("*") or [""]
            text = replies.pop(0) if len(replies) > 1 else replies[0]
            return Turn(speaker=speaker, text=text, run_id=f"run-{len(calls)}", cost=0.01)
        monkeypatch.setattr(runner, "_run_member", _fake)
        return calls

    return _install


def test_a_centralized_run_assigns_then_finishes_when_the_lead_says_so(stub_members):
    team = store.save_team(_team(mode="centralized", leader_agent_id="orch",
                                 leader_name="Lead", max_rounds=5, synthesize=False))
    stub_members({
        "Lead": [
            '{"assignments": [{"agent": "Sam", "instruction": "build the header"}]}',
            '{"assignments": [], "done": true, "final": "the page is up"}',
        ],
        "Sam": ["header pushed"],
        "Rae": ["looks fine"],
    })

    run = run_team(team.team_id, "ship the page")
    assert run.status == "completed" and run.stop_reason == "goal_met"
    assert run.result == "the page is up"
    # Only the member the lead actually assigned was paid for.
    senders = [m.sender for m in store.list_messages(run.team_run_id)]
    assert "Sam" in senders and "Rae" not in senders


def test_an_assignment_is_recorded_as_a_directed_message(stub_members):
    team = store.save_team(_team(mode="centralized", leader_agent_id="orch",
                                 leader_name="Lead", max_rounds=1, synthesize=False))
    stub_members({
        "Lead": ['{"assignments": [{"agent": "Sam", "instruction": "build the header"}]}'],
        "Sam": ["done"],
    })
    run = run_team(team.team_id, "ship the page")
    instruction = next(m for m in store.list_messages(run.team_run_id)
                       if m.kind == "instruction")
    assert instruction.recipients == ["Sam"] and instruction.content == "build the header"


def test_a_parallel_team_stops_when_every_member_is_done(stub_members):
    team = store.save_team(_team(mode="parallel", max_rounds=6, synthesize=False))
    stub_members({
        "Sam": ["working on it", f"pushed the last fix\n{DONE_TOKEN}"],
        "Rae": ["reviewing", f"nothing left to review\n{DONE_TOKEN}"],
    })

    run = run_team(team.team_id, "ship the page")
    assert run.rounds_done == 2 and run.stop_reason == "goal_met"


def test_one_member_declaring_done_does_not_end_a_parallel_team(stub_members):
    team = store.save_team(_team(mode="parallel", max_rounds=2, synthesize=False))
    stub_members({"Sam": [f"I am finished\n{DONE_TOKEN}"], "Rae": ["still going"]})

    run = run_team(team.team_id, "ship the page")
    assert run.rounds_done == 2 and run.stop_reason == "max_rounds"


def test_a_silent_round_ends_the_run_rather_than_paying_for_more_silence(stub_members):
    team = store.save_team(_team(mode="parallel", max_rounds=5, synthesize=False))
    stub_members({"*": [""]})
    run = run_team(team.team_id, "ship the page")
    assert run.rounds_done == 1 and run.stop_reason == "goal_met"


def test_the_round_cap_is_a_completed_run_not_a_failure(stub_members):
    team = store.save_team(_team(mode="parallel", max_rounds=2, synthesize=False))
    stub_members({"*": ["still thinking"]})
    run = run_team(team.team_id, "ship the page")
    assert run.rounds_done == 2 and run.stop_reason == "max_rounds"
    assert run.status == "completed"


def test_a_stop_written_by_another_process_still_ends_the_team(monkeypatch, stub_members):
    """The row is the cross-process half of a stop: a server that restarted mid
    run has no event to set, and the round loop still has to notice."""
    team = store.save_team(_team(mode="parallel", max_rounds=6, synthesize=False))
    calls = stub_members({"*": ["carrying on"]})

    import teams.runner as runner
    original = runner._run_parallel

    def _stop_after_first(**kwargs):
        result = original(**kwargs)
        store.request_stop(kwargs["run"].team_run_id)
        return result

    monkeypatch.setattr(runner, "_run_parallel", _stop_after_first)
    run = run_team(team.team_id, "ship the page")
    assert run.status == "stopped" and run.stop_reason == "stopped"
    assert run.rounds_done == 1
    # A stopped run is not summarised: the user asked for it to end, not for one
    # more paid call.
    assert not any(c["speaker"] for c in calls if "closing task" in c["prompt"].lower())


def test_a_member_that_fails_does_not_end_the_run(stub_members, monkeypatch):
    team = store.save_team(_team(mode="parallel", max_rounds=1, synthesize=False))
    import teams.runner as runner

    def _fake(*, team, member, agent_id, speaker, prompt, **kwargs):
        if speaker == "Sam":
            return Turn(speaker=speaker, error="model unreachable")
        return Turn(speaker=speaker, text="I carried on")

    monkeypatch.setattr(runner, "_run_member", _fake)
    run = run_team(team.team_id, "ship the page")
    kinds = {m.sender: m.kind for m in store.list_messages(run.team_run_id)}
    assert kinds["Sam"] == "error" and kinds["Rae"] == "message"
    assert run.status == "completed"


def test_the_goal_is_the_first_thing_on_the_board(stub_members):
    team = store.save_team(_team(mode="parallel", max_rounds=1, synthesize=False))
    stub_members({"*": ["ok"]})
    run = run_team(team.team_id, "ship the page")
    first = store.list_messages(run.team_run_id)[0]
    assert first.kind == "goal" and first.content == "ship the page"


def test_a_team_with_two_members_of_the_same_name_is_refused():
    team = store.save_team(_team(members=[
        TeamMember(agent_id="a", name="Sam"), TeamMember(agent_id="b", name="Sam"),
    ], mode="parallel"))
    with pytest.raises(ValueError, match="display name"):
        run_team(team.team_id, "go")


def test_a_centralized_team_without_a_lead_is_refused():
    team = store.save_team(_team(mode="centralized", leader_agent_id=None))
    with pytest.raises(ValueError, match="leader"):
        run_team(team.team_id, "go")


# ── Storage ──────────────────────────────────────────────────────────────────

def test_deleting_a_team_takes_its_runs_and_messages_with_it():
    team = store.save_team(_team())
    run = store.save_run(TeamRun(team_id=team.team_id))
    store.append_message(TeamMessage(team_run_id=run.team_run_id, content="hi"))

    assert store.delete_team(team.team_id)
    assert store.get_run(run.team_run_id) is None
    assert store.list_messages(run.team_run_id) == []


# ── The seat an agent actually gets ──────────────────────────────────────────

def test_a_member_keeps_its_own_prompt_and_gains_the_team_block(monkeypatch):
    """The charter is added to the agent's instructions, never in place of them."""
    import teams.runner as runner
    from managers.run_manager import get_run_by_id

    built = {}

    class _FakeAgent:
        agent_id, provider, model = "swe", "openai", "gpt-x"

        def run(self, prompt, **kwargs):
            built["prompt"] = prompt
            return type("R", (), {"ok": True, "agent_output": "header pushed",
                                  "error": None, "response": None})()

    def _fake_create_agent(agent_id, workspace=None, **overrides):
        built["overrides"] = overrides
        return _FakeAgent()

    class _FakeFactory:
        def load_definition(self, agent_id):
            return {"system_prompt": "You are a senior engineer."}

    monkeypatch.setattr("agents.agent_factory.create_agent", _fake_create_agent)
    monkeypatch.setattr("agents.agent_factory.get_factory", lambda: _FakeFactory())

    team = _team()
    turn = runner._run_member(
        team=team, member=team.members[0], agent_id="swe", speaker="Sam",
        prompt="do the thing", workspace=None, task_id=None, session_id=None,
        team_run_id="trun_test",
    )

    assert turn.text == "header pushed"
    system = built["overrides"]["system_prompt"]
    assert system.startswith("You are a senior engineer.")   # its own instructions survive
    assert "Ship the landing page." in system                # the charter is added
    assert "Rae" in system                                   # so is the roster

    # The turn is a real run, so it is inspectable from the Messages list.
    record = get_run_by_id(turn.run_id)
    assert record["status"] == "completed" and record["agent_id"] == "swe"
    assert record["output"] == "header pushed"


def test_a_member_whose_agent_cannot_be_built_reports_it_instead_of_raising(monkeypatch):
    def _boom(agent_id, workspace=None, **overrides):
        raise RuntimeError("no such model")

    monkeypatch.setattr("agents.agent_factory.create_agent", _boom)
    monkeypatch.setattr("agents.agent_factory.get_factory",
                        lambda: type("F", (), {"load_definition": lambda s, a: {"system_prompt": ""}})())

    import teams.runner as runner
    team = _team()
    turn = runner._run_member(
        team=team, member=team.members[0], agent_id="swe", speaker="Sam",
        prompt="do the thing", workspace=None, task_id=None, session_id=None,
        team_run_id="trun_test",
    )
    assert turn.error and "no such model" in turn.error and not turn.text


def test_a_chat_turn_does_not_mint_a_task():
    """A conversation turn is not a unit of work; a task per message would bury
    the task board."""
    import teams.runner as runner
    from tasks import service as _ts

    before = len(_ts.list_tasks())
    ws, path, task_id, session_id = runner._prepare_context(
        _team(), None, None, None, "conv-1", "ship the page",
    )
    assert task_id is None
    assert len(_ts.list_tasks()) == before


def test_a_run_started_from_the_teams_page_does_get_a_task():
    import teams.runner as runner
    from tasks import service as _ts

    ws, path, task_id, session_id = runner._prepare_context(
        _team(), None, None, None, None, "ship the page",
    )
    assert task_id
    task = _ts.get_task(task_id)
    assert task is not None and task.title.startswith("Team: ")
    # Never todo, not even briefly: a task in todo is one a polling orchestrator
    # can pick up and route somewhere else while the team is starting.
    assert task.status == _ts.TaskStatus.in_progress
    # Who holds it is recorded when the run claims it, not here — see
    # test_a_team_run_claims_its_task_and_starts_it_without_an_approval_step.


# ── Autonomous: work moves by request, not by roll call ──────────────────────

def test_the_request_lands_on_the_entry_member_and_nobody_else_acts(stub_members):
    """The point of the mode: a member with nothing asked of it costs nothing."""
    team = store.save_team(_team(mode="autonomous", max_rounds=1, synthesize=False))
    calls = stub_members({"Sam": ["I will take this one."], "Rae": ["should not run"]})

    run_team(team.team_id, "ship the page")
    assert [c["speaker"] for c in calls] == ["Sam"]


def test_the_entry_member_is_told_to_do_it_or_route_it(stub_members):
    team = store.save_team(_team(mode="autonomous", max_rounds=1, synthesize=False))
    calls = stub_members({"*": ["taken"]})
    run_team(team.team_id, "ship the page")
    assert "Either act on it or route it" in calls[0]["prompt"]


def test_naming_a_teammate_is_what_gives_them_a_turn(stub_members):
    team = store.save_team(_team(mode="autonomous", max_rounds=4, synthesize=False))
    calls = stub_members({
        "Sam": ["@Rae: header is pushed, please review it"],
        "Rae": [f"looks fine\n{DONE_TOKEN}"],
    })

    run = run_team(team.team_id, "ship the page")
    assert [c["speaker"] for c in calls] == ["Sam", "Rae"]
    # Rae was handed Sam's message whole, as the assignment it is.
    assert "Sam has handed this to you" in calls[1]["prompt"]
    assert "header is pushed" in calls[1]["prompt"]
    assert run.rounds_done == 2 and run.stop_reason == "goal_met"


def test_a_reply_that_asks_nobody_for_anything_ends_the_run(stub_members):
    """Work has come to rest: another round would only be the team asking
    itself whether it is finished."""
    team = store.save_team(_team(mode="autonomous", max_rounds=6, synthesize=False))
    calls = stub_members({"Sam": ["I did it myself; here is the page."]})

    run = run_team(team.team_id, "ship the page")
    assert run.rounds_done == 1 and run.stop_reason == "goal_met"
    assert len(calls) == 1


def test_a_handoff_wins_over_a_done_token_written_in_the_same_turn(stub_members):
    """Both at once breaks the protocol. Honouring the request is the safe read:
    dropping it would silently lose work a colleague was asked for."""
    team = store.save_team(_team(mode="autonomous", max_rounds=3, synthesize=False))
    calls = stub_members({
        "Sam": [f"@Rae: please review this\n{DONE_TOKEN}"],
        "Rae": ["reviewed"],
    })
    run_team(team.team_id, "ship the page")
    assert [c["speaker"] for c in calls] == ["Sam", "Rae"]


def test_an_explicit_entry_agent_takes_the_request_instead_of_the_first_seat(stub_members):
    team = store.save_team(_team(mode="autonomous", entry_agent_id="rev",
                                 max_rounds=1, synthesize=False))
    calls = stub_members({"*": ["taken"]})
    run_team(team.team_id, "ship the page")
    assert [c["speaker"] for c in calls] == ["Rae"]


def test_a_handoff_is_read_even_when_private_messages_are_off(stub_members):
    """Turning off private messages governs who may talk out of earshot, not
    whether a member may ask a colleague for something."""
    team = store.save_team(_team(mode="autonomous", allow_direct_messages=False,
                                 max_rounds=3, synthesize=False))
    calls = stub_members({"Sam": ["@Rae: please review"], "Rae": ["fine"]})
    run_team(team.team_id, "ship the page")
    assert [c["speaker"] for c in calls] == ["Sam", "Rae"]


def test_a_member_handing_work_to_itself_does_not_get_another_turn(stub_members):
    team = store.save_team(_team(mode="autonomous", max_rounds=5, synthesize=False))
    calls = stub_members({"Sam": ["@Sam: keep going"]})
    run = run_team(team.team_id, "ship the page")
    assert len(calls) == 1 and run.rounds_done == 1


# ── Stopping ─────────────────────────────────────────────────────────────────

def test_a_stop_mid_round_ends_the_run_as_stopped_not_as_an_answer(stub_members):
    """The turn that was interrupted is not read back as the team's result."""
    import teams.runner as runner

    team = store.save_team(_team(mode="parallel", max_rounds=5, synthesize=True))
    stopped_runs = []

    def _fake(*, team, member, agent_id, speaker, prompt, team_run_id, **kwargs):
        control.request_stop(team_run_id)
        stopped_runs.append(team_run_id)
        return Turn(speaker=speaker, stopped=True)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runner, "_run_member", _fake)
    try:
        run = run_team(team.team_id, "ship the page")
    finally:
        monkeypatch.undo()

    assert run.status == "stopped" and run.stop_reason == "stopped"
    # No synthesis pass: a paid call to summarise an interrupted run is not what
    # pressing stop asks for.
    assert run.result == ""
    # And the run's stop event is not left behind in the process.
    assert not control.is_stopped(stopped_runs[0])


def test_stopping_a_run_that_is_not_running_is_refused():
    team = store.save_team(_team(mode="parallel"))
    run = TeamRun(team_id=team.team_id, status="completed")
    store.save_run(run)
    assert stop_run(run.team_run_id) is False
    assert stop_run("trun_does_not_exist") is False


def test_a_stop_marks_the_row_and_interrupts_the_turns_in_flight():
    """Both halves: the durable status a reloaded page reads, and the in-memory
    event a member's turn is watching."""
    team = store.save_team(_team(mode="parallel"))
    run = TeamRun(team_id=team.team_id, status="running")
    store.save_run(run)
    control.register(run.team_run_id)

    assert stop_run(run.team_run_id) is True
    assert control.is_stopped(run.team_run_id)
    assert store.get_run(run.team_run_id).status == "stopping"
    control.release(run.team_run_id)


# ── The task a team run is doing ─────────────────────────────────────────────

def test_a_team_run_claims_its_task_and_starts_it_without_an_approval_step():
    """The user picked this team and pressed run: there is nobody left to
    approve the assignment, and a task holding an assignment with no run id
    reads as pending_approval and sits in todo behind a prompt."""
    from tasks import service as task_service
    from tasks.models import AgentState, TaskStatus
    import teams.runner as runner

    team = store.save_team(_team(mode="parallel"))
    task = task_service.create_task(title="Ship it", workspace="ws")
    run = TeamRun(team_id=team.team_id, task_id=str(task.id), workspace="ws",
                  goal="ship the page")

    runner._claim_task(team, run)

    updated = task_service.get_task(task.id)
    assert updated.status == TaskStatus.in_progress
    assert updated.assigned_agent_type == f"Team: {team.name}"
    assert updated.assigned_agent_run_id == run.team_run_id
    assert updated.agent_state != AgentState.pending_approval


def test_a_stopped_run_leaves_its_task_stopped_rather_than_blocked():
    from tasks import service as task_service
    from tasks.models import TaskStatus
    import teams.runner as runner

    team = store.save_team(_team(mode="parallel"))
    task = task_service.create_task(title="Ship it", workspace="ws")
    run = TeamRun(team_id=team.team_id, task_id=str(task.id), workspace="ws",
                  status="stopped", stop_reason="stopped")
    runner._claim_task(team, run)

    runner._finalize_task(run)

    assert task_service.get_task(task.id).status == TaskStatus.stopped
