"""
One workspace per run (common/workspace_scope.py): workspace management and
the pinned ``workspace`` argument, for every workspace. The per tool lookups by id have their own files
(tests/test_workspace_scope_coordination.py, tests/test_workspace_scope_data.py).
"""
from __future__ import annotations

import json


from common import workspace_scope as scope


def _spec(agent_id, tools, **kw):
    from agents.registry import AgentSpec
    return AgentSpec(id=agent_id, name=agent_id, description="d", type="local", entrypoint="m:f",
                     tools=list(tools), **kw)


def test_workspace_management_is_the_main_agents_in_default(monkeypatch):
    monkeypatch.setattr(scope, "WORKSPACE_ADMIN_TOOLS", frozenset({"create_workspace_tool"}))
    assert not scope.tool_allowed("helper", "create_workspace_tool")
    assert scope.tool_allowed("main-agent", "create_workspace_tool")
    assert scope.tool_allowed("main-agent", "create_workspace_tool", "default")
    assert not scope.tool_allowed("main-agent", "create_workspace_tool", "team-a")


def test_the_bootstrap_agents_already_follow_the_rules():
    from pathlib import Path
    seed = json.loads((Path(__file__).resolve().parents[1] / "bootstrap" / "agents.json").read_text())
    assert {a["id"]: scope.offenders(a["id"], a.get("tools") or []) for a in seed["agents"]
            if scope.offenders(a["id"], a.get("tools") or [])} == {}


def _build(agent_id, tools, workspace, monkeypatch):
    from agents.agent_factory import AgentFactory
    from agents.registry import add_agent
    from workspace import create_workspace_folder, get_workspace_folder
    create_workspace_folder(workspace)
    add_agent(_spec(agent_id, tools), user_edit=False)
    monkeypatch.setattr("agents.agent_factory.AgentFactory.load_definition",
                        lambda self, aid: {"id": aid, "name": aid, "system_prompt": "You help.",
                                           "tools": list(tools), "provider": "openai",
                                           "model": "gpt-4o-mini"}, raising=False)
    return AgentFactory()._build_agent(agent_id, workspace=str(get_workspace_folder(workspace)))


def test_every_workspace_pins_the_workspace_argument(monkeypatch):
    agent = _build("planner_x", ["create_task", "list_tasks"], "team-a", monkeypatch)
    create_task = next(t for t in agent._tools if t.name == "create_task")
    import uuid
    elsewhere = f"never-made-{uuid.uuid4().hex[:8]}"
    refused = json.loads(create_task.invoke({"title": "x", "workspace": elsewhere}))
    assert refused["code"] == "other_workspace"
    from workspace import get_workspace_folder
    assert get_workspace_folder(elsewhere) is None  # naming it did not create it


def test_check_record_follows_the_run(monkeypatch):
    from agents.agent_loop import LoopState, reset_state, set_state
    token = set_state(LoopState(run_id="r", agent_id="helper", workspace="team-a"))
    monkeypatch.setenv("AGENT_WORKSPACE", "team-a")
    try:
        assert scope.check_record("team-a") is None
        assert "not in this workspace" in scope.check_record("team-b", what="view v1")
    finally:
        reset_state(token)

