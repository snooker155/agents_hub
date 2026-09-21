"""Turning a reported run into the same records a local run leaves behind.

The point of observe mode is that a run the hub never started is indistinguish-
able, once recorded, from one it did: the same ``runs`` row, the same session,
the same live events on the same SSE channel, the same token and cost columns.
Everything here is therefore a thin adapter onto machinery that already exists
(``managers.run_manager``, ``common.session_service``, ``common.live_runs``,
``common.session_broker``) rather than a parallel world for external work.

Three calls make up a reported run::

    open(connection, ...)              -> run_id, session_id
    events(connection, run_id, frames) -> accepted
    close(connection, run_id, ...)     -> the finished record

A fourth ends a run that is not finished but waiting::

    interrupt(connection, run_id, ...) -> the parked record

**Pausing, in a direction where the hub cannot call out.** A graph that stops to
ask a human (LangGraph's ``interrupt()``) parks its run here with the question
attached. The hub has no way to push the answer back — in observe mode nothing
here ever calls the agent — so the client comes and asks for it
(``answer_for``). When it does, the parked run is finished: the question was
asked, answered and delivered. The work that follows arrives as a new run
carrying ``resumed_from``, which is also how this product's own agents behave,
since an agent that asks a question ends its run and resumes in the next one.

**Why the accumulator lives in memory.** Frames arrive across many requests, and
a run's answer, tool trail and token totals are built from all of them. Writing
the partial state to the database on every batch would mean a write per token
batch for every concurrent run; keeping it in a bounded in-process map costs
nothing and loses only the accumulation, not the run. A restart mid-run is
survivable on purpose: the events were already published, the row already
exists, and the ``close`` call carries the authoritative output and usage.

**What a connection may not do.** These calls write run records, session links
and live events. They do not create tasks, trigger flows, grant tools or write
memory. A reporting channel that can act is a remote-execution API wearing a
different name.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from common.agent_frames import MAX_FIELD, FrameTranslator, normalize_usage

# Bounds on what one connection may push. Generous enough for a busy production
# graph, small enough that a misbehaving or hostile client cannot use the
# endpoint as free storage or a denial of service.
MAX_EVENTS_PER_REQUEST = 500
MAX_OPEN_RUNS = 500
# Requests per connection per minute, counted over a sliding window.
RATE_LIMIT_PER_MINUTE = 1200
# How long an accumulator with no traffic is kept before it is dropped. A run
# that goes quiet for this long has either finished elsewhere or died.
IDLE_TTL_SECONDS = 3600


class IngestError(Exception):
    """A reported run could not be accepted. Carries the HTTP status to use."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── the in-memory half ───────────────────────────────────────────────────────

class _LiveRun:
    """One reported run, while it is being reported."""

    def __init__(self, connection_id: str, run_id: str, session_id: str,
                 input_text: str = ""):
        self.connection_id = connection_id
        self.run_id = run_id
        self.session_id = session_id
        # Kept because the payload written at close replaces the one seeded at
        # open, heavy key for heavy key: a run whose input is only in the seed
        # loses it the moment it finishes.
        self.input_text = input_text
        self.translator = FrameTranslator()
        self.started = time.time()
        self.touched = time.time()


_lock = threading.Lock()
_runs: Dict[str, _LiveRun] = {}
_calls: Dict[str, Deque[float]] = {}


def _sweep(now: float) -> None:
    """Drop accumulators nothing has reported to for a while. Caller holds the lock."""
    stale = [rid for rid, run in _runs.items() if now - run.touched > IDLE_TTL_SECONDS]
    for rid in stale:
        _runs.pop(rid, None)


def check_rate(connection_id: str) -> None:
    """Count this request against the connection's budget, or refuse it.

    In-process and per worker, which is the honest description: it is a guard
    against a client in a loop, not a distributed quota. It is here because
    ``/api/ingest`` is the first surface in this product a stranger with a token
    can post to at volume.
    """
    now = time.time()
    with _lock:
        window = _calls.setdefault(connection_id, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= RATE_LIMIT_PER_MINUTE:
            raise IngestError(
                f"rate limit: more than {RATE_LIMIT_PER_MINUTE} ingest requests in a minute",
                status=429,
            )
        window.append(now)


def live_run(connection_id: str, run_id: str) -> Optional[_LiveRun]:
    """The accumulator for a run, if this connection owns it."""
    with _lock:
        run = _runs.get(run_id)
    if run is None or run.connection_id != connection_id:
        return None
    return run


def open_runs(connection_id: str) -> int:
    with _lock:
        return sum(1 for r in _runs.values() if r.connection_id == connection_id)


# ── the recorded half ────────────────────────────────────────────────────────

def _publish(session_id: str, event: Dict[str, Any]) -> None:
    """Put one event on the session's channel and into the live-run tail.

    Both, because they answer different questions: the broker feeds the tabs
    watching right now, and ``live_runs`` lets a tab opened mid-run catch up on
    what it missed instead of joining a broadcast already in progress.
    """
    from common import live_runs
    from common.session_broker import broker

    try:
        live_runs.record_run_event(event, session_id=session_id)
    except Exception:
        pass
    try:
        broker.publish_threadsafe(session_id, event)
    except Exception:
        pass


def open_run(
    connection: Dict[str, Any],
    *,
    input_text: str = "",
    thread: Optional[str] = None,
    title: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    resumed_from: Optional[str] = None,
    started_at: Optional[str] = None,
) -> Dict[str, str]:
    """Start recording a run the hub did not launch.

    ``started_at`` is for a run that is being *imported* rather than watched:
    OTLP spans arrive after the work is over, sometimes minutes after, and a
    list in which every imported run happened "just now" and took no time is
    worse than no timestamps at all. A live reporter leaves it unset and the
    clock is read here, which is the truth for it.
    """
    from common.session_service import get_or_create_chat_session
    from managers import run_manager

    connection_id = str(connection["id"])
    if open_runs(connection_id) >= MAX_OPEN_RUNS:
        raise IngestError(
            f"connection '{connection_id}' already has {MAX_OPEN_RUNS} runs open; "
            f"close them before opening more",
            status=429,
        )

    run_id = f"ing-{uuid.uuid4().hex[:16]}"
    # A caller's own thread id groups its runs into one session, the way a chat
    # conversation groups its turns. Without one, the run is its own session,
    # which is right for a scheduled job that has no conversation.
    # A resumed run belongs with the one it continues, so it joins that run's
    # session even when the client sent no thread of its own.
    if thread:
        conversation_id = f"conn:{connection_id}:{thread}"
    elif resumed_from:
        conversation_id = _conversation_of(resumed_from) or f"conn:{connection_id}:{run_id}"
    else:
        conversation_id = f"conn:{connection_id}:{run_id}"
    session_id = get_or_create_chat_session(
        conversation_id=conversation_id,
        title=(title or connection.get("name") or connection_id)[:200],
        workspace=connection.get("workspace"),
        agent_id=connection_id,
    )

    run_manager.open_run(
        run_id,
        agent_id=connection_id,
        session_id=session_id,
        session_type="chat",
        workspace=connection.get("workspace"),
        title=(title or "")[:200] or None,
        provider=provider,
        model=model,
        # What marks this run as reported rather than run here. Both are read by
        # the ownership check and, later, by the UI that lists a connection's
        # runs without scanning every agent.
        origin="ingest",
        connection_id=connection_id,
        metadata=dict(metadata or {}),
        # Set when this run continues one that had stopped for a question, so
        # the two read as one piece of work.
        resumed_from=resumed_from or None,
        **({"started_at": started_at} if started_at else {}),
    )
    run_manager.seed_run_input_context(run_id, "", (input_text or "")[:MAX_FIELD])

    now = time.time()
    live = _LiveRun(connection_id, run_id, session_id, (input_text or "")[:MAX_FIELD])
    if started_at:
        # So the duration this run reports is the work's, not the import's.
        live.started = _epoch_of(started_at, default=now)
    with _lock:
        _sweep(now)
        _runs[run_id] = live

    _publish(session_id, {"type": "meta", "run_id": run_id, "agent_id": connection_id,
                          "session_id": session_id})
    return {"run_id": run_id, "session_id": session_id}


def _conversation_of(run_id: str) -> Optional[str]:
    """The conversation a previous run's session belongs to, if it still exists."""
    try:
        from common.session_service import get_context_by_id
        from managers import run_manager

        previous = run_manager.get_run_by_id(run_id)
        session_id = (previous or {}).get("session_id")
        context = get_context_by_id(str(session_id)) if session_id else None
        return str((context or {}).get("conversation_id") or "") or None
    except Exception:
        return None


def ingest_events(
    connection: Dict[str, Any],
    run_id: str,
    frames: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Translate reported frames and publish them. Returns what was accepted."""
    connection_id = str(connection["id"])
    run = live_run(connection_id, run_id)
    if run is None:
        # Either the run belongs to another connection, or this worker never saw
        # it opened. Both are reported the same way on purpose: which one it is
        # would tell a caller whether a run id exists.
        raise IngestError(f"run '{run_id}' is not open for this connection", status=404)

    if len(frames) > MAX_EVENTS_PER_REQUEST:
        raise IngestError(
            f"at most {MAX_EVENTS_PER_REQUEST} events per request, got {len(frames)}",
            status=413,
        )

    published = 0
    for frame in frames:
        for event in run.translator.feed(frame):
            _publish(run.session_id, {**event, "run_id": run_id, "session_id": run.session_id})
            published += 1
    run.touched = time.time()

    return {"run_id": run_id, "accepted": len(frames), "published": published}


def close_run(
    connection: Dict[str, Any],
    run_id: str,
    *,
    ok: Optional[bool] = None,
    output: Optional[str] = None,
    error: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    finished_at: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Finish a reported run and write what it cost and what it produced.

    The body of this call wins over what was streamed: a graph may post-process
    its own output, and it knows its final token counts better than the frames
    did. Anything it leaves out falls back to what the frames accumulated.
    """
    from managers import run_manager

    connection_id = str(connection["id"])
    run = live_run(connection_id, run_id)
    if run is None:
        raise IngestError(f"run '{run_id}' is not open for this connection", status=404)

    translator = run.translator
    if usage:
        extra = normalize_usage(usage)
        if extra:
            translator.credit(extra)

    resolved_ok = translator.ok if ok is None else bool(ok)
    resolved_ok = True if resolved_ok is None else resolved_ok
    text = (output if output is not None else translator.output) or ""
    message = (error or translator.error or "").strip()
    # Measured here for a run that was watched; stated by the caller for one
    # that was imported, where "now" is when the spans arrived rather than when
    # the work ended.
    elapsed_ms = int((time.time() - run.started) * 1000) if duration_ms is None else int(duration_ms)

    process = {
        "input_context": {"system_prompt": "", "history": [], "user_message": run.input_text},
        "response_text": text[:MAX_FIELD] if resolved_ok else "",
        "tool_calls": translator.tool_calls(),
        "token_usage": translator.token_usage(),
        "duration_ms": elapsed_ms,
    }

    record = run_manager.close_run(
        run_id,
        status="completed" if resolved_ok else "failed",
        exit_code=0 if resolved_ok else 1,
        error=message or None,
        output=text[:MAX_FIELD] if resolved_ok else None,
        process=process,
        # An imported run finished when it finished, not when its spans got
        # here; a live reporter leaves this unset and "now" is correct for it.
        **({"finished_at": finished_at} if finished_at else {}),
        # On the run record rather than in the heavy payload: the path through
        # the graph is the one thing a list of reported runs wants to show, and
        # the payload's canonical shape is a fixed set of columns anyway, so an
        # extra key there would be dropped on the way in.
        graph_path=translator.path(),
    )

    _publish(run.session_id, {
        "type": "done", "run_id": run_id, "session_id": run.session_id,
        "ok": resolved_ok, "response": text[:MAX_FIELD], "error": message or None,
        "usage": translator.token_usage(), "duration_ms": elapsed_ms,
    })

    with _lock:
        _runs.pop(run_id, None)

    return {
        "run_id": run_id,
        "status": "completed" if resolved_ok else "failed",
        "usage": translator.token_usage(),
        "duration_ms": elapsed_ms,
        "recorded": record is not None,
    }


def interrupt_run(
    connection: Dict[str, Any],
    run_id: str,
    *,
    question: str = "",
    choices: Optional[List[str]] = None,
    key: str = "",
    node: str = "",
    output: Optional[str] = None,
) -> Dict[str, Any]:
    """Park a run that has stopped to ask a human something.

    Deliberately not a variant of ``close``: a paused run is not a finished one,
    and overloading the call that ends runs is how a pause quietly becomes a
    completion the first time someone passes the wrong flag.
    """
    from managers import run_manager

    connection_id = str(connection["id"])
    run = live_run(connection_id, run_id)
    if run is None:
        raise IngestError(f"run '{run_id}' is not open for this connection", status=404)

    translator = run.translator
    pending = {
        "question": (question or "")[:2000],
        "choices": [str(c)[:200] for c in (choices or [])][:20],
        "key": (key or "")[:200],
        "node": (node or "")[:200],
        "asked_at": _utc_now_iso(),
    }
    duration_ms = int((time.time() - run.started) * 1000)

    record = run_manager.update_run(run_id, {
        # Not a terminal status on purpose: retention never deletes a run that
        # is waiting for a person, and the watchdog leaves it alone because it
        # only reaps runs marked running with a dead pid.
        "status": "awaiting_input",
        "pending_question": pending,
        "graph_path": translator.path(),
        "process": {
            "input_context": {"system_prompt": "", "history": [],
                              "user_message": run.input_text},
            "response_text": (output if output is not None else translator.output)[:MAX_FIELD],
            "tool_calls": translator.tool_calls(),
            "token_usage": translator.token_usage(),
            "duration_ms": duration_ms,
        },
    })

    _publish(run.session_id, {
        "type": "awaiting_input", "run_id": run_id, "session_id": run.session_id,
        **{k: v for k, v in pending.items() if k != "asked_at"},
    })
    _notify_question(connection, run_id, pending)

    # The accumulator goes: what happens next is a *new* run, and holding this
    # one open would count against the connection's open-run budget for as long
    # as a person takes to answer, which can be days.
    with _lock:
        _runs.pop(run_id, None)

    return {"run_id": run_id, "status": "awaiting_input", "pending_question": pending,
            "recorded": record is not None}


def answer_run(run_id: str, value: Any, *, answered_by: str = "") -> Dict[str, Any]:
    """Record a human's answer to a parked run. Called by the operator's side."""
    from managers import run_manager

    record = run_manager.get_run_by_id(run_id)
    if record is None:
        raise IngestError(f"run '{run_id}' not found", status=404)
    if record.get("status") != "awaiting_input":
        raise IngestError(f"run '{run_id}' is not waiting for an answer", status=409)

    answer = {
        "value": value,
        "answered_at": _utc_now_iso(),
        "answered_by": answered_by or "",
    }
    run_manager.update_run(run_id, {"status": "answered", "pending_answer": answer})
    session_id = record.get("session_id")
    if session_id:
        _publish(str(session_id), {"type": "answered", "run_id": run_id, "value": value})
    return {"run_id": run_id, "status": "answered", "answer": answer}


def answer_for(connection: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """What the client polls: has anyone answered this run's question yet?

    Collecting the answer is what finishes the parked run. From this side the
    exchange is over at that point — asked, answered, delivered — and the work
    that follows is reported as its own run. Leaving it open instead would mean
    a status no retention pass may ever clear.
    """
    from managers import run_manager

    record = run_manager.get_run_by_id(run_id)
    if record is None or str(record.get("connection_id") or "") != str(connection["id"]):
        # Same answer for "not yours" and "does not exist", so a run id cannot
        # be probed for.
        raise IngestError(f"run '{run_id}' is not known for this connection", status=404)

    status = str(record.get("status") or "")
    if status == "awaiting_input":
        return {"run_id": run_id, "status": "waiting",
                "question": record.get("pending_question") or {}}
    if status != "answered":
        return {"run_id": run_id, "status": status or "unknown"}

    answer = record.get("pending_answer") or {}
    run_manager.close_run(run_id, status="completed", exit_code=0)
    return {"run_id": run_id, "status": "answered", "value": answer.get("value"),
            "answered_at": answer.get("answered_at")}


def _notify_question(connection: Dict[str, Any], run_id: str, pending: Dict[str, Any]) -> None:
    """Put the question in the operator's inbox. Best effort by design.

    A question nobody is told about is a run that waits forever, but a
    notification that fails must not fail the parking itself.
    """
    try:
        from plans import service as plan_service

        plan_service.create_notification(
            title=f"{connection.get('name') or connection['id']} is waiting for an answer",
            body=pending.get("question") or "A reported run stopped to ask a question.",
            severity="info",
            source={"origin": "connection", "connection_id": connection["id"], "run_id": run_id},
            workspace=connection.get("workspace") or None,
            channels=["dashboard"],
        )
    except Exception:
        pass


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _epoch_of(stamp: str, *, default: float) -> float:
    """An ISO timestamp as epoch seconds, or ``default`` if it is unreadable."""
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def reset_for_tests() -> None:
    """Drop all in-memory state. Used by tests, never in the running service."""
    with _lock:
        _runs.clear()
        _calls.clear()


__all__ = [
    "IngestError", "MAX_EVENTS_PER_REQUEST", "MAX_OPEN_RUNS", "RATE_LIMIT_PER_MINUTE",
    "answer_for", "answer_run", "check_rate", "close_run", "ingest_events",
    "interrupt_run", "open_run", "open_runs", "reset_for_tests",
]
