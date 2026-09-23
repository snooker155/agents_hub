"""The members registry (common/members.py) and the deployment map
(dashboard/backend/routes/deployment.py)."""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from common import db, leases, members

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture(autouse=True)
def _owner(tmp_path, monkeypatch):
    leases.set_owner_id("replica-a")
    monkeypatch.setattr(members, "SERVICE_LOGS_DIR", tmp_path / "service_logs")
    members._log_state.clear()
    yield
    members.detach_log_file()
    members._log_state.clear()
    leases.set_owner_id(None)


def _age(member_id: str, seconds: float) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE members SET heartbeat_at = ? WHERE member_id = ?", (past, member_id))


def test_register_beat_and_status():
    rec = members.register("api", capabilities={"http": True}, load={"sse_clients": 0})
    assert rec["member_id"] == "replica-a" and rec["status"] == "live"
    assert rec["role"] == "api" and rec["host"] and rec["pid"]
    assert rec["capabilities"] == {"http": True}
    assert members.beat({"sse_clients": 3}) is True
    assert members.get("replica-a")["load"] == {"sse_clients": 3}
    _age("replica-a", members.STALE_AFTER_SECONDS + 10)
    assert members.get("replica-a")["status"] == "stale"
    members.mark_stopped()
    assert members.get("replica-a")["status"] == "stopped"
    assert members.beat() is True          # a beat after a stop is a restart
    assert members.get("replica-a")["status"] == "live"


def test_beat_reports_a_missing_row():
    assert members.beat() is False


def test_list_orders_live_stale_stopped_and_prune_forgets_old_rows():
    members.register("worker", member_id="w-live")
    members.register("worker", member_id="w-stale")
    members.register("api", member_id="api-stopped")
    _age("w-stale", 120)
    members.mark_stopped("api-stopped")
    order = [m["member_id"] for m in members.list_members()]
    assert order == ["w-live", "w-stale", "api-stopped"]
    assert [m["member_id"] for m in members.list_members(include_stopped=False)] == ["w-live", "w-stale"]
    _age("w-stale", members.FORGET_AFTER_SECONDS + 60)
    assert members.prune() == 1
    assert members.get("w-stale") is None
    assert members.forget("api-stopped") is True


def test_the_member_log_file_and_its_tail(tmp_path):
    path = members.attach_log_file()
    assert path and path.parent == tmp_path / "service_logs"
    members.register("api")
    logging.getLogger("test.members").warning("hello from the member")
    for h in logging.getLogger().handlers:
        h.flush()
    text = members.read_log("replica-a", tail=5)
    assert text and "hello from the member" in text
    assert members.read_log("nobody") is None


def test_the_beat_thread_registers_and_beats(monkeypatch):
    loads = []
    beat = members.MemberBeat("worker", capabilities={"concurrency": 2},
                              load_fn=lambda: loads.append(1) or {"tracked": len(loads)},
                              interval=0.02)
    beat.start()
    import time
    deadline = time.time() + 3
    while beat.beats < 2 and time.time() < deadline:
        time.sleep(0.02)
    beat.stop()
    rec = members.get("replica-a")
    assert beat.registered and beat.beats >= 2
    assert rec["status"] == "stopped" and rec["load"]["tracked"] >= 2
    assert rec["capabilities"] == {"concurrency": 2}


# ── the map ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def test_the_map_groups_everything_by_host(client, monkeypatch):
    from managers import run_manager as rm
    from tasks import service as ts

    members.register("api", member_id="replica-a")
    members.register("worker", member_id="worker-b")
    with db.transaction() as conn:
        conn.execute("UPDATE members SET host = ? WHERE member_id = ?", ("host-b", "worker-b"))
    leases.acquire("scheduler", "replica-a", 60)
    leases.acquire("watchdog", "worker-b", 60)

    t = ts.create_task("mapped")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    rm.update_run(rid, {"host": "host-b", "heartbeat_at": datetime.now(timezone.utc).isoformat()})
    from common import run_queue
    run_queue.enqueue("queued-1", "task", {}, workspace="default")

    monkeypatch.setattr("managers.container_manager.list_containers", lambda: [
        {"name": "agents-hub-run-x", "host": "host-b", "state": "running", "agent_id": "swe_agent"}])

    resp = client.get("/api/deployment")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {m["member_id"]: m for m in body["members"]}
    assert by_id["replica-a"]["leases"] == ["scheduler"]
    assert by_id["worker-b"]["leases"] == ["watchdog"]
    assert body["self"]["member_id"] == "replica-a"
    assert body["queue"]["queued"] == 1 and body["queue"]["items"][0]["run_id"] == "queued-1"
    runs = {r["run_id"]: r for r in body["runs"]}
    assert runs[rid]["host"] == "host-b" and runs[rid]["heartbeat_age_seconds"] < 30
    hosts = {h["host"]: h for h in body["hosts"]}
    assert hosts["host-b"]["runs"] == 1 and hosts["host-b"]["containers"] == 1
    assert "worker-b" in hosts["host-b"]["members"]


def test_member_log_route_and_forget(client):
    members.attach_log_file()
    members.register("api", member_id="replica-a")
    logging.getLogger("test.members").warning("route sees me")
    for h in logging.getLogger().handlers:
        h.flush()
    resp = client.get("/api/deployment/members/replica-a/logs?tail=10")
    assert resp.status_code == 200 and "route sees me" in resp.text
    assert client.get("/api/deployment/members/ghost/logs").status_code == 404
    assert client.delete("/api/deployment/members/replica-a").status_code == 409
    members.mark_stopped("replica-a")
    assert client.delete("/api/deployment/members/replica-a").status_code == 200
    assert members.get("replica-a") is None


def test_health_and_cli_show_the_map(monkeypatch):
    from typer.testing import CliRunner
    from cli.main import app as cli_app

    members.register("api", member_id="replica-a")
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    result = CliRunner().invoke(cli_app, ["deployment"])
    assert result.exit_code == 0, result.output
    assert "replica-a" in result.output and "queue:" in result.output
