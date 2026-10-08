"""
Task snapshots for eval cases: freeze what a task looked like so a case can
replay it later, in isolation.

:func:`snapshot_task` reads a task and returns the ``Case.artifact`` shape::

    {"task_id", "title", "description", "context",
     "documents": [{"name", "text"}], "files": [{"path", "text"}],
     "truncated": bool, "skipped": {"secret": n, "binary": n, "too_large": n}}

:func:`materialize_artifact` writes an artifact's files into a fresh
directory (``<workspace>/.eval/<eval_run_id>/<case_id>``, or a temporary
directory when there is no workspace folder), which the runner hands to the
target as its working directory.

Boundaries. A snapshot copies text a person could have pasted into the case
by hand, and nothing that grants access to anything:

- **copied**: the task's title, description and context fields (priority,
  deadline, the parent task's goal, the blocked reason), the results of the
  tasks it depends on as documents, and a capped slice of the text files in
  the task's project folder;
- **never copied**: secrets (files matched by name: ``.env`` and ``.env.*``,
  ``*.pem``, ``*.key``, ``*.p12``, ``*.pfx``, ``*.keystore``, ``*.jks``,
  ``id_rsa*``, ``id_ed25519*``, ``.netrc``, ``.npmrc``, ``.pypirc``,
  ``credentials*``, ``secrets*``), anything under ``.git`` or another VCS
  folder, the workspace's own settings file (``.workspace.json``), the
  ``.eval`` folder itself, dependency and cache folders, binary files, the
  connections store (it lives in the database, and nothing here reads it),
  API keys, workspace settings and any external state (issues, remote
  repositories, databases, other services). A target that needs those at
  run time reaches them the way it always does, through its own tools, and
  what it reaches is live, not a snapshot.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Folders never walked into.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".eval", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    "dist", "build", ".next", ".cache", "connections",
})

#: File name patterns treated as secrets, matched case-insensitively on the
#: base name.
SECRET_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "*.jks", "*.kdbx", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".netrc",
    ".npmrc", ".pypirc", ".git-credentials", "credentials*", "secrets*",
    "*.secret", "*.secrets",
)

#: Settings files of the workspace itself: never part of a task's material.
SETTINGS_FILES = frozenset({".workspace.json"})

#: One file larger than this is not copied at all, rather than cut.
MAX_FILE_BYTES = 100_000


def is_secret_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in SECRET_PATTERNS)


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def collect_files(root: Path, *, max_files: int = 50, max_bytes: int = 200_000
                  ) -> Dict[str, Any]:
    """Text files under ``root``, secrets and skipped folders left out.

    Walks in sorted order so two snapshots of the same tree agree. Stops at
    ``max_files`` files or ``max_bytes`` total, whichever comes first, and
    says so in ``truncated``.
    """
    files: List[Dict[str, str]] = []
    skipped = {"secret": 0, "binary": 0, "too_large": 0}
    total = 0
    truncated = False
    root = Path(root)
    if not root.is_dir():
        return {"files": files, "skipped": skipped, "truncated": False}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            full = Path(dirpath) / fname
            if full.is_symlink():
                continue
            if fname in SETTINGS_FILES or is_secret_name(fname):
                skipped["secret"] += 1
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                skipped["too_large"] += 1
                continue
            if len(files) >= max_files or total + size > max_bytes:
                truncated = True
                continue
            try:
                data = full.read_bytes()
            except OSError:
                continue
            if not _is_text(data):
                skipped["binary"] += 1
                continue
            rel = PurePosixPath(full.relative_to(root).as_posix())
            files.append({"path": str(rel), "text": data.decode("utf-8")})
            total += size
    return {"files": files, "skipped": skipped, "truncated": truncated}


def _task_context(task: Any) -> str:
    """The context fields of a task, as the lines an agent would be told."""
    from tasks import service as tasks_service

    lines: List[str] = []
    title = str(getattr(task, "title", "") or "").strip()
    if title:
        lines.append(f"Title: {title}")
    key = getattr(task, "key", None)
    if key:
        lines.append(f"Key: {key}")
    priority = getattr(task, "priority", None)
    if priority is not None:
        lines.append(f"Priority: {getattr(priority, 'value', priority)}")
    due = getattr(task, "due_at", None)
    if due is not None:
        lines.append(f"Deadline: {due.isoformat() if hasattr(due, 'isoformat') else due}")
    if getattr(task, "project", None):
        lines.append(f"Project: {task.project}")
    if getattr(task, "blocked_reason", None):
        lines.append(f"Blocked reason: {task.blocked_reason}")
    parent_id = getattr(task, "parent_id", None)
    if parent_id:
        try:
            parent = tasks_service.get_task(parent_id)
        except Exception:  # noqa: BLE001 - context is best effort
            parent = None
        if parent is not None:
            goal = f"Parent task: {parent.title}"
            if parent.description:
                goal += f"\n{parent.description}"
            lines.append(goal)
    return "\n".join(lines)


def _dependency_documents(task: Any, limit_chars: int = 20_000) -> List[Dict[str, str]]:
    from tasks import service as tasks_service

    docs: List[Dict[str, str]] = []
    for dep_id in list(getattr(task, "depends", None) or []):
        try:
            dep = tasks_service.get_task(dep_id)
            result = tasks_service.get_task_result(dep.id) if dep else None
        except Exception:  # noqa: BLE001 - one missing dependency never fails the snapshot
            continue
        if dep is None or not (result or "").strip():
            continue
        docs.append({
            "name": f"Result of {dep.key or str(dep.id)[:8]}: {dep.title}",
            "text": result.strip()[:limit_chars],
        })
    return docs


def _project_root(task: Any) -> Optional[Path]:
    """The task's project folder, or None when the task names no workspace.

    Never the process's working directory: a task without a workspace has
    no files of its own, and copying wherever the server happens to run from
    would copy the server.
    """
    if not getattr(task, "workspace", None):
        return None
    try:
        from workspace import resolve_task_workspace
        ws_name, ws_path = resolve_task_workspace(task)
    except Exception:  # noqa: BLE001 - a task whose folder cannot be found has no files
        return None
    if not ws_name:
        return None
    return Path(ws_path)


def snapshot_task(task_id: str, *, max_files: int = 50, max_bytes: int = 200_000
                  ) -> Dict[str, Any]:
    """A ``Case.artifact`` built from the task ``task_id``.

    See the module docstring for exactly what is copied and what never is.
    Raises ``ValueError`` when the task does not exist.
    """
    from tasks import service as tasks_service

    task = tasks_service.get_task(str(task_id))
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    root = _project_root(task)
    collected = (collect_files(root, max_files=max_files, max_bytes=max_bytes)
                 if root is not None
                 else {"files": [], "skipped": {"secret": 0, "binary": 0, "too_large": 0},
                       "truncated": False})
    return {
        "task_id": str(task.id),
        "title": str(task.title or ""),
        "description": str(task.description or ""),
        "context": _task_context(task),
        "documents": _dependency_documents(task),
        "files": collected["files"],
        "truncated": bool(collected["truncated"]),
        "skipped": collected["skipped"],
    }


def _safe_relpath(path: str) -> Optional[PurePosixPath]:
    """A relative path that stays inside its root, or None."""
    rel = PurePosixPath(str(path or "").replace("\\", "/"))
    if not rel.parts or rel.is_absolute() or any(p in ("..", "") for p in rel.parts):
        return None
    if any(p in SKIP_DIRS for p in rel.parts[:-1]) or is_secret_name(rel.name):
        return None
    return rel


def isolation_dir(workspace: Optional[str], eval_run_id: str, case_id: str,
                  attempt: int = 1) -> Path:
    """Where a case with an artifact runs: ``<workspace>/.eval/<run>/<case>``
    (with ``-<attempt>`` past the first attempt, so repeats never share a
    tree), or a fresh temporary directory when there is no workspace folder."""
    leaf = case_id if attempt <= 1 else f"{case_id}-{attempt}"
    if workspace:
        try:
            from workspace import WORKSPACES_ROOT
            ws_root = (Path(WORKSPACES_ROOT) / workspace).resolve()
            if ws_root.is_dir():
                target = ws_root / ".eval" / eval_run_id / leaf
                target.mkdir(parents=True, exist_ok=True)
                return target
        except Exception:  # noqa: BLE001 - fall back to a temporary directory
            log.debug("eval snapshot directory unavailable", exc_info=True)
    return Path(tempfile.mkdtemp(prefix=f"eval-{eval_run_id}-{leaf}-"))


def materialize_artifact(artifact: Dict[str, Any], directory: Path) -> Path:
    """Write the artifact's files and documents under ``directory``.

    Files keep their relative paths; a path that would escape the directory
    or that names a secret is dropped. Documents go under ``_documents/``.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for item in artifact.get("files") or []:
        rel = _safe_relpath(str((item or {}).get("path") or ""))
        if rel is None:
            continue
        dest = directory.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(str(item.get("text") or ""), encoding="utf-8")
    docs = [d for d in (artifact.get("documents") or []) if isinstance(d, dict)]
    if docs:
        doc_dir = directory / "_documents"
        doc_dir.mkdir(exist_ok=True)
        for i, doc in enumerate(docs, 1):
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                           for ch in str(doc.get("name") or f"document-{i}"))[:80]
            (doc_dir / f"{i:02d}-{safe}.md").write_text(str(doc.get("text") or ""), encoding="utf-8")
    return directory


__all__ = [
    "snapshot_task", "collect_files", "materialize_artifact", "isolation_dir",
    "is_secret_name", "SECRET_PATTERNS", "SKIP_DIRS",
]
