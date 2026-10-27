"""Keep a hub's state on a Docker volume and the checkout's .agents_hub in step.

A hub in Docker with its state on a volume (docker-compose.volume.yml) sets
``AGENTS_HUB_STATE_SYNC_DIR`` to the checkout's .agents_hub as it sees it. Then:

* at start, before the backend opens the database (``python -m
  common.state_sync import``, the compose command), the folder is copied into
  the volume when the folder changed since the last sync and the volume did
  not;
* at shutdown (the backend's lifespan, :func:`export_on_shutdown`), the volume
  is copied to the folder when the folder did not change since the last sync.
  The previous folder is kept as ``.agents_hub.prev``.

So the state follows whichever hub ran last. When both sides changed (a hub
on the host worked on the folder while this one ran on the volume) nothing is
copied either way and the log says so; ``--force`` settles it by hand.

Two rules guard the database, after the incident of 2026-10-27
(docs/containers.md): the folder's database is never opened while it has a
``-wal`` or ``-shm`` file next to it (a hub on the host has it open, or died
with it open), and the database is always copied with SQLite's backup API, a
consistent snapshot, never as a file. A container opening a database a host
process has open is exactly what destroyed that WAL.

The bookkeeping lives in the volume, in ``.state_sync.json``: when the last
sync happened, the folder's database stamp right after it, and whether the
volume was exported when this hub last stopped.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

SYNC_DIR_ENV = "AGENTS_HUB_STATE_SYNC_DIR"
DB_NAME = "agents_hub.db"
MARKER = ".state_sync.json"
INCOMING = ".incoming"
_DB_FILES = (DB_NAME, DB_NAME + "-wal", DB_NAME + "-shm", DB_NAME + "-journal")
_SKIP = set(_DB_FILES) | {MARKER, INCOMING}


# ── What it does, in words ───────────────────────────────────────────────────
# Said at every start of a hub in Docker with its state on a volume (the
# backend log) and by the setup wizard where it asks where agents run.

HOW_IT_WORKS = (
    "The hub's state (database, workspaces, logs) lives in the Docker volume "
    "{volume}, not in the checkout's .agents_hub.",
    "At start the checkout's .agents_hub is copied into the volume when a hub on the "
    "host changed it since the last sync.",
    "At stop the hub stops its agent containers and copies the volume back to "
    ".agents_hub; the previous folder is kept as .agents_hub.prev. Stop it with "
    "`docker compose stop` or `down`, never by killing it, or the copy is skipped.",
    "Do not run a hub on the host on the same .agents_hub at the same time: when both "
    "change, neither is overwritten and the log says so. Settle it inside the hub's "
    "container: `docker compose exec backend ah state export --force` (the volume wins) "
    "or, with the hub stopped, `docker compose run --rm backend ah state import --force` "
    "(the folder wins).",
    "While a hub on the host has its database open (a .agents_hub/agents_hub.db-wal "
    "file exists), nothing is copied in either direction.",
)


def describe(volume: str = "agents_hub_state") -> str:
    return "\n".join("  - " + line.format(volume=volume) for line in HOW_IT_WORKS)


# ── Where ────────────────────────────────────────────────────────────────────

def sync_dir() -> Optional[Path]:
    """The folder to keep in step with, or None when sync is off. Only with
    the state on a volume: without one the hub works on the folder itself."""
    raw = os.environ.get(SYNC_DIR_ENV, "").strip()
    if not raw:
        return None
    from managers.container_manager import state_volume
    if not state_volume():
        return None
    return Path(raw)


def _state_root() -> Path:
    from common.paths import AGENTS_HUB_ROOT
    return Path(AGENTS_HUB_ROOT)


# ── Stamps and the marker ────────────────────────────────────────────────────

def _db_stamp(root: Path) -> Optional[Dict[str, int]]:
    try:
        st = (root / DB_NAME).stat()
    except OSError:
        return None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size}


def _in_use(root: Path) -> bool:
    """A -wal or -shm next to the database: some process has it open (or
    died with it open). SQLite removes both when the last connection closes."""
    return any((root / name).exists() for name in (DB_NAME + "-wal", DB_NAME + "-shm"))


def _read_marker() -> Dict[str, Any]:
    try:
        return json.loads((_state_root() / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_marker(**fields: Any) -> None:
    data = {**_read_marker(), **fields}
    path = _state_root() / MARKER
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def mark_running() -> None:
    """From now on the volume changes: the next start must not take it for
    exported until a shutdown export says so."""
    _write_marker(exported=False, started_at=_now())


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _folder_changed(folder: Path, marker: Dict[str, Any]) -> bool:
    return _db_stamp(folder) != marker.get("folder")


# ── Copying ──────────────────────────────────────────────────────────────────

def _backup_db(src: Path, dst: Path) -> None:
    """A consistent copy of a SQLite database, whatever is writing it."""
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _copy_tree(src: Path, dst: Path) -> None:
    """Everything under ``src`` except the database files and the sync's own
    bookkeeping, into ``dst`` (created)."""
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name in _SKIP:
            continue
        target = dst / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target, follow_symlinks=False)


def _stop_agent_containers() -> int:
    """Stop this hub's run and instance containers, which write to the volume:
    an export taken while they still run would miss what they write after."""
    try:
        from managers.container_manager import LABEL_MANAGED, _run
        out = _run(["docker", "ps", "-q", "--filter", f"label={LABEL_MANAGED}"], timeout=15)
        ids = [line for line in (out.stdout or "").split() if line]
        if ids:
            _run(["docker", "stop", "-t", "10", *ids], timeout=60)
        return len(ids)
    except Exception:  # noqa: BLE001 - the export still runs; a straggler only loses its last writes
        log.warning("state sync: could not stop agent containers", exc_info=True)
        return 0


# ── Export: volume -> folder ─────────────────────────────────────────────────

def export_state(folder: Path, *, force: bool = False, final: bool = False) -> Tuple[bool, str]:
    """Copy the volume's state over ``folder``, keeping the old folder as
    ``.agents_hub.prev``. Returns (done, why).

    ``final`` is the shutdown export: only then is the volume marked
    exported. A manual export while the hub runs is a snapshot; the volume
    keeps changing after it, so the next start must not treat it as synced."""
    root = _state_root()
    marker = _read_marker()
    if not (root / DB_NAME).exists():
        return False, "the volume has no database yet"
    if _in_use(folder):
        return False, (f"{folder} has a -wal or -shm file: a hub on the host has its database "
                       "open (or stopped without closing it). Stop it, then export.")
    if not force and (folder / DB_NAME).exists():
        if "folder" not in marker:
            return False, ("this volume and the folder were never synced and both hold a database: "
                           "pick one with `ah state export --force` or `ah state import --force` in the hub's container")
        if _folder_changed(folder, marker):
            return False, ("the folder changed since the last sync (a hub on the host worked on it) and so "
                           "did the volume: kept both. Settle it with export --force or import --force.")
    parent = folder.parent
    staging = parent / (folder.name + ".syncing")
    prev = parent / (folder.name + ".prev")
    shutil.rmtree(staging, ignore_errors=True)
    _copy_tree(root, staging)
    _backup_db(root / DB_NAME, staging / DB_NAME)
    if folder.exists():
        shutil.rmtree(prev, ignore_errors=True)
        folder.rename(prev)
    staging.rename(folder)
    _write_marker(folder=_db_stamp(folder), synced_at=_now(), exported=final, direction="export")
    return True, f"exported to {folder} (previous folder kept as {prev.name})"


def export_on_shutdown() -> None:
    """The backend's shutdown step: stop the agent containers, then export.
    Never raises; a skipped export leaves the volume marked unexported, so
    the next start will not overwrite it."""
    folder = sync_dir()
    if folder is None:
        return
    try:
        stopped = _stop_agent_containers()
        if stopped:
            log.info("state sync: stopped %d agent container(s) before the export", stopped)
        done, why = export_state(folder, final=True)
        (log.info if done else log.warning)("state sync at shutdown: %s", why)
    except Exception:  # noqa: BLE001 - shutdown must finish; the volume keeps the state
        log.exception("state sync at shutdown failed; the state stays on the volume")


# ── Import: folder -> volume ─────────────────────────────────────────────────

def import_state(folder: Path, *, force: bool = False) -> Tuple[bool, str]:
    """Copy ``folder`` into the volume, replacing its contents. Only before
    this hub opens the database. Returns (done, why)."""
    root = _state_root()
    marker = _read_marker()
    if not (folder / DB_NAME).exists():
        return False, f"{folder} has no database"
    if _in_use(root):
        return False, ("the volume's database is open (this hub runs, or stopped without closing "
                       "it): import only with the hub stopped")
    if _in_use(folder):
        return False, (f"{folder} has a -wal or -shm file: a hub on the host has its database open "
                       "(or stopped without closing it); the volume is used as it is")
    volume_has_db = (root / DB_NAME).exists() and not marker.get("importing")
    if not force and volume_has_db:
        if "folder" not in marker:
            return False, ("this volume and the folder were never synced and both hold a database: "
                           "pick one with `ah state import --force` or `ah state export --force` in the hub's container")
        if not _folder_changed(folder, marker):
            return False, "the folder did not change since the last sync"
        if not marker.get("exported"):
            return False, ("the folder changed since the last sync, but so did the volume (this hub "
                           "stopped without exporting): kept both. Settle it with import --force or "
                           "export --force.")
    incoming = root / INCOMING
    shutil.rmtree(incoming, ignore_errors=True)
    _write_marker(importing=True)
    _copy_tree(folder, incoming)
    _backup_db(folder / DB_NAME, incoming / DB_NAME)
    for entry in root.iterdir():
        if entry.name in (INCOMING, MARKER):
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in incoming.iterdir():
        entry.rename(root / entry.name)
    incoming.rmdir()
    _write_marker(folder=_db_stamp(folder), synced_at=_now(), exported=True,
                  direction="import", importing=False)
    return True, f"imported {folder} into the volume"


def import_on_start() -> int:
    """The compose command's first step. Always returns 0: a failed or
    skipped import leaves the volume as it was, and the hub starts on it."""
    folder = sync_dir()
    if folder is None:
        return 0
    from managers.container_manager import state_volume
    print(f"State on a Docker volume, kept in step with {folder}:\n{describe(state_volume())}", flush=True)
    try:
        done, why = import_state(folder)
        print(f"state sync at start: {why}", flush=True)
    except Exception as exc:  # noqa: BLE001 - the hub starts on the volume as it is
        print(f"state sync at start failed, the volume is used as it is: {exc}", flush=True)
    try:
        mark_running()
    except OSError as exc:
        print(f"state sync: could not write the marker: {exc}", flush=True)
    return 0


def status() -> Dict[str, Any]:
    folder = sync_dir()
    marker = _read_marker()
    return {
        "sync_dir": str(folder) if folder else None,
        "marker": marker,
        "folder_db": _db_stamp(folder) if folder else None,
        "folder_in_use": _in_use(folder) if folder else None,
        "folder_changed": _folder_changed(folder, marker) if folder else None,
    }


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "status"
    force = "--force" in args
    if command == "import" and not force and "--manual" not in args:
        return import_on_start()
    folder = sync_dir()
    if command in ("import", "export"):
        if folder is None:
            print(f"state sync is off: set {SYNC_DIR_ENV} and AGENTS_HUB_STATE_VOLUME")
            return 1
        done, why = (import_state if command == "import" else export_state)(folder, force=force)
        print(why)
        return 0 if done else 1
    print(json.dumps(status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
