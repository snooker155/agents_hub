"""Running out of context window, made visible before it bites.

The chats that keep their own transcript (the Studio, the architect and planner
chats, every entity build chat) re-send the whole conversation each turn, so
they grow until the model refuses them. Two things have to hold for the user to
see that coming instead of hitting a wall:

* every ``usage`` event says how full the window is, against the window of the
  model that actually ran;
* the failure, when it does happen, is labelled as *this* failure rather than
  arriving as a provider string the UI can only print in red.
"""
import asyncio

import pytest

from agents.callbacks.chat_stream import ChatStreamCallback
from chat.errors import CONTEXT_OVERFLOW, error_code, error_event
from providers.context_windows import is_context_overflow


class _Gen:
    def __init__(self, text=""):
        self.text = text


class _Response:
    """What on_llm_end reads: generations plus reported token usage."""

    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.generations = [[_Gen("ok")]]
        self.llm_output = {"token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }}


@pytest.fixture
def callback(tmp_path):
    loop = asyncio.new_event_loop()
    try:
        yield ChatStreamCallback(loop, asyncio.Queue(), [], tmp_path / "run.log")
    finally:
        loop.close()


def _events(cb):
    """Drain what the callback emitted.

    It posts through ``loop.call_soon_threadsafe`` (it runs in the agent's
    worker thread in production), so the loop has to turn once before the
    queue holds anything.
    """
    cb.loop.run_until_complete(asyncio.sleep(0))
    out = []
    while not cb.queue.empty():
        out.append(cb.queue.get_nowait())
    return out


# ── classifying the failure ───────────────────────────────────────────────────

@pytest.mark.parametrize("message", [
    # Our own guard, which fires when the backend accepted an over-sized prompt.
    "Context window exceeded: the last prompt was 210000 tokens but model "
    "'claude-x' accepts at most 200000.",
    # …and the other half: the request the backend refused outright.
    "Error code: 400 - This model's maximum context length is 8192 tokens, "
    "however you requested 9000 tokens",
    "prompt is too long: 205000 tokens > 200000 maximum",
    "The input token count (1500000) exceeds the maximum number of tokens allowed",
    "ValueError: the request exceeds the available KV cache size",
])
def test_an_overfull_context_is_recognised_whoever_reported_it(message):
    assert is_context_overflow(message)


@pytest.mark.parametrize("message", [
    "tool 'read_file' failed: no such file",
    "Connection reset by peer",
    "rate limit exceeded, retry in 20s",
    "",
])
def test_ordinary_failures_are_left_alone(message):
    """Mislabelling these would tell the user to clear a chat that is fine."""
    assert not is_context_overflow(message)
    assert error_code(message) is None


def test_the_error_event_carries_the_code_the_ui_acts_on():
    ev = error_event("agent", "prompt is too long: 205000 tokens > 200000")
    assert ev["type"] == "error" and ev["source"] == "agent"
    assert ev["code"] == CONTEXT_OVERFLOW

    plain = error_event("agent", "tool failed")
    assert "code" not in plain


# ── reporting the fill ────────────────────────────────────────────────────────

def test_usage_events_carry_the_window_of_the_model_that_ran(callback, monkeypatch):
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)
    callback.bind_model("anthropic", "claude-x")

    callback.on_llm_start({}, ["prompt"])
    callback.on_llm_end(_Response(prompt_tokens=120_000, completion_tokens=500))

    usage = [e for e in _events(callback) if e["type"] == "usage"]
    assert usage == [{
        "type": "usage", "prompt_tokens": 120_000, "completion_tokens": 500,
        "total_tokens": 120_500, "cached_tokens": 0, "estimated": False,
        "context_window": 200_000, "context_used": 120_000,
    }]


def test_the_fill_is_the_largest_prompt_not_the_running_total(callback, monkeypatch):
    """Each call re-sends the conversation, so the calls do not add up: what has
    to fit the window is the biggest single prompt."""
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 200_000)
    callback.bind_model("anthropic", "claude-x")

    for prompt_tokens in (30_000, 90_000, 70_000):
        callback.on_llm_start({}, ["prompt"])
        callback.on_llm_end(_Response(prompt_tokens=prompt_tokens))

    assert callback.context_usage() == {
        "context_window": 200_000, "context_used": 90_000,
    }
    # The cost of the turn is still the sum — a different question, kept apart.
    assert callback.prompt_tokens == 190_000


def test_an_unknown_window_reports_nothing_rather_than_a_guess(callback, monkeypatch):
    """A made-up ceiling would be worse than none: the meter stays hidden."""
    monkeypatch.setattr("providers.context_windows.get_model_context_window",
                        lambda provider, model: 0)
    callback.bind_model("ollama", "some-local-model")

    callback.on_llm_start({}, ["prompt"])
    callback.on_llm_end(_Response(prompt_tokens=1_000))

    assert callback.context_usage()["context_window"] == 0
