"""The launch queue (common/run_queue.py): claiming, leasing, handing back."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common import db, run_queue


def _lapse(run_id: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE run_queue SET lease_until = ? WHERE run_id = ?", (past, run_id))


def test_enqueue_and_claim_in_priority_then_age_order():
    run_queue.enqueue("r1", "task", {"n": 1})
    run_queue.enqueue("r2", "task", {"n": 2}, priority=5)
    run_queue.enqueue("r3", "task", {"n": 3})
    got = [run_queue.claim("w1")["run_id"] for _ in range(3)]
    assert got == ["r2", "r1", "r3"]
    assert run_queue.claim("w1") is None


def test_a_claimed_row_is_leased_to_its_worker_and_not_claimable_by_another():
    run_queue.enqueue("r1", "task", {})
    job = run_queue.claim("w1", ttl_seconds=60)
    assert job["status"] == run_queue.STATUS_LEASED and job["attempts"] == 1
    assert run_queue.claim("w2") is None
    _lapse("r1")
    again = run_queue.claim("w2")
    assert again["run_id"] == "r1" and again["attempts"] == 2


def test_claim_honours_execution_modes():
    run_queue.enqueue("docker-run", "task", {}, execution_mode="docker")
    run_queue.enqueue("local-run", "task", {}, execution_mode="local")
    assert run_queue.claim("w1", execution_modes=["local"])["run_id"] == "local-run"
    assert run_queue.claim("w1", execution_modes=["local"]) is None
    assert run_queue.claim("w1", execution_modes=["local", "docker"])["run_id"] == "docker-run"


def test_renew_only_extends_the_owners_rows():
    run_queue.enqueue("r1", "task", {})
    run_queue.claim("w1", ttl_seconds=60)
    before = run_queue.get("r1")["lease_until"]
    assert run_queue.renew(["r1"], "w2") == 0
    assert run_queue.renew(["r1"], "w1", ttl_seconds=600) == 1
    assert run_queue.get("r1")["lease_until"] > before


def test_finish_and_requeue():
    run_queue.enqueue("r1", "task", {})
    run_queue.claim("w1")
    assert run_queue.requeue("r1", "boom") is True
    row = run_queue.get("r1")
    assert row["status"] == run_queue.STATUS_QUEUED and row["last_error"] == "boom"
    assert row["lease_owner"] is None
    run_queue.claim("w1")
    run_queue.finish("r1")
    assert run_queue.get("r1")["status"] == run_queue.STATUS_DONE


def test_requeue_gives_up_after_max_attempts():
    run_queue.enqueue("r1", "task", {})
    for _ in range(run_queue.MAX_ATTEMPTS):
        assert run_queue.claim("w1") is not None
        ok = run_queue.requeue("r1", "boom")
    assert ok is False
    assert run_queue.get("r1")["status"] == run_queue.STATUS_FAILED
    assert run_queue.claim("w1") is None


def test_sweep_closes_detached_running_rows_and_hands_back_stuck_leases():
    run_queue.enqueue("running", "task", {})
    run_queue.claim("w1")
    run_queue.mark_running("running", "w1")
    run_queue.enqueue("stuck", "task", {})
    run_queue.claim("w1")
    run_queue.enqueue("fresh", "task", {})
    run_queue.claim("w1", ttl_seconds=600)
    _lapse("running")
    _lapse("stuck")

    done = run_queue.sweep()
    assert done == {"closed": 1, "requeued": 1, "failed": 0}
    assert run_queue.get("running")["status"] == run_queue.STATUS_DONE
    assert run_queue.get("stuck")["status"] == run_queue.STATUS_QUEUED
    assert run_queue.get("fresh")["status"] == run_queue.STATUS_LEASED


def test_sweep_fails_a_launch_nobody_completes():
    run_queue.enqueue("r1", "task", {})
    for _ in range(run_queue.MAX_ATTEMPTS):
        run_queue.claim("w1")
        _lapse("r1")
        run_queue.sweep()
    assert run_queue.get("r1")["status"] == run_queue.STATUS_FAILED


def test_stats_counts_and_oldest_age():
    assert run_queue.stats()["queued"] == 0
    run_queue.enqueue("r1", "task", {})
    run_queue.enqueue("r2", "flow", {})
    run_queue.claim("w1")
    s = run_queue.stats()
    assert s["queued"] == 1 and s["leased"] == 1
    assert s["oldest_queued_seconds"] >= 0


def test_enqueue_replaces_an_earlier_row_for_the_same_run():
    run_queue.enqueue("r1", "task", {"v": 1})
    run_queue.claim("w1")
    run_queue.enqueue("r1", "task", {"v": 2})
    row = run_queue.get("r1")
    assert row["status"] == run_queue.STATUS_QUEUED and row["payload"] == {"v": 2}
    assert row["attempts"] == 0
