"""Alert rules: run_failed, spend_run_over and spend_daily_over.

Firing is exercised through :func:`notify.rules.evaluate_run_finished`, the
single call site ``managers/runs/notifications.py`` uses. Delivery itself
(``plans.service.create_notification``) is stubbed, the same pattern
``tests/test_run_notifications.py`` uses for the inbox.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from notify import rules as notify_rules
from notify import store as notify_store
from workspace import create_workspace_folder


@pytest.fixture
def ws():
    """A fresh workspace per test: workspace metadata lives on disk under the
    test root and is not reset by the ``fresh_db`` fixture (SQLite only), so a
    shared name would leak rules between tests."""
    name = f"notify-rules-ws-{uuid4().hex[:8]}"
    create_workspace_folder(name)
    return name


@pytest.fixture
def fired(monkeypatch):
    """Capture every notification a rule raises."""
    from plans import service as plan_service

    calls = []

    def _capture(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(plan_service, "create_notification", _capture)
    return calls


def test_run_failed_fires(ws, fired):
    notify_store.create_rule(ws, {"kind": "run_failed", "channels": ["dashboard"]})
    run = {"status": "failed", "agent_id": "swe_agent", "workspace": ws, "error": "boom"}

    notify_rules.evaluate_run_finished(run)

    assert len(fired) == 1
    assert "swe_agent" in fired[0]["title"]
    assert fired[0]["severity"] == "error"
    assert fired[0]["channels"] == ["dashboard"]


def test_run_failed_ignores_completed_runs(ws, fired):
    notify_store.create_rule(ws, {"kind": "run_failed", "channels": ["dashboard"]})
    notify_rules.evaluate_run_finished({"status": "completed", "agent_id": "a", "workspace": ws})
    assert fired == []


def test_run_failed_agent_filter_skips_non_matching(ws, fired):
    notify_store.create_rule(ws, {"kind": "run_failed", "agent_id": "swe_agent", "channels": ["dashboard"]})
    notify_rules.evaluate_run_finished({"status": "failed", "agent_id": "other_agent", "workspace": ws})
    assert fired == []


def test_run_failed_agent_filter_matches(ws, fired):
    notify_store.create_rule(ws, {"kind": "run_failed", "agent_id": "swe_agent", "channels": ["dashboard"]})
    notify_rules.evaluate_run_finished({"status": "failed", "agent_id": "swe_agent", "workspace": ws})
    assert len(fired) == 1


def test_disabled_rule_never_fires(ws, fired):
    rule = notify_store.create_rule(ws, {"kind": "run_failed", "channels": ["dashboard"]})
    notify_store.update_rule(ws, rule["id"], {"enabled": False})
    notify_rules.evaluate_run_finished({"status": "failed", "agent_id": "a", "workspace": ws})
    assert fired == []


def test_spend_run_over_fires(ws, fired, monkeypatch):
    notify_store.create_rule(ws, {"kind": "spend_run_over", "threshold_usd": 1.0, "channels": ["dashboard"]})
    monkeypatch.setattr("common.pricing.load_price_map", lambda: {})
    monkeypatch.setattr("common.pricing.run_cost_usd", lambda run, prices: 2.5)

    notify_rules.evaluate_run_finished({"status": "completed", "agent_id": "a", "workspace": ws})

    assert len(fired) == 1
    assert "1.00" in fired[0]["title"]


def test_spend_run_over_below_threshold_does_not_fire(ws, fired, monkeypatch):
    notify_store.create_rule(ws, {"kind": "spend_run_over", "threshold_usd": 5.0, "channels": ["dashboard"]})
    monkeypatch.setattr("common.pricing.load_price_map", lambda: {})
    monkeypatch.setattr("common.pricing.run_cost_usd", lambda run, prices: 0.1)

    notify_rules.evaluate_run_finished({"status": "completed", "agent_id": "a", "workspace": ws})

    assert fired == []


def test_spend_daily_over_fires_once_per_day(ws, fired, monkeypatch):
    rule = notify_store.create_rule(ws, {"kind": "spend_daily_over", "threshold_usd": 5.0, "channels": ["dashboard"]})
    monkeypatch.setattr(
        "managers.runs.store.query_runs",
        lambda **kw: {"items": [{"channel": "chat"}, {"channel": "chat"}], "total": 2, "limit": 10000, "offset": 0},
    )
    monkeypatch.setattr("common.pricing.load_price_map", lambda: {})
    monkeypatch.setattr("common.pricing.run_cost_usd", lambda run, prices: 3.0)  # 2 runs * 3.0 = 6.0 > 5.0

    run = {"status": "completed", "agent_id": "a", "workspace": ws}
    notify_rules.evaluate_run_finished(run)
    notify_rules.evaluate_run_finished(run)  # a second run finishing the same day

    assert len(fired) == 1  # fired exactly once despite two terminal runs
    updated = notify_store.get_rule(ws, rule["id"])
    assert updated["last_fired_date"] is not None


def test_spend_daily_over_below_threshold_does_not_fire(ws, fired, monkeypatch):
    notify_store.create_rule(ws, {"kind": "spend_daily_over", "threshold_usd": 100.0, "channels": ["dashboard"]})
    monkeypatch.setattr(
        "managers.runs.store.query_runs",
        lambda **kw: {"items": [{"channel": "chat"}], "total": 1, "limit": 10000, "offset": 0},
    )
    monkeypatch.setattr("common.pricing.load_price_map", lambda: {})
    monkeypatch.setattr("common.pricing.run_cost_usd", lambda run, prices: 1.0)

    notify_rules.evaluate_run_finished({"status": "completed", "agent_id": "a", "workspace": ws})

    assert fired == []


def test_evaluation_error_never_raises(ws, monkeypatch):
    notify_store.create_rule(ws, {"kind": "run_failed", "channels": ["dashboard"]})
    monkeypatch.setattr("notify.store.list_rules", lambda workspace: (_ for _ in ()).throw(RuntimeError("boom")))
    notify_rules.evaluate_run_finished({"status": "failed", "workspace": ws})  # must not raise


def test_entity_run_reaching_a_terminal_status_evaluates_rules(ws, fired):
    """A flow, team, loop or scenario run closing fires the same rules a leaf
    run does (common/entity_runs.py hooks evaluate_run_finished on the
    transition), and only once: a later bookkeeping write is not a second
    finish."""
    from common import entity_runs

    notify_store.create_rule(ws, {"kind": "run_failed", "channels": ["dashboard"]})
    entity_runs.upsert({"run_id": "team-x", "kind": "team", "entity_id": "team-a",
                        "workspace": ws, "status": "running"})
    assert fired == []

    entity_runs.close("team-x", status="failed", exit_code=1, error="boom")
    assert len(fired) == 1

    entity_runs.update("team-x", {"error": "boom, again"})
    assert len(fired) == 1


# ── SLO alerts (common/slo.py): ticked, not per-run-finished ────────────────
#
# evaluate_slo_alerts reads common.slo.evaluate() once and fans it out to
# every workspace's slo_start_latency / slo_error_rate rules, so these tests
# fake the objectives directly rather than fabricating hundreds of run rows
# (that is common/slo.py's own test file's job, tests/test_slo.py).

def _fake_slo(monkeypatch, *, start_status="ok", error_status="ok"):
    payload = {
        "status": "breach" if "breach" in (start_status, error_status) else "ok",
        "objectives": {
            "start_p95": {"status": start_status, "value_seconds": 45.0,
                         "threshold_seconds": 30.0, "sample": 20, "window_seconds": 3600},
            "error_rate": {"status": error_status, "value": 0.1,
                          "threshold": 0.05, "sample": 20, "window_seconds": 3600},
        },
    }
    monkeypatch.setattr("common.slo.evaluate", lambda *a, **k: payload)


def test_slo_rule_fires_once_on_breach_and_not_again_while_it_holds(ws, fired, monkeypatch):
    rule = notify_store.create_rule(ws, {"kind": "slo_start_latency", "channels": ["dashboard"]})
    _fake_slo(monkeypatch, start_status="breach")

    notify_rules.evaluate_slo_alerts(force=True)
    assert len(fired) == 1
    assert "breach" in fired[0]["title"].lower()
    assert fired[0]["severity"] == "warning"

    notify_rules.evaluate_slo_alerts(force=True)  # still breaching
    assert len(fired) == 1  # no repeat

    updated = notify_store.get_rule(ws, rule["id"])
    assert updated["state"] == {"status": "breach"}


def test_slo_rule_fires_again_on_recovery(ws, fired, monkeypatch):
    notify_store.create_rule(ws, {"kind": "slo_error_rate", "channels": ["dashboard"]})
    _fake_slo(monkeypatch, error_status="breach")
    notify_rules.evaluate_slo_alerts(force=True)
    assert len(fired) == 1

    _fake_slo(monkeypatch, error_status="ok")
    notify_rules.evaluate_slo_alerts(force=True)
    assert len(fired) == 2
    assert "recovered" in fired[1]["title"].lower()
    assert fired[1]["severity"] == "info"

    notify_rules.evaluate_slo_alerts(force=True)  # still ok
    assert len(fired) == 2  # no repeat


def test_slo_rule_ignores_no_data_and_keeps_last_state(ws, fired, monkeypatch):
    rule = notify_store.create_rule(ws, {"kind": "slo_start_latency", "channels": ["dashboard"]})
    _fake_slo(monkeypatch, start_status="breach")
    notify_rules.evaluate_slo_alerts(force=True)
    assert len(fired) == 1

    _fake_slo(monkeypatch, start_status="no_data")
    notify_rules.evaluate_slo_alerts(force=True)
    assert len(fired) == 1  # no_data neither fires nor clears the breach state
    assert notify_store.get_rule(ws, rule["id"])["state"] == {"status": "breach"}


def test_slo_rule_disabled_never_fires(ws, fired, monkeypatch):
    rule = notify_store.create_rule(ws, {"kind": "slo_start_latency", "channels": ["dashboard"]})
    notify_store.update_rule(ws, rule["id"], {"enabled": False})
    _fake_slo(monkeypatch, start_status="breach")
    notify_rules.evaluate_slo_alerts(force=True)
    assert fired == []


def test_slo_evaluation_is_throttled_without_force(ws, fired, monkeypatch):
    notify_store.create_rule(ws, {"kind": "slo_start_latency", "channels": ["dashboard"]})
    _fake_slo(monkeypatch, start_status="breach")
    calls = []
    monkeypatch.setattr("common.slo.evaluate", lambda *a, **k: calls.append(1) or {
        "status": "breach", "objectives": {
            "start_p95": {"status": "breach", "value_seconds": 45.0, "threshold_seconds": 30.0,
                         "sample": 20, "window_seconds": 3600},
            "error_rate": {"status": "ok", "value": 0.0, "threshold": 0.05,
                          "sample": 20, "window_seconds": 3600},
        }})
    notify_rules.evaluate_slo_alerts(force=True)
    notify_rules.evaluate_slo_alerts()  # not forced, throttled: no second evaluate() call
    assert len(calls) == 1
    assert len(fired) == 1


def test_slo_evaluation_error_never_raises(ws, monkeypatch):
    notify_store.create_rule(ws, {"kind": "slo_start_latency", "channels": ["dashboard"]})
    monkeypatch.setattr("common.slo.evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    notify_rules.evaluate_slo_alerts(force=True)  # must not raise
