"""common/metrics.py: the hand-written Prometheus exposition ``render()``
that backs ``GET /metrics``. Tested directly against the store modules
(``managers.run_manager``, ``common.run_queue``, ``common.leases``,
``notify.outbound``), rather than over HTTP — that wiring is
``tests/test_ops_routes.py``'s job.
"""
from __future__ import annotations

from common import leases, metrics, run_queue
from managers import run_manager as rm
from notify import outbound
from providers import catalog as model_catalog


def _seed_prices():
    model_catalog.save_catalog_raw({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.0, "output_price": 8.0},
        ]},
    })


def _run(run_id, *, workspace="default", status="completed",
          inbound=1000, outbound_tokens=500, provider="openai", model="gpt-4o"):
    rm.upsert_run({
        "run_id": run_id,
        "workspace": workspace,
        "status": status,
        "provider": provider,
        "model": model,
        "process": {"token_usage": {
            "inbound_tokens": inbound,
            "outbound_tokens": outbound_tokens,
            "total_tokens": inbound + outbound_tokens,
        }, "duration_ms": 10},
    })


def test_render_never_raises_on_an_empty_database():
    text = metrics.render()
    assert "agents_hub_database_up 1" in text


def test_render_reports_run_counts_by_status():
    _run("r1", status="completed")
    _run("r2", status="completed")
    _run("r3", status="failed")
    text = metrics.render()
    assert 'agents_hub_runs_total{status="completed"} 2' in text
    assert 'agents_hub_runs_total{status="failed"} 1' in text


def test_render_reports_the_running_gauge_including_stop():
    _run("r1", status="running")
    _run("r2", status="stop")
    _run("r3", status="completed")
    text = metrics.render()
    assert "agents_hub_runs_running 2" in text


def test_render_reports_tokens_and_cost_per_workspace():
    _seed_prices()
    _run("r1", workspace="acme", inbound=1_000_000, outbound_tokens=1_000_000)
    text = metrics.render()
    assert 'agents_hub_tokens_total{workspace="acme"} 2000000' in text
    # 1M input tokens @ $2/1M + 1M output tokens @ $8/1M = $10.
    assert 'agents_hub_cost_usd_total{workspace="acme"} 10.0' in text


def test_render_reports_queue_rows_by_status():
    run_queue.enqueue("q1", "task", {})
    run_queue.enqueue("q2", "task", {}, priority=1)
    text = metrics.render()
    assert 'agents_hub_run_queue{status="queued"} 2' in text
    assert 'agents_hub_run_queue{status="done"} 0' in text


def test_render_reports_outbox_pending_and_dead_counts():
    outbound.enqueue({"kind": "webhook", "url": "http://example.test"}, {"type": "t"})
    text = metrics.render()
    assert 'agents_hub_outbox{state="pending"} 1' in text
    assert 'agents_hub_outbox{state="dead"} 0' in text


def test_render_reports_lease_rows():
    leases.set_owner_id("replica-a")
    try:
        leases.acquire("scheduler", "replica-a", 60)
        text = metrics.render()
        assert 'agents_hub_lease_held{role="scheduler",owner="replica-a"} 1' in text
        assert 'agents_hub_lease_age_seconds{role="scheduler"}' in text
    finally:
        leases.set_owner_id(None)


def test_render_reports_an_expired_lease_as_not_held():
    from datetime import datetime, timedelta, timezone
    from common import db

    leases.set_owner_id("replica-a")
    try:
        leases.acquire("scheduler", "replica-a", 60)
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with db.transaction() as conn:
            conn.execute("UPDATE service_leases SET until = ? WHERE role = ?",
                        (past, "scheduler"))
        text = metrics.render()
        assert 'agents_hub_lease_held{role="scheduler",owner="replica-a"} 0' in text
    finally:
        leases.set_owner_id(None)


def test_render_includes_help_and_type_for_every_documented_metric():
    text = metrics.render()
    for name in (
        "agents_hub_runs_total", "agents_hub_runs_running", "agents_hub_run_queue",
        "agents_hub_run_queue_oldest_seconds", "agents_hub_outbox",
        "agents_hub_lease_age_seconds", "agents_hub_lease_held",
        "agents_hub_tokens_total", "agents_hub_database_up", "agents_hub_info",
    ):
        assert f"# HELP {name} " in text
        assert f"# TYPE {name} gauge" in text


def test_render_labels_info_with_role_and_instance():
    text = metrics.render()
    assert 'agents_hub_info{role="all",instance="' in text
