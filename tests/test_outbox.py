"""The outbox behind outbound delivery (notify/outbound.py): rows first,
delivery by the lease holder, retries with backoff."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import db, leases
from notify import outbound


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(outbound, "RETRY_BACKOFF_SECONDS", 0)
    leases.set_owner_id("replica-a")
    yield
    leases.set_owner_id(None)


def _endpoint(**over):
    return {"id": "e1", "kind": "webhook", "url": "https://example.com/hook", **over}


def _event():
    return {"id": "evt-1", "type": "notification", "workspace": "default",
            "data": {"title": "Hi", "body": "There", "severity": "info"}}


def test_dispatch_writes_a_row_and_the_drainer_delivers_it(monkeypatch):
    delivered = []
    monkeypatch.setattr(outbound, "deliver", lambda e, ev: delivered.append((e, ev)) or True)
    outbound.dispatch(_endpoint(), _event())
    outbound._queue.join()
    assert delivered == [(_endpoint(), _event())]
    assert outbound.stats() == {"pending": 0, "dead": 0}
    assert outbound.pending() == []


def test_a_failed_delivery_is_retried_later_with_backoff(monkeypatch):
    monkeypatch.setattr(outbound, "deliver", lambda e, ev: False)
    outbound.enqueue(_endpoint(), _event())
    assert outbound.drain() == 0
    row = db.get_conn().execute("SELECT attempts, next_attempt_at, delivered_at FROM outbox").fetchone()
    assert row["attempts"] == 1 and row["delivered_at"] is None
    assert datetime.fromisoformat(row["next_attempt_at"]) > datetime.now(timezone.utc) + timedelta(seconds=20)
    assert outbound.pending() == []
    assert outbound.stats()["pending"] == 1


def test_given_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(outbound, "deliver", lambda e, ev: False)
    outbound.enqueue(_endpoint(), _event())
    for _ in range(outbound.MAX_ATTEMPTS):
        with db.transaction() as conn:
            conn.execute("UPDATE outbox SET next_attempt_at = ?",
                         ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        outbound.drain()
    assert outbound.stats() == {"pending": 0, "dead": 1}


def test_only_the_lease_holder_drains(monkeypatch):
    delivered = []
    monkeypatch.setattr(outbound, "deliver", lambda e, ev: delivered.append(1) or True)
    outbound.enqueue(_endpoint(), _event())
    leases.acquire(outbound.LEASE_ROLE, "replica-b", 60)
    assert outbound.drain() == 0
    assert not delivered
    assert outbound.drain(require_lease=False) == 1


def test_rows_survive_the_process_that_raised_them(monkeypatch):
    """A row left by an earlier process is delivered by the next drainer."""
    outbound.enqueue(_endpoint(), _event())
    delivered = []
    monkeypatch.setattr(outbound, "deliver", lambda e, ev: delivered.append(ev["id"]) or True)
    outbound.start()
    outbound._queue.join()
    assert delivered == ["evt-1"]


def test_prune_drops_old_delivered_rows(monkeypatch):
    monkeypatch.setattr(outbound, "deliver", lambda e, ev: True)
    outbound.enqueue(_endpoint(), _event())
    outbound.drain()
    with db.transaction() as conn:
        conn.execute("UPDATE outbox SET created_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
    assert outbound.prune(older_than_days=7) == 1
