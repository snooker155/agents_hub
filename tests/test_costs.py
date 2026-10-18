"""
Cost aggregation and pricing lookup.
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


# ── runs list (Costs page, Runs tab) ──────────────────────────────────────────

def test_costs_runs_rows_add_up_to_the_breakdown(monkeypatch, tmp_path):
    from common.costs_report import costs_breakdown, costs_runs
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1_000_000, outbound=500_000,
              started_at="2026-07-06T10:00:00+00:00")  # $7.50
    _make_run("r2", workspace="ws1", inbound=0, outbound=1_000_000,
              started_at="2026-07-07T10:00:00+00:00")  # $10.00
    _make_run("r3", workspace="ws1", inbound=10, outbound=10, model="mystery",
              started_at="2026-07-08T10:00:00+00:00")  # no price
    _make_run("r4", workspace="ws2", inbound=1_000_000, outbound=0)

    page = costs_runs("ws1")
    assert page["total"] == 3
    assert [r["run_id"] for r in page["items"]] == ["r3", "r2", "r1"]  # newest first
    assert sum(r["cost"] for r in page["items"]) == pytest.approx(costs_breakdown("ws1")["totals"]["cost"])
    assert {r["run_id"]: r["priced"] for r in page["items"]} == {"r1": True, "r2": True, "r3": False}

    by_cost = costs_runs("ws1", sort="cost", order="desc", limit=1, offset=0)
    assert by_cost["total"] == 3 and by_cost["items"][0]["run_id"] == "r2"
    assert costs_runs("ws1", sort="cost", order="asc", limit=2, offset=1)["items"][0]["run_id"] == "r1"


def test_costs_runs_respects_visible_workspaces(monkeypatch, tmp_path):
    from common.costs_report import costs_runs
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1, outbound=1)
    _make_run("r2", workspace="ws2", inbound=1, outbound=1)
    page = costs_runs(workspaces={"ws2"})
    assert [r["run_id"] for r in page["items"]] == ["r2"]


def test_costs_runs_filters_by_agent_and_status(monkeypatch, tmp_path):
    from common.costs_report import costs_runs
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1, outbound=1, agent_id="a1")
    _make_run("r2", workspace="ws1", inbound=1, outbound=1, agent_id="a2")
    rm.update_run("r2", {"status": "failed"})
    # Unfiltered, every agent's runs are listed (a loop variable once shadowed
    # the agent filter and kept only the first row's agent).
    assert costs_runs("ws1")["total"] == 2
    assert [r["run_id"] for r in costs_runs("ws1", agent_id="a2")["items"]] == ["r2"]
    assert [r["run_id"] for r in costs_runs("ws1", status="failed")["items"]] == ["r2"]


def test_a_bare_until_day_includes_that_whole_day(monkeypatch, tmp_path):
    from common.costs_report import costs_breakdown, costs_runs
    _seed_prices(monkeypatch, tmp_path)
    _make_run("r1", workspace="ws1", inbound=1_000_000, outbound=0,
              started_at="2026-07-06T22:00:00+00:00")
    assert costs_runs("ws1", until="2026-07-06")["total"] == 1
    assert costs_runs("ws1", until="2026-07-05")["total"] == 0
    assert costs_breakdown("ws1", until="2026-07-06")["totals"]["runs"] == 1
