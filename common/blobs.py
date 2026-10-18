"""
State files under ``AGENTS_HUB_ROOT``, addressed by a root-relative key.

Everything under ``AGENTS_HUB_ROOT`` (run logs, flow logs, view assets,
generated Dockerfiles) lives on this one host, and every process that reads
or writes it shares the directory. This module is the one place the callers
go through for those files, keyed by a path *relative* to ``AGENTS_HUB_ROOT``
as a posix string (``run_logs/agent_run_<id>.log``, never an absolute path).
:func:`rel` builds that key from an absolute path.

None of this ever raises. A missing or unreadable file is logged at debug and
treated as "the file is not there": a read must never turn into a failed run.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger("common.blobs")


def rel(path: Any) -> str:
    """Turn a path under ``AGENTS_HUB_ROOT`` into its key (posix, relative).

    A relative path is treated as already being under the root. Raises
    ``ValueError`` for a path that resolves outside it, so a caller can never
    accidentally read something from outside state.
    """
    p = Path(path)
    if not p.is_absolute():
        p = AGENTS_HUB_ROOT / p
    p = p.resolve()
    root = AGENTS_HUB_ROOT.resolve()
    try:
        return p.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path is not under AGENTS_HUB_ROOT: {path!r}") from exc


class BlobStore:
    """The file operations, each taking a root-relative key."""

    def configured(self) -> bool:
        """Always False: files live on this host's disk and nowhere else."""
        return False

    def mirror(self, rel_path: str) -> bool:
        """Nothing to mirror anywhere; kept for the writers that call it."""
        return False

    def ensure_local(self, rel_path: str) -> Optional[Path]:
        p = AGENTS_HUB_ROOT / rel_path
        return p if p.is_file() else None

    def read_text(self, rel_path: str, default: Optional[str] = None) -> Optional[str]:
        p = AGENTS_HUB_ROOT / rel_path
        try:
            return p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - best-effort read, module contract: never raise
            log.debug("read_text failed for %s", rel_path, exc_info=True)
            return default

    def read_bytes(self, rel_path: str) -> Optional[bytes]:
        p = AGENTS_HUB_ROOT / rel_path
        try:
            return p.read_bytes()
        except Exception:  # noqa: BLE001 - best-effort read, module contract: never raise
            log.debug("read_bytes failed for %s", rel_path, exc_info=True)
            return None

    def exists(self, rel_path: str) -> bool:
        return (AGENTS_HUB_ROOT / rel_path).is_file()

    def delete(self, rel_path: str) -> None:
        try:
            (AGENTS_HUB_ROOT / rel_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - best-effort cleanup, module contract: never raise
            log.debug("delete failed for %s", rel_path, exc_info=True)

    def list(self, prefix: str) -> List[str]:
        base = AGENTS_HUB_ROOT / prefix
        if not base.is_dir():
            return []
        out: List[str] = []
        for f in base.rglob("*"):
            if f.is_file():
                out.append(rel(f))
        return sorted(out)


#: The historical name of the one backend, kept for the callers and tests
#: that spell it out.
LocalBlobStore = BlobStore

_store_instance: Optional[BlobStore] = None


def store() -> BlobStore:
    """The process-wide store."""
    global _store_instance
    if _store_instance is None:
        _store_instance = BlobStore()
    return _store_instance


def reset() -> None:
    """Drop the cached store so the next call rebuilds it. Tests only."""
    global _store_instance
    _store_instance = None


# ── public functions, keyed by a root-relative path ─────────────────────────

def configured() -> bool:
    """Always False: there is no remote store in this edition."""
    return False


def mirror(rel_path: str) -> bool:
    """A no-op that returns False: files are only ever on this host."""
    return False


def ensure_local(rel_path: str) -> Optional[Path]:
    """The local path for ``rel_path``, or None when the file is not there."""
    try:
        return store().ensure_local(rel_path)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("ensure_local failed for %s", rel_path, exc_info=True)
        return None


def read_text(rel_path: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return store().read_text(rel_path, default)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("read_text failed for %s", rel_path, exc_info=True)
        return default


def read_bytes(rel_path: str) -> Optional[bytes]:
    try:
        return store().read_bytes(rel_path)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("read_bytes failed for %s", rel_path, exc_info=True)
        return None


def exists(rel_path: str) -> bool:
    try:
        return store().exists(rel_path)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("exists() failed for %s", rel_path, exc_info=True)
        return False


def delete(rel_path: str) -> None:
    try:
        store().delete(rel_path)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("delete failed for %s", rel_path, exc_info=True)


def list(prefix: str) -> List[str]:  # noqa: A001 - the natural name for this API
    try:
        return store().list(prefix)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("list failed for prefix %s", prefix, exc_info=True)
        return []


__all__ = [
    "BlobStore",
    "LocalBlobStore",
    "rel",
    "store",
    "reset",
    "configured",
    "mirror",
    "ensure_local",
    "read_text",
    "read_bytes",
    "exists",
    "delete",
    "list",
]
