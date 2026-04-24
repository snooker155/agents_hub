"""
Session context service — file-based storage helpers.

Manages session_contexts.json without any FastAPI dependency so that
both API routes and tool/agent subprocess code can use these functions.

A session context groups one or more individual agent run messages:
- Chat: one session per conversation_id, one message per exchange
- Task: one session per task_id, one message per agent run on that task
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from filelock import FileLock

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
STATE_DIR = PROJECT_ROOT / "agents" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

CONTEXTS_FILE = STATE_DIR / "session_contexts.json"
CONTEXTS_LOCK = STATE_DIR / "session_contexts.json.lock"

CONTINUATIONS_FILE = STATE_DIR / "pending_continuations.json"
CONTINUATIONS_LOCK = STATE_DIR / "pending_continuations.json.lock"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_contexts(timeout: float = 10.0) -> List[Dict[str, Any]]:
    if not CONTEXTS_FILE.exists():
        return []
    with FileLock(str(CONTEXTS_LOCK), timeout=timeout):
        try:
            txt = CONTEXTS_FILE.read_text(encoding="utf-8")
            return json.loads(txt) if txt.strip() else []
        except Exception:
            return []


def save_contexts(contexts: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    CONTEXTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(contexts, ensure_ascii=False, indent=2)
    with FileLock(str(CONTEXTS_LOCK), timeout=timeout):
        tmp = CONTEXTS_FILE.with_suffix(CONTEXTS_FILE.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, CONTEXTS_FILE)


def upsert_context(ctx: Dict[str, Any]) -> None:
    contexts = load_contexts()
    for i, c in enumerate(contexts):
        if c.get("session_id") == ctx.get("session_id"):
            contexts[i] = {**c, **ctx}
            save_contexts(contexts)
            return
    contexts.append(ctx)
    save_contexts(contexts)


def get_context_by_id(session_id: str) -> Optional[Dict[str, Any]]:
    for c in load_contexts():
        if c.get("session_id") == session_id:
            return c
    return None


def get_or_create_task_session(
    title: str,
    workspace: Optional[str] = None,
    is_flow: bool = False,
) -> str:
    """Create a new session context for a task run and return its session_id."""
    session_id = str(uuid4())
    ctx: Dict[str, Any] = {
        "session_id": session_id,
        "title": title,
        "description": "",
        "workspace": workspace,
        "session_type": "task",
        "is_flow": is_flow,
        "message_ids": [],
        "events": [],
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "finished_at": None,
    }
    upsert_context(ctx)
    return session_id


def get_or_create_chat_session(
    conversation_id: str,
    title: str,
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Return the session_id for a chat conversation, creating a context if needed."""
    for c in load_contexts():
        if c.get("conversation_id") == conversation_id:
            return c["session_id"]

    session_id = str(uuid4())
    ctx: Dict[str, Any] = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "title": title,
        "description": "",
        "workspace": workspace,
        "is_flow": False,
        "session_type": "chat",
        "agent_id": agent_id,
        "message_ids": [],
        "events": [],
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "finished_at": None,
    }
    upsert_context(ctx)
    return session_id


def add_run_to_session(session_id: str, run_id: str) -> None:
    """Append a run_id to a session context's message_ids (idempotent)."""
    ctx = get_context_by_id(session_id)
    if ctx is None:
        return
    message_ids = list(ctx.get("message_ids") or [])
    if run_id not in message_ids:
        message_ids.append(run_id)
    upsert_context({**ctx, "message_ids": message_ids, "updated_at": _utc_now_iso()})


def add_event_to_session(session_id: str, event: Dict[str, Any]) -> None:
    """Append a non-message event (e.g. approval step) to a session context."""
    ctx = get_context_by_id(session_id)
    if ctx is None:
        return
    events = list(ctx.get("events") or [])
    events.append(event)
    upsert_context({**ctx, "events": events, "updated_at": _utc_now_iso()})


# ── Session continuations ─────────────────────────────────────────────────────

def _load_continuations(timeout: float = 10.0) -> List[Dict[str, Any]]:
    if not CONTINUATIONS_FILE.exists():
        return []
    with FileLock(str(CONTINUATIONS_LOCK), timeout=timeout):
        try:
            txt = CONTINUATIONS_FILE.read_text(encoding="utf-8")
            return json.loads(txt) if txt.strip() else []
        except Exception:
            return []


def _save_continuations(items: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    CONTINUATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    with FileLock(str(CONTINUATIONS_LOCK), timeout=timeout):
        tmp = CONTINUATIONS_FILE.with_suffix(CONTINUATIONS_FILE.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, CONTINUATIONS_FILE)


def register_continuation(
    session_id: str,
    task_id: str,
    workspace: Optional[str] = None,
    agent_id: str = "orchestrator",
) -> None:
    """Register that session_id should be re-invoked on agent_id when task_id finishes."""
    items = _load_continuations()
    # Deduplicate: one pending continuation per (session_id, task_id)
    items = [c for c in items if not (c["session_id"] == session_id and c["task_id"] == str(task_id))]
    items.append({
        "session_id": session_id,
        "task_id": str(task_id),
        "workspace": workspace,
        "agent_id": agent_id,
        "registered_at": _utc_now_iso(),
    })
    _save_continuations(items)


def pop_continuations_for_task(task_id: str) -> List[Dict[str, Any]]:
    """Remove and return all pending continuations for the given task_id."""
    items = _load_continuations()
    matched = [c for c in items if c["task_id"] == str(task_id)]
    remaining = [c for c in items if c["task_id"] != str(task_id)]
    if matched:
        _save_continuations(remaining)
    return matched


def has_continuations_for_task(task_id: str) -> bool:
    """Return True if there are pending continuations for the given task_id (non-destructive)."""
    return any(c["task_id"] == str(task_id) for c in _load_continuations())
