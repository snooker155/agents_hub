"""The transport: batched, off the caller's thread, and never fatal.

This runs inside somebody else's production process. Three rules follow, and
they outrank everything else this package does:

1. **It must not raise into the graph.** A monitoring handler that throws takes
   a customer's run down to report a chart. Every public method here swallows
   its errors and, at most, hands them to an ``on_error`` callback.
2. **It must not make the graph wait.** All HTTP happens on a worker thread; the
   graph's thread only appends to a queue.
3. **It must not grow without bound.** If the hub is unreachable the queue fills
   and the oldest events are dropped, because a monitoring buffer that eats a
   production process's memory is worse than missing telemetry.

The hub is reached with :mod:`urllib` from the standard library rather than
``requests`` or ``httpx`` on purpose: adding a dependency to someone's
production image is a conversation, and this package is meant to be an easy yes.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

# Named for the package, so an application that configures logging can raise,
# lower or silence this one channel without touching the rest of its own.
logger = logging.getLogger("agents_hub_langgraph")

# Whether the first failure has been reported. Once per *process*, not per
# client: the recommended usage is one tracer per invocation, so a per-client
# warning would mean a line per run for as long as a hub stayed unreachable,
# which is the noise this package exists not to make.
_warned = False
_warn_lock = threading.Lock()


def warn_once(exc: Exception) -> None:
    """Say once that reporting is failing, then never again.

    Silence is right for a package living in someone's production process, and
    wrong for the ten minutes somebody spends wiring it up: a mistyped token
    looks exactly like success. One warning is the compromise, with ``on_error``
    and :attr:`HubTracer.errors` for anyone who wants all of them.
    """
    global _warned
    with _warn_lock:
        if _warned:
            return
        _warned = True
    logger.warning(
        "agents-hub: reporting failed (%s: %s). Further failures are silent: "
        "pass on_error= to see them, or read tracer.errors. Telemetry only, "
        "the graph is unaffected.",
        type(exc).__name__, exc,
    )


def reset_warning() -> None:
    """Allow the warning again. For tests, which run many failures in one process."""
    global _warned
    with _warn_lock:
        _warned = False

DEFAULT_FLUSH_INTERVAL = 0.5
DEFAULT_MAX_BATCH = 200
# Frames held while the hub is unreachable or the run is still opening. Past
# this, the oldest go: the newest events describe where the run is now.
DEFAULT_QUEUE_SIZE = 10_000
DEFAULT_TIMEOUT = 10.0
# How long the worker thread waits with nothing to do before ending itself. The
# recommended usage is one tracer per invocation, so a server handling a
# thousand requests would otherwise leak a thousand threads that live until the
# process does. The thread is restarted by the next enqueue.
DEFAULT_IDLE_EXIT = 30.0
# Frames are held until the run they belong to has been opened. If the open
# failed — the hub was down, the token was revoked — they have nowhere to go,
# and holding them would make the batch buffer the one unbounded thing in a
# package whose queue is carefully bounded.
MAX_PENDING_WITHOUT_RUN = DEFAULT_MAX_BATCH * 4


class Transport:
    """How a payload reaches the hub. Replaceable, which is what makes this testable."""

    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def get(self, path: str) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method="GET",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
        return json.loads(raw)

    def post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
        return json.loads(raw)


class HubClient:
    """A queue, a worker thread, and one reported run at a time.

    The run is opened lazily: the first frame arrives before the hub has
    answered with a run id, so frames queue behind the open and are sent once it
    lands. That keeps the graph's first token off the network round trip that
    starts the run.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        max_batch: int = DEFAULT_MAX_BATCH,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        on_error: Optional[Callable[[Exception], None]] = None,
        idle_exit: float = DEFAULT_IDLE_EXIT,
    ):
        self._transport = transport
        self._flush_interval = flush_interval
        self._max_batch = max_batch
        self._on_error = on_error
        self._idle_exit = idle_exit
        self._queue: "queue.Queue[tuple]" = queue.Queue(maxsize=queue_size)
        self._run_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._lock = threading.Lock()
        # Work accepted but not yet delivered. A counter rather than an "idle"
        # flag because the flag had a hole in it: between the worker taking an
        # item off the queue and marking itself busy, the queue was empty and
        # nothing was flagged, so a flush in that window reported everything
        # sent when the item had not even been posted. The counter is
        # incremented before the item is queued and decremented after it has
        # actually gone, so there is no such window.
        self._inflight = 0
        self._stopped = False
        self._worker_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self.dropped = 0
        self.errors = 0
        # The last run parked for a question, kept so the answer can be
        # collected against it after the run itself has gone.
        self.parked_run_id: Optional[str] = None
        # Started lazily: constructing a tracer that is never used should not
        # cost a thread.

    # ── what the tracer calls ───────────────────────────────────────────────

    @property
    def run_id(self) -> Optional[str]:
        with self._lock:
            return self._run_id

    def open(self, payload: Dict[str, Any]) -> None:
        self._put(("open", payload))

    def emit(self, frame: Dict[str, Any]) -> None:
        self._put(("frame", frame))

    def close(self, payload: Dict[str, Any]) -> None:
        self._put(("close", payload))

    def interrupt(self, payload: Dict[str, Any]) -> None:
        """Park the run: the graph stopped to ask a human something."""
        self._put(("interrupt", payload))

    def read_answer(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Ask the hub whether the question has been answered yet.

        Synchronous, unlike everything else here, and deliberately so: the
        caller is a graph that cannot continue until it has the answer, so there
        is nothing to gain by queueing this behind the event batches.
        """
        try:
            return self._transport.get(f"/api/ingest/runs/{run_id}/answer")
        except Exception as exc:  # noqa: BLE001 - a hub that is down is not fatal
            self._fail(exc)
            return None

    def topology(self, payload: Dict[str, Any]) -> None:
        self._put(("topology", payload))

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything accepted has reached the hub. True if it did.

        Worth calling before a short-lived script exits: without it the process
        can end with a run still only half reported.
        """
        self._ensure_worker()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._inflight == 0:
                    return True
            time.sleep(0.02)
        with self._lock:
            return self._inflight == 0

    def stop(self, timeout: float = 5.0) -> bool:
        """Send what is queued, then end the worker thread. True if it drained.

        The worker ends by itself after a while idle, which is enough for a
        process that keeps running. This is for one that does not, or for a test
        that must not leave a thread behind still writing after it finished.

        The stop is a message in the queue rather than a flag the worker
        notices eventually: the worker spends its time blocked on that queue, so
        a flag alone would be read only after the next poll interval, and a
        caller waiting for the thread would pay that wait for nothing.
        """
        with self._worker_lock:
            self._stopped = True
            worker = self._worker
        if worker is None or not worker.is_alive():
            return self._inflight == 0
        try:
            self._queue.put_nowait(("stop", None))
        except queue.Full:
            pass
        worker.join(timeout=timeout)
        with self._worker_lock:
            self._worker = None
        with self._lock:
            return self._inflight == 0

    # ── the worker ──────────────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        """Start the worker if it is not running. Cheap, and safe to call often."""
        with self._worker_lock:
            if self._stopped:
                return
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker, name="agents-hub-tracer", daemon=True)
            self._worker.start()

    def _done(self, count: int = 1) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - count)

    def _put(self, item: tuple) -> None:
        # Counted before it is queued: an item that is in the queue but not yet
        # counted is an item a concurrent flush would not wait for.
        with self._lock:
            self._inflight += 1
        try:
            self._queue.put_nowait(item)
            # After the enqueue, never before: a worker deciding to exit checks
            # the queue while holding the same lock this takes, so an item that
            # is already queued cannot be stranded by a thread on its way out.
            self._ensure_worker()
            return
        except queue.Full:
            # Drop the oldest rather than the newest: what the run is doing now
            # is more useful than what it was doing when the hub went away.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except queue.Empty:
                pass
            except queue.Full:
                pass
            # The dropped item was counted when it arrived; the one replacing it
            # is counted too, so the oldest one's count is released here.
            self._done()
            self.dropped += 1
        self._ensure_worker()

    def _fail(self, exc: Exception) -> None:
        self.errors += 1
        if self._on_error is None:
            warn_once(exc)
            return
        try:
            self._on_error(exc)
        except Exception:
            # An error handler that itself fails must not become the failure
            # this whole class exists to avoid.
            pass

    def _should_exit(self, pending: List[Dict[str, Any]], idle_since: float) -> bool:
        """Whether this worker may end. Decided under the start/stop lock.

        Re-checking the queue inside the lock is what makes the restart safe: an
        item enqueued a moment ago either is visible here, and the worker stays,
        or its ``_ensure_worker`` is blocked on this lock and will start a fresh
        worker the instant this one clears ``self._worker``.
        """
        if self._stopped and self._queue.empty() and not pending:
            with self._worker_lock:
                self._worker = None
            return True
        if self._run_id is not None:
            return False
        if pending:
            # No run to send them to, and long enough idle that none is coming.
            self.dropped += len(pending)
            self._done(len(pending))
            pending.clear()
        if time.time() - idle_since < self._idle_exit:
            return False
        with self._worker_lock:
            if not self._queue.empty():
                return False
            self._worker = None
            return True

    def _run_worker(self) -> None:
        pending: List[Dict[str, Any]] = []
        last_flush = time.time()
        idle_since = time.time()
        while True:
            try:
                kind, payload = self._queue.get(timeout=self._flush_interval)
                idle_since = time.time()
            except queue.Empty:
                if pending and self.run_id:
                    self._send_frames(pending)
                    self._done(len(pending))
                    pending = []
                    last_flush = time.time()
                    idle_since = time.time()
                if self._should_exit(pending, idle_since):
                    return
                continue

            try:
                if kind == "open":
                    self._do_open(payload)
                    self._done()
                elif kind == "frame":
                    # Stays counted until the batch it joins is actually sent.
                    pending.append(payload)
                elif kind == "topology":
                    self._safe_post("/api/ingest/topology", payload)
                    self._done()
                elif kind == "stop":
                    # Deliver what is already queued, then end. Anything put in
                    # after this is dropped on purpose: the caller said stop.
                    if pending:
                        self._send_frames(pending)
                        self._done(len(pending))
                        pending = []
                    while not self._queue.empty():
                        try:
                            more_kind, more_payload = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if more_kind == "frame":
                            self._send_frames([more_payload])
                            self._done()
                        elif more_kind == "open":
                            self._do_open(more_payload)
                            self._done()
                        elif more_kind == "close":
                            self._do_close(more_payload)
                            self._done()
                        elif more_kind == "interrupt":
                            self._do_interrupt(more_payload)
                            self._done()
                        elif more_kind == "topology":
                            self._safe_post("/api/ingest/topology", more_payload)
                            self._done()
                    with self._worker_lock:
                        self._worker = None
                    return
                elif kind in ("close", "interrupt"):
                    if pending:
                        self._send_frames(pending)
                        self._done(len(pending))
                        pending = []
                    if kind == "close":
                        self._do_close(payload)
                    else:
                        self._do_interrupt(payload)
                    self._done()
            except Exception as exc:  # noqa: BLE001 - a worker that dies stops all reporting
                self._fail(exc)
                # Whatever it was, it is not coming back: leaving it counted
                # would make every later flush wait out its whole timeout.
                if kind != "frame":
                    self._done()

            if self.run_id is None and len(pending) > MAX_PENDING_WITHOUT_RUN:
                # Drop the oldest: what the run is doing now is more use than
                # what it was doing when the hub stopped answering.
                overflow = len(pending) - MAX_PENDING_WITHOUT_RUN
                del pending[:overflow]
                self._done(overflow)
                self.dropped += overflow

            due = len(pending) >= self._max_batch or (time.time() - last_flush) >= self._flush_interval
            if pending and self.run_id and due:
                self._send_frames(pending)
                self._done(len(pending))
                pending = []
                last_flush = time.time()

    def _do_open(self, payload: Dict[str, Any]) -> None:
        body = self._safe_post("/api/ingest/runs", payload)
        with self._lock:
            self._run_id = (body or {}).get("run_id")
            self._session_id = (body or {}).get("session_id")

    def _do_close(self, payload: Dict[str, Any]) -> None:
        run_id = self.run_id
        if run_id:
            self._safe_post(f"/api/ingest/runs/{run_id}/close", payload)
        with self._lock:
            self._run_id = None
            self._session_id = None

    def _do_interrupt(self, payload: Dict[str, Any]) -> None:
        """Park the run and remember which one is waiting.

        The run id is kept after the run leaves: the caller asks for the answer
        by it, and by then the client has moved on to whatever comes next.
        """
        run_id = self.run_id
        if run_id:
            self._safe_post(f"/api/ingest/runs/{run_id}/interrupt", payload)
        with self._lock:
            self.parked_run_id = run_id
            self._run_id = None
            self._session_id = None

    def _send_frames(self, frames: List[Dict[str, Any]]) -> None:
        run_id = self.run_id
        if not run_id:
            # The run never opened (the hub was down when it started). The
            # frames have nowhere to go; dropping them is the only option that
            # does not grow without bound.
            self.dropped += len(frames)
            return
        for start in range(0, len(frames), self._max_batch):
            batch = frames[start:start + self._max_batch]
            self._safe_post(f"/api/ingest/runs/{run_id}/events", {"events": batch})

    def _safe_post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return self._transport.post(path, payload)
        except Exception as exc:  # noqa: BLE001 - the hub being down is not the graph's problem
            self._fail(exc)
            return None


__all__ = ["HubClient", "Transport", "DEFAULT_FLUSH_INTERVAL", "DEFAULT_MAX_BATCH"]
