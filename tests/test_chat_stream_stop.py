"""A stopped run is not an error: the cancellation that reaches the error
callbacks stays out of the chat (agents/callbacks/chat_stream.py)."""
import asyncio

import pytest

from agents.callbacks.chat_stream import ChatStreamCallback


@pytest.fixture
def cb(tmp_path):
    loop = asyncio.new_event_loop()
    try:
        yield ChatStreamCallback(loop, asyncio.Queue(), [], tmp_path / "run.log")
    finally:
        loop.close()


def _events(cb):
    cb.loop.run_until_complete(asyncio.sleep(0))
    out = []
    while not cb.queue.empty():
        out.append(cb.queue.get_nowait())
    return out


def test_cancelled_run_emits_no_error(cb):
    cb.on_llm_error(asyncio.CancelledError())
    cb.on_chain_error(asyncio.CancelledError())
    assert _events(cb) == []
    assert cb.log_lines.count("[stopped] run stopped by user") == 1


def test_stop_flag_interrupt_emits_no_error(cb):
    cb.cancelled = True
    cb.on_chain_error(InterruptedError("Run stopped by user"))
    assert _events(cb) == []


def test_real_failure_still_reported(cb):
    cb.on_chain_error(RuntimeError("boom"))
    assert [e["type"] for e in _events(cb)] == ["error"]
