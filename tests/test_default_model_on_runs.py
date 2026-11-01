"""An agent that names no model, in a workspace that names none either, runs on
the hub's global default (the administrator's choice on the Models page). The
factory names that model, so the run's record holds it and the person's spend
(common/user_budget.py) prices its tokens instead of counting $0."""
from __future__ import annotations

from agents.agent_factory import get_factory
from common.pricing import run_cost_usd


def test_a_provider_without_a_model_gets_the_global_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-default-test")
    provider, model, _base, _key = get_factory()._resolve_model_config({}, None)
    assert (provider, model) == ("openai", "gpt-default-test")

    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-default-test")
    provider, model, _base, _key = get_factory()._resolve_model_config({"provider": "anthropic"}, None)
    assert (provider, model) == ("anthropic", "claude-default-test")


def test_a_named_model_is_kept(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-default-test")
    _p, model, _b, _k = get_factory()._resolve_model_config({"provider": "openai", "model": "gpt-own"}, None)
    assert model == "gpt-own"


def test_the_named_default_is_priced():
    prices = {("openai", "gpt-default-test"): (1.0, 2.0, 0.5)}
    named = {"provider": "openai", "model": "gpt-default-test",
             "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 1_000_000}}}
    assert run_cost_usd({**named, "model": ""}, prices) == 0
    assert run_cost_usd(named, prices) == 3.0
