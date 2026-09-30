"""sandbox/: the base interface, provider registry, and the docker/local
providers. Docker itself is never needed: the docker path is exercised with
a mocked subprocess, exactly like tests/test_run_code.py did before this
package existed.
"""
from __future__ import annotations

import subprocess

import pytest

from sandbox import docker as docker_mod
from sandbox import local as local_mod
from sandbox import registry
from sandbox.base import SandboxNetwork, SandboxRequest, SandboxResult


# ── base ─────────────────────────────────────────────────────────────────────

def test_sandbox_result_ok_requires_exit_zero_and_no_error():
    assert SandboxResult(exit_code=0).ok
    assert not SandboxResult(exit_code=1).ok
    assert not SandboxResult(exit_code=0, error="boom").ok


def test_sandbox_network_defaults_to_none():
    assert SandboxRequest(language="python", code="").network.type == "none"


# ── registry ─────────────────────────────────────────────────────────────────

def test_get_provider_returns_the_same_instance_each_time():
    assert registry.get_provider("docker") is registry.get_provider("docker")
    assert registry.get_provider("local") is not registry.get_provider("docker")


def test_get_provider_refuses_an_unknown_name():
    with pytest.raises(KeyError):
        registry.get_provider("nope")


def test_available_reports_every_provider(monkeypatch):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    out = registry.available()
    assert set(out) == {"docker", "local", "e2b", "modal"}
    assert out["docker"]["available"] is True
    assert out["local"]["available"] is True
    # Neither SDK is installed in this checkout's environment.
    assert out["e2b"]["available"] is False and out["e2b"]["reason"]
    assert out["modal"]["available"] is False and out["modal"]["reason"]


def test_available_survives_a_provider_that_raises(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(registry.get_provider("docker"), "is_available", boom)
    out = registry.available()
    assert out["docker"]["available"] is False and "kaboom" in out["docker"]["reason"]


class _Env:
    def __init__(self, sandbox_provider="inherit"):
        self.sandbox_provider = sandbox_provider


def test_resolve_prefers_the_environments_own_choice(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_provider", "docker")
    assert registry.resolve(_Env("e2b"), settings) == "e2b"


def test_resolve_falls_back_to_the_setting_when_environment_inherits(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_provider", "modal")
    assert registry.resolve(_Env("inherit"), settings) == "modal"
    assert registry.resolve(None, settings) == "modal"


def test_resolve_falls_back_to_local_only_when_fallback_is_local(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_provider", "docker")
    monkeypatch.setattr(settings, "code_runner_fallback", "none")
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)
    assert registry.resolve(None, settings) == "docker"  # still docker: no opt-in

    monkeypatch.setattr(settings, "code_runner_fallback", "local")
    assert registry.resolve(None, settings) == "local"


def test_resolve_does_not_fall_back_when_docker_is_available(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_provider", "docker")
    monkeypatch.setattr(settings, "code_runner_fallback", "local")
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    assert registry.resolve(None, settings) == "docker"


# ── docker: command construction (pure) ─────────────────────────────────────

def _cmd(**overrides):
    args = dict(language="python", code_dir="/host/code", image="python:3.12-slim",
                name="agents-hub-code-x", memory="512m", cpus="1", pids_limit=128)
    args.update(overrides)
    return docker_mod.build_docker_command(**args)


def test_docker_command_defaults_to_network_none():
    cmd = _cmd()
    joined = " ".join(cmd)
    assert cmd[:3] == ["docker", "run", "--rm"]
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--memory 512m",
                 "--cpus 1", "--pids-limit 128", "--user 65534:65534",
                 "--security-opt no-new-privileges", "-v /host/code:/code:ro", "-w /code"):
        assert flag in joined, flag
    assert cmd[-3:] == ["python:3.12-slim", "python", "/code/main.py"]
    assert "/work" not in joined
    assert "-i" not in cmd


@pytest.mark.parametrize("language,tail", [
    ("node", ["node", "/code/main.js"]), ("bash", ["bash", "/code/main.sh"])])
def test_each_language_runs_its_interpreter(language, tail):
    assert _cmd(language=language, image="img")[-2:] == tail


def test_mount_flag_adds_the_workspace_read_only():
    assert "-v /host/ws/demo:/work:ro" in " ".join(_cmd(workspace_dir="/host/ws/demo"))


def test_stdin_makes_the_container_interactive():
    assert "-i" in _cmd(interactive=True)


def test_network_and_extra_env_are_threaded_through():
    cmd = _cmd(network="agents-hub-egress", extra_env={"HTTP_PROXY": "http://tok@gw:8099"})
    joined = " ".join(cmd)
    assert "--network agents-hub-egress" in joined
    assert "-e HTTP_PROXY=http://tok@gw:8099" in joined


def test_images_are_configurable_per_language():
    assert docker_mod.parse_images("") == docker_mod.DEFAULT_IMAGES
    assert docker_mod.parse_images("python=python:3.13-slim, node=node:22")["python"] == "python:3.13-slim"
    assert docker_mod.parse_images('{"bash": "bash:5.2"}')["bash"] == "bash:5.2"
    assert docker_mod.parse_images('{"ruby": "ruby:3"}') == docker_mod.DEFAULT_IMAGES


# ── docker: network resolution ──────────────────────────────────────────────

def test_resolve_network_none_and_unrestricted_need_no_egress():
    name, env, err = docker_mod._resolve_network(SandboxNetwork(type="none"), None)
    assert (name, env, err) == ("none", {}, "")
    name, env, err = docker_mod._resolve_network(SandboxNetwork(type="unrestricted"), None)
    assert (name, env, err) == ("bridge", {}, "")


def test_resolve_network_limited_refuses_when_the_proxy_is_off(monkeypatch):
    from environments import egress
    monkeypatch.setattr(egress, "enabled", lambda: False)
    name, env, err = docker_mod._resolve_network(SandboxNetwork(type="limited", hosts=["pypi.org"]), None)
    assert name == "none" and env == {} and "egress proxy" in err


def test_resolve_network_limited_refuses_when_the_gateway_cannot_start(monkeypatch):
    from environments import egress
    from managers import container_manager as cm
    monkeypatch.setattr(egress, "enabled", lambda: True)
    monkeypatch.setattr(cm, "ensure_egress_gateway", lambda: None)
    name, env, err = docker_mod._resolve_network(SandboxNetwork(type="limited", hosts=["pypi.org"]), None)
    assert name == "none" and "gateway" in err


def test_resolve_network_limited_registers_a_token_and_points_at_the_gateway(monkeypatch):
    from environments import egress
    from managers import container_manager as cm
    monkeypatch.setattr(egress, "enabled", lambda: True)
    monkeypatch.setattr(egress, "port", lambda: 8099)
    monkeypatch.setattr(egress, "register", lambda hosts, **kw: "tok-123")
    monkeypatch.setattr(cm, "ensure_egress_gateway", lambda: "agents-hub-egress-gateway")
    name, env, err = docker_mod._resolve_network(
        SandboxNetwork(type="limited", hosts=["pypi.org"]), "env-1")
    assert err == ""
    assert name == cm.EGRESS_NETWORK_NAME
    assert env["HTTP_PROXY"] == "http://tok-123@agents-hub-egress-gateway:8099"
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"]


# ── docker: run() ────────────────────────────────────────────────────────────

@pytest.fixture
def settings(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_images", "")
    monkeypatch.setattr(settings, "code_runner_memory", "512m")
    monkeypatch.setattr(settings, "code_runner_cpus", "1")
    monkeypatch.setattr(settings, "code_runner_pids_limit", 128)
    monkeypatch.setattr(settings, "code_runner_max_timeout", 300)
    return settings


def test_docker_provider_unsupported_language():
    result = docker_mod.DockerProvider().run(SandboxRequest(language="ruby", code="puts 1"))
    assert result.exit_code == -1 and "unsupported language" in result.error


def test_docker_provider_reports_unavailable(monkeypatch, settings):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)
    result = docker_mod.DockerProvider().run(SandboxRequest(language="python", code="1"))
    assert result.exit_code == -1 and "docker is not available" in result.error
    assert result.provider == "docker"


def test_docker_provider_runs_and_reports_the_provider(monkeypatch, settings):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    result = docker_mod.DockerProvider().run(
        SandboxRequest(language="python", code="print(42)", timeout=60))
    assert result.exit_code == 0 and "42" in result.stdout
    assert result.provider == "docker"
    assert "--network" in seen["cmd"] and "none" in seen["cmd"]


def test_docker_provider_limited_network_refuses_without_the_proxy(monkeypatch, settings):
    from environments import egress
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(egress, "enabled", lambda: False)
    monkeypatch.setattr(docker_mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not run when the network cannot be enforced"))
    result = docker_mod.DockerProvider().run(SandboxRequest(
        language="python", code="1", network=SandboxNetwork(type="limited", hosts=["pypi.org"])))
    assert result.exit_code == -1 and "egress proxy" in result.error


def test_docker_provider_timeout_removes_the_container(monkeypatch, settings):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    result = docker_mod.DockerProvider().run(SandboxRequest(language="bash", code="sleep 100", timeout=2))
    assert result.timed_out and "timed out after 2s" in result.error
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "-f", name]


def test_docker_provider_mounts_the_workspace_read_only(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod, "_host_path", lambda p: p)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    docker_mod.DockerProvider().run(SandboxRequest(
        language="python", code="print(1)", workspace=str(tmp_path)))
    assert f"{tmp_path}:/work:ro" in " ".join(seen["cmd"])


def test_docker_provider_writes_extra_files(monkeypatch, settings):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    written = {}

    def fake_run(cmd, **kw):
        code_dir = cmd[cmd.index("-v") + 1].split(":")[0]
        written["files"] = sorted(p.name for p in __import__("pathlib").Path(code_dir).iterdir())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    docker_mod.DockerProvider().run(SandboxRequest(
        language="python", code="print(1)", extra_files={"helper.py": "x = 1"}))
    assert written["files"] == ["helper.py", "main.py"]


def test_docker_provider_cleans_up_the_temp_dir(monkeypatch, settings):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    before = set(docker_mod._scratch_root().iterdir())
    docker_mod.DockerProvider().run(SandboxRequest(language="python", code="print(1)"))
    assert set(docker_mod._scratch_root().iterdir()) == before


# ── local ────────────────────────────────────────────────────────────────────

def test_local_provider_runs_a_python_one_liner(settings):
    result = local_mod.LocalProvider().run(SandboxRequest(
        language="python", code="import sys; print(sum(range(10))); print('e', file=sys.stderr)"))
    assert result.exit_code == 0 and result.stdout.strip() == "45" and result.stderr.strip() == "e"
    assert result.provider == "local"


def test_local_provider_reads_stdin_and_reports_failure(settings):
    result = local_mod.LocalProvider().run(SandboxRequest(
        language="python", code="import sys; print(sys.stdin.read().upper()); sys.exit(3)", stdin="hi"))
    assert result.exit_code == 3 and "HI" in result.stdout


def test_local_provider_does_not_see_secrets(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    result = local_mod.LocalProvider().run(SandboxRequest(
        language="python", code="import os; print(os.environ.get('OPENAI_API_KEY'))"))
    assert "sk-should-not-leak" not in result.stdout and "None" in result.stdout


def test_local_provider_times_out(settings):
    result = local_mod.LocalProvider().run(SandboxRequest(
        language="python", code="import time; time.sleep(10)", timeout=1))
    assert result.timed_out and "timed out after 1s" in result.error


def test_local_provider_refuses_a_missing_workspace_dir(settings, tmp_path):
    result = local_mod.LocalProvider().run(SandboxRequest(
        language="python", code="1", workspace=str(tmp_path / "nope")))
    assert result.exit_code == -1 and "not a directory" in result.error


def test_local_provider_workspace_is_writable_at_work(settings, tmp_path):
    result = local_mod.LocalProvider().run(SandboxRequest(
        language="python", code="import os; print(os.environ['WORK'])", workspace=str(tmp_path)))
    assert result.stdout.strip() == str(tmp_path)


def test_local_provider_no_interpreter(settings, monkeypatch):
    monkeypatch.setattr(local_mod.shutil, "which", lambda name: None)
    result = local_mod.LocalProvider().run(SandboxRequest(language="python", code="1"))
    assert "no python interpreter" in result.error


def test_local_provider_is_always_available():
    ok, reason = local_mod.LocalProvider().is_available()
    assert ok and reason == ""
