"""The needs_approval gate: what it holds, where it holds it, and how it resumes."""
import dataclasses
import json
import sys
from pathlib import Path

import pytest
from langchain_core.tools import tool

from agents import hooks
from agents.callbacks.guards import ApprovalSignal
from tasks import service as ts
from tasks.models import TaskStatus
from tools import approval


@tool("run_shell")
def fake_run_shell(command: str) -> str:
    """Stand-in for the real shell tool, so the gate is tested without one."""
    return f"ran {command}"


@tool("read_file")
def fake_read_file(path: str) -> str:
    """Stand-in for a tool nobody needs to approve."""
    return f"contents of {path}"


@pytest.fixture
def gate_on(monkeypatch):
    """A workspace that has turned the approval gate on, and no hooks."""
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr(
        "workspace.get_workspace_metadata",
        lambda name: {"settings": {"require_tool_approval": True}},
    )
    return "acme"


@pytest.fixture
def in_task(monkeypatch):
    """Run the gate as if inside a tracked task run, and hand back the task."""
    task = ts.create_task("gated work", workspace="acme")
    from common.agent_context import current_task_id
    token = current_task_id.set(str(task.id))
    yield task
    current_task_id.reset(token)


def _guard(workspace="acme", spec=None):
    return hooks.ToolGuard(agent_id="swe_agent", spec=spec, workspace=workspace)


# -------------------- the list --------------------

def test_the_default_list_holds_the_destructive_tools():
    assert approval.needs_approval("run_shell")
    assert approval.needs_approval("delete_file")
    assert approval.needs_approval("stop_node")
    assert not approval.needs_approval("read_file")


def test_sandboxed_code_and_browser_actions_are_gated():
    # run_code still executes what the model wrote; browser_act can submit a
    # form on a live site. Opening and reading a page are not gated.
    assert approval.needs_approval("run_code")
    assert approval.needs_approval("browser_act")
    assert not approval.needs_approval("browser_open")
    assert not approval.needs_approval("browser_read")


def test_reasoning_tools_and_ask_user_are_never_gated():
    # Gating the question tool would need approval to ask for approval.
    assert not approval.needs_approval("ask_user")
    assert not approval.needs_approval("think")


def test_an_agent_can_widen_and_narrow_the_list():
    from agents.registry import AgentSpec
    spec = AgentSpec(
        id="a", name="A", type="local", entrypoint="m:f",
        approval_tools=["read_file"], approval_exempt=["run_shell"],
    )
    assert approval.needs_approval("read_file", spec)
    # The exemption wins: one agent trusted with a gated tool does not turn the
    # gate off for everybody else.
    assert not approval.needs_approval("run_shell", spec)


def test_a_spec_without_the_fields_behaves_as_the_default():
    class Legacy:
        pass
    assert approval.needs_approval("run_shell", Legacy())


def test_the_gate_is_off_until_the_workspace_turns_it_on(monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": {}})
    assert approval.approval_gate_enabled("acme") is False
    monkeypatch.setattr(
        "workspace.get_workspace_metadata",
        lambda name: {"settings": {"require_tool_approval": True}},
    )
    assert approval.approval_gate_enabled("acme") is True


# -------------------- the gate in a task --------------------

def test_a_gated_call_in_a_task_parks_the_run(gate_on, in_task):
    guard = _guard()
    with pytest.raises(ApprovalSignal) as excinfo:
        guard.before("run_shell", {"command": "rm -rf build"})
    signal = excinfo.value
    assert signal.tool == "run_shell"
    assert signal.tool_input == {"command": "rm -rf build"}
    assert guard.pending["fingerprint"]
    assert guard.pending["agent_id"] == "swe_agent"


def test_an_ungated_tool_runs_untouched(gate_on, in_task):
    assert _guard().before("read_file", {"path": "a.txt"}) is None


def test_the_gate_does_nothing_while_the_workspace_leaves_it_off(monkeypatch, in_task):
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": {}})
    assert _guard().before("run_shell", {"command": "ls"}) is None


def test_a_hook_that_asks_gates_a_tool_that_is_not_on_the_list(monkeypatch, in_task):
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": {}})
    monkeypatch.setattr(
        hooks, "run_pre_tool_use",
        lambda *a, **k: hooks.HookOutcome("ask", "this path is outside the project", "reviewer"),
    )
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    with pytest.raises(ApprovalSignal) as excinfo:
        _guard().before("read_file", {"path": "/etc/passwd"})
    assert excinfo.value.reason == "this path is outside the project"
    assert excinfo.value.payload["hook"] == "reviewer"


def test_a_hook_denial_is_handed_back_as_the_tool_output(monkeypatch, in_task):
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr(
        hooks, "run_pre_tool_use",
        lambda *a, **k: hooks.HookOutcome("deny", "no shell in this workspace", "policy"),
    )
    # A denial is not an approval question: the agent reads why and moves on.
    assert _guard().before("run_shell", {"command": "ls"}) == "no shell in this workspace"


# -------------------- the gate in chat --------------------

def test_in_chat_the_gate_refuses_instead_of_parking(gate_on):
    # No task context is set: this is a chat run, and nothing can park.
    refusal = _guard().before("run_shell", {"command": "rm -rf build"})
    body = json.loads(refusal)
    assert body["code"] == "approval_required"
    assert body["action"] == "run_shell"
    assert "rm -rf build" in body["effect"]


def test_the_chat_refusal_matches_the_service_ops_shape():
    from tools.service_ops import _approval_required
    body = json.loads(_approval_required("stop_run", "r-1", "terminate run r-1"))
    assert body == {
        "ok": False,
        "error": ("stop_run needs the user's approval. It would terminate run r-1. "
                  "Tell the user exactly this, and only call again with "
                  "user_approved=True once they have agreed."),
        "code": "approval_required",
        "action": "stop_run",
        "target": "r-1",
        "effect": "terminate run r-1",
    }


# -------------------- approved calls --------------------

def test_an_approved_call_passes_once_and_is_consumed(gate_on, in_task):
    guard = _guard()
    call = {"command": "rm -rf build"}
    fingerprint = approval.call_fingerprint("run_shell", call)
    ts.approve_tool_call(in_task.id, "run_shell", fingerprint, note="go ahead")

    assert guard.before("run_shell", call) is None          # spent
    with pytest.raises(ApprovalSignal):
        guard.before("run_shell", call)                     # and only once
    assert ts.get_task(in_task.id).approved_calls == []


def test_an_approval_does_not_cover_a_different_call(gate_on, in_task):
    ts.approve_tool_call(
        in_task.id, "run_shell",
        approval.call_fingerprint("run_shell", {"command": "ls"}),
    )
    with pytest.raises(ApprovalSignal):
        _guard().before("run_shell", {"command": "rm -rf build"})


def test_the_fingerprint_ignores_key_order_but_not_values():
    assert (approval.call_fingerprint("t", {"a": 1, "b": 2})
            == approval.call_fingerprint("t", {"b": 2, "a": 1}))
    assert (approval.call_fingerprint("t", {"a": 1})
            != approval.call_fingerprint("t", {"a": 2}))


# -------------------- the wrapped tool --------------------

def test_wrapping_leaves_the_reasoning_and_question_tools_alone(gate_on):
    from tools.human_input import ask_user
    wrapped = hooks.guard_action_tools(
        [fake_run_shell, fake_read_file, ask_user], agent_id="swe_agent", workspace="acme")
    kinds = {t.name: type(t).__name__ for t in wrapped}
    assert kinds["run_shell"] == "GuardedTool"
    assert kinds["read_file"] == "GuardedTool"
    assert kinds["ask_user"] != "GuardedTool"


def test_nothing_is_wrapped_when_neither_hooks_nor_the_gate_are_configured(monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": {}})
    tools = [fake_run_shell]
    assert hooks.guard_action_tools(tools, workspace="acme") is tools


def test_a_wrapped_tool_still_runs_and_post_hooks_see_its_output(gate_on, monkeypatch):
    seen = {}

    def _post(tool_id, tool_input, output, **kwargs):
        seen["output"] = output
        return "rewritten"

    monkeypatch.setattr(hooks, "run_post_tool_use", _post)
    wrapped = hooks.guard_action_tools([fake_read_file], workspace="acme")[0]
    assert wrapped.run({"path": "a.txt"}) == "rewritten"
    assert seen["output"] == "contents of a.txt"


def test_the_parked_call_is_readable_from_the_agent_that_stopped(gate_on, in_task):
    class _Agent:
        pass

    agent = _Agent()
    agent._tools = hooks.guard_action_tools(
        [fake_run_shell], agent_id="swe_agent", workspace="acme")
    with pytest.raises(ApprovalSignal):
        agent._tools[0].run({"command": "rm -rf build"})
    pending = hooks.pending_approval_for(agent)
    assert pending["tool"] == "run_shell"
    # Read off the agent's own guard, never a module global, so two runs in one
    # process cannot see each other's parked call.
    assert hooks.pending_approval_for(_Agent()) is None


# -------------------- gate order: think-gate wraps the approval guard --------------------
#
# agent_factory builds each action tool as GatedTool(GuardedTool(tool)) — the
# think-gate outermost — so a fresh run that has not called `think` yet
# refuses before the approval guard is ever reached. The opposite order used
# to let the guard consume an approved fingerprint and hand the call to a
# gate that could still refuse it, spending the operator's yes on a refusal.

def test_a_gate_refusal_never_reaches_the_approval_guard(gate_on, in_task):
    from reasoning.think_gate import GatedTool, ThinkGate

    class _Agent:
        pass

    agent = _Agent()
    guarded = hooks.guard_action_tools(
        [fake_run_shell], agent_id="swe_agent", workspace="acme")[0]
    gate = ThinkGate("deep")  # enforces a think before every action
    agent._tools = [GatedTool(guarded, gate)]

    # No think yet: the gate refuses on its own, without the inner GuardedTool
    # (and therefore the approval machinery) ever running.
    refusal = agent._tools[0].run({"command": "rm -rf build"})
    assert "BLOCKED" in refusal
    assert hooks.pending_approval_for(agent) is None


def test_pending_approval_for_finds_the_guard_through_the_gate(gate_on, in_task):
    from reasoning.think_gate import GatedTool, ThinkGate

    class _Agent:
        pass

    agent = _Agent()
    guarded = hooks.guard_action_tools(
        [fake_run_shell], agent_id="swe_agent", workspace="acme")[0]
    gate = ThinkGate("deep")
    gate.note_think()  # clears the gate, so this call reaches the guard
    agent._tools = [GatedTool(guarded, gate)]

    with pytest.raises(ApprovalSignal):
        agent._tools[0].run({"command": "rm -rf build"})
    # _guards_of unwraps the GatedTool via .inner to find the ToolGuard that
    # actually holds the parked call.
    pending = hooks.pending_approval_for(agent)
    assert pending is not None and pending["tool"] == "run_shell"


# -------------------- parking and resuming --------------------

def test_parking_a_task_stores_the_call_and_keeps_it_unfinished():
    task = ts.create_task("gated work", workspace="acme")
    ts.park_task_awaiting_approval(
        task.id,
        {"tool": "run_shell", "input": {"command": "ls"}, "reason": "listed",
         "fingerprint": "abc", "agent_id": "swe_agent"},
        run_id="r-1",
    )
    parked = ts.get_task(task.id)
    assert parked.status == TaskStatus.awaiting_approval
    assert parked.pending_approval["tool"] == "run_shell"
    assert parked.pending_approval["run_id"] == "r-1"
    assert parked.pending_approval["asked_at"]


@pytest.fixture
def client(monkeypatch):
    """The task routes, with agent launches captured instead of spawned."""
    launched = {}

    def _capture(task_id, agent_id, params=None, run_id=None):
        launched["task_id"] = str(task_id)
        launched["agent_id"] = agent_id
        launched["params"] = params or {}
        return ("new-run", "session-1")

    import agents.agent_launcher as launcher
    monkeypatch.setattr(launcher, "start_run", _capture)

    # The suite runs against an empty state root with no agents.json; the route
    # only asks the registry whether the agent it is about to resume exists.
    from agents.registry import AgentSpec
    monkeypatch.setattr(
        "agents.registry.get_agent",
        lambda agent_id: AgentSpec(id=agent_id, name="SWE", type="local",
                                   entrypoint="agents.standard_agent:StandardAgent"),
    )

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tasks as task_routes

    monkeypatch.setattr(task_routes.agent_launcher, "start_run", _capture)
    app = FastAPI()
    app.include_router(task_routes.router)
    return TestClient(app), launched


def _parked_task(agent_id="swe_agent", call=None):
    task = ts.create_task("gated work", workspace="acme")
    ts.update_task(task.id, assigned_agent_type=agent_id)
    ts.park_task_awaiting_approval(
        task.id,
        {"tool": "run_shell", "input": call or {"command": "rm -rf build"},
         "reason": "on the approval list", "agent_id": agent_id,
         "fingerprint": approval.call_fingerprint("run_shell", call or {"command": "rm -rf build"})},
        run_id="r-1",
    )
    return task


def test_approving_records_the_call_and_resumes_the_agent(client):
    api, launched = client
    task = _parked_task()

    resp = api.post(f"/api/tasks/{task.id}/approve", json={"approved": True, "note": "go"})

    assert resp.status_code == 200
    assert resp.json()["approved"] is True
    resumed = ts.get_task(task.id)
    assert resumed.status == TaskStatus.in_progress
    assert resumed.pending_approval is None
    # The approval is for that exact call, so the resumed run can make it once.
    assert [c["tool"] for c in resumed.approved_calls] == ["run_shell"]
    assert resumed.approved_calls[0]["fingerprint"] == approval.call_fingerprint(
        "run_shell", {"command": "rm -rf build"})
    assert launched["agent_id"] == "swe_agent"
    assert "approved" in launched["params"]["description"]


def test_denying_resumes_without_approving_anything(client):
    api, launched = client
    task = _parked_task()

    resp = api.post(f"/api/tasks/{task.id}/approve",
                    json={"approved": False, "note": "too risky"})

    assert resp.status_code == 200
    resumed = ts.get_task(task.id)
    assert resumed.status == TaskStatus.in_progress
    assert resumed.approved_calls == []
    assert "refused" in launched["params"]["description"]
    assert "too risky" in launched["params"]["description"]


def test_approving_a_task_that_is_not_parked_is_refused(client):
    api, _ = client
    task = ts.create_task("ordinary work", workspace="acme")
    resp = api.post(f"/api/tasks/{task.id}/approve", json={"approved": True})
    assert resp.status_code == 400


def test_the_resumed_run_can_make_exactly_the_approved_call(client, gate_on, monkeypatch):
    """End to end: park, approve through the API, then let the gate see it again."""
    api, _ = client
    task = _parked_task()
    api.post(f"/api/tasks/{task.id}/approve", json={"approved": True})

    from common.agent_context import current_task_id
    token = current_task_id.set(str(task.id))
    try:
        guard = _guard()
        assert guard.before("run_shell", {"command": "rm -rf build"}) is None
        with pytest.raises(ApprovalSignal):
            guard.before("run_shell", {"command": "rm -rf src"})
    finally:
        current_task_id.reset(token)


def test_the_spec_round_trips_the_approval_overrides():
    from agents.registry import AgentSpec, _validate_agent_dict
    spec = AgentSpec(id="a", name="A", type="local", entrypoint="m:f",
                     approval_tools=["read_file"], approval_exempt=["run_shell"])
    restored = _validate_agent_dict(spec.to_dict())
    assert restored.approval_tools == ["read_file"]
    assert restored.approval_exempt == ["run_shell"]
    # A record that does not use them keeps a clean JSON shape.
    assert "approval_tools" not in dataclasses.replace(
        spec, approval_tools=[], approval_exempt=[]).to_dict()


def test_a_new_run_does_not_inherit_the_previous_run_s_parked_call(gate_on, in_task):
    """Built agents are cached and reused, so the parked call must be cleared."""
    class _Agent:
        pass

    agent = _Agent()
    agent._tools = hooks.guard_action_tools(
        [fake_run_shell], agent_id="swe_agent", workspace="acme")
    with pytest.raises(ApprovalSignal):
        agent._tools[0].run({"command": "rm -rf build"})
    assert hooks.pending_approval_for(agent) is not None

    hooks.clear_pending_approval(agent)
    assert hooks.pending_approval_for(agent) is None
