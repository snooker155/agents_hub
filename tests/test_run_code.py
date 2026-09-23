"""run_code (tools/run_code.py): the docker command line, the local fallback,
truncation, timeouts and the workspace mount. Docker itself is never needed:
the docker path is exercised with a mocked subprocess."""
from __future__ import annotations

import subprocess
import sys

import pytest

from tools import run_code as rc
from tools.capabilities import READS_PRIVATE, check_combination, grants_of, is_idempotent


@pytest.fixture
def settings(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_images", "")
    monkeypatch.setattr(settings, "code_runner_fallback", "none")
    monkeypatch.setattr(settings, "code_runner_max_timeout", 300)
    return settings


@pytest.fixture
def no_docker(monkeypatch):
    monkeypatch.setattr(rc, "docker_available", lambda: False)


@pytest.fixture
def local(settings, no_docker, monkeypatch):
    monkeypatch.setattr(settings, "code_runner_fallback", "local")
    return settings


def _run(**kw):
    return rc.run_code.invoke(kw)


# ── The docker command line ──────────────────────────────────────────────────

def _cmd(**overrides):
    args = dict(language="python", code_dir="/host/code", image="python:3.12-slim",
                name="agents-hub-code-x", memory="512m", cpus="1", pids_limit=128)
    args.update(overrides)
    return rc.build_docker_command(**args)


def test_docker_command_is_isolated():
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
    cmd = " ".join(_cmd(workspace_dir="/host/ws/demo"))
    assert "-v /host/ws/demo:/work:ro" in cmd


def test_stdin_makes_the_container_interactive():
    assert "-i" in _cmd(interactive=True)


def test_images_are_configurable_per_language():
    assert rc.parse_images("") == rc.DEFAULT_IMAGES
    assert rc.parse_images("python=python:3.13-slim, node=node:22")["python"] == "python:3.13-slim"
    assert rc.parse_images('{"bash": "bash:5.2"}')["bash"] == "bash:5.2"
    assert rc.parse_images('{"ruby": "ruby:3"}') == rc.DEFAULT_IMAGES


def test_the_docker_path_runs_the_built_command(settings, monkeypatch):
    monkeypatch.setattr(rc, "docker_available", lambda: True)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    out = _run(language="python", code="print(42)", stdin="in")
    assert "exit_code: 0" in out and "42" in out and "runtime: docker (python:3.12-slim)" in out
    assert "--network" in seen["cmd"] and "-i" in seen["cmd"]
    assert seen["kw"]["input"] == "in" and seen["kw"]["timeout"] == 60


def test_a_docker_timeout_removes_the_container(settings, monkeypatch):
    monkeypatch.setattr(rc, "docker_available", lambda: True)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    out = _run(language="bash", code="sleep 100", timeout=2)
    assert "timed out after 2s" in out
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "-f", name]


def test_mount_without_a_workspace_is_refused(settings, monkeypatch):
    monkeypatch.setattr(rc, "docker_available", lambda: True)
    monkeypatch.setattr(rc, "_workspace_dir", lambda: None)
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    out = _run(language="python", code="print(1)", mount_workspace=True)
    assert "no workspace directory" in out


def test_mount_with_a_workspace_passes_it(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "docker_available", lambda: True)
    monkeypatch.setattr(rc, "_workspace_dir", lambda: str(tmp_path))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rc.subprocess, "run", fake_run)
    _run(language="python", code="print(1)", mount_workspace=True)
    assert f"{tmp_path.resolve()}:/work:ro" in seen["cmd"]


# ── Without docker ───────────────────────────────────────────────────────────

def test_without_docker_and_without_fallback_it_explains(settings, no_docker):
    out = _run(language="python", code="print(1)")
    assert "exit_code: -1" in out and "CODE_RUNNER_FALLBACK=local" in out


def test_local_fallback_runs_a_python_one_liner(local):
    out = _run(language="python", code="import sys; print(sum(range(10))); print('e', file=sys.stderr)")
    assert "exit_code: 0" in out
    assert "stdout:\n45" in out and "stderr:\ne" in out
    assert "runtime: local" in out
    assert "duration_ms:" in out


def test_local_fallback_reads_stdin_and_reports_failure(local):
    out = _run(language="python", code="import sys; print(sys.stdin.read().upper()); sys.exit(3)",
               stdin="hi")
    assert "exit_code: 3" in out and "HI" in out


def test_local_fallback_does_not_see_secrets(local, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    out = _run(language="python", code="import os; print(os.environ.get('OPENAI_API_KEY'))")
    assert "sk-should-not-leak" not in out and "None" in out


def test_local_fallback_times_out(local):
    out = _run(language="python", code="import time; time.sleep(10)", timeout=1)
    assert "timed out after 1s" in out and "exit_code: -1" in out


def test_timeout_is_capped(local, monkeypatch):
    monkeypatch.setattr(local, "code_runner_max_timeout", 5)
    seen = {}

    def fake_local(language, code_dir, timeout, stdin, mount):
        seen["timeout"] = timeout
        return "ok"

    monkeypatch.setattr(rc, "_run_local", fake_local)
    _run(language="python", code="1", timeout=999)
    assert seen["timeout"] == 5


def test_output_is_truncated(local):
    out = _run(language="python", code=f"print('x' * {rc._MAX_OUTPUT + 500})")
    assert "...[truncated]" in out
    assert len(out) < rc._MAX_OUTPUT + 500


def test_the_temp_dir_is_removed(local):
    before = set(rc._scratch_root().iterdir())
    _run(language="python", code="print(1)")
    assert set(rc._scratch_root().iterdir()) == before


def test_unsupported_language(settings):
    # The schema refuses it first; the function body refuses it too.
    assert "unsupported language" in rc.run_code.func(language="ruby", code="puts 1")


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_local_fallback_runs_bash(local):
    out = _run(language="bash", code="echo $((6*7))")
    assert "stdout:\n42" in out


# ── Registration and classification ──────────────────────────────────────────

def test_run_code_in_a_container_only_reads_private(settings):
    """No network in the container: nothing untrusted comes in, nothing goes
    out. The workspace mount is the one thing it can read."""
    assert grants_of("run_code") == frozenset({READS_PRIVATE})
    assert check_combination(["run_code"]) is None
    assert not is_idempotent("run_code")


def test_run_code_with_the_local_fallback_is_run_shell(settings, monkeypatch):
    monkeypatch.setattr(settings, "code_runner_fallback", "local")
    assert grants_of("run_code") == grants_of("run_shell")
    assert check_combination(["run_code"]).rule_id == "lethal_trifecta"


def test_run_code_grant_fails_closed(monkeypatch):
    import common.config
    monkeypatch.delattr(common.config, "settings")
    assert grants_of("run_code") == grants_of("run_shell")


def test_run_code_is_in_the_execution_category():
    from tools.registry import get_tool_by_id
    spec = get_tool_by_id("run_code")
    assert spec is not None and spec.category == "execution"
    assert {p["name"] for p in spec.parameters} >= {"language", "code", "timeout", "stdin", "mount_workspace"}
