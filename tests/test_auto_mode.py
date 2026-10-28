"""
Auto mode (common/auto_mode.py): a chat whose person approved every
confirmation in advance.

The guard is driven directly, as in tests/test_tool_approvals_chat.py: a
dashboard chat turn with a recording stream emitter, no model. Auto mode
comes either from the turn (``set_turn``) or from the run record, which is
what a process that only has the run id reads.
"""
from __future__ import annotations

import json
import uuid

import pytest

from agents import hooks
from common import auto_mode, stream_sink, tool_approvals
from tools import permission_policy as policy


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


def _open_run(run_id: str, **kw):
    from managers import run_manager
    run_manager.open_run(run_id, "swe_agent", link_to_session=False, status="running",
                         session_type="chat", message_origin=kw.pop("origin", "chat"),
                         task_id=f"conv-{uuid.uuid4().hex[:6]}", workspace="acme", **kw)


@pytest.fixture
def settings(monkeypatch):
    """The workspace settings every read sees; tests change the dict."""
    values = {"require_tool_approval": True}
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": values})
    monkeypatch.setattr(tool_approvals, "POLL_SECONDS", 0.02)
    return values


@pytest.fixture
def chat_turn(settings):
    from agents.agent_loop import LoopState, reset_state, set_state
    tokens = []
    emitters = []

    def _make(**run_fields):
        run_id = _rid()
        _open_run(run_id, **run_fields)
        tokens.append(set_state(LoopState(run_id=run_id, agent_id="swe_agent", workspace="acme")))
        events: list = []
        emitters.append(stream_sink.set_emitter(events.append))
        return run_id, events
    yield _make
    # The loop state and the emitter are context variables of the test
    # thread: left set, the next test in this worker would run "inside" this turn.
    for token in reversed(emitters):
        stream_sink.reset_emitter(token)
    for token in reversed(tokens):
        reset_state(token)



def _guard():
    return hooks.ToolGuard(agent_id="swe_agent", workspace="acme")


def test_a_held_call_runs_at_once_and_the_card_says_auto_mode(chat_turn):
    run_id, events = chat_turn()
    token = auto_mode.set_turn(True)
    try:
        result = _guard().before("run_shell", {"command": "rm -rf build"})
    finally:
        auto_mode.reset_turn(token)
    assert result is None  # None: run the call, nobody waited
    card = next(e for e in events if e["type"] == "tool_approval")
    resolved = next(e for e in events if e["type"] == "tool_approval_resolved")
    assert card["auto"] is True and resolved["status"] == "approved"
    assert resolved["decided_by_name"] == auto_mode.DECIDED_BY
    row = tool_approvals.get(card["approval_id"])
    assert row["status"] == "approved" and row["by"] == auto_mode.BY
    verdict = policy.call_verdict("run_shell", object(), {"command": "rm -rf build"})
    assert verdict == {"evaluated_permission": "allow", "reason_code": "auto_mode_approved"}


def test_the_run_record_carries_auto_mode_to_another_process(chat_turn):
    run_id, events = chat_turn(auto_approve=True)
    assert auto_mode.is_on(run_id)
    assert _guard().before("run_shell", {"command": "ls"}) is None
    assert any(e.get("auto") for e in events)


def test_without_auto_mode_the_call_still_waits(chat_turn, monkeypatch):
    monkeypatch.setattr(tool_approvals, "timeout_seconds", lambda settings=None: 0.2)
    run_id, events = chat_turn()
    token = auto_mode.set_turn(False)
    try:
        body = json.loads(_guard().before("run_shell", {"command": "ls"}))
    finally:
        auto_mode.reset_turn(token)
    assert body["code"] == "approval_timeout"


def test_a_denied_call_stays_denied(chat_turn, monkeypatch):
    """A refusal is not a confirmation: a hook that denies wins over auto mode."""
    monkeypatch.setattr(hooks, "run_pre_tool_use",
                        lambda *a, **k: hooks.HookOutcome(decision="deny", reason="not here"))
    run_id, events = chat_turn(auto_approve=True)
    assert _guard().before("run_shell", {"command": "ls"}) == "not here"
    assert not any(e.get("type") == "tool_approval" for e in events)


def test_user_approved_gates_pass_and_are_recorded(chat_turn):
    run_id, events = chat_turn()
    assert auto_mode.approved("run_team_tool", True) is True
    assert auto_mode.approved("run_team_tool", False, {"team_id": "t"}) is False
    token = auto_mode.set_turn(True)
    try:
        assert auto_mode.approved("run_team_tool", False, {"team_id": "t"}) is True
        # The person has to type into this card: auto mode never answers it.
        assert auto_mode.approved("propose_connection", False) is False
    finally:
        auto_mode.reset_turn(token)
    rows = tool_approvals.list_for_run(run_id)
    assert [r["tool"] for r in rows] == ["run_team_tool"]
    assert rows[0]["status"] == "approved" and rows[0]["input"] == {"team_id": "t"}
    assert any(e.get("type") == "tool_approval_resolved" and e.get("auto") for e in events)


def test_the_stop_run_gate_uses_auto_mode(chat_turn, monkeypatch):
    from tools import service_ops
    run_id, _ = chat_turn()
    refused = json.loads(service_ops.stop_run.invoke({"run_id": "r-1"}))
    assert refused["code"] == "approval_required"
    token = auto_mode.set_turn(True)
    try:
        out = service_ops.stop_run.invoke({"run_id": "r-1"})
    finally:
        auto_mode.reset_turn(token)
    assert "approval_required" not in out


def test_only_interactive_chats_in_allowing_workspaces(settings):
    assert auto_mode.allowed_for("acme", None)
    assert auto_mode.allowed_for("acme", "assistant-chat")
    assert not auto_mode.allowed_for("acme", "telegram")
    assert not auto_mode.allowed_for("acme", "widget")
    settings["allow_auto_approve"] = False
    assert not auto_mode.allowed_for("acme", "chat")


def test_the_turn_prompt_says_auto_mode_is_on():
    from chat.context import context_block_lines
    from chat.models import ChatRequest
    on = ChatRequest(agent_id="swe_agent", message="hi", auto_approve=True)
    off = ChatRequest(agent_id="swe_agent", message="hi")
    assert any("Auto mode is on" in line for line in context_block_lines(on))
    assert not any("Auto mode" in line for line in context_block_lines(off))


def test_a_turn_from_elsewhere_runs_without_auto_mode(settings, monkeypatch):
    """execute_locally drops the flag where auto mode is not allowed, before
    the pipeline (and the run record) sees it."""
    import asyncio
    from chat import pipelines
    from chat.models import ChatRequest

    seen = {}

    async def _pipeline(request):
        seen["flag"] = request.auto_approve
        seen["turn"] = auto_mode.is_on("no-such-run")
        yield {"type": "done"}

    monkeypatch.setattr(pipelines, "_local_pipeline", lambda request, kind: _pipeline(request))

    async def _drive(request):
        return [e async for e in pipelines.execute_locally(request, "agent")]

    asyncio.run(_drive(ChatRequest(agent_id="swe_agent", message="hi", auto_approve=True,
                                   source="telegram", workspace="acme")))
    assert seen == {"flag": False, "turn": False}
    asyncio.run(_drive(ChatRequest(agent_id="swe_agent", message="hi", auto_approve=True,
                                   workspace="acme")))
    assert seen == {"flag": True, "turn": True}


def test_workspace_policy_route_switches_auto_mode(settings, monkeypatch):
    """The admin switch lives in the workspace's tool policy."""
    stored = {"settings": dict(settings)}
    monkeypatch.setattr("dashboard.backend.routes.workspaces.get_workspace_metadata",
                        lambda name: stored)
    monkeypatch.setattr("dashboard.backend.routes.workspaces.update_workspace_metadata",
                        lambda name, patch: stored.update(patch))
    monkeypatch.setattr("dashboard.backend.routes.workspaces._ensure_writable_workspace",
                        lambda name: None)
    from dashboard.backend.routes import workspaces
    assert workspaces._policy_payload("acme")["allow_auto_approve"] is True

    import asyncio
    from starlette.requests import Request
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    asyncio.run(workspaces.update_workspace_policy(request, "acme", {"allow_auto_approve": False}))
    assert stored["settings"]["allow_auto_approve"] is False
    assert workspaces._policy_payload("acme")["allow_auto_approve"] is False
    asyncio.run(workspaces.update_workspace_policy(request, "acme", {"allow_auto_approve": True}))
    assert "allow_auto_approve" not in stored["settings"]
