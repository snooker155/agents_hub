"""
The code this process runs, as one short stamp.

A runner replica (services/) is a long-lived process: it imports the hub's
modules once and keeps them. When the backend restarts on new code (a
release, or ``main.py --reload`` while editing) the replicas would go on with
the old code, and a turn relayed to one of them builds its agent from it: a
tool added since is silently missing while the instructions, read from disk
on every build, already tell the model to use it.

So every carrier is stamped with the stamp of the process that started it
(instances/carrier.py), and the service supervisor replaces a replica whose
stamp is not the backend's own (services/supervisor.py).

The stamp hashes the content of the Python sources a replica can import, so
a touched file or a checkout of the same content keeps it, and two hosts
with the same code agree. It is computed once per process, at its first use:
the backend calls it when its supervisor starts, so the stamp describes the
code the backend loaded, not what is on disk later.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from common.paths import PROJECT_ROOT

#: Directories, at any depth, that hold no code a replica imports.
SKIPPED_DIRS = frozenset({
    "node_modules", "__pycache__", "tests", "site", "docs", "examples", "deploy",
    "scripts", "frontend", "chroma_db",
})


@lru_cache(maxsize=1)
def current() -> str:
    """The stamp of the code this process runs (module docstring)."""
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


__all__ = ["current", "SKIPPED_DIRS"]
