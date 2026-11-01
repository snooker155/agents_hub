"""
A workspace's name comes from the run (the request, the run record, the
launcher's AGENT_WORKSPACE), never from a folder: a project folder or the
/workspace mount of an agent container names no workspace. The folder an agent
works in is passed beside the name (``workdir``), and only file tools use it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from common.workspace_context import _workspace_ctx, name_of, run_workspace


@pytest.fixture
def no_run(monkeypatch):
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    token = _workspace_ctx.set(None)
    yield
    _workspace_ctx.reset(token)


def test_the_run_names_its_workspace(no_run, monkeypatch):
    assert run_workspace() is None
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    assert run_workspace() == "acme"
    token = _workspace_ctx.set("beta")  # the request's, over the process's
    try:
        assert run_workspace() == "beta"
    finally:
        _workspace_ctx.reset(token)


def test_a_folder_never_names_a_workspace(no_run, monkeypatch):
    from workspace import WORKSPACES_ROOT
    assert name_of("acme") == "acme"
    assert name_of(None) is None
    # A folder, even one under the workspaces root, gives the run's name.
    for folder in ("/workspace", "/workspace/site", str(Path(WORKSPACES_ROOT) / "other" / "proj")):
        assert name_of(folder) is None
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    assert name_of("/workspace") == "acme"
    assert name_of(str(Path(WORKSPACES_ROOT) / "other")) == "acme"


def test_a_launch_argument_names_a_workspace_only_as_a_name(no_run, monkeypatch):
    from workspace import WORKSPACES_ROOT, resolve_workspace_arg
    assert resolve_workspace_arg("acme") == (str((Path(WORKSPACES_ROOT) / "acme").resolve()), "acme")
    assert resolve_workspace_arg(None)[1] == ""
    assert resolve_workspace_arg("/workspace") == ("/workspace", "")
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    assert resolve_workspace_arg("/workspace") == ("/workspace", "acme")


def test_create_agent_takes_a_name_and_a_folder_beside_it(monkeypatch):
    from agents.agent_factory import AgentFactory
    from workspace import WORKSPACES_ROOT
    f = AgentFactory()
    built = []
    monkeypatch.setattr(f, "_build_agent",
                        lambda agent_id, workspace=None, workdir=None, **kw: built.append((workspace, workdir)) or object())
    with pytest.raises(ValueError, match="not a folder"):
        f.create_agent("ghost", workspace="/workspace")
    f.create_agent("ghost", workspace="acme")
    f.create_agent("ghost", workspace="acme", workdir="/workspace")
    assert built == [("acme", str(Path(WORKSPACES_ROOT) / "acme")), ("acme", "/workspace")]
    # Two folders of one workspace are two builds (the file tools differ).
    f.create_agent("ghost", workspace="acme", workdir="/workspace/other")
    assert len(built) == 3


def test_an_agent_in_a_container_keeps_the_runs_name(monkeypatch, tmp_path):
    """The case that broke: a container's agent at /workspace asked the hub
    about workspace "workspace". The agent, its model lookup and its file
    tools get the name it was built with, and only the tools get the folder."""
    from agents.agent_factory import AgentFactory
    from agents.registry import AgentSpec, add_agent
    from workspace import create_workspace_folder
    create_workspace_folder("acme")
    add_agent(AgentSpec(id="named_agent", name="Named", description="d", type="local",
                        entrypoint="m:f", tools=["read_file"]), user_edit=False)
    monkeypatch.setattr("agents.agent_factory.AgentFactory.load_definition",
                        lambda self, agent_id: {"id": agent_id, "name": "Named",
                                                "system_prompt": "You help.", "tools": ["read_file"],
                                                "provider": "openai", "model": "gpt-4o-mini"},
                        raising=False)
    seen = []
    real = AgentFactory._resolve_model_config
    monkeypatch.setattr(AgentFactory, "_resolve_model_config",
                        lambda self, config, workspace: seen.append(workspace) or real(self, config, workspace))
    agent = AgentFactory()._build_agent("named_agent", workspace="acme", workdir=str(tmp_path))
    assert seen and set(seen) == {"acme"}
    assert (agent.workspace, agent.workdir) == ("acme", str(tmp_path))
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    read = next(t for t in agent._tools if t.name == "read_file")
    assert "hi" in str(read.invoke({"path": "a.txt"}))


def test_a_container_is_always_told_its_workspace():
    from managers.container_manager import remote_container_env
    assert remote_container_env({"AGENT_WORKSPACE": ""})["AGENT_WORKSPACE"] == "default"
    assert remote_container_env({})["AGENT_WORKSPACE"] == "default"
    assert remote_container_env({"AGENT_WORKSPACE": "acme"})["AGENT_WORKSPACE"] == "acme"


def test_a_carrier_scope_ignores_its_folder(no_run, monkeypatch):
    from runtime.instance_run import InstanceLoop
    assert InstanceLoop("inst-1", "a", "/workspace").scope == "default"
    assert InstanceLoop("inst-1", "a", "acme").scope == "acme"
    monkeypatch.setenv("AGENT_WORKSPACE", "beta")
    assert InstanceLoop("inst-1", "a", "/workspace").scope == "beta"


def test_flow_nodes_read_the_runs_name_not_its_folder():
    from flow.entities.containers import _nested
    from flow.state import RunContext
    ctx = RunContext(workspace="/workspace", workspace_name="acme")
    assert _nested.workspace_name(ctx) == "acme"
    assert _nested.workspace_name(RunContext(workspace="/workspace")) is None


def test_a_flow_run_takes_its_launchers_name(no_run, monkeypatch):
    from flow.task_driver import run_workspace_name
    assert run_workspace_name({"workspace": "own"}) == "own"
    assert run_workspace_name({}) is None
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    assert run_workspace_name({"workspace": "own"}) == "acme"
