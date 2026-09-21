"""
Smoke tests for the three subprocess entry points that previously had none:
``runtime/flow_run.py``, ``runtime/node_run.py`` and ``runtime/agent_run.py``.

No LLM or network call happens in any of these tests. The seam is always the
same shape: ``agents.agent_lifecycle.create_agent`` / ``.invoke_agent`` (the
shared build->invoke->finalize arc used by both the flow node executor and the
plain agent runner) are monkeypatched to return canned objects, so the real
run-record / task-store plumbing around them runs against the temp sqlite db
from ``tests/conftest.py``.

Run: ``python -m pytest tests/test_runtime_smoke.py -q``
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from managers.run_manager import get_run_by_id
from tasks import service as ts


def _fake_stats(**over):
    base = dict(prompt_tokens=1, completion_tokens=2, total_tokens=3,
                cached_prompt_tokens=0, tool_calls=0)
    base.update(over)
    return SimpleNamespace(**base)


# ── runtime/flow_run.py ──────────────────────────────────────────────────────

def test_flow_run_main_missing_flow_exits_failed(monkeypatch):
    """A flow id that resolves to nothing must fail preflight cleanly: exit(1),
    finalize_flow_task('failed', ...), and close the flow-run record."""
    import flow.store as flow_store
    import runtime.flow_run as rf

    monkeypatch.setattr(flow_store, "get_flow", lambda flow_id: None)

    calls: dict = {}
    monkeypatch.setattr(
        rf.run_store, "close_flow_run",
        lambda run_id, **kw: calls.setdefault("close_flow_run", (run_id, kw)),
    )
    monkeypatch.setattr(
        rf, "_set_flow_running",
        lambda flow_id, running: calls.setdefault("set_flow_running", (flow_id, running)),
    )
    monkeypatch.setattr(
        rf, "finalize_flow_task",
        lambda task_id, status, code, **kw: calls.setdefault(
            "finalize_flow_task", (task_id, status, code, kw)),
    )

    argv = [
        "flow_run.py",
        "--flow-id", "does-not-exist",
        "--workspace", "/tmp/some-ws",
        "--task-id", "task-missing",
        "--run-id", "run-missing",
        "--session-id", "sess-missing",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        rf.main()

    assert exc.value.code == 1
    run_id, close_kw = calls["close_flow_run"]
    assert run_id == "run-missing"
    assert close_kw["status"] == "failed"
    assert close_kw["exit_code"] == 1
    assert "does-not-exist" in close_kw["error"]
    assert calls["set_flow_running"] == ("does-not-exist", False)
    assert calls["finalize_flow_task"][0] == "task-missing"
    assert calls["finalize_flow_task"][1] == "failed"
    assert calls["finalize_flow_task"][2] == 1


def test_flow_run_main_single_node_flow_completes(monkeypatch, tmp_path):
    """Happy path: a one-node agent flow, driven end to end through
    runtime.flow_run.main() -> flow.engine.run_flow_engine -> flow.task_driver,
    with only the agent build/invoke seam (agents.agent_lifecycle) stubbed out.
    Asserts the flow-run record closes 'completed' and the task is finalized."""
    import flow.store as flow_store
    import flow.validate as fv
    import flow.dispatch as fd
    import agents.agent_lifecycle as al
    import runtime.flow_run as rf

    task = ts.create_task("run the flow")

    flow_def = {
        "id": "flow-1",
        "name": "Test Flow",
        "nodes": [{"id": "n1", "agent_id": "test_agent", "label": "Node One", "output": ["result"]}],
        "edges": [],
    }
    monkeypatch.setattr(flow_store, "get_flow", lambda flow_id: flow_def)
    # Bypass full structural/entity preflight (covered by tests/test_flow_engine.py
    # and tests/test_flow_validate.py); this test is about the runner's wiring.
    monkeypatch.setattr(fv, "validate_flow", lambda flow: None)
    monkeypatch.setattr(fv, "resolve_entities", lambda nodes: {})

    class _FakeSpec:
        def __init__(self, node):
            self.id = node["id"]
            self.name = node.get("label")
            self.category = "agent"

    class _FakeAgentEntity:
        runs_in_process = True

        def __init__(self, node):
            self.spec = _FakeSpec(node)

    monkeypatch.setattr(fd.FlowEntity, "for_node", classmethod(lambda cls, node: _FakeAgentEntity(node)))

    # The seam: both the task-subprocess runner (runtime.agent_run) and the flow
    # node executor (flow.task_driver) build/invoke agents through this shared
    # arc, so stubbing it here avoids any real LLM/provider call.
    fake_agent = SimpleNamespace(provider="prov", model="mdl", system_prompt="sys prompt")
    monkeypatch.setattr(al, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)
    fake_result = SimpleNamespace(ok=True, agent_output="node output", error=None)
    fake_invocation = SimpleNamespace(result=fake_result, duration_ms=5, process={}, stats=_fake_stats())
    monkeypatch.setattr(al, "invoke_agent", lambda *a, **kw: fake_invocation)

    calls: dict = {}
    monkeypatch.setattr(
        rf.run_store, "close_flow_run",
        lambda run_id, **kw: calls.setdefault("close_flow_run", (run_id, kw)),
    )
    monkeypatch.setattr(
        rf, "_set_flow_running",
        lambda flow_id, running: calls.setdefault("set_flow_running", (flow_id, running)),
    )
    monkeypatch.setattr(
        rf, "finalize_flow_task",
        lambda task_id, status, code, **kw: calls.setdefault(
            "finalize_flow_task", (task_id, status, code, kw)),
    )

    argv = [
        "flow_run.py",
        "--flow-id", "flow-1",
        "--workspace", str(tmp_path),
        "--task-id", str(task.id),
        "--run-id", "run-42",
        "--session-id", "sess-42",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    rf.main()  # no exit on the success path

    assert calls["close_flow_run"][0] == "run-42"
    assert calls["close_flow_run"][1]["status"] == "completed"
    assert calls["close_flow_run"][1]["exit_code"] == 0
    assert calls["set_flow_running"] == ("flow-1", False)
    assert calls["finalize_flow_task"][:3] == (str(task.id), "completed", 0)


# ── runtime/node_run.py ──────────────────────────────────────────────────────
#
# run_orchestrator_loop / run_worker_loop are `while True` polling loops that
# re-import half a dozen service modules (tasks.service, workspace,
# agents.agent_factory, projects.storage, common.session_service...) on every
# sweep and only exit via KeyboardInterrupt/process death. Driving even one
# iteration hermetically would mean monkeypatching all of those plus
# time.sleep, well past the ~40-line budget for a smoke test, and would mostly
# re-test the same create_agent/invoke_agent seam already covered above and in
# test_agent_run_main_success below. So instead this covers the two loops'
# extracted, already-testable pieces: the instance-inbox drain (both loops
# check it before anything else on each sweep) and the status setter.

def test_drain_instance_inbox_empty_returns_false():
    import runtime.node_run as nr

    # No instance registered for this node id -> instance_store.get_by_node
    # returns None -> the function bails out before touching anything else.
    assert nr._drain_instance_inbox("no-such-node", "swe_agent", None) is False


def test_set_status_running_then_terminal(monkeypatch):
    import runtime.node_run as nr
    import managers.node_manager as node_manager

    calls = []
    monkeypatch.setattr(
        node_manager, "update_node",
        lambda node_id, updates: calls.append((node_id, dict(updates))),
    )

    nr._set_status("node-1", "running")
    node_id, updates = calls[-1]
    assert node_id == "node-1"
    assert updates["status"] == "running"
    assert "finished_at" not in updates  # only the terminal branch sets it

    nr._set_status("node-1", "completed", exit_code=0)
    node_id, updates = calls[-1]
    assert updates["status"] == "completed"
    assert updates["exit_code"] == 0
    assert "finished_at" in updates


def test_session_publisher_without_session_id_is_noop():
    import runtime.node_run as nr

    assert nr._session_publisher(None, "run-1", "agent-1") == []


def test_session_publisher_with_session_id_returns_one_callback():
    import runtime.node_run as nr
    from agents.callbacks import SessionPublishCallback

    cbs = nr._session_publisher("sess-1", "run-1", "agent-1")
    assert len(cbs) == 1
    assert isinstance(cbs[0], SessionPublishCallback)


# ── runtime/agent_run.py ─────────────────────────────────────────────────────

def test_agent_run_main_success(monkeypatch, tmp_path):
    import runtime.agent_run as ar
    import agents.registry as registry
    import agents.agent_lifecycle as al

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(id=agent_id))

    fake_agent = SimpleNamespace(provider="prov", model="mdl", system_prompt="sys")
    monkeypatch.setattr(al, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)
    fake_result = SimpleNamespace(ok=True, agent_output="agent did the thing", error=None)
    fake_invocation = SimpleNamespace(result=fake_result, duration_ms=7, process={}, stats=_fake_stats())
    monkeypatch.setattr(al, "invoke_agent", lambda *a, **kw: fake_invocation)

    # Pre-set AGENT_LOG_FILE so _register_run_start skips _setup_cli_log, which
    # would otherwise repoint sys.stdout/sys.stderr at a Tee for the rest of the
    # test process.
    monkeypatch.setenv("AGENT_LOG_FILE", str(tmp_path / "agent.log"))
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    argv = ["agent_run.py", "test_agent", "do the thing", "--workspace", "ws1", "--run-id", "run-abc"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        ar.main()
    assert exc.value.code == 0

    rec = get_run_by_id("run-abc")
    assert rec["status"] == "completed"
    assert rec["output"] == "agent did the thing"


def test_agent_run_main_failure(monkeypatch, tmp_path):
    import runtime.agent_run as ar
    import agents.registry as registry
    import agents.agent_lifecycle as al

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(id=agent_id))

    fake_agent = SimpleNamespace(provider="prov", model="mdl", system_prompt="sys")
    monkeypatch.setattr(al, "create_agent", lambda agent_id, workspace=None, **kw: fake_agent)
    fake_result = SimpleNamespace(ok=False, agent_output=None, error="boom")
    fake_invocation = SimpleNamespace(result=fake_result, duration_ms=3, process={}, stats=_fake_stats())
    monkeypatch.setattr(al, "invoke_agent", lambda *a, **kw: fake_invocation)

    monkeypatch.setenv("AGENT_LOG_FILE", str(tmp_path / "agent2.log"))
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    argv = ["agent_run.py", "test_agent", "do the thing", "--workspace", "ws1", "--run-id", "run-fail"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        ar.main()
    assert exc.value.code == 1

    rec = get_run_by_id("run-fail")
    assert rec["status"] == "failed"
    assert rec["error"] == "boom"
