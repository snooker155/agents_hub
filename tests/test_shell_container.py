"""run_shell outside an agent container while the execution mode is docker:
the command goes into a throwaway container instead of the hub's own host.

A chat turn runs in the hub process whatever the execution mode says, so
without this the docker mode fenced background runs only. The container
manager also stamps every agent container with the network it was given
(AGENTS_HUB_RUN_NETWORK), which is how run_shell and the capability guard
tell "inside a container" from "configured for one".
"""
import subprocess

import pytest

from managers import container_manager as cm
from tools import shell
from tools.shell import run_shell


def _docker_mode(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "agent_mode", "docker")
    monkeypatch.delenv("AGENTS_HUB_RUN_NETWORK", raising=False)


# ── where the command runs ───────────────────────────────────────────────────

def test_local_mode_runs_on_the_host(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "agent_mode", "local")
    monkeypatch.delenv("AGENTS_HUB_RUN_NETWORK", raising=False)
    assert shell._shell_in_container() is False


def test_docker_mode_on_the_hub_goes_into_a_container(monkeypatch):
    _docker_mode(monkeypatch)
    assert shell._shell_in_container() is True


def test_inside_an_agent_container_the_command_runs_there(monkeypatch):
    _docker_mode(monkeypatch)
    monkeypatch.setenv("AGENTS_HUB_RUN_NETWORK", "agents-hub")
    assert shell._shell_in_container() is False


def test_run_shell_in_docker_mode_runs_the_container_argv(monkeypatch, tmp_path):
    _docker_mode(monkeypatch)
    monkeypatch.setattr(shell, "_resolve_shell_cwd", lambda: str(tmp_path))
    monkeypatch.setattr("sandbox.docker.docker_available", lambda: True)
    monkeypatch.setattr(cm, "image_tag_for_agent", lambda agent_id: "agents-hub/base:latest")
    monkeypatch.setattr(cm, "agent_network", lambda: "agents-hub")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="hi\n", stderr="")

    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    out = run_shell.invoke({"command": "echo hi", "timeout": 5})

    assert "exit_code: 0" in out and "hi" in out
    argv = seen["argv"]
    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[-3:] == ["sh", "-c", "echo hi"]
    assert f"{tmp_path}:{cm.CONTAINER_WORKSPACE_DIR}" in argv
    # Nothing of the hub's own environment reaches the container.
    assert "env" not in seen["kwargs"] and "shell" not in seen["kwargs"]


def test_run_shell_refuses_when_docker_is_down(monkeypatch):
    _docker_mode(monkeypatch)
    monkeypatch.setattr("sandbox.docker.docker_available", lambda: False)

    def must_not_run(*a, **k):
        raise AssertionError("ran a command although docker is down")

    monkeypatch.setattr(shell.subprocess, "run", must_not_run)
    out = run_shell.invoke({"command": "echo hi", "timeout": 5})
    assert "exit_code: -1" in out and "not run" in out


def test_run_shell_reports_a_container_that_did_not_start(monkeypatch, tmp_path):
    _docker_mode(monkeypatch)
    monkeypatch.setattr(shell, "_resolve_shell_cwd", lambda: str(tmp_path))
    monkeypatch.setattr("sandbox.docker.docker_available", lambda: True)
    monkeypatch.setattr(cm, "image_tag_for_agent", lambda agent_id: "agents-hub/base:latest")
    monkeypatch.setattr(cm, "agent_network", lambda: "agents-hub")
    monkeypatch.setattr(shell.subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 125, stdout="", stderr="Unable to find image"))
    out = run_shell.invoke({"command": "echo hi", "timeout": 5})
    assert "exit_code: -1" in out and "did not start" in out and "Unable to find image" in out


# ── the container's shape ────────────────────────────────────────────────────

def _shell_argv(network: str):
    return cm.build_shell_command(container_name="c", agent_id="a", image="img",
                                  network=network, workspace="/w", command="ls")


def test_shell_container_is_fenced_like_a_run():
    argv = _shell_argv("agents-hub")
    for flag in ("--read-only", "--cap-drop", "--security-opt", "--memory", "--pids-limit"):
        assert flag in argv
    assert argv[argv.index("--network") + 1] == "agents-hub"
    assert f"{cm.RUN_NETWORK_ENV}=agents-hub" in argv
    # No state directory: the command sees its own files only.
    assert not any(cm.CONTAINER_STATE_DIR in a for a in argv)


def test_shell_container_without_network_has_no_host_alias():
    argv = _shell_argv("none")
    assert argv[argv.index("--network") + 1] == "none"
    assert "--add-host" not in argv


# ── the network agent containers get, and the stamp ─────────────────────────

def test_agent_network_honours_the_setting(monkeypatch):
    monkeypatch.setenv("AGENT_DOCKER_NETWORK", "none")
    monkeypatch.setattr(cm, "get_or_create_network", lambda: pytest.fail("bridge created"))
    assert cm.agent_network() == "none"


def test_agent_network_defaults_to_the_bridge(monkeypatch):
    monkeypatch.delenv("AGENT_DOCKER_NETWORK", raising=False)
    monkeypatch.setattr(cm, "get_or_create_network", lambda: cm.NETWORK_NAME)
    assert cm.agent_network() == cm.NETWORK_NAME


def test_run_container_is_stamped_with_its_network():
    argv = cm.build_run_command(
        container_name="c", agent_id="a", image="img", network="none",
        translated_cmd=["python", "-m", "runtime.agent_run"], state_dir="/s", tasks_dir="/t",
    )
    assert f"{cm.RUN_NETWORK_ENV}=none" in argv
