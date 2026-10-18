"""Leases (common/leases.py): the roles only one loop may play at a time.

One process holds every role in this edition, so the leases live in a
dictionary. These tests pin the API the loops are written against: acquire
and renew, refusal while another owner holds an unexpired lease, takeover of
an expired one with a new fencing token, and fenced writes that refuse once
the lease moved on.
"""
from datetime import datetime, timedelta, timezone

import pytest

from common import leases


@pytest.fixture(autouse=True)
def _clean():
    leases.reset()
    leases.set_owner_id("owner-a")
    yield
    leases.set_owner_id(None)
    leases.reset()


def _expire(role: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    leases._leases[role]["until"] = past


def test_acquire_renew_and_refuse_another_owner():
    assert leases.acquire("scheduler", "owner-a", 60) is True
    assert leases.acquire("scheduler", "owner-a", 60) is True
    assert leases.acquire("scheduler", "owner-b", 60) is False
    assert leases.holder("scheduler")["owner"] == "owner-a"


def test_an_expired_lease_is_taken_over_with_a_new_version():
    assert leases.hold("scheduler")
    first = leases.fencing_token("scheduler")
    _expire("scheduler")
    assert leases.holder("scheduler") is None
    assert leases.acquire("scheduler", "owner-b", 60) is True
    assert leases.holder("scheduler")["version"] == first + 1
    assert leases.fencing_token("scheduler") is None  # owner-a no longer holds it


def test_fenced_write_refuses_after_the_lease_moved_on():
    assert leases.hold("outbox")
    token = leases.fencing_token("outbox")
    with leases.fenced("outbox", token):
        pass
    _expire("outbox")
    leases.acquire("outbox", "owner-b", 60)
    with pytest.raises(leases.LeaseLost):
        with leases.fenced("outbox", token):
            raise AssertionError("the body must not run")


def test_release_and_release_all():
    leases.hold("a")
    leases.hold("b")
    assert leases.release("a") is True
    assert leases.release("a") is False
    assert leases.release_all() == 1
    assert leases.all_leases() == []


def test_all_leases_marks_mine_and_age():
    leases.hold("watchers")
    rows = leases.all_leases()
    assert rows[0]["role"] == "watchers" and rows[0]["mine"] is True
    assert rows[0]["expired"] is False and rows[0]["age_seconds"] >= 0
    assert leases.held_by_me("watchers")
