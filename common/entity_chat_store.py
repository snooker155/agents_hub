"""
Persistence for entity build chats.

A scenario, a loop — anything with a chat of its own on its page — needs the
conversation to survive a reload, and needs it to survive the *entity* being
edited from the form next to it. So the transcript lives here rather than on the
entity: saving a scenario must not reset the chat that helped write it.

Three things are stored per (kind, entity_id):

* ``messages`` — the plain user/assistant transcript, which is what the next
  turn's prompt is built from.
* ``trace``    — the rich display feed (thinking + tool steps interleaved with
  the turns) the UI replays on reload, so a reopened page shows the session as
  it happened rather than a bare conversation.
* ``session_epoch`` — the chat-session generation. "Clear chat" bumps it, which
  makes the next turn open a new backend session instead of appending to the
  previous one, keeping each thread separate in Messages.
* ``epoch_high`` — the highest epoch ever handed out, which is how the UI knows
  this chat has a past even when the only other thread was empty and dropped.
* ``sessions`` — the finished threads. Starting a new session files the live one
  here instead of dropping it, so the page can list what was said before and
  reopen any of it. Reopening is a swap, not a copy: the chosen thread becomes
  the live one (epoch included), so the next turn continues it in Messages under
  the conversation id it already had.

Storage is a single JSON object keyed by ``"<kind>:<entity_id>"`` behind an
OS-level file lock — the same approach as
:class:`projects.graph_store.ProjectGraphStore`, which is the store this one
generalises.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock

from common.paths import ENTITY_CHATS_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: How many finished threads one entity keeps. Every session holds its whole
#: display trace, and the store is a single JSON file that is rewritten on each
#: turn, so the history is bounded rather than allowed to grow forever.
MAX_ARCHIVED_SESSIONS = 30

#: How much of the opening message becomes a session's label in the picker.
TITLE_CHARS = 70


class EntityChatStore:
    """File-based store mapping (kind, entity_id) → one build-chat session."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or ENTITY_CHATS_FILE)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({})

    @staticmethod
    def _key(kind: str, entity_id: str) -> str:
        return f"{kind}:{entity_id}"

    @staticmethod
    def _blank(kind: str) -> Dict[str, Any]:
        return {"kind": kind, "messages": [], "trace": [], "session_epoch": 0,
                "epoch_high": 0, "sessions": []}

    @staticmethod
    def session_id(epoch: int) -> str:
        """A thread's stable id. Epochs never repeat within one entity."""
        return f"s{int(epoch)}"

    @staticmethod
    def _title(messages: List[dict]) -> str:
        """A thread's label: what the user opened it with."""
        for m in messages:
            if m.get("role") == "user" and (m.get("content") or "").strip():
                text = " ".join((m["content"] or "").split())
                return text[:TITLE_CHARS] + ("…" if len(text) > TITLE_CHARS else "")
        return ""

    @classmethod
    def _summary(cls, session: Dict[str, Any], active: bool) -> Dict[str, Any]:
        messages = list(session.get("messages") or [])
        epoch = int(session.get("session_epoch", session.get("epoch")) or 0)
        return {
            "id": cls.session_id(epoch),
            "epoch": epoch,
            "active": active,
            "title": cls._title(messages),
            "messages": len(messages),
            "started_at": session.get("started_at")
            or (messages[0].get("at") if messages else None),
            "updated_at": session.get("updated_at"),
        }

    @staticmethod
    def _epoch_high(entry: Dict[str, Any]) -> int:
        """The highest epoch this chat has ever handed out.

        Kept on the entry as well as derived from it, because the derivation
        alone forgets: an empty thread is never archived, so a chat that went
        "thread 0, new chat, reopen thread 0" would look like it had only ever
        had one. That is the difference between a chat that offers its history
        and one where the control vanishes the moment you use it.
        """
        epochs = [0]
        for value in [entry.get("epoch_high"), entry.get("session_epoch")] + [
            s.get("epoch") for s in (entry.get("sessions") or [])
        ]:
            try:
                epochs.append(int(value or 0))
            except (TypeError, ValueError):
                continue
        return max(epochs)

    @classmethod
    def _next_epoch(cls, entry: Dict[str, Any]) -> int:
        """One past every epoch this entity has ever used.

        Not ``session_epoch + 1``: reopening an older thread makes an older epoch
        the live one again, and a plain increment would then collide with a
        thread already in the archive.
        """
        nxt = cls._epoch_high(entry) + 1
        entry["epoch_high"] = nxt
        return nxt

    @classmethod
    def _has_history(cls, entry: Dict[str, Any]) -> bool:
        """Has this chat been more than one conversation?

        What the History control keys off. True from the first "New chat"
        onwards, including while the user is reading one of the old threads and
        the only other one was empty and dropped.
        """
        return bool(entry.get("sessions")) or cls._epoch_high(entry) > 0

    @classmethod
    def _archive_active(cls, entry: Dict[str, Any]) -> None:
        """File the live thread under ``sessions``. An empty one is dropped.

        Replaces any archived thread with the same epoch, which is what makes
        reopening idempotent: a thread put back live and then left again returns
        to its own slot instead of being duplicated.
        """
        messages = list(entry.get("messages") or [])
        trace = list(entry.get("trace") or [])
        if not messages and not trace:
            return
        epoch = int(entry.get("session_epoch") or 0)
        kept = [s for s in (entry.get("sessions") or [])
                if int(s.get("epoch") or 0) != epoch]
        kept.append({
            "epoch": epoch,
            "messages": messages,
            "trace": trace,
            "started_at": (messages[0].get("at") if messages else None) or _now(),
            "updated_at": entry.get("updated_at") or _now(),
        })
        entry["sessions"] = kept[-MAX_ARCHIVED_SESSIONS:]

    # ── reads ───────────────────────────────────────────────────────────────

    def get_messages(self, kind: str, entity_id: str, timeout: float = 10.0) -> List[dict]:
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(kind, entity_id)) or {}
            return list(entry.get("messages") or [])

    def get_trace(self, kind: str, entity_id: str, timeout: float = 10.0) -> List[dict]:
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(kind, entity_id)) or {}
            return list(entry.get("trace") or [])

    def get_session_epoch(self, kind: str, entity_id: str, timeout: float = 10.0) -> int:
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(kind, entity_id)) or {}
            try:
                return int(entry.get("session_epoch") or 0)
            except (TypeError, ValueError):
                return 0

    def history(self, kind: str, entity_id: str,
                timeout: float = 10.0) -> Dict[str, Any]:
        """This chat's threads, and whether it has a history at all.

        Summaries only (title, size, timestamps): the list needs those, and the
        transcripts behind them are large enough that sending them all would
        cost more than the whole feature saves.

        ``has_history`` is not ``len(sessions) > 1``. A chat being read from one
        of its old threads may have exactly one thread left and still be a chat
        with a past, and that is the moment the control must not disappear.
        """
        with FileLock(str(self.lock_path), timeout=timeout):
            entry = self._load_unlocked().get(self._key(kind, entity_id))
        if not entry:
            return {"sessions": [], "has_history": False}
        archived = sorted(
            (self._summary(s, active=False) for s in (entry.get("sessions") or [])),
            key=lambda s: (s.get("updated_at") or "", s.get("epoch") or 0),
            reverse=True,
        )
        return {
            "sessions": [self._summary(entry, active=True)] + archived,
            "has_history": self._has_history(entry),
        }

    def list_sessions(self, kind: str, entity_id: str,
                      timeout: float = 10.0) -> List[Dict[str, Any]]:
        """Every thread this entity has: the live one first, then the archive."""
        return self.history(kind, entity_id, timeout=timeout)["sessions"]

    def has_chat(self, kind: str, entity_id: str, timeout: float = 10.0) -> bool:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._key(kind, entity_id) in self._load_unlocked()

    # ── writes ──────────────────────────────────────────────────────────────

    def append_message(self, kind: str, entity_id: str, role: str, content: str,
                       timeout: float = 10.0) -> Dict[str, Any]:
        """Append one chat turn, creating the session if it does not exist yet."""
        msg = {"role": role, "content": content, "at": _now()}
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(kind, entity_id)
            entry = data.get(key) or self._blank(kind)
            entry.setdefault("messages", []).append(msg)
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)
        return msg

    def append_trace(self, kind: str, entity_id: str, items: List[dict],
                     timeout: float = 10.0) -> None:
        """Append one turn's display items. Called when a run completes — which
        happens even if the client disconnected mid-run, so the session is whole
        when the user comes back."""
        if not items:
            return
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(kind, entity_id)
            entry = data.get(key) or self._blank(kind)
            entry["trace"] = (entry.get("trace") or []) + list(items)
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)

    def clear(self, kind: str, entity_id: str, new_session: bool = True,
              timeout: float = 10.0) -> int:
        """Start a fresh thread; returns the (possibly bumped) session epoch.

        With ``new_session`` the thread being left is filed under ``sessions``
        rather than destroyed, so "Clear" is now "start a new chat" and the
        picker can bring this one back. Without it the transcript is genuinely
        dropped, which is what a caller resetting an entity wants.
        """
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(kind, entity_id)
            entry = data.get(key)
            if not entry:
                return 0
            if new_session:
                self._archive_active(entry)
                entry["session_epoch"] = self._next_epoch(entry)
            entry["messages"] = []
            entry["trace"] = []
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)
            return int(entry.get("session_epoch") or 0)

    def activate_session(self, kind: str, entity_id: str, session_id: str,
                         timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """Reopen an archived thread as the live one, filing the current one.

        The epoch travels with the thread, so the next turn lands in the backend
        session that thread already had: the user continues the conversation
        instead of starting a lookalike beside it.

        Returns the restored transcript, or ``None`` when there is no such
        thread.
        """
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(kind, entity_id)
            entry = data.get(key)
            if not entry:
                return None
            current = int(entry.get("session_epoch") or 0)
            if session_id == self.session_id(current):
                return {"messages": list(entry.get("messages") or []),
                        "trace": list(entry.get("trace") or []),
                        "session_epoch": current}
            sessions = list(entry.get("sessions") or [])
            chosen = next((s for s in sessions
                           if self.session_id(int(s.get("epoch") or 0)) == session_id), None)
            if chosen is None:
                return None
            self._archive_active(entry)
            entry["sessions"] = [s for s in (entry.get("sessions") or [])
                                 if int(s.get("epoch") or 0) != int(chosen.get("epoch") or 0)]
            entry["messages"] = list(chosen.get("messages") or [])
            entry["trace"] = list(chosen.get("trace") or [])
            entry["session_epoch"] = int(chosen.get("epoch") or 0)
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)
            return {"messages": list(entry["messages"]),
                    "trace": list(entry["trace"]),
                    "session_epoch": int(entry["session_epoch"])}

    def delete_session(self, kind: str, entity_id: str, session_id: str,
                       timeout: float = 10.0) -> bool:
        """Drop one archived thread. The live one is not deletable this way."""
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(kind, entity_id)
            entry = data.get(key)
            if not entry or session_id == self.session_id(int(entry.get("session_epoch") or 0)):
                return False
            sessions = list(entry.get("sessions") or [])
            kept = [s for s in sessions
                    if self.session_id(int(s.get("epoch") or 0)) != session_id]
            if len(kept) == len(sessions):
                return False
            entry["sessions"] = kept
            entry["updated_at"] = _now()
            data[key] = entry
            self._atomic_write(data)
            return True

    def delete(self, kind: str, entity_id: str, timeout: float = 10.0) -> bool:
        """Drop an entity's chat entirely — called when the entity is deleted."""
        with FileLock(str(self.lock_path), timeout=timeout):
            data = self._load_unlocked()
            key = self._key(kind, entity_id)
            if key in data:
                del data[key]
                self._atomic_write(data)
                return True
            return False

    # ── file io ─────────────────────────────────────────────────────────────

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


#: The process-wide store. One JSON file, so one instance is enough and the
#: routes do not each build their own.
_store: Optional[EntityChatStore] = None


def entity_chat_store() -> EntityChatStore:
    global _store
    if _store is None:
        _store = EntityChatStore()
    return _store


__all__ = ["EntityChatStore", "entity_chat_store"]
