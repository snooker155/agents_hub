"""
The code stamp of a runner replica (common/code_version.py) is the code it
starts from: the sources on disk for a process (read fresh, not cached for a
launcher's whole life), the image build for a container. A replica is stale
only when a new one would start from other code (services/replicas.py), so a
worker that has run since before the backend moved on no longer stamps
every replica it starts as stale (the service replaced them in a loop, and
the Cluster map showed two of the same runner while it did).
"""
from __future__ import annotations

from common import code_version
from services import replicas


def test_the_disk_stamp_follows_the_files(monkeypatch):
    stamps = iter(["aaa", "bbb"])
    monkeypatch.setattr(code_version, "_hash_sources", lambda: next(stamps))
    monkeypatch.setitem(code_version._disk, "stamp", "")
    clock = [100.0]
    monkeypatch.setattr(code_version.time, "monotonic", lambda: clock[0])
    assert code_version.current() == "aaa"
    clock[0] += 5
    assert code_version.current() == "aaa"          # within the cache
    clock[0] += code_version.DISK_TTL_SECONDS
    assert code_version.current() == "bbb"          # a long-lived launcher sees the edit


def test_a_process_replica_is_stale_only_once_the_disk_moved_on(monkeypatch):
    monkeypatch.setattr(code_version, "current", lambda: "new")
    assert not replicas.is_stale({"carrier_code": "new"})
    assert replicas.is_stale({"carrier_code": "old"})
    assert replicas.is_stale({})                    # started before stamps


def test_a_container_replica_follows_its_image(monkeypatch):
    monkeypatch.setattr(code_version, "current", lambda: "disk-changed")
    builds = {"agents-hub/base:latest": "img:1111"}
    monkeypatch.setattr(code_version, "image_stamp", lambda image: builds.get(image, ""))
    rep = {"carrier_code": "img:1111", "carrier_image": "agents-hub/base:latest"}
    # The sources on disk changed, but the container runs its image's code.
    assert not replicas.is_stale(rep)
    builds["agents-hub/base:latest"] = "img:2222"   # the image was rebuilt
    assert replicas.is_stale(rep)
    # An image this host's Docker cannot read: left alone, never a loop.
    assert not replicas.is_stale({"carrier_code": "img:1111", "carrier_image": "elsewhere/img:1"})


def test_image_stamps(monkeypatch):
    answers = {("inspect", "-f", "{{.Config.Image}} {{.Image}}", "box"): "agents-hub/base:latest sha256:abcdef0123456789ffff",
               ("image", "inspect", "-f", "{{.Id}}", "agents-hub/base:latest"): "sha256:abcdef0123456789ffff"}
    monkeypatch.setattr(code_version, "_docker", lambda args: answers.get(tuple(args), ""))
    code_version._images.clear()
    assert code_version.container_stamp("box") == ("agents-hub/base:latest", "img:abcdef0123456789")
    assert code_version.image_stamp("agents-hub/base:latest") == "img:abcdef0123456789"
    assert code_version.container_stamp("gone") == ("", "")
    assert code_version.image_stamp("missing:1") == ""
