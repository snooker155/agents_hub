"""
Workspace files: a file uploaded once and referenced by its id everywhere.

Before this module every surface that took a file kept its own copy of it: a
chat attachment travelled as base64 in each request, a memory pool's knowledge
file lived in the workspace's ``knowledge/`` folder, an eval case carried a
text snapshot, and a task could not carry a file at all. The same document
used in three places was three uploads with nothing tying them together. This
is the Files API shape of the Anthropic and OpenAI platforms: upload once, get
back ``file_<16 hex>``, and pass the id to a chat turn, a memory pool, a task
or an eval case.

A record (one row of ``workspace_files``, migration 0021) belongs to exactly
one workspace. Its content lives under the state root at
``files/<workspace>/<file_id>/<safe name>`` and is mirrored to the object
store through ``common/blobs.py``, so a worker or a replica on another host
reads it back with :func:`local_path` / :func:`read_bytes` the same way it
reads a run log. Uploading the same bytes into the same workspace returns the
record that already exists (same sha256), so a file attached to ten chat turns
is still one file.

Two limits, both read live from ``.env`` like the other operator settings
(``common.config.live_setting``): ``AGENTS_HUB_FILES_MAX_FILE_MB`` per file and
``AGENTS_HUB_FILES_MAX_WORKSPACE_MB`` for everything one workspace holds.

A file an agent writes into the workspace folder with its filesystem tools
(``write_file``, ``create_file``, ``apply_unified_diff``) is a workspace file
too, without a copy: :func:`register_path` gives the file at
``workspaces/<workspace>/<path>`` a record whose ``storage_key`` is that path,
so the Files page lists what agents produce next to what people upload, and
the same id keeps pointing at the file after the agent rewrites it (the
record is updated in place). :func:`index_workspace` walks a whole workspace
folder the same way, for files written before the registry existed or by a
process that bypasses the tools (Claude Code, a shell), and tombstones the
records of files that are gone.

Deleting a file keeps its row as a tombstone (``deleted_at`` set) and removes
the content, locally and in the object store. An old chat turn, a citation or
an audit row can still say which file it was; nothing can read it any more.

Text for a prompt (:func:`text_for_prompt`) covers plain text, markdown, JSON,
CSV and source code, and PDF through ``pypdf`` (the library the RAG ingest in
``dashboard/backend/rag/service.py`` already uses, with the same ``PyPDF2``
fallback) when it is installed. Anything else (an image, an archive) is named
with its type and size, never inlined.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from common import blobs, db
from common.config import DEFAULT_IGNORE
from common.paths import AGENTS_HUB_ROOT

log = logging.getLogger(__name__)

#: Top folder under the state root that holds every workspace's file content.
FILES_DIR = "files"

#: Top folder under the state root that holds the workspace folders, the
#: agents' working directories (``common.paths.WORKSPACES_ROOT``). A record
#: whose ``storage_key`` starts with it is the file in the folder itself.
WORKSPACES_DIR = "workspaces"

#: Folders of a workspace the registry never indexes: version control, caches
#: and virtual environments (``DEFAULT_IGNORE``) plus build output. Hidden
#: folders and files (a leading dot) are skipped too: the hub keeps its own
#: bookkeeping there (``.logs``, ``.views``, ``.patch_backups``, ``.plans``).
INDEX_SKIP_DIRS = frozenset(DEFAULT_IGNORE) | {"venv", "env", "dist", "build", "target"}

#: Top folders of a workspace whose files come from somewhere other than an
#: agent's tool call, and the source they are registered with.
FOLDER_SOURCES = {"knowledge": "memory", "chat_uploads": "chat", "task_files": "task"}

#: ``file_`` plus 16 hex characters, the same length as the other hub ids.
FILE_ID_RE = re.compile(r"^file_[0-9a-f]{16}$")

MAX_FILE_MB_ENV = "AGENTS_HUB_FILES_MAX_FILE_MB"
MAX_WORKSPACE_MB_ENV = "AGENTS_HUB_FILES_MAX_WORKSPACE_MB"
DEFAULT_MAX_FILE_MB = 25
DEFAULT_MAX_WORKSPACE_MB = 1024

#: Where a file came from. Informational (the Files page filters on it); an
#: unknown value is stored as given rather than refused.
SOURCES = ("upload", "chat", "agent", "memory", "task", "eval", "api")

#: Extensions read as text whatever the guessed MIME type says. Covers the
#: formats the brief names (text, markdown, JSON, CSV, code) plus the config
#: formats agents are routinely handed.
TEXT_EXTENSIONS = frozenset({
    ".txt", ".text", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".xml", ".html", ".htm", ".css", ".scss", ".sql", ".graphql",
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".java",
    ".kt", ".kts", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".cs", ".swift", ".m", ".scala", ".lua", ".pl", ".r", ".sh",
    ".bash", ".zsh", ".ps1", ".bat", ".vue", ".svelte", ".tex", ".proto",
    ".dockerfile", ".gradle", ".properties", ".env.example",
})

#: MIME types (beyond ``text/*``) whose bytes are text.
TEXT_MIME_TYPES = frozenset({
    "application/json", "application/xml", "application/x-yaml", "application/yaml",
    "application/javascript", "application/x-javascript", "application/typescript",
    "application/x-sh", "application/sql", "application/toml", "application/x-ndjson",
    "application/ld+json", "image/svg+xml",
})

#: A few extensions the platform's ``mimetypes`` table does not always know.
_EXTRA_MIME = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".yaml": "application/yaml",
    ".yml": "application/yaml", ".jsonl": "application/x-ndjson", ".ts": "text/x-typescript",
    ".tsx": "text/x-typescript", ".jsx": "text/javascript", ".toml": "application/toml",
    ".csv": "text/csv", ".log": "text/plain", ".rst": "text/x-rst",
}

_MIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")


class FileError(Exception):
    """A refusal the caller can show as is; ``status`` is the HTTP answer."""
    status = 400


class FileTooLarge(FileError):
    status = 413


class WorkspaceQuotaExceeded(FileError):
    status = 413


# ── settings ─────────────────────────────────────────────────────────────────

def _limit_mb(env_key: str, default: int) -> float:
    try:
        from common.config import live_setting
        return max(0.0, float(live_setting(env_key, str(default))))
    except (TypeError, ValueError):
        return float(default)


def max_file_bytes() -> int:
    """Largest single file, in bytes (``AGENTS_HUB_FILES_MAX_FILE_MB``)."""
    return int(_limit_mb(MAX_FILE_MB_ENV, DEFAULT_MAX_FILE_MB) * 1024 * 1024)


def max_workspace_bytes() -> int:
    """Everything one workspace may hold, in bytes (``AGENTS_HUB_FILES_MAX_WORKSPACE_MB``)."""
    return int(_limit_mb(MAX_WORKSPACE_MB_ENV, DEFAULT_MAX_WORKSPACE_MB) * 1024 * 1024)


def limits() -> Dict[str, int]:
    return {"max_file_bytes": max_file_bytes(), "max_workspace_bytes": max_workspace_bytes()}


# ── names ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_file_id() -> str:
    return f"file_{uuid.uuid4().hex[:16]}"


def _workspace_name(workspace: Optional[str]) -> str:
    """The bare workspace name, or a refusal: a file always belongs to one."""
    raw = str(workspace or "").strip()
    name = Path(raw).name if raw else ""
    if not name or name in (".", "..") or name != raw:
        raise FileError("a workspace file needs a workspace (a plain workspace name)")
    return name


def display_name(name: Optional[str], fallback: str = "file") -> str:
    """The name a person sees: the last path component, without control
    characters, at most 200 characters. Unicode is kept."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    if base in ("", ".", ".."):
        base = fallback
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 200 - len(ext) - 1] + dot + ext) if dot and len(ext) <= 16 else base[:200]
    return base


def disk_name(name: Optional[str], fallback: str = "file") -> str:
    """A name safe to create on disk and hand to a shell: letters (any
    script), digits, ``.``, ``-`` and ``_``; everything else becomes ``_``."""
    base = display_name(name, fallback)
    safe = re.sub(r"[^\w.\-]+", "_", base).strip("._") or fallback
    if len(safe) > 120:
        stem, dot, ext = safe.rpartition(".")
        safe = (stem[: 120 - len(ext) - 1] + dot + ext) if dot and len(ext) <= 16 else safe[:120]
    return safe


def guess_mime(name: str, given: Optional[str] = None) -> str:
    """The given MIME type when it is well formed, else a guess from the name."""
    value = str(given or "").split(";", 1)[0].strip().lower()
    if value and _MIME_RE.match(value) and value != "application/octet-stream":
        return value
    ext = Path(str(name or "")).suffix.lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _enc = mimetypes.guess_type(str(name or ""))
    return guessed or value or "application/octet-stream"


# ── rows ─────────────────────────────────────────────────────────────────────

_COLUMNS = ("file_id", "workspace", "name", "mime_type", "size", "sha256", "storage_key",
            "source", "created_by", "created_at", "deleted_at", "meta")


def _row_to_record(row: Any, *, internal: bool = False) -> Dict[str, Any]:
    meta = db.loads(row["meta"], {}) if row["meta"] else {}
    record = {
        "file_id": row["file_id"],
        "workspace": row["workspace"],
        "name": row["name"],
        "mime_type": row["mime_type"] or "application/octet-stream",
        "size": int(row["size"] or 0),
        "sha256": row["sha256"],
        "source": row["source"] or "upload",
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "meta": meta if isinstance(meta, dict) else {},
    }
    if row["deleted_at"]:
        record["deleted_at"] = row["deleted_at"]
    if internal:
        record["storage_key"] = row["storage_key"]
    return record


def _fetch(file_id: str, *, include_deleted: bool = False) -> Optional[Any]:
    if not file_id or not FILE_ID_RE.match(str(file_id)):
        return None
    sql = "SELECT * FROM workspace_files WHERE file_id = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    return db.get_conn().execute(sql, (str(file_id),)).fetchone()


def get_file(file_id: str, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    """The record, or None when it does not exist or was deleted (unless
    ``include_deleted``, which returns the tombstone with ``deleted_at``)."""
    row = _fetch(file_id, include_deleted=include_deleted)
    return _row_to_record(row) if row else None


def get_files(file_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """The live records for ``file_ids``, in the order given; unknown or
    deleted ids are skipped."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for fid in file_ids or []:
        fid = str(fid or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        record = get_file(fid)
        if record is not None:
            out.append(record)
    return out


def find_duplicate(workspace: str, sha256: str) -> Optional[Dict[str, Any]]:
    """The live file in ``workspace`` with exactly these bytes, if any."""
    row = db.get_conn().execute(
        "SELECT * FROM workspace_files WHERE workspace = ? AND sha256 = ? AND deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1",
        (str(workspace), str(sha256)),
    ).fetchone()
    return _row_to_record(row) if row else None


def workspace_usage(workspace: str) -> int:
    """Bytes the live files of ``workspace`` hold."""
    row = db.get_conn().execute(
        "SELECT COALESCE(SUM(size), 0) FROM workspace_files WHERE workspace = ? AND deleted_at IS NULL",
        (str(workspace),),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"


def list_files(workspace: str, *, source: Optional[str] = None, q: Optional[str] = None,
               limit: int = 200) -> List[Dict[str, Any]]:
    """The live files of ``workspace``, newest first, optionally narrowed to a
    ``source`` and to names containing ``q`` (case-insensitive; an exact file
    id also matches)."""
    where = ["workspace = ?", "deleted_at IS NULL"]
    args: List[Any] = [str(workspace)]
    if source:
        where.append("source = ?")
        args.append(str(source))
    text = str(q or "").strip()
    if text:
        where.append("(LOWER(name) LIKE ? ESCAPE '\\' OR file_id = ?)")
        args.extend([_like_pattern(text), text])
    limit = max(1, min(int(limit or 200), 1000))
    rows = db.get_conn().execute(
        f"SELECT * FROM workspace_files WHERE {' AND '.join(where)} "
        "ORDER BY created_at DESC, file_id LIMIT ?",
        tuple(args) + (limit,),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


# ── create / read / delete ───────────────────────────────────────────────────

def create_file(workspace: str, name: str, data: bytes, *, mime_type: Optional[str] = None,
                source: str = "upload", created_by: Optional[str] = None,
                meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Store ``data`` as a file of ``workspace`` and return its record.

    The same bytes already stored in the same workspace return that record
    unchanged (its name, source and meta stay what the first upload set).
    Raises :class:`FileTooLarge` past the per-file limit and
    :class:`WorkspaceQuotaExceeded` when the workspace would pass its total.
    """
    ws = _workspace_name(workspace)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray)):
        raise FileError("file content must be bytes")
    data = bytes(data)
    size = len(data)
    per_file = max_file_bytes()
    if size > per_file:
        raise FileTooLarge(
            f"'{display_name(name)}' is {size} bytes; the limit is {per_file} bytes per file "
            f"({MAX_FILE_MB_ENV})")
    digest = hashlib.sha256(data).hexdigest()
    existing = find_duplicate(ws, digest)
    if existing is not None:
        return existing
    quota = max_workspace_bytes()
    used = workspace_usage(ws)
    if used + size > quota:
        raise WorkspaceQuotaExceeded(
            f"workspace '{ws}' holds {used} bytes of files; adding {size} would pass its "
            f"limit of {quota} bytes ({MAX_WORKSPACE_MB_ENV})")

    file_id = new_file_id()
    shown = display_name(name)
    storage_key = f"{FILES_DIR}/{ws}/{file_id}/{disk_name(shown)}"
    path = AGENTS_HUB_ROOT / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    record_meta = dict(meta or {})
    row = (
        file_id, ws, shown, guess_mime(shown, mime_type), size, digest, storage_key,
        str(source or "upload"), (str(created_by) if created_by else None), _now(), None,
        json.dumps(record_meta, ensure_ascii=False, default=str),
    )
    try:
        with db.transaction() as conn:
            conn.execute(
                f"INSERT INTO workspace_files ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                row,
            )
    except Exception:
        # The row is the record; content with no row is an orphan nobody can
        # name, so it goes with the failed insert.
        shutil.rmtree(path.parent, ignore_errors=True)
        raise
    blobs.mirror(storage_key)
    return get_file(file_id) or _row_to_record(dict(zip(_COLUMNS, row)))


def _storage_key(file_id: str) -> str:
    row = _fetch(file_id)
    if row is None:
        raise KeyError(file_id)
    return str(row["storage_key"])


def local_path(file_id: str) -> Path:
    """A readable local copy of the file's content, fetched from the object
    store first when this host does not have it. ``KeyError`` when the file
    is unknown, deleted, or its content is nowhere."""
    key = _storage_key(file_id)
    path = blobs.ensure_local(key)
    if path is None:
        raise KeyError(file_id)
    return Path(path)


def read_bytes(file_id: str) -> bytes:
    """The file's content. ``KeyError`` when missing (see :func:`local_path`)."""
    key = _storage_key(file_id)
    data = blobs.read_bytes(key)
    if data is None:
        raise KeyError(file_id)
    return data


def delete_file(file_id: str) -> bool:
    """Mark the file deleted and remove its content. False when there was no
    live file with this id."""
    row = _fetch(file_id)
    if row is None:
        return False
    with db.transaction() as conn:
        conn.execute("UPDATE workspace_files SET deleted_at = ? WHERE file_id = ? AND deleted_at IS NULL",
                     (_now(), str(file_id)))
    key = str(row["storage_key"])
    blobs.delete(key)
    if key.startswith(f"{FILES_DIR}/"):
        # A copied file has a folder of its own; a folder-backed file sits in
        # the agent's working tree, whose empty folders are the agent's.
        try:
            folder = (AGENTS_HUB_ROOT / key).parent
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            log.debug("files: could not remove the empty folder of %s", file_id, exc_info=True)
    return True


# ── files in the workspace folder ────────────────────────────────────────────

def workspace_rel_path(rel_path: str) -> str:
    """``rel_path`` as a clean workspace-relative posix path (no leading
    slash, no ``.`` or ``..`` parts), or a refusal."""
    raw = str(rel_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise FileError("a workspace path is relative to the workspace folder")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        raise FileError("a workspace path stays inside the workspace folder")
    return "/".join(parts)


def is_indexable(rel_path: str) -> bool:
    """Whether a workspace path is one the registry follows: nothing hidden,
    nothing under a skipped folder (see ``INDEX_SKIP_DIRS``)."""
    try:
        parts = workspace_rel_path(rel_path).split("/")
    except FileError:
        return False
    return not any(p.startswith(".") or p in INDEX_SKIP_DIRS for p in parts)


def _path_key(ws: str, rel: str) -> str:
    return f"{WORKSPACES_DIR}/{ws}/{rel}"


def _fetch_by_key(ws: str, key: str) -> Optional[Any]:
    return db.get_conn().execute(
        "SELECT * FROM workspace_files WHERE workspace = ? AND storage_key = ? AND deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1",
        (ws, key),
    ).fetchone()


def file_at_path(workspace: str, rel_path: str) -> Optional[Dict[str, Any]]:
    """The live record of the workspace file at ``rel_path``, if registered."""
    ws = _workspace_name(workspace)
    row = _fetch_by_key(ws, _path_key(ws, workspace_rel_path(rel_path)))
    return _row_to_record(row) if row else None


def _register_path(ws: str, rel: str, *, source: str, created_by: Optional[str],
                   meta: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], str]:
    """:func:`register_path` plus what happened: ``added``, ``updated`` or
    ``unchanged``."""
    key = _path_key(ws, rel)
    path = AGENTS_HUB_ROOT / key
    if path.is_symlink() or not path.is_file():
        raise FileError(f"'{rel}' is not a regular file of workspace '{ws}'")
    stat = path.stat()
    size = int(stat.st_size)
    per_file = max_file_bytes()
    if size > per_file:
        raise FileTooLarge(
            f"'{rel}' is {size} bytes; the limit is {per_file} bytes per file ({MAX_FILE_MB_ENV})")
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    shown = display_name(rel.rsplit("/", 1)[-1])
    mime = guess_mime(shown)
    written_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    row = _fetch_by_key(ws, key)
    if row is not None:
        record = _row_to_record(row)
        merged = {**record["meta"], **(meta or {}), "path": rel}
        if (record["sha256"] == sha and record["size"] == size and record["name"] == shown
                and record["mime_type"] == mime and merged == record["meta"]):
            return record, "unchanged"
        with db.transaction() as conn:
            conn.execute(
                "UPDATE workspace_files SET name = ?, mime_type = ?, size = ?, sha256 = ?, meta = ? "
                "WHERE file_id = ?",
                (shown, mime, size, sha, json.dumps(merged, ensure_ascii=False, default=str),
                 record["file_id"]),
            )
        blobs.mirror(key)
        return get_file(record["file_id"]) or record, "updated"

    quota = max_workspace_bytes()
    used = workspace_usage(ws)
    if used + size > quota:
        raise WorkspaceQuotaExceeded(
            f"workspace '{ws}' holds {used} bytes of files; adding {size} would pass its "
            f"limit of {quota} bytes ({MAX_WORKSPACE_MB_ENV})")
    file_id = new_file_id()
    values = (
        file_id, ws, shown, mime, size, sha, key, str(source or "agent"),
        (str(created_by) if created_by else None), written_at, None,
        json.dumps({**(meta or {}), "path": rel}, ensure_ascii=False, default=str),
    )
    with db.transaction() as conn:
        conn.execute(
            f"INSERT INTO workspace_files ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))})",
            values,
        )
    blobs.mirror(key)
    return get_file(file_id) or _row_to_record(dict(zip(_COLUMNS, values))), "added"


def register_path(workspace: str, rel_path: str, *, source: str = "agent",
                  created_by: Optional[str] = None,
                  meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make the file at ``<workspace folder>/rel_path`` a workspace file
    without copying it: the record's content is the file in the folder.

    Registering the path again after the file changed updates the record in
    place (name, type, size, hash; ``meta`` merged in, ``meta.path`` always
    the workspace-relative path), so the id a chat turn or a task already
    holds keeps pointing at the file. ``created_at`` is when the file was
    last written. Raises :class:`FileError` when the path leaves the
    workspace or is not a regular file, and the two limit errors like
    :func:`create_file`.
    """
    ws = _workspace_name(workspace)
    rel = workspace_rel_path(rel_path)
    record, _status = _register_path(ws, rel, source=source, created_by=created_by, meta=meta)
    return record


def write_folder_file(workspace: str, rel_path: str, data: bytes, *, source: str = "upload",
                      created_by: Optional[str] = None) -> Dict[str, Any]:
    """Put ``data`` at ``<workspace folder>/rel_path`` and register it.

    An upload that names a path lands in the folder, where the agents' file
    tools see it, instead of the file store; the record is the one
    :func:`register_path` keeps for folder files, so an existing file at the
    path is replaced and its record updated in place. The limits are checked
    before anything is written, so a refused upload leaves the folder as it
    was. Raises :class:`FileError` for a path that leaves the workspace.
    """
    ws = _workspace_name(workspace)
    rel = workspace_rel_path(rel_path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = bytes(data)
    size = len(data)
    per_file = max_file_bytes()
    if size > per_file:
        raise FileTooLarge(
            f"'{rel}' is {size} bytes; the limit is {per_file} bytes per file ({MAX_FILE_MB_ENV})")
    path = AGENTS_HUB_ROOT / _path_key(ws, rel)
    previous = int(path.stat().st_size) if path.is_file() and not path.is_symlink() else 0
    quota = max_workspace_bytes()
    used = workspace_usage(ws)
    if used - previous + size > quota:
        raise WorkspaceQuotaExceeded(
            f"workspace '{ws}' holds {used} bytes of files; adding {size} would pass its "
            f"limit of {quota} bytes ({MAX_WORKSPACE_MB_ENV})")
    if path.is_symlink() or path.is_dir():
        raise FileError(f"'{rel}' is not a regular file of workspace '{ws}'")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    record, _status = _register_path(ws, rel, source=source, created_by=created_by, meta=None)
    return record


def unregister_path(workspace: str, rel_path: str) -> bool:
    """Tombstone the record of a workspace file deleted from the folder. The
    content is gone already, so only the row changes. False when the path
    had no live record."""
    ws = _workspace_name(workspace)
    key = _path_key(ws, workspace_rel_path(rel_path))
    row = _fetch_by_key(ws, key)
    if row is None:
        return False
    with db.transaction() as conn:
        conn.execute("UPDATE workspace_files SET deleted_at = ? WHERE file_id = ? AND deleted_at IS NULL",
                     (_now(), str(row["file_id"])))
    blobs.delete(key)
    return True


def _folder_source(rel: str) -> str:
    return FOLDER_SOURCES.get(rel.split("/", 1)[0], "agent")


def index_workspace(workspace: str, *, created_by: Optional[str] = None) -> Dict[str, Any]:
    """Register every file of the workspace folder and tombstone the records
    of folder files that are gone.

    Walks ``workspaces/<workspace>`` skipping hidden entries and
    ``INDEX_SKIP_DIRS``; a file under ``knowledge/`` gets source ``memory``,
    under ``chat_uploads/`` ``chat``, under ``task_files/`` ``task``, anything
    else ``agent`` (``FOLDER_SOURCES``). A file past the per-file limit or
    the workspace quota is listed under ``skipped`` with the reason. Returns
    ``{workspace, added, updated, unchanged, removed, skipped: [{path, reason}]}``.
    """
    ws = _workspace_name(workspace)
    root = AGENTS_HUB_ROOT / WORKSPACES_DIR / ws
    if not root.is_dir():
        raise FileError(f"workspace '{ws}' has no folder")
    summary: Dict[str, Any] = {"workspace": ws, "added": 0, "updated": 0, "unchanged": 0,
                               "removed": 0, "skipped": []}
    seen: set = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in INDEX_SKIP_DIRS)
        for name in sorted(filenames):
            if name.startswith(".") or name in INDEX_SKIP_DIRS:
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                _record, status = _register_path(ws, rel, source=_folder_source(rel),
                                                 created_by=created_by, meta=None)
            except FileError as exc:
                summary["skipped"].append({"path": rel, "reason": str(exc)})
                continue
            seen.add(_path_key(ws, rel))
            summary[status] += 1
    prefix = f"{WORKSPACES_DIR}/{ws}/"
    rows = db.get_conn().execute(
        "SELECT file_id, storage_key FROM workspace_files WHERE workspace = ? AND deleted_at IS NULL",
        (ws,),
    ).fetchall()
    gone = [str(r["file_id"]) for r in rows
            if str(r["storage_key"]).startswith(prefix) and str(r["storage_key"]) not in seen]
    if gone:
        now = _now()
        with db.transaction() as conn:
            for fid in gone:
                conn.execute("UPDATE workspace_files SET deleted_at = ? WHERE file_id = ? AND deleted_at IS NULL",
                             (now, fid))
        summary["removed"] = len(gone)
    return summary


# ── uses that only a chat turn knows about ──────────────────────────────────

def record_use(file_id: str, kind: str, ref_id: str, label: Optional[str] = None) -> None:
    """Remember that ``kind``/``ref_id`` (a chat conversation) used the file.
    Best-effort and idempotent: a failure here never fails the turn."""
    if not file_id or not kind or not ref_id:
        return
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO workspace_file_uses (file_id, kind, ref_id, label, at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (file_id, kind, ref_id) DO NOTHING",
                (str(file_id), str(kind), str(ref_id), (str(label)[:200] if label else None), _now()),
            )
    except Exception:  # noqa: BLE001 - where-used bookkeeping must not break the turn that attached the file
        log.debug("files: could not record a %s use of %s", kind, file_id, exc_info=True)


def list_uses(file_id: str, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM workspace_file_uses WHERE file_id = ?"
    args: List[Any] = [str(file_id)]
    if kind:
        sql += " AND kind = ?"
        args.append(str(kind))
    rows = db.get_conn().execute(sql + " ORDER BY at DESC", tuple(args)).fetchall()
    return [{"kind": r["kind"], "ref_id": r["ref_id"], "label": r["label"], "at": r["at"]}
            for r in rows]


# ── text ─────────────────────────────────────────────────────────────────────

def is_pdf(record: Dict[str, Any]) -> bool:
    return (record.get("mime_type") == "application/pdf"
            or Path(str(record.get("name") or "")).suffix.lower() == ".pdf")


def is_text(record: Dict[str, Any], head: Optional[bytes] = None) -> bool:
    """Whether the file's bytes are text: by extension, by MIME type, or (for
    an unknown type) by the first bytes decoding as UTF-8 without a NUL."""
    name = str(record.get("name") or "").lower()
    ext = Path(name).suffix
    mime = str(record.get("mime_type") or "")
    if ext in TEXT_EXTENSIONS or name in ("dockerfile", "makefile", "readme", "license"):
        return True
    if mime.startswith("text/") or mime in TEXT_MIME_TYPES:
        return True
    if head is None or mime not in ("", "application/octet-stream"):
        return False
    sample = head[:8192]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut by the sample's end is still text.
        return exc.start >= len(sample) - 3
    return True


def _pdf_text(path: Path) -> Optional[str]:
    """Text of a PDF with ``pypdf`` (``PyPDF2`` as a fallback), the same
    libraries the RAG ingest reads PDFs with; None when neither is installed
    or the file will not parse."""
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(path))
    except ImportError:
        try:
            import PyPDF2 as pypdf  # type: ignore  # noqa: N813
            reader = pypdf.PdfReader(str(path))
        except ImportError:
            return None
        except Exception:  # noqa: BLE001 - a broken PDF reads as "no text", not as a failed turn
            log.debug("files: PyPDF2 could not open %s", path, exc_info=True)
            return None
    except Exception:  # noqa: BLE001 - a broken PDF reads as "no text", not as a failed turn
        log.debug("files: pypdf could not open %s", path, exc_info=True)
        return None
    try:
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # noqa: BLE001 - same as above: unreadable pages are "no text"
        log.debug("files: could not extract text from %s", path, exc_info=True)
        return None


def extract_text(file_id: str) -> Optional[str]:
    """The file's text for a prompt or an agent tool, or None for a binary
    file (or a PDF with no PDF library installed). ``KeyError`` when the
    file is missing."""
    record = get_file(file_id)
    if record is None:
        raise KeyError(file_id)
    if is_pdf(record):
        return _pdf_text(local_path(file_id))
    data = read_bytes(file_id)
    if not is_text(record, data):
        return None
    return data.decode("utf-8", errors="replace")


def _size_label(size: int) -> str:
    size = int(size or 0)
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def describe(record: Dict[str, Any]) -> str:
    """One line naming a file without its content: name, id, type and size."""
    return (f"{record.get('name')} ({record.get('file_id')}, "
            f"{record.get('mime_type') or 'application/octet-stream'}, {_size_label(record.get('size') or 0)})")


def text_for_prompt(file_ids: List[str], *, budget_chars: int = 40_000) -> str:
    """``File: name (id)`` blocks with each file's text, for a prompt.

    ``budget_chars`` caps the text of all files together: the file that
    crosses it is cut with a marker, and files after it are named only. A
    binary file is always named with its type and size instead of inlined; a
    missing or deleted id says so.
    """
    remaining = max(0, int(budget_chars))
    blocks: List[str] = []
    for fid in file_ids or []:
        fid = str(fid or "").strip()
        if not fid:
            continue
        record = get_file(fid)
        if record is None:
            blocks.append(f"File: {fid}\n[missing: this file was deleted or never existed]")
            continue
        header = f"File: {record['name']} ({fid})"
        try:
            text = extract_text(fid)
        except KeyError:
            blocks.append(f"{header}\n[missing: the content of this file is not available]")
            continue
        if text is None:
            kind = "PDF" if is_pdf(record) else "binary"
            blocks.append(f"{header}\n[{kind} file, {record['mime_type']}, "
                          f"{_size_label(record['size'])}: not inlined]")
            continue
        if remaining <= 0:
            blocks.append(f"{header}\n[not inlined: the text budget for attached files is used up]")
            continue
        if len(text) > remaining:
            text = text[:remaining] + "\n...[truncated]"
            remaining = 0
        else:
            remaining -= len(text)
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)


# ── copies on disk ───────────────────────────────────────────────────────────

def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_target(dest: Path, name: str, sha256: str) -> Path:
    """Where a file named ``name`` lands in ``dest``: that name when free or
    already holding these exact bytes, else ``stem-2.ext``, ``stem-3.ext``..."""
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = dest / name
    n = 2
    while candidate.exists():
        if candidate.is_file():
            try:
                if _sha256_of(candidate) == sha256:
                    return candidate
            except OSError:
                log.debug("files: could not hash %s", candidate, exc_info=True)
        candidate = dest / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def locate_copy(record: Dict[str, Any], dest_dir: Any) -> Optional[Path]:
    """The copy :func:`materialize` made of ``record`` in ``dest_dir`` (the
    file there with the same bytes, under its name or a suffixed one), or
    None when there is none."""
    dest = Path(dest_dir)
    if not dest.is_dir():
        return None
    target = _free_target(dest, disk_name(record["name"]), record["sha256"])
    return target if target.exists() else None


def materialize(file_ids: List[str], dest_dir: Any) -> List[Path]:
    """Copy the files into ``dest_dir`` under safe names and return the paths.

    Never overwrites: a name already taken by other content gets a ``-2``,
    ``-3``... suffix, and a name already holding the same bytes is reused (a
    task run started twice does not collect copies). Unknown, deleted or
    unreadable ids are skipped (logged), so one missing file does not stop
    a run that has others.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    for fid in file_ids or []:
        record = get_file(str(fid or "").strip())
        if record is None:
            log.info("files: %s is missing or deleted; not copied to %s", fid, dest)
            continue
        try:
            source = local_path(record["file_id"])
        except KeyError:
            log.warning("files: the content of %s is not available; not copied", fid)
            continue
        target = _free_target(dest, disk_name(record["name"]), record["sha256"])
        if not target.exists():
            shutil.copyfile(source, target)
        if target not in out:
            out.append(target)
    return out


__all__ = [
    "FileError", "FileTooLarge", "WorkspaceQuotaExceeded",
    "create_file", "get_file", "get_files", "list_files", "read_bytes", "local_path",
    "delete_file", "materialize", "locate_copy", "text_for_prompt", "extract_text", "describe",
    "find_duplicate", "workspace_usage", "limits", "max_file_bytes", "max_workspace_bytes",
    "record_use", "list_uses", "display_name", "disk_name", "guess_mime", "is_text", "is_pdf",
    "new_file_id", "FILE_ID_RE", "SOURCES",
    "register_path", "unregister_path", "index_workspace", "file_at_path", "is_indexable",
    "workspace_rel_path", "WORKSPACES_DIR", "INDEX_SKIP_DIRS", "FOLDER_SOURCES",
]
