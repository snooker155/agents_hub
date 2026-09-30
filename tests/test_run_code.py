"""run_code (tools/run_code.py): routing to a sandbox provider
(sandbox/registry.py), the local fallback, truncation, timeouts, the
workspace mount and the tool's registration/classification. The docker
command line itself and the providers' own behaviour are covered in
tests/test_sandbox*.py; this file is about run_code's own wiring: which
provider a run gets and what its output says.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from sandbox import docker as docker_mod
from tools import run_code as rc
from tools.capabilities import READS_PRIVATE, check_combination, grants_of, is_idempotent


@pytest.fixture
def settings(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "code_runner_images", "")
    monkeypatch.setattr(settings, "code_runner_fallback", "none")
    monkeypatch.setattr(settings, "code_runner_max_timeout", 300)
    monkeypatch.setattr(settings, "code_runner_provider", "docker")
    return settings


@pytest.fixture
def no_docker(monkeypatch):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)


@pytest.fixture
def local(settings, no_docker, monkeypatch):
    monkeypatch.setattr(settings, "code_runner_fallback", "local")
    return settings


@pytest.fixture(autouse=True)
def _no_environment(monkeypatch):
    """None of these tests run inside an agent's environment fence unless a
    test says otherwise."""
    monkeypatch.delenv("AGENTS_HUB_ENVIRONMENT_ID", raising=False)
    monkeypatch.delenv("AGENTS_HUB_NETWORK", raising=False)
    monkeypatch.delenv("AGENTS_HUB_ALLOWED_HOSTS", raising=False)


def _run(**kw):
    return rc.run_code.invoke(kw)


# ── Routing through the registry ─────────────────────────────────────────────

def test_the_docker_path_runs_and_names_the_provider(settings, monkeypatch):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    out = _run(language="python", code="print(42)", stdin="in")
    assert "exit_code: 0" in out and "42" in out and "runtime: docker" in out
    assert "--network" in seen["cmd"] and "-i" in seen["cmd"]
    assert seen["kw"]["input"] == "in" and seen["kw"]["timeout"] == 60


def test_a_docker_timeout_removes_the_container(settings, monkeypatch):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    out = _run(language="bash", code="sleep 100", timeout=2)
    assert "timed out after 2s" in out
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[1] == ["docker", "rm", "-f", name]


def test_mount_without_a_workspace_is_refused(settings, monkeypatch):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(rc, "_workspace_dir", lambda: None)
    monkeypatch.setattr(docker_mod.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    out = _run(language="python", code="print(1)", mount_workspace=True)
    assert "no workspace directory" in out


def test_mount_with_a_workspace_passes_it(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(rc, "_workspace_dir", lambda: str(tmp_path))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    _run(language="python", code="print(1)", mount_workspace=True)
    assert f"{tmp_path.resolve()}:/work:ro" in seen["cmd"]


def test_a_limited_environment_relaxes_the_network(settings, monkeypatch):
    """AGENTS_HUB_NETWORK=limited (set by environments/launch.py for the run
    this tool executes inside) reaches the docker sandbox as a real
    allowlisted network instead of the blanket "none" default."""
    from environments import egress
    from managers import container_manager as cm

    monkeypatch.setenv("AGENTS_HUB_NETWORK", "limited")
    monkeypatch.setenv("AGENTS_HUB_ALLOWED_HOSTS", "pypi.org, github.com")
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(egress, "enabled", lambda: True)
    monkeypatch.setattr(egress, "port", lambda: 8099)
    monkeypatch.setattr(egress, "register", lambda hosts, **kw: "tok-xyz")
    monkeypatch.setattr(cm, "ensure_egress_gateway", lambda: "agents-hub-egress-gateway")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    out = _run(language="python", code="print(1)")
    assert "exit_code: 0" in out
    joined = " ".join(seen["cmd"])
    assert "--network agents-hub-egress" in joined
    assert "HTTP_PROXY=http://tok-xyz@agents-hub-egress-gateway:8099" in joined


def test_no_environment_keeps_network_none(settings, monkeypatch):
    """Unchanged default: no environment (or one that never set
    AGENTS_HUB_NETWORK) still gets the historical no-network sandbox."""
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)
    _run(language="python", code="print(1)")
    assert "--network none" in " ".join(seen["cmd"])


def test_an_environments_sandbox_provider_is_honoured(settings, monkeypatch):
    from environments.models import Environment

    monkeypatch.setenv("AGENTS_HUB_ENVIRONMENT_ID", "env-1")
    env = Environment(name="e2b-env", sandbox_provider="e2b")
    monkeypatch.setattr("environments.service.get_environment", lambda eid: env)

    class _Fake:
        name = "e2b"

        def is_available(self):
            return True, ""

        def run(self, request):
            from sandbox.base import SandboxResult
            return SandboxResult(exit_code=0, stdout="from e2b\n", provider="e2b")

    from sandbox import registry
    monkeypatch.setattr(registry, "get_provider", lambda name: _Fake() if name == "e2b" else pytest.fail(name))
    out = _run(language="python", code="print(1)")
    assert "runtime: e2b" in out and "from e2b" in out


def test_an_unknown_environment_id_fails_open_to_the_default(settings, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_ENVIRONMENT_ID", "gone")
    monkeypatch.setattr("environments.service.get_environment",
                        lambda eid: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "ok\n", ""))
    out = _run(language="python", code="print(1)")
    assert "runtime: docker" in out and "exit_code: 0" in out


# ── Images, parsing (re-exported from sandbox.docker) ───────────────────────

def test_images_are_configurable_per_language():
    assert rc.parse_images("") == rc.DEFAULT_IMAGES
    assert rc.parse_images("python=python:3.13-slim, node=node:22")["python"] == "python:3.13-slim"
    assert rc.parse_images('{"bash": "bash:5.2"}')["bash"] == "bash:5.2"
    assert rc.parse_images('{"ruby": "ruby:3"}') == rc.DEFAULT_IMAGES


def test_docker_available_is_reexported_for_doctor_and_system_workspace():
    # common/doctor.py and common/system_workspace.py both do
    # `from tools.run_code import docker_available`; it must keep resolving.
    assert rc.docker_available is docker_mod.docker_available


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

    from sandbox.base import SandboxResult

    class _Fake:
        name = "local"

        def is_available(self):
            return True, ""

        def run(self, request):
            seen["timeout"] = request.timeout
            return SandboxResult(exit_code=0, provider="local")

    from sandbox import registry
    monkeypatch.setattr(registry, "get_provider", lambda name: _Fake())
    _run(language="python", code="1", timeout=999)
    assert seen["timeout"] == 5


def test_output_is_truncated(local):
    out = _run(language="python", code=f"print('x' * {rc._MAX_OUTPUT + 500})")
    assert "...[truncated]" in out
    assert len(out) < rc._MAX_OUTPUT + 500


def test_the_temp_dir_is_removed(local):
    before = set(docker_mod._scratch_root().iterdir())
    _run(language="python", code="print(1)")
    assert set(docker_mod._scratch_root().iterdir()) == before


def test_unsupported_language(settings):
    # The schema refuses it first; the function body refuses it too.
    assert "unsupported language" in rc.run_code.func(language="ruby", code="puts 1")


@pytest.mark.skipif(sys.platform == "win32", reason="bash")
def test_local_fallback_runs_bash(local):
    out = _run(language="bash", code="echo $((6*7))")
    assert "stdout:\n42" in out


# ── run_snippet (shared with the code view) ──────────────────────────────────

def test_run_snippet_reports_the_provider(local):
    result = rc.run_snippet("python", "print(1)")
    assert result["ok"] is True and result["sandbox"] == "local" and result["runtime"] == "local"


def test_run_snippet_unavailable_reports_sandbox_unavailable(settings, no_docker):
    result = rc.run_snippet("python", "print(1)")
    assert result["ok"] is False and result["sandbox"] == "unavailable"


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


def test_run_code_with_a_limited_network_ingests_and_sends_but_reads_nothing(settings, monkeypatch):
    """Inside a run whose environment has a limited network the snippet can
    reach that environment's hosts, so it is classified like the web tools;
    it loses the workspace mount, so it no longer reads private files, and
    the tool set's own check catches an agent that also does."""
    from tools.capabilities import CAN_EXFILTRATE, INGESTS_UNTRUSTED
    monkeypatch.setenv("AGENTS_HUB_NETWORK", "limited")
    assert grants_of("run_code") == frozenset({INGESTS_UNTRUSTED, CAN_EXFILTRATE})
    # Text in and data out with no private reads: a warning, not a block.
    alone = check_combination(["run_code"])
    assert alone.rule_id == "exfiltration_path" and alone.severity == "warn"
    assert check_combination(["run_code", "read_file"]).rule_id == "lethal_trifecta"


def test_a_limited_network_refuses_the_workspace_mount(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTS_HUB_NETWORK", "limited")
    monkeypatch.setenv("AGENTS_HUB_ALLOWED_HOSTS", "pypi.org")
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod.subprocess, "run",
                        lambda cmd, **kw: pytest.fail("nothing may run with both"))
    out = _run(language="python", code="print(1)", mount_workspace=True)
    assert "either the workspace or the network" in out


def test_run_code_grant_fails_closed(monkeypatch):
    import common.config
    monkeypatch.delattr(common.config, "settings")
    assert grants_of("run_code") == grants_of("run_shell")


def test_run_code_is_in_the_execution_category():
    from tools.registry import get_tool_by_id
    spec = get_tool_by_id("run_code")
    assert spec is not None and spec.category == "execution"
    assert {p["name"] for p in spec.parameters} >= {"language", "code", "timeout", "stdin", "mount_workspace"}


def test_docker_mount_sets_work_env(tmp_path):
    from sandbox.docker import build_docker_command
    cmd = build_docker_command(language="python", code_dir=str(tmp_path), image="python:3.12-slim",
                               name="t", memory="512m", cpus="1", pids_limit=128, workspace_dir="/host/ws")
    i = cmd.index("/host/ws:/work:ro")
    assert cmd[i - 1] == "-v"
    assert cmd[i + 1:i + 3] == ["-e", "WORK=/work"]
    without = build_docker_command(language="python", code_dir=str(tmp_path), image="python:3.12-slim",
                                   name="t", memory="512m", cpus="1", pids_limit=128)
    assert "WORK=/work" not in without
