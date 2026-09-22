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
