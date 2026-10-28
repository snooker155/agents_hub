"""ToolRepetitionGuard (agents/callbacks/guards.py): only the same call with the
same arguments repeated past the limit stops a run; one tool called with
different arguments (a researcher opening page after page) is ordinary work."""
from __future__ import annotations

import pytest

from agents.callbacks.guards import ToolRepetitionError, ToolRepetitionGuard


def _call(guard, name, args):
    guard.on_tool_start({"name": name}, args)
    guard.on_tool_end("ok")


def test_one_tool_with_different_arguments_never_stops():
    guard = ToolRepetitionGuard(max_repeats=3)
    for i in range(20):
        _call(guard, "fetch_url", f'{{"url": "https://example.com/{i}"}}')


def test_the_same_call_repeated_past_the_limit_stops():
    guard = ToolRepetitionGuard(max_repeats=3)
    for _ in range(3):
        _call(guard, "fetch_url", '{"url": "https://a", "max_chars": 10}')
    with pytest.raises(ToolRepetitionError, match="same arguments"):
        # Key order does not make it a different call.
        _call(guard, "fetch_url", '{"max_chars": 10, "url": "https://a"}')


def test_think_between_identical_calls_does_not_break_the_streak():
    guard = ToolRepetitionGuard(max_repeats=2)
    _call(guard, "fetch_url", '{"url": "https://a"}')
    _call(guard, "think", '{"thought": "hm"}')
    _call(guard, "fetch_url", '{"url": "https://a"}')
    with pytest.raises(ToolRepetitionError):
        _call(guard, "fetch_url", '{"url": "https://a"}')


def test_another_call_resets_the_streak():
    guard = ToolRepetitionGuard(max_repeats=2)
    _call(guard, "fetch_url", '{"url": "https://a"}')
    _call(guard, "fetch_url", '{"url": "https://a"}')
    _call(guard, "fetch_url", '{"url": "https://b"}')
    _call(guard, "fetch_url", '{"url": "https://a"}')
    _call(guard, "fetch_url", '{"url": "https://a"}')
