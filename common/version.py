"""
Which build of Agents Hub this is: the release version and the commit.

One source for the version, ``pyproject.toml``'s ``[project] version``, which
``scripts/release.py`` bumps together with the frontend's ``package.json`` and
the Helm chart. An image built by the release workflow cannot read the
checkout's git history, so it carries both values as environment variables
baked in at build time (``AGENTS_HUB_VERSION``, ``AGENTS_HUB_GIT_SHA``, see the
``Dockerfile``); they win over what the files say, because an image is exactly
the case where the bind mounted checkout may be a different commit.

Nothing here raises: a version that cannot be read is ``"unknown"``, and a
missing git is an empty sha. ``/api/system/version``, ``ah version``, the
support bundle and the backup manifest all read :func:`info`.
"""
from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from typing import Dict

from common.paths import PROJECT_ROOT

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$")


def is_semver(value: str) -> bool:
    return bool(_SEMVER.match(value or ""))


def _pyproject_version() -> str:
    try:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def app_version() -> str:
    """The release version: the image's baked value, else pyproject.toml."""
    baked = (os.environ.get("AGENTS_HUB_VERSION") or "").strip()
    if baked:
        return baked.lstrip("v")
    return _pyproject_version() or "unknown"


@lru_cache(maxsize=1)
def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(PROJECT_ROOT), capture_output=True,
                             text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def git_sha() -> str:
    baked = (os.environ.get("AGENTS_HUB_GIT_SHA") or "").strip()
    return baked or _git("rev-parse", "HEAD")


def info() -> Dict[str, str]:
    """``version``, ``git_sha`` and, for a checkout, ``git_branch`` and
    ``git_describe`` (the nearest release tag plus commits since it)."""
    sha = git_sha()
    return {
        "version": app_version(),
        "git_sha": sha,
        "git_short": sha[:12],
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_describe": _git("describe", "--tags", "--match", "v*", "--always", "--dirty"),
    }


__all__ = ["app_version", "git_sha", "info", "is_semver"]
