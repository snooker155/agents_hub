"""Inbound signature verification and delivery idempotency.

Shared by every webhook this hub accepts from the outside: the external node
runner (``routes/external.py``), the flow trigger (``routes/flows.py``) and
the task-filing webhook (``routes/notify.py``). All three verify the same
HMAC scheme :mod:`notify.outbound` uses to sign what it sends out, so one
function here is the single place that scheme is checked against.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from notify import store as notify_store

DEFAULT_TOLERANCE_SECONDS = 300


def sign_payload(secret: str, raw_body: bytes) -> str:
    """``sha256=<hex hmac>`` of ``raw_body`` with ``secret``.

    The same scheme :func:`notify.outbound._deliver_webhook` signs with, and
    the one :func:`verify_signature` checks an incoming request against.
    """
    mac = hmac.new((secret or "").encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify_signature(
    secret: str,
    raw_body: bytes,
    header_value: Optional[str],
    timestamp: Optional[str],
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """True when ``header_value`` is a valid, fresh signature of ``raw_body``.

    Fails closed: a missing secret, header or timestamp, a timestamp more than
    ``tolerance`` seconds from now, or a mismatched signature are all
    rejections. The comparison is constant-time so the endpoint cannot be
    probed byte by byte.
    """
    if not secret or not header_value or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > tolerance:
        return False
    expected = sign_payload(secret, raw_body)
    return hmac.compare_digest(expected, header_value)


def seen_delivery(delivery_id: str) -> bool:
    """True when this delivery id was already processed within 24 hours.

    Records it as seen the first time, as a side effect, so a caller that
    gets False back can go on to act on the payload exactly once.
    """
    return notify_store.record_delivery(delivery_id)
