"""Service leases (common/leases.py): the roles only one process may play.

Two owners are played from one process through ``set_owner_id``; the
database is what arbitrates, exactly as it does between two replicas.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import db, leases


@pytest.fixture(autouse=True)
def _owner():
    leases.set_owner_id("replica-a")
    yield
    leases.set_owner_id(None)


def _expire(role: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE service_leases SET until = ? WHERE role = ?", (past, role))


def test_first_caller_takes_the_role_and_renews_it():
    assert leases.acquire("scheduler", "replica-a", 60) is True
    first = leases.holder("scheduler")
    assert first["owner"] == "replica-a"
    assert leases.acquire("scheduler", "replica-a", 60) is True
    assert leases.holder("scheduler")["until"] >= first["until"]


def test_a_live_lease_is_refused_to_another_owner():
    assert leases.acquire("scheduler", "replica-a", 60) is True
    assert leases.acquire("scheduler", "replica-b", 60) is False
    assert leases.holder("scheduler")["owner"] == "replica-a"


def test_an_expired_lease_is_taken_over():
    assert leases.acquire("scheduler", "replica-a", 60) is True
    _expire("scheduler")
    assert leases.holder("scheduler") is None
    assert leases.acquire("scheduler", "replica-b", 60) is True
    assert leases.holder("scheduler")["owner"] == "replica-b"
    # The old owner does not get it back by renewing.
    assert leases.acquire("scheduler", "replica-a", 60) is False


def test_release_only_by_the_holder():
    leases.acquire("telegram", "replica-a", 60)
    assert leases.release("telegram", "replica-b") is False
    assert leases.holder("telegram")["owner"] == "replica-a"
    assert leases.release("telegram", "replica-a") is True
    assert leases.holder("telegram") is None


def test_release_all_drops_every_role_of_one_owner():
    for role in ("scheduler", "watchdog", "outbox"):
        leases.acquire(role, "replica-a", 60)
    leases.acquire("telegram", "replica-b", 60)
    assert leases.release_all("replica-a") == 3
    roles = {r["role"]: r for r in leases.all_leases()}
    assert set(roles) == {"telegram"}


def test_hold_uses_this_process_identity_and_reports_ownership():
    assert leases.hold("watchdog", 60) is True
    assert leases.held_by_me("watchdog") is True
    leases.set_owner_id("replica-b")
    assert leases.hold("watchdog", 60) is False
    assert leases.held_by_me("watchdog") is False


def test_all_leases_marks_expiry_and_ownership():
    leases.acquire("scheduler", "replica-a", 60)
    leases.acquire("outbox", "replica-b", 60)
    _expire("outbox")
    rows = {r["role"]: r for r in leases.all_leases()}
    assert rows["scheduler"]["mine"] is True and rows["scheduler"]["expired"] is False
    assert rows["outbox"]["mine"] is False and rows["outbox"]["expired"] is True
    assert rows["scheduler"]["age_seconds"] is not None


def test_hold_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("database gone")
    monkeypatch.setattr(leases, "acquire", _boom)
    assert leases.hold("scheduler") is False


def test_two_threads_racing_for_one_role_exactly_one_wins():
    import threading
    wins = []
    barrier = threading.Barrier(2)

    def _try(owner):
        leases.set_owner_id(owner)
        barrier.wait()
        if leases.acquire("scheduler", owner, 60):
            wins.append(owner)

    threads = [threading.Thread(target=_try, args=(o,)) for o in ("t-1", "t-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
