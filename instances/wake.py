"""
Waiting for a carrier's mailbox to get a message.

A resident instance (instances/carrier.py) waits on its mailbox between runs.
:func:`wait` (called by the carrier's loop) checks the mailbox's indexed
pending query every ``AGENTS_HUB_INSTANCE_POLL_SECONDS`` (default 0.5 s), so
a message is picked up within that interval of being written. One indexed
lookup per half second per live instance is cheap next to a model call, and
needs nothing beyond the database the backend and the carrier share.

The mailbox row is the truth: the carrier claims it and answers in its own run.
"""
from __future__ import annotations

import os
import time

DEFAULT_POLL_SECONDS = 0.5


def poll_seconds() -> float:
    try:
        value = float(os.environ.get("AGENTS_HUB_INSTANCE_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    except ValueError:
        value = DEFAULT_POLL_SECONDS
    return min(max(value, 0.05), 10.0)


def _pending(instance_id: str) -> bool:
    from instances import inbox
    try:
        return inbox.has_pending(instance_id)
    except Exception:  # noqa: BLE001 - a failed read is retried on the next tick
        return False


def wait(instance_id: str, timeout: float) -> bool:
    """Block up to ``timeout`` seconds for mail. True when a message may be
    waiting, False on a quiet timeout."""
    if _pending(instance_id):
        return True
    deadline = time.monotonic() + max(0.0, float(timeout))
    interval = poll_seconds()
    while True:
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(min(interval, deadline - now))
        if _pending(instance_id):
            return True


__all__ = ["DEFAULT_POLL_SECONDS", "poll_seconds", "wait"]
