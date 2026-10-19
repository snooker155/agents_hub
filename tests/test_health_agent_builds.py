"""
Agent build reuse in the health snapshot (common/health.py ``_agent_cache``):
the backend process's own counts (agents/agent_cache.py).
"""
from agents import agent_cache
from common import health
from instances import carrier, store


def test_snapshot_reports_this_process_build_counts():
    builds = health.snapshot()["agent_cache"]
    stats = agent_cache.cache_stats()
    for key in ("entries", "hits", "misses"):
        assert builds[key] == stats.get(key, 0)
    assert "replicas" not in builds


def test_a_heartbeat_without_counts_still_beats():
    store.create("a1", instance_id="r5", kind="resident", state="active")
    carrier.heartbeat("r5")
    assert store.get("r5")["heartbeat_at"]
