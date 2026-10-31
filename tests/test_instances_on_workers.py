"""With workers (the backend in the ``api`` role) no agent runs on the
backend: an instance's carrier, a resident copy or a service replica, is a
launch of kind ``instance`` on the run queue, and a worker spawns it on its
own host (instances/carrier.py, runtime/worker.py, docs/workers.md).

Before this every carrier was a child of the API process: the agents' load
sat on the API container and workers on other hosts never got one.
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from common import db, run_queue
from instances import carrier, registry, store
from runtime.worker import Worker


class _Spec(SimpleNamespace):
    def is_remote(self) -> bool:
        return False


@pytest.fixture
def fake_agent(monkeypatch):
    spec = _Spec(id="swe_agent", name="SWE")
    monkeypatch.setattr("agents.registry.get_agent", lambda agent_id: spec if agent_id == "swe_agent" else None)
    return spec


@pytest.fixture
def popen(monkeypatch):
    calls = []

    class _Proc:
        pid = 434343

    def _popen(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr(carrier.subprocess, "Popen", _popen)
    monkeypatch.setattr("common.config.agent_execution_mode", lambda workspace=None: "local")
    return calls


@pytest.fixture
def api_role(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_ROLE", "api")


def _as_worker(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_ROLE", "worker")
    return Worker(concurrency=2, modes=["local", "docker"], owner="worker-test")


def test_the_api_queues_the_carrier_instead_of_spawning_it(fake_agent, popen, api_role):
    inst = carrier.start("swe_agent", workspace="default")
    assert popen == []
    assert inst["carrier_status"] == "queued" and inst["state"] == "starting"
    row = run_queue.get(inst["instance_id"])
    assert row["kind"] == "instance" and row["status"] == "queued" and row["execution_mode"] == "local"
    # Waiting for a worker is not a dead carrier.
    assert carrier.process_alive(inst)
    assert carrier.sync(store.get(inst["instance_id"]))["carrier_status"] == "queued"


def test_a_worker_spawns_it_on_its_own_host_outside_its_run_slots(fake_agent, popen, api_role, monkeypatch):
    inst = carrier.start("swe_agent", workspace="default")
    worker = _as_worker(monkeypatch)
    assert worker.tick() == 1
    assert len(popen) == 1 and "runtime.instance_run" in popen[0]
    after = store.get(inst["instance_id"])
    assert after["carrier_status"] == "starting" and after["carrier_host"] == socket.gethostname()
    assert after["pid"] == 434343
    # The row only asked for the process; the carrier takes no run slot.
    assert run_queue.get(inst["instance_id"])["status"] == run_queue.STATUS_DONE
    assert worker.tracked == {}
    assert worker.tick() == 0 and len(popen) == 1


def test_stopping_a_queued_carrier_withdraws_its_launch(fake_agent, popen, api_role, monkeypatch):
    inst = carrier.start("swe_agent", workspace="default")
    assert carrier.stop(inst["instance_id"])
    assert run_queue.get(inst["instance_id"]) is None
    assert store.get(inst["instance_id"])["state"] == "stopped"
    _as_worker(monkeypatch).tick()
    assert popen == []


def test_a_launch_no_worker_could_start_fails_the_instance(fake_agent, popen, api_role):
    inst = carrier.start("swe_agent", workspace="default")
    run_queue.finish(inst["instance_id"], error="RuntimeError: no docker here")
    synced = carrier.sync(store.get(inst["instance_id"]))
    assert synced["state"] == "failed" and "no docker here" in synced["carrier_error"]


def test_a_restart_waits_for_the_old_carrier_on_another_host(fake_agent, popen, api_role, monkeypatch):
    base = registry.ensure_instance("swe_agent", kind=store.RESIDENT_KIND, workspace="default", state="standby")
    iid = base["instance_id"]
    now = datetime.now(timezone.utc).isoformat()
    store.update(iid, carrier_status="running", carrier_mode="local", carrier_host="other-host",
                 pid=111, heartbeat_at=now, carrier_started_at=now)

    carrier.restart(iid)
    current = store.get(iid)
    assert current["stop_requested_at"] and current["carrier_status"] == "stopping"
    assert run_queue.get(iid)["payload"]["after_stop"] is True

    worker = _as_worker(monkeypatch)
    worker.tick()
    assert popen == []            # the old one still beats: wait
    row = run_queue.get(iid)
    assert row["status"] == run_queue.STATUS_LEASED and int(row["attempts"]) == 0

    # The old carrier sees the request and stops; the deferral runs out.
    carrier.update_from_process(iid, "stopped", exit_code=0)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE run_queue SET lease_until = ? WHERE run_id = ?", (past, iid))
    worker.tick()
    assert len(popen) == 1
    assert store.get(iid)["carrier_host"] == socket.gethostname()


def test_without_workers_the_backend_spawns_as_before(fake_agent, popen, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_ROLE", "all")
    inst = carrier.start("swe_agent", workspace="default")
    assert len(popen) == 1 and run_queue.get(inst["instance_id"]) is None


def test_a_child_that_reports_running_first_is_not_set_back_to_starting(fake_agent, monkeypatch):
    """A carrier booting fast marks itself running before Popen returns in
    the spawning process; that used to be overwritten with ``starting`` and
    the instance stayed starting for good."""
    monkeypatch.setenv("AGENTS_HUB_ROLE", "all")
    monkeypatch.setattr("common.config.agent_execution_mode", lambda workspace=None: "local")

    class _Proc:
        pid = 454545

    def _popen(cmd, **kwargs):
        iid = cmd[cmd.index("--instance-id") + 1]
        assert store.get(iid)["carrier_status"] == "starting"   # written before the process
        carrier.update_from_process(iid, "running")              # the child, already up
        return _Proc()

    monkeypatch.setattr(carrier.subprocess, "Popen", _popen)
    inst = carrier.start("swe_agent", workspace="default")
    after = store.get(inst["instance_id"])
    assert after["carrier_status"] == "running" and after["state"] == "standby" and after["pid"] == 454545


# ── why a carrier stopped ────────────────────────────────────────────────────

def _carried_elsewhere(**fields):
    base = registry.ensure_instance("swe_agent", kind=store.RESIDENT_KIND, workspace="default", state="standby")
    now = datetime.now(timezone.utc).isoformat()
    row = {"carrier_status": "running", "carrier_mode": "local", "carrier_host": "worker-b",
           "pid": 111, "heartbeat_at": now, "carrier_started_at": now,
           "carrier_log_file": str(carrier.CARRIER_LOGS_DIR / f"instance_{base['instance_id']}.log"),
           **fields}
    return store.update(base["instance_id"], **row)


def test_a_stop_on_another_host_carries_its_reason_to_the_process(api_role):
    inst = _carried_elsewhere()
    assert carrier.stop(inst["instance_id"], reason="service: idle for 612 s, over the 600 s limit")
    row = store.get(inst["instance_id"])
    assert row["stop_reason"] == "service: idle for 612 s, over the 600 s limit"
    log = open(row["carrier_log_file"]).read()
    assert "idle for 612 s" in log and "worker-b" in log
    # The process reads it on its way out and ends with it, not a bare "stopped".
    from runtime import instance_run
    reason = instance_run._stop_reason(inst["instance_id"])
    carrier.update_from_process(inst["instance_id"], "stopped", exit_code=0, error=reason)
    after = store.get(inst["instance_id"])
    assert after["state"] == "stopped" and after["last_activity"].startswith("service: idle for 612 s")
    assert "idle for 612 s" in open(row["carrier_log_file"]).read().split("[carrier_state]")[-1]


def test_a_stop_from_outside_the_hub_says_so():
    from runtime import instance_run
    inst = _carried_elsewhere()
    assert "outside the hub" in instance_run._stop_reason(inst["instance_id"])


def test_a_carrier_whose_heartbeat_stopped_is_closed_with_the_reason(api_role):
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    inst = _carried_elsewhere()
    store.update(inst["instance_id"], heartbeat_at=old, carrier_started_at=old)
    synced = carrier.sync(store.get(inst["instance_id"]))
    assert synced["state"] == "stopped"
    assert "no heartbeat" in synced["last_activity"] and "worker-b" in synced["last_activity"]


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_a_container_being_started_on_another_host_is_not_written_off(api_role):
    # docker run on the worker, maybe pulling the image: no container yet
    # and no heartbeat, but the worker beats while it starts it.
    inst = _carried_elsewhere(carrier_status="starting", carrier_mode="docker", pid=None,
                              heartbeat_at=None, carrier_started_at=_ago(300),
                              container_name="agents-hub-node-x", spawn_beat_at=_ago(5))
    assert carrier.process_alive(inst)
    assert carrier.sync(store.get(inst["instance_id"]))["carrier_status"] == "starting"
    # Its host stopped beating (the worker died mid-start): written off.
    store.update(inst["instance_id"], spawn_beat_at=_ago(200))
    synced = carrier.sync(store.get(inst["instance_id"]))
    assert synced["state"] == "stopped" and "never reported in" in synced["last_activity"]


def test_a_long_docker_run_leaves_the_carrier_its_heartbeat_window(api_role):
    # Started 5 min ago, docker run just returned: the last spawn beat stands
    # in for the heartbeat it has not sent yet.
    inst = _carried_elsewhere(carrier_status="running", carrier_mode="docker", pid=None,
                              heartbeat_at=None, carrier_started_at=_ago(300),
                              container_name="agents-hub-node-x", spawn_beat_at=_ago(40))
    assert carrier.process_alive(inst)


def test_a_respawn_clears_the_previous_carriers_heartbeat(fake_agent, popen, api_role, monkeypatch):
    inst = carrier.start("swe_agent", workspace="default")
    store.update(inst["instance_id"], heartbeat_at=_ago(600))
    _as_worker(monkeypatch).tick()
    after = store.get(inst["instance_id"])
    assert after.get("heartbeat_at") is None and after.get("spawn_beat_at")
    assert carrier.process_alive({**after, "carrier_host": "worker-b"})
