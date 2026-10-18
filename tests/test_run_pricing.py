"""
Per-run pricing lookup.
"""
import pytest

from managers import run_manager as rm
from common import pricing
from providers import catalog as model_catalog


def _seed_prices(monkeypatch=None, tmp_path=None):
    """Seed the model catalog (database-backed) with known prices."""
    model_catalog.save_catalog_raw({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.50, "output_price": 10.00},
        ]},
    })


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
    model_catalog.save_catalog_raw({
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.50,
             "output_price": 10.00, "cached_input_price": 0.0},
        ]},
    })
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


