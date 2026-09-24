"""Service leases (common/leases.py): the roles only one process may play.

Two owners are played from one process through ``set_owner_id``; the
database is what arbitrates, exactly as it does between two replicas.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import db, leases


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    # Each test gets a fresh database; the per-process memory of held
    # versions must start empty with it.
    monkeypatch.setattr(leases, "_held", {})
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


# ── fencing tokens ───────────────────────────────────────────────────────────

def _version(role: str) -> int:
    return int(db.get_conn().execute(
        "SELECT version FROM service_leases WHERE role = ?", (role,)).fetchone()["version"])


def test_version_bumps_on_takeover_and_not_on_renewal():
    assert leases.acquire("scheduler", "replica-a", 60) is True
    assert _version("scheduler") == 1
    assert leases.acquire("scheduler", "replica-a", 60) is True
    assert _version("scheduler") == 1
    _expire("scheduler")
    assert leases.acquire("scheduler", "replica-b", 60) is True
    assert _version("scheduler") == 2
    assert leases.holder("scheduler")["version"] == 2
    assert {r["role"]: r["version"] for r in leases.all_leases()} == {"scheduler": 2}


def test_fencing_token_after_hold_and_none_after_release():
    assert leases.fencing_token("watchdog") is None
    assert leases.hold("watchdog", 60) is True
    token = leases.fencing_token("watchdog")
    assert token == _version("watchdog")
    assert leases.verify("watchdog", token) is True
    leases.release("watchdog")
    assert leases.fencing_token("watchdog") is None
    assert leases.verify("watchdog", token) is False


def test_a_stale_token_fails_verify_after_another_owner_took_over():
    assert leases.hold("outbox", 60) is True
    stale = leases.fencing_token("outbox")
    _expire("outbox")
    # Expired but not yet taken: still not ours to write under.
    assert leases.verify("outbox", stale) is False
    leases.set_owner_id("replica-b")
    assert leases.hold("outbox", 60) is True
    fresh = leases.fencing_token("outbox")
    assert fresh == stale + 1
    assert leases.verify("outbox", fresh) is True
    leases.set_owner_id("replica-a")
    assert leases.fencing_token("outbox") is None
    assert leases.verify("outbox", stale) is False
    assert leases.verify("outbox", fresh) is False  # right version, wrong owner


def test_fenced_raises_lease_lost_and_writes_nothing():
    assert leases.hold("outbox", 60) is True
    stale = leases.fencing_token("outbox")
    _expire("outbox")
    leases.acquire("outbox", "replica-b", 60)
    with pytest.raises(leases.LeaseLost):
        with leases.fenced("outbox", stale) as conn:
            conn.execute("UPDATE service_leases SET owner = 'stale-write' WHERE role = 'outbox'")
    assert leases.holder("outbox")["owner"] == "replica-b"


def test_fenced_writes_under_a_live_token():
    assert leases.hold("outbox", 60) is True
    with leases.fenced("outbox", leases.fencing_token("outbox")) as conn:
        conn.execute("UPDATE service_leases SET renewed_at = 'marked' WHERE role = 'outbox'")
    assert leases.holder("outbox")["renewed_at"] == "marked"


def test_outbox_drain_stops_when_the_lease_is_lost(monkeypatch):
    from notify import outbound

    for i in range(3):
        outbound.enqueue({"id": f"e{i}", "kind": "webhook", "url": "https://example.com"},
                         {"id": f"evt-{i}", "type": "notification"})
    sent = []

    def _deliver(endpoint, event):
        sent.append(event["id"])
        if len(sent) == 1:
            # The drainer stalls past its TTL during the first delivery and
            # another replica takes the role over.
            _expire(outbound.LEASE_ROLE)
            leases.acquire(outbound.LEASE_ROLE, "replica-b", 60)
        return True

    monkeypatch.setattr(outbound, "deliver", _deliver)
    assert outbound.drain() == 0
    assert sent == ["evt-0"]
    rows = db.get_conn().execute("SELECT attempts, delivered_at FROM outbox").fetchall()
    assert all(r["attempts"] == 0 and r["delivered_at"] is None for r in rows)


def test_watchdog_sweep_with_a_stale_fence_closes_nothing(monkeypatch):
    from managers import run_manager as rm
    from managers import run_watchdog as wd

    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    runs = [{"run_id": f"r{i}", "status": "running", "heartbeat_at": old} for i in range(2)]
    written = []
    monkeypatch.setattr(rm, "load_runs", lambda *a, **k: runs)
    monkeypatch.setattr(rm, "update_run", lambda run_id, upd: written.append(run_id))
    for name in ("_sweep_queue", "_sweep_entity_runs", "_sweep_instances"):
        monkeypatch.setattr(wd, name, lambda: 0)
    monkeypatch.setattr(wd, "_sweep_containers", lambda: None)

    assert leases.hold(wd.LEASE_ROLE, 60) is True
    token = leases.fencing_token(wd.LEASE_ROLE)
    assert wd.sweep_once((wd.LEASE_ROLE, token)) == 2
    assert written == ["r0", "r1"]

    written.clear()
    _expire(wd.LEASE_ROLE)
    leases.acquire(wd.LEASE_ROLE, "replica-b", 60)
    assert wd.sweep_once((wd.LEASE_ROLE, token)) == 0
    assert written == []


def test_run_due_jobs_stops_firing_when_the_scheduler_lease_is_lost(monkeypatch):
    from types import SimpleNamespace

    from plans import service as ps

    jobs = [SimpleNamespace(id=f"j{i}") for i in range(3)]
    fired = []

    def _fire(job, trigger="schedule"):
        fired.append(job.id)
        _expire("scheduler")
        leases.acquire("scheduler", "replica-b", 60)
        return {"ok": True, "job_id": job.id}

    monkeypatch.setattr(ps, "claim_due_jobs", lambda owner=None: jobs)
    monkeypatch.setattr(ps, "fire_job", _fire)
    assert leases.hold("scheduler", 60) is True
    results = ps.run_due_jobs("replica-a", fence_token=leases.fencing_token("scheduler"))
    assert fired == ["j0"] and len(results) == 1
