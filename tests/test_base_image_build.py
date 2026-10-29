"""The agents base image: pulled from the release when this hub is one, built
otherwise, and made from the Containers page as a background job.

A hub running from the published images had no way to get
agents-hub/base:latest: the page's build went through the legacy builder (no
buildx in the backend image), ran past its limit, and the request it held open
was cut by the proxy in front ("Network Error"). Every runner and instance then
failed with Docker Hub's "pull access denied".
"""
from __future__ import annotations

import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import managers.container_manager as cm


@pytest.fixture
def no_settings(monkeypatch):
    """WITH_RAG and AGENTS_HUB_AGENTS_IMAGE as the environment says, not the
    developer's .env."""
    import common.config as config
    monkeypatch.setattr(config, "live_setting", lambda key, default="": __import__("os").environ.get(key, default))
    monkeypatch.delenv("WITH_RAG", raising=False)
    monkeypatch.delenv("AGENTS_HUB_AGENTS_IMAGE", raising=False)


def test_release_image_follows_the_version_and_flavour(monkeypatch, no_settings):
    monkeypatch.delenv("AGENTS_HUB_VERSION", raising=False)
    assert cm.release_base_image() == ""
    monkeypatch.setenv("AGENTS_HUB_VERSION", "v0.12.0")
    assert cm.release_base_image() == "ghcr.io/snooker155/agents-hub-agents:0.12.0"
    monkeypatch.setenv("WITH_RAG", "true")
    monkeypatch.setenv("AGENTS_HUB_AGENTS_IMAGE", "registry.local/agents")
    assert cm.release_base_image() == "registry.local/agents:0.12.0-rag"


def _fake_docker(monkeypatch, *, pull_ok=True, build_ok=True):
    calls: list[list[str]] = []

    def stream(cmd, on_line, timeout):
        calls.append(cmd)
        ok = pull_ok if cmd[1] == "pull" else build_ok
        if on_line:
            on_line(f"{cmd[1]} output")
        return (0 if ok else 1), (f"{cmd[1]} output" if ok else f"{cmd[1]} output\n{cmd[1]} broke")

    def run(cmd, timeout=300, capture=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cm, "_stream", stream)
    monkeypatch.setattr(cm, "_run", run)
    return calls


def test_a_release_pulls_and_tags_instead_of_building(monkeypatch, no_settings):
    monkeypatch.setenv("AGENTS_HUB_VERSION", "0.12.0")
    calls = _fake_docker(monkeypatch)
    result = cm.build_base_image()
    assert result["success"] and result["source"] == "ghcr.io/snooker155/agents-hub-agents:0.12.0"
    assert [c[:2] for c in calls] == [["docker", "pull"], ["docker", "tag"]]
    assert calls[1][2:] == ["ghcr.io/snooker155/agents-hub-agents:0.12.0", cm.BASE_IMAGE]


def test_a_failed_pull_falls_back_to_the_build(monkeypatch, no_settings):
    monkeypatch.setenv("AGENTS_HUB_VERSION", "0.12.0")
    calls = _fake_docker(monkeypatch, pull_ok=False)
    lines: list[str] = []
    result = cm.build_base_image(on_line=lines.append)
    assert result["success"] and result["source"] == "build"
    assert [c[1] for c in calls] == ["pull", "build"]
    assert any("building from source instead" in line for line in lines)


def test_a_checkout_or_no_cache_builds(monkeypatch, no_settings):
    monkeypatch.delenv("AGENTS_HUB_VERSION", raising=False)
    calls = _fake_docker(monkeypatch, build_ok=False)
    result = cm.build_base_image()
    assert not result["success"] and result["error"] == "build broke"
    assert [c[1] for c in calls] == ["build"]

    monkeypatch.setenv("AGENTS_HUB_VERSION", "0.12.0")
    calls = _fake_docker(monkeypatch)
    assert cm.build_base_image(no_cache=True)["success"]
    assert [c[1] for c in calls] == ["build"] and "--no-cache" in calls[0]


def test_a_build_past_its_limit_says_so(monkeypatch, no_settings):
    monkeypatch.delenv("AGENTS_HUB_VERSION", raising=False)

    def stream(cmd, on_line, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(cm, "_stream", stream)
    result = cm.build_base_image()
    assert not result["success"] and "did not finish within 60 minutes" in result["error"]


def test_stream_hands_over_each_line():
    lines: list[str] = []
    code, out = cm._stream(["sh", "-c", "echo one; echo two >&2; exit 3"], lines.append, timeout=10)
    assert code == 3 and lines == ["one", "two"] and out == "one\ntwo"


def test_a_missing_hub_image_explains_what_to_do(monkeypatch, no_settings):
    monkeypatch.setenv("AGENTS_HUB_VERSION", "0.12.0")
    denied = ("Unable to find image 'agents-hub/base:latest' locally\n"
              "docker: Error response from daemon: pull access denied for agents-hub/base")
    hint = cm.missing_image_hint("agents-hub/base:latest", denied)
    assert "Containers page" in hint and "docker pull ghcr.io/snooker155/agents-hub-agents:0.12.0" in hint
    assert hint.endswith(denied)
    # Someone else's image, or another failure, is passed through as it was.
    assert cm.missing_image_hint("python:3.11", denied) == denied
    assert cm.missing_image_hint("agents-hub/base:latest", "port is already allocated") == "port is already allocated"


# ── The Containers page's background job ─────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    from dashboard.backend.routes import containers
    monkeypatch.setattr(cm, "DOCKERFILE_DIR", tmp_path)
    app = FastAPI()
    app.include_router(containers.router)
    return TestClient(app)


def _wait_finished(client):
    for _ in range(100):
        state = client.get("/api/containers/build-base").json()
        if state["state"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("the build never finished")


def test_post_returns_at_once_and_get_follows_the_build(client, monkeypatch):
    def build(no_cache=False, on_line=None):
        on_line("step 1")
        time.sleep(0.2)
        on_line("step 2")
        return {"success": True, "source": "build", "error": None}

    monkeypatch.setattr(cm, "build_base_image", build)
    assert client.get("/api/containers/build-base").json() == {"state": "idle"}
    started = client.post("/api/containers/build-base", json={"no_cache": False})
    assert started.status_code == 202 and started.json()["state"] == "running"
    # A second click while it runs joins it rather than starting another.
    again = client.post("/api/containers/build-base", json={})
    assert again.json()["started_at"] == started.json()["started_at"]
    done = _wait_finished(client)
    assert done["state"] == "done" and done["source"] == "build"
    assert done["log"].splitlines()[-2:] == ["step 1", "step 2"]


def test_a_failed_build_reports_its_error(client, monkeypatch):
    monkeypatch.setattr(cm, "build_base_image",
                        lambda no_cache=False, on_line=None: {"success": False, "error": "no space left"})
    client.post("/api/containers/build-base", json={})
    done = _wait_finished(client)
    assert done["state"] == "failed" and done["error"] == "no space left"


def test_a_build_whose_backend_is_gone_reads_as_failed(client, tmp_path):
    import json
    (tmp_path / "base-build.json").write_text(json.dumps(
        {"state": "running", "heartbeat": time.time() - 3600, "log": "", "error": None}))
    state = client.get("/api/containers/build-base").json()
    assert state["state"] == "failed" and "restarted" in state["error"]
