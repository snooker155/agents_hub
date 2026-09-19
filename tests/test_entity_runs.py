"""
Launching, following and stopping scenarios, teams and loops from a tool call.

The load-bearing behaviour is the approval gate. Everything these tools start
spends money that nothing automatic takes back, so the first call must refuse —
and refuse *usefully*, with the cost estimate attached, because "shall I run
this?" and "this will cost about a dollar" are the same question. A gate that
merely says no teaches the model to set the flag and move on.

The runners are stubbed. What is verified is the contract around them: nothing
starts without approval, nothing starts that could not run anyway, and a run
that did start is reported by id rather than waited for.
"""
import json
import threading
import time

import pytest

from tools.entity_runs import (
    get_loop_run_tool, get_scenario_run_tool, get_team_run_tool,
    run_loop_tool, run_scenario_tool, run_team_tool,
    stop_loop_run_tool, stop_scenario_run_tool, stop_team_run_tool,
)


def call(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


@pytest.fixture(autouse=True)
def no_workspace(monkeypatch):
    """Run outside any workspace, so nothing is refused for belonging elsewhere."""
    import tools.entity_runs as entity_runs
    monkeypatch.setattr(entity_runs, "resolve_active_workspace",
                        lambda preferred=None: None)


@pytest.fixture(autouse=True)
def drain_threads():
    """Let a stubbed runner's thread finish before the next test's database."""
    yield
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not [t for t in threading.enumerate()
                if t.name.startswith(("sim-", "team-", "loop-")) and t.is_alive()]:
            return
        time.sleep(0.02)


# ── Scenarios ─────────────────────────────────────────────────────────────────

@pytest.fixture
def scenario():
    from playground import store
    from playground.models import Role, Scenario

    return store.save_scenario(Scenario(
        name="Tavern", environment="social", max_ticks=5,
        roles=[Role(agent_id="alpha", name="Mara")],
    ))


@pytest.fixture
def sim_runner(monkeypatch, scenario):
    """A ``run_simulation`` that writes its row and returns, without any models."""
    from playground import store
    from playground.models import SimRun
    import playground.runner as runner

    started = {}

    def _fake_run(scenario_id, *, workspace=None, on_start=None, on_tick=None):
        run = store.save_sim_run(SimRun(scenario_id=scenario_id, status="running",
                                        workspace=workspace, environment="social"))
        started["run"] = run
        if on_start:
            on_start(run)
        return run

    monkeypatch.setattr(runner, "run_simulation", _fake_run)
    return started


def test_a_simulation_is_not_started_without_approval(scenario, sim_runner):
    out = call(run_scenario_tool, scenario_id=scenario.scenario_id)
    assert out["ok"] is False
    assert out["code"] == "approval_required"
    # The refusal is the moment to show the user the number they are approving.
    assert out["estimate"]["max_ticks"] == 5
    assert "run" not in sim_runner


def test_an_approved_simulation_starts_and_reports_its_id(scenario, sim_runner):
    out = call(run_scenario_tool, scenario_id=scenario.scenario_id, user_approved=True)
    assert out["ok"], out
    assert out["sim_run_id"] == sim_runner["run"].sim_run_id
    assert out["scenario_id"] == scenario.scenario_id


def test_a_scenario_with_no_cast_is_refused_before_the_gate(sim_runner):
    """An empty scenario cannot run at all, so it is a setup problem to fix —
    not something to ask the user to approve."""
    from playground import store
    from playground.models import Scenario

    empty = store.save_scenario(Scenario(name="Empty", environment="social"))
    out = call(run_scenario_tool, scenario_id=empty.scenario_id, user_approved=True)
    assert out["ok"] is False
    assert out["code"] == "invalid_scenario"
    assert "run" not in sim_runner


def test_an_unknown_scenario_is_not_found(sim_runner):
    out = call(run_scenario_tool, scenario_id="scn_missing", user_approved=True)
    assert out["code"] == "not_found"


def test_a_scenario_from_another_workspace_is_refused(monkeypatch, sim_runner):
    from playground import store
    from playground.models import Role, Scenario
    import tools.entity_runs as entity_runs

    other = store.save_scenario(Scenario(
        name="Elsewhere", environment="social", workspace="other",
        roles=[Role(agent_id="alpha", name="Mara")],
    ))
    monkeypatch.setattr(entity_runs, "resolve_active_workspace",
                        lambda preferred=None: "here")
    out = call(run_scenario_tool, scenario_id=other.scenario_id, user_approved=True)
    assert out["code"] == "forbidden"
    assert "run" not in sim_runner


def test_a_failing_launch_is_reported_rather_than_raised(monkeypatch, scenario):
    import playground.runner as runner

    def _boom(*a, **k):
        raise RuntimeError("the world would not build")

    monkeypatch.setattr(runner, "run_simulation", _boom)
    out = call(run_scenario_tool, scenario_id=scenario.scenario_id, user_approved=True)
    assert out["ok"] is False
    assert out["code"] == "start_failed"
    assert "the world would not build" in out["error"]


def test_a_simulation_run_is_reported_by_id_or_by_scenario(scenario, sim_runner):
    call(run_scenario_tool, scenario_id=scenario.scenario_id, user_approved=True)
    run_id = sim_runner["run"].sim_run_id

    by_id = call(get_scenario_run_tool, sim_run_id=run_id)
    assert by_id["sim_run_id"] == run_id
    assert by_id["live"] is True

    by_scenario = call(get_scenario_run_tool, scenario_id=scenario.scenario_id)
    assert by_scenario["sim_run_id"] == run_id


def test_asking_about_a_run_needs_something_to_look_up():
    assert call(get_scenario_run_tool)["code"] == "invalid"
    assert call(get_scenario_run_tool, sim_run_id="sim_nope")["code"] == "not_found"


def test_stopping_a_simulation_that_is_not_running_says_so(monkeypatch):
    import playground.runner as runner
    monkeypatch.setattr(runner, "stop_simulation", lambda rid: False)
    out = call(stop_scenario_run_tool, sim_run_id="sim_1")
    assert out["ok"] is False
    assert out["code"] == "not_running"


def test_stopping_a_live_simulation_reports_it(monkeypatch):
    import playground.runner as runner
    monkeypatch.setattr(runner, "stop_simulation", lambda rid: True)
    assert call(stop_scenario_run_tool, sim_run_id="sim_1")["ok"]


# ── Teams ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def team():
    from teams import store
    from teams.models import Team, TeamMember

    return store.save_team(Team(
        name="Platform", description="keep the platform shippable",
        leader_agent_id="alpha", max_rounds=4,
        members=[TeamMember(agent_id="alpha", name="Lead", manifest="assigns work")],
    ))


@pytest.fixture
def team_runner(monkeypatch, team):
    from teams import store
    from teams.models import TeamRun
    import teams.runner as runner

    started = {}

    def _fake_run(team_id, goal, *, workspace=None, task_id=None, session_id=None,
                  conversation_id=None, on_message=None):
        run = store.save_run(TeamRun(team_id=team_id, goal=goal, status="running",
                                     workspace=workspace))
        started["run"] = run
        started["goal"] = goal
        if on_message:
            on_message(None)
        return run

    monkeypatch.setattr(runner, "run_team", _fake_run)
    return started


def test_a_team_run_is_not_started_without_approval(team, team_runner):
    out = call(run_team_tool, team_id=team.team_id, goal="ship the thing")
    assert out["code"] == "approval_required"
    assert out["estimate"]["members"] == 1
    assert "run" not in team_runner


def test_an_approved_team_run_starts_with_its_goal(team, team_runner):
    out = call(run_team_tool, team_id=team.team_id, goal="ship the thing",
               user_approved=True)
    assert out["ok"], out
    assert out["team_run_id"] == team_runner["run"].team_run_id
    assert team_runner["goal"] == "ship the thing"


def test_a_team_run_falls_back_to_the_teams_own_description(team, team_runner):
    """A team with a description already says what it is for; making the user
    retype it as a goal is ceremony."""
    call(run_team_tool, team_id=team.team_id, user_approved=True)
    assert team_runner["goal"] == "keep the platform shippable"


def test_a_team_run_with_nothing_to_work_on_is_refused(team_runner):
    from teams import store
    from teams.models import Team, TeamMember

    silent = store.save_team(Team(
        name="Mute", leader_agent_id="alpha",
        members=[TeamMember(agent_id="alpha", name="Lead", manifest="x")],
    ))
    out = call(run_team_tool, team_id=silent.team_id, user_approved=True)
    assert out["ok"] is False
    assert "goal" in out["error"]
    assert "run" not in team_runner


def test_a_team_with_no_members_is_refused(team_runner):
    from teams import store
    from teams.models import Team

    empty = store.save_team(Team(name="Nobody", description="do things"))
    out = call(run_team_tool, team_id=empty.team_id, user_approved=True)
    assert out["code"] == "invalid_team"


def test_a_team_run_reports_its_result(team, team_runner):
    call(run_team_tool, team_id=team.team_id, user_approved=True)
    out = call(get_team_run_tool, team_id=team.team_id)
    assert out["team_run_id"] == team_runner["run"].team_run_id
    assert out["goal"] == "keep the platform shippable"
    assert out["live"] is True


def test_stopping_a_team_run_that_is_not_running_says_so(monkeypatch):
    import teams.runner as runner
    monkeypatch.setattr(runner, "stop_run", lambda rid: False)
    assert call(stop_team_run_tool, team_run_id="trun_1")["code"] == "not_running"


# ── Loops ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def one_flow(monkeypatch):
    flow = {"id": "flow-1", "name": "Write and review", "workspace": None,
            "nodes": [{"id": "n1", "data": {"agent_id": "alpha"}}], "edges": []}
    import flow.store as flow_store
    monkeypatch.setattr(flow_store, "get_flow",
                        lambda fid: flow if fid == flow["id"] else None)
    return flow


@pytest.fixture
def loop(one_flow):
    from loops import store
    from loops.models import Loop

    return store.save_loop(Loop(
        name="Polish", description="make it publishable", flow_id="flow-1",
        exit_criterion="every claim carries a source", max_iterations=3,
    ))


@pytest.fixture
def loop_runner(monkeypatch, loop):
    from loops import store
    from loops.models import LoopRun
    import loops.runner as runner

    started = {}

    def _fake_run(loop_id, *, goal="", workspace=None, task_id=None, seed=None,
                  on_iteration=None):
        run = store.save_run(LoopRun(loop_id=loop_id, goal=goal, status="running",
                                     workspace=workspace))
        started["run"] = run
        started["goal"] = goal
        if on_iteration:
            on_iteration(None)
        return run

    monkeypatch.setattr(runner, "run_loop", _fake_run)
    return started


def test_a_loop_run_is_not_started_without_approval(loop, loop_runner):
    out = call(run_loop_tool, loop_id=loop.loop_id)
    assert out["code"] == "approval_required"
    assert out["estimate"]["max_iterations"] == 3
    assert "run" not in loop_runner


def test_an_approved_loop_run_starts(loop, loop_runner):
    out = call(run_loop_tool, loop_id=loop.loop_id, goal="the pricing article",
               user_approved=True)
    assert out["ok"], out
    assert out["loop_run_id"] == loop_runner["run"].loop_run_id
    assert loop_runner["goal"] == "the pricing article"


def test_a_loop_whose_flow_is_gone_is_refused_before_the_gate(monkeypatch, loop,
                                                              loop_runner):
    """The runner would only discover this after opening a run record, leaving a
    failed run in the history for a problem that was visible up front."""
    import flow.store as flow_store
    monkeypatch.setattr(flow_store, "get_flow", lambda fid: None)
    out = call(run_loop_tool, loop_id=loop.loop_id, user_approved=True)
    assert out["ok"] is False
    assert out["code"] == "invalid_loop"
    assert out["flow_id"] == "flow-1"
    assert "run" not in loop_runner


def test_a_loop_run_reports_its_score_trajectory(loop, loop_runner):
    from loops import store
    from loops.models import Iteration

    call(run_loop_tool, loop_id=loop.loop_id, user_approved=True)
    run_id = loop_runner["run"].loop_run_id
    store.save_iteration(Iteration(loop_run_id=run_id, iteration=1, score=40,
                                   verdict="continue"))
    store.save_iteration(Iteration(loop_run_id=run_id, iteration=2, score=78,
                                   verdict="stop"))

    out = call(get_loop_run_tool, loop_run_id=run_id)
    # The trajectory is the point of a loop: 40 → 78 says what the final answer
    # on its own cannot.
    assert [i["score"] for i in out["trajectory"]] == [40, 78]
    assert out["loop_id"] == loop.loop_id


def test_stopping_a_loop_run_that_is_not_running_says_so():
    assert call(stop_loop_run_tool, loop_run_id="lrun_nope")["code"] == "not_running"


# ── the grant model ───────────────────────────────────────────────────────────

def test_reading_a_run_is_a_private_read_and_starting_one_is_not():
    """Starting a run spends money, which the approval gate governs; reading one
    back returns what the agents produced, which is what the capability table
    is about."""
    from tools.capabilities import READS_PRIVATE, grants_of

    assert grants_of("get_team_run_tool") == frozenset({READS_PRIVATE})
    assert grants_of("run_team_tool") == frozenset()
    assert grants_of("stop_team_run_tool") == frozenset()
    assert grants_of("entity_runs") == frozenset({READS_PRIVATE})
