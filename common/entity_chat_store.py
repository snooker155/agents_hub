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
* ``switch_to`` — a thread a turn asked to reopen once it ends (the assistant's
  ``assistant_conversations`` tool): a turn cannot swap the thread it is
  writing into, so the route that ran it does the swap afterwards.

Storage is a :class:`common.docstore.DocStore` collection, one document per
``"<kind>:<entity_id>"``, so a read-modify-write is atomic across processes and
hosts under ``store.transaction()`` instead of the OS-level file lock this used
to take. An existing ``entity_chats.json`` is imported once on first use and
renamed ``.migrated``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.docstore import DocStore
from common.paths import ENTITY_CHATS_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: How many finished threads one entity keeps. Every session holds its whole
#: display trace, and the store is rewritten on each turn, so the history is
#: bounded rather than allowed to grow forever.
MAX_ARCHIVED_SESSIONS = 30

#: How much of the opening message becomes a session's label in the picker.
TITLE_CHARS = 70


class EntityChatStore:
    """Store mapping (kind, entity_id) -> one build-chat session, in the database."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else ENTITY_CHATS_FILE
        self.docs = DocStore("entity_chats", legacy_file=self.path)

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
            "workspaces": cls._workspaces(messages),
            "workspace": cls._thread_workspace(session),
        }

    @staticmethod
    def _workspaces(messages: List[dict]) -> List[str]:
        """The workspaces a thread's turns ran in, in the order first used.
        Only a chat whose turns can run elsewhere than its home (the
        assistant) stamps its messages; the rest give an empty list."""
        out: List[str] = []
        for m in messages:
            ws = m.get("workspace")
            if ws and ws not in out:
                out.append(ws)
        return out

    @classmethod
    def _thread_workspace(cls, session: Dict[str, Any], unstamped: Optional[str] = None) -> Optional[str]:
        """The one workspace a thread belongs to, for a chat whose
        conversations each stay in one (the assistant): where its first turn
        ran, else the workspace an empty live thread was opened for, else
        ``unstamped`` for a thread from before turns were stamped."""
        messages = session.get("messages") or []
        stamped = cls._workspaces(messages)
        if stamped:
            return stamped[0]
        return session.get("workspace") or (unstamped if messages else None)

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
            **({"workspace": entry["workspace"]} if entry.get("workspace") else {}),
        })
        entry["sessions"] = kept[-MAX_ARCHIVED_SESSIONS:]

    # ── reads ───────────────────────────────────────────────────────────────

    def get_messages(self, kind: str, entity_id: str, timeout: float = 10.0) -> List[dict]:
        entry = self.docs.get(self._key(kind, entity_id)) or {}
        return list(entry.get("messages") or [])

    def get_trace(self, kind: str, entity_id: str, timeout: float = 10.0) -> List[dict]:
        entry = self.docs.get(self._key(kind, entity_id)) or {}
        return list(entry.get("trace") or [])

    def get_session_epoch(self, kind: str, entity_id: str, timeout: float = 10.0) -> int:
        entry = self.docs.get(self._key(kind, entity_id)) or {}
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
        entry = self.docs.get(self._key(kind, entity_id))
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

    def threads(self, kind: str, entity_id: str) -> List[Dict[str, Any]]:
        """Every thread with its transcript, the live one first: what the
        Chat page shows as text (the assistant's threads, routes/chats.py).
        Empty threads are left out."""
        entry = self.docs.get(self._key(kind, entity_id))
        if not entry:
            return []
        out = []
        for session, active in [(entry, True)] + [(s, False) for s in (entry.get("sessions") or [])]:
            messages = list(session.get("messages") or [])
            if not messages:
                continue
            out.append({**self._summary(session, active=active), "transcript": messages,
                        "updated_at": session.get("updated_at") or messages[-1].get("at")})
        return out

    def list_sessions(self, kind: str, entity_id: str,
                      timeout: float = 10.0) -> List[Dict[str, Any]]:
        """Every thread this entity has: the live one first, then the archive."""
        return self.history(kind, entity_id, timeout=timeout)["sessions"]

    def has_chat(self, kind: str, entity_id: str, timeout: float = 10.0) -> bool:
        return self.docs.exists(self._key(kind, entity_id))

    # ── writes ──────────────────────────────────────────────────────────────

    def append_message(self, kind: str, entity_id: str, role: str, content: str,
                       timeout: float = 10.0, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Append one chat turn, creating the session if it does not exist yet.
        ``meta`` rides along on the message (a spoken turn's ``voice``)."""
        msg = {**(meta or {}), "role": role, "content": content, "at": _now()}
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key) or self._blank(kind)
            entry.setdefault("messages", []).append(msg)
            entry["updated_at"] = _now()
            self.docs.put(key, entry)
        return msg

    def append_trace(self, kind: str, entity_id: str, items: List[dict],
                     timeout: float = 10.0) -> None:
        """Append one turn's display items. Called when a run completes — which
        happens even if the client disconnected mid-run, so the session is whole
        when the user comes back."""
        if not items:
            return
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key) or self._blank(kind)
            entry["trace"] = (entry.get("trace") or []) + list(items)
            entry["updated_at"] = _now()
            self.docs.put(key, entry)

    def clear(self, kind: str, entity_id: str, new_session: bool = True,
              timeout: float = 10.0, archive: bool = True) -> int:
        """Start a fresh thread; returns the (possibly bumped) session epoch.

        With ``new_session`` the thread being left is filed under ``sessions``
        rather than destroyed, so "Clear" is now "start a new chat" and the
        picker can bring this one back. Without it the transcript is genuinely
        dropped, which is what a caller resetting an entity wants.
        ``archive=False`` with ``new_session`` drops the thread being left but
        still opens a new epoch, so the next turn is a new conversation in
        Messages (the assistant's "clear the transcript").
        """
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key)
            if not entry:
                return 0
            if new_session:
                if archive:
                    self._archive_active(entry)
                entry["session_epoch"] = self._next_epoch(entry)
            entry["messages"] = []
            entry["trace"] = []
            entry["updated_at"] = _now()
            self.docs.put(key, entry)
            return int(entry.get("session_epoch") or 0)

    def activate_session(self, kind: str, entity_id: str, session_id: str,
                         timeout: float = 10.0, drop_active: bool = False) -> Optional[Dict[str, Any]]:
        """Reopen an archived thread as the live one, filing the current one.

        The epoch travels with the thread, so the next turn lands in the backend
        session that thread already had: the user continues the conversation
        instead of starting a lookalike beside it.

        ``drop_active`` leaves the current thread out of the archive: a thread
        that was nothing but the request to go back to another one.

        Returns the restored transcript, or ``None`` when there is no such
        thread.
        """
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key)
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
            if not drop_active:
                self._archive_active(entry)
            entry["sessions"] = [s for s in (entry.get("sessions") or [])
                                 if int(s.get("epoch") or 0) != int(chosen.get("epoch") or 0)]
            entry["messages"] = list(chosen.get("messages") or [])
            entry["trace"] = list(chosen.get("trace") or [])
            entry["session_epoch"] = int(chosen.get("epoch") or 0)
            entry["updated_at"] = _now()
            self.docs.put(key, entry)
            return {"messages": list(entry["messages"]),
                    "trace": list(entry["trace"]),
                    "session_epoch": int(entry["session_epoch"])}

    def delete_session(self, kind: str, entity_id: str, session_id: str,
                       timeout: float = 10.0) -> bool:
        """Drop one archived thread. The live one is not deletable this way."""
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key)
            if not entry or session_id == self.session_id(int(entry.get("session_epoch") or 0)):
                return False
            sessions = list(entry.get("sessions") or [])
            kept = [s for s in sessions
                    if self.session_id(int(s.get("epoch") or 0)) != session_id]
            if len(kept) == len(sessions):
                return False
            entry["sessions"] = kept
            entry["updated_at"] = _now()
            self.docs.put(key, entry)
            return True

    def enter_workspace(self, kind: str, entity_id: str, workspace: str,
                        unstamped: str = "default") -> bool:
        """Make the live thread one of ``workspace``'s, for a chat whose
        conversations each stay in one workspace (the assistant). A live
        thread of another workspace is filed, and this workspace's latest
        conversation reopened, or a new one started. ``unstamped`` is the
        workspace of a thread from before turns were stamped. Returns whether
        the live thread changed."""
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key)
            if not entry:
                return False
            if self._thread_workspace(entry, unstamped) == workspace:
                if entry.get("workspace") != workspace:
                    # Remembered, so a blank thread started here later is still this workspace's.
                    entry["workspace"] = workspace
                    self.docs.put(key, entry)
                return False
            own = [s for s in (entry.get("sessions") or [])
                   if self._thread_workspace(s, unstamped) == workspace]
            empty = not entry.get("messages") and not entry.get("trace")
            if empty and (not own or not entry.get("workspace")):
                # A blank thread that is nobody's yet, or nothing to reopen: it is this workspace's now.
                entry["workspace"] = workspace
                self.docs.put(key, entry)
                return False
            self._archive_active(entry)
            if own:
                chosen = max(own, key=lambda s: (s.get("updated_at") or "", int(s.get("epoch") or 0)))
                epoch = int(chosen.get("epoch") or 0)
                entry["sessions"] = [s for s in (entry.get("sessions") or [])
                                     if int(s.get("epoch") or 0) != epoch]
                entry["messages"] = list(chosen.get("messages") or [])
                entry["trace"] = list(chosen.get("trace") or [])
                entry["session_epoch"] = epoch
            else:
                entry["session_epoch"] = self._next_epoch(entry)
                entry["messages"] = []
                entry["trace"] = []
            entry["workspace"] = workspace
            entry["updated_at"] = _now()
            self.docs.put(key, entry)
            return True

    def request_switch(self, kind: str, entity_id: str, session_id: str, run_id: str = "") -> bool:
        """Ask for ``session_id`` to become the live thread once the running
        turn ends (:meth:`take_switch`). False when there is no such thread."""
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key)
            if not entry:
                return False
            epochs = [int(entry.get("session_epoch") or 0)] + [
                int(s.get("epoch") or 0) for s in (entry.get("sessions") or [])]
            if session_id not in {self.session_id(e) for e in epochs}:
                return False
            entry["switch_to"] = {"session_id": session_id, "run_id": run_id, "at": _now()}
            self.docs.put(key, entry)
            return True

    def take_switch(self, kind: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """The switch a turn asked for, removed from the entry; None when
        nothing was asked."""
        key = self._key(kind, entity_id)
        with self.docs.transaction():
            entry = self.docs.get(key)
            if not entry or not entry.get("switch_to"):
                return None
            switch = entry.pop("switch_to")
            self.docs.put(key, entry)
            return switch

    def delete(self, kind: str, entity_id: str, timeout: float = 10.0) -> bool:
        """Drop an entity's chat entirely — called when the entity is deleted."""
        return self.docs.delete(self._key(kind, entity_id))


#: The process-wide store. One collection, so one instance is enough and the
#: routes do not each build their own.
_store: Optional[EntityChatStore] = None


def entity_chat_store() -> EntityChatStore:
    global _store
    if _store is None:
        _store = EntityChatStore()
    return _store


__all__ = ["EntityChatStore", "entity_chat_store"]
