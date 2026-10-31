"""
The code a runner replica runs, as one short stamp.

A runner replica (services/) is a long-lived process: it imports the hub's
modules once and keeps them. When the code changes (a release, an edit while
developing, a rebuilt agents image) the replicas would go on with the old
code, and a turn relayed to one of them builds its agent from it: a tool
added since is silently missing while the instructions, read from disk on
every build, already tell the model to use it.

So every carrier is stamped with the code it starts from (instances/carrier.py),
and the service supervisor replaces a replica whose stamp is not what a new
replica would start from now (services/replicas.py ``is_stale``):

- a process carrier imports the sources on disk: :func:`current` hashes the
  content of the Python sources a replica can import, so a touched file or a
  checkout of the same content keeps it, and two hosts with the same code
  agree. It is read from disk, cached for :data:`DISK_TTL_SECONDS`: a
  long-lived worker that starts carriers must stamp them with the code they
  load, not the code it loaded itself (that stamped every replica it started
  as stale after the backend moved on, and the service replaced them in a
  loop).
- a container carrier runs the code baked into its image, whatever is on
  disk: its stamp is the image's id (:func:`container_stamp`), and it is
  stale once its image name points at another build (:func:`image_stamp`).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

from common.paths import PROJECT_ROOT

#: Directories, at any depth, that hold no code a replica imports.
SKIPPED_DIRS = frozenset({
    "node_modules", "__pycache__", "tests", "site", "docs", "examples", "deploy",
    "scripts", "frontend", "chroma_db",
})

#: How long a stamp read from disk (or from Docker) is reused.
DISK_TTL_SECONDS = 15.0
IMAGE_TTL_SECONDS = 30.0
IMAGE_PREFIX = "img:"

_disk: Dict[str, Any] = {"at": 0.0, "stamp": ""}
_images: Dict[str, Tuple[float, str]] = {}


def _hash_sources() -> str:
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIPPED_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as fh:
                    content = fh.read()
            except OSError:
                continue
            digest.update(os.path.relpath(path, PROJECT_ROOT).encode())
            digest.update(b"\0")
            digest.update(content)
    return digest.hexdigest()[:16]


def current() -> str:
    """The stamp of the sources on disk now (module docstring)."""
    now = time.monotonic()
    if not _disk["stamp"] or now - float(_disk["at"]) > DISK_TTL_SECONDS:
        _disk["stamp"] = _hash_sources()
        _disk["at"] = now
    return str(_disk["stamp"])


def _docker(args) -> str:
    """Docker's answer, or ``""``: a stamp it cannot read must never fail
    the start of a replica."""
    try:
        res = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 - see the docstring
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def container_stamp(container: str) -> Tuple[str, str]:
    """``(image name, stamp)`` of a running container: the image it was
    created from and that build's id. ``("", "")`` when Docker cannot say."""
    out = _docker(["inspect", "-f", "{{.Config.Image}} {{.Image}}", container])
    name, _, image_id = out.partition(" ")
    if not name or not image_id:
        return "", ""
    return name, IMAGE_PREFIX + image_id.split(":")[-1][:16]


def image_stamp(image: Optional[str]) -> str:
    """The stamp a container made from ``image`` now would carry; ``""``
    when this host's Docker does not know it (then nothing is replaced)."""
    if not image:
        return ""
    now = time.monotonic()
    cached = _images.get(image)
    if cached and now - cached[0] <= IMAGE_TTL_SECONDS:
        return cached[1]
    image_id = _docker(["image", "inspect", "-f", "{{.Id}}", image])
    stamp = IMAGE_PREFIX + image_id.split(":")[-1][:16] if image_id else ""
    _images[image] = (now, stamp)
    return stamp


__all__ = ["DISK_TTL_SECONDS", "IMAGE_PREFIX", "SKIPPED_DIRS", "container_stamp", "current", "image_stamp"]
