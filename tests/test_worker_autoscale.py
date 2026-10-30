"""
Worker autoscaling (common/worker_autoscale.py): more workers when launches
wait with no free slot, fewer when one has been idle a while, inside the
page's bounds, one action per cooldown; and the Cluster routes that start,
stop and scale workers (dashboard/backend/routes/deployment.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from common import worker_autoscale as wa
from common import worker_fleet

POLICY = {**wa.DEFAULTS, "enabled": True, "min_workers": 1, "max_workers": 3,
          "scale_up_wait_seconds": 30, "scale_down_idle_minutes": 10, "cooldown_seconds": 60}
NOTHING = {"runs": 0, "carriers": 0, "oldest_seconds": 0.0}


def w(mid, runs=0, conc=2, carriers=0, origin="compose", draining=False):
    return {"member_id": mid, "runs": runs, "concurrency": conc, "carriers": carriers,
            "origin": origin, "draining": draining, "stoppable": carriers == 0}


def test_starts_up_to_the_minimum():
    assert wa.decide({**POLICY, "min_workers": 2}, [w("a")], NOTHING, {}) == {
        "action": "up", "count": 1, "reason": "below_min"}


def test_starts_what_the_queue_needs_when_every_slot_is_taken():
    busy = [w("a", runs=2)]
    waiting = {"runs": 5, "carriers": 0, "oldest_seconds": 45}
    assert wa.decide(POLICY, busy, waiting, {}) == {"action": "up", "count": 2, "reason": "queue_waiting"}
    # Not yet: the wait is short, or a slot is free.
    assert wa.decide(POLICY, busy, {**waiting, "oldest_seconds": 5}, {})["action"] is None
    assert wa.decide(POLICY, [w("a", runs=1)], waiting, {})["action"] is None
    # Not past the maximum.
    full = [w("a", runs=2), w("b", runs=2), w("c", runs=2)]
    assert wa.decide(POLICY, full, waiting, {}) == {"action": None, "reason": "at_max"}


def test_an_instance_waiting_with_no_worker_starts_one():
    assert wa.decide({**POLICY, "min_workers": 0}, [], {"runs": 0, "carriers": 1, "oldest_seconds": 0}, {}) == {
        "action": "up", "count": 1, "reason": "instances_waiting"}


def test_stops_an_idle_worker_the_hubs_first_never_below_the_minimum():
    workers = [w("compose-1"), w("hub-1", origin="hub"), w("hub-2", origin="hub")]
    idle = {"compose-1": 3600.0, "hub-1": 700.0, "hub-2": 900.0}
    assert wa.decide(POLICY, workers, NOTHING, idle) == {"action": "down", "member_id": "hub-2", "reason": "idle"}
    # Not idle long enough, or carrying instances, or something waits.
    assert wa.decide(POLICY, workers, NOTHING, {k: 60.0 for k in idle})["action"] is None
    carrying = [w("a", carriers=1), w("b", carriers=2)]
    assert wa.decide(POLICY, carrying, NOTHING, {"a": 9e9, "b": 9e9})["action"] is None
    assert wa.decide(POLICY, workers, {"runs": 1, "carriers": 0, "oldest_seconds": 1}, idle)["action"] is None
    assert wa.decide(POLICY, [w("a")], NOTHING, {"a": 9e9})["action"] is None
    # Nor while another is still draining.
    assert wa.decide(POLICY, [*workers, w("x", draining=True)], NOTHING, idle)["action"] is None


def test_policy_bounds():
    with pytest.raises(ValueError):
        wa.save_policy({"min_workers": 5, "max_workers": 2})
    with pytest.raises(ValueError):
        wa.save_policy({"cooldown_seconds": 1})
    saved = wa.save_policy({"enabled": True, "max_workers": "6", "unknown": 1})
    assert saved["enabled"] is True and saved["max_workers"] == 6 and "unknown" not in saved
    assert wa.policy()["max_workers"] == 6


def test_tick_acts_once_per_cooldown_and_keeps_its_events(monkeypatch):
    wa.save_policy({**POLICY, "min_workers": 2})
    started = []
    monkeypatch.setattr(worker_fleet, "workers", lambda: [w("a")])
    monkeypatch.setattr(worker_fleet, "add", lambda n: started.append(n) or [f"c{len(started)}"])
    monkeypatch.setattr(wa, "queue_waiting", lambda: NOTHING)
    scaler = wa.Autoscaler()
    assert scaler.tick(now=1000.0)["action"] == "up"
    scaler.tick(now=1030.0)   # inside the cooldown
    assert started == [1]
    scaler.tick(now=1100.0)
    assert started == [1, 1]
    assert [e["action"] for e in wa.events()] == ["up", "up"]
    assert wa.status()["last_decision"]["reason"] == "below_min"


def test_tick_does_nothing_while_off(monkeypatch):
    wa.save_policy({"enabled": False, "min_workers": 3})
    monkeypatch.setattr(worker_fleet, "workers", lambda: [])
    monkeypatch.setattr(worker_fleet, "add", lambda n: pytest.fail("started a worker while off"))
    assert wa.Autoscaler().tick(now=1.0) == {"action": None, "reason": "off"}


def test_a_failed_start_is_an_event(monkeypatch):
    wa.save_policy({**POLICY, "min_workers": 1})
    monkeypatch.setattr(worker_fleet, "workers", lambda: [])
    monkeypatch.setattr(wa, "queue_waiting", lambda: NOTHING)

    def boom(n):
        raise worker_fleet.FleetError("no docker")
    monkeypatch.setattr(worker_fleet, "add", boom)
    wa.Autoscaler().tick(now=1.0)
    assert wa.events()[0]["action"] == "error" and "no docker" in wa.events()[0]["error"]


def test_queue_waiting_counts_runs_and_carriers():
    from common import run_queue
    run_queue.enqueue("r1", "task", {})
    run_queue.enqueue("r2", "flow", {})
    run_queue.enqueue("i1", "instance", {})
    out = wa.queue_waiting()
    assert out["runs"] == 2 and out["carriers"] == 1 and out["oldest_seconds"] >= 0


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import deployment
    app = FastAPI()
    app.include_router(deployment.router)
    app.dependency_overrides[deployment._require_operator] = lambda: None
    monkeypatch.setattr(worker_fleet, "_docker_reachable", lambda: False)
    return TestClient(app)


def test_routes(client, monkeypatch):
    from common import members
    monkeypatch.setattr(worker_fleet, "add", lambda n: [f"w{i}" for i in range(n)])
    assert client.post("/api/cluster/workers", json={"count": 2}).json() == {"started": ["w0", "w1"]}
    assert client.post("/api/cluster/workers", json={"count": 50}).status_code == 400

    members.register("worker", member_id="w-1")
    overview = client.get("/api/cluster/workers").json()
    assert [x["member_id"] for x in overview["workers"]] == ["w-1"]
    assert overview["autoscale"]["policy"]["enabled"] is False

    assert client.post("/api/cluster/workers/w-1/stop").json()["draining"] is True
    assert client.get("/api/cluster/workers").json()["workers"][0]["draining"] is True
    assert client.delete("/api/cluster/workers/w-1/stop").json()["draining"] is False
    assert client.post("/api/cluster/workers/nobody/stop").status_code == 404

    from instances import store
    store.create("helper", kind="runner", carrier_host=members.get("w-1")["host"], carrier_status="running")
    busy = client.post("/api/cluster/workers/w-1/stop")
    assert busy.status_code == 409 and busy.json()["detail"]["carriers"] == 1

    assert client.put("/api/cluster/autoscale", json={"enabled": True, "max_workers": 5}).json()["max_workers"] == 5
    assert client.put("/api/cluster/autoscale", json={"min_workers": 9, "max_workers": 2}).status_code == 400
