"""
A run where a fallback model answered some calls is priced per model: the
fallback's calls at the fallback's rate, the rest at the run's own model
(common/pricing.run_cost_usd over loop.answered_by, which agents/loop_ext/
fallback.py fills with each fallback call's tokens).
"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agents.agent_loop import LoopState
from agents.loop_ext import fallback
from common.pricing import run_cost_usd

PRICES = {
    ("openai", "big"): (10.0, 30.0, 1.0),
    ("anthropic", "small"): (1.0, 2.0, 0.1),
}


def _run(loop=None, inbound=3_000_000, outbound=1_000_000):
    run = {"provider": "openai", "model": "big",
           "process": {"token_usage": {"inbound_tokens": inbound, "outbound_tokens": outbound}}}
    if loop is not None:
        run["loop"] = loop
    return run


def test_a_run_without_fallbacks_is_priced_at_its_model():
    assert run_cost_usd(_run(), PRICES) == 3 * 10.0 + 1 * 30.0


def test_fallback_calls_are_priced_at_the_fallback_rate():
    loop = {"fallback_used": True, "answered_by": [
        {"provider": "openai", "model": "big", "fallback": False},
        {"provider": "anthropic", "model": "small-2026", "price_model": "small", "fallback": True,
         "input_tokens": 2_000_000, "output_tokens": 500_000, "cached_tokens": 0},
    ]}
    cost = run_cost_usd(_run(loop), PRICES)
    # 1M in + 0.5M out at the run's model, 2M in + 0.5M out at the fallback's.
    assert abs(cost - (1 * 10.0 + 0.5 * 30.0 + 2 * 1.0 + 0.5 * 2.0)) < 1e-9


def test_fallback_entries_without_tokens_leave_the_old_pricing():
    loop = {"fallback_used": True, "answered_by": [
        {"provider": "anthropic", "model": "small", "fallback": True}]}
    assert run_cost_usd(_run(loop), PRICES) == 3 * 10.0 + 1 * 30.0


def test_the_callback_records_a_fallback_calls_tokens():
    state = LoopState()
    cb = fallback._CandidateCallback(state, {"provider": "anthropic", "model": "small"},
                                     is_primary=False, reason_holder={"reason": "RateLimitError"})
    msg = AIMessage(content="ok", usage_metadata={"input_tokens": 120, "output_tokens": 30,
                                                  "total_tokens": 150})
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
    entry = state.answered_by[-1]
    assert entry["fallback"] is True and entry["reason"] == "RateLimitError"
    assert entry["input_tokens"] == 120 and entry["output_tokens"] == 30
    assert entry["price_model"] == "small"
