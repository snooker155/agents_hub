"""
Leases for the roles only one loop may play at a time, kept in this process.

The backend starts several loops that must not run twice: the plan
scheduler, the run watchdog. This hub is
one process, so a role is held by whoever asked for it first and the
bookkeeping is a dictionary, not a table; the module keeps the API the loops
were written against (``hold``, ``fencing_token``, ``fenced``) so each loop
still guards its writes the same way, and tests can still play a second
owner with :func:`set_owner_id`.

Usage in a loop::

    from common import leases

    while running:
        if leases.hold("scheduler"):
            tick()
        sleep(20)

:func:`hold` acquires or renews in one call and returns whether this process
holds the role right now. A lease carries ``version``, bumped on each insert
and takeover (never on a renewal); :func:`fencing_token` is the version this
owner got, and a loop that writes on behalf of its role checks it inside the
write's own transaction with :func:`fenced`, which raises :class:`LeaseLost`
before the body runs when the role has moved on.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from common import db

log = logging.getLogger("common.leases")

#: Default lease length. Loops tick every 20 to 60 seconds; a holder that misses
#: two ticks in a row is treated as gone.
DEFAULT_TTL_SECONDS = float(os.environ.get("AGENTS_HUB_LEASE_TTL_SECONDS", "90"))

_owner_override: Optional[str] = None

_lock = threading.Lock()
#: role -> {owner, until, acquired_at, renewed_at, version}
_leases: Dict[str, Dict[str, Any]] = {}


class LeaseLost(Exception):
    """The lease this write was fenced by is no longer ours: expired, taken
    over, or released. Raised before the write, never after."""


def owner_id() -> str:
    """This process's identity as a lease owner."""
    if _owner_override:
        return _owner_override
    explicit = os.environ.get("AGENTS_HUB_INSTANCE_ID", "").strip()
    if explicit:
        return explicit
    return f"{socket.gethostname()}:{os.getpid()}"


def set_owner_id(owner: Optional[str]) -> None:
    """Pin the owner id for this process (tests use it to play two owners)."""
    global _owner_override
    _owner_override = owner


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _expired(rec: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    expires = _parse(rec.get("until"))
    return expires is None or expires <= (now or _now())


def acquire(role: str, owner: Optional[str] = None,
            ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bool:
    """Take ``role`` for ``owner``, or renew it when already held by them.

    Returns True when ``owner`` holds the role after the call. A role held by
    someone else with an unexpired lease is refused; an expired one is taken
    over.
    """
    owner = owner or owner_id()
    now = _now()
    until = (now + timedelta(seconds=ttl_seconds)).isoformat()
    with _lock:
        rec = _leases.get(role)
        if rec is None:
            _leases[role] = {"role": role, "owner": owner, "until": until,
                             "acquired_at": now.isoformat(), "renewed_at": now.isoformat(),
                             "version": 1}
            log.info("lease %s acquired by %s", role, owner)
            return True
        if rec["owner"] == owner:
            rec["until"] = until
            rec["renewed_at"] = now.isoformat()
            return True
        if not _expired(rec, now):
            return False
        log.info("lease %s taken over by %s from %s", role, owner, rec["owner"])
        rec.update({"owner": owner, "until": until, "acquired_at": now.isoformat(),
                    "renewed_at": now.isoformat(), "version": int(rec.get("version") or 0) + 1})
        return True


def release(role: str, owner: Optional[str] = None) -> bool:
    """Give ``role`` up, if ``owner`` holds it. True when it was held."""
    owner = owner or owner_id()
    with _lock:
        rec = _leases.get(role)
        if rec is None or rec["owner"] != owner:
            return False
        del _leases[role]
        return True


def release_all(owner: Optional[str] = None) -> int:
    """Release every role ``owner`` holds (process shutdown)."""
    owner = owner or owner_id()
    with _lock:
        mine = [role for role, rec in _leases.items() if rec["owner"] == owner]
        for role in mine:
            del _leases[role]
        return len(mine)


def holder(role: str) -> Optional[Dict[str, Any]]:
    """Who holds ``role`` and until when, or None when nobody does (an expired
    lease counts as nobody)."""
    with _lock:
        rec = _leases.get(role)
        if rec is None or _expired(rec):
            return None
        return dict(rec)


def all_leases() -> List[Dict[str, Any]]:
    """Every lease with its age, for the health page."""
    now = _now()
    out: List[Dict[str, Any]] = []
    with _lock:
        records = [dict(rec) for _, rec in sorted(_leases.items())]
    for rec in records:
        renewed = _parse(rec.get("renewed_at"))
        rec["expired"] = _expired(rec, now)
        rec["age_seconds"] = (now - renewed).total_seconds() if renewed else None
        rec["mine"] = str(rec.get("owner") or "") == owner_id()
        out.append(rec)
    return out


def hold(role: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bool:
    """Acquire or renew ``role`` for this process; True when held."""
    try:
        return acquire(role, owner_id(), ttl_seconds)
    except Exception:  # noqa: BLE001 - never raises, module contract: a hiccup means "not held this tick"
        log.warning("could not acquire or renew lease %s", role, exc_info=True)
        return False


def held_by_me(role: str) -> bool:
    """Whether this process holds ``role`` right now, without renewing it."""
    rec = holder(role)
    return bool(rec and str(rec.get("owner") or "") == owner_id())


def fencing_token(role: str) -> Optional[int]:
    """The lease version this process holds ``role`` under, or None when it
    does not hold it. Take it right after :func:`hold` and pass it to
    :func:`verify` or :func:`fenced` with every write."""
    rec = holder(role)
    if rec is None or str(rec.get("owner") or "") != owner_id():
        return None
    return int(rec.get("version") or 0)


def verify(role: str, token: Optional[int], conn: Any = None) -> bool:
    """Whether ``role`` is still held by this process under ``token``: the
    owner is us, the version is ``token`` and it has not expired. ``conn`` is
    accepted for the callers that pass their transaction's connection."""
    if token is None:
        return False
    rec = holder(role)
    if rec is None or str(rec.get("owner") or "") != owner_id():
        return False
    return int(rec.get("version") or 0) == int(token)


@contextmanager
def fenced(role: str, token: Optional[int]) -> Iterator[Any]:
    """Open ``db.transaction()`` and check the lease inside it; yields the
    connection. Raises :class:`LeaseLost` before the body runs when the lease
    is no longer ours under ``token``, so nothing is written."""
    with db.transaction() as conn:
        if not verify(role, token, conn):
            raise LeaseLost(f"lease {role} is no longer held under version {token}")
        yield conn


def reset() -> None:
    """Forget every lease. Tests only."""
    with _lock:
        _leases.clear()


__all__ = [
    "DEFAULT_TTL_SECONDS", "LeaseLost", "acquire", "all_leases", "fenced", "fencing_token",
    "held_by_me", "hold", "holder", "owner_id", "release", "release_all", "reset",
    "set_owner_id", "verify",
]
