"""
A tool call that waits for a person inside a dashboard chat turn
(common/tool_approvals.py, the chat branch of ToolGuard._hold, the routes).

No model is called: the guard is driven directly, on a worker thread the way
an async run drives it, with a recording stream emitter standing in for the
chat's SSE stream.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
import uuid

import pytest

from agents import hooks
from common import stream_sink, tool_approvals
from tools import permission_policy as policy


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


def _open_run(run_id: str, *, origin: str = "chat", session_type: str = "chat", **kw):
    from managers import run_manager
    run_manager.open_run(run_id, kw.pop("agent_id", "swe_agent"), link_to_session=False,
                         status="running", session_type=session_type, message_origin=origin,
                         task_id=kw.pop("task_id", f"conv-{uuid.uuid4().hex[:6]}"),
                         workspace=kw.pop("workspace", "acme"), **kw)


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": {"require_tool_approval": True}})
    monkeypatch.setattr(tool_approvals, "POLL_SECONDS", 0.02)
    return "acme"


@pytest.fixture
def chat_turn(monkeypatch, gate_on):
    """A running dashboard chat turn with a stream: returns ``(run_id, events)``."""
    run_id = _rid()
    _open_run(run_id)
    # The run's loop state: where the guard reads the run id and the policy
    # trail lives, as in a real run.
    from agents.agent_loop import LoopState, reset_state, set_state
    state_token = set_state(LoopState(run_id=run_id, agent_id="swe_agent", workspace="acme"))
    events: list = []
    token = stream_sink.set_emitter(events.append)
    yield run_id, events
    stream_sink.reset_emitter(token)
    reset_state(state_token)


def _guard():
    return hooks.ToolGuard(agent_id="swe_agent", workspace="acme")


def _start(fn, *args):
    """Run ``fn`` on a thread with this context, like asyncio.to_thread does."""
    out: dict = {}
    ctx = contextvars.copy_context()
    thread = threading.Thread(target=lambda: out.update(result=ctx.run(fn, *args)), daemon=True)
    thread.start()
    return thread, out


def _wait_for(events, kind, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in events:
            if e.get("type") == kind:
                return e
        time.sleep(0.01)
    raise AssertionError(f"no {kind} event in {events}")


# ── the store ────────────────────────────────────────────────────────────────

def test_one_answer_wins_and_a_late_one_is_refused():
    row = tool_approvals.open_approval(run_id=_rid(), tool="run_shell", tool_input={"command": "ls"})
    assert row["status"] == "pending" and row["input"] == {"command": "ls"}
    first = tool_approvals.decide(row["approval_id"], "approve", note="fine", author="ann")
    assert first["status"] == "approved" and first["note"] == "fine"
    assert first["decided_by_name"] == "ann"
    assert tool_approvals.decide(row["approval_id"], "deny") is None
    with pytest.raises(ValueError):
        tool_approvals.decide(row["approval_id"], "maybe")


def test_an_answer_after_the_deadline_does_not_run_the_call():
    row = tool_approvals.open_approval(run_id=_rid(), tool="run_shell", timeout_s=-1)
    assert tool_approvals.decide(row["approval_id"], "approve") is None
    assert tool_approvals.get(row["approval_id"])["status"] == "expired"


def test_the_timeout_comes_from_the_workspace_then_the_env(monkeypatch):
    monkeypatch.setenv(tool_approvals.TIMEOUT_ENV, "120")
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    assert tool_approvals.timeout_seconds({}) == 120
    assert tool_approvals.timeout_seconds({"tool_approval_timeout": 45}) == 45
    # Clamped: a typo can neither refuse at once nor hold a turn for days.
    assert tool_approvals.timeout_seconds({"tool_approval_timeout": 0}) == tool_approvals.MIN_TIMEOUT_SECONDS
    monkeypatch.setenv(tool_approvals.TIMEOUT_ENV, "")
    assert tool_approvals.timeout_seconds({}) == 600


# ── where a call may wait ────────────────────────────────────────────────────

def test_only_a_dashboard_chat_turn_with_a_stream_holds_a_call():
    run_id = _rid()
    _open_run(run_id)
    assert tool_approvals.chat_context(run_id) is None  # no stream to show a card on
    token = stream_sink.set_emitter(lambda e: None)
    try:
        assert tool_approvals.chat_context(run_id)["run_id"] == run_id
        telegram = _rid()
        _open_run(telegram, origin="telegram")
        assert tool_approvals.chat_context(telegram) is None
        assert tool_approvals.chat_context("") is None
    finally:
        stream_sink.reset_emitter(token)


def test_a_run_started_inside_a_dashboard_turn_holds_too():
    from chat.runs import reset_turn_context, set_turn_context

    child = _rid()
    _open_run(child, session_type="delegation", origin="delegation")
    token = stream_sink.set_emitter(lambda e: None)
    ctx_token = set_turn_context({"instance_id": "i-1", "source": "chat"})
    try:
        assert tool_approvals.chat_context(child) is not None
    finally:
        reset_turn_context(ctx_token)
    ctx_token = set_turn_context({"instance_id": "i-1", "source": "widget"})
    try:
        assert tool_approvals.chat_context(child) is None
    finally:
        reset_turn_context(ctx_token)
        stream_sink.reset_emitter(token)


# ── the guard in a chat turn ─────────────────────────────────────────────────

def test_approve_runs_the_call_in_the_same_turn(chat_turn):
    from common import audit
    from managers.run_manager import get_run_by_id

    run_id, events = chat_turn
    guard = _guard()
    thread, out = _start(guard.before, "run_shell", {"command": "rm -rf build"})
    card = _wait_for(events, "tool_approval")
    assert card["tool"] == "run_shell" and card["input"] == {"command": "rm -rf build"}
    assert card["status"] == "pending" and card["expires_at"]
    # The run says what it waits on, and is still running (Stop works as usual).
    run = get_run_by_id(run_id)
    assert run["status"] == "running"
    assert run["awaiting"]["approval_id"] == card["approval_id"]

    tool_approvals.decide(card["approval_id"], "approve", author="ann")
    thread.join(5)
    assert out["result"] is None  # None: run the call
    resolved = _wait_for(events, "tool_approval_resolved")
    assert resolved["status"] == "approved"
    assert get_run_by_id(run_id).get("awaiting") is None
    # One trail entry for the one call, carrying the answer, not the ask.
    verdict = policy.call_verdict("run_shell", object(), {"command": "rm -rf build"})
    assert verdict == {"evaluated_permission": "allow", "reason_code": "human_approved"}
    codes = [i["details"].get("reason_code") for i in audit.query(action="tool.policy")["items"]]
    assert "human_approved" in codes


def test_deny_hands_the_note_back_as_the_tool_output(chat_turn):
    run_id, events = chat_turn
    thread, out = _start(_guard().before, "run_shell", {"command": "drop table users"})
    card = _wait_for(events, "tool_approval")
    tool_approvals.decide(card["approval_id"], "deny", note="use the staging copy", author="ann")
    thread.join(5)
    body = json.loads(out["result"])
    assert body["code"] == "approval_denied"
    assert "use the staging copy" in body["error"] and body["note"] == "use the staging copy"
    verdict = policy.call_verdict("run_shell", object(), {"command": "drop table users"})
    assert verdict["reason_code"] == "human_denied"


def test_nobody_answering_is_a_refusal_that_says_so(chat_turn, monkeypatch):
    monkeypatch.setattr(tool_approvals, "timeout_seconds", lambda settings=None: 0.2)
    run_id, events = chat_turn
    result = _guard().before("run_shell", {"command": "ls"})
    body = json.loads(result)
    assert body["code"] == "approval_timeout"
    assert _wait_for(events, "tool_approval_resolved")["status"] == "expired"


def test_stop_ends_the_wait(chat_turn):
    from managers.run_manager import update_run

    run_id, events = chat_turn
    thread, out = _start(_guard().before, "run_shell", {"command": "ls"})
    card = _wait_for(events, "tool_approval")
    update_run(run_id, {"status": "stop"})
    thread.join(5)
    assert json.loads(out["result"])["code"] == "approval_cancelled"
    assert tool_approvals.get(card["approval_id"])["status"] == "cancelled"


def test_a_chat_with_nobody_at_a_card_keeps_the_advisory_refusal(gate_on, monkeypatch):
    run_id = _rid()
    _open_run(run_id, origin="telegram")
    monkeypatch.setattr(hooks.ToolGuard, "_run_id", staticmethod(lambda: run_id))
    token = stream_sink.set_emitter(lambda e: None)
    try:
        result = _guard().before("run_shell", {"command": "ls"})
    finally:
        stream_sink.reset_emitter(token)
    assert json.loads(result)["code"] == "approval_required"
    assert tool_approvals.list_for_run(run_id) == []


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from common.auth import Principal
    from routes import tool_approvals as routes

    app = FastAPI()

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        who = request.headers.get("x-test-principal")
        if who:
            kind, _, rest = who.partition(":")
            uid, _, role = rest.partition(":")
            request.state.principal = Principal(id=uid, username=uid, role=role or "member", kind=kind)
        return await call_next(request)

    app.include_router(routes.router)
    return TestClient(app)


def _pending(run_id, owner="u-owner"):
    _open_run(run_id)
    return tool_approvals.open_approval(run_id=run_id, tool="run_shell", tool_input={"command": "ls"},
                                        workspace="acme", owner=owner)


def _answer(client, approval_id, principal=None, **body):
    headers = {"x-test-principal": principal} if principal else {}
    return client.post(f"/api/tool-approvals/{approval_id}",
                       json={"decision": "approve", **body}, headers=headers)


def test_the_operator_answers_and_it_is_audited(client):
    from common import audit

    row = _pending(_rid())
    resp = _answer(client, row["approval_id"], note="ok")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approval"]["status"] == "approved"
    # A second answer finds nothing waiting.
    assert _answer(client, row["approval_id"], decision="deny").status_code == 409
    results = [i["result"] for i in audit.query(action="tool.approval")["items"]]
    assert "approved" in results and "error" in results


def test_an_agents_own_credential_may_not_answer(client):
    row = _pending(_rid())
    assert _answer(client, row["approval_id"], principal="service:svc:admin").status_code == 403
    assert tool_approvals.get(row["approval_id"])["status"] == "pending"


def test_a_bad_decision_is_a_400(client):
    row = _pending(_rid())
    assert _answer(client, row["approval_id"], decision="later").status_code == 400


@pytest.fixture
def multi(monkeypatch):
    from common import identity
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    monkeypatch.setattr("common.access.can_see_workspace", lambda principal, ws: True)


def test_in_multi_mode_only_the_owner_or_an_admin_may_answer(client, multi):
    row = _pending(_rid())
    assert _answer(client, row["approval_id"], principal="user:u-other:member").status_code == 403
    assert _answer(client, row["approval_id"], principal="user:u-owner:member").status_code == 200
    other = _pending(_rid())
    assert _answer(client, other["approval_id"], principal="user:u-admin:admin").status_code == 200


def test_a_reopened_chat_finds_the_waiting_call(client):
    run_id = _rid()
    row = _pending(run_id)
    resp = client.get(f"/api/runs/{run_id}/tool-approvals", params={"status": "pending"})
    assert [a["approval_id"] for a in resp.json()["approvals"]] == [row["approval_id"]]
    assert client.get(f"/api/tool-approvals/{row['approval_id']}").json()["approval"]["tool"] == "run_shell"
    assert client.get("/api/tool-approvals/appr_missing").status_code == 404


def test_the_run_state_side_opens_reads_and_closes(client):
    run_id = _rid()
    opened = client.post(f"/api/run-state/runs/{run_id}/tool-approvals",
                         json={"tool": "run_shell", "tool_input": {"command": "ls"}, "timeout_s": 30})
    approval = opened.json()["approval"]
    assert approval["run_id"] == run_id and approval["status"] == "pending"
    read = client.get(f"/api/run-state/tool-approvals/{approval['approval_id']}").json()["approval"]
    assert read["input"] == {"command": "ls"}
    closed = client.post(f"/api/run-state/tool-approvals/{approval['approval_id']}/close",
                         json={"status": "expired"}).json()["approval"]
    assert closed["status"] == "expired"
    assert client.post(f"/api/run-state/tool-approvals/{approval['approval_id']}/close",
                       json={"status": "approved"}).status_code == 400


# ── the transports ───────────────────────────────────────────────────────────

def test_the_direct_transport_reaches_the_store():
    from common.state_transport import DirectStateTransport

    t = DirectStateTransport()
    row = t.open_tool_approval({"run_id": _rid(), "tool": "delete_file", "tool_input": {"path": "a"}})
    assert t.tool_approval(row["approval_id"])["tool"] == "delete_file"
    assert t.close_tool_approval(row["approval_id"], "cancelled")["status"] == "cancelled"


def test_the_http_transport_posts_to_the_run_state_routes(monkeypatch):
    import requests
    from common.state_transport import HttpStateTransport

    sent = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"approval": {"approval_id": "appr_1", "status": "pending"}}

    def _request(method, url, json=None, headers=None, timeout=None):
        sent.append((method, url.split("/api/run-state")[1], json))
        return _Resp()

    monkeypatch.setattr(requests, "request", _request)
    t = HttpStateTransport()
    assert t.open_tool_approval({"run_id": "r1", "tool": "run_shell"})["approval_id"] == "appr_1"
    assert t.tool_approval("appr_1")["status"] == "pending"
    t.close_tool_approval("appr_1", "expired")
    assert [(m, p) for m, p, _ in sent] == [
        ("POST", "/runs/r1/tool-approvals"),
        ("GET", "/tool-approvals/appr_1"),
        ("POST", "/tool-approvals/appr_1/close"),
    ]


# ── the workspace setting ────────────────────────────────────────────────────

def test_the_wait_is_set_per_workspace_from_settings():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import workspaces as workspace_routes
    from tools.permission_policy import workspace_settings
    from workspace import create_workspace_folder

    name = f"appr-ws-{uuid.uuid4().hex[:8]}"
    create_workspace_folder(name)
    app = FastAPI()
    app.include_router(workspace_routes.router)
    client = TestClient(app)
    resp = client.put(f"/api/workspaces/{name}/policy", json={"tool_approval_timeout": 90})
    assert resp.status_code == 200 and resp.json()["tool_approval_timeout"] == 90
    assert tool_approvals.timeout_seconds(workspace_settings(name)) == 90
    assert client.put(f"/api/workspaces/{name}/policy",
                      json={"tool_approval_timeout": 3}).status_code == 400
    cleared = client.put(f"/api/workspaces/{name}/policy", json={"tool_approval_timeout": None})
    assert "tool_approval_timeout" not in cleared.json()


def test_an_async_tool_call_waits_off_the_event_loop_and_then_runs(chat_turn):
    """The path a chat turn takes: GuardedTool._arun decides in a worker thread,
    so the event loop stays free (here it answers the card itself)."""
    from langchain_core.tools import tool

    @tool("run_shell")
    def fake_run_shell(command: str) -> str:
        """Stand-in for the real shell tool."""
        return f"ran {command}"

    run_id, events = chat_turn
    wrapped = hooks.GuardedTool(fake_run_shell, _guard())

    async def _turn():
        call = asyncio.ensure_future(wrapped.arun({"command": "make clean"}))
        while not any(e.get("type") == "tool_approval" for e in events):
            await asyncio.sleep(0.01)  # the loop keeps running while the call waits
        card = next(e for e in events if e.get("type") == "tool_approval")
        tool_approvals.decide(card["approval_id"], "approve")
        return await asyncio.wait_for(call, 5)

    assert asyncio.run(_turn()) == "ran make clean"
