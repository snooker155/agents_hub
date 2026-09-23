"""
teams.launcher: the database half of a team run, and the shared envelope it
hands off to (runtime/entity_launch.py).

A team run used to be a daemon thread of the API process with no lease, no
heartbeat and nothing left after a restart. What is worth pinning here is the
seam: a pending record and a claimed task exist before any process does, the
spec handed to the launch envelope names the right entrypoint and carries
``--resume`` only on a resume, the ``api`` role queues rather than spawns, and
a resume refuses when there is nothing sensible to resume.
"""
from __future__ import annotations

import pytest

from common import entity_runs
from teams import control, store
from teams.launcher import TeamResumeError, resume_team_run, start_team_run, stop_team_run
from teams.models import Team, TeamMember


def _team(**kw):
    kw.setdefault("name", "Launch crew")
    kw.setdefault("mode", "parallel")
    kw.setdefault("members", [TeamMember(agent_id="swe", name="Sam", manifest="writes code")])
    return store.save_team(Team(**kw))


@pytest.fixture(autouse=True)
def no_real_spawn(monkeypatch):
    """Every test here drives the database half only — nothing may spawn a
    real subprocess. dispatch()'s default role calls launch straight through,
    so replacing teams.launcher.launch_prepared with a recorder is enough;
    a test that wants the api-role queuing path leaves dispatch itself alone."""
    calls: list = []
    monkeypatch.setattr("teams.launcher.launch_prepared", lambda spec: calls.append(spec))
    return calls


def test_start_team_run_creates_a_pending_record_and_dispatches_a_team_spec(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")

    assert run.status == "pending"
    assert run.team_id == team.team_id
    assert run.goal == "ship the page"
    assert run.log_file and "team_run_" in run.log_file
    # The task is claimed before any process exists (teams.runner._claim_task).
    assert run.task_id

    assert len(no_real_spawn) == 1
    spec = no_real_spawn[0]
    assert spec["kind"] == "team"
    assert spec["entity_id"] == team.team_id
    assert spec["run_id"] == run.team_run_id
    assert spec["entrypoint"] == "team_run"
    assert spec["cli_args"] == ["--run-id", run.team_run_id]
    assert spec["resume"] is False
    assert spec["log_file"] == run.log_file


def test_start_team_run_falls_back_to_the_teams_own_description(no_real_spawn):
    team = _team(description="keep the lights on")
    run = start_team_run(team.team_id, "")
    assert run.goal == "keep the lights on"


def test_start_team_run_refuses_a_team_with_no_leader_in_centralized_mode(no_real_spawn):
    team = _team(mode="centralized", leader_agent_id=None)
    with pytest.raises(ValueError, match="leader"):
        start_team_run(team.team_id, "go")
    assert no_real_spawn == []


def test_start_team_run_refuses_an_unknown_team(no_real_spawn):
    with pytest.raises(ValueError, match="not found"):
        start_team_run("team_nope", "go")


def test_the_api_role_enqueues_the_launch_instead_of_spawning(monkeypatch):
    """In the api role dispatch() puts the spec on run_queue for a worker to
    claim, rather than calling launch_prepared in this process at all."""
    from common import run_queue

    monkeypatch.setattr("common.config.hub_role", lambda: "api")
    team = _team()
    run = start_team_run(team.team_id, "ship the page")

    queued = run_queue.get(run.team_run_id)
    assert queued is not None
    assert queued["kind"] == "team"
    assert queued["status"] == "queued"
    assert queued["payload"]["entrypoint"] == "team_run"
    # Nothing spawned it — the record is exactly as start_team_run's own
    # database half left it.
    assert store.get_run(run.team_run_id).status == "pending"


# ── Resume ───────────────────────────────────────────────────────────────────

def test_resume_refuses_an_unknown_run(no_real_spawn):
    with pytest.raises(TeamResumeError, match="not found"):
        resume_team_run("trun_nope")


def test_resume_refuses_a_run_that_is_still_live(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    with pytest.raises(TeamResumeError, match="pending"):
        resume_team_run(run.team_run_id)


def test_resume_refuses_a_finished_run_with_no_checkpoint(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.close(run.team_run_id, status="stopped", exit_code=1)
    with pytest.raises(TeamResumeError, match="no checkpoint"):
        resume_team_run(run.team_run_id)


def test_resume_relaunches_a_stopped_run_with_its_checkpoint(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.save_checkpoint(run.team_run_id, {"round": 2, "state": {}, "spend": 0.01})
    entity_runs.close(run.team_run_id, status="stopped", exit_code=1)

    resumed = resume_team_run(run.team_run_id)
    assert resumed.team_run_id == run.team_run_id

    # One dispatch from start_team_run, one from this resume.
    assert len(no_real_spawn) == 2
    spec = no_real_spawn[-1]
    assert spec["resume"] is True
    assert spec["cli_args"] == ["--run-id", run.team_run_id, "--resume"]
    assert spec["header"] == "Team run resumed"
    assert spec["mode"] == "a"


def test_an_auto_resume_bumps_resume_attempts_a_manual_one_does_not(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.save_checkpoint(run.team_run_id, {"round": 1, "state": {}, "spend": 0.0})
    entity_runs.close(run.team_run_id, status="failed", exit_code=1)

    resume_team_run(run.team_run_id, auto=False)
    assert store.get_run(run.team_run_id).resume_attempts == 0

    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.close(run.team_run_id, status="failed", exit_code=1)
    resume_team_run(run.team_run_id, auto=True)
    assert store.get_run(run.team_run_id).resume_attempts == 1


# ── Stop ─────────────────────────────────────────────────────────────────────

def test_stop_team_run_marks_the_durable_status_and_the_in_memory_event(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    control.register(run.team_run_id)

    assert stop_team_run(run.team_run_id) is True
    assert store.get_run(run.team_run_id).status == "stopping"
    assert control.is_stopped(run.team_run_id)
    control.release(run.team_run_id)


def test_stop_team_run_on_a_finished_run_is_refused(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.close(run.team_run_id, status="completed", exit_code=0)
    assert stop_team_run(run.team_run_id) is False


# ── The HTTP surface ─────────────────────────────────────────────────────────

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.backend.routes.teams import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_run_route_returns_the_record(no_real_spawn):
    team = _team()
    resp = _client().post(f"/api/teams/{team.team_id}/run", json={"goal": "ship the page"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team_id"] == team.team_id
    assert body["status"] == "pending"
    assert body["goal"] == "ship the page"


def test_the_run_route_400s_on_an_invalid_team(no_real_spawn):
    team = _team(mode="centralized", leader_agent_id=None)
    resp = _client().post(f"/api/teams/{team.team_id}/run", json={"goal": "go"})
    assert resp.status_code == 400
    assert "leader" in resp.json()["detail"]


def test_the_resume_route_returns_the_relaunched_record(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.save_checkpoint(run.team_run_id, {"round": 1, "state": {}, "spend": 0.0})
    entity_runs.close(run.team_run_id, status="stopped", exit_code=1)

    resp = _client().post(f"/api/teams/runs/{run.team_run_id}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["team_run_id"] == run.team_run_id


def test_the_resume_route_400s_without_a_checkpoint(no_real_spawn):
    team = _team()
    run = start_team_run(team.team_id, "ship the page")
    entity_runs.mark_running(run.team_run_id, pid=1, host="h")
    entity_runs.close(run.team_run_id, status="stopped", exit_code=1)

    resp = _client().post(f"/api/teams/runs/{run.team_run_id}/resume")
    assert resp.status_code == 400
    assert "no checkpoint" in resp.json()["detail"]
