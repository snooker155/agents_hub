"""Budget enforcement for the chat pipeline.

The launcher, evals and loops all refuse to start a run once a workspace's
hard budget cap is met (common.budget.check_budget). Chat had no such gate:
a workspace could keep spending turn after turn with no run ever refusing to
start. send_chat_message now checks the budget before the agent is built or
invoked, and maps a breach to ChatSendError(status=402).

Everything ahead of that check (validation, attachments, references, the run
record) is stubbed out so the test exercises only the new gating logic, not
the rest of the pipeline's DB/session bookkeeping.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agents.agent_invoke as agent_invoke
import chat.send as chat_send
import common.budget as budget
from chat.models import ChatRequest
from chat.send import ChatSendError, send_chat_message


def _request(**overrides) -> ChatRequest:
    fields = dict(agent_id="demo-agent", message="hello", workspace="ws-budget-test")
    fields.update(overrides)
    return ChatRequest(**fields)


@pytest.fixture(autouse=True)
def stub_pipeline(monkeypatch):
    monkeypatch.setattr(chat_send, "validate_chat_request", lambda request: None)
    monkeypatch.setattr(chat_send, "materialize_attachments", lambda request: None)
    monkeypatch.setattr(chat_send, "resolve_references", lambda request: None)
    monkeypatch.setattr(
        chat_send, "build_chat_context",
        lambda request: ("prompt", "/tmp/ws-budget-test"),
    )
    monkeypatch.setattr(
        chat_send, "create_chat_run",
        lambda request: ("run-1", "msg-1", "/tmp/run-1.log", [], None),
    )
    monkeypatch.setattr(
        chat_send, "apply_workspace_ctx",
        lambda request, workspace_abs: "ws-budget-test",
    )
    monkeypatch.setattr(chat_send, "_write_log", lambda *a, **k: None)
    monkeypatch.setattr(chat_send, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(chat_send, "get_run", lambda run_id: {"status": "completed"})
    monkeypatch.setattr(chat_send, "get_pool_id", lambda *a, **k: None)
    monkeypatch.setattr(chat_send, "auto_journal", lambda *a, **k: None)
    monkeypatch.setattr(chat_send, "agent_overrides", lambda agent_id: {})


def test_budget_exceeded_blocks_the_turn_before_the_agent_is_built(monkeypatch):
    def _raise(workspace):
        raise budget.BudgetExceededError(workspace, 12.0, 10.0, "monthly")

    monkeypatch.setattr(budget, "check_budget", _raise)

    create_calls = []
    monkeypatch.setattr(
        chat_send, "create_agent",
        lambda *a, **k: create_calls.append((a, k)) or SimpleNamespace(),
    )

    with pytest.raises(ChatSendError) as exc_info:
        asyncio.run(send_chat_message(_request()))

    assert exc_info.value.status == 402
    assert "budget cap" in exc_info.value.detail
    # The agent was never built, so it was certainly never invoked.
    assert create_calls == []


def test_budget_under_cap_lets_the_turn_proceed(monkeypatch):
    monkeypatch.setattr(budget, "check_budget", lambda workspace: None)

    fake_agent = SimpleNamespace(provider="anthropic", model="claude-test")
    monkeypatch.setattr(chat_send, "create_agent", lambda *a, **k: fake_agent)

    class _Result:
        ok = True
        agent_output = "hi there"
        error = None

    class _Invocation:
        result = _Result()

    invoked = []

    def _fake_invoke_agent(agent, prompt, **kwargs):
        invoked.append((agent, prompt))
        return _Invocation()

    monkeypatch.setattr(agent_invoke, "invoke_agent", _fake_invoke_agent)

    result = asyncio.run(send_chat_message(_request()))

    assert result == {"response": "hi there", "ok": True, "run_id": "run-1"}
    assert len(invoked) == 1
