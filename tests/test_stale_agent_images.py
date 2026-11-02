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
            found = self.images.get(cmd[-1])
            return _Done(stdout=json.dumps(found["labels"]) if found else "", returncode=0 if found else 1)
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
