"""The per-call policy trail: every tool call of a run carries
``evaluated_permission`` and ``reason_code`` (tools/permission_policy.py), in
the run payload's ``tool_calls``, on the live ``tool_end`` event and in the log
marker the run views parse back; deny, ask and auto decisions write audit rows,
a plain allow does not."""
from __future__ import annotations

import pytest
from langchain_core.tools import tool

from agents import hooks
from agents.agent_loop import LoopState, reset_state, set_state
from agents.callbacks.chat_stream import ChatStreamCallback
from agents.callbacks.guards import ApprovalSignal
from agents.callbacks.run_statistics import StatsCollectorCallback
from agents.registry import AgentSpec
from common import audit
from tools import permission_policy as policy


@tool("run_shell")
def fake_run_shell(command: str) -> str:
    """Stand-in for the real shell tool."""
    return f"ran {command}"


@tool("read_file")
def fake_read_file(path: str) -> str:
    """Stand-in for a harmless tool."""
    return f"contents of {path}"


def _spec(tool_policy=None):
    return AgentSpec(id="trail_agent", name="Trail agent", type="local",
                     entrypoint="agents.standard_agent:StandardAgent",
                     description="Tidies files.", tool_policy=dict(tool_policy or {}))


@pytest.fixture
def settings(monkeypatch):
    current: dict = {}
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    monkeypatch.delenv("AGENT_TASK_ID", raising=False)
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {})
    monkeypatch.setattr("workspace.get_workspace_metadata", lambda name: {"settings": current})
    return current


@pytest.fixture
def state():
    st = LoopState(run_id="trail-run", agent_id="trail_agent", workspace="acme")
    token = set_state(st)
    yield st
    reset_state(token)


@pytest.fixture
def classifier(monkeypatch):
    box = {"reply": '{"decision": "run", "reason": "fits"}'}

    def fake(provider, model, system, user, timeout):
        if isinstance(box["reply"], BaseException):
            raise box["reply"]
        return box["reply"]

    monkeypatch.setattr(policy, "ask_model", fake)
    return box


def _rows():
    rows = audit.query(action="tool.policy")
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    return [r for r in rows if (r.get("details") or {}).get("agent_id") == "trail_agent"]


def _run(tools, calls, callbacks):
    """Run each (tool name, input) through the wrapped tools with callbacks."""
    by_name = {t.name: t for t in tools}
    out = []
    for name, args in calls:
        try:
            out.append(by_name[name].invoke(args, config={"callbacks": callbacks}))
        except ApprovalSignal as sig:
            out.append(sig)
    return out


# -------------------- reason codes --------------------

@pytest.mark.parametrize("kwargs,code", [
    ({"by": "hook", "decision": "deny"}, "hook_deny"),
    ({"by": "hook", "decision": "ask"}, "hook_ask"),
    ({"by": "auto", "decision": "run"}, "auto_run"),
    ({"by": "auto", "decision": "deny"}, "auto_deny"),
    ({"by": "auto", "decision": "ask", "reason": "touches prod"}, "auto_ask"),
    ({"by": "auto", "decision": "ask",
      "reason": "The policy classifier did not answer in time, so a person decides."}, "auto_unclear"),
    ({"by": "policy", "decision": "ask", "mode": "always_ask", "source": "approval_list"}, "approval_list"),
    ({"by": "policy", "decision": "ask", "mode": "always_ask", "source": "agent"}, "policy_always_ask"),
    ({"by": "policy", "decision": "run", "mode": "always_allow", "source": "workspace"}, "policy_always_allow"),
    ({"by": "policy", "decision": "run", "mode": "always_allow", "source": "default"}, "default_allow"),
    ({"by": "auto", "decision": "run", "approved": True}, "human_approved"),
])
def test_reason_codes(kwargs, code):
    assert policy.reason_code(**kwargs) == code
    assert code in policy.REASON_CODES


def test_a_call_no_guard_saw_reads_as_default_allow(state):
    stats = StatsCollectorCallback()
    _run([fake_read_file], [("read_file", {"path": "a"})], [stats])
    assert stats.tool_history[0]["evaluated_permission"] == "allow"
    assert stats.tool_history[0]["reason_code"] == "default_allow"
    assert policy.default_verdict("think")["reason_code"] == "never_gated"


# -------------------- through the guard and the callbacks --------------------

def test_every_call_carries_the_two_fields(settings, state, classifier, tmp_path):
    settings["require_tool_approval"] = True
    spec = _spec({"read_file": "always_allow", "run_shell": "auto"})
    tools = hooks.guard_action_tools([fake_read_file, fake_run_shell],
                                     agent_id="trail_agent", spec=spec, workspace="acme")
    stats = StatsCollectorCallback()
    events: list = []
    chat = _chat(events, tmp_path)
    callbacks = [stats, chat]

    classifier["reply"] = '{"decision": "deny", "reason": "not this"}'
    _run(tools, [("read_file", {"path": "a"}), ("run_shell", {"command": "rm -rf /"})], callbacks)

    first, second = stats.tool_history
    assert (first["evaluated_permission"], first["reason_code"]) == ("allow", "policy_always_allow")
    assert (second["evaluated_permission"], second["reason_code"]) == ("deny", "auto_deny")
    process = stats.build_process(1)
    assert process["tool_calls"][1]["reason_code"] == "auto_deny"
    # The log marker carries both, for the run views to parse back.
    marks = [line for line in stats.thinking_history if line.startswith("[tool_call]")]
    assert "permission=deny reason_code=auto_deny" in marks[1]
    ends = [e for e in events if e.get("type") == "tool_end"]
    assert ends[-1]["evaluated_permission"] == "deny"
    assert ends[-1]["reason_code"] == "auto_deny"
    assert chat.tool_history[-1]["reason_code"] == "auto_deny"
    assert "permission=deny reason_code=auto_deny" in "\n".join(chat.log_lines)

    # The plain always_allow is not in the Tool policy collection, the auto deny is.
    assert [d["tool"] for d in state.tool_decisions] == ["run_shell"]


def _chat(events: list, tmp_path) -> ChatStreamCallback:
    """A chat stream callback whose events land in *events*."""
    class _Chat(ChatStreamCallback):
        def _emit(self, payload: dict):
            events.append(payload)

        def _abort_if_cancelled(self):
            return None

    return _Chat(None, None, [], tmp_path / "chat.log", run_id="trail-run")


def test_hook_deny_and_ask_in_chat(settings, state, monkeypatch):
    spec = _spec()
    monkeypatch.setattr(hooks, "load_hooks", lambda ws: {"PreToolUse": [{"matcher": ".*"}]})
    tools = hooks.guard_action_tools([fake_run_shell], agent_id="trail_agent", spec=spec,
                                     workspace="acme")
    stats = StatsCollectorCallback()
    monkeypatch.setattr(hooks, "run_pre_tool_use",
                        lambda *a, **k: hooks.HookOutcome("deny", "no shell", "rules"))
    _run(tools, [("run_shell", {"command": "ls"})], [stats])
    monkeypatch.setattr(hooks, "run_pre_tool_use",
                        lambda *a, **k: hooks.HookOutcome("ask", "check first", "rules"))
    _run(tools, [("run_shell", {"command": "pwd"})], [stats])
    codes = [(c["evaluated_permission"], c["reason_code"]) for c in stats.tool_history]
    assert codes == [("deny", "hook_deny"), ("ask", "hook_ask")]


def test_approval_list_in_a_task_parks_and_reads_ask(settings, state):
    from common.agent_context import current_task_id
    from tasks import service as ts
    task = ts.create_task("trail work", workspace="acme")
    token = current_task_id.set(str(task.id))
    try:
        settings["require_tool_approval"] = True
        tools = hooks.guard_action_tools([fake_run_shell], agent_id="trail_agent", spec=_spec(),
                                         workspace="acme")
        stats = StatsCollectorCallback()
        out = _run(tools, [("run_shell", {"command": "make"})], [stats])
        assert isinstance(out[0], ApprovalSignal)
        assert stats.tool_history[0]["evaluated_permission"] == "ask"
        assert stats.tool_history[0]["reason_code"] == "approval_list"
    finally:
        current_task_id.reset(token)


def test_unclear_classifier_reads_auto_unclear(settings, state, classifier):
    classifier["reply"] = RuntimeError("provider down")
    tools = hooks.guard_action_tools([fake_run_shell], agent_id="trail_agent",
                                     spec=_spec({"*": "auto"}), workspace="acme")
    stats = StatsCollectorCallback()
    _run(tools, [("run_shell", {"command": "x"})], [stats])
    assert stats.tool_history[0]["reason_code"] == "auto_unclear"
    assert stats.tool_history[0]["evaluated_permission"] == "ask"


# -------------------- audit --------------------

def test_audit_rows_for_deny_ask_and_auto_not_for_plain_allow(settings, state, classifier, monkeypatch):
    before = len(_rows())
    spec = _spec({"read_file": "always_allow", "run_shell": "auto"})
    tools = hooks.guard_action_tools([fake_read_file, fake_run_shell], agent_id="trail_agent",
                                     spec=spec, workspace="acme")
    stats = StatsCollectorCallback()
    _run(tools, [("read_file", {"path": "a"}), ("read_file", {"path": "b"})], [stats])
    assert len(_rows()) == before

    classifier["reply"] = '{"decision": "run", "reason": "fine"}'
    _run(tools, [("run_shell", {"command": "make"})], [stats])
    rows = _rows()
    assert len(rows) == before + 1
    details = rows[0]["details"]
    assert details["reason_code"] == "auto_run"
    assert details["evaluated_permission"] == "allow"

    # A cached repeat of the same call is on the trail, not in the audit log.
    _run(tools, [("run_shell", {"command": "make"})], [stats])
    assert len(_rows()) == before + 1
    assert stats.tool_history[-1]["reason_code"] == "auto_run"


# -------------------- the run views --------------------

def test_run_views_parse_the_marker_and_the_stored_payload():
    from dashboard.backend.routes.sessions import _attach_tool_verdicts, _extract_tools_from_log
    log = "\n".join([
        "[tool_start] step=1 tool=run_shell input={'command': 'ls'}",
        "[tool_end] output=nope",
        "[tool_call] 2026-10-03T10:00:00 step=1 tool=run_shell duration_ms=3 "
        "permission=deny reason_code=hook_deny",
    ])
    tools = _extract_tools_from_log(log)
    assert tools[0]["evaluated_permission"] == "deny"
    assert tools[0]["reason_code"] == "hook_deny"

    stored = [{"step": 4, "tool": "read_file", "evaluated_permission": "allow",
               "reason_code": "default_allow"}]
    progress = [{"step": 4, "tool": "read_file"}]
    _attach_tool_verdicts(progress, "", stored)
    assert progress[0]["reason_code"] == "default_allow"


def test_think_gate_refusal_reads_think_required(state):
    from reasoning.think_gate import _note_refusal
    _note_refusal("write_file")
    assert policy.call_verdict("write_file", object()) == {
        "evaluated_permission": "deny", "reason_code": "think_required"}


def test_several_callbacks_each_read_the_same_verdict(state):
    policy.note_call("run_shell", "deny", "hook_deny")
    a, b = object(), object()
    assert policy.call_verdict("run_shell", a)["reason_code"] == "hook_deny"
    assert policy.call_verdict("run_shell", b)["reason_code"] == "hook_deny"
    # Read once per consumer: the next call of the tool is a different entry.
    assert policy.call_verdict("run_shell", a)["reason_code"] == "default_allow"
