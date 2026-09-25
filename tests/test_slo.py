"""common/slo.py: the two SLO objectives over a rolling window.

Fabricated run rows only, no launcher involved: ``managers.run_manager.upsert_run``
for the leaf table, ``common.entity_runs.upsert`` for the container table, both
with explicit ``created_at`` / ``started_at`` / ``finished_at`` so the window
math is exact rather than timing-dependent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import entity_runs, slo
from managers import run_manager as rm


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime.now(timezone.utc)


def _leaf(run_id, *, created_at, started_at=None, finished_at=None, status="completed"):
    rm.upsert_run({
        "run_id": run_id, "workspace": "default", "status": status,
        "created_at": _iso(created_at),
        "started_at": _iso(started_at) if started_at else None,
        "finished_at": _iso(finished_at) if finished_at else None,
    })


def _entity(run_id, *, created_at, started_at=None, finished_at=None, status="completed"):
    entity_runs.upsert({
        "run_id": run_id, "kind": "flow", "entity_id": "f1", "workspace": "default",
        "status": status,
        "created_at": _iso(created_at),
        "started_at": _iso(started_at) if started_at else None,
        "finished_at": _iso(finished_at) if finished_at else None,
    }, notify=False)


# ── start_p95_seconds ────────────────────────────────────────────────────────

def test_start_p95_is_none_with_no_samples():
    value, sample = slo.start_p95_seconds(3600)
    assert value is None
    assert sample == 0


def test_start_p95_over_leaf_and_entity_runs_together():
    # Nine runs starting 5s after creation, one at 60s: p95 (index 8 of 10,
    # rounded) lands on the slow one.
    for i in range(9):
        _leaf(f"r{i}", created_at=NOW - timedelta(seconds=30), started_at=NOW - timedelta(seconds=25))
    _entity("e0", created_at=NOW - timedelta(seconds=90), started_at=NOW - timedelta(seconds=30))
    value, sample = slo.start_p95_seconds(3600)
    assert sample == 10
    assert value == pytest.approx(60.0, abs=0.5)


def test_start_p95_ignores_runs_outside_the_window():
    _leaf("old", created_at=NOW - timedelta(hours=5), started_at=NOW - timedelta(hours=5) + timedelta(seconds=5))
    value, sample = slo.start_p95_seconds(3600)
    assert value is None
    assert sample == 0


def test_start_p95_skips_runs_with_no_started_at():
    _leaf("pending", created_at=NOW - timedelta(seconds=10), started_at=None)
    value, sample = slo.start_p95_seconds(3600)
    assert value is None
    assert sample == 0


# ── error_rate ───────────────────────────────────────────────────────────────

def test_error_rate_is_none_below_min_sample():
    for i in range(5):
        _leaf(f"r{i}", created_at=NOW - timedelta(seconds=10),
              started_at=NOW - timedelta(seconds=9), finished_at=NOW - timedelta(seconds=1),
              status="failed")
    rate, sample = slo.error_rate(3600)
    assert rate is None
    assert sample == 5  # reported so a caller can show "5/20 needed"


def test_error_rate_counts_failed_over_finished():
    for i in range(15):
        _leaf(f"ok{i}", created_at=NOW - timedelta(seconds=10),
              started_at=NOW - timedelta(seconds=9), finished_at=NOW - timedelta(seconds=1),
              status="completed")
    for i in range(5):
        _leaf(f"bad{i}", created_at=NOW - timedelta(seconds=10),
              started_at=NOW - timedelta(seconds=9), finished_at=NOW - timedelta(seconds=1),
              status="failed")
    rate, sample = slo.error_rate(3600)
    assert sample == 20
    assert rate == pytest.approx(0.25)


def test_error_rate_normalizes_leaf_error_status_to_failed():
    for i in range(19):
        _leaf(f"ok{i}", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
              status="completed")
    _leaf("bad", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
          status="error")  # the leaf table's own spelling for failed
    rate, sample = slo.error_rate(3600)
    assert sample == 20
    assert rate == pytest.approx(1 / 20)


def test_error_rate_excludes_active_and_parked_runs():
    for i in range(20):
        _leaf(f"ok{i}", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
              status="completed")
    _leaf("still-running", created_at=NOW - timedelta(seconds=5), status="running")
    rate, sample = slo.error_rate(3600)
    assert sample == 20  # the running one is not "finished", so not counted either way


def test_error_rate_includes_entity_runs():
    for i in range(19):
        _leaf(f"ok{i}", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
              status="completed")
    _entity("bad-flow", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
            status="failed")
    rate, sample = slo.error_rate(3600)
    assert sample == 20
    assert rate == pytest.approx(1 / 20)


# ── evaluate() ───────────────────────────────────────────────────────────────

def test_evaluate_reports_no_data_with_an_empty_database():
    result = slo.evaluate()
    assert result["status"] == "no_data"
    assert result["objectives"]["start_p95"]["status"] == "no_data"
    assert result["objectives"]["error_rate"]["status"] == "no_data"


def test_evaluate_breach_on_slow_starts():
    # created 120s before started: well over the default 30s threshold.
    for i in range(5):
        _leaf(f"slow{i}", created_at=NOW - timedelta(seconds=120), started_at=NOW - timedelta(seconds=1))
    result = slo.evaluate()
    assert result["objectives"]["start_p95"]["status"] == "breach"
    assert result["status"] == "breach"


def test_evaluate_ok_on_fast_starts():
    for i in range(5):
        _leaf(f"fast{i}", created_at=NOW - timedelta(seconds=10), started_at=NOW - timedelta(seconds=9))
    result = slo.evaluate()
    assert result["objectives"]["start_p95"]["status"] == "ok"


def test_evaluate_honours_live_setting_threshold(monkeypatch):
    """A stricter AGENTS_HUB_SLO_START_P95_SECONDS turns an otherwise-ok
    sample into a breach, read live (common.config.live_setting), no restart."""
    monkeypatch.setenv("AGENTS_HUB_SLO_START_P95_SECONDS", "1")
    for i in range(5):
        _leaf(f"r{i}", created_at=NOW - timedelta(seconds=10), started_at=NOW - timedelta(seconds=5))
    result = slo.evaluate()
    assert result["objectives"]["start_p95"]["threshold_seconds"] == 1.0
    assert result["objectives"]["start_p95"]["status"] == "breach"


def test_evaluate_error_rate_threshold_from_env(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_SLO_ERROR_RATE", "0.5")
    for i in range(15):
        _leaf(f"ok{i}", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
              status="completed")
    for i in range(5):
        _leaf(f"bad{i}", created_at=NOW - timedelta(seconds=10), finished_at=NOW - timedelta(seconds=1),
              status="failed")
    result = slo.evaluate()
    assert result["objectives"]["error_rate"]["threshold"] == 0.5
    assert result["objectives"]["error_rate"]["status"] == "ok"  # 0.25 < 0.5
