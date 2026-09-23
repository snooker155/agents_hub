"""
The sign of life every entity run's process gives, and how a stop reaches it.

An agent run has beaten its heartbeat since stage 2 of the scaling plan
(runtime/agent_run.py); the watchdog judges a run by that beat and never by a
pid it cannot see. The processes that run a flow, a team or a scenario need
the same thread, and the same trick for a stop requested from another host:
a stop marks the record ``stopping`` (common/entity_runs.py), the beat reads
the status back and delivers the stop locally. For an agent run "locally"
means a SIGTERM to itself; for a team or a scenario it means setting the
in-memory event the runner already polls between turns and ticks, so the
run ends at its next safe point with its record closed by its own hands.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

from common.logging_config import marker_logger

log = logging.getLogger("runtime.entity_heartbeat")
_marker = marker_logger(__name__)

#: Seconds between beats. The watchdog treats a run quiet for
#: ``ENTITY_HEARTBEAT_STALE_SECONDS`` (managers/run_watchdog.py) as dead, so
#: this must be several times shorter.
HEARTBEAT_SECONDS = float(os.environ.get("RUN_HEARTBEAT_SECONDS", "15"))


class EntityHeartbeat(threading.Thread):
    """Stamps ``entity_runs.heartbeat_at`` every few seconds for as long as
    the process lives, and calls ``on_stop`` once when the record says
    ``stopping``.

    ``on_stop`` runs on this thread; it should only set an event or a flag
    (``teams.control.request_stop``, ``playground.control.request_stop``)
    and return. A run that ignores the request for ``force_after`` beats is
    sent a SIGTERM, so a hung runner still ends.
    """

    def __init__(self, run_id: str, on_stop: Optional[Callable[[], None]] = None, *,
                 interval: Optional[float] = None, force_after: int = 8) -> None:
        super().__init__(name=f"entity-heartbeat-{run_id[:8]}", daemon=True)
        self.run_id = run_id
        self.on_stop = on_stop
        self.interval = float(interval or HEARTBEAT_SECONDS)
        self.force_after = int(force_after)
        # Not ``_stop``: threading.Thread has a private ``_stop()`` of its own
        # on Python 3.11 and 3.12, called from join().
        self._halt = threading.Event()
        self.beats = 0
        self.stop_seen = 0

    def beat_once(self) -> Optional[str]:
        """One beat: stamp the heartbeat, read the status back, deliver a
        stop request. Returns the status read, for tests and callers that
        beat by hand (a runner that has its own tick loop)."""
        from common import entity_runs
        try:
            status = entity_runs.touch_heartbeat(self.run_id)
        except Exception:  # noqa: BLE001 - a missed beat is not worth ending the run over
            log.debug("heartbeat write failed for %s", self.run_id, exc_info=True)
            return None
        self.beats += 1
        if status == "stopping":
            self.stop_seen += 1
            if self.stop_seen == 1:
                _marker.info(f"[heartbeat] stop requested for run {self.run_id}; ending at the next safe point")
                if self.on_stop is not None:
                    try:
                        self.on_stop()
                    except Exception:  # noqa: BLE001 - the stop hook is best-effort; the force below still applies
                        log.debug("on_stop hook failed for %s", self.run_id, exc_info=True)
            elif self.stop_seen >= self.force_after:
                _marker.info(f"[heartbeat] run {self.run_id} did not stop on request; terminating")
                self._halt.set()
                try:
                    import signal
                    os.kill(os.getpid(), signal.SIGTERM)
                except Exception:  # noqa: BLE001 - last resort
                    os._exit(143)
        return status

    def run(self) -> None:
        while not self._halt.wait(self.interval):
            self.beat_once()

    def stop(self) -> None:
        self._halt.set()


__all__ = ["EntityHeartbeat", "HEARTBEAT_SECONDS"]
