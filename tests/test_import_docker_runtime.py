"""Docker mode for imported agents: the hub builds the image and runs one
container per workspace, mounting that workspace at its own host path. Every docker call is faked.

Run: ``python -m pytest tests/test_import_docker_runtime.py -q``
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agents.importer import checks, docker_runtime
from agents.importer.manifest import AgentManifest
from agents.remote_agent import RemoteAgent


def _cp(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeDocker:
    """A docker CLI that answers what ensure_container asks, in order."""

    def __init__(self, *, image_id: str = "sha256:img1", existing: Dict[str, Any] | None = None):
        self.image_id = image_id
        self.existing = existing  # {"state", "image_id"} of a pre-existing container
        self.calls: List[List[str]] = []
        self.host_port = "49152"

    def __call__(self, argv: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        if argv[:2] == ["image", "inspect"]:
            return _cp(self.image_id + "\n") if self.image_id else _cp("", 1, "No such image")
        if argv[0] == "inspect":
            fmt = argv[2]
            if not self.existing:
                return _cp("", 1, "No such object")
            if "State.Status" in fmt:
                return _cp(self.existing["state"] + "\n")
            if ".Image" in fmt:
                return _cp(self.existing.get("image_id", "") + "\n")
            return _cp("2026-10-09T00:00:00Z\n")
        if argv[0] == "port":
            return _cp(f"127.0.0.1:{self.host_port}\n")
        if argv[0] == "run":
            self.existing = {"state": "running", "image_id": self.image_id}
            return _cp("abc123\n")
        if argv[0] == "rm":
            self.existing = None
            return _cp("")
        if argv[0] == "ps":
            return _cp("")
        if argv[0] == "logs":
            return _cp("")
        return _cp("")


@pytest.fixture
def descriptor() -> Dict[str, Any]:
    return {
        "runtime_mode": "docker",
        "run_path": "/run",
        "health_path": "/health",
        "stream_path": "/run/stream",
        "port": 8430,
        "dockerfile": "Dockerfile.agenthub",
        "clone_path": "",
        "manifest": {
            "port": 8430,
            "docker_context": ".",
            "env": [{"name": "OPENAI_API_KEY", "required": True},
                    {"name": "AIDER_MODEL", "required": False}],
        },
    }


@pytest.fixture
def no_health_wait(monkeypatch):
    monkeypatch.setattr(docker_runtime, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr("managers.container_manager.register_container", lambda *a, **k: None)


# ── naming and mode ─────────────────────────────────────────────────────────

def test_mode_defaults_to_url_and_names_are_docker_safe():
    assert docker_runtime.is_docker_mode({}) is False
    assert docker_runtime.is_docker_mode({"runtime_mode": "url"}) is False
    assert docker_runtime.is_docker_mode({"runtime_mode": "Docker"}) is True
    assert docker_runtime.image_tag("Aider") == "agents-hub-import/aider:latest"
    assert docker_runtime.container_name("aider", "my ws/x") == "agents-hub-import-aider-my-ws-x"


# ── mounts: the workspace ───────────────────────────────────────────────────

def test_mounts_are_the_workspace_folder_at_its_own_path(monkeypatch):
    from workspace import WORKSPACES_ROOT
    mounts = docker_runtime.mounts_for("alpha")
    ws_dir = str((Path(WORKSPACES_ROOT) / "alpha").resolve())
    assert mounts == [(ws_dir, ws_dir)]
    assert Path(ws_dir).is_dir()


# ── the workspace a path belongs to ─────────────────────────────────────────

def test_workspace_for_path_reads_workspace_and_project_folders():
    from workspace import WORKSPACES_ROOT
    root = Path(WORKSPACES_ROOT).resolve()
    assert docker_runtime.workspace_for_path(None) == "default"
    assert docker_runtime.workspace_for_path(str(root / "eps")) == "eps"
    assert docker_runtime.workspace_for_path(str(root / "eps" / "proj" / "src")) == "eps"


# ── starting a container ────────────────────────────────────────────────────

def test_ensure_container_runs_with_mounts_port_labels_and_declared_env(
        monkeypatch, descriptor, no_health_wait, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("NOT_DECLARED", "secret")
    monkeypatch.delenv("AIDER_MODEL", raising=False)
    fake = FakeDocker()
    monkeypatch.setattr(docker_runtime, "_docker", fake)

    record = docker_runtime.ensure_container("aider", descriptor, "zeta")

    run = next(c for c in fake.calls if c[0] == "run")
    from workspace import WORKSPACES_ROOT
    ws_dir = str((Path(WORKSPACES_ROOT) / "zeta").resolve())
    assert f"{ws_dir}:{ws_dir}" in run
    assert run[run.index("-p") + 1].startswith("127.0.0.1:") and run[run.index("-p") + 1].endswith(":8430")
    assert f"{docker_runtime.LABEL_WORKSPACE}=zeta" in run
    assert f"{docker_runtime.LABEL_IMPORT}=aider" in run
    assert "OPENAI_API_KEY=sk-test" in run
    assert f"{docker_runtime.ENV_WORKSPACE}={ws_dir}" in run
    assert not any(v.startswith("NOT_DECLARED=") or v.startswith("AIDER_MODEL=") for v in run)
    assert run[-1] == "agents-hub-import/aider:latest"
    assert record["url"] == "http://127.0.0.1:49152"
    assert record["state"] == "running"


def test_ensure_container_refuses_without_an_image(monkeypatch, descriptor, no_health_wait):
    monkeypatch.setattr(docker_runtime, "_docker", FakeDocker(image_id=""))
    with pytest.raises(docker_runtime.DockerRuntimeError, match="not built"):
        docker_runtime.ensure_container("aider", descriptor, "default")


def test_ensure_container_reuses_a_running_container_on_the_current_image(
        monkeypatch, descriptor, no_health_wait):
    fake = FakeDocker(existing={"state": "running", "image_id": "sha256:img1"})
    monkeypatch.setattr(docker_runtime, "_docker", fake)
    record = docker_runtime.ensure_container("aider", descriptor, "default")
    assert record["url"] == "http://127.0.0.1:49152"
    assert not any(c[0] in ("run", "rm") for c in fake.calls)


def test_ensure_container_replaces_a_container_on_an_older_image(
        monkeypatch, descriptor, no_health_wait):
    fake = FakeDocker(existing={"state": "running", "image_id": "sha256:old"})
    monkeypatch.setattr(docker_runtime, "_docker", fake)
    docker_runtime.ensure_container("aider", descriptor, "default")
    kinds = [c[0] for c in fake.calls]
    assert kinds.index("rm") < kinds.index("run")


def test_a_container_that_never_answers_is_removed(monkeypatch, descriptor):
    fake = FakeDocker()
    monkeypatch.setattr(docker_runtime, "_docker", fake)
    monkeypatch.setattr("managers.container_manager.register_container", lambda *a, **k: None)

    def never(*a, **k):
        raise docker_runtime.DockerRuntimeError("no answer")
    monkeypatch.setattr(docker_runtime, "_wait_healthy", never)
    with pytest.raises(docker_runtime.DockerRuntimeError, match="no answer"):
        docker_runtime.ensure_container("aider", descriptor, "default")
    assert any(c[0] == "rm" for c in fake.calls)


def test_build_image_uses_the_clone_dockerfile_and_context(monkeypatch, descriptor, tmp_path):
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "Dockerfile.agenthub").write_text("FROM scratch\n")
    descriptor["clone_path"] = str(clone)
    descriptor["commit"] = "abc"
    fake = FakeDocker()
    monkeypatch.setattr(docker_runtime, "_docker", fake)
    built = docker_runtime.build_image("aider", descriptor)
    build = next(c for c in fake.calls if c[0] == "build")
    assert build[1:3] == ["-t", "agents-hub-import/aider:latest"]
    assert build[build.index("-f") + 1] == str(clone / "Dockerfile.agenthub")
    assert build[-1] == str(clone.resolve())
    assert built["image_id"] == "sha256:img1" and built["commit"] == "abc"


def test_build_image_without_a_clone_is_an_operator_error(descriptor, monkeypatch):
    monkeypatch.setattr(docker_runtime, "_docker", FakeDocker())
    with pytest.raises(docker_runtime.DockerRuntimeError, match="clone is missing"):
        docker_runtime.build_image("aider", descriptor)


# ── the remote agent in docker mode ─────────────────────────────────────────

def test_capability_questions_never_start_a_container(monkeypatch, descriptor):
    def boom(*a, **k):
        raise AssertionError("url_for must not be called")
    monkeypatch.setattr(docker_runtime, "url_for", boom)
    agent = RemoteAgent("aider", "Aider", descriptor, workspace="/tmp/x")
    assert agent.docker_mode and agent.has_endpoint
    assert agent.supports_streaming is True
    assert agent.supports_resume is False
    assert agent.supports_topology is False


def test_the_run_url_is_the_workspace_container(monkeypatch, descriptor):
    seen: Dict[str, Any] = {}

    def url_for(agent_id, remote, workspace_path):
        seen["args"] = (agent_id, workspace_path)
        return "http://127.0.0.1:50000"
    monkeypatch.setattr(docker_runtime, "url_for", url_for)
    agent = RemoteAgent("aider", "Aider", descriptor, workspace="/ws/alpha")
    assert agent.run_url == "http://127.0.0.1:50000/run"
    assert agent.stream_url == "http://127.0.0.1:50000/run/stream"
    assert seen["args"] == ("aider", "/ws/alpha")


def test_a_container_that_cannot_start_is_a_failed_result_not_an_exception(monkeypatch, descriptor):
    def url_for(*a, **k):
        raise docker_runtime.DockerRuntimeError("the image for 'aider' is not built")
    monkeypatch.setattr(docker_runtime, "url_for", url_for)
    agent = RemoteAgent("aider", "Aider", descriptor, workspace="/ws/alpha")
    result = agent.run("hello")
    assert result.ok is False and "not built" in (result.error or "")
    health = agent.check_health()
    assert health["ok"] is False and "not built" in health["detail"]


# ── readiness checks ────────────────────────────────────────────────────────

def test_endpoint_check_passes_in_docker_mode_without_a_url():
    assert checks.check_endpoint("", {"runtime_mode": "docker"}).ok is True
    assert checks.check_endpoint("", {}).ok is False


def test_health_check_reports_the_image_before_any_container(monkeypatch, descriptor):
    monkeypatch.setattr(docker_runtime, "status", lambda agent_id, d: {
        "docker_available": True, "image": {"exists": False}, "containers": []})
    check = checks.check_health("", AgentManifest(found=True), descriptor, agent_id="aider")
    assert check.ok is False and check.required is False and "not built" in check.detail

    monkeypatch.setattr(docker_runtime, "status", lambda agent_id, d: {
        "docker_available": True, "image": {"exists": True}, "containers": []})
    check = checks.check_health("", AgentManifest(found=True), descriptor, agent_id="aider")
    assert check.ok is True and "first run" in check.detail


# ── the service layer ───────────────────────────────────────────────────────

def test_switching_modes_persists_and_leaving_docker_stops_containers(monkeypatch, descriptor):
    from agents import registry
    from agents.importer import service

    spec = registry.AgentSpec(id="aider", name="Aider", type="remote",
                              entrypoint=service.REMOTE_ENTRYPOINT, tools=[], remote=dict(descriptor))
    saved: List[Any] = []
    stopped: List[Any] = []
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: spec if agent_id == "aider" else None)
    monkeypatch.setattr(registry, "add_agent", lambda s: saved.append(s))
    monkeypatch.setattr(service, "recheck", lambda agent_id, **k: {"runnable": True})
    monkeypatch.setattr(docker_runtime, "stop_containers", lambda *a, **k: stopped.append(a) or [])

    out = service.set_runtime_mode("aider", "url")
    assert out["mode"] == "url" and saved[-1].remote["runtime_mode"] == "url"
    assert stopped, "leaving docker mode stops the agent's containers"

    spec.remote["runtime_mode"] = "url"
    out = service.set_runtime_mode("aider", "docker")
    assert out["mode"] == "docker" and saved[-1].remote["runtime_mode"] == "docker"

    with pytest.raises(service.ImportError_):
        service.set_runtime_mode("aider", "cloud")


def test_docker_mode_needs_a_dockerfile(monkeypatch, descriptor):
    from agents import registry
    from agents.importer import service

    descriptor.pop("dockerfile")
    spec = registry.AgentSpec(id="x", name="x", type="remote",
                              entrypoint=service.REMOTE_ENTRYPOINT, tools=[], remote=dict(descriptor))
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: spec)
    with pytest.raises(service.ImportError_, match="Dockerfile"):
        service.set_runtime_mode("x", "docker")
