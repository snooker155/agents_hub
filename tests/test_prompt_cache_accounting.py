"""Prompt-cache accounting: counting the cheap half of an agent loop's input.

An agent loop re-sends its whole conversation on every step, so a long run's
inbound total is mostly the same prefix over and over. The provider serves that
prefix from its prompt cache and bills it at a fraction of the input rate, but
until these counts were tracked every one of those tokens was priced as fresh
input — which overstated a real 37-step run by roughly an order of magnitude.

Two things have to hold: the count has to survive whichever shape the provider
reports it in, and it has to reach the run record that cost aggregation reads.
"""
import asyncio

import pytest

from agents.callbacks.chat_stream import ChatStreamCallback
from agents.callbacks.run_statistics import cached_input_tokens
from managers import run_manager as rm


class _Detail:
    """``prompt_tokens_details`` arrives as a pydantic object on some SDKs."""

    def model_dump(self):
        return {"cached_tokens": 512}


@pytest.mark.parametrize("usage, expected", [
    # OpenAI chat completions.
    ({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 896}}, 896),
    # OpenAI responses API.
    ({"prompt_tokens": 1000, "input_tokens_details": {"cached_tokens": 768}}, 768),
    # LangChain's normalised usage_metadata.
    ({"prompt_tokens": 1000, "input_token_details": {"cache_read": 640}}, 640),
    # Anthropic.
    ({"prompt_tokens": 1000, "cache_read_input_tokens": 384}, 384),
    # An SDK object rather than a plain dict.
    ({"prompt_tokens": 1000, "prompt_tokens_details": _Detail()}, 512),
    # A provider that reports nothing prices exactly as before.
    ({"prompt_tokens": 1000}, 0),
    ({}, 0),
    ({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 0}}, 0),
])
def test_cached_input_tokens_reads_every_provider_shape(usage, expected):
    assert cached_input_tokens(usage) == expected


class _Gen:
    def __init__(self, text="ok"):
        self.text = text


class _Response:
    def __init__(self, prompt_tokens, completion_tokens, cached_tokens):
        self.generations = [[_Gen()]]
        self.llm_output = {"token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        }}


@pytest.fixture
def callback(tmp_path):
    loop = asyncio.new_event_loop()
    try:
        yield ChatStreamCallback(loop, asyncio.Queue(), [], tmp_path / "run.log")
    finally:
        loop.close()


def test_the_callback_sums_cache_reads_across_the_loop(callback):
    """Three steps of a loop whose prompt grows while its prefix stays cached."""
    for prompt, cached in ((1000, 0), (1200, 1000), (1400, 1200)):
        callback.on_llm_start({}, ["prompt"])
        callback.on_llm_end(_Response(prompt, 50, cached))

    assert callback.prompt_tokens == 3600
    assert callback.cached_prompt_tokens == 2200
    assert callback.completion_tokens == 150


def test_a_cache_read_can_never_exceed_its_own_prompt(callback):
    callback.on_llm_start({}, ["prompt"])
    callback.on_llm_end(_Response(100, 10, 999_999))
    assert callback.cached_prompt_tokens == 100


def test_the_count_survives_the_round_trip_through_a_run_record():
    """Cost aggregation reads ``process.token_usage`` off the stored run, so the
    cached count has to come back out of the database, not just go in."""
    rm.upsert_run({
        "run_id": "cache-roundtrip-1",
        "workspace": "ws",
        "provider": "openai",
        "model": "gpt-4o",
        "process": {"token_usage": {
            "inbound_tokens": 675_179,
            "outbound_tokens": 5_448,
            "total_tokens": 680_627,
            "cached_tokens": 650_876,
        }, "duration_ms": 159_000},
    })
    stored = rm.get_run_by_id("cache-roundtrip-1")
    assert stored["process"]["token_usage"]["cached_tokens"] == 650_876

    from common.pricing import run_cached_tokens, run_tokens
    assert run_tokens(stored) == (675_179, 5_448)
    assert run_cached_tokens(stored) == 650_876


def test_a_run_recorded_without_cache_data_reports_zero():
    rm.upsert_run({
        "run_id": "cache-roundtrip-2",
        "workspace": "ws",
        "provider": "openai",
        "model": "gpt-4o",
        "process": {"token_usage": {"inbound_tokens": 100, "outbound_tokens": 10},
                    "duration_ms": 5},
    })
    from common.pricing import run_cached_tokens
    assert run_cached_tokens(rm.get_run_by_id("cache-roundtrip-2")) == 0
