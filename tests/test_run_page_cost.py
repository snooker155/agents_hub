"""The run page's cost estimate (routes/messages.py ``_run_cost``): the same
figure common/pricing.py ``run_cost_usd`` gives the Costs page and budgets,
with the log-parsed totals of a chat run standing in when the record has
none, and ``priced`` False for a model the catalog has no price for."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def run_cost(monkeypatch):
    import common.pricing as pricing
    from routes import messages
    # $3 in, $15 out, $0.30 cached per 1M tokens.
    monkeypatch.setattr(pricing, "load_price_map",
                        lambda: {("anthropic", "claude-x"): (3.0, 15.0, 0.3)})
    return messages._run_cost


def test_priced_from_the_record(run_cost):
    run = {"provider": "anthropic", "model": "claude-x",
           "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 100_000}}}
    cost = run_cost(run, "claude-x", "anthropic", {"total_tokens": 1_100_000})
    assert cost == {"usd": 4.5, "priced": True}


def test_chat_run_uses_the_page_totals(run_cost):
    run = {"agent_id": "a"}
    usage = {"inbound_tokens": 2_000_000, "outbound_tokens": 0, "total_tokens": 2_000_000}
    cost = run_cost(run, "claude-x", "anthropic", usage)
    assert cost == {"usd": 6.0, "priced": True}


def test_unknown_model_is_not_priced(run_cost):
    run = {"provider": "local", "model": "mystery",
           "process": {"token_usage": {"inbound_tokens": 10, "outbound_tokens": 10}}}
    assert run_cost(run, "mystery", "local", {"total_tokens": 20}) == {"usd": 0.0, "priced": False}


def test_local_model_is_free_not_unpriced(run_cost):
    run = {"provider": "hub-local", "model": "qwen3-8b",
           "process": {"token_usage": {"inbound_tokens": 10, "outbound_tokens": 10}}}
    assert run_cost(run, "qwen3-8b", "hub-local", {"total_tokens": 20}) == {"usd": 0.0, "priced": True}
