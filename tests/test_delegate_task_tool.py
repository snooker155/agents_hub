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
