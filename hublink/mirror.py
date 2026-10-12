"""
The one-way mirror: copies of what happened here, sent to the linked hub.

Three kinds, each switched on its own in the link record (``mirror``):

* ``runs``: a run that reached a terminal status, as the OTLP span
  ``common/otel_export.py`` already builds, posted to the remote's
  ``/api/ingest/v1/traces`` with the mirror connection's token. The remote
  reads it back as a run of that connection, back-dated to when it ran.
* ``chats``: a conversation, whole, each time it is saved, to the remote's
  ``PUT /api/hublink/mirror/chats`` with the personal key. The remote keeps
  it read-only under ``mirror:<node>:<id>``, in the target workspace, owned
  by the key's owner.
* ``memory``: the notes of a personal pool each time one is written, to
  ``PUT /api/hublink/mirror/memory``; the remote merges them by note id into
  the owner's personal pool there.

Everything goes through an outbox (the ``hub_link_outbox`` DocStore): the
hooks (:func:`on_run_finished`, :func:`on_chat_saved`,
:func:`on_memory_saved`) only write a small marker keyed by the record, so a
chat saved ten times is sent once, with what it holds when the drainer gets
to it. The drainer (:func:`drain`, run every few seconds by
``hublink/runtime.py`` on one replica) reads the records then, sends them in
batches and deletes the markers that went through. A remote that is down
costs nothing but a growing outbox, capped at :data:`MAX_OUTBOX`.

Nothing is read back, and nothing mirrored is mirrored again: a run the hub
only reported in (``origin`` ``ingest``) and a chat that is itself a mirror
(``origin`` ``mirror``) are skipped, so two hubs linked to each other do not
echo.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common.docstore import DocStore
from hublink import store
from hublink.remote import Remote, RemoteError

log = logging.getLogger(__name__)

_outbox = DocStore("hub_link_outbox")
_lock = threading.Lock()

MAX_OUTBOX = 5000
BATCH = 50
MAX_ATTEMPTS = 50
#: Run statuses that end a run's life (the otel exporter's list).
TERMINAL_STATUSES = ("completed", "stopped", "failed", "error")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the outbox ───────────────────────────────────────────────────────────────

def enqueue(kind: str, record_id: str, **meta: Any) -> None:
    """Mark ``record_id`` of ``kind`` as due. Keyed by the record, so a
    second mark before the first was sent replaces it."""
    key = f"{kind}:{record_id}"
    with _outbox.transaction():
        existing = _outbox.get(key)
        doc = {"kind": kind, "id": str(record_id), "meta": meta,
               "created_at": (existing or {}).get("created_at") or _now_iso(),
               "attempts": int((existing or {}).get("attempts") or 0), "last_error": ""}
        _outbox.put(key, doc)
        if _outbox.count() > MAX_OUTBOX:
            keys = _outbox.keys()
            for old in keys[: len(keys) - MAX_OUTBOX]:
                _outbox.delete(old)


def outbox_stats() -> Dict[str, Any]:
    docs = [d for d in _outbox.values() if isinstance(d, dict)]
    by_kind: Dict[str, int] = {}
    for d in docs:
        by_kind[str(d.get("kind"))] = by_kind.get(str(d.get("kind")), 0) + 1
    errors = [str(d.get("last_error")) for d in docs if d.get("last_error")]
    return {"pending": len(docs), "by_kind": by_kind, "last_error": errors[-1] if errors else ""}


def clear_outbox() -> int:
    return _outbox.clear()


# ── hooks (called from the stores) ───────────────────────────────────────────

def on_run_finished(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> None:
    """From ``managers.runs.store._update_run``: once, on the transition into
    a terminal status, when runs are mirrored."""
    try:
        if not store.mirror_enabled("runs"):
            return
        status = str(new.get("status") or "")
        if status not in TERMINAL_STATUSES:
            return
        if str((old or {}).get("status") or "") in TERMINAL_STATUSES:
            return
        if str(new.get("origin") or "") == "ingest":
            return
        run_id = str(new.get("run_id") or "")
        if run_id:
            enqueue("run", run_id)
    except Exception:  # noqa: BLE001 - the mirror must never break run recording
        log.debug("hub link: run hook failed", exc_info=True)


def on_chat_saved(chat_id: str, origin: Optional[str] = None) -> None:
    try:
        if not chat_id or str(origin or "") == "mirror":
            return
        if not store.mirror_enabled("chats"):
            return
        enqueue("chat", str(chat_id))
    except Exception:  # noqa: BLE001 - the mirror must never break a chat save
        log.debug("hub link: chat hook failed", exc_info=True)


def on_memory_saved(memory_id: str) -> None:
    try:
        if not memory_id or not store.mirror_enabled("memory"):
            return
        enqueue("memory", str(memory_id))
    except Exception:  # noqa: BLE001 - the mirror must never break a memory write
        log.debug("hub link: memory hook failed", exc_info=True)


# ── sending ──────────────────────────────────────────────────────────────────

def _run_payload(run_id: str) -> Optional[Dict[str, Any]]:
    from managers.run_manager import get_run_by_id
    from common.otel_export import _with_process, build_span
    rec = get_run_by_id(run_id)
    if not rec:
        return None
    return build_span(_with_process(dict(rec)))


def _send_runs(doc: Dict[str, Any], ids: List[str]) -> Dict[str, str]:
    conn = doc.get("connection") or {}
    token = str(conn.get("token") or "")
    if not token:
        return {i: "no mirror connection on the remote (relink with runs on)" for i in ids}
    rm = Remote(doc["url"], token)
    errors: Dict[str, str] = {}
    for run_id in ids:
        try:
            payload = _run_payload(run_id)
        except Exception as exc:  # noqa: BLE001 - an unreadable run is reported and retried
            errors[run_id] = f"cannot read run: {exc}"
            continue
        if payload is None:
            continue  # deleted since; nothing to send
        try:
            rm.call("POST", "/api/ingest/v1/traces", json_body=payload, ok=(200, 201, 202, 204))
        except RemoteError as exc:
            errors[run_id] = str(exc)
    return errors


def _chat_doc(chat_id: str) -> Optional[Dict[str, Any]]:
    from common import chat_store
    return chat_store.get_chat(chat_id)


def _send_chats(doc: Dict[str, Any], ids: List[str]) -> Dict[str, str]:
    rm = Remote(doc["url"], doc.get("api_key") or "")
    chats = []
    for chat_id in ids:
        chat = _chat_doc(chat_id)
        if chat is not None and str(chat.get("origin") or "") != "mirror":
            chats.append(chat)
    if not chats:
        return {}
    body = {"node": doc.get("name") or store.default_node_name(),
            "workspace": doc.get("target_workspace") or "", "chats": chats}
    try:
        rm.put_json("/api/hublink/mirror/chats", body)
    except RemoteError as exc:
        return {c["id"]: str(exc) for c in chats}
    return {}


def _memory_doc(memory_id: str) -> Optional[Dict[str, Any]]:
    from memory import personal
    from memory.store import MemoryStore
    mem = MemoryStore().get(memory_id)
    if mem is None or not personal.is_personal(mem):
        return None
    return {"source_id": str(mem.id), "name": mem.name, "workspace": mem.workspace,
            # Notes that came in through a mirror are not sent on: two hubs
            # linked to each other would otherwise echo them back and forth.
            "notes": [n for n in mem.notes if isinstance(n, dict) and not n.get("mirror")],
            "blocks": [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in mem.blocks]}


def _send_memory(doc: Dict[str, Any], ids: List[str]) -> Dict[str, str]:
    rm = Remote(doc["url"], doc.get("api_key") or "")
    pools = []
    for memory_id in ids:
        pool = _memory_doc(memory_id)
        if pool is not None:
            pools.append(pool)
    if not pools:
        return {}
    body = {"node": doc.get("name") or store.default_node_name(),
            "workspace": doc.get("target_workspace") or "", "pools": pools}
    try:
        rm.put_json("/api/hublink/mirror/memory", body)
    except RemoteError as exc:
        return {p["source_id"]: str(exc) for p in pools}
    return {}


_SENDERS = {"run": _send_runs, "chat": _send_chats, "memory": _send_memory}
_KIND_SWITCH = {"run": "runs", "chat": "chats", "memory": "memory"}


def drain(*, force: bool = False, limit: int = BATCH) -> Dict[str, int]:
    """Send what is due. Returns counts. ``force`` ignores the lease (the
    Sync button, the CLI); the background loop holds it so one replica drains."""
    doc = store.get()
    if doc is None:
        return {"sent": 0, "failed": 0, "skipped": 0}
    with _lock:
        pending = [(k, d) for k, d in _outbox.all().items() if isinstance(d, dict)]
        pending.sort(key=lambda kd: str(kd[1].get("created_at") or ""))
        batch = pending[:limit]
        by_kind: Dict[str, List[str]] = {}
        keys_by_kind: Dict[str, Dict[str, str]] = {}
        sent = failed = skipped = 0
        for key, d in batch:
            kind = str(d.get("kind") or "")
            if kind not in _SENDERS or not doc.get("mirror", {}).get(_KIND_SWITCH[kind]):
                _outbox.delete(key)
                skipped += 1
                continue
            by_kind.setdefault(kind, []).append(str(d["id"]))
            keys_by_kind.setdefault(kind, {})[str(d["id"])] = key
        for kind, ids in by_kind.items():
            try:
                errors = _SENDERS[kind](doc, ids)
            except Exception as exc:  # noqa: BLE001 - a sender that raises fails its whole batch
                log.debug("hub link: %s sender failed", kind, exc_info=True)
                errors = {i: f"{type(exc).__name__}: {exc}" for i in ids}
            for rec_id in ids:
                key = keys_by_kind[kind][rec_id]
                err = errors.get(rec_id)
                if not err:
                    _outbox.delete(key)
                    sent += 1
                    continue
                failed += 1
                d = _outbox.get(key) or {}
                attempts = int(d.get("attempts") or 0) + 1
                if attempts >= MAX_ATTEMPTS:
                    _outbox.delete(key)
                else:
                    _outbox.put(key, {**d, "attempts": attempts, "last_error": err[:500]})
        if failed:
            first = next((d.get("last_error") for _, d in pending if d.get("last_error")), "")
            store.update(last_error=str(first or ""))
        elif sent:
            store.update(last_sync_at=_now_iso(), last_error="")
    return {"sent": sent, "failed": failed, "skipped": skipped}


__all__ = ["BATCH", "MAX_OUTBOX", "TERMINAL_STATUSES", "clear_outbox", "drain", "enqueue",
           "on_chat_saved", "on_memory_saved", "on_run_finished", "outbox_stats"]
