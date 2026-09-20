"""
What a run is saying *right now*.

Every surface that shows a run in progress has had the same gap: you see it only
if you were watching when it started. The events are a broadcast, so a browser
that connects mid-run sees the second half of a sentence, and a browser that
opens the run's page after the answer has streamed sees nothing at all until the
record is written at the end.

This module is the missing short-term memory. It holds, per in-flight run, the
text streamed so far plus the thinking and tool steps behind it, so any surface
can ask "what has happened up to now?" once and then follow the live channel
from there. It is deliberately *not* storage: the run record, its log file and
its payloads remain the durable account. Everything here is bounded, in-process,
and dropped shortly after the run ends.

Two feeds keep it filled, and each event passes through exactly one of them:

* :func:`start_turn` / :func:`record` — a chat turn, from ``chat.broadcast``,
  which sees the whole pipeline including the terminal ``done``.
* :func:`record_run_event` — a run executing in another process, from the
  ``/api/sessions/{id}/events`` relay.

Anything not on those paths simply has no live snapshot, which every reader
already has to handle: a run that finished before the page opened never has one.
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Any, Dict, List, Optional

#: Streamed answer text kept per run. A live tail, not the archive — the run log
#: holds the whole thing.
MAX_TEXT = 40_000
#: Thinking lines and tool steps kept per run, newest wins once full.
MAX_THINKING = 60
MAX_TOOLS = 120
#: How long a finished turn stays readable, so a page opened just after the last
#: token still shows the answer rather than an empty live panel.
KEEP_FINISHED_SECONDS = 120.0
#: Hard ceiling on tracked turns, oldest evicted first.
MAX_TURNS = 300

_lock = threading.RLock()
_turns: Dict[str, Dict[str, Any]] = {}
_by_run: Dict[str, str] = {}
_by_conversation: Dict[str, str] = {}
_ids = itertools.count(1)


def _now() -> float:
    return time.time()


def _blank(turn_id: str, **fields) -> Dict[str, Any]:
    turn = {
        "turn_id": turn_id,
        "run_id": None,
        "session_id": None,
        "conversation_id": None,
        "agent_id": None,
        "source": None,
        "user_message": "",
        "text": "",
        "thinking": [],
        "thinking_live": "",
        "tools": [],
        "status": "running",
        "error": None,
        "usage": None,
        "duration_ms": None,
        "started_at": _now(),
        "finished_at": None,
    }
    turn.update({k: v for k, v in fields.items() if v is not None})
    return turn


def _sweep() -> None:
    """Drop what nobody can still be interested in. Called on every write, which
    is often, so it only looks at what it must: finished turns past their keep
    window, then the oldest turns if the table is still over its ceiling."""
    now = _now()
    stale = [tid for tid, t in _turns.items()
             if t["finished_at"] and now - t["finished_at"] > KEEP_FINISHED_SECONDS]
    for tid in stale:
        _drop(tid)
    if len(_turns) > MAX_TURNS:
        oldest = sorted(_turns.values(), key=lambda t: t["started_at"])
        for turn in oldest[:len(_turns) - MAX_TURNS]:
            _drop(turn["turn_id"])


def _drop(turn_id: str) -> None:
    turn = _turns.pop(turn_id, None)
    if not turn:
        return
    if turn.get("run_id"):
        _by_run.pop(turn["run_id"], None)
    conv = turn.get("conversation_id")
    if conv and _by_conversation.get(conv) == turn_id:
        _by_conversation.pop(conv, None)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[-MAX_TEXT:]


# ── the chat feed ────────────────────────────────────────────────────────────

def start_turn(*, conversation_id: Optional[str], user_message: str = "",
               source: Optional[str] = None) -> str:
    """Open a turn and return its id. One conversation has one live turn: a new
    one replaces whatever was left of the last, which is what the conversation
    itself does."""
    turn_id = f"t{next(_ids)}"
    with _lock:
        if conversation_id and conversation_id in _by_conversation:
            _drop(_by_conversation[conversation_id])
        _turns[turn_id] = _blank(turn_id, conversation_id=conversation_id,
                                 user_message=user_message or "", source=source)
        if conversation_id:
            _by_conversation[conversation_id] = turn_id
        _sweep()
    return turn_id


def record(turn_id: str, event: Dict[str, Any]) -> None:
    """Fold one pipeline event into the turn."""
    if not turn_id or not isinstance(event, dict):
        return
    with _lock:
        turn = _turns.get(turn_id)
        if turn is None:
            return
        _apply(turn, event)


def finish(turn_id: str, *, status: str = "finished", **fields) -> None:
    with _lock:
        turn = _turns.get(turn_id)
        if turn is None:
            return
        turn["status"] = status
        turn["finished_at"] = _now()
        for key, value in fields.items():
            if value is not None:
                turn[key] = value


# ── the relay feed (runs in another process) ─────────────────────────────────

def record_run_event(event: Dict[str, Any], *, session_id: Optional[str] = None) -> None:
    """Fold one event relayed from a subprocess run into that run's turn.

    The run id travels on the event (``SessionPublishCallback`` stamps it), and
    a turn is opened on first sight: unlike a chat turn there is no start signal
    to hang it on, and the first event is as good a beginning as exists.
    """
    if not isinstance(event, dict):
        return
    run_id = str(event.get("run_id") or "")
    if not run_id:
        return
    with _lock:
        turn_id = _by_run.get(run_id)
        turn = _turns.get(turn_id) if turn_id else None
        if turn is None:
            turn_id = f"t{next(_ids)}"
            turn = _blank(turn_id, run_id=run_id, session_id=session_id)
            _turns[turn_id] = turn
            _by_run[run_id] = turn_id
            _sweep()
        elif session_id and not turn.get("session_id"):
            turn["session_id"] = session_id
        _apply(turn, event)


# ── folding ──────────────────────────────────────────────────────────────────

def _bind_run(turn: Dict[str, Any], run_id: str) -> None:
    if not run_id or turn.get("run_id") == run_id:
        return
    turn["run_id"] = run_id
    _by_run[run_id] = turn["turn_id"]


def _apply(turn: Dict[str, Any], event: Dict[str, Any]) -> None:
    kind = event.get("type")
    if event.get("run_id"):
        _bind_run(turn, str(event["run_id"]))
    if event.get("session_id") and not turn.get("session_id"):
        turn["session_id"] = str(event["session_id"])
    if event.get("agent_id") and not turn.get("agent_id"):
        turn["agent_id"] = str(event["agent_id"])

    if kind == "token":
        turn["text"] = _clip(turn["text"] + str(event.get("token") or ""))
        # The answer has started, so the reasoning ticker is over.
        turn["thinking_live"] = ""
    elif kind in ("think", "plan"):
        message = str(event.get("message") or event.get("content") or "").strip()
        if message:
            turn["thinking"] = (turn["thinking"] + [{"kind": kind, "content": message}])[-MAX_THINKING:]
        turn["thinking_live"] = ""
    elif kind == "think_delta":
        turn["thinking_live"] = _clip(turn["thinking_live"] + str(event.get("delta") or ""))
    elif kind == "tool_start":
        turn["tools"] = (turn["tools"] + [{
            "step": event.get("step"), "tool": event.get("tool"),
            "input": event.get("input") or "", "output": None, "error": None,
        }])[-MAX_TOOLS:]
    elif kind in ("tool_end", "tool_error"):
        field = "output" if kind == "tool_end" else "error"
        value = event.get("output") if kind == "tool_end" else (event.get("error") or "")
        for step in reversed(turn["tools"]):
            if step.get("step") == event.get("step") or step.get("tool") == event.get("tool"):
                step[field] = value
                break
    elif kind == "node_start":
        label = event.get("label") or event.get("agent_id") or event.get("node_id")
        if label:
            turn["thinking"] = (turn["thinking"] + [{"kind": "node", "content": str(label)}])[-MAX_THINKING:]
    elif kind == "team_message":
        # A team answers as a conversation; the live view shows it as it is said.
        who = event.get("sender") or event.get("agent_id") or ""
        body = str(event.get("content") or "")
        if body:
            turn["text"] = _clip(f"{turn['text']}\n\n**{who}**\n{body}" if turn["text"] else f"**{who}**\n{body}")
    elif kind == "error":
        turn["status"] = "failed"
        turn["error"] = str(event.get("error") or event.get("message") or "")
    elif kind in ("done", "session_done"):
        ok = event.get("ok")
        turn["status"] = "finished" if ok or ok is None else "failed"
        turn["error"] = event.get("error") or turn["error"]
        response = event.get("response")
        if isinstance(response, str) and response.strip():
            turn["text"] = _clip(response)
        turn["usage"] = event.get("usage") or turn["usage"]
        turn["duration_ms"] = event.get("duration_ms") or turn["duration_ms"]
        turn["thinking_live"] = ""
        turn["finished_at"] = _now()


# ── reads ────────────────────────────────────────────────────────────────────

def _public(turn: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if turn is None:
        return None
    return {**turn, "thinking": list(turn["thinking"]), "tools": [dict(s) for s in turn["tools"]]}


def by_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _public(_turns.get(_by_run.get(str(run_id), ""), None))


def by_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _public(_turns.get(_by_conversation.get(str(conversation_id), ""), None))


def active() -> List[Dict[str, Any]]:
    """Every turn still running, newest first. Used by tests and diagnostics."""
    with _lock:
        return sorted((_public(t) for t in _turns.values() if t["status"] == "running"),
                      key=lambda t: t["started_at"], reverse=True)


def reset() -> None:
    """Forget everything. For tests, and for a backend that is shutting down."""
    with _lock:
        _turns.clear()
        _by_run.clear()
        _by_conversation.clear()


__all__ = ["start_turn", "record", "finish", "record_run_event", "by_run",
           "by_conversation", "active", "reset", "MAX_TEXT", "KEEP_FINISHED_SECONDS"]
