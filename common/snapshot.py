"""
Registry snapshots for run containers.

The agent registry, the custom provider list and the model catalog live in
the database (``common/docstore.py``). A run container does not get the
database: under Postgres it is never handed the URL and password, and under
SQLite the file is pinned read-only for the ``http`` state transport. What it
gets instead is a *snapshot*: before the launcher starts the container it
writes the three registries as JSON into ``<state>/run_snapshots/<run_id>/``
(:func:`write_snapshots`), mounts that directory read-only, and sets
``AGENTS_HUB_SNAPSHOT_DIR`` to its path inside the container. Each registry
module checks :func:`read_snapshot` first and, when the variable is set,
serves the snapshot and refuses writes, so a process inside a container can
neither see anyone else's edits mid-run nor change what it will be allowed
to do (the capability guard reads the same record).

Outside a container the variable is unset, :func:`read_snapshot` returns
None, and every registry goes to the database.

File names inside the snapshot directory are the names the state directory
used to hold: ``agents.json``, ``custom_providers.json``, ``models.json``.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT

SNAPSHOT_DIR_ENV = "AGENTS_HUB_SNAPSHOT_DIR"
SNAPSHOTS_ROOT = AGENTS_HUB_ROOT / "run_snapshots"

AGENTS_SNAPSHOT = "agents.json"
PROVIDERS_SNAPSHOT = "custom_providers.json"
MODELS_SNAPSHOT = "models.json"


def snapshot_dir() -> Optional[Path]:
    """The snapshot directory this process was pointed at, or None."""
    raw = os.environ.get(SNAPSHOT_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def in_snapshot_mode() -> bool:
    return snapshot_dir() is not None


def read_snapshot(name: str) -> Optional[Any]:
    """The parsed JSON of ``<snapshot dir>/<name>``, or None when this process
    is not in snapshot mode. A missing or unreadable file in snapshot mode is
    an empty snapshot (``{}``): the container must not fall through to the
    database."""
    base = snapshot_dir()
    if base is None:
        return None
    path = base / name
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def refuse_write(what: str) -> None:
    """Raise when a registry is asked to change inside a run container."""
    raise RuntimeError(
        f"{what} is read-only inside a run container: it works from the snapshot in "
        f"{SNAPSHOT_DIR_ENV}, not from the database. Make the change from the backend "
        "or the CLI on the host.")


def write_snapshots(run_id: str) -> Path:
    """Export the registries for one run. Returns the directory written.

    Each exporter is the ``export_snapshot()`` of its module and returns the
    JSON document that registry's snapshot file holds: the same shape the
    state-directory file had, so a container running older code reads it
    too."""
    from agents import registry as agent_registry
    from providers import registry as provider_registry
    from providers import catalog as model_catalog

    target = SNAPSHOTS_ROOT / str(run_id)
    target.mkdir(parents=True, exist_ok=True)
    documents: Dict[str, Any] = {
        AGENTS_SNAPSHOT: agent_registry.export_snapshot(),
        PROVIDERS_SNAPSHOT: provider_registry.export_snapshot(),
        MODELS_SNAPSHOT: model_catalog.export_snapshot(),
    }
    for name, doc in documents.items():
        tmp = target / (name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target / name)
    return target


def remove_snapshot(run_id: str) -> bool:
    """Delete one run's snapshot directory (when its run has finished)."""
    target = SNAPSHOTS_ROOT / str(run_id)
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


def prune_snapshots(known_run_ids: set) -> int:
    """Delete snapshot directories whose run no longer exists (maintenance)."""
    if not SNAPSHOTS_ROOT.is_dir():
        return 0
    removed = 0
    for entry in SNAPSHOTS_ROOT.iterdir():
        if entry.is_dir() and entry.name not in known_run_ids:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
