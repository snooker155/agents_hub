"""Global agent-streaming setting: resolution and per-callback effects.

The setting decides whether every agent is built with a streaming LLM, which is
what makes non-chat surfaces show live output and what turns each token into a
stop checkpoint. These tests pin the three things that are easy to break: how the
flag is resolved, that it stays live rather than frozen at import, and that the
per-token stop check does not turn into a database read per token.
"""
import pytest

from agents.agent_factory import resolve_streaming
from agents.callbacks.control import RunStopCallback
from agents.callbacks.streaming import SessionPublishCallback
from common import config


@pytest.fixture
def dot_env(monkeypatch):
    """Control what ``streaming_enabled()`` reads, without touching the real .env."""
    state = {}

    monkeypatch.setattr(config, "read_dot_env", lambda: dict(state))
    monkeypatch.delenv("AGENT_STREAMING", raising=False)
    return state


# ── resolution ───────────────────────────────────────────────────────────────

def test_off_by_default(dot_env):
    assert config.streaming_enabled() is False
    assert resolve_streaming(None, {}) is False


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("1", True), ("on", True), ("yes", True),
    ("false", False), ("False", False), ("0", False), ("", False),
])
def test_env_flag_shapes(dot_env, raw, expected):
    dot_env["AGENT_STREAMING"] = raw
    assert config.streaming_enabled() is expected


def test_setting_drives_every_agent(dot_env):
    dot_env["AGENT_STREAMING"] = "true"
    assert resolve_streaming(None, {}) is True


def test_explicit_override_wins_both_ways(dot_env):
    """The chat pipeline forces streaming on; the extractors force it off."""
    dot_env["AGENT_STREAMING"] = "true"
    assert resolve_streaming(None, {"streaming": False}) is False
    dot_env["AGENT_STREAMING"] = "false"
    assert resolve_streaming(None, {"streaming": True}) is True


def test_agent_record_can_only_force_on(dot_env):
    """A stored ``false`` is indistinguishable from "not configured", so it must
    not veto the global setting."""
    dot_env["AGENT_STREAMING"] = "false"
    assert resolve_streaming(True, {}) is True
    dot_env["AGENT_STREAMING"] = "true"
    assert resolve_streaming(False, {}) is True


def test_env_var_is_the_fallback_source(dot_env, monkeypatch):
    monkeypatch.setenv("AGENT_STREAMING", "true")
    assert config.streaming_enabled() is True
    dot_env["AGENT_STREAMING"] = "false"   # the file wins over the process env
    assert config.streaming_enabled() is False


# ── stop checks under streaming ──────────────────────────────────────────────

def test_token_stop_check_is_throttled(monkeypatch):
    """Hundreds of tokens must not become hundreds of run-store lookups."""
    reads = []
    monkeypatch.setattr(
        "managers.run_manager.get_run_by_id",
        lambda run_id: reads.append(run_id) or {"status": "running"},
    )
    cb = RunStopCallback("run-1")
    for _ in range(500):
        cb.on_llm_new_token("x")
    assert len(reads) == 1          # one poll for the whole burst
    assert cb.cancelled is False


def test_stop_aborts_on_the_next_token(monkeypatch):
    """A cancelled flag is honoured immediately, without waiting for the poll."""
    monkeypatch.setattr("managers.run_manager.get_run_by_id", lambda run_id: {"status": "running"})
    cb = RunStopCallback("run-1")
    cb.on_llm_new_token("x")
    cb.cancelled = True
    with pytest.raises(InterruptedError):
        cb.on_llm_new_token("y")


def test_tool_start_still_polls_every_time(monkeypatch):
    """Tool boundaries are rare and are the important abort points."""
    reads = []
    monkeypatch.setattr(
        "managers.run_manager.get_run_by_id",
        lambda run_id: reads.append(run_id) or {"status": "running"},
    )
    cb = RunStopCallback("run-1")
    for _ in range(3):
        cb.on_tool_start({}, "in")
    assert len(reads) == 3


# ── token publishing ─────────────────────────────────────────────────────────

def _capture(cb):
    sent = []
    cb._send = sent.append
    return sent


def test_tokens_are_batched_into_one_event():
    cb = SessionPublishCallback("sess", "run-1", "agent")
    sent = _capture(cb)
    for ch in "hello world":
        cb.on_llm_new_token(ch)
    assert sent == []               # nothing shipped yet: below both bounds
    cb.flush()
    assert sent == [{"type": "token", "token": "hello world", "run_id": "run-1"}]


def test_large_burst_flushes_by_size():
    cb = SessionPublishCallback("sess", "run-1", "agent")
    sent = _capture(cb)
    for _ in range(400):
        cb.on_llm_new_token("x")
    # Shipped in chunks as the size bound is crossed, rather than accumulating
    # the whole completion; the remainder rides on the next flush.
    assert len(sent) >= 2, "a long completion must not sit unpublished in the buffer"
    cb.flush()
    assert all(e["type"] == "token" for e in sent)
    assert "".join(e["token"] for e in sent) == "x" * 400


def test_non_token_events_flush_first():
    """Ordering: text the model produced before a tool call must arrive first."""
    cb = SessionPublishCallback("sess", "run-1", "agent")
    sent = _capture(cb)
    cb.on_llm_new_token("thinking out loud")
    cb.on_tool_start({"name": "read_file"}, "path.py")
    assert [e["type"] for e in sent] == ["token", "tool_start"]


def test_done_carries_usage_and_flushes_tail():
    class _Stats:
        prompt_tokens, completion_tokens, total_tokens, tool_calls = 10, 20, 30, 2

    class _Inv:
        stats = _Stats()
        duration_ms = 1234

    class _Result:
        ok = True
        agent_output = "final answer"

    cb = SessionPublishCallback("sess", "run-1", "agent")
    sent = _capture(cb)
    cb.on_llm_new_token("tail")
    cb.publish_done(_Result(), _Inv())

    assert sent[0] == {"type": "token", "token": "tail", "run_id": "run-1"}
    done = sent[1]
    assert done["type"] == "done"
    assert done["ok"] is True
    assert done["response"] == "final answer"
    assert done["usage"] == {"inbound_tokens": 10, "outbound_tokens": 20,
                             "total_tokens": 30, "cached_tokens": 0}
    assert done["duration_ms"] == 1234


def test_done_reports_a_stopped_run_as_not_ok():
    class _Inv:
        stats = None
        duration_ms = 5

    class _Result:
        ok = True
        agent_output = "half an answer"

    cb = SessionPublishCallback("sess", "run-1", "agent")
    sent = _capture(cb)
    cb.publish_done(_Result(), _Inv(), stopped=True)
    assert sent[0]["ok"] is False
    assert "stopped" in sent[0]["response"]
