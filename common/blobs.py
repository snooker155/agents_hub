"""
Mirror of on-disk state files into an object store.

Everything under ``AGENTS_HUB_ROOT`` (run logs, node logs, flow logs, view
assets, generated Dockerfiles) is today only on the host that wrote it: a
single SQLite-backed deployment shares one host anyway (docs/scaling.md), so
this has never mattered. It starts to matter once ``AGENTS_HUB_DATABASE_URL``
puts the database in Postgres and workers or backend replicas run on other
hosts (docs/workers.md): a run can finish on host A and be read from a
dashboard on host B. This module is the fix for the *files*, the same way
``common/db.py`` is the fix for the database.

Two backends, chosen by :data:`AGENTS_HUB_BLOB_URL` (empty by default):

- :class:`LocalBlobStore` — a no-op mirror. Every file already lives under
  ``AGENTS_HUB_ROOT`` on this one host, so there is nothing to copy anywhere;
  reads are served straight off disk.
- :class:`S3BlobStore` — an S3-compatible bucket (real AWS S3, or MinIO via
  ``AGENTS_HUB_BLOB_ENDPOINT``). A writer mirrors its file after writing it; a
  reader falls back to downloading a file it does not have locally.

Every function here is keyed by a path *relative* to ``AGENTS_HUB_ROOT``, as a
posix string (``run_logs/agent_run_<id>.log``, never an absolute path), so the
same key names a file on disk and an object in the store. :func:`rel` builds
that key from an absolute path.

None of this ever raises. A store failure (network, credentials, a missing
key) is logged at debug and treated as "the file is not there" — an object
store outage must never turn into a failed run or a broken read. See
docs/storage.md for the operating picture.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger("common.blobs")

BLOB_URL_ENV = "AGENTS_HUB_BLOB_URL"
BLOB_ENDPOINT_ENV = "AGENTS_HUB_BLOB_ENDPOINT"


def rel(path: Any) -> str:
    """Turn a path under ``AGENTS_HUB_ROOT`` into its store key (posix, relative).

    A relative path is treated as already being under the root. Raises
    ``ValueError`` for a path that resolves outside it, so a caller can never
    accidentally mirror or fetch something from outside state.
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
    """What a backend provides. Every method takes/returns a root-relative key."""

    def configured(self) -> bool:
        """True when a remote store is set (mirroring/fetching is meaningful)."""
        raise NotImplementedError

    def mirror(self, rel_path: str) -> bool:
        """Upload the local file at ``rel_path``, if it exists. True on success."""
        raise NotImplementedError

    def ensure_local(self, rel_path: str) -> Optional[Path]:
        """Return the local path if present; else fetch it from the store and
        return that path; else None."""
        raise NotImplementedError

    def read_text(self, rel_path: str, default: Optional[str] = None) -> Optional[str]:
        raise NotImplementedError

    def read_bytes(self, rel_path: str) -> Optional[bytes]:
        raise NotImplementedError

    def exists(self, rel_path: str) -> bool:
        raise NotImplementedError

    def delete(self, rel_path: str) -> None:
        """Remove the local file and the store object, if either exists."""
        raise NotImplementedError

    def list(self, prefix: str) -> List[str]:
        """Root-relative keys under ``prefix`` known to the store (sorted)."""
        raise NotImplementedError


class LocalBlobStore(BlobStore):
    """The default: no remote store, so nothing is mirrored anywhere.

    Every file already lives under ``AGENTS_HUB_ROOT`` on the one host every
    process shares, so reads are answered straight off disk and writes are a
    no-op. This is what every single-host deployment (the default, and every
    deployment before this module existed) keeps using.
    """

    def configured(self) -> bool:
        return False

    def mirror(self, rel_path: str) -> bool:
        return False

    def ensure_local(self, rel_path: str) -> Optional[Path]:
        p = AGENTS_HUB_ROOT / rel_path
        return p if p.is_file() else None

    def read_text(self, rel_path: str, default: Optional[str] = None) -> Optional[str]:
        p = AGENTS_HUB_ROOT / rel_path
        try:
            return p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - best-effort read, module contract: never raise
            log.debug("local read_text failed for %s", rel_path, exc_info=True)
            return default

    def read_bytes(self, rel_path: str) -> Optional[bytes]:
        p = AGENTS_HUB_ROOT / rel_path
        try:
            return p.read_bytes()
        except Exception:  # noqa: BLE001 - best-effort read, module contract: never raise
            log.debug("local read_bytes failed for %s", rel_path, exc_info=True)
            return None

    def exists(self, rel_path: str) -> bool:
        return (AGENTS_HUB_ROOT / rel_path).is_file()

    def delete(self, rel_path: str) -> None:
        try:
            (AGENTS_HUB_ROOT / rel_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - best-effort cleanup, module contract: never raise
            log.debug("local delete failed for %s", rel_path, exc_info=True)

    def list(self, prefix: str) -> List[str]:
        base = AGENTS_HUB_ROOT / prefix
        if not base.is_dir():
            return []
        out: List[str] = []
        for f in base.rglob("*"):
            if f.is_file():
                out.append(rel(f))
        return sorted(out)


class S3BlobStore(BlobStore):
    """An S3-compatible bucket, addressed by ``s3://bucket/prefix``.

    ``boto3`` is imported lazily, inside :meth:`_get_client`, so a deployment
    that never sets ``AGENTS_HUB_BLOB_URL`` never needs the package installed
    (mirrors ``common/broker_bridge.py``'s lazy ``redis`` import). ``endpoint``
    points the client at a MinIO (or other S3-compatible) server instead of
    real AWS; region and credentials come from the usual AWS env vars.
    ``client`` lets a test inject a fake client instead of talking to a real
    service.
    """

    def __init__(self, url: str, *, endpoint: Optional[str] = None, client: Any = None) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"blob url must look like s3://bucket/prefix, got {url!r}")
        self._bucket = parsed.netloc
        self._prefix = parsed.path.strip("/")
        self._endpoint = endpoint or None
        self._client_override = client
        self._client: Any = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import boto3  # local import: optional dependency (requirements-blobs.txt)

                    kwargs: dict = {}
                    if self._endpoint:
                        kwargs["endpoint_url"] = self._endpoint
                    self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key(self, rel_path: str) -> str:
        tail = str(rel_path).strip("/")
        return f"{self._prefix}/{tail}" if self._prefix else tail

    def _strip_prefix(self, key: str) -> str:
        if self._prefix and key.startswith(self._prefix + "/"):
            return key[len(self._prefix) + 1:]
        return key

    def configured(self) -> bool:
        return True

    def mirror(self, rel_path: str) -> bool:
        local = AGENTS_HUB_ROOT / rel_path
        if not local.is_file():
            return False
        try:
            self._get_client().upload_file(str(local), self._bucket, self._key(rel_path))
            return True
        except Exception:  # noqa: BLE001 - best-effort mirror, module contract: never raise
            log.debug("blob mirror failed for %s", rel_path, exc_info=True)
            return False

    def ensure_local(self, rel_path: str) -> Optional[Path]:
        local = AGENTS_HUB_ROOT / rel_path
        if local.is_file():
            return local
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            self._get_client().download_file(self._bucket, self._key(rel_path), str(local))
            return local if local.is_file() else None
        except Exception:  # noqa: BLE001 - best-effort fetch, module contract: never raise
            log.debug("blob ensure_local failed for %s", rel_path, exc_info=True)
            return None

    def read_text(self, rel_path: str, default: Optional[str] = None) -> Optional[str]:
        p = self.ensure_local(rel_path)
        if p is None:
            return default
        try:
            return p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - best-effort read, module contract: never raise
            log.debug("s3 read_text failed for %s", rel_path, exc_info=True)
            return default

    def read_bytes(self, rel_path: str) -> Optional[bytes]:
        p = self.ensure_local(rel_path)
        if p is None:
            return None
        try:
            return p.read_bytes()
        except Exception:  # noqa: BLE001 - best-effort read, module contract: never raise
            log.debug("s3 read_bytes failed for %s", rel_path, exc_info=True)
            return None

    def exists(self, rel_path: str) -> bool:
        if (AGENTS_HUB_ROOT / rel_path).is_file():
            return True
        try:
            self._get_client().head_object(Bucket=self._bucket, Key=self._key(rel_path))
            return True
        except Exception:  # noqa: BLE001 - best-effort check, module contract: never raise
            log.debug("blob head_object failed for %s", rel_path, exc_info=True)
            return False

    def delete(self, rel_path: str) -> None:
        try:
            (AGENTS_HUB_ROOT / rel_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - best-effort cleanup, module contract: never raise
            log.debug("local delete failed for %s", rel_path, exc_info=True)
        try:
            self._get_client().delete_object(Bucket=self._bucket, Key=self._key(rel_path))
        except Exception:  # noqa: BLE001 - best-effort cleanup, module contract: never raise
            log.debug("blob delete failed for %s", rel_path, exc_info=True)

    def list(self, prefix: str) -> List[str]:
        out: List[str] = []
        try:
            client = self._get_client()
        except Exception:  # noqa: BLE001 - best-effort listing, module contract: never raise
            log.debug("blob list: no client for prefix %s", prefix, exc_info=True)
            return out
        key_prefix = self._key(prefix)
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": key_prefix}
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = client.list_objects_v2(**kwargs)
            except Exception:  # noqa: BLE001 - best-effort listing, module contract: never raise
                log.debug("blob list failed for prefix %s", prefix, exc_info=True)
                break
            for item in resp.get("Contents") or []:
                key = item.get("Key") or ""
                if key:
                    out.append(self._strip_prefix(key))
            if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                token = resp["NextContinuationToken"]
            else:
                break
        return sorted(out)


# ── singleton, chosen from settings ─────────────────────────────────────────

_store_lock = threading.Lock()
_store_instance: Optional[BlobStore] = None


def _build_store() -> BlobStore:
    from common.config import live_setting, settings

    url = live_setting(BLOB_URL_ENV, settings.blob_url)
    if not url:
        return LocalBlobStore()
    endpoint = live_setting(BLOB_ENDPOINT_ENV, settings.blob_endpoint) or None
    try:
        return S3BlobStore(url, endpoint=endpoint)
    except ValueError:
        log.warning("invalid %s %r; falling back to local storage", BLOB_URL_ENV, url, exc_info=True)
        return LocalBlobStore()


def store() -> BlobStore:
    """The process-wide blob store, built once from ``AGENTS_HUB_BLOB_URL``."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = _build_store()
    return _store_instance


def reset() -> None:
    """Drop the cached store so the next call rebuilds it from current settings.

    Tests only: a real process's blob backend does not change mid-run.
    """
    global _store_instance
    with _store_lock:
        _store_instance = None


# ── public functions, keyed by a root-relative path ─────────────────────────

def configured() -> bool:
    """True when a remote store is set (``AGENTS_HUB_BLOB_URL`` is non-empty)."""
    try:
        return store().configured()
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("configured() failed", exc_info=True)
        return False


def mirror(rel_path: str) -> bool:
    """Upload the local file at ``rel_path`` to the store, best-effort.

    A no-op (returns False) on the local backend, when the file does not
    exist locally, or on any store failure.
    """
    try:
        return store().mirror(rel_path)
    except Exception:  # noqa: BLE001 - best-effort, module contract: never raise
        log.debug("mirror failed for %s", rel_path, exc_info=True)
        return False


def ensure_local(rel_path: str) -> Optional[Path]:
    """Return the local path for ``rel_path``, fetching it from the store first
    if it is not already there. None if it exists nowhere."""
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
    "S3BlobStore",
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
