"""
Smoke tests for the two chat-pipeline modules that previously had none:
``chat/send.py`` (the blocking single-agent turn) and ``chat/streaming.py``
(the shared token-batching / result-assembly loop used by the SSE path).

No LLM or network call happens in any of these tests: the agent build/invoke
seam is stubbed, and the streaming test drives ``drive_streaming_run`` with a
fake queue and a fake asyncio.Task standing in for the agent coroutine.

Run: ``python -m pytest tests/test_chat_pipeline_smoke.py -q``
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat.models import ChatRequest
from chat.send import ChatSendError, send_chat_message
from chat.streaming import StreamDriveResult, drive_streaming_run
from managers.run_manager import get_run_by_id


def _fake_agent_spec():
    """Stand-in AgentSpec: just enough surface for validate_chat_request,
    agent_overrides and the Telegram capability guard to not blow up."""
    return SimpleNamespace(tools=[], model_overrides=lambda: {})


# ── chat/send.py ─────────────────────────────────────────────────────────────

def test_send_chat_message_stores_message_and_response(monkeypatch):
    import agents.registry as registry
    import agents.agent_invoke as agent_invoke
    import chat.send as send_mod

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: _fake_agent_spec())
    fake_agent = SimpleNamespace(provider="prov", model="mdl")
    monkeypatch.setattr(send_mod, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)

    fake_result = SimpleNamespace(ok=True, agent_output="Hi there", error=None)
    monkeypatch.setattr(
        agent_invoke, "invoke_agent",
        lambda *a, **kw: SimpleNamespace(result=fake_result),
    )

    req = ChatRequest(agent_id="test_agent", message="hello there")
    out = asyncio.run(send_chat_message(req))

    assert set(out.keys()) == {"response", "ok", "run_id"}
    assert out["ok"] is True
    assert out["response"] == "Hi there"

    rec = get_run_by_id(out["run_id"])
    assert rec["status"] == "completed"
    log_text = Path(rec["log_file"]).read_text()
    assert "hello there" in log_text  # the user message
    assert "Hi there" in log_text     # the agent's response


def test_send_chat_message_invoke_raises_maps_to_5xx(monkeypatch):
    import agents.registry as registry
    import agents.agent_invoke as agent_invoke
    import chat.send as send_mod

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: _fake_agent_spec())
    fake_agent = SimpleNamespace(provider="prov", model="mdl")
    monkeypatch.setattr(send_mod, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)

    def _raise(*a, **kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(agent_invoke, "invoke_agent", _raise)

    req = ChatRequest(agent_id="test_agent", message="hello")
    with pytest.raises(ChatSendError) as exc:
        asyncio.run(send_chat_message(req))

    assert exc.value.status >= 500
    assert "provider exploded" in exc.value.detail

    # The route layer relies on the run record reflecting the same failure.
    from managers.run_manager import load_runs
    matching = [r for r in load_runs() if r.get("error") == "provider exploded"]
    assert matching and matching[0]["status"] == "failed"


# ── chat/streaming.py ────────────────────────────────────────────────────────

def test_drive_streaming_run_batches_tokens_and_strips_ui_block(monkeypatch):
    import chat.streaming as streaming_mod

    # Not stopped at any point during the drive.
    monkeypatch.setattr(streaming_mod, "get_run", lambda run_id: {"status": "running"})

    async def _agent_coro():
        # Give the drive loop a chance to pull the pre-queued events before the
        # task resolves, exercising both the "task not done" drain path and the
        # final drain-what's-left path.
        await asyncio.sleep(0.05)
        return SimpleNamespace(ok=True, agent_output="", error=None, response=None)

    async def _drive():
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put({"type": "token", "token": "Hello "})
        await queue.put({"type": "tool_start", "tool": "search"})
        # No structured response is registered under "nope", so this exercises
        # the "UI block present but unknown kind -> stripped, plain text kept"
        # path in agents.agent_response.parse_agent_response.
        await queue.put({"type": "token", "token": 'world<<<ui>>>{"kind": "nope"}<<<end>>>'})

        task = asyncio.ensure_future(_agent_coro())
        result = StreamDriveResult()
        callback = SimpleNamespace(
            prompt_tokens=1, completion_tokens=2, total_tokens=3,
            cached_prompt_tokens=0, tool_calls=1,
            tool_history=[], thinking_history=[], llm_invocations=[],
            llm_invoke_responses=[], artifact_history=[],
            cancelled=False, _last_prompt_struct={},
            context_usage=lambda: {},
        )
        events = []
        async for ev in drive_streaming_run(
            task=task, queue=queue, callback=callback, run_id="run-1",
            full_prompt="hello", started_ts=time.perf_counter(), result=result,
        ):
            events.append(ev)
        return events, result

    events, result = asyncio.run(_drive())

    types = [e["type"] for e in events]
    assert types.count("token") == 2
    assert "tool_start" in types
    assert result.ok is True
    # Tokens joined back together, with the trailing UI marker block removed.
    assert result.response == "Hello world"
    assert "<<<ui>>>" not in result.response
    assert result.stopped is False
