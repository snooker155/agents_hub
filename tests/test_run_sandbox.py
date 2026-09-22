"""Task-run sandboxing: Docker wiring in agent_launcher, the hardened run
container profile in container_manager, and container-aware stop/liveness.

Defect being closed: AGENT_EXECUTION_MODE=docker was documented as covering
task runs but runtime.docker_runner.start_run_container had no callers — every
task run went through subprocess.Popen regardless of the setting. These tests
pin the fix (agent_launcher resolves the mode and calls the container path)
and the sandbox it launches into (managers.container_manager.build_run_command
/ container_env), without needing a real Docker daemon anywhere.
"""
from __future__ import annotations

import pytest

from common import config
from managers import container_manager as cm
from managers import run_manager as rm
from managers import run_watchdog
from tasks import service as ts
from tasks.models import TaskStatus


# ── shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fake_agent_registry(monkeypatch):
    """agent_launcher.start_run only checks get_agent(agent_id) truthily
    before launching — stub it so these tests don't need a real agents.json
    seeded in the throwaway test state root (nothing else in this suite
    bootstraps one; production runs it through common.bootstrap at startup)."""
    import agents.registry as registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: object())


@pytest.fixture
def dot_env(monkeypatch):
    """Same trick as test_execution_mode_setting.py: control the live resolver
    without touching the real .env, so the mode is deterministic regardless of
    what this checkout's .env happens to hold."""
    state = {}
    monkeypatch.setattr(config, "read_dot_env", lambda: dict(state))
    monkeypatch.delenv("AGENT_EXECUTION_MODE", raising=False)
    return state


@pytest.fixture
def no_popen(monkeypatch):
    """Fail loudly if the local subprocess path is ever reached.

    Used by the Docker-mode tests: a real ``python runtime/agent_run.py``
    spawn here would actually try to run an agent against this test's fake
    task, so any accidental fall-through to the local branch must be an
    immediate, obvious test failure rather than a stray background process.
    """
    import agents.agent_launcher as launcher

    def _boom(*a, **k):
        raise AssertionError("subprocess.Popen must not be called in Docker mode")

    monkeypatch.setattr(launcher.subprocess, "Popen", _boom)


class _FakeProc:
    pid = 4242


@pytest.fixture
def fake_popen(monkeypatch):
    """Stand in for subprocess.Popen in local-mode tests: records the call,
    returns a fake handle with a pid, never spawns a real process."""
    import agents.agent_launcher as launcher

    calls = []

    def _fake(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProc()

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake)
    return calls


# ── managers.container_manager.container_env ────────────────────────────────

def test_container_env_drops_host_only_vars_and_keeps_provider_keys():
    env = {
        "OPENAI_API_KEY": "sk-test",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "AGENTS_HUB_API_TOKEN": "relay-token",
        "AGENT_SESSION_ID": "sess-1",
        "HOST_PROJECT_ROOT": "/Users/dev/agents_hub",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
        "HOME": "/Users/dev",
        "PATH": "/usr/bin:/bin",
        "npm_config_registry": "https://registry.npmjs.org/",
        "VIRTUAL_ENV": "/opt/venv",
        "VIRTUAL_ENV_PROMPT": "(venv)",
        "CONDA_PREFIX": "/opt/conda",
    }
    out = cm.container_env(env)

    # Kept: provider keys, the relay token, and run-specific bookkeeping.
    assert out["OPENAI_API_KEY"] == "sk-test"
    assert out["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert out["AGENTS_HUB_API_TOKEN"] == "relay-token"
    assert out["AGENT_SESSION_ID"] == "sess-1"

    # Dropped: everything that only describes this host.
    for host_only in (
        "HOST_PROJECT_ROOT", "DOCKER_HOST", "SSH_AUTH_SOCK", "HOME", "PATH",
        "npm_config_registry", "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "CONDA_PREFIX",
    ):
        assert host_only not in out


# ── managers.container_manager.build_run_command ────────────────────────────

@pytest.fixture
def no_host_translation(monkeypatch):
    """Deterministic _host_path: no compose-style rebasing in play."""
    monkeypatch.delenv("HOST_PROJECT_ROOT", raising=False)


def _run_cmd(**overrides):
    kwargs = dict(
        container_name="agents-hub-run-abc123",
        agent_id="swe_agent",
        image="agents-hub/base:latest",
        network="agents-hub",
        translated_cmd=["python", "-m", "runtime.agent_run", "swe_agent",
                         "--workspace", "/workspace", "--task-id", "t1", "--run-id", "r1"],
        state_dir="/host/.agents_hub",
        tasks_dir="/host/tasks",
        workspace="/host/workspaces/default",
        env={"OPENAI_API_KEY": "sk-test", "HOST_PROJECT_ROOT": "/host"},
        memory="1g",
        cpus="1",
        pids_limit=256,
        agents_file="/host/.agents_hub/agents.json",
        custom_providers_file="/host/.agents_hub/custom_providers.json",
    )
    kwargs.update(overrides)
    return cm.build_run_command(**kwargs)


def test_run_command_is_hardened(no_host_translation):
    argv = _run_cmd()

    assert "--cap-drop" in argv and "ALL" in argv
    assert "--security-opt" in argv and "no-new-privileges" in argv
    assert "--read-only" in argv
    assert "--tmpfs" in argv and "/tmp" in argv
    assert "--memory" in argv and "1g" in argv
    assert "--cpus" in argv and "1" in argv
    assert "--pids-limit" in argv and "256" in argv


def test_run_command_never_mounts_the_docker_socket(no_host_translation):
    argv = _run_cmd()
    assert "/var/run/docker.sock" not in " ".join(argv)


def test_run_command_pins_agents_json_read_only(no_host_translation):
    argv = _run_cmd()
    joined = " ".join(argv)
    assert "/host/.agents_hub/agents.json:/app/.agents_hub/agents.json:ro" in joined
    assert "/host/.agents_hub/custom_providers.json:/app/.agents_hub/custom_providers.json:ro" in joined


def test_run_command_scrubs_env_but_keeps_provider_keys(no_host_translation):
    argv = _run_cmd()
    joined = " ".join(argv)
    assert "-e OPENAI_API_KEY=sk-test" in joined
    assert "HOST_PROJECT_ROOT" not in joined


def test_run_command_omits_ro_mounts_when_files_absent(no_host_translation):
    argv = _run_cmd(agents_file=None, custom_providers_file=None)
    assert "agents.json" not in " ".join(argv)


def test_run_command_default_transport_mounts_state_dir_read_write(no_host_translation):
    """AGENT_RUN_STATE_TRANSPORT unset (or "db"): unchanged from before the
    http transport existed, the state dir mounts read-write."""
    argv = _run_cmd()
    assert "/host/.agents_hub:/app/.agents_hub" in argv
    assert "/host/.agents_hub:/app/.agents_hub:ro" not in argv
    assert "run_logs" not in " ".join(argv)


def test_run_command_http_transport_mounts_state_dir_read_only(no_host_translation):
    """AGENT_RUN_STATE_TRANSPORT=http: the state dir goes :ro wholesale, with
    run_logs/ re-mounted :rw on top for the run's own log file. See
    docs/containers.md and common/state_transport.py."""
    argv = _run_cmd(env={
        "OPENAI_API_KEY": "sk-test",
        "HOST_PROJECT_ROOT": "/host",
        "AGENT_RUN_STATE_TRANSPORT": "http",
    })
    joined = " ".join(argv)
    assert "/host/.agents_hub:/app/.agents_hub:ro" in joined
    assert "/host/.agents_hub/run_logs:/app/.agents_hub/run_logs" in joined
    assert "/host/.agents_hub/run_logs:/app/.agents_hub/run_logs:ro" not in joined
    # The read-only overlays for agents.json / custom_providers.json still apply.
    assert "/host/.agents_hub/agents.json:/app/.agents_hub/agents.json:ro" in joined
    # The relay env var itself still reaches the container.
    assert "-e AGENT_RUN_STATE_TRANSPORT=http" in joined


# ── agents.agent_launcher.start_run: mode resolution + Docker wiring ────────

def test_local_mode_still_calls_popen(dot_env, fake_popen):
    dot_env["AGENT_EXECUTION_MODE"] = "local"
    from agents import agent_launcher

    t = ts.create_task("local run")
    run_id, _session_id = agent_launcher.start_run(str(t.id), "swe_agent", None)

    assert fake_popen, "local mode must still spawn a subprocess"
    rec = rm.get_run_by_id(run_id)
    assert rec["pid"] == 4242
    assert rec["status"] == "running"
    assert not rec.get("container_name")


def test_docker_mode_spawns_no_subprocess_and_records_container(dot_env, no_popen, monkeypatch):
    dot_env["AGENT_EXECUTION_MODE"] = "docker"
    from agents import agent_launcher
    from runtime import docker_runner

    calls = []

    def _fake_start_run_container(run_id, agent_id, inner_cmd, cwd=None, env=None):
        container_name = f"agents-hub-run-{run_id.replace('-', '')[:12]}"
        calls.append({"run_id": run_id, "agent_id": agent_id, "inner_cmd": inner_cmd,
                      "cwd": cwd, "env": env, "container_name": container_name})
        return {
            "success": True,
            "container_id": "deadbeefcafe",
            "container_name": container_name,
            "image": "agents-hub/base:latest",
            "error": None,
        }

    monkeypatch.setattr(docker_runner, "start_run_container", _fake_start_run_container)

    t = ts.create_task("docker run")
    run_id, _session_id = agent_launcher.start_run(str(t.id), "swe_agent", None)

    assert len(calls) == 1
    inner_cmd = calls[0]["inner_cmd"]
    # Module form, not a script path — a host script path would not resolve
    # to anything inside the container (see node_manager.start_node).
    assert inner_cmd[1:3] == ["-m", "runtime.agent_run"]
    assert "swe_agent" in inner_cmd

    rec = rm.get_run_by_id(run_id)
    assert rec["execution_mode"] == "docker"
    assert rec["container_name"] == calls[0]["container_name"]
    assert rec["status"] == "running"
    assert not rec.get("pid")


def test_docker_start_failure_closes_the_run_as_failed(dot_env, no_popen, monkeypatch):
    dot_env["AGENT_EXECUTION_MODE"] = "docker"
    from agents import agent_launcher
    from runtime import docker_runner

    def _fake_start_run_container(run_id, agent_id, inner_cmd, cwd=None, env=None):
        return {"success": False, "container_id": None, "container_name": None,
                "image": "agents-hub/base:latest", "error": "no such image"}

    monkeypatch.setattr(docker_runner, "start_run_container", _fake_start_run_container)

    t = ts.create_task("doomed docker run")
    run_id, _session_id = agent_launcher.start_run(str(t.id), "swe_agent", None)

    rec = rm.get_run_by_id(run_id)
    assert rec["status"] == "failed"
    assert "no such image" in (rec.get("error") or "")

    # The task must not be left stuck in_progress with no agent working it.
    done_task = ts.get_task(t.id)
    assert done_task.status == TaskStatus.blocked


# ── stop / liveness: container_name, not execution_mode, is the signal ─────

def test_stop_run_by_id_stops_the_container_even_if_execution_mode_reset(monkeypatch):
    """Regression guard for the clobber: the container's own agent_run.py
    calls open_run() on startup, which unconditionally records its own
    execution_mode ("local", forced so it never nests containers) — so by the
    time an operator hits Stop, execution_mode may already read "local" again
    while container_name (never touched after launch) still says otherwise."""
    t = ts.create_task("stop me")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    rm.update_run(rid, {"container_name": "agents-hub-run-abc123", "execution_mode": "local"})

    stopped = {}

    def _fake_stop_container(name):
        stopped["name"] = name
        return True

    monkeypatch.setattr(cm, "stop_container", _fake_stop_container)

    assert rm.stop_run_by_id(rid) is True
    assert stopped["name"] == "agents-hub-run-abc123"
    assert rm.get_run_by_id(rid)["status"] == "stop"


def test_watchdog_uses_container_running_not_pid(monkeypatch):
    t = ts.create_task("containerized work")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    # A pid that certainly does not exist — the container branch must never
    # consult it once container_name is set.
    rm.update_run(rid, {"container_name": "agents-hub-run-abc123", "pid": 999999999})

    monkeypatch.setattr(cm, "container_running", lambda name: True)
    closed = run_watchdog.sweep_once()
    assert rm.get_run_by_id(rid)["status"] == "running"

    monkeypatch.setattr(cm, "container_running", lambda name: False)
    closed = run_watchdog.sweep_once()
    assert closed >= 1
    assert rm.get_run_by_id(rid)["status"] == "failed"
