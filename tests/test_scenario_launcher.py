"""
playground.launcher — the database half of a scenario launch, and its
delegation to runtime.entity_launch for the process half.

Every test here fakes out the actual process spawn (``playground.launcher
.launch_prepared`` or, for the queue path, the ``api`` role's own dispatch),
per the ground rule for this suite: never spawn a real subprocess.
"""
import pytest

from playground import store
from playground.models import Role, Scenario, SimRun


def make_scenario(**kw):
    defaults = dict(name="s", environment="market",
                    roles=[Role(agent_id="a", name="Alice")], max_ticks=3)
    defaults.update(kw)
    return store.save_scenario(Scenario(**defaults))


@pytest.fixture
def fake_launch(monkeypatch):
    """Capture the spec a launch would have handed to
    ``runtime.entity_launch`` instead of actually spawning anything."""
    import playground.launcher as launcher
    captured = []
    monkeypatch.setattr(launcher, "launch_prepared", captured.append)
    return captured


# ── start_scenario_run ───────────────────────────────────────────────────────

def test_start_scenario_run_writes_a_pending_record(fake_launch):
    from playground.launcher import start_scenario_run

    s = make_scenario(roles=[Role(agent_id="a", name="Alice"),
                             Role(agent_id="b", name="Bob")])
    run = start_scenario_run(s.scenario_id)

    assert run.status == "pending"
    assert run.scenario_id == s.scenario_id
    # The scenario is frozen into the run's own config at launch, the same
    # way the thread-based path always did.
    assert [r["name"] for r in run.config["roles"]] == ["Alice", "Bob"]
    stored = store.get_sim_run(run.sim_run_id)
    assert stored is not None and stored.status == "pending"
    assert stored.log_file and stored.log_file.endswith(f"scenario_run_{run.sim_run_id}.log")


def test_start_scenario_run_dispatches_a_scenario_run_spec(fake_launch):
    from playground.launcher import start_scenario_run

    s = make_scenario(workspace="acme")
    run = start_scenario_run(s.scenario_id)

    assert len(fake_launch) == 1
    spec = fake_launch[0]
    assert spec["kind"] == "scenario"
    assert spec["run_id"] == run.sim_run_id
    assert spec["entity_id"] == s.scenario_id
    assert spec["entrypoint"] == "scenario_run"
    assert spec["cli_args"] == ["--run-id", run.sim_run_id]
    assert spec["workspace"] == "acme"
    assert spec["log_file"] == run.log_file
    assert spec["resume"] is False
    assert spec["execution_mode"] in ("local", "docker")


def test_start_scenario_run_validates_like_run_simulations_own_head(fake_launch):
    from playground.launcher import start_scenario_run

    empty = make_scenario(roles=[])
    with pytest.raises(ValueError, match="no roles"):
        start_scenario_run(empty.scenario_id)
    # Nothing was written for a scenario that never validated.
    assert store.list_sim_runs(empty.scenario_id) == []
    assert fake_launch == []

    dupes = make_scenario(roles=[Role(agent_id="a", name="Alice"),
                                 Role(agent_id="b", name="Alice")])
    with pytest.raises(ValueError, match="display name"):
        start_scenario_run(dupes.scenario_id)

    with pytest.raises(ValueError, match="not found"):
        start_scenario_run("no-such-scenario")


def test_api_role_enqueues_the_launch_with_kind_scenario(monkeypatch):
    """In the ``api`` role, the database half still runs here; only the
    process spawn moves to a worker (runtime/entity_launch.py, docs/workers.md).
    The launch spec is put on ``run_queue`` instead of being executed."""
    import common.config as config
    from common import db
    from playground.launcher import start_scenario_run

    monkeypatch.setattr(config, "hub_role", lambda: "api")
    s = make_scenario()
    run = start_scenario_run(s.scenario_id)

    row = db.get_conn().execute(
        "SELECT kind, run_id, workspace FROM run_queue WHERE run_id = ?",
        (run.sim_run_id,),
    ).fetchone()
    assert row is not None
    assert row["kind"] == "scenario"
    # Nothing was actually spawned — the run stays exactly where the database
    # half left it, pending, until a worker claims the queued row.
    assert store.get_sim_run(run.sim_run_id).status == "pending"


# ── resume_scenario_run ──────────────────────────────────────────────────────

def test_resume_refuses_a_completed_run(fake_launch):
    from playground.launcher import ScenarioResumeError, resume_scenario_run

    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="completed"))
    with pytest.raises(ScenarioResumeError, match="completed"):
        resume_scenario_run(run.sim_run_id)
    assert fake_launch == []


def test_resume_refuses_an_unknown_run(fake_launch):
    from playground.launcher import ScenarioResumeError, resume_scenario_run

    with pytest.raises(ScenarioResumeError, match="not found"):
        resume_scenario_run("no-such-run")


def test_resume_refuses_a_run_with_no_checkpoint(fake_launch):
    from playground.launcher import ScenarioResumeError, resume_scenario_run

    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="stopped"))
    with pytest.raises(ScenarioResumeError, match="checkpoint"):
        resume_scenario_run(run.sim_run_id)
    assert fake_launch == []


def test_resume_relaunches_a_stopped_run_under_the_same_id(fake_launch):
    from common import entity_runs
    from playground.launcher import resume_scenario_run

    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="stopped",
                                    workspace="acme"))
    entity_runs.save_checkpoint(run.sim_run_id, {"tick": 3, "env": {"pickled": ""}})

    resumed = resume_scenario_run(run.sim_run_id)

    assert resumed.sim_run_id == run.sim_run_id
    assert resumed.status == "running"
    assert store.get_sim_run(run.sim_run_id).status == "running"

    assert len(fake_launch) == 1
    spec = fake_launch[0]
    assert spec["run_id"] == run.sim_run_id
    assert spec["cli_args"] == ["--run-id", run.sim_run_id, "--resume"]
    assert spec["resume"] is True
    assert spec["mode"] == "a"
    assert spec["header"] == "Scenario run resumed"
    assert spec["workspace"] == "acme"


def test_resume_increments_resume_attempts_only_when_auto(fake_launch):
    from common import entity_runs
    from playground.launcher import resume_scenario_run

    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="failed"))
    entity_runs.save_checkpoint(run.sim_run_id, {"tick": 1, "env": {}})

    manual = resume_scenario_run(run.sim_run_id, auto=False)
    assert manual.resume_attempts == 0

    store.RUNS.update(run.sim_run_id, {"status": "failed"})
    automatic = resume_scenario_run(run.sim_run_id, auto=True)
    assert automatic.resume_attempts == 1


# ── stop / trigger delegation ────────────────────────────────────────────────

def test_stop_scenario_run_delegates_to_the_durable_stop():
    from playground.launcher import stop_scenario_run

    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="running"))
    assert stop_scenario_run(run.sim_run_id) is True
    assert store.stop_requested(run.sim_run_id)
    # A finished run has nothing to stop.
    done = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="completed"))
    assert stop_scenario_run(done.sim_run_id) is False
    assert stop_scenario_run("no-such-run") is False


def test_trigger_scenario_run_pushes_durably():
    from playground.launcher import trigger_scenario_run

    s = make_scenario()
    run = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="running"))
    assert trigger_scenario_run(run.sim_run_id, "Alice", "hello", sender="ops")

    pending = store.drain_triggers(run.sim_run_id)
    assert pending == [{"agent": "Alice", "text": "hello", "sender": "ops"}]

    # A finished run does not accept a poke.
    done = store.save_sim_run(SimRun(scenario_id=s.scenario_id, status="completed"))
    assert not trigger_scenario_run(done.sim_run_id, "Alice", "hi")
