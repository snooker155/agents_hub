"""One rule for the execution mode: the workspace's own ``settings.agent_mode``
first, then the global setting. Task runs, instances, teams and run_shell all
ask common.config.agent_execution_mode, so a workspace switched to docker is
docker for each of them (teams used to read a key nothing ever wrote)."""
import pytest

from common.config import agent_execution_mode


@pytest.fixture
def ws_settings(monkeypatch):
    stored = {}
    monkeypatch.setattr("workspace.get_workspace_metadata",
                        lambda name: {"settings": stored.get(name, {})})
    return stored


@pytest.fixture
def global_local(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "agent_mode", "local")


def test_workspace_override_wins(ws_settings, global_local):
    ws_settings["w"] = {"agent_mode": "docker"}
    assert agent_execution_mode("w") == "docker"
    assert agent_execution_mode() == "local"


def test_no_override_falls_back_to_the_global_mode(ws_settings, global_local):
    assert agent_execution_mode("w") == "local"


def test_an_unknown_value_reads_as_local(ws_settings, global_local):
    ws_settings["w"] = {"agent_mode": "podman"}
    assert agent_execution_mode("w") == "local"


def test_every_launcher_sees_the_same_mode(ws_settings, global_local, monkeypatch):
    ws_settings["w"] = {"agent_mode": "docker"}
    monkeypatch.setattr("managers.container_manager.db_shared_with_containers", lambda: True)
    from instances.carrier import _execution_mode
    from runtime.entity_launch import execution_mode_for
    assert execution_mode_for("w") == "docker"  # teams
    assert _execution_mode("w") == "docker"      # instances


def test_run_shell_follows_the_active_workspace(ws_settings, global_local, monkeypatch):
    from tools import shell
    monkeypatch.delenv("AGENTS_HUB_RUN_NETWORK", raising=False)
    monkeypatch.setattr("common.workspace_context.resolve_active_workspace", lambda *a: "w")
    ws_settings["w"] = {"agent_mode": "docker"}
    assert shell._shell_in_container() is True
