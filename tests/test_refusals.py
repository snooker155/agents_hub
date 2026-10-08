"""
Refusals as data (chat/refusals.py): the capability guard and the spend limits
reach the chat as a structure with a code, the agent and the numbers, beside
the plain message.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from chat import refusals  # noqa: E402


def _violation():
    from agents.capability_guard import CapabilityViolation
    from tools.capabilities import Violation
    v = Violation(rule_id="lethal_trifecta", title="Lethal trifecta", explanation="Too much.",
                  capabilities=frozenset({"private_data", "untrusted_content"}),
                  sources={"private_data": ["read_file"], "untrusted_content": ["web_fetch"]})
    return CapabilityViolation("scout", v)


def test_capability_violation_becomes_a_structured_refusal():
    out = refusals.refusal_of(_violation())
    assert out["code"] == "capability_guard" and out["agent_id"] == "scout"
    assert out["capabilities"] == ["private_data", "untrusted_content"]
    assert out["sources"]["private_data"] == ["read_file"]
    assert out["override_allowed"] is True
    assert "Agent 'scout'" in out["message"]


def test_system_workspace_rule_has_no_override():
    from agents.capability_guard import CapabilityViolation
    from tools.capabilities import SYSTEM_WORKSPACE_RULE_ID, Violation
    v = Violation(rule_id=SYSTEM_WORKSPACE_RULE_ID, title="t", explanation="e", capabilities=frozenset())
    assert refusals.refusal_of(CapabilityViolation("x", v))["override_allowed"] is False


def test_workspace_and_person_budgets_are_told_apart():
    from common.budget import BudgetExceededError
    from common.user_budget import UserBudgetExceededError
    ws = refusals.refusal_of(BudgetExceededError("team", 12.0, 10.0, "monthly"), agent_id="a")
    assert (ws["code"], ws["kind"], ws["workspace"], ws["limit_usd"], ws["spent_usd"]) == (
        "budget", "workspace", "team", 10.0, 12.0)
    person = refusals.refusal_of(UserBudgetExceededError("u1", 5.0, 5.0, "ann"))
    assert person["kind"] == "person" and person["user_id"] == "u1"


def test_an_ordinary_error_is_not_a_refusal():
    assert refusals.refusal_of(RuntimeError("boom")) is None


def test_the_route_detail_carries_the_refusal(monkeypatch):
    import asyncio
    import pytest
    from fastapi import HTTPException
    from routes import chat as chat_route
    from chat.send import ChatSendError
    from common.budget import BudgetExceededError
    refusal = refusals.refusal_of(BudgetExceededError("w", 3.0, 2.0, "monthly"))

    async def boom(request):
        raise ChatSendError("over", status=402, refusal=refusal)
    monkeypatch.setattr(chat_route, "send_chat_message", boom)
    with pytest.raises(HTTPException) as info:
        asyncio.run(chat_route.send_message(object()))
    assert info.value.status_code == 402
    assert info.value.detail["message"] == "over" and info.value.detail["refusal"]["kind"] == "workspace"


def test_a_refused_build_reaches_the_drive_result_as_a_refusal(monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace
    import chat.streaming as streaming_mod
    monkeypatch.setattr(streaming_mod, "get_run", lambda run_id: {"status": "running"})
    exc = _violation()

    async def _build():
        raise exc

    async def _drive():
        task = asyncio.ensure_future(_build())
        result = streaming_mod.StreamDriveResult()
        callback = SimpleNamespace(
            prompt_tokens=0, completion_tokens=0, total_tokens=0, cached_prompt_tokens=0, tool_calls=0,
            tool_history=[], thinking_history=[], llm_invocations=[], llm_invoke_responses=[],
            artifact_history=[], cancelled=False, _last_prompt_struct={}, context_usage=lambda: {})
        async for _ in streaming_mod.drive_streaming_run(
                task=task, queue=asyncio.Queue(), callback=callback, run_id="r", full_prompt="x",
                started_ts=time.perf_counter(), result=result):
            pass
        return result

    result = asyncio.run(_drive())
    assert result.ok is False and result.refusal["code"] == "capability_guard"
    assert result.refusal["agent_id"] == "scout"
