"""
Persistence for project structure graphs.

A saved graph is a hand-edited or AI-generated ``{nodes, edges}`` for one
project + view. It is stored so it survives the deterministic rebuild: when a
saved graph exists the API serves it instead of re-deriving from source. The
deterministic builder ([projects.graph]) remains the "reset to auto" path.

Storage is a single JSON object keyed by ``"<project_id>:<view>"``, guarded by
an OS-level file lock (same approach as [projects.storage.ProjectStore]).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock

from common.paths import PROJECT_GRAPHS_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectGraphStore:
    """File-based store mapping (project_id, view) → saved graph."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or PROJECT_GRAPHS_FILE)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({})

    @staticmethod
    def _key(project_id: str, view: str) -> str:
        return f"{project_id}:{view}"

    def get(self, project_id: str, view: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked().get(self._key(project_id, view))

    def save(self, project_id: str, view: str, nodes: List[dict], edges: List[dict],
             source: str = "manual", timeout: float = 10.0) -> Dict[str, Any]:
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            prev = data.get(self._key(project_id, view)) or {}
            entry = {
                "view": view,
                "nodes": nodes,
                "edges": edges,
                "source": source,            # "auto" | "manual" | "generated"
                # Preserve the interactive chat transcript + session epoch + the
                # rich display trace across graph saves (a graph edit must not
                # reset the conversation or its tool/thinking history).
                "messages": prev.get("messages", []),
                "trace": prev.get("trace", []),
                "session_epoch": prev.get("session_epoch", 0),
                "updated_at": _now(),
            }
            data[self._key(project_id, view)] = entry
            self._atomic_write(data)
        return entry

    def get_messages(self, project_id: str, view: str, timeout: float = 10.0) -> List[dict]:
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(project_id, view)) or {}
            return list(entry.get("messages") or [])

    def append_message(self, project_id: str, view: str, role: str, content: str,
                       timeout: float = 10.0) -> Dict[str, Any]:
        """Append one chat turn, creating the entry if needed (graph stays empty)."""
        msg = {"role": role, "content": content, "at": _now()}
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(project_id, view)
            entry = data.get(key) or {"view": view, "nodes": [], "edges": [],
                                      "source": "auto", "messages": []}
            entry.setdefault("messages", []).append(msg)
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)
        return msg

    def get_trace(self, project_id: str, view: str, timeout: float = 10.0) -> List[dict]:
        """The rich display trace (user/assistant turns + thinking + tool/graph
        steps) the chat UI replays on reload, so the full session survives."""
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(project_id, view)) or {}
            return list(entry.get("trace") or [])

    def append_trace(self, project_id: str, view: str, items: List[dict],
                     timeout: float = 10.0) -> None:
        """Append one turn's display items to the stored trace.

        The architect-chat worker calls this when a run completes (it runs to the
        end even if the client disconnected), so the full session — thinking and
        tool/graph steps included — is persisted server-side and survives reload.
        """
        if not items:
            return
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(project_id, view)
            entry = data.get(key) or {"view": view, "nodes": [], "edges": [],
                                      "source": "auto", "messages": []}
            entry["trace"] = (entry.get("trace") or []) + list(items)
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)

    def get_session_epoch(self, project_id: str, view: str, timeout: float = 10.0) -> int:
        """The current chat-session generation for this view.

        The architect-chat backend session id is derived from this counter, so
        bumping it (see ``clear_messages``) makes the next turn open a brand-new
        session instead of appending to the previous one. Starts at 0 (the legacy
        un-suffixed session) and increments on every "clear & new session".
        """
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(project_id, view)) or {}
            try:
                return int(entry.get("session_epoch") or 0)
            except (TypeError, ValueError):
                return 0

    def clear_messages(self, project_id: str, view: str, new_session: bool = True,
                       timeout: float = 10.0) -> int:
        """Clear the chat transcript; returns the (possibly bumped) session epoch.

        When ``new_session`` (the default), also advance the session epoch so the
        next chat turn starts a fresh backend session — letting the user genuinely
        start over and keeping each run-thread separate in the Messages view.
        """
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            entry = data.get(self._key(project_id, view))
            if not entry:
                return 0
            entry["messages"] = []
            entry["trace"] = []
            if new_session:
                try:
                    entry["session_epoch"] = int(entry.get("session_epoch") or 0) + 1
                except (TypeError, ValueError):
                    entry["session_epoch"] = 1
            entry["updated_at"] = _now()
            self._atomic_write(data)
            return int(entry.get("session_epoch") or 0)

    def delete(self, project_id: str, view: str, timeout: float = 10.0) -> bool:
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            if self._key(project_id, view) in data:
                del data[self._key(project_id, view)]
                self._atomic_write(data)
                return True
            return False

    def delete_project(self, project_id: str, timeout: float = 10.0) -> int:
        """Remove all saved views for a project (used on project delete)."""
        prefix = f"{project_id}:"
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            keys = [k for k in data if k.startswith(prefix)]
            for k in keys:
                del data[k]
            if keys:
                self._atomic_write(data)
            return len(keys)

    def _load_unlocked(self) -> Dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return {}
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _atomic_write(self, payload: Dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)
