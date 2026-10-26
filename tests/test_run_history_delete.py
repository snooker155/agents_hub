"""Deleting one run from a loop's or a team's history (DELETE
/api/loops/runs/{id}, /api/teams/runs/{id}): the record goes with what only
it owns (a loop's iterations, a team's board), a run still going is refused,
and the other runs stay."""
from __future__ import annotations

import sys
from pathlib import Path

from loops import store as loop_store
from loops.models import Iteration, Loop, LoopRun
from teams import store as team_store
from teams.models import Team, TeamMember, TeamMessage, TeamRun


def _client(module: str):
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import importlib
    app = FastAPI()
    app.include_router(importlib.import_module(f"dashboard.backend.routes.{module}").router)
    return TestClient(app)


def test_a_loop_run_is_deleted_with_its_iterations_and_a_live_one_is_refused():
    loop = loop_store.save_loop(Loop(name="Polish", flow_id="f", exit_criterion="reads well"))
    done = LoopRun(loop_id=loop.loop_id, goal="one", status="completed")
    live = LoopRun(loop_id=loop.loop_id, goal="two", status="running")
    loop_store.save_run(done)
    loop_store.save_run(live)
    loop_store.save_iteration(Iteration(loop_run_id=done.loop_run_id, iteration=1, flow_run_id="fr1"))
    client = _client("loops")

    assert client.delete(f"/api/loops/runs/{live.loop_run_id}").status_code == 409
    assert client.delete(f"/api/loops/runs/{done.loop_run_id}").json() == {"deleted": True}
    assert loop_store.get_run(done.loop_run_id) is None
    assert loop_store.list_iterations(done.loop_run_id) == []
    assert loop_store.get_run(live.loop_run_id) is not None
    assert client.delete(f"/api/loops/runs/{done.loop_run_id}").status_code == 404


def test_a_team_run_is_deleted_with_its_board_and_a_live_one_is_refused():
    team = team_store.save_team(Team(name="Drinks", mode="parallel",
                                     members=[TeamMember(agent_id="writer", name="Writer")]))
    done = TeamRun(team_id=team.team_id, goal="one", status="completed")
    live = TeamRun(team_id=team.team_id, goal="two", status="running")
    team_store.save_run(done)
    team_store.save_run(live)
    team_store.append_message(TeamMessage(team_run_id=done.team_run_id, round=0, sender="(request)",
                                          kind="goal", content="one"))
    client = _client("teams")

    assert client.delete(f"/api/teams/runs/{live.team_run_id}").status_code == 409
    assert client.delete(f"/api/teams/runs/{done.team_run_id}").json() == {"deleted": True}
    assert team_store.get_run(done.team_run_id) is None
    assert team_store.list_messages(done.team_run_id) == []
    assert team_store.get_run(live.team_run_id) is not None
    assert client.delete(f"/api/teams/runs/{done.team_run_id}").status_code == 404
