"""
Agent build reuse in the health snapshot (common/health.py ``_agent_cache``):
the builds live in each replica's memory, so the snapshot sums what every
live replica sent with its heartbeat (runtime/instance_run.py).
"""
from datetime import datetime, timedelta, timezone

from common import health
from instances import carrier, store


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_live_replicas_counts_are_summed_and_stale_or_stopped_ones_left_out():
    store.create("a1", instance_id="r1", kind="resident", state="active")
    store.create("a1", instance_id="r2", kind="resident", state="standby")
    store.create("a1", instance_id="r3", kind="resident", state="active")
    store.create("a1", instance_id="r4", kind="resident", state="stopped")
    carrier.heartbeat("r1", agent_builds={"entries": 2, "hits": 10, "misses": 2})
    carrier.heartbeat("r2", agent_builds={"entries": 1, "hits": 5, "misses": 1})
    store.update("r3", heartbeat_at=_ago(600), agent_builds={"entries": 9, "hits": 90, "misses": 9})
    store.update("r4", heartbeat_at=_ago(1), agent_builds={"entries": 9, "hits": 90, "misses": 9})

    builds = health.snapshot()["agent_cache"]
    assert builds["replicas"] == {"entries": 3, "hits": 15, "misses": 3, "replicas": 2}
    here = builds["here"]
    assert builds["hits"] == here["hits"] + 15
    assert builds["misses"] == here["misses"] + 3


def test_a_heartbeat_without_counts_still_beats():
    store.create("a1", instance_id="r5", kind="resident", state="active")
    carrier.heartbeat("r5")
    assert store.get("r5")["heartbeat_at"]
    assert health.snapshot()["agent_cache"]["replicas"]["replicas"] == 0
