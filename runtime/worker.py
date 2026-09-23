"""
The worker role: a process that claims launches from the queue and spawns
them on its own host.

``ah worker`` (or ``python -m runtime.worker``) runs this loop. It serves no
HTTP and starts none of the backend's singletons; it only turns rows of
``run_queue`` (common/run_queue.py) into processes, using the very same
launch code the backend uses in the default role
(``agents.agent_launcher.launch_prepared``, ``flow.launcher.launch_prepared``).
A deployment with one backend in the ``api`` role and N workers therefore
runs agents on N hosts; a worker with no Docker socket sets
``AGENTS_HUB_WORKER_MODES=local`` and is never handed a container run.

Each tick the worker:

1. renews the lease on every launch it still tracks (a child that has not
   exited yet), and closes the rows of children that have;
2. while it has capacity (``AGENTS_HUB_WORKER_CONCURRENCY``), claims the next
   launch it can run and spawns it. A launch that raises is handed back to
   the queue (``run_queue.requeue``) so another worker, or this one after the
   fault is fixed, can try again, up to ``run_queue.MAX_ATTEMPTS``.

Children are detached (their own session), so a worker that dies does not
take its runs down: the run's own heartbeat keeps it alive in the watchdog's
eyes, and the queue sweep closes the orphaned row. On SIGTERM the worker
stops claiming, keeps renewing the rows of children still running for up to
``AGENTS_HUB_WORKER_DRAIN_SECONDS`` so a replacement does not double-launch
them, releases its leases and exits (docs/workers.md).
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Spawned from anywhere; make the checkout importable like the other entrypoints.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

log = logging.getLogger("runtime.worker")

TICK_SECONDS = float(os.environ.get("AGENTS_HUB_WORKER_TICK_SECONDS", "2"))
LEASE_SECONDS = float(os.environ.get("AGENTS_HUB_WORKER_LEASE_SECONDS", "60"))
DRAIN_SECONDS = float(os.environ.get("AGENTS_HUB_WORKER_DRAIN_SECONDS", "300"))


def _launch(spec: Dict[str, Any]) -> None:
    """Dispatch one claimed launch to the launcher of its kind."""
    kind = str(spec.get("kind") or "task")
    if kind == "task":
        from agents.agent_launcher import launch_prepared
        launch_prepared(spec)
    elif kind == "flow":
        from flow.launcher import launch_prepared as launch_flow
        launch_flow(spec)
    else:
        raise ValueError(f"unknown launch kind {kind!r}")


def _child_alive(spec: Dict[str, Any]) -> Optional[bool]:
    """Whether the process spawned for a launch still runs on this host.
    None when the record does not say (the launch failed before a pid)."""
    kind = str(spec.get("kind") or "task")
    run_id = str(spec["run_id"])
    try:
        if kind == "flow":
            from flow import run_store
            rec = run_store.get_flow_run(run_id) or {}
        else:
            from managers.run_manager import get_run_by_id
            rec = get_run_by_id(run_id) or {}
    except Exception:
        return None
    status = str(rec.get("status") or "")
    if status not in ("running", "stop", "pending", "queued"):
        return False
    container = rec.get("container_name")
    if container:
        from managers.container_manager import container_running
        return container_running(str(container))
    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        return None
    from managers.runs.lifecycle import _pid_exists
    return _pid_exists(pid)


class Worker:
    def __init__(self, *, concurrency: Optional[int] = None,
                 modes: Optional[List[str]] = None, owner: Optional[str] = None) -> None:
        from common.config import settings, worker_execution_modes
        from common import leases

        self.owner = owner or leases.owner_id()
        self.concurrency = int(concurrency or settings.worker_concurrency or 4)
        self.modes = list(modes or worker_execution_modes())
        self.tracked: Dict[str, Dict[str, Any]] = {}   # run_id -> spec
        self.stopping = threading.Event()
        self.launched = 0
        self.failed = 0
        self.started_at = time.time()

    # ── one tick ─────────────────────────────────────────────────────────────

    def tick(self) -> int:
        """Renew, reap, claim. Returns how many launches were spawned."""
        from common import run_queue

        self._reap()
        if self.tracked:
            run_queue.renew(list(self.tracked), self.owner, ttl_seconds=LEASE_SECONDS)
        if self.stopping.is_set():
            return 0
        spawned = 0
        while len(self.tracked) < self.concurrency:
            job = run_queue.claim(self.owner, ttl_seconds=LEASE_SECONDS,
                                  execution_modes=self.modes)
            if job is None:
                break
            spec = dict(job.get("payload") or {})
            spec.setdefault("kind", job.get("kind"))
            spec.setdefault("run_id", job.get("run_id"))
            run_id = str(spec["run_id"])
            try:
                _launch(spec)
            except Exception as exc:  # noqa: BLE001 - a launch failure is a queue event
                self.failed += 1
                log.exception("launch of %s run %s failed", spec.get("kind"), run_id[:8])
                run_queue.requeue(run_id, f"{type(exc).__name__}: {exc}")
                # Not again this tick: the same row would come straight back,
                # and a fault on this host (no interpreter, no daemon) is
                # better retried by another worker or after a pause.
                break
            run_queue.mark_running(run_id, self.owner)
            spec["_claimed"] = time.time()
            self.tracked[run_id] = spec
            self.launched += 1
            spawned += 1
            log.info("worker %s launched %s run %s", self.owner, spec.get("kind"), run_id[:8])
        return spawned

    def _reap(self) -> None:
        from common import run_queue

        for run_id, spec in list(self.tracked.items()):
            alive = _child_alive(spec)
            if alive is False or (alive is None and time.time() - spec.get("_claimed", 0) > 60):
                run_queue.finish(run_id)
                self.tracked.pop(run_id, None)

    # ── the loop ─────────────────────────────────────────────────────────────

    def run_forever(self, *, install_signals: bool = True) -> None:
        from common import leases

        if install_signals:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    signal.signal(sig, lambda *_: self.request_stop())
                except Exception:
                    pass
        log.info("worker %s ready: modes=%s concurrency=%d host=%s",
                 self.owner, ",".join(self.modes), self.concurrency, socket.gethostname())
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("worker tick failed")
            self.stopping.wait(TICK_SECONDS)
        # Drain: keep the rows of running children leased until they exit or
        # the drain window closes, so a replacement worker never relaunches
        # them, then let go.
        deadline = time.time() + DRAIN_SECONDS
        while self.tracked and time.time() < deadline:
            try:
                self.tick()
            except Exception:
                log.exception("worker drain tick failed")
            time.sleep(min(TICK_SECONDS, 5.0))
        try:
            leases.release_all(self.owner)
        except Exception:
            pass
        log.info("worker %s stopped (%d launched, %d failed)", self.owner, self.launched, self.failed)

    def request_stop(self) -> None:
        if not self.stopping.is_set():
            log.info("worker %s stopping: no more claims, draining %d child(ren)",
                     self.owner, len(self.tracked))
        self.stopping.set()

    def status(self) -> Dict[str, Any]:
        return {
            "owner": self.owner, "host": socket.gethostname(), "modes": list(self.modes),
            "concurrency": self.concurrency, "tracked": list(self.tracked),
            "launched": self.launched, "failed": self.failed,
            "uptime_seconds": time.time() - self.started_at, "stopping": self.stopping.is_set(),
        }


def main(argv: Optional[List[str]] = None, *, run: Callable[[Worker], None] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Claim launches from the run queue and spawn them here.")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="Launches kept alive at once (AGENTS_HUB_WORKER_CONCURRENCY).")
    ap.add_argument("--modes", default=None,
                    help="Comma-separated: local, docker (AGENTS_HUB_WORKER_MODES).")
    ap.add_argument("--once", action="store_true", help="One tick, then exit (diagnostics).")
    args = ap.parse_args(argv)

    os.environ["AGENTS_HUB_ROLE"] = "worker"
    from common.logging_config import configure_logging
    try:
        configure_logging(None)
    except Exception:
        logging.basicConfig(level=logging.INFO)
    from common.bootstrap import ensure_initial_state
    ensure_initial_state()

    modes = [m.strip() for m in args.modes.split(",")] if args.modes else None
    worker = Worker(concurrency=args.concurrency, modes=modes)
    if args.once:
        n = worker.tick()
        print(f"worker tick: launched {n}, tracking {len(worker.tracked)}")
        return 0
    (run or Worker.run_forever)(worker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
