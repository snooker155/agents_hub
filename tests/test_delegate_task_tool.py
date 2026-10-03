"""Delegation inside a task (tools/delegation.py): a subtask, a real launch on
a chosen catalog model, and the result back to the caller.

The launcher is replaced by a fake that records what it was asked to start
and settles the run the way the child process would (a run record closed as
completed, the task result written), so no subprocess is spawned. The
registry is a small fake too, as in tests/test_delegation_guard.py.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from agents.registry import AgentSpec
from common.agent_context import current_agent_id, current_task_id
from tasks.service import add_subtask, create_task, get_task, set_task_result
from tools import delegation


CATALOG = {
    "openai": {"default": "gpt-4o-mini", "models": [
        {"id": "gpt-4o-mini", "enabled": True, "context_window": 128000},
        {"id": "gpt-4o", "enabled": False},
    ]},
    "anthropic": {"default": "", "models": [
        {"id": "claude-haiku-4-5", "enabled": True, "context_window": 200000},
        {"id": "shared-name", "enabled": True},
    ]},
    "ollama": {"default": "", "models": [{"id": "shared-name", "enabled": True}]},
}


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    monkeypatch.setattr("providers.catalog.load_catalog_raw", lambda: CATALOG)


@pytest.fixture(autouse=True)
def _direct_transport(monkeypatch):
    """The tool launches through the run's state transport, which defaults to
    the HTTP relay when the database is Postgres (tests/test_state_transport.py).
    There is no backend behind these tests, so pin the direct transport; the
    container tests below override it with in_container, which always picks
    HTTP and stubs the backend."""
    monkeypatch.setattr("common.config.run_state_transport", lambda: "db")


@pytest.fixture
def registry(monkeypatch):
    specs = {
        "lead": AgentSpec(id="lead", name="Lead", type="langchain", entrypoint="x",
                          provider="openai", model="gpt-4o"),
        "worker": AgentSpec(id="worker", name="Worker", type="langchain", entrypoint="x"),
        "picky": AgentSpec(id="picky", name="Picky", type="langchain", entrypoint="x",
                           delegates=["worker"]),
    }
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: specs.get(aid))
    monkeypatch.setattr("agents.registry.list_agents", lambda: list(specs.values()))
    monkeypatch.setattr("tools.langchain_tools.reg_get_agent", lambda aid: specs.get(aid))
    return specs


@pytest.fixture
def parent(monkeypatch):
    task = create_task("Parent work", "the whole job", workspace="default",
                       budget_usd=2.5, environment_id="env-1")
    token_task = current_task_id.set(str(task.id))
    token_agent = current_agent_id.set("lead")
    monkeypatch.setenv("AGENT_WORKSPACE", "default")
    monkeypatch.delenv(delegation.DEPTH_ENV, raising=False)
    monkeypatch.delenv("AGENT_RUN_ID", raising=False)
    try:
        yield task
    finally:
        current_task_id.reset(token_task)
        current_agent_id.reset(token_agent)


class FakeLauncher:
    """Stands in for agents.agent_launcher.start_run: remembers the launch,
    the child environment in force, and settles the run as asked."""

    def __init__(self, monkeypatch, *, outcome="completed", output="the answer"):
        self.calls = []
        self.outcome = outcome
        self.output = output
        monkeypatch.setattr("agents.agent_launcher.start_run", self)

    def __call__(self, task_id, agent_id, params=None, run_id=None):
        from runtime.entity_launch import _CHILD_ENV
        from managers.run_manager import update_run
        self.calls.append({"task_id": task_id, "agent_id": agent_id, "params": dict(params or {}),
                           "run_id": run_id, "env": dict(_CHILD_ENV.get() or {})})
        if self.outcome == "completed":
            set_task_result(_uuid(task_id), self.output, run_id=run_id, agent_id=agent_id)
            update_run(run_id, {"status": "completed", "output": self.output,
                                "provider": params.get("provider") or "openai",
                                "model": params.get("model") or "gpt-4o"})
        elif self.outcome == "failed":
            update_run(run_id, {"status": "failed", "error": "boom"})
        else:
            update_run(run_id, {"status": "running"})
        return run_id, None


def _uuid(value):
    from uuid import UUID
    return UUID(str(value))


def _call(**kwargs):
    return json.loads(delegation.delegate_task_tool.invoke(kwargs))


# ── the catalog ──────────────────────────────────────────────────────────────

def test_enabled_models_lists_only_enabled_ones_in_catalog_order():
    ids = [m["id"] for m in delegation.enabled_models()]
    assert ids == ["openai/gpt-4o-mini", "anthropic/claude-haiku-4-5", "anthropic/shared-name",
                   "ollama/shared-name"]


def test_resolve_model_accepts_full_and_unambiguous_bare_ids():
    assert delegation.resolve_model("openai/gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert delegation.resolve_model("claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")


def test_resolve_model_refuses_ambiguous_disabled_and_unknown():
    with pytest.raises(ValueError, match="several providers"):
        delegation.resolve_model("shared-name")
    with pytest.raises(ValueError, match="not an enabled catalog model"):
        delegation.resolve_model("openai/gpt-4o")
    with pytest.raises(ValueError, match="available: openai/gpt-4o-mini"):
        delegation.resolve_model("nope")


def test_list_models_tool_reports_catalog_default_and_self(registry, parent):
    data = json.loads(delegation.list_models_tool.invoke({}))
    assert data["ok"] is True
    assert [m["id"] for m in data["models"]][0] == "openai/gpt-4o-mini"
    assert data["self"] == {"provider": "openai", "model": "gpt-4o"}
    assert set(data["workspace_default"]) == {"provider", "model"}


# ── refusals ─────────────────────────────────────────────────────────────────

def test_refused_outside_a_task(registry):
    data = _call(agent_id="worker", input="do it")
    assert data["ok"] is False and data["code"] == "no_task"


def test_refused_for_an_unknown_agent(registry, parent):
    data = _call(agent_id="ghost", input="do it")
    assert data["code"] == "not_found"


def test_refused_by_the_callers_delegates_list(registry, parent, monkeypatch):
    token = current_agent_id.set("picky")
    try:
        assert _call(agent_id="lead", input="do it")["code"] == "forbidden"
        launcher = FakeLauncher(monkeypatch)
        assert _call(agent_id="worker", input="do it")["ok"] is True
        assert launcher.calls[0]["agent_id"] == "worker"
    finally:
        current_agent_id.reset(token)


def test_refused_past_the_depth_limit(registry, parent, monkeypatch):
    monkeypatch.setenv(delegation.DEPTH_ENV, str(delegation.max_depth()))
    data = _call(agent_id="worker", input="do it")
    assert data["code"] == "too_deep"
    assert data["max_depth"] == delegation.DEFAULT_MAX_DEPTH


def test_refused_past_the_concurrency_limit(registry, parent, monkeypatch):
    """max_concurrent_delegates (fifth-cycle stage 3): this run's own limit,
    read off its environment (agents.agent_launcher stamps it there at
    launch, see tests/test_run_overrides.py); here simulated directly, as
    test_refused_past_the_depth_limit does for the depth env var."""
    monkeypatch.setenv("AGENT_RUN_ID", "parent-run")
    monkeypatch.setenv(delegation.MAX_CONCURRENT_ENV, "1")
    FakeLauncher(monkeypatch, outcome="running")
    first = _call(agent_id="worker", input="first", wait=False)
    assert first["ok"] is True

    second = _call(agent_id="worker", input="second", wait=False)
    assert second["ok"] is False and second["code"] == "too_many_delegates"
    assert second["running"] == 1 and second["max_concurrent_delegates"] == 1


def test_concurrency_limit_frees_up_once_a_child_finishes(registry, parent, monkeypatch):
    from managers.run_manager import get_run_by_id, update_run

    monkeypatch.setenv("AGENT_RUN_ID", "parent-run")
    monkeypatch.setenv(delegation.MAX_CONCURRENT_ENV, "1")
    FakeLauncher(monkeypatch, outcome="running")
    first = _call(agent_id="worker", input="first", wait=False)
    assert first["ok"] is True
    # The run this one was delegated from is on the record, so a later
    # delegation from the same run can count it among its live children.
    assert get_run_by_id(first["run_id"])["parent_run_id"] == "parent-run"

    update_run(first["run_id"], {"status": "completed"})
    FakeLauncher(monkeypatch, outcome="completed")
    second = _call(agent_id="worker", input="second", wait=False)
    assert second["ok"] is True


def test_default_concurrency_limit_is_six_without_the_env_var(monkeypatch):
    monkeypatch.delenv(delegation.MAX_CONCURRENT_ENV, raising=False)
    assert delegation.current_max_concurrent_delegates() == 6
    monkeypatch.setenv(delegation.MAX_CONCURRENT_ENV, "40")  # clamped to the 1..32 range
    assert delegation.current_max_concurrent_delegates() == 32


def test_running_delegate_count_ignores_runs_outside_the_parent(registry, parent, monkeypatch):
    assert delegation.running_delegate_count("") == 0
    assert delegation.running_delegate_count("no-such-run") == 0


def test_refused_for_a_model_outside_the_catalog(registry, parent, monkeypatch):
    launcher = FakeLauncher(monkeypatch)
    data = _call(agent_id="worker", input="do it", model="openai/gpt-4o")
    assert data["code"] == "bad_model"
    assert "openai/gpt-4o-mini" in data["models"]
    assert launcher.calls == []


# ── the delegation itself ────────────────────────────────────────────────────

def test_delegation_creates_a_subtask_launches_on_the_model_and_returns_the_output(
        registry, parent, monkeypatch):
    launcher = FakeLauncher(monkeypatch, output="42 files, all green")
    monkeypatch.setenv("AGENT_RUN_ID", "parent-run")

    data = _call(agent_id="worker", input="Count the files\nand report", model="claude-haiku-4-5")

    assert data["ok"] is True, data
    assert data["finished"] is True and data["status"] == "completed"
    assert data["output"] == "42 files, all green"
    assert data["provider"] == "anthropic" and data["model"] == "claude-haiku-4-5"

    child = get_task(_uuid(data["task_id"]))
    assert child.parent_id == parent.id
    assert child.title == "Count the files"
    assert child.description == "Count the files\nand report"
    assert child.budget_usd == 2.5 and child.environment_id == "env-1"
    assert child.assigned_agent_type == "worker"
    assert child.assigned_agent_run_id == data["run_id"]
    assert "delegated by" in (child.routing_reason or "")

    [call] = launcher.calls
    assert call["task_id"] == data["task_id"]
    assert call["params"] == {"provider": "anthropic", "model": "claude-haiku-4-5"}
    assert call["env"][delegation.DEPTH_ENV] == "1"
    assert call["env"][delegation.PARENT_RUN_ENV] == "parent-run"


def test_without_a_model_the_delegate_keeps_its_own(registry, parent, monkeypatch):
    launcher = FakeLauncher(monkeypatch)
    data = _call(agent_id="worker", input="do it", title="A part")
    assert data["ok"] is True
    assert launcher.calls[0]["params"] == {}
    assert get_task(_uuid(data["task_id"])).title == "A part"


def test_depth_grows_along_the_chain(registry, parent, monkeypatch):
    launcher = FakeLauncher(monkeypatch)
    monkeypatch.setenv(delegation.DEPTH_ENV, "1")
    assert _call(agent_id="worker", input="do it")["ok"] is True
    assert launcher.calls[0]["env"][delegation.DEPTH_ENV] == "2"


def test_a_failed_delegate_is_reported_as_an_error(registry, parent, monkeypatch):
    FakeLauncher(monkeypatch, outcome="failed")
    data = _call(agent_id="worker", input="do it")
    assert data["ok"] is False and data["code"] == "delegate_failed"
    assert data["status"] == "failed" and data["error"] == "boom"


def test_wait_false_returns_right_after_the_launch(registry, parent, monkeypatch):
    FakeLauncher(monkeypatch, outcome="running")
    data = _call(agent_id="worker", input="do it", wait=False)
    assert data["ok"] is True and data["finished"] is False
    assert data["status"] == "running"
    assert "get_task_result" in data["message"]


def test_the_wait_times_out_and_leaves_the_run_going(registry, parent, monkeypatch):
    FakeLauncher(monkeypatch, outcome="running")
    monkeypatch.setattr(delegation, "POLL_SECONDS", 0.01)
    data = _call(agent_id="worker", input="do it", timeout_seconds=1)
    assert data["ok"] is True and data["finished"] is False
    assert "still running" in data["message"]
    assert data["waited_seconds"] >= 1


def test_a_stopped_parent_stops_the_child(registry, parent, monkeypatch):
    from managers.run_manager import preopen_run, update_run
    FakeLauncher(monkeypatch, outcome="running")
    monkeypatch.setattr(delegation, "POLL_SECONDS", 0.01)
    parent_run = f"parent-{uuid4().hex[:8]}"
    preopen_run(parent_run, "lead", task_id=str(parent.id), status="running")
    update_run(parent_run, {"status": "stop"})
    monkeypatch.setenv("AGENT_RUN_ID", parent_run)
    stopped = []
    monkeypatch.setattr("managers.run_manager.stop_run",
                        lambda task_id, run_id=None: stopped.append(run_id) or True)

    data = _call(agent_id="worker", input="do it")

    assert data["ok"] is False and data["code"] == "stopped"
    assert stopped == [data["run_id"]]


def test_a_budget_refusal_blocks_the_subtask(registry, parent, monkeypatch):
    from common.budget import BudgetExceededError

    def refuse(task_id, agent_id, params=None, run_id=None):
        raise BudgetExceededError("default", 12.0, 10.0, "monthly")

    monkeypatch.setattr("agents.agent_launcher.start_run", refuse)
    data = _call(agent_id="worker", input="do it")
    assert data["code"] == "budget"
    child = get_task(_uuid(data["task_id"]))
    assert child.status.value == "blocked" and "budget cap" in (child.blocked_reason or "")


# ── the pieces underneath ────────────────────────────────────────────────────

def test_add_subtask_inherits_the_parents_cap_and_environment(parent):
    child = add_subtask(parent.id, "part")
    assert child.budget_usd == 2.5 and child.environment_id == "env-1"
    own = add_subtask(parent.id, "part", budget_usd=0.5, environment_id="env-2")
    assert own.budget_usd == 0.5 and own.environment_id == "env-2"


def test_model_overrides_only_carry_what_was_given():
    from runtime.agent_run import model_overrides
    assert model_overrides(None, None) == {}
    assert model_overrides("", " ") == {}
    assert model_overrides("openai", None) == {"provider": "openai"}
    assert model_overrides(None, "gpt-4o-mini") == {"model": "gpt-4o-mini"}


def test_prepare_run_passes_the_model_as_flags(registry, parent):
    from agents.agent_launcher import prepare_run
    spec = prepare_run(str(parent.id), "worker", {"provider": "anthropic", "model": "claude-haiku-4-5"})
    args = spec["cli_args"]
    assert args[args.index("--provider") + 1] == "anthropic"
    assert args[args.index("--model") + 1] == "claude-haiku-4-5"
    plain = prepare_run(str(parent.id), "worker", {})
    assert "--model" not in plain["cli_args"] and "--provider" not in plain["cli_args"]


# ── where the launch happens: in-process on a host, on the backend from a container ──


class FakeBackend:
    """Stands in for the ``requests`` module HttpStateTransport uses: records
    every call and answers the delegation routes the way the backend would."""

    def __init__(self, monkeypatch, *, answer=None, unreachable=False):
        import sys as _sys
        self.calls = []
        self.answer = answer if answer is not None else {
            "ok": True,
            "task": {"id": "child-1", "status": "in_progress", "title": "part"},
            "run": {"run_id": "run-1", "status": "running"},
            "requested": {},
            "agent": {"id": "worker", "name": "Worker"},
        }
        self.unreachable = unreachable
        backend = self

        class _Response:
            def json(self):
                return backend.answer

        class _Requests:
            @staticmethod
            def request(method, url, json=None, headers=None, timeout=None):
                backend.calls.append({"method": method, "url": url, "json": json, "timeout": timeout})
                if backend.unreachable:
                    raise ConnectionError("no route to host")
                return _Response()

        monkeypatch.setitem(_sys.modules, "requests", _Requests)
        from common import auth
        monkeypatch.setattr(auth, "auth_headers", lambda: {"Authorization": "Bearer tok"})


@pytest.fixture
def in_container(monkeypatch):
    from common import hostnet
    monkeypatch.setattr(hostnet, "in_container", lambda: True)
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)


def test_on_a_host_the_launch_follows_the_runs_own_transport(registry, parent, monkeypatch):
    """A host subprocess delegates the way it keeps its own records: in
    process under the direct transport, over HTTP under the http one."""
    from common import hostnet
    from common.state_transport import DirectStateTransport, HttpStateTransport
    monkeypatch.setattr(hostnet, "in_container", lambda: False)
    monkeypatch.setattr("common.config.run_state_transport", lambda: "db")
    assert isinstance(delegation._transport(), DirectStateTransport)
    monkeypatch.setattr("common.config.run_state_transport", lambda: "http")
    assert isinstance(delegation._transport(), HttpStateTransport)


def test_inside_a_container_the_backend_launches(registry, parent, monkeypatch, in_container):
    """The tool decides what to delegate; the backend creates the subtask and
    launches, so nothing is spawned inside the container itself."""
    launcher = FakeLauncher(monkeypatch)
    backend = FakeBackend(monkeypatch)
    monkeypatch.setenv(delegation.DEPTH_ENV, "1")
    monkeypatch.setenv("AGENT_RUN_ID", "parent-run")

    out = _call(agent_id="worker", input="do the part", wait=False)

    assert out["ok"] is True
    assert out["task_id"] == "child-1" and out["run_id"] == "run-1"
    assert launcher.calls == []  # no in-process launch
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"http://host.docker.internal:8000/api/run-state/tasks/{parent.id}/delegate"
    body = call["json"]
    assert body["agent_id"] == "worker" and body["input"] == "do the part"
    assert body["caller_agent_id"] == "lead" and body["workspace"] == "default"
    assert body["depth"] == 1
    assert body["env"] == {delegation.DEPTH_ENV: "2", delegation.PARENT_RUN_ENV: "parent-run"}
    assert body["launched_by"]


def test_inside_a_container_a_refusal_from_the_backend_is_the_tools_refusal(registry, parent, monkeypatch, in_container):
    FakeBackend(monkeypatch, answer={"ok": False, "error": "Agent not found", "code": "not_found",
                                     "agent_id": "ghost"})
    out = _call(agent_id="ghost", input="x")
    assert out["ok"] is False and out["code"] == "not_found" and out["agent_id"] == "ghost"


def test_inside_a_container_an_unreachable_backend_is_a_refusal_not_a_local_launch(
        registry, parent, monkeypatch, in_container):
    launcher = FakeLauncher(monkeypatch)
    FakeBackend(monkeypatch, unreachable=True)
    out = _call(agent_id="worker", input="do the part")
    assert out["ok"] is False and out["code"] == "unreachable"
    assert launcher.calls == []


def test_inside_a_container_the_wait_polls_the_backend(registry, parent, monkeypatch, in_container):
    backend = FakeBackend(monkeypatch)
    monkeypatch.setattr(delegation, "POLL_SECONDS", 0.01)
    states = iter([
        {"run": {"run_id": "run-1", "status": "running"}, "task": {"id": "child-1", "status": "in_progress"}, "output": ""},
        {"run": {"run_id": "run-1", "status": "completed", "provider": "openai", "model": "gpt-4o"},
         "task": {"id": "child-1", "status": "done"}, "output": "the answer"},
    ])
    launch_answer = dict(backend.answer)

    class _Router:
        """Answer the launch, then each poll from the states above."""
        def json(self):
            return None

    def request(method, url, json=None, headers=None, timeout=None):
        backend.calls.append({"method": method, "url": url, "json": json})
        resp = _Router()
        if url.endswith("/delegate"):
            resp.json = lambda: launch_answer
        elif "/delegation" in url:
            resp.json = lambda: next(states)
        else:
            resp.json = lambda: {"run": {"run_id": "parent-run", "status": "running"}}
        return resp

    import sys as _sys
    _sys.modules["requests"].request = staticmethod(request)

    out = _call(agent_id="worker", input="do the part")
    assert out["ok"] is True and out["finished"] is True
    assert out["output"] == "the answer" and out["model"] == "gpt-4o"
    polls = [c for c in backend.calls if "/delegation" in c["url"]]
    assert len(polls) == 2
    assert polls[0]["url"] == "http://host.docker.internal:8000/api/run-state/tasks/child-1/delegation?run_id=run-1"


def test_the_backend_side_launches_exactly_as_the_tool_did(registry, parent, monkeypatch):
    """tasks.delegate.launch_delegation is what the route runs: the same
    subtask, the same launch, the same child environment."""
    from tasks.delegate import launch_delegation
    launcher = FakeLauncher(monkeypatch)

    out = launch_delegation(str(parent.id), {
        "agent_id": "worker", "input": "do the part", "model": "openai/gpt-4o-mini",
        "workspace": "default", "caller_agent_id": "lead", "depth": 0,
        "env": {delegation.DEPTH_ENV: "1", delegation.PARENT_RUN_ENV: "parent-run"},
    })

    assert out["ok"] is True
    assert out["agent"] == {"id": "worker", "name": "Worker"}
    assert out["requested"] == {"provider": "openai", "model": "gpt-4o-mini"}
    assert out["task"]["parent_id"] == str(parent.id)
    assert out["run"]["run_id"] == launcher.calls[0]["run_id"]
    assert launcher.calls[0]["env"][delegation.DEPTH_ENV] == "1"
    assert launcher.calls[0]["env"][delegation.PARENT_RUN_ENV] == "parent-run"


def test_the_backend_side_applies_the_callers_allowlist(registry, parent, monkeypatch):
    from tasks.delegate import launch_delegation
    launcher = FakeLauncher(monkeypatch)
    out = launch_delegation(str(parent.id), {
        "agent_id": "lead", "input": "x", "caller_agent_id": "picky", "depth": 0, "env": {},
    })
    assert out["ok"] is False and out["code"] == "forbidden"
    assert launcher.calls == []


def test_delegation_status_reads_the_run_the_task_and_the_result(registry, parent, monkeypatch):
    from tasks.delegate import delegation_status, launch_delegation
    FakeLauncher(monkeypatch, output="the answer")
    launched = launch_delegation(str(parent.id), {"agent_id": "worker", "input": "part", "depth": 0, "env": {}})
    snap = delegation_status(launched["task"]["id"], launched["run"]["run_id"])
    assert snap["run"]["status"] == "completed"
    assert snap["task"]["id"] == launched["task"]["id"]
    assert snap["output"] == "the answer"
