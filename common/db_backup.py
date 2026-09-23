"""
One archive with the whole state in it: ``ah db backup`` / ``restore`` / ``verify``.

``common.db_transfer`` already knows how to copy every table between SQLite
and Postgres; this module is a thin shell around it. A backup always produces
a self-contained SQLite file (``database.sqlite`` inside the archive), never a
``pg_dump``, so restoring needs nothing beyond this checkout and whichever
driver the *target* database happens to need: when the live database is
Postgres, :func:`backup` copies it into a temporary SQLite file with
``db_transfer.transfer`` (the same call ``ah db migrate`` makes); when it is
already SQLite, ``VACUUM INTO`` does the same thing without going through
Python row by row.

Beside the database, the archive optionally holds the state directories that
live next to it in ``AGENTS_HUB_ROOT``: run/node/flow logs, workspaces, view
assets, generated Dockerfiles, imported agent checkouts, and the shared-memory
pools (episodes, graphs, extractions). Nothing else moves: run queue leases,
Redis, and a running process's pid are not part of the state a backup can
restore, and the ``.env`` file with its secrets is never touched.

A ``manifest.json`` at the root of the archive records what was backed up and
from where, so :func:`verify` can check an archive without touching the live
database, and :func:`restore` can compare what it loaded against what the
archive claims to hold.
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from common import db
from common import db_transfer
from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT

# State directories that travel with the database, if present. Anything not
# listed here (run_snapshots/, the *.migrated markers, agents_hub.db* itself)
# is either derived, superseded, or the database file already handled above.
FILE_DIRS = (
    "run_logs", "node_logs", "flow_logs", "workspaces", "views",
    "dockerfiles", "imported_agents", "episodes", "graphs", "extractions",
)

# Directory names never worth archiving wherever they turn up (a workspace's
# own venv or node_modules, a cloned agent's build cache): large, disposable,
# and reproducible with an install rather than a restore.
_EXCLUDE_DIR_NAMES = {"__pycache__", "node_modules", ".venv"}

_ARCHIVE_PREFIX = "files/"
_MANIFEST_NAME = "manifest.json"
_DB_NAME = "database.sqlite"

LogFn = Callable[[str], None]


def _say(log: Optional[LogFn]) -> LogFn:
    return log or (lambda *_: None)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _archive_path(target: Path) -> Path:
    """``target`` itself when it names a file, or a timestamped name inside
    it when it is (or will be) a directory."""
    target = Path(target).expanduser()
    if target.is_dir() or str(target).endswith(("/", "\\")):
        target.mkdir(parents=True, exist_ok=True)
        return target / f"agents_hub_backup_{_timestamp()}.tar.gz"
    return target


def _app_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("agents-hub")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "unknown"


def _sqlite_table_counts(path: Path) -> Dict[str, int]:
    """Row counts of a standalone SQLite file, the same tables ``db_transfer``
    would copy (the ledger and SQLite's own bookkeeping table excepted)."""
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        counts: Dict[str, int] = {}
        for (name,) in rows:
            if name in db_transfer.SKIP_TABLES:
                continue
            counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        return counts
    finally:
        conn.close()


def _dump_database(dest: Path, *, log: LogFn) -> None:
    """Copy the configured database into a fresh, standalone SQLite file at
    ``dest``, whatever backend it actually lives in."""
    if dest.exists():
        dest.unlink()
    if db.dialect() == "sqlite":
        # The live database is already SQLite: a page-level VACUUM INTO is
        # faster than copying every row through Python, and gives the exact
        # same archive shape (a plain SQLite file) as the Postgres path below.
        conn = db.get_conn()
        conn.execute("VACUUM INTO ?", (str(dest),))
        log(f"database.sqlite: copied via VACUUM INTO ({db.DB_FILE})")
    else:
        report = db_transfer.transfer(str(dest), force=True, log=log)
        total = sum(v["target"] for v in report["tables"].values())
        log(f"database.sqlite: copied {total} row(s) from Postgres via transfer")


def _skip_member(rel_parts: tuple) -> bool:
    if any(p in _EXCLUDE_DIR_NAMES for p in rel_parts):
        return True
    name = rel_parts[-1] if rel_parts else ""
    if name.endswith(".migrated"):
        return True
    if name.startswith("agents_hub.db"):
        return True
    return False


def _add_manifest(tar: tarfile.TarFile, manifest: Dict[str, Any]) -> None:
    data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    info = tarfile.TarInfo(name=_MANIFEST_NAME)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    tar.addfile(info, io.BytesIO(data))


def _extract(tar: tarfile.TarFile, member: tarfile.TarInfo, path: Any) -> None:
    """``tar.extract`` with the "data" filter (Python's own recommended,
    safer default from 3.12 on: no device files, no writes outside ``path``,
    ownership left to the caller); older interpreters lack the argument."""
    try:
        tar.extract(member, path=path, filter="data")
    except TypeError:
        tar.extract(member, path=path)


def _read_manifest(tar: tarfile.TarFile) -> Dict[str, Any]:
    try:
        member = tar.getmember(_MANIFEST_NAME)
    except KeyError as exc:
        raise RuntimeError(f"{_MANIFEST_NAME} is missing from the archive") from exc
    f = tar.extractfile(member)
    if f is None:
        raise RuntimeError(f"{_MANIFEST_NAME} has no content")
    try:
        return json.loads(f.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"{_MANIFEST_NAME} is not valid JSON: {exc}") from exc


def backup(target: Path, *, include_files: bool = True, log: Optional[LogFn] = None) -> Dict[str, Any]:
    """Write one ``.tar.gz`` archive holding the database and, by default, the
    state directories beside it. ``target`` is either the archive's own path,
    or a directory to name a timestamped archive inside.

    Returns a report: the archive path, the source the database was copied
    from, its schema version, per-table row counts, and which directories
    were included.
    """
    say = _say(log)
    archive = _archive_path(Path(target))
    archive.parent.mkdir(parents=True, exist_ok=True)

    info = db_transfer.status()
    with tempfile.TemporaryDirectory(prefix="agents_hub_backup_") as tmp:
        dumped = Path(tmp) / _DB_NAME
        _dump_database(dumped, log=say)
        counts = _sqlite_table_counts(dumped)

        included: List[str] = []
        if include_files:
            included = [d for d in FILE_DIRS if (AGENTS_HUB_ROOT / d).is_dir()]

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": _app_version(),
            "source": {"dialect": info["dialect"], "location": info["location"]},
            "schema_version": info["schema_version"],
            "counts": counts,
            "files": included,
        }

        with tarfile.open(archive, "w:gz") as tar:
            _add_manifest(tar, manifest)
            tar.add(str(dumped), arcname=_DB_NAME)
            def _filter(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
                rel = Path(tarinfo.name).parts[1:]  # drop "files/<dir>"
                return None if _skip_member(rel) else tarinfo

            for name in included:
                say(f"{name}/: adding")
                tar.add(str(AGENTS_HUB_ROOT / name), arcname=f"{_ARCHIVE_PREFIX}{name}", filter=_filter)

    return {
        "archive": str(archive),
        "manifest": manifest,
    }


def _extract_files(tar: tarfile.TarFile, *, log: LogFn) -> int:
    """Extract every ``files/...`` member into ``AGENTS_HUB_ROOT``, overwriting
    what is already there and deleting nothing else. Returns files written."""
    root = AGENTS_HUB_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for member in tar.getmembers():
        if not member.name.startswith(_ARCHIVE_PREFIX):
            continue
        rel = member.name[len(_ARCHIVE_PREFIX):]
        if not rel:
            continue
        dest = (root / rel).resolve()
        if root not in dest.parents and dest != root:
            # Defensive only: archives here are our own, but a hand-edited one
            # must never be able to write outside AGENTS_HUB_ROOT.
            continue
        member.name = rel
        _extract(tar, member, root)
        if member.isfile():
            n += 1
    return n


def restore(archive: Path, *, force: bool = False, include_files: bool = True,
            log: Optional[LogFn] = None) -> Dict[str, Any]:
    """Load ``archive`` (as written by :func:`backup`) into the database this
    process is configured with, and, by default, extract its files into
    ``AGENTS_HUB_ROOT``.

    Refuses when the configured database already holds rows, unless ``force``.
    Returns a report comparing what was loaded against the archive's manifest.
    """
    say = _say(log)
    archive = Path(archive)

    current = db_transfer.status()
    busy = [t for t, n in current["counts"].items() if t != "meta" and n]
    if busy and not force:
        raise RuntimeError(
            f"the configured database already holds rows in {busy}; pass --force to overwrite it")

    with tarfile.open(archive, "r:gz") as tar:
        manifest = _read_manifest(tar)
        try:
            db_member = tar.getmember(_DB_NAME)
        except KeyError as exc:
            raise RuntimeError(f"the archive has no {_DB_NAME}") from exc

        with tempfile.TemporaryDirectory(prefix="agents_hub_restore_") as tmp:
            _extract(tar, db_member, tmp)
            dumped = Path(tmp) / _DB_NAME

            target_ref = str(db.DB_FILE) if db.dialect() == "sqlite" else db.database_url()
            transfer_report = db_transfer.transfer(target_ref, source=str(dumped), force=True, log=say)

        files_restored = 0
        if include_files:
            files_restored = _extract_files(tar, log=say)

    mismatches = {}
    manifest_counts = manifest.get("counts", {})
    for table, want in manifest_counts.items():
        got = transfer_report["tables"].get(table, {}).get("target")
        if got != want:
            mismatches[table] = {"manifest": want, "restored": got}

    return {
        "archive": str(archive),
        "manifest": manifest,
        "tables": transfer_report["tables"],
        "files_restored": files_restored,
        "mismatches": mismatches,
    }


def verify(archive: Path) -> Dict[str, Any]:
    """Open ``archive`` and check it without touching the live database: the
    manifest parses, ``database.sqlite`` opens, and its row counts match what
    the manifest claims."""
    archive = Path(archive)
    with tarfile.open(archive, "r:gz") as tar:
        manifest = _read_manifest(tar)
        try:
            db_member = tar.getmember(_DB_NAME)
        except KeyError as exc:
            raise RuntimeError(f"the archive has no {_DB_NAME}") from exc
        with tempfile.TemporaryDirectory(prefix="agents_hub_verify_") as tmp:
            _extract(tar, db_member, tmp)
            dumped = Path(tmp) / _DB_NAME
            try:
                counts = _sqlite_table_counts(dumped)
            except Exception as exc:
                raise RuntimeError(f"{_DB_NAME} will not open: {exc}") from exc

    manifest_counts = manifest.get("counts", {})
    mismatches = {}
    for table in sorted(set(manifest_counts) | set(counts)):
        want = manifest_counts.get(table)
        got = counts.get(table)
        if want != got:
            mismatches[table] = {"manifest": want, "actual": got}

    return {
        "archive": str(archive),
        "manifest": manifest,
        "counts": counts,
        "ok": not mismatches,
        "mismatches": mismatches,
    }


__all__ = ["backup", "restore", "verify", "FILE_DIRS"]
