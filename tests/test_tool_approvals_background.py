"""
A tool call that waits for a person in a background flow node or a team
member's turn (common/tool_approvals.py ``background_scope``, the background
branch of ToolGuard._hold, flow/task_driver.py, teams/runner.py, the routes).

No model is called: the guard is driven directly on a worker thread inside a
background scope, with a recording emitter standing in for the flow run's
live channel and the notification service replaced by a recorder.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from agents import hooks
from common import tool_approvals
from tools import permission_policy as policy


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": {"require_tool_approval": True}})
    monkeypatch.setattr(tool_approvals, "BACKGROUND_POLL_SECONDS", 0.02)
    return "acme"


@pytest.fixture
def bell(monkeypatch):
    """The notifications made and the ones marked read."""
    from plans import service as plan_service

    made: list = []
    read: list = []

    def create_notification(**kw):
        made.append(kw)
        return SimpleNamespace(id=f"n-{len(made)}")

    monkeypatch.setattr(plan_service, "create_notification", create_notification)
    monkeypatch.setattr(plan_service, "mark_notification_read", lambda nid, read_=True: read.append(nid))
    return made, read


@pytest.fixture
def node(gate_on, bell):
    """A background flow node's run: ``(run_id, flow_run_id, events, stop)``.
    ``stop["now"] = True`` is the flow (or loop) being stopped."""
    from agents.agent_loop import LoopState, reset_state, set_state
    from managers import run_manager

    run_id, flow_run_id = _rid(), f"fr-{uuid.uuid4().hex[:8]}"
    run_manager.open_run(run_id, "swe_agent", link_to_session=False, status="running",
                         session_type="task", channel="flow", workspace="acme",
                         flow_id="flow-1", flow_run_id=flow_run_id, flow_node_id="n1")
    state_token = set_state(LoopState(run_id=run_id, agent_id="swe_agent", workspace="acme"))
    events: list = []
    stop = {"now": False}
    with tool_approvals.background_scope(
            kind="flow", group_run_id=flow_run_id, entity_id="flow-1", entity_name="Release notes",
            seat_id="n1", seat_label="Writer", emitter=events.append, should_stop=lambda: stop["now"],
            link={"loop_id": "loop-1", "loop_run_id": "lr-1"}):
        yield run_id, flow_run_id, events, stop
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


def _wait_for(events, kind, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in events:
            if e.get("type") == kind:
                return e
        time.sleep(0.01)
    raise AssertionError(f"no {kind} event in {events}")


# ── the scope ────────────────────────────────────────────────────────────────

def test_only_a_run_inside_a_background_scope_waits_there():
    assert tool_approvals.background_context(_rid()) is None
    with tool_approvals.background_scope(kind="flow", group_run_id="fr-1"):
        ctx = tool_approvals.background_context("run-x")
        assert ctx["flow_run_id"] == "fr-1" and ctx["background"] is True
        assert "team_run_id" not in ctx
        assert tool_approvals.background_context("") is None
    with tool_approvals.background_scope(kind="team", group_run_id="tr-1"):
        assert tool_approvals.background_context("run-x")["team_run_id"] == "tr-1"
    assert tool_approvals.background_context("run-x") is None
    with pytest.raises(ValueError):
        with tool_approvals.background_scope(kind="crew", group_run_id="x"):
            pass


def test_the_background_wait_is_a_day_unless_the_workspace_says_otherwise(monkeypatch):
    monkeypatch.delenv(tool_approvals.BACKGROUND_TIMEOUT_ENV, raising=False)
    assert tool_approvals.background_timeout_seconds({}) == 24 * 3600
    assert tool_approvals.background_timeout_seconds({"tool_approval_background_timeout": 120}) == 120
    # The chat's own wait is a separate setting.
    assert tool_approvals.background_timeout_seconds({"tool_approval_timeout": 30}) == 24 * 3600


# ── the guard in a background node ───────────────────────────────────────────

def test_approve_runs_the_call_in_the_node(node, bell):
    from managers.run_manager import get_run_by_id

    run_id, flow_run_id, events, _ = node
    made, read = bell
    thread, out = _start(_guard().before, "run_shell", {"command": "make release"})
    card = _wait_for(events, "tool_approval")
    assert card["tool"] == "run_shell" and card["status"] == "pending"
    # The row names its flow run: the run's pages read the cards from there.
    pending = tool_approvals.list_for_flow_run(flow_run_id, status="pending")
    assert [a["approval_id"] for a in pending] == [card["approval_id"]]
    assert get_run_by_id(run_id)["awaiting"]["approval_id"] == card["approval_id"]
    # The bell says so, and links to the run and the loop.
    assert len(made) == 1
    source = made[0]["source"]
    assert source["kind"] == "tool_approval" and source["approval_id"] == card["approval_id"]
    assert source["flow_run_id"] == flow_run_id and source["loop_run_id"] == "lr-1"
    assert source["message"]["key"] == "toolApprovalNeeded"
    assert source["message"]["params"]["flow"] == "Release notes"
    assert made[0]["severity"] == "warning" and made[0]["workspace"] == "acme"

    tool_approvals.decide(card["approval_id"], "approve", author="ann")
    thread.join(15)
    assert out["result"] is None  # None: run the call
    assert _wait_for(events, "tool_approval_resolved")["status"] == "approved"
    assert read == ["n-1"]
    assert get_run_by_id(run_id).get("awaiting") is None
    verdict = policy.call_verdict("run_shell", object(), {"command": "make release"})
    assert verdict == {"evaluated_permission": "allow", "reason_code": "human_approved"}


def test_a_node_on_a_task_waits_instead_of_parking(node):
    """A flow on a task used to raise ApprovalSignal, which the flow driver
    took for a finished node. It waits in the node now."""
    from common.agent_context import current_task_id

    _, _, events, _ = node
    token = current_task_id.set("task-123")
    try:
        thread, out = _start(_guard().before, "run_shell", {"command": "rm -rf dist"})
    finally:
        current_task_id.reset(token)
    card = _wait_for(events, "tool_approval")
    tool_approvals.decide(card["approval_id"], "deny", note="keep dist", author="ann")
    thread.join(15)
    body = json.loads(out["result"])
    assert body["code"] == "approval_denied" and body["note"] == "keep dist"
    # Worded for a run nobody chats in.
    assert "finish without it" in body["error"]


def test_stopping_the_flow_ends_the_wait(node):
    _, _, events, stop = node
    thread, out = _start(_guard().before, "run_shell", {"command": "ls"})
    card = _wait_for(events, "tool_approval")
    stop["now"] = True
    thread.join(15)
    assert json.loads(out["result"])["code"] == "approval_cancelled"
    assert tool_approvals.get(card["approval_id"])["status"] == "cancelled"


def test_nobody_answering_finishes_the_node_without_the_call(node, monkeypatch, bell):
    monkeypatch.setattr(tool_approvals, "background_timeout_seconds", lambda settings=None: 0.2)
    _, _, events, _ = node
    body = json.loads(_guard().before("run_shell", {"command": "ls"}))
    assert body["code"] == "approval_timeout"
    assert "Finish without it" in body["error"] and "conversation" not in body["error"]
    assert _wait_for(events, "tool_approval_resolved")["status"] == "expired"
    assert bell[1] == ["n-1"]


# ── the flow driver ──────────────────────────────────────────────────────────

def test_a_flow_agent_node_runs_inside_a_background_scope(monkeypatch, tmp_path):
    from flow import task_driver

    seen: dict = {}

    def fake_lifecycle(agent_id, workspace, prompt, **kw):
        seen["ctx"] = tool_approvals.background_context("run-in-node")
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(task_driver, "run_agent_lifecycle", fake_lifecycle)
    monkeypatch.setattr(task_driver, "open_run", lambda *a, **kw: None)
    monkeypatch.setattr(task_driver, "update_run", lambda *a, **kw: None)
    monkeypatch.setattr(task_driver, "run_log_path", lambda rid: tmp_path / f"{rid}.log")
    args = SimpleNamespace(flow_id="flow-9", run_id="fr-9", task_id=None, session_id=None,
                           workspace=str(tmp_path))
    stopped = {"now": False}
    task_driver._run_agent_node(
        {"node_id": "n1", "agent_id": "swe_agent", "label": "Builder"}, "n1", "do it",
        args=args, flow_state=None, agent_overrides={}, port=0,
        should_stop=lambda: stopped["now"], flow_name="Ship",
        approval_link={"loop_id": "loop-9"},
    )
    ctx = seen["ctx"]
    assert ctx is not None and ctx["flow_run_id"] == "fr-9"
    assert ctx["should_stop"]() is False
    stopped["now"] = True
    assert ctx["should_stop"]() is True
    # Nothing leaks past the node.
    assert tool_approvals.background_context("run-in-node") is None


# ── the routes and the setting ───────────────────────────────────────────────

def test_the_flow_run_lists_its_waiting_calls():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tool_approvals as routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    flow_run_id = f"fr-{uuid.uuid4().hex[:8]}"
    row = tool_approvals.open_approval(run_id=_rid(), tool="run_shell", flow_run_id=flow_run_id)
    tool_approvals.open_approval(run_id=_rid(), tool="run_shell", flow_run_id="fr-other")
    resp = client.get(f"/api/flow-runs/{flow_run_id}/tool-approvals", params={"status": "pending"})
    assert [a["approval_id"] for a in resp.json()["approvals"]] == [row["approval_id"]]
    assert resp.json()["approvals"][0]["flow_run_id"] == flow_run_id
    # The run-state side records the flow run too (a run container).
    opened = client.post(f"/api/run-state/runs/{_rid()}/tool-approvals",
                         json={"tool": "run_shell", "flow_run_id": flow_run_id}).json()["approval"]
    assert opened["flow_run_id"] == flow_run_id


def test_the_background_wait_is_set_per_workspace():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import workspaces as workspace_routes
    from tools.permission_policy import workspace_settings
    from workspace import create_workspace_folder

    name = f"appr-bg-{uuid.uuid4().hex[:8]}"
    create_workspace_folder(name)
    app = FastAPI()
    app.include_router(workspace_routes.router)
    client = TestClient(app)
    resp = client.put(f"/api/workspaces/{name}/policy", json={"tool_approval_background_timeout": 7200})
    assert resp.status_code == 200 and resp.json()["tool_approval_background_timeout"] == 7200
    assert tool_approvals.background_timeout_seconds(workspace_settings(name)) == 7200
    assert client.put(f"/api/workspaces/{name}/policy",
                      json={"tool_approval_background_timeout": 5}).status_code == 400
    cleared = client.put(f"/api/workspaces/{name}/policy", json={"tool_approval_background_timeout": ""})
    assert "tool_approval_background_timeout" not in cleared.json()


# ── a team member's turn ─────────────────────────────────────────────────────

def _team():
    from teams.models import Team, TeamMember

    return Team(name="Launch crew", charter="Ship it.", members=[
        TeamMember(agent_id="swe", name="Sam", role="engineer", manifest="Writes code."),
    ])


def _member_turn(monkeypatch, team_run_id, tool_input, answer):
    """Run Sam's turn, whose agent makes one gated call; ``answer`` gets the
    waiting row and settles it from another thread. Returns ``(turn, out)``,
    ``out["result"]`` being what the guard said to the call."""
    import teams.runner as runner
    from agents.agent_loop import LoopState, reset_state, set_state

    out: dict = {}

    class _Agent:
        agent_id, provider, model = "swe", "openai", "gpt-x"

        def run(self, prompt, **kwargs):
            token = set_state(LoopState(run_id=kwargs["run_id"], agent_id="swe", workspace="acme"))
            try:
                out["result"] = _guard().before("run_shell", tool_input)
            finally:
                reset_state(token)
            return type("R", (), {"ok": True, "agent_output": "done", "error": None, "response": None})()

    class _Factory:
        def load_definition(self, agent_id):
            return {"system_prompt": "You are an engineer."}

    monkeypatch.setattr("agents.agent_factory.create_agent", lambda agent_id, workspace=None, **kw: _Agent())
    monkeypatch.setattr("agents.agent_factory.get_factory", lambda: _Factory())

    def _answer():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = tool_approvals.list_for_team_run(team_run_id, status="pending")
            if rows:
                answer(rows[0])
                return
            time.sleep(0.01)

    helper = threading.Thread(target=_answer, daemon=True)
    helper.start()
    team = _team()
    turn = runner._run_member(team=team, member=team.members[0], agent_id="swe", speaker="Sam",
                              prompt="ship", workspace=None, task_id=None, session_id=None,
                              team_run_id=team_run_id)
    helper.join(15)
    return turn, out


def test_a_team_member_waits_for_a_person_then_runs_the_call(gate_on, bell, monkeypatch):
    from teams import control

    team_run_id = f"trun_{uuid.uuid4().hex[:8]}"
    seen: dict = {}

    def approve(row):
        seen["row"] = row
        seen["waiting"] = control.is_waiting(team_run_id)
        tool_approvals.decide(row["approval_id"], "approve", author="ann")

    turn, out = _member_turn(monkeypatch, team_run_id, {"command": "deploy"}, approve)
    assert out["result"] is None and turn.text == "done"
    row = seen["row"]
    assert row["team_run_id"] == team_run_id and row["flow_run_id"] is None
    # The team's turn clock was paused while the call waited, and runs again.
    assert seen["waiting"] is True and control.is_waiting(team_run_id) is False
    made, read = bell
    source = made[0]["source"]
    assert source["team_run_id"] == team_run_id and source["team_id"]
    assert source["message"]["key"] == "toolApprovalNeededTeam"
    assert source["message"]["params"]["team"] == "Launch crew"
    assert source["message"]["params"]["agent"] == "Sam"
    assert read == ["n-1"]


def test_a_denied_team_call_is_refused_and_the_turn_goes_on(gate_on, bell, monkeypatch):
    team_run_id = f"trun_{uuid.uuid4().hex[:8]}"
    turn, out = _member_turn(
        monkeypatch, team_run_id, {"command": "rm -rf /data"},
        lambda row: tool_approvals.decide(row["approval_id"], "deny", note="not prod", author="ann"))
    body = json.loads(out["result"])
    assert body["code"] == "approval_denied" and body["note"] == "not prod"
    assert turn.text == "done" and not turn.error


def test_a_wait_does_not_count_against_the_team_clock():
    from teams import control
    from teams.runner import TeamStopped, _check_between_rounds

    team_run_id = f"trun_{uuid.uuid4().hex[:8]}"
    team = SimpleNamespace(cost_ceiling=None)
    started = time.monotonic() - 100
    with pytest.raises(TeamStopped):
        _check_between_rounds(team_run_id, started, 60, 0.0, team, None)
    control.note_wait(team_run_id, True)
    control.note_wait(team_run_id, True)     # two members wait at once: counted once
    time.sleep(0.05)
    control.note_wait(team_run_id, False)
    assert control.is_waiting(team_run_id)
    control.note_wait(team_run_id, False)
    assert not control.is_waiting(team_run_id)
    waited = control.waited_seconds(team_run_id)
    assert 0.04 < waited < 1.0
    control._waits[team_run_id][2] = 50.0    # as if members had waited 50 s
    _check_between_rounds(team_run_id, started, 60, 0.0, team, None)
    control.release(team_run_id)


def test_the_team_run_lists_its_waiting_calls():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tool_approvals as routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    team_run_id = f"trun_{uuid.uuid4().hex[:8]}"
    row = tool_approvals.open_approval(run_id=_rid(), tool="run_shell", team_run_id=team_run_id)
    resp = client.get(f"/api/team-runs/{team_run_id}/tool-approvals", params={"status": "pending"})
    assert [a["approval_id"] for a in resp.json()["approvals"]] == [row["approval_id"]]
    opened = client.post(f"/api/run-state/runs/{_rid()}/tool-approvals",
                         json={"tool": "run_shell", "team_run_id": team_run_id}).json()["approval"]
    assert opened["team_run_id"] == team_run_id


# ── a loop's judge ───────────────────────────────────────────────────────────

def _loop(**kw):
    from loops.models import Loop, LoopRun

    loop = Loop(name="Polish", flow_id="flow-7", evaluator_mode=kw.pop("mode", "agent"),
                evaluator_agent_id="swe_agent", **kw)
    return loop, LoopRun(loop_id=loop.loop_id)


def test_an_agent_judge_waits_for_a_person_then_scores(gate_on, bell, monkeypatch):
    """The judge runs the tests it meant to run once a person said yes, and
    scores; before, the call was refused and the judge scored blind."""
    from agents.agent_loop import LoopState, reset_state, set_state
    from loops import runner

    loop, run = _loop()
    flow_run_id = f"fr-{uuid.uuid4().hex[:8]}"
    out: dict = {}

    class _Judge:
        agent_id, provider, model = "swe_agent", "openai", "gpt-x"

        def run(self, prompt, **kwargs):
            out["run_id"] = kwargs.get("run_id")
            token = set_state(LoopState(run_id=kwargs["run_id"], agent_id="swe_agent", workspace="acme"))
            try:
                out["result"] = _guard().before("run_shell", {"command": "pytest -q"})
            finally:
                reset_state(token)
            reply = '{"score": 0.9, "verdict": "stop", "reason": "tests pass", "feedback": ""}'
            return type("R", (), {"ok": True, "agent_output": reply, "error": None, "response": None})()

    monkeypatch.setattr("agents.agent_factory.create_agent", lambda agent_id, workspace=None, **kw: _Judge())

    def _approve():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = tool_approvals.list_for_flow_run(flow_run_id, status="pending")
            if rows:
                out["row"] = rows[0]
                tool_approvals.decide(rows[0]["approval_id"], "approve", author="ann")
                return
            time.sleep(0.01)

    helper = threading.Thread(target=_approve, daemon=True)
    helper.start()
    verdict, _cost = runner._judge(
        loop, {"name": "Polish flow", "nodes": []}, run, flow_run_id,
        goal="ship", output="draft", iteration=1, history=[], state={}, workspace=None,
        record={"workspace": "acme", "title": "Polish — judge, iteration 1"},
    )
    helper.join(15)
    assert out["result"] is None                       # the call ran
    assert verdict.verdict == "stop" and verdict.score == 0.9
    # The judge's run is a record of its own, under the pass's flow run.
    from managers.run_manager import get_run_by_id
    rec = get_run_by_id(out["run_id"])
    assert rec["status"] == "completed" and rec["agent_id"] == "swe_agent"
    assert rec["flow_run_id"] == flow_run_id and rec["flow_node_id"] == "judge"
    assert rec["in_process"] is True and rec["workspace"] == "acme"
    assert rec["title"] == "Polish — judge, iteration 1"
    assert "tests pass" in (rec.get("output") or "")
    assert out["row"]["run_id"] == out["run_id"]
    assert out["row"]["agent_id"] == "swe_agent"
    source = bell[0][0]["source"]
    assert source["flow_run_id"] == flow_run_id and source["node_id"] == "judge"
    assert source["loop_id"] == loop.loop_id and source["loop_run_id"] == run.loop_run_id
    assert source["message"]["params"]["flow"] == "Polish flow"


def test_a_model_judge_has_no_tools_and_no_scope(monkeypatch):
    from loops import runner

    loop, run = _loop(mode="model")
    seen: dict = {}

    def fake_evaluate(loop_, flow_, **kw):
        seen["kw"] = kw
        seen["ctx"] = tool_approvals.background_context("anything")
        return SimpleNamespace(verdict="continue"), 0.0

    monkeypatch.setattr(runner, "evaluate", fake_evaluate)
    runner._judge(loop, {"nodes": []}, run, "fr-x", goal="g", output="o", iteration=1,
                  history=[], state={}, workspace=None)
    assert seen["ctx"] is None and "run_id" not in seen["kw"]
