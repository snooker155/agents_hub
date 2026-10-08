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


# ── the operator metrics: finished runs, models, durations, tools ───────────

def _agent_run(run_id, agent, status="completed", duration_ms=10, tool_calls=None,
               provider="openai", model="gpt-4o", inbound=1000, outbound_tokens=500, finished=None):
    rm.upsert_run({
        "run_id": run_id, "agent_id": agent, "workspace": "default", "status": status,
        "provider": provider, "model": model,
        "started_at": "2026-10-07T10:00:00+00:00",
        "finished_at": finished or "2026-10-07T10:00:01+00:00",
        "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound_tokens,
                                    "total_tokens": inbound + outbound_tokens},
                    "duration_ms": duration_ms, "tool_calls": tool_calls or []},
    })


def test_runs_finished_by_agent_and_status():
    _agent_run("a1", "writer")
    _agent_run("a2", "writer")
    _agent_run("a3", "writer", status="failed")
    _agent_run("a4", "coder", status="running")  # not finished: not counted
    text = metrics.render()
    assert 'agents_hub_runs_finished_total{agent="writer",status="completed"} 2' in text
    assert 'agents_hub_runs_finished_total{agent="writer",status="failed"} 1' in text
    assert 'agent="coder"' not in text.split("agents_hub_runs_finished_total", 2)[2].split("# HELP")[0]
    assert "# TYPE agents_hub_runs_finished_total counter" in text


def test_agent_labels_are_bounded_with_the_rest_as_other():
    for i in range(metrics.TOP_N + 5):
        _agent_run(f"x{i}", f"agent-{i:02d}")
    # the first agent is busy, so it stays named
    _agent_run("busy1", "agent-00")
    _agent_run("busy2", "agent-00")
    text = metrics.render()
    series = [ln for ln in text.splitlines() if ln.startswith("agents_hub_runs_finished_total{")]
    assert len(series) == metrics.TOP_N + 1
    assert 'agent="other",status="completed"} 5' in text
    assert 'agent="agent-00",status="completed"} 3' in text


def test_model_tokens_and_cost_by_provider_and_model():
    _seed_prices()
    _agent_run("m1", "writer", inbound=1_000_000, outbound_tokens=500_000)
    text = metrics.render()
    assert 'agents_hub_model_tokens_total{provider="openai",model="gpt-4o",direction="input"} 1000000' in text
    assert 'agents_hub_model_tokens_total{provider="openai",model="gpt-4o",direction="output"} 500000' in text
    assert 'agents_hub_model_cost_usd_total{provider="openai",model="gpt-4o"} 6.0' in text


def test_run_duration_histogram_has_cumulative_buckets_sum_and_count():
    _agent_run("d1", "writer", duration_ms=500)       # <= 1 s
    _agent_run("d2", "writer", duration_ms=20_000)    # <= 30 s
    _agent_run("d3", "writer", duration_ms=7_200_000)  # over an hour
    text = metrics.render()
    assert "# TYPE agents_hub_run_duration_seconds histogram" in text
    assert 'agents_hub_run_duration_seconds_bucket{le="1"} 1' in text
    assert 'agents_hub_run_duration_seconds_bucket{le="30"} 2' in text
    assert 'agents_hub_run_duration_seconds_bucket{le="3600"} 2' in text
    assert 'agents_hub_run_duration_seconds_bucket{le="+Inf"} 3' in text
    assert "agents_hub_run_duration_seconds_count 3" in text
    assert "agents_hub_run_duration_seconds_sum 7220.5" in text


def test_tool_calls_count_runs_finished_after_the_first_scrape():
    metrics.reset_tool_counts()
    _agent_run("t0", "writer", tool_calls=[{"tool": "old_tool", "status": "ok"}],
               finished="2000-01-01T00:00:00+00:00")
    metrics.render()  # sets the watermark: counting starts now
    from datetime import datetime, timezone
    later = datetime.now(timezone.utc).isoformat()
    rm.upsert_run({
        "run_id": "t1", "agent_id": "writer", "workspace": "default", "status": "completed",
        "started_at": later, "finished_at": later,
        "process": {"tool_calls": [{"tool": "web_search", "status": "ok"},
                                   {"tool": "web_search", "status": "ok"},
                                   {"tool": "web_search", "status": "error"},
                                   {"tool": "read_file", "status": "ok"}]},
    })
    text = metrics.render()
    assert 'agents_hub_tool_calls_total{tool="web_search",status="ok"} 2' in text
    assert 'agents_hub_tool_calls_total{tool="web_search",status="error"} 1' in text
    assert 'agents_hub_tool_calls_total{tool="read_file",status="ok"} 1' in text
    assert "old_tool" not in text
    # a second scrape does not count the same run twice
    assert 'agents_hub_tool_calls_total{tool="web_search",status="ok"} 2' in metrics.render()
    metrics.reset_tool_counts()
