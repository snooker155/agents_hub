"""Flows and loops follow the workspace's execution mode like teams do: in
docker mode the whole run (its agent nodes with it) lives in one run
container, mounted on the workspace folder and never on the project root.
Inside an agent container the mode is always local, so a nested team, loop or
flow it starts runs there as a subprocess instead of trying to start docker.
"""
from pathlib import Path

import pytest

from common.paths import PROJECT_ROOT

_FLOW = {"id": "f1", "name": "F", "nodes": [{"id": "n1", "data": {"agent_id": "a", "category": "agent"}}],
         "edges": []}


@pytest.fixture
def docker_mode(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_RUN_NETWORK", raising=False)
    monkeypatch.setattr("runtime.entity_launch.execution_mode_for", lambda ws: "docker")


# ── flows ────────────────────────────────────────────────────────────────────

@pytest.fixture
def flow_specs(monkeypatch):
    from flow import launcher
    monkeypatch.setattr("flow.store.get_flow", lambda fid: _FLOW if fid == "f1" else None)
    specs: list = []
    monkeypatch.setattr(launcher, "_dispatch", lambda spec: specs.append(spec))
    return specs


def test_a_flow_in_docker_mode_runs_in_a_container_on_its_workspace(docker_mode, flow_specs):
    from flow.launcher import start_flow_run
    start_flow_run(None, "f1", {"workspace": "dock-ws"})
    spec = flow_specs[-1]
    assert spec["execution_mode"] == "docker"
    ws_path = Path(spec["ws_path"]).resolve()
    assert ws_path != Path(PROJECT_ROOT).resolve()
    # The same folder the flow process is told to work in.
    assert str(ws_path) == str(Path(spec["cli_args"][spec["cli_args"].index("--workspace") + 1]).resolve())


def test_a_flow_in_local_mode_starts_from_the_project_root(monkeypatch, flow_specs):
    monkeypatch.setattr("runtime.entity_launch.execution_mode_for", lambda ws: "local")
    from flow.launcher import start_flow_run
    start_flow_run(None, "f1", {"workspace": "dock-ws"})
    spec = flow_specs[-1]
    assert spec["execution_mode"] == "local"
    assert spec["ws_path"] == str(PROJECT_ROOT)


# ── loops ────────────────────────────────────────────────────────────────────

def test_a_loop_in_docker_mode_runs_in_a_container(docker_mode, monkeypatch):
    from loops import store
    from loops.launcher import start_loop_run
    from loops.models import Loop
    monkeypatch.setattr("flow.store.get_flow", lambda fid: _FLOW if fid == "f1" else None)
    specs: list = []
    monkeypatch.setattr("loops.launcher.launch_prepared", lambda spec: specs.append(spec))
    loop = store.save_loop(Loop(name="L", flow_id="f1", exit_criterion="done"))
    start_loop_run(loop.loop_id, "go")
    assert specs[-1]["execution_mode"] == "docker"
    assert Path(specs[-1]["ws_path"]).is_dir()


# ── the mount and the mode inside a container ────────────────────────────────

def test_the_project_root_is_never_mounted(tmp_path):
    from runtime.entity_launch import _mount_dir
    assert _mount_dir(str(PROJECT_ROOT)) is None
    assert _mount_dir(str(Path(PROJECT_ROOT).parent)) is None
    assert _mount_dir("") is None
    assert _mount_dir(str(tmp_path / "missing")) is None
    assert _mount_dir(str(tmp_path)) == str(tmp_path.resolve())


def test_inside_an_agent_container_the_mode_is_local(monkeypatch):
    from common.config import agent_execution_mode, settings
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": {"agent_mode": "docker"}})
    assert agent_execution_mode("w") == "docker"
    monkeypatch.setenv("AGENTS_HUB_RUN_NETWORK", "agents-hub")
    assert agent_execution_mode("w") == "local"
    assert agent_execution_mode() == "local"
