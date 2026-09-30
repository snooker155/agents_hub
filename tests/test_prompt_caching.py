"""Marking the part of the prompt that never changes, so it is not paid for twice.

Every turn and every step of an agent loop re-sends the same system prompt.
Anthropic will serve that prefix from its cache at a fraction of the input
price, but only when the prompt says which block to cache; OpenAI does it on its
own, provided the prefix is byte-identical from call to call, which is what
sending the history as messages buys.

What has to hold: the marker goes on an Anthropic system prompt that is big
enough for Anthropic to keep, it goes nowhere else, nothing per-turn creeps into
the system prompt, and the cache reads the provider reports land in the run's
cached-token count at the cached price.

Run: ``python -m pytest tests/test_prompt_caching.py -q``
"""
from __future__ import annotations

import asyncio

import pytest

from agents.agent_base import AgentBase
from providers.adapters import (
    MIN_CACHEABLE_CHARS,
    cacheable_content,
    is_cache_marked,
    supports_prompt_cache_control,
)

BIG = "You are a careful agent. " * 400      # well over the minimum
SMALL = "You are a careful agent."


class _Agent(AgentBase):
    def run(self, instruction, **kwargs):  # pragma: no cover - never invoked
        raise NotImplementedError


def _system_message(provider, system_prompt, llm=None):
    agent = _Agent(agent_id="a", name="A", system_prompt=system_prompt, tools=[],
                   provider=provider, model="m")
    return agent._system_message(llm)


# ── which prompts get the marker ─────────────────────────────────────────────

def test_a_large_anthropic_system_prompt_is_marked_for_the_cache():
    message = _system_message("anthropic", BIG)

    assert isinstance(message.content, list)
    assert message.content[0]["type"] == "text"
    assert message.content[0]["text"] == BIG
    assert message.content[0]["cache_control"] == {"type": "ephemeral"}


def test_a_small_anthropic_system_prompt_is_left_alone():
    """Anthropic ignores a cacheable block under ~1024 tokens, so marking one
    buys a cache write with no read to follow it."""
    assert len(SMALL) < MIN_CACHEABLE_CHARS
    assert _system_message("anthropic", SMALL).content == SMALL


def test_openai_needs_no_marker():
    """It caches stable prefixes by itself; a block list would only be noise."""
    assert _system_message("openai", BIG).content == BIG
    assert not supports_prompt_cache_control("openai")


@pytest.mark.parametrize("provider", ["google", "ollama", "lmstudio", "my-gateway", ""])
def test_no_other_provider_is_sent_a_marker(provider):
    assert _system_message(provider, BIG).content == BIG


def test_an_inherited_provider_is_read_off_the_model_that_was_built():
    """``provider=None`` means "whatever the workspace default is", which is only
    known once the chat model exists."""

    class ChatAnthropic:  # stands in for the real class by name
        pass

    assert is_cache_marked(_system_message(None, BIG, llm=ChatAnthropic()).content)

    class ChatOpenAI:
        pass

    assert not is_cache_marked(_system_message(None, BIG, llm=ChatOpenAI()).content)


def test_the_marker_never_changes_the_prompt_text():
    marked = cacheable_content("anthropic", BIG)
    assert marked[0]["text"] == BIG
    assert cacheable_content("anthropic", "") == ""


# ── the summary message ──────────────────────────────────────────────────────

def test_the_summary_is_marked_when_the_prefix_it_closes_is_worth_caching():
    """A summary is short by design, but it sits right behind the system prompt,
    and it is the pair that Anthropic measures."""
    from chat.compaction import summary_message

    marked = summary_message("a short summary", provider="anthropic", system_prompt=BIG)
    assert is_cache_marked(marked.content)

    unmarked = summary_message("a short summary", provider="anthropic", system_prompt="")
    assert not is_cache_marked(unmarked.content)

    other = summary_message("a short summary", provider="openai", system_prompt=BIG)
    assert not is_cache_marked(other.content)


# ── the system prompt has to be the same next turn ───────────────────────────

def test_nothing_per_turn_reaches_the_system_prompt():
    """A timestamp or a per-turn note in the system prompt would invalidate the
    cached prefix on every single turn. This turn's context belongs in the human
    message, where it already is."""
    from chat.context import build_chat_context
    from chat.models import ChatAttachment, ChatRequest

    request = ChatRequest(
        agent_id="a", message="what changed?",
        attachments=[ChatAttachment(filename="notes.txt", content="a note")],
    )
    prompt, _ = build_chat_context(request)
    assert "notes.txt" in prompt and "a note" in prompt

    agent = _Agent(agent_id="a", name="A", system_prompt=BIG, tools=[],
                   provider="anthropic", model="m")
    first = agent._system_message(None).content
    second = agent._system_message(None).content
    assert first == second


# ── what the cache reads cost ────────────────────────────────────────────────

class _Gen:
    def __init__(self, text="ok"):
        self.text = text


class _AnthropicResponse:
    """Anthropic reports its cache reads at the top level of the usage block."""

    def __init__(self, prompt_tokens, cached):
        self.generations = [[_Gen()]]
        self.llm_output = {"token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 40,
            "total_tokens": prompt_tokens + 40,
            "cache_read_input_tokens": cached,
        }}


@pytest.fixture
def callback(tmp_path):
    from agents.callbacks.chat_stream import ChatStreamCallback
    loop = asyncio.new_event_loop()
    try:
        yield ChatStreamCallback(loop, asyncio.Queue(), [], tmp_path / "run.log")
    finally:
        loop.close()


def test_anthropic_cache_reads_land_in_the_cached_token_count(callback):
    callback.on_llm_start({}, ["prompt"])
    callback.on_llm_end(_AnthropicResponse(prompt_tokens=12_000, cached=11_000))

    assert callback.prompt_tokens == 12_000
    assert callback.cached_prompt_tokens == 11_000


def test_the_run_stats_callback_reads_the_same_field():
    from agents.callbacks.run_statistics import RunStatsCallback

    stats = RunStatsCallback()
    stats.on_llm_start({}, ["prompt"])
    stats.on_llm_end(_AnthropicResponse(prompt_tokens=9_000, cached=8_500))

    assert stats.cached_prompt_tokens == 8_500
    assert stats.build_process(1)["token_usage"]["cached_tokens"] == 8_500


def test_a_cached_token_is_priced_at_the_cached_rate():
    from common import pricing

    run = {
        "provider": "anthropic", "model": "claude-x",
        "process": {"token_usage": {
            "inbound_tokens": 100_000, "outbound_tokens": 1_000,
            "total_tokens": 101_000, "cached_tokens": 90_000,
        }},
    }
    prices = {("anthropic", "claude-x"): (3.0, 15.0, 0.3)}
    cost = pricing.run_cost_usd(run, prices)

    fresh = 10_000 / 1_000_000 * 3.0
    cached = 90_000 / 1_000_000 * 0.3
    out = 1_000 / 1_000_000 * 15.0
    assert cost == pytest.approx(fresh + cached + out)
    # …and it is genuinely cheaper than pricing the whole prompt as fresh input.
    assert cost < 100_000 / 1_000_000 * 3.0 + out
