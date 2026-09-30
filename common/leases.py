"""
Leases for the roles only one process may play at a time.

The backend starts several loops that must not run twice across a deployment:
the plan scheduler, the run watchdog, the Telegram poller (two pollers on one
bot token fight over ``getUpdates``), the outbox drainer and, when the event
bridge is on, the external-state publisher. With one backend replica that is
trivially true. With several, each role is guarded by a row in
``service_leases`` (``common/migrations/0007_scale.sql``): the process that
holds an unexpired lease on a role runs it, everyone else checks back every
tick and takes over when the lease lapses.

A lease is taken and renewed with one read-modify-write under
``db.transaction()``, which is exclusive on both backends (``BEGIN
IMMEDIATE`` on SQLite, the advisory lock on Postgres), so two replicas racing
for the same role cannot both win. The owner id is ``AGENTS_HUB_INSTANCE_ID``
when set, else ``hostname:pid``, the same identity the broker bridge and the
plan scheduler's job leases use.

Usage in a loop::

    from common import leases

    while running:
        if leases.hold("scheduler"):
            tick()
        sleep(20)

:func:`hold` acquires or renews in one call and returns whether this process
holds the role right now. Pick a TTL longer than the loop's tick, so a slow
tick does not lose the role, and short enough that a dead holder is replaced
within a minute or so. Holders release their roles on shutdown so a restart
takes them back immediately instead of waiting the TTL out.

Fencing. A holder that stalls past its TTL (a GC pause, a slow tick) can be
superseded and then wake up still believing it holds the role. Every lease
row therefore carries ``version``, bumped on each insert and takeover (never
on a renewal); :func:`fencing_token` is the version this process got, and a
loop that writes on behalf of its role checks it inside the write's own
transaction::

    token = leases.fencing_token("outbox")
    with leases.fenced("outbox", token) as conn:   # raises LeaseLost
        conn.execute(...)

The check and the write share the exclusive transaction, so no takeover can
slip in between them.
"""
from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from common import db

log = logging.getLogger("common.leases")

#: Default lease length. Loops tick every 20 to 60 seconds; a holder that misses
#: two ticks in a row is treated as gone.
DEFAULT_TTL_SECONDS = float(os.environ.get("AGENTS_HUB_LEASE_TTL_SECONDS", "90"))

_owner_override: Optional[str] = None

#: What this process last acquired or renewed, per role: (owner, version).
#: The owner is kept beside the version because tests play two replicas from
#: one process with :func:`set_owner_id`; a token belongs to one owner only.
_held: Dict[str, Tuple[str, int]] = {}


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
    """Pin the owner id for this process (tests use it to play two replicas
    from one process)."""
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
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT owner, until, version FROM service_leases WHERE role = ?", (role,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO service_leases (role, owner, until, acquired_at, renewed_at, version) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (role, owner, until, now.isoformat(), now.isoformat()))
            _held[role] = (owner, 1)
            log.info("lease %s acquired by %s", role, owner)
            return True
        current_owner = str(row["owner"] or "")
        expires = _parse(row["until"])
        version = int(row["version"] or 0)
        if current_owner == owner:
            # A renewal keeps the version: the incarnation did not change.
            conn.execute(
                "UPDATE service_leases SET until = ?, renewed_at = ? WHERE role = ?",
                (until, now.isoformat(), role))
            _held[role] = (owner, version)
            return True
        if expires is not None and expires > now:
            if _held.get(role, ("", 0))[0] == owner:
                _held.pop(role, None)
            return False
        conn.execute(
            "UPDATE service_leases SET owner = ?, until = ?, acquired_at = ?, renewed_at = ?, "
            "version = ? WHERE role = ?",
            (owner, until, now.isoformat(), now.isoformat(), version + 1, role))
        _held[role] = (owner, version + 1)
        log.info("lease %s taken over by %s (was %s, version %d)",
                 role, owner, current_owner or "nobody", version + 1)
        return True


def release(role: str, owner: Optional[str] = None) -> bool:
    """Give ``role`` up if ``owner`` holds it. Returns True when a row was
    removed."""
    owner = owner or owner_id()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT owner FROM service_leases WHERE role = ?", (role,)).fetchone()
        if row is None or str(row["owner"] or "") != owner:
            return False
        conn.execute("DELETE FROM service_leases WHERE role = ?", (role,))
        if _held.get(role, ("", 0))[0] == owner:
            _held.pop(role, None)
        return True


def release_all(owner: Optional[str] = None) -> int:
    """Release every role ``owner`` holds (process shutdown)."""
    owner = owner or owner_id()
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT role FROM service_leases WHERE owner = ?", (owner,)).fetchall()
        for row in rows:
            conn.execute("DELETE FROM service_leases WHERE role = ?", (row["role"],))
            if _held.get(row["role"], ("", 0))[0] == owner:
                _held.pop(row["role"], None)
        return len(rows)


def holder(role: str) -> Optional[Dict[str, Any]]:
    """Who holds ``role`` and until when, or None when nobody does (an expired
    lease counts as nobody)."""
    row = db.get_conn().execute(
        "SELECT role, owner, until, acquired_at, renewed_at, version FROM service_leases "
        "WHERE role = ?",
        (role,)).fetchone()
    if row is None:
        return None
    expires = _parse(row["until"])
    if expires is None or expires <= _now():
        return None
    return dict(row)


def all_leases() -> List[Dict[str, Any]]:
    """Every lease row with its age, for health and metrics."""
    rows = db.get_conn().execute(
        "SELECT role, owner, until, acquired_at, renewed_at, version FROM service_leases "
        "ORDER BY role"
    ).fetchall()
    now = _now()
    out: List[Dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        expires = _parse(rec.get("until"))
        renewed = _parse(rec.get("renewed_at"))
        rec["expired"] = expires is None or expires <= now
        rec["age_seconds"] = (now - renewed).total_seconds() if renewed else None
        rec["mine"] = str(rec.get("owner") or "") == owner_id()
        out.append(rec)
    return out


def hold(role: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bool:
    """Acquire or renew ``role`` for this process; True when held.

    Never raises: a database hiccup means "not held this tick", and the loop
    tries again next time. A role nobody can hold for a while is visible on
    the health page (``service_leases`` shows an expired holder)."""
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
    """The lease version this process last acquired or renewed for ``role``,
    or None when it does not hold it. Take it right after :func:`hold` and
    pass it to :func:`verify` or :func:`fenced` with every write."""
    held = _held.get(role)
    if held is None or held[0] != owner_id():
        return None
    return held[1]


def verify(role: str, token: Optional[int], conn: Any = None) -> bool:
    """Whether ``role`` is still held by this process under ``token``: the
    row's owner is us, its version is ``token`` and it has not expired.

    Pass the transaction's ``conn`` to make the check part of a write (that
    is what :func:`fenced` does); without it the answer is only as fresh as
    the read."""
    if token is None:
        return False
    conn = conn if conn is not None else db.get_conn()
    row = conn.execute(
        "SELECT owner, until, version FROM service_leases WHERE role = ?", (role,)).fetchone()
    if row is None or str(row["owner"] or "") != owner_id():
        return False
    if int(row["version"] or 0) != int(token):
        return False
    expires = _parse(row["until"])
    return expires is not None and expires > _now()


@contextmanager
def fenced(role: str, token: Optional[int]) -> Iterator[Any]:
    """Open ``db.transaction()`` and check the lease inside it; yields the
    connection. Raises :class:`LeaseLost` before the body runs when the lease
    is no longer ours under ``token``, so nothing is written."""
    with db.transaction() as conn:
        if not verify(role, token, conn):
            raise LeaseLost(f"lease {role} is no longer held under version {token}")
        yield conn


__all__ = [
    "DEFAULT_TTL_SECONDS", "LeaseLost", "acquire", "all_leases", "fenced", "fencing_token",
    "held_by_me", "hold", "holder", "owner_id", "release", "release_all", "set_owner_id",
    "verify",
]
