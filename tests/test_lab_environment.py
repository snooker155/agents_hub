"""
The research lab environment, its views, its template and its reproducibility eval.

The lab is tested the way the market is: as a program, with no agent in the
loop. The one side effect it has (running an experiment through
``tools.run_code.run_snippet``) is replaced by a stub, so a test decides what
an experiment "printed" and checks what the world did with it.
"""
import json
import sys
from pathlib import Path

import pytest

from playground import store
from playground.environments import create_environment
from playground.models import Role, Scenario, SimRun


def lab(**params):
    env = create_environment("lab", {"question": "Is the mean 0.5?", **params}, seed=5)
    env.register_cast([
        {"name": "Lead", "role": "lead"}, {"name": "Theo", "role": "theorist"},
        {"name": "Exp", "role": "experimentalist"}, {"name": "Crit", "role": "critic"},
        {"name": "Scribe", "role": "scribe"},
    ])
    env.begin_tick(1)
    return env


@pytest.fixture
def snippet(monkeypatch):
    """Replace the sandbox: record every call, answer with ``calls.reply``."""
    import tools.run_code as run_code

    class Calls(list):
        reply = {"ok": True, "exit_code": 0, "sandbox": "local", "duration_ms": 12,
                 "stdout": "warming up\n{\"mean\": 0.498, \"within\": 0.97}\n",
                 "stderr": "", "error": None}

    calls = Calls()

    def fake(language, code, timeout=60, stdin=None, **_):
        calls.append({"language": language, "code": code, "timeout": timeout,
                      "stdin": json.loads(stdin) if stdin else None})
        return dict(calls.reply)

    monkeypatch.setattr(run_code, "run_snippet", fake)
    return calls


def _hyp_and_experiment(env, parent=None):
    res = env.apply("Theo", "propose_hypothesis", {"text": "mean is near 0.5",
                                                   "parent_id": parent})
    assert res.ok, res.message
    hid = res.effects["hypothesis_id"]
    res = env.apply("Exp", "design_experiment", {
        "hypothesis_id": hid, "design": "draw n uniforms",
        "code": "import json,sys\nprint(json.dumps({'mean': 0.5}))",
        "params": {"n": 1000}})
    assert res.ok, res.message
    return hid, res.effects["experiment_id"]


# ── Actions and state ─────────────────────────────────────────────────────────

def test_the_lab_is_registered_with_its_contract():
    from playground.environments import get_environment_class
    cls = get_environment_class("lab")
    assert cls is not None and cls.renderer == "lab"
    names = {a["name"] for a in cls.ACTIONS}
    assert {"propose_hypothesis", "design_experiment", "run_experiment", "analyze",
            "critique", "decide", "write_up", "add_formula", "speak_to",
            "observe"} <= names
    assert cls.IDLE_ACTIONS == ("observe",)
    assert cls.TOOL_ALLOWLIST == ("calculator",)
    assert set(cls.OBJECTIVES) == {"hypotheses_decided", "experiments_run", "report_complete"}


def test_proposing_designing_and_deciding_move_a_hypothesis_through_its_states():
    env = lab()
    hid, eid = _hyp_and_experiment(env)
    assert env.hypotheses[hid]["status"] == "testing"
    assert env.experiments[eid]["status"] == "designed"
    assert not env.apply("Theo", "propose_hypothesis", {"text": "x", "parent_id": "H9"}).ok
    assert not env.apply("Lead", "decide", {"hypothesis_id": hid, "verdict": "maybe"}).ok
    res = env.apply("Lead", "decide", {"hypothesis_id": hid, "verdict": "confirmed",
                                        "note": "the numbers agree"})
    assert res.ok
    assert env.hypotheses[hid]["decided_by"] == "Lead"
    assert not env.apply("Nobody", "observe", {}).ok


def test_critique_write_up_formula_and_speech():
    env = lab()
    hid, eid = _hyp_and_experiment(env)
    assert env.apply("Crit", "critique", {"target_id": eid, "text": "one seed"}).ok
    assert env.apply("Crit", "critique", {"target_id": hid, "text": "vague"}).ok
    assert not env.apply("Crit", "critique", {"target_id": "E99", "text": "?"}).ok
    assert env.experiments[eid]["critiques"][0]["author"] == "Crit"
    # The author of what was critiqued is woken.
    assert any("critiqued" in r for r in env.pending_triggers("Exp"))
    assert env.apply("Scribe", "write_up", {"section": "results", "text": "0.498"}).ok
    assert env.report == {"Results": "0.498"}
    assert env.apply("Theo", "add_formula", {"label": "sd", "latex": r"\frac{1}{\sqrt{12n}}"}).ok
    assert env.apply("Lead", "speak_to", {"recipient": "Exp", "text": "run it"}).ok
    assert not env.apply("Lead", "speak_to", {"recipient": "Ghost", "text": "?"}).ok


def test_run_experiment_parses_the_last_json_line_and_builds_the_dataset(snippet):
    env = lab(experiment_timeout=30)
    hid, eid = _hyp_and_experiment(env)
    res = env.apply("Exp", "run_experiment", {"experiment_id": eid})
    assert res.ok, res.message
    exp = env.experiments[eid]
    assert exp["status"] == "done"
    assert exp["result"] == {"mean": 0.498, "within": 0.97}
    assert exp["metrics"] == {"mean": 0.498, "within": 0.97}
    call = snippet[0]
    assert call["language"] == "python" and call["timeout"] == 30
    # The seed and params reach the program on stdin.
    assert call["stdin"] == {"params": {"n": 1000}, "seed": 5}
    assert env.budget["experiments_used"] == 1
    data = env.datasets["results"]
    assert data["columns"] == ["experiment", "hypothesis", "seed", "mean", "within"]
    assert data["rows"] == [[eid, hid, 5, 0.498, 0.97]]
    # An explicit seed wins.
    env.apply("Exp", "run_experiment", {"experiment_id": eid, "seed": 99})
    assert snippet[1]["stdin"]["seed"] == 99
    # A rerun replaces the experiment's row but keeps every run in its history.
    assert [r["seed"] for r in env.experiments[eid]["runs"]] == [5, 99]
    assert len(env.datasets["results"]["rows"]) == 1
    # analyze merges numeric metrics only.
    assert env.apply("Exp", "analyze", {"experiment_id": eid, "analysis": "close",
                                         "metrics": {"gap": 0.002, "note": "x"}}).ok
    assert env.experiments[eid]["metrics"]["gap"] == 0.002
    assert "note" not in env.experiments[eid]["metrics"]


def test_a_program_without_a_json_last_line_keeps_its_stdout(snippet):
    env = lab()
    _, eid = _hyp_and_experiment(env)
    snippet.reply = {**snippet.reply, "stdout": "no json here\n"}
    res = env.apply("Exp", "run_experiment", {"experiment_id": eid})
    assert "not a JSON object" in res.message
    assert env.experiments[eid]["result"] == {"stdout": "no json here\n"}
    assert env.budget["experiments_used"] == 1


def test_an_unavailable_sandbox_fails_the_experiment_without_charging(snippet):
    env = lab()
    _, eid = _hyp_and_experiment(env)
    snippet.reply = {"ok": False, "sandbox": "unavailable", "stdout": "", "stderr": "",
                     "error": "docker is not available"}
    res = env.apply("Exp", "run_experiment", {"experiment_id": eid})
    assert not res.ok
    assert env.experiments[eid]["status"] == "failed"
    assert "docker is not available" in env.experiments[eid]["result"]["error"]
    assert env.budget["experiments_used"] == 0


def test_the_budget_refuses_a_run_once_spent(snippet):
    env = lab(max_experiments=2)
    _, eid = _hyp_and_experiment(env)
    assert env.apply("Exp", "run_experiment", {"experiment_id": eid}).ok
    assert env.apply("Exp", "run_experiment", {"experiment_id": eid}).ok
    res = env.apply("Exp", "run_experiment", {"experiment_id": eid})
    assert not res.ok and "budget is spent" in res.message
    assert len(snippet) == 2


# ── Ending ────────────────────────────────────────────────────────────────────

def test_is_done_when_every_root_hypothesis_is_decided():
    env = lab()
    assert not env.is_done()          # no hypothesis yet
    root, _ = _hyp_and_experiment(env)
    child, _ = _hyp_and_experiment(env, parent=root)
    env.apply("Lead", "decide", {"hypothesis_id": root, "verdict": "needs_repeat"})
    assert not env.is_done()
    env.apply("Lead", "decide", {"hypothesis_id": root, "verdict": "refuted"})
    # The child is still open; only roots count.
    assert env.is_done()
    assert env.frame()["stop_reason"] == "hypotheses_decided"
    assert env.ending


def test_is_done_when_the_budget_is_exhausted(snippet):
    env = lab(max_experiments=1)
    _, eid = _hyp_and_experiment(env)
    assert not env.is_done()
    env.apply("Exp", "run_experiment", {"experiment_id": eid})
    assert env.is_done()
    assert env.frame()["stop_reason"] == "budget_exhausted"


def test_the_frame_nests_hypotheses_as_a_tree():
    env = lab()
    root, eid = _hyp_and_experiment(env)
    child, _ = _hyp_and_experiment(env, parent=root)
    grandchild, _ = _hyp_and_experiment(env, parent=child)
    frame = env.frame()
    assert frame["renderer"] == "lab"
    assert frame["question"] == "Is the mean 0.5?"
    tree = frame["hypotheses"]
    assert [n["id"] for n in tree] == [root]
    assert tree[0]["experiments"] == [eid]
    assert tree[0]["children"][0]["id"] == child
    assert tree[0]["children"][0]["children"][0]["id"] == grandchild
    assert tree[0]["children"][0]["children"][0]["depth"] == 2
    assert frame["budget"] == {"experiments_used": 0, "experiments_max": 12}
    assert frame["stop_reason"] == ""


def test_score_counts_per_agent_and_the_objectives(snippet):
    env = lab()
    hid, eid = _hyp_and_experiment(env)
    env.apply("Exp", "run_experiment", {"experiment_id": eid})
    env.apply("Lead", "decide", {"hypothesis_id": hid, "verdict": "confirmed"})
    env.apply("Scribe", "write_up", {"section": "Abstract", "text": "done"})
    score = env.score()
    assert score["Theo"]["proposed"] == 1
    assert score["Exp"]["experiments_run"] == 1
    assert score["Lead"]["decisions"] == 1
    assert score["Scribe"]["sections_written"] == 1
    assert score["Lead"]["hypotheses_decided"] == 1
    assert score["Lead"]["report_complete"] == 0.25


def test_snapshot_and_restore_round_trip_the_whole_state(snippet):
    env = lab()
    hid, eid = _hyp_and_experiment(env)
    env.apply("Exp", "run_experiment", {"experiment_id": eid})
    env.apply("Theo", "add_formula", {"label": "sd", "latex": "x"})
    env.apply("Lead", "speak_to", {"recipient": "Exp", "text": "again"})
    snap = json.loads(json.dumps(env.snapshot()))
    fresh = create_environment("lab", {"question": "Is the mean 0.5?"}, seed=5)
    fresh.restore(snap)
    assert fresh.frame() == env.frame()
    assert fresh.score() == env.score()
    assert fresh.drain_inbox("Exp")[0]["text"] == "again"
    # The id counters survive, so the next hypothesis does not reuse H1.
    res = fresh.apply("Theo", "propose_hypothesis", {"text": "next"})
    assert res.effects["hypothesis_id"] == "H2"


# ── Views ─────────────────────────────────────────────────────────────────────

def test_views_are_valid_specs_of_their_kind(snippet):
    from views.models import validate_spec
    env = lab()
    assert env.views() == []
    hid, eid = _hyp_and_experiment(env)
    env.apply("Exp", "run_experiment", {"experiment_id": eid})
    env.apply("Theo", "add_formula", {"label": "sd_n", "latex": r"\sigma = \frac{1}{\sqrt{12n}}"})
    env.apply("Scribe", "write_up", {"section": "Results", "text": "mean 0.498"})
    views = {v["key"]: v for v in env.views()}
    assert set(views) == {"results_table", "results_chart", "formulas", "report"}
    assert [v["kind"] for v in views.values()] == ["table", "chart", "latex", "document"]
    for v in views.values():
        validate_spec(v["kind"], v["spec"])
    chart = views["results_chart"]["spec"]["vega_lite"]
    assert chart["encoding"]["color"]["field"] == "hypothesis"
    assert chart["data"]["values"][0] == {"experiment": eid, "hypothesis": hid, "value": 0.498}
    assert "## Results" in views["report"]["spec"]["markdown"]


def test_sync_env_views_creates_then_updates_in_place(snippet):
    from playground.runner import _sync_env_views
    from views import store as vstore

    env = lab()
    run = SimRun(scenario_id="scn_x", workspace=None, environment="lab")
    mapping = _sync_env_views(env, run, {})
    assert mapping == {}
    _, eid = _hyp_and_experiment(env)
    env.apply("Exp", "run_experiment", {"experiment_id": eid})
    mapping = _sync_env_views(env, run, mapping)
    assert set(mapping) == {"results_table", "results_chart"}
    owned = vstore.views_owned_by("scenario", run.sim_run_id)
    assert len(owned) == 2
    table_id = mapping["results_table"]
    assert len(vstore.get_view(table_id)["spec"]["rows"]) == 1

    _, eid2 = _hyp_and_experiment(env)
    env.apply("Exp", "run_experiment", {"experiment_id": eid2, "seed": 7})
    env.apply("Scribe", "write_up", {"section": "Abstract", "text": "short"})
    mapping = _sync_env_views(env, run, mapping)
    assert mapping["results_table"] == table_id        # same view, updated
    assert len(vstore.get_view(table_id)["spec"]["rows"]) == 2
    assert len(vstore.views_owned_by("scenario", run.sim_run_id)) == 3
    assert vstore.get_view(mapping["report"])["owner"] == {
        "kind": "scenario", "id": run.sim_run_id}


def test_sync_env_views_is_never_fatal():
    from playground.runner import _sync_env_views

    class Broken:
        def views(self):
            raise RuntimeError("boom")

    run = SimRun(scenario_id="scn_x")
    assert _sync_env_views(Broken(), run, {"a": "b"}) == {"a": "b"}


def test_update_spec_refuses_an_invalid_spec_and_an_unknown_view():
    from views import store as vstore
    from views.models import ViewValidationError
    view = vstore.create_view("table", "t", {"columns": ["a"], "rows": [[1]]})
    assert vstore.update_spec(view.view_id, {"columns": ["a"], "rows": [[1], [2]]})
    with pytest.raises(ViewValidationError):
        vstore.update_spec(view.view_id, {"rows": "nope"})
    assert not vstore.update_spec("vw_missing", {"columns": [], "rows": []})


# ── Teams and templates ───────────────────────────────────────────────────────

def _team(members=None, leader="alpha"):
    from teams.models import Team, TeamMember
    from teams.store import save_team
    team = Team(name="Crew", leader_agent_id=leader, members=members or [
        TeamMember(agent_id="alpha", name="Ada", role="lead", goal="decide"),
        TeamMember(agent_id="beta", name="", role="experimentalist",
                   manifest="runs the experiments"),
        TeamMember(agent_id="beta", name="", role="critic", goal="doubt"),
    ])
    return save_team(team)


def test_roles_from_team_builds_the_cast_from_members():
    from playground.runner import roles_from_team, validate_scenario_for_run
    team = _team()
    scenario = Scenario(name="lab", environment="lab", team_id=team.team_id)
    roles = roles_from_team(scenario)
    assert [r.display_name() for r in roles] == ["Ada", "beta", "beta 2"]
    assert roles[0].starts and not roles[1].starts
    assert roles[1].goal == "runs the experiments"      # manifest when no goal
    assert roles[2].goal == "doubt"
    # validate fills the cast in place when the scenario has none.
    validate_scenario_for_run(scenario)
    assert len(scenario.roles) == 3
    # Roles on the scenario win.
    own = Scenario(name="lab", environment="lab", team_id=team.team_id,
                   roles=[Role(agent_id="alpha", name="Solo")])
    validate_scenario_for_run(own)
    assert [r.name for r in own.roles] == ["Solo"]
    assert roles_from_team(Scenario(team_id="team_missing")) == []


def test_team_id_round_trips_through_the_store():
    team = _team()
    saved = store.save_scenario(Scenario(name="x", environment="lab", team_id=team.team_id))
    assert store.get_scenario(saved.scenario_id).team_id == team.team_id


def test_estimate_counts_the_team_cast():
    from playground.runner import estimate_cost
    team = _team()
    est = estimate_cost(Scenario(name="x", environment="lab", team_id=team.team_id,
                                 max_ticks=2))
    assert est["agents"] == 3 and est["llm_calls"] == 6


def test_the_lab_template_is_a_valid_scenario():
    from playground.runner import validate_scenario_for_run
    from playground.scenario_templates import TEMPLATES, list_templates, scenario_from_template
    assert [t["id"] for t in list_templates()] == ["lab"]
    payload = scenario_from_template("lab", agent_id="alpha")
    scenario = Scenario.from_dict(payload)
    validate_scenario_for_run(scenario)
    assert scenario.environment == "lab" and scenario.activation == "triggered"
    assert [r.role for r in scenario.roles] == ["lead", "theorist", "experimentalist",
                                                 "critic", "scribe"]
    assert all(r.agent_id == "alpha" for r in scenario.roles)
    assert sum(r.starts for r in scenario.roles) == 1
    assert scenario.max_ticks == 30
    # The template itself is untouched by a copy.
    assert TEMPLATES["lab"]["roles"][0]["agent_id"] == ""
    with pytest.raises(ValueError):
        scenario_from_template("lab")
    with pytest.raises(KeyError):
        scenario_from_template("nope", agent_id="alpha")


def test_the_template_tool_validates_like_create(monkeypatch):
    from agents.registry import AgentSpec
    import agents.registry as registry
    import common.workspace_context as wc
    import tools.scenario_management as sm

    specs = [AgentSpec(id="alpha", name="Alpha", type="langchain",
                       entrypoint="agents.agent_factory:build_agent_executor",
                       description="x")]
    monkeypatch.setattr(registry, "list_agents", lambda: list(specs))
    monkeypatch.setattr(wc, "resolve_active_workspace", lambda preferred=None: None)
    monkeypatch.setattr(sm, "resolve_active_workspace", lambda preferred=None: None)

    out = json.loads(sm.create_scenario_from_template_tool.invoke(
        {"template": "lab", "agent_id": "alpha",
         "env_params": {"question": "Is pi above 3?"}}))
    assert out["ok"], out
    stored = store.get_scenario(out["scenario_id"])
    assert stored.env_params["question"] == "Is pi above 3?"
    assert stored.env_params["max_experiments"] == 12
    bad = json.loads(sm.create_scenario_from_template_tool.invoke(
        {"template": "lab", "agent_id": "ghost"}))
    assert not bad["ok"]
    team = _team()
    by_team = json.loads(sm.create_scenario_from_template_tool.invoke(
        {"template": "lab", "team_id": team.team_id}))
    assert by_team["ok"], by_team
    assert by_team["scenario"]["team_id"] == team.team_id
    assert by_team["scenario"]["roles"] == []


# ── Routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import playground as playground_routes
    app = FastAPI()
    app.include_router(playground_routes.router)
    return TestClient(app)


def test_template_routes_list_and_create(client):
    listed = client.get("/api/playground/scenarios/templates")
    assert listed.status_code == 200
    assert listed.json()["templates"][0]["id"] == "lab"
    made = client.post("/api/playground/scenarios/from-template",
                       json={"template": "lab", "agent_id": "alpha", "workspace": "ws1"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["environment"] == "lab" and body["workspace"] == "ws1"
    assert len(body["roles"]) == 5
    assert client.post("/api/playground/scenarios/from-template",
                       json={"template": "lab"}).status_code == 400
    assert client.post("/api/playground/scenarios/from-template",
                       json={"template": "nope", "agent_id": "a"}).status_code == 404
    assert client.post("/api/playground/scenarios/from-template",
                       json={"template": "lab", "team_id": "team_missing"}).status_code == 400
    team = _team()
    by_team = client.post("/api/playground/scenarios/from-template",
                          json={"template": "lab", "team_id": team.team_id}).json()
    assert by_team["team_id"] == team.team_id and by_team["roles"] == []


def test_repeat_eval_creates_a_set_with_repeats(client, monkeypatch):
    import evals.runner as eval_runner
    from evals import store as eval_store
    from evals.models import EvalRun

    started = []

    def fake_run_eval(eval_set_id, configs, workspace=None, **_):
        run = EvalRun(eval_set_id=eval_set_id, workspace=workspace, configs=configs)
        eval_store.save_eval_run(run)
        started.append((eval_set_id, configs))
        return run

    monkeypatch.setattr(eval_runner, "run_eval", fake_run_eval)
    made = client.post("/api/playground/scenarios/from-template",
                       json={"template": "lab", "agent_id": "alpha"}).json()
    resp = client.post(f"/api/playground/scenarios/{made['scenario_id']}/repeat-eval",
                       json={"repeats": 4, "model": "m1", "provider": "p1"})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["eval_run_id"] and out["repeats"] == 4
    evalset = eval_store.get_eval_set(out["eval_id"])
    assert evalset.name == "Reproducibility: Research lab"
    assert evalset.target == {"kind": "scenario", "id": made["scenario_id"]}
    assert evalset.cases[0].input.startswith("For n up to 10000")
    assert evalset.graders[0].kind == "assertions"
    cfg = started[0][1][0]
    assert cfg.resolved_repeats() == 4 and cfg.target_kind == "scenario"
    assert cfg.model == "m1"
    assert client.post(f"/api/playground/scenarios/{made['scenario_id']}/repeat-eval",
                       json={"repeats": 99}).status_code == 400
    assert client.post("/api/playground/scenarios/scn_missing/repeat-eval",
                       json={"repeats": 2}).status_code == 404


def test_the_repeat_eval_graders_pass_on_a_lab_output():
    from evals.graders import grade_all
    from evals.models import Case, GraderSpec
    from evals.targets import render_scenario
    from routes.playground import _repeat_eval_graders  # noqa: F401 - path set by client fixture above

    env = lab()
    hid, _ = _hyp_and_experiment(env)
    env.budget["experiments_used"] = 1
    env.apply("Lead", "decide", {"hypothesis_id": hid, "verdict": "confirmed"})
    output = render_scenario(env.score(), env.state())
    specs = [GraderSpec.from_dict(g) for g in _repeat_eval_graders(
        Scenario(environment="lab"))]
    result = grade_all(output, Case(input="q"), specs)
    assert result[2], result


# ── The runner records the lab's own stop reason ─────────────────────────────

def test_a_run_that_decides_its_hypothesis_stops_with_that_reason(monkeypatch, snippet):
    """A scripted run: the lead proposes and decides on tick 1, so the lab is
    done and the run's stop reason is the lab's, not the generic "terminal"."""
    import playground.runner as runner
    from playground.models import AgentDecision

    scenario = store.save_scenario(Scenario(
        name="lab", environment="lab", env_params={"question": "q"},
        roles=[Role(agent_id="alpha", name="Lead", role="lead")], max_ticks=5,
    ))
    plan = iter([
        {"action": "propose_hypothesis", "args": {"text": "h"}},
        {"action": "decide", "args": {"hypothesis_id": "H1", "verdict": "confirmed"}},
        {"action": "write_up", "args": {"section": "Abstract", "text": "done"}},
    ])

    def fake_decisions(env, scenario, sim_run_id, tick, history, workspace, acting,
                       plan_, observations):
        # One tick does everything: resolve applies these in order.
        return [AgentDecision(agent="Lead", action=next(plan))]

    def fake_resolve(self, submissions):
        results = [self.apply("Lead", "propose_hypothesis", {"text": "h"}),
                   self.apply("Lead", "decide", {"hypothesis_id": "H1",
                                                 "verdict": "confirmed"}),
                   self.apply("Lead", "write_up", {"section": "Abstract", "text": "x"})]
        return results

    from playground.environments.lab import LabEnvironment
    monkeypatch.setattr(runner, "_run_decisions", fake_decisions)
    monkeypatch.setattr(LabEnvironment, "resolve", fake_resolve)
    run = runner.run_simulation(scenario.scenario_id)
    assert run.status == "completed"
    assert run.stop_reason == "hypotheses_decided"
    assert run.ticks_done == 1
    from views import store as vstore
    kinds = sorted(v["kind"] for v in vstore.views_owned_by("scenario", run.sim_run_id))
    assert kinds == ["document"]
    assert run.final_state["views"]["report"]


def test_eval_delivers_the_case_to_a_team_cast_scenario(monkeypatch):
    """A scenario cast only by a team has no roles on its record; the eval
    adapter must still deliver the case input to somebody, and the model
    override must reach the team's roles too."""
    import playground.runner as runner_mod
    import runtime.entity_heartbeat as hb_mod
    from evals.models import Case, EvalSet, RunConfig
    from evals.targets import run_scenario_target

    team = _team()
    scenario = store.save_scenario(Scenario(name="lab", environment="lab", team_id=team.team_id))

    delivered = []
    seen_config = {}

    class _Heartbeat:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    def fake_run_simulation(scenario_id, workspace=None, run=None, on_start=None, **_):
        seen_config.update(run.config)
        run.status = "completed"
        run.scores = {}
        run.final_state = {}
        run.total_cost = 0.0
        if on_start:
            on_start(run)
        return run

    monkeypatch.setattr(hb_mod, "EntityHeartbeat", _Heartbeat)
    monkeypatch.setattr(runner_mod, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(runner_mod, "trigger_agent",
                        lambda run_id, agent, text, sender="": delivered.append((agent, text)))

    cfg = RunConfig(target={"kind": "scenario", "id": scenario.scenario_id}, model="gpt-5")
    case = Case(case_id="c1", input="Is the mean near 0.5?")
    evalset = EvalSet(name="repro", cases=[case])
    outcome = run_scenario_target(case, cfg, evalset, "erun", None, prompt=case.input)

    assert outcome.ok
    # The team's leader (Ada) opens the scene and receives the case.
    assert delivered == [("Ada", "Is the mean near 0.5?")]
    # The frozen roster came from the team and carries the override.
    assert [r["name"] for r in seen_config["roles"]] == ["Ada", "beta", "beta 2"]
    assert {r["model"] for r in seen_config["roles"]} == {"gpt-5"}
