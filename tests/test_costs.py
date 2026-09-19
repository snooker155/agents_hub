"""
Cost aggregation, pricing lookup and per-workspace budget enforcement.
"""
import json

import pytest

from managers import run_manager as rm
from common import pricing, budget


def _seed_prices(monkeypatch, tmp_path):
    """Point the pricing catalog at a temp models.json with known prices."""
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.50, "output_price": 10.00},
        ]},
    }), encoding="utf-8")
    monkeypatch.setattr(pricing, "MODELS_FILE", models_file)
    return models_file


def _make_run(run_id, *, workspace, inbound, outbound, provider="openai",
              model="gpt-4o", started_at="2026-07-06T10:00:00+00:00",
              agent_id="a1", task_id="t1"):
    rm.upsert_run({
        "run_id": run_id,
        "workspace": workspace,
        "provider": provider,
        "model": model,
        "started_at": started_at,
        "agent_id": agent_id,
        "task_id": task_id,
        "process": {"token_usage": {
            "inbound_tokens": inbound,
            "outbound_tokens": outbound,
            "total_tokens": inbound + outbound,
        }, "duration_ms": 10},
    })


def test_run_cost_usd(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    prices = pricing.load_price_map()
    # 1M inbound @ $2.50 + 0.5M outbound @ $10.00 = 2.50 + 5.00 = 7.50
    run = {"provider": "openai", "model": "gpt-4o",
           "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 500_000}}}
    assert pricing.run_cost_usd(run, prices) == pytest.approx(7.50)


def test_cached_input_is_billed_at_the_cached_rate(monkeypatch, tmp_path):
    """The defect this split fixes: an agent loop re-sends its whole prompt every
    step, so almost all of a long run's input is a cache read the provider bills
    at a tenth of the input rate. Counting it as fresh input overstated a real
    37-step visualizer run by roughly an order of magnitude."""
    _seed_prices(monkeypatch, tmp_path)
    prices = pricing.load_price_map()
    # No explicit cached price in the catalog -> a tenth of $2.50.
    assert prices[("openai", "gpt-4o")] == (2.50, 10.00, pytest.approx(0.25))
    run = {"provider": "openai", "model": "gpt-4o",
           "process": {"token_usage": {
               "inbound_tokens": 1_000_000, "outbound_tokens": 0,
               "cached_tokens": 900_000}}}
    # 100k fresh @ $2.50/1M + 900k cached @ $0.25/1M = 0.25 + 0.225
    assert pricing.run_cost_usd(run, prices) == pytest.approx(0.475)


def test_a_run_without_cache_data_prices_as_all_fresh_input(monkeypatch, tmp_path):
    """Runs recorded before cache tracking existed carry no cached count, and
    must keep costing exactly what they always did."""
    _seed_prices(monkeypatch, tmp_path)
    prices = pricing.load_price_map()
    run = {"provider": "openai", "model": "gpt-4o",
           "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}}}
    assert pricing.run_cost_usd(run, prices) == pytest.approx(2.50)


def test_an_explicit_cached_price_beats_the_discount_default(monkeypatch, tmp_path):
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.50,
             "output_price": 10.00, "cached_input_price": 0.0},
        ]},
    }), encoding="utf-8")
    monkeypatch.setattr(pricing, "MODELS_FILE", models_file)
    prices = pricing.load_price_map()
    run = {"provider": "openai", "model": "gpt-4o",
           "process": {"token_usage": {
               "inbound_tokens": 1_000_000, "outbound_tokens": 0,
               "cached_tokens": 1_000_000}}}
    # An explicit 0 is a real price, not a missing field.
    assert pricing.run_cost_usd(run, prices) == 0.0


def test_cached_tokens_never_exceed_inbound(monkeypatch, tmp_path):
    """Providers report cache reads as a subset of the prompt. A record claiming
    otherwise must not bill negatively."""
    _seed_prices(monkeypatch, tmp_path)
    prices = pricing.load_price_map()
    run = {"provider": "openai", "model": "gpt-4o",
           "process": {"token_usage": {
               "inbound_tokens": 1_000, "outbound_tokens": 0,
               "cached_tokens": 999_999}}}
    assert pricing.run_cost_usd(run, prices) == pytest.approx(1_000 / 1e6 * 0.25)


def test_run_cost_unknown_model_is_zero(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    prices = pricing.load_price_map()
    run = {"provider": "openai", "model": "mystery",
           "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}}}
    assert pricing.run_cost_usd(run, prices) == 0.0


def test_workspace_period_spend_totals(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1_000_000, outbound=500_000)  # $7.50
    _make_run("r2", workspace="ws1", inbound=0, outbound=1_000_000)        # $10.00
    _make_run("r3", workspace="ws2", inbound=1_000_000, outbound=0)        # other ws
    assert budget.workspace_period_spend("ws1", "total") == pytest.approx(17.50)
    assert budget.workspace_period_spend("ws2", "total") == pytest.approx(2.50)


def test_period_filter_excludes_old_runs(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    _make_run("old", workspace="ws1", inbound=1_000_000, outbound=0,
              started_at="2000-01-01T00:00:00+00:00")
    # "monthly" period start is this month, so the ancient run is excluded.
    assert budget.workspace_period_spend("ws1", "monthly") == pytest.approx(0.0)
    # "total" counts everything.
    assert budget.workspace_period_spend("ws1", "total") == pytest.approx(2.50)


def test_check_budget_raises_over_hard_cap(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1_000_000, outbound=500_000)  # $7.50
    monkeypatch.setattr(budget, "get_budget",
                        lambda ws: {"hard_limit_usd": 5.0, "soft_limit_usd": 0.0, "period": "total"})
    with pytest.raises(budget.BudgetExceededError):
        budget.check_budget("ws1")


def test_check_budget_no_cap_is_noop(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1_000_000, outbound=500_000)
    monkeypatch.setattr(budget, "get_budget",
                        lambda ws: {"hard_limit_usd": 0.0, "soft_limit_usd": 0.0, "period": "total"})
    # No hard cap → never raises regardless of spend.
    budget.check_budget("ws1")


def test_check_budget_under_cap_is_noop(monkeypatch, tmp_path):
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1_000_000, outbound=0)  # $2.50
    monkeypatch.setattr(budget, "get_budget",
                        lambda ws: {"hard_limit_usd": 100.0, "soft_limit_usd": 0.0, "period": "total"})
    budget.check_budget("ws1")


def test_check_budget_fails_open_on_error(monkeypatch):
    # get_budget blowing up must not raise out of check_budget (fail open).
    def _boom(ws):
        raise RuntimeError("catalog unreadable")
    monkeypatch.setattr(budget, "get_budget", _boom)
    budget.check_budget("ws1")  # no exception


def test_normalize_budget_defaults():
    b = budget.normalize_budget({"hard_limit_usd": "3.5", "period": "weekly"})
    assert b["hard_limit_usd"] == 3.5
    assert b["period"] == "monthly"  # invalid period falls back
    assert b["soft_limit_usd"] == 0.0
    # negatives clamp to zero
    assert budget.normalize_budget({"hard_limit_usd": -5})["hard_limit_usd"] == 0.0
