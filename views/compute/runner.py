"""
Frame-streaming driver for server compute.

Runs a server runtime (:mod:`views.compute`) for a bounded number of steps,
publishing a **frame** event to the ``view:<id>`` channel every ``stream_every``
steps and optionally recording them into a replayable clip. Runtime-agnostic:
it only relies on the ``step(dt)`` / ``frame()`` contract.

Budgets (steps / wall-time / frame count) keep a heavy job from running away —
the precise tier costs real CPU, so every job is bounded and reports what it did.
The job runs in a background thread (see the ``view_compute`` tool) so it streams
without blocking the agent turn; frames publish via the thread-safe broker.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from views.compute import create_runtime

log = logging.getLogger("views.compute.runner")

# Hard budgets — a compute job can never exceed these regardless of args.
MAX_STEPS = 20000
MAX_FRAMES = 600
MAX_WALL_SECONDS = 30.0


def _publish(view_id: str, event: Dict[str, Any]) -> None:
    try:
        from common.session_broker import broker
        broker.publish_threadsafe(f"view:{view_id}", event)
    except Exception:
        pass


def run_compute(
    view_id: str,
    runtime: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    steps: int = 400,
    dt: float = 0.01,
    max_frames: int = 200,
    record: bool = True,
    clip_name: str = "clip",
) -> Dict[str, Any]:
    """Run ``runtime`` for ``steps`` steps, streaming (and optionally recording)
    frames. Returns a summary; raises ``ValueError`` for an unknown runtime."""
    rt = create_runtime(runtime, params or {})
    if rt is None:
        raise ValueError(f"unknown server runtime: {runtime}")

    steps = max(1, min(MAX_STEPS, int(steps)))
    max_frames = max(1, min(MAX_FRAMES, int(max_frames)))
    stream_every = max(1, steps // max_frames)

    frames = []
    started = time.monotonic()
    seq = 0
    stopped = None
    _publish(view_id, {"type": "compute_start", "view_id": view_id, "runtime": runtime, "steps": steps})

    for i in range(steps):
        try:
            rt.step(dt)
        except Exception as exc:               # numerical blowup → stop cleanly
            stopped = f"runtime error: {exc}"
            break
        if i % stream_every == 0:
            channels = rt.frame()
            evt = {"type": "frame", "view_id": view_id, "t": round(i * dt, 4), "seq": seq, "channels": channels}
            _publish(view_id, evt)
            if record:
                frames.append({"t": evt["t"], "seq": seq, "channels": channels})
            seq += 1
        if time.monotonic() - started > MAX_WALL_SECONDS:
            stopped = "wall-time budget reached"
            break
        if seq >= max_frames:
            stopped = "frame budget reached"
            break

    clip_ref = None
    if record and frames:
        from views.store import save_clip
        clip_ref = save_clip(view_id, clip_name, {"runtime": runtime, "params": params or {}, "dt": dt, "frames": frames})

    summary = {
        "frames": seq,
        "clip": clip_ref,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "stopped": stopped,
    }
    _publish(view_id, {"type": "compute_done", "view_id": view_id, **summary})
    log.info("compute view=%s runtime=%s frames=%d clip=%s stopped=%s", view_id, runtime, seq, clip_ref, stopped)
    return summary


def run_compute_async(view_id: str, runtime: str, params: Optional[Dict[str, Any]] = None, **kw) -> None:
    """Fire-and-forget ``run_compute`` on a daemon thread (streams as it goes)."""
    import threading
    threading.Thread(target=lambda: _safe_run(view_id, runtime, params, **kw), daemon=True).start()


def _safe_run(view_id: str, runtime: str, params, **kw) -> None:
    try:
        run_compute(view_id, runtime, params, **kw)
    except Exception:
        log.exception("compute job failed for view=%s runtime=%s", view_id, runtime)
        _publish(view_id, {"type": "compute_done", "view_id": view_id, "frames": 0, "error": "compute failed"})


__all__ = ["run_compute", "run_compute_async", "MAX_STEPS", "MAX_FRAMES", "MAX_WALL_SECONDS"]
