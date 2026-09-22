"""
Run groups: one interface over flows, loops, teams and task containers.

Four stores, four shapes, four stop functions — and every caller that wanted
"what is running and what did it cost" had to know all four. These tests pin the
one thing the abstraction promises: whichever kind you ask about, you get the
same fields, the cost is the catalog-priced sum of the group's own agent runs,
and stopping it goes through the store that already knew how.

The adapters are readers over the existing stores, so the fixtures here write
through those stores rather than into the tables directly: if a store changes
where it keeps a field, these tests should notice.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from flow import run_store as flow_run_store
from managers import run_manager as rm
from managers.runs import groups as run_groups

PRICES = {("openai", "gpt-4o"): (10.0, 30.0, 1.0)}


@pytest.fixture(autouse=True)
def priced(monkeypatch):
    """A tiny price catalog, so cost assertions are arithmetic and not a
    reflection of whatever the machine's model catalog happens to hold."""
    import common.pricing as pricing
    monkeypatch.setattr(pricing, "load_price_map", lambda: PRICES)


def _agent_run(run_id, *, inbound=0, outbound=0, **extra):
    """One finished agent run, priced. 1M inbound tokens costs 10.0 here."""
    rm.upsert_run({
        "run_id": run_id, "agent_id": "writer", "status": "completed",
        "provider": "openai", "model": "gpt-4o",
        "process": {"token_usage": {"inbound_tokens": inbound,
                                    "outbound_tokens": outbound,
                                    "total_tokens": inbound + outbound}},
        **extra,
    })
    return run_id


# ── Flow groups ───────────────────────────────────────────────────────────────

def _a_flow_run(flow_run_id="fr-1", flow_id="flow-a", workspace="ws"):
    flow_run_store.open_flow_run(flow_run_id, flow_id, workspace=workspace,
                                 title="nightly build")
    flow_run_store.mark_running(flow_run_id, pid=0)
    return flow_run_id


def test_a_flow_execution_reads_as_a_group_of_its_node_runs():
    _a_flow_run()
    _agent_run("run-1", inbound=1_000_000, flow_run_id="fr-1")
    _agent_run("run-2", outbound=1_000_000, flow_run_id="fr-1")
    _agent_run("run-3", inbound=1_000_000, flow_run_id="fr-other")

    group = run_groups.get_group("flow", "fr-1")
    assert group.kind == "flow"
    assert group.status == "running"
    assert group.active
    assert group.workspace == "ws"
    assert group.title == "nightly build"
    assert group.parent_id == "flow-a"
    assert sorted(group.children) == ["run-1", "run-2"]
    # Another execution's run is not this group's spend.
    assert group.total_cost == pytest.approx(40.0)


def test_group_cost_prices_only_the_groups_own_runs():
    _a_flow_run()
    _agent_run("run-1", inbound=500_000, flow_run_id="fr-1")
    assert run_groups.group_cost("flow", "fr-1") == pytest.approx(5.0)
    assert run_groups.group_cost("flow", "no-such-run") == 0.0


def test_cached_tokens_are_priced_as_cached_in_a_group_too():
    """The group must not reimplement the price rule, only sum it."""
    _a_flow_run()
    rm.upsert_run({
        "run_id": "run-c", "agent_id": "writer", "status": "completed",
        "provider": "openai", "model": "gpt-4o", "flow_run_id": "fr-1",
        "process": {"token_usage": {"inbound_tokens": 1_000_000,
                                    "cached_tokens": 1_000_000}},
    })
    # All inbound served from cache: 1.0 per million, not 10.0.
    assert run_groups.group_cost("flow", "fr-1") == pytest.approx(1.0)


def test_stopping_a_flow_group_closes_the_record_and_its_node_runs():
    _a_flow_run()
    rm.upsert_run({"run_id": "run-1", "agent_id": "writer", "status": "running",
                   "pid": 0, "in_process": True, "flow_run_id": "fr-1"})

    assert run_groups.stop_group("flow", "fr-1") is True
    assert flow_run_store.get_flow_run("fr-1")["status"] == "stopped"
    assert rm.get_run_by_id("run-1")["status"] == "stop"


def test_stopping_an_already_finished_flow_group_reports_nothing_to_stop():
    _a_flow_run("fr-done")
    flow_run_store.close_flow_run("fr-done", status="completed")
    assert run_groups.stop_group("flow", "fr-done") is False


# ── Loop groups ───────────────────────────────────────────────────────────────

def _a_loop_run(loop_run_id="lrun-1", workspace="ws"):
    from loops import store as loop_store
    from loops.models import Iteration, LoopRun
    run = LoopRun(loop_run_id=loop_run_id, loop_id="loop-a", workspace=workspace,
                  goal="make it good", status="running")
    loop_store.save_run(run)
    for n, frid in enumerate(("fr-i1", "fr-i2"), start=1):
        flow_run_store.open_flow_run(frid, "loop-flow", workspace=workspace)
        loop_store.save_iteration(Iteration(loop_run_id=loop_run_id, iteration=n,
                                            flow_run_id=frid, status="completed"))
    return run


def test_a_loop_runs_children_are_the_flow_runs_of_its_iterations():
    _a_loop_run()
    _agent_run("run-i1", inbound=1_000_000, flow_run_id="fr-i1")
    _agent_run("run-i2", inbound=2_000_000, flow_run_id="fr-i2")

    group = run_groups.get_group("loop", "lrun-1")
    assert group.kind == "loop"
    assert group.children == ["fr-i1", "fr-i2"]
    assert group.title == "make it good"
    assert group.parent_id == "loop-a"
    # A loop's spend is the sum of its iterations' flow groups.
    assert group.total_cost == pytest.approx(30.0)


def test_a_loop_is_stopped_through_its_own_stores_stop_request():
    from loops import store as loop_store
    _a_loop_run()
    assert run_groups.stop_group("loop", "lrun-1") is True
    assert loop_store.stop_requested("lrun-1") is True


# ── Team groups ───────────────────────────────────────────────────────────────

def _a_team_run(team_run_id="trun-1", workspace="ws"):
    from teams import store as team_store
    from teams.models import TeamMessage, TeamRun
    run = TeamRun(team_run_id=team_run_id, team_id="team-a", workspace=workspace,
                  goal="ship it", status="running")
    team_store.save_run(run)
    for run_id in ("run-t1", "run-t2"):
        team_store.append_message(TeamMessage(
            team_run_id=team_run_id, round=1, sender="analyst",
            kind="message", content="done", run_id=run_id))
    return run


def test_a_team_runs_children_are_the_member_turns_it_recorded():
    """A member run records its team, not which execution it belonged to; the
    message bus is what ties a turn to one team run."""
    _a_team_run()
    _agent_run("run-t1", inbound=1_000_000)
    _agent_run("run-t2", inbound=1_000_000)
    _agent_run("run-elsewhere", inbound=9_000_000)

    group = run_groups.get_group("team", "trun-1")
    assert group.children == ["run-t1", "run-t2"]
    assert group.total_cost == pytest.approx(20.0)
    assert group.title == "ship it"
    assert group.parent_id == "team-a"


def test_a_team_is_stopped_through_its_own_stores_stop_request():
    from teams import store as team_store
    _a_team_run()
    assert run_groups.stop_group("team", "trun-1") is True
    assert team_store.stop_requested("trun-1") is True


# ── Container groups ──────────────────────────────────────────────────────────

def _a_container(workspace="ws"):
    from tasks import service as ts
    parent = ts.create_task(title="Ship the feature", workspace=workspace)
    child = ts.add_subtask(parent.id, title="Write the code")
    return parent, child


def test_a_task_container_reads_as_a_group_of_its_subtasks_runs():
    parent, child = _a_container()
    _agent_run("run-sub", inbound=1_000_000, task_id=str(child.id))
    _agent_run("run-parent", inbound=1_000_000, task_id=str(parent.id))
    _agent_run("run-unrelated", inbound=5_000_000, task_id=str(uuid4()))

    group = run_groups.get_group("container", str(parent.id))
    assert group.kind == "container"
    assert sorted(group.children) == ["run-parent", "run-sub"]
    assert group.total_cost == pytest.approx(20.0)
    assert group.title == "Ship the feature"
    assert group.workspace == "ws"
    # Timestamps come out of the task store as datetimes; the group hands back
    # the same ISO strings every other kind does.
    assert isinstance(group.started_at, str)


def test_stopping_a_container_pauses_it_through_the_task_service(no_launch):
    from tasks import service as ts
    parent, child = _a_container()
    assert run_groups.stop_group("container", str(parent.id)) is True
    assert ts.get_task(parent.id).status == ts.TaskStatus.stopped


# ── The shared surface ────────────────────────────────────────────────────────

def test_listing_covers_every_kind_and_can_be_narrowed_to_one():
    _a_flow_run()
    _a_loop_run()
    _a_team_run()
    parent, _child = _a_container()

    kinds = {g.kind for g in run_groups.list_groups()}
    assert kinds == {"flow", "loop", "team", "container"}

    only_flows = run_groups.list_groups(kind="flow")
    assert only_flows and all(g.kind == "flow" for g in only_flows)


def test_listing_filters_by_workspace():
    _a_flow_run("fr-ws1", workspace="alpha")
    _a_flow_run("fr-ws2", workspace="beta")
    ids = [g.id for g in run_groups.list_groups(kind="flow", workspace="alpha")]
    assert ids == ["fr-ws1"]


def test_an_unknown_kind_is_rejected_rather_than_silently_empty():
    for call in (lambda: run_groups.get_group("nope", "x"),
                 lambda: run_groups.list_groups(kind="nope"),
                 lambda: run_groups.stop_group("nope", "x")):
        with pytest.raises(ValueError, match="Unknown run group kind"):
            call()


def test_an_unknown_id_is_none_for_every_kind():
    for kind in run_groups.KINDS:
        assert run_groups.get_group(kind, str(uuid4())) is None


def test_every_kind_answers_with_the_same_fields():
    """The point of the abstraction: a caller reads one shape, whatever the
    group is."""
    _a_flow_run()
    _a_loop_run()
    _a_team_run()
    _a_container()
    expected = {"kind", "id", "status", "started_at", "finished_at", "total_cost",
                "error", "children", "workspace", "title", "parent_id", "active"}
    for group in run_groups.list_groups():
        assert set(group.to_dict()) == expected


def test_the_group_object_can_stop_itself():
    _a_flow_run("fr-self")
    group = run_groups.get_group("flow", "fr-self")
    assert group.stop() is True
    assert flow_run_store.get_flow_run("fr-self")["status"] == "stopped"


# ── The API ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import run_groups as route_module
    app = FastAPI()
    app.include_router(route_module.router)
    return TestClient(app)


def test_the_route_lists_groups_and_narrows_by_kind_and_workspace(client):
    _a_flow_run("fr-ws1", workspace="alpha")
    _a_flow_run("fr-ws2", workspace="beta")
    _a_team_run()

    body = client.get("/api/runs/groups").json()
    assert {g["kind"] for g in body["groups"]} == {"flow", "team"}
    assert body["kinds"] == list(run_groups.KINDS)

    narrowed = client.get("/api/runs/groups", params={"kind": "flow",
                                                      "workspace": "alpha"}).json()
    assert [g["id"] for g in narrowed["groups"]] == ["fr-ws1"]


def test_the_route_returns_one_group_with_its_children_and_cost(client):
    _a_flow_run()
    _agent_run("run-1", inbound=1_000_000, flow_run_id="fr-1")

    body = client.get("/api/runs/groups/flow/fr-1").json()
    assert body["id"] == "fr-1"
    assert body["children"] == ["run-1"]
    assert body["total_cost"] == pytest.approx(10.0)
    assert body["active"] is True


def test_the_route_stops_a_group(client):
    _a_flow_run("fr-stop")
    assert client.post("/api/runs/groups/flow/fr-stop/stop").json()["stopped"] is True
    assert flow_run_store.get_flow_run("fr-stop")["status"] == "stopped"


def test_the_route_answers_404_for_an_unknown_group_and_400_for_an_unknown_kind(client):
    assert client.get("/api/runs/groups/flow/nope").status_code == 404
    assert client.get("/api/runs/groups/nope/x").status_code == 400
    assert client.get("/api/runs/groups", params={"kind": "nope"}).status_code == 400
