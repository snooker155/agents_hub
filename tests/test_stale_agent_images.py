"""Images built on top of the base image follow it when it is rebuilt.

The code lives in agents-hub/base. A per-agent image (agents-hub/<id>) and an
environment's derived image (agents-hub-env:<hash>) are made FROM it and kept
by tag, so after the base was rebuilt they still ran the old code: delegation
to swe_agent kept failing with "workspace 'workspace' is not this run's
workspace" after the fix, until the images were rebuilt by hand.
"""
from __future__ import annotations

import json
from typing import Dict, List

import managers.container_manager as cm


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class _Docker:
    """Images by tag ({tag: {"id": ..., "labels": {...}}}) and the commands run."""

    def __init__(self, images: Dict[str, Dict], fail_build: bool = False):
        self.images = images
        self.fail_build = fail_build
        self.calls: List[List[str]] = []

    def __call__(self, cmd, timeout=300, capture=True):
        self.calls.append(cmd)
        if cmd[:3] == ["docker", "images", "-q"]:
            found = self.images.get(cmd[-1])
            return _Done(stdout=(found["id"] + "\n") if found else "")
        if cmd[:3] == ["docker", "image", "inspect"]:
            tags = cmd[5:]
            lines = [f"{json.dumps([tag])}\t{self.images[tag]['id']}\t"
                     f"{self.images[tag].get('created', '2026-11-01T19:40:48Z')}\t"
                     f"{json.dumps(self.images[tag]['labels'])}"
                     for tag in tags if tag in self.images]
            return _Done(stdout="\n".join(lines), returncode=0 if len(lines) == len(tags) else 1)
        if cmd[:2] == ["docker", "build"]:
            if self.fail_build:
                return _Done(returncode=1, stderr="build failed")
            tag = cmd[cmd.index("-t") + 1]
            labels = {}
            for i, arg in enumerate(cmd):
                if arg == "--label":
                    key, _, value = cmd[i + 1].partition("=")
                    labels[key] = value
            self.images[tag] = {"id": "sha256:new-" + tag, "labels": labels}
            return _Done()
        raise AssertionError(cmd)

    def builds(self) -> List[List[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "build"]]


def _setup(monkeypatch, tmp_path, docker):
    monkeypatch.setattr(cm, "DOCKERFILE_DIR", tmp_path)
    monkeypatch.setattr(cm, "_run", docker)


def test_a_per_agent_image_records_its_base(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b1", "labels": {}}})
    _setup(monkeypatch, tmp_path, docker)
    assert cm.build_image("swe_agent", "SWE")["success"]
    assert docker.images["agents-hub/swe_agent:latest"]["labels"][cm.LABEL_BASE_ID] == "sha256:b1"


def test_a_per_agent_image_on_the_current_base_is_used_as_is(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b1", "labels": {}},
                      "agents-hub/swe_agent:latest": {"id": "sha256:a1",
                                                      "labels": {cm.LABEL_BASE_ID: "sha256:b1"}}})
    _setup(monkeypatch, tmp_path, docker)
    assert cm.image_tag_for_agent("swe_agent") == "agents-hub/swe_agent:latest"
    assert docker.builds() == []


def test_a_per_agent_image_on_an_older_base_is_rebuilt(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b2", "labels": {}},
                      "agents-hub/swe_agent:latest": {"id": "sha256:a1", "labels": {
                          cm.LABEL_BASE_ID: "sha256:b1", "agents-hub.agent-name": "SWE"}}})
    _setup(monkeypatch, tmp_path, docker)
    assert cm.image_tag_for_agent("swe_agent") == "agents-hub/swe_agent:latest"
    assert len(docker.builds()) == 1
    assert docker.images["agents-hub/swe_agent:latest"]["labels"][cm.LABEL_BASE_ID] == "sha256:b2"
    assert 'agents-hub.agent-name="SWE"' in (tmp_path / "swe_agent.Dockerfile").read_text()
    # Rebuilt once: the next run finds it current.
    assert cm.image_tag_for_agent("swe_agent") == "agents-hub/swe_agent:latest"
    assert len(docker.builds()) == 1


def test_a_per_agent_image_without_the_label_is_rebuilt(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b2", "labels": {}},
                      "agents-hub/main-agent:latest": {"id": "sha256:a0", "labels": {}}})
    _setup(monkeypatch, tmp_path, docker)
    assert cm.image_tag_for_agent("main-agent") == "agents-hub/main-agent:latest"
    assert len(docker.builds()) == 1


def test_a_stale_per_agent_image_that_fails_to_rebuild_gives_the_base(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b2", "labels": {}},
                      "agents-hub/swe_agent:latest": {"id": "sha256:a1",
                                                      "labels": {cm.LABEL_BASE_ID: "sha256:b1"}}},
                     fail_build=True)
    _setup(monkeypatch, tmp_path, docker)
    assert cm.image_tag_for_agent("swe_agent") == cm.BASE_IMAGE


def test_an_environment_image_follows_a_rebuilt_base(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b1", "labels": {}}})
    _setup(monkeypatch, tmp_path, docker)
    first = cm.ensure_environment_image(cm.BASE_IMAGE, ["numpy"])
    assert cm.ensure_environment_image(cm.BASE_IMAGE, ["numpy"]) == first
    assert len(docker.builds()) == 1
    docker.images[cm.BASE_IMAGE]["id"] = "sha256:b2"   # the base was rebuilt
    second = cm.ensure_environment_image(cm.BASE_IMAGE, ["numpy"])
    assert second != first and second.startswith("agents-hub-env:")
    assert len(docker.builds()) == 2


def test_a_base_build_records_its_version_and_commit(monkeypatch):
    monkeypatch.setattr(cm, "release_base_image", lambda: "")
    monkeypatch.setattr(cm, "_source_stamp", lambda: {
        "version": "0.12.0", "revision": "3a4a53b5c0ffee", "source": "v0.12.0-215-g3a4a53b5-dirty"})
    seen = {}
    monkeypatch.setattr(cm, "_stream", lambda cmd, on_line, timeout: seen.setdefault("cmd", cmd) and (0, ""))
    assert cm.build_base_image()["success"]
    cmd = seen["cmd"]
    assert "AGENTS_HUB_VERSION=0.12.0" in cmd and "AGENTS_HUB_GIT_SHA=3a4a53b5c0ffee" in cmd
    assert f"{cm.LABEL_SOURCE}=v0.12.0-215-g3a4a53b5-dirty" in cmd


def test_a_per_agent_image_records_the_base_version(monkeypatch, tmp_path):
    docker = _Docker({cm.BASE_IMAGE: {"id": "sha256:b1", "created": "2026-11-01T19:40:48Z", "labels": {
        cm.LABEL_VERSION: "0.12.0", cm.LABEL_REVISION: "3a4a53b5c0ffee"}}})
    _setup(monkeypatch, tmp_path, docker)
    assert cm.build_image("swe_agent", "SWE")["success"]
    labels = docker.images["agents-hub/swe_agent:latest"]["labels"]
    assert labels[cm.LABEL_BASE_VERSION] == "0.12.0"
    assert labels[cm.LABEL_BASE_REVISION] == "3a4a53b5c0ffee"
    assert labels[cm.LABEL_BASE_CREATED] == "2026-11-01T19:40:48Z"


def test_agents_status_gives_the_base_and_marks_outdated_images(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from dashboard.backend.routes import containers as route
    docker = _Docker({
        cm.BASE_IMAGE: {"id": "sha256:b2", "created": "2026-11-01T19:40:48Z",
                        "labels": {cm.LABEL_VERSION: "0.12.0", cm.LABEL_REVISION: "3a4a53b5",
                                   cm.LABEL_SOURCE: "v0.12.0-215-g3a4a53b5"}},
        "agents-hub/main-agent:latest": {"id": "sha256:a2", "labels": {
            cm.LABEL_BASE_ID: "sha256:b2", cm.LABEL_BASE_VERSION: "0.12.0"}},
        "agents-hub/swe_agent:latest": {"id": "sha256:a1", "labels": {
            cm.LABEL_BASE_ID: "sha256:b1", cm.LABEL_BASE_REVISION: "e4ee60fa"}},
        "agents-hub/bean_scout:latest": {"id": "sha256:a0", "labels": {}},
    })
    _setup(monkeypatch, tmp_path, docker)
    monkeypatch.setattr(cm, "list_images", lambda: [
        {"repository": tag.split(":")[0], "tag": "latest"} for tag in docker.images])
    monkeypatch.setattr(route, "list_agents", lambda: [
        SimpleNamespace(id=i, name=i) for i in ("main-agent", "swe_agent", "bean_scout", "writer")])
    app = FastAPI()
    app.include_router(route.router)
    body = TestClient(app).get("/api/containers/agents-status").json()
    assert body["base"] == {"exists": True, "image": cm.BASE_IMAGE, "id": "sha256:b2",
                            "created": "2026-11-01T19:40:48Z", "version": "0.12.0",
                            "revision": "3a4a53b5", "source": "v0.12.0-215-g3a4a53b5"}
    by_id = {a["agent_id"]: a for a in body["agents"]}
    assert by_id["main-agent"]["stale"] is False and by_id["main-agent"]["base_version"] == "0.12.0"
    assert by_id["swe_agent"]["stale"] is True and by_id["swe_agent"]["base_revision"] == "e4ee60fa"
    assert by_id["bean_scout"]["stale"] is True and by_id["bean_scout"]["base_id"] == ""
    assert by_id["writer"]["image_exists"] is False and "stale" not in by_id["writer"]
    # One inspect for the base and every built agent image, not one per agent.
    assert len([c for c in docker.calls if c[:3] == ["docker", "image", "inspect"]]) == 1
