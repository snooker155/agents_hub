"""
The Dashboard's load widgets (common/dashboard_overview.py).
"""
from datetime import datetime, timedelta, timezone

from common import dashboard_overview
from managers import run_manager as rm
from providers import catalog as model_catalog


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _run(run_id, *, status, ago, workspace="ws", agent_id="a1", channel="chat",
         inbound=1_000_000, outbound=0, duration_s=4, service_id=None):
    started = _iso(ago)
    rm.upsert_run({
        "run_id": run_id, "workspace": workspace, "agent_id": agent_id, "channel": channel,
        "provider": "openai", "model": "gpt-4o", "status": status,
        "started_at": started, "finished_at": _iso(ago - timedelta(seconds=duration_s)),
        "service_id": service_id,
        "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound,
                                    "total_tokens": inbound + outbound}},
    })


def _seed_prices():
    model_catalog.save_catalog_raw({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.00, "output_price": 10.00},
        ]},
    })


def test_runs_by_day(monkeypatch):
    _seed_prices()
    _run("r1", status="completed", ago=timedelta(hours=1))
    _run("r2", status="failed", ago=timedelta(hours=2), agent_id="a2", inbound=500_000)
    _run("r3", status="completed", ago=timedelta(days=3))
    # Another workspace and an evaluation run never count.
    _run("r4", status="completed", ago=timedelta(hours=1), workspace="other")
    _run("r5", status="completed", ago=timedelta(hours=1), channel="eval")
    _run("r6", status="completed", ago=timedelta(days=40))

    out = dashboard_overview.overview("ws", days=14)

    last = out["runs"]["last_24h"]
    assert (last["total"], last["completed"], last["failed"]) == (2, 1, 1)
    assert last["success_rate"] == 50.0
    assert last["avg_duration_ms"] == 4000
    assert last["channels"] == {"chat": 2}
    per_day = out["runs"]["per_day"]
    assert len(per_day) == 14
    assert sum(d["completed"] for d in per_day) == 2
    assert sum(d["failed"] for d in per_day) == 1
    assert "costs" not in out


def test_a_failing_section_does_not_blank_the_others(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("store down")
    monkeypatch.setattr(dashboard_overview, "_live", boom)
    out = dashboard_overview.overview(None)
    assert out["live"] == {"error": "store down"}
    assert "per_day" in out["runs"]


def test_live_lists_what_runs_and_sets_aside_the_silent(monkeypatch):
    from common import entity_runs
    rm.upsert_run({"run_id": "now", "workspace": "ws", "agent_id": "a1", "status": "running",
                   "started_at": _iso(timedelta(minutes=2))})
    # A stop request never honoured, months ago: no sign of life.
    rm.upsert_run({"run_id": "old", "workspace": "ws", "agent_id": "a1", "status": "stop",
                   "started_at": _iso(timedelta(days=90))})
    # A run stopped in process: stop and finish time together, so it ended.
    rm.upsert_run({"run_id": "done", "workspace": "ws", "agent_id": "a1", "status": "stop",
                   "started_at": _iso(timedelta(minutes=30)), "finished_at": _iso(timedelta(minutes=29))})
    entity_runs.upsert({"run_id": "flow1", "entity_id": "f1", "workspace": "ws", "status": "running",
                        "started_at": _iso(timedelta(minutes=1)), "heartbeat_at": _iso(timedelta(seconds=20))},
                       kind="flow")

    live = dashboard_overview.overview("ws")["live"]
    assert sorted((i["kind"], i["run_id"]) for i in live["items"]) == [("agent", "now"), ("flow", "flow1")]
    assert live["by_kind"] == {"agent": 1, "flow": 1}
    assert live["stale_total"] == 1 and live["stale"][0]["run_id"] == "old"
    recent = {r["run_id"]: r["status"] for r in live["recent"]}
    assert recent["done"] == "stopped"


def test_recent_leaves_out_runs_the_watchdog_closed_late(monkeypatch):
    rm.upsert_run({"run_id": "late", "workspace": "ws", "agent_id": "a1", "status": "stopped",
                   "started_at": _iso(timedelta(days=90)), "finished_at": _iso(timedelta(minutes=1)),
                   "settled_by": "watchdog"})
    rm.upsert_run({"run_id": "real", "workspace": "ws", "agent_id": "a1", "status": "completed",
                   "started_at": _iso(timedelta(minutes=9)), "finished_at": _iso(timedelta(minutes=8))})
    recent = [r["run_id"] for r in dashboard_overview.overview("ws")["live"]["recent"]]
    assert recent == ["real"]


def test_health_does_not_count_a_finished_stop_as_running():
    from common.health import snapshot
    rm.upsert_run({"run_id": "a", "status": "running", "started_at": _iso(timedelta(minutes=1))})
    rm.upsert_run({"run_id": "b", "status": "stop", "started_at": _iso(timedelta(minutes=1))})
    rm.upsert_run({"run_id": "c", "status": "stop", "started_at": _iso(timedelta(minutes=5)),
                   "finished_at": _iso(timedelta(minutes=4))})
    assert snapshot()["database"]["running_runs"] == 2
