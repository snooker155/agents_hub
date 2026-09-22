"""Lease-based idempotency for job firing, and the PlanScheduler tick loop.

The suite has no pytest-asyncio; async bodies are driven with asyncio.run,
following the pattern in test_chat_broadcast.py.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from plans import service as ps
from plans.models import JobKind, JobStatus, Notification, Recurrence, ScheduledJob
from plans.scheduler import PlanScheduler
from plans.storage import PlanStore

UTC = timezone.utc


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A PlanStore in a throwaway file, wired in as plans.service's singleton
    so create_job/fire_job/claim_due_jobs all operate on it."""
    s = PlanStore(path=tmp_path / "plans.json")
    monkeypatch.setattr(ps, "plan_store", s)
    return s


@pytest.fixture
def no_side_effects(monkeypatch):
    """Stand in for the real notification/task/flow side effects fire_job runs,
    and record which job ids actually fired."""
    fired = []

    def _fake_create_notification(**kw):
        fired.append(kw.get("source", {}).get("job_id"))
        return Notification(title=kw.get("title", ""), body=kw.get("body", ""))

    monkeypatch.setattr(ps, "create_notification", _fake_create_notification)
    return fired


def _job(**kw):
    defaults = dict(kind=JobKind.notification, title="t", run_at=datetime.now(UTC))
    defaults.update(kw)
    return ScheduledJob(**defaults)


# -------------------- claim_due_jobs: lease + race --------------------

def test_claim_due_jobs_selects_only_due_unleased(store):
    now = datetime.now(UTC)
    due = store.add(_job(run_at=now - timedelta(seconds=5)))
    future = store.add(_job(run_at=now + timedelta(hours=1)))

    claimed = store.claim_due_jobs(now, "owner-a", lease_seconds=60)

    assert {j.id for j in claimed} == {due.id}
    assert store.get(due.id).lease_owner == "owner-a"
    assert store.get(future.id).lease_owner is None


def test_claim_due_jobs_skips_a_live_lease(store):
    now = datetime.now(UTC)
    job = store.add(_job(
        run_at=now - timedelta(seconds=5),
        lease_owner="other", lease_until=now + timedelta(seconds=60),
    ))

    claimed = store.claim_due_jobs(now, "owner-a", lease_seconds=60)

    assert claimed == []
    assert store.get(job.id).lease_owner == "other"


def test_claim_due_jobs_takes_an_expired_lease(store):
    now = datetime.now(UTC)
    job = store.add(_job(
        run_at=now - timedelta(seconds=5),
        lease_owner="stale", lease_until=now - timedelta(seconds=1),
    ))

    claimed = store.claim_due_jobs(now, "owner-a", lease_seconds=60)

    assert {j.id for j in claimed} == {job.id}
    assert store.get(job.id).lease_owner == "owner-a"


def test_two_threads_racing_claim_due_jobs_exactly_one_wins(store):
    now = datetime.now(UTC)
    store.add(_job(run_at=now - timedelta(seconds=5)))

    wins = []
    barrier = threading.Barrier(8)

    def worker(owner):
        barrier.wait()
        claimed = store.claim_due_jobs(datetime.now(UTC), owner, lease_seconds=30)
        if claimed:
            wins.append(owner)

    threads = [threading.Thread(target=worker, args=(f"owner-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(wins) == 1


# -------------------- fire_job: idempotency across a crash --------------------

def test_fire_job_self_claims_an_unclaimed_job(store, no_side_effects):
    """Calling fire_job directly, with no prior claim_due_jobs/claim_job_now
    call, still fires exactly once: it claims the job itself first, so a
    tool, a route or a test can call it without the two-step dance."""
    job = store.add(_job(run_at=datetime.now(UTC) - timedelta(seconds=5)))
    result = ps.fire_job(job)
    assert result["ok"] is True
    assert no_side_effects == [str(job.id)]
    assert store.get(job.id).status == JobStatus.fired


def test_fire_job_self_claim_is_atomic_across_a_race(store, no_side_effects):
    """Two callers each hold their own snapshot of the same unclaimed, due
    job (as if two replicas both read it just before either claimed it) and
    both call fire_job directly. The self-claim inside fire_job is the same
    atomic PlanStore operation used by the scheduler tick, so exactly one of
    them actually fires — the other gets back a "locked" result instead of
    a second, duplicate side effect."""
    now = datetime.now(UTC)
    job = store.add(_job(run_at=now - timedelta(seconds=5)))
    snapshot_a = store.get(job.id)
    snapshot_b = store.get(job.id)

    result_a = ps.fire_job(snapshot_a)
    result_b = ps.fire_job(snapshot_b)

    oks = sorted([result_a["ok"], result_b["ok"]])
    assert oks == [False, True]
    assert len(no_side_effects) == 1


def test_fire_job_completes_bookkeeping_and_advances_a_recurring_job(store, no_side_effects):
    """A normal, uninterrupted fire: the side effect runs once, and the
    bookkeeping (lease cleared, fire_count advanced, run_at moved past now)
    lands in the same call."""
    now = datetime.now(UTC)
    job = store.add(_job(run_at=now - timedelta(seconds=5), recurrence=Recurrence.hourly))
    claimed = store.claim_due_jobs(now, "owner-a", lease_seconds=60)[0]

    ps.fire_job(claimed)

    assert len(no_side_effects) == 1
    after_first = store.get(job.id)
    assert after_first.fire_count == 1
    assert after_first.lease_owner is None
    assert after_first.status == JobStatus.scheduled  # recurring: still scheduled
    assert after_first.run_at > now  # advanced to the next slot

    # The new slot is in the future, so it is not immediately due again.
    reclaimed = store.claim_due_jobs(after_first.run_at - timedelta(seconds=1), "owner-b", lease_seconds=60)
    assert reclaimed == []


def test_fire_job_retry_on_same_slot_skips_the_side_effect(store, no_side_effects):
    """Directly exercise the crash window: last_fired_slot is already stamped
    for this run_at (as fire_job stamps it before the side effect), so a
    fresh claim + fire on the same slot must not create a second notification,
    while still completing lease/fire_count/run_at bookkeeping."""
    now = datetime.now(UTC)
    job = store.add(_job(run_at=now - timedelta(seconds=5), recurrence=Recurrence.none))
    # Pretend a previous attempt got as far as stamping the slot, then crashed
    # with the lease still held (about to expire).
    store.update(
        job.id,
        last_fired_slot=now - timedelta(seconds=5),
        lease_owner="crashed-owner",
        lease_until=now - timedelta(seconds=1),
    )

    claimed = store.claim_due_jobs(datetime.now(UTC), "owner-b", lease_seconds=60)
    assert len(claimed) == 1
    result = ps.fire_job(claimed[0])

    assert result.get("skipped") == "already_fired_this_slot"
    assert no_side_effects == []  # the notification was never (re)created
    final = store.get(job.id)
    assert final.status == JobStatus.fired
    assert final.fire_count == 1
    assert final.lease_owner is None


# -------------------- PlanScheduler._loop --------------------

@pytest.fixture
def fast_scheduler():
    sch = PlanScheduler()
    sch.TICK_SECONDS = 0.01
    sch.ESCALATION_INTERVAL_SECONDS = 0.03
    return sch


async def _run_for(sch, seconds):
    await sch.start()
    await asyncio.sleep(seconds)
    await sch.stop()


def test_loop_fires_due_jobs_skips_future_and_leased(store, no_side_effects, fast_scheduler, monkeypatch):
    maintenance_calls = []
    escalation_calls = []
    monkeypatch.setattr("common.maintenance.run_maintenance", lambda: maintenance_calls.append(1))
    monkeypatch.setattr("plans.escalation.sweep_awaiting_input", lambda: escalation_calls.append(1))

    now = datetime.now(UTC)
    due = store.add(_job(run_at=now - timedelta(seconds=5)))
    future = store.add(_job(run_at=now + timedelta(hours=1)))
    leased = store.add(_job(
        run_at=now - timedelta(seconds=5),
        lease_owner="other", lease_until=now + timedelta(hours=1),
    ))
    expired_lease = store.add(_job(
        run_at=now - timedelta(seconds=5),
        lease_owner="stale", lease_until=now - timedelta(seconds=1),
    ))

    asyncio.run(_run_for(fast_scheduler, 0.1))

    fired_ids = set(no_side_effects)
    assert str(due.id) in fired_ids
    assert str(expired_lease.id) in fired_ids
    assert str(future.id) not in fired_ids
    assert str(leased.id) not in fired_ids

    assert store.get(due.id).status == JobStatus.fired
    assert store.get(future.id).status == JobStatus.scheduled
    assert store.get(leased.id).lease_owner == "other"  # untouched by the loop

    # Maintenance ticks every loop pass; escalation only every ~0.03s, both
    # driven by the same 0.1s run, so maintenance must clearly outpace it.
    assert len(maintenance_calls) >= 3
    assert 1 <= len(escalation_calls) < len(maintenance_calls)


def test_stop_ends_the_loop_promptly_even_with_a_long_tick(store, monkeypatch):
    monkeypatch.setattr("common.maintenance.run_maintenance", lambda: None)
    monkeypatch.setattr("plans.escalation.sweep_awaiting_input", lambda: None)

    sch = PlanScheduler()
    sch.TICK_SECONDS = 5.0  # long enough that a naive stop() would visibly hang
    sch.ESCALATION_INTERVAL_SECONDS = 5.0

    async def _body():
        await sch.start()
        await asyncio.sleep(0.05)  # let it enter the tick-wait
        started = time.monotonic()
        await sch.stop()
        return time.monotonic() - started

    elapsed = asyncio.run(_body())
    assert elapsed < 1.0
    assert not sch.is_running()
