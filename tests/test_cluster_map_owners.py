"""
The cluster map ties each agent container to its owner and to the worker that
started it, and marks which hosts are active (the page hides the rest).
"""
from __future__ import annotations

from dashboard.backend.routes.deployment import _host_row, _owned_containers

MEMBERS = [
    {"member_id": "w1:1", "role": "worker", "status": "live", "host": "w1"},
    {"member_id": "api:1", "role": "api", "status": "live", "host": "api"},
    {"member_id": "old:1", "role": "worker", "status": "stopped", "host": "old"},
]


def test_containers_carry_their_owner_and_worker():
    containers = [{"name": "ah-inst-a", "host": "api", "state": "running"},
                  {"name": "stray", "host": "api", "state": "exited"}]
    instances = [{"instance_id": "i-a", "container_name": "ah-inst-a", "workspace": "acme",
                  "agent_id": "helper", "carrier_host": "w1", "carrier_status": "active"}]
    runs = [{"run_id": "r-1", "container_name": "ah-run-1", "workspace": "acme",
             "agent_id": "swe_agent", "host": "w1", "status": "running"}]
    out = {c["name"]: c for c in _owned_containers(containers, instances, runs, [], MEMBERS)}
    inst = out["ah-inst-a"]
    assert (inst["owner_kind"], inst["owner_id"], inst["workspace"]) == ("instance", "i-a", "acme")
    assert inst["worker"] == "w1:1" and inst["launched_on"] == "w1"
    assert inst["state"] == "running"  # the daemon's state wins over the record's
    # A run's container the daemon here does not list still shows, from its record.
    run = out["ah-run-1"]
    assert run["remote"] is True and run["owner_kind"] == "run" and run["worker"] == "w1:1"
    assert out["stray"]["worker"] == "" and "owner_kind" not in out["stray"]


def test_hosts_without_live_members_or_work_are_inactive():
    containers = [{"name": "ah-inst-a", "launched_on": "w1", "host": "api"}]
    rows = {h: _host_row(h, MEMBERS, [], [], [], containers) for h in ("w1", "api", "old")}
    assert rows["w1"]["active"] and rows["w1"]["containers"] == 1 and rows["w1"]["status"] == "live"
    assert rows["api"]["active"] and rows["api"]["containers"] == 0
    assert not rows["old"]["active"] and rows["old"]["status"] == "stopped"
    # A stopped host still running something stays on the page.
    busy = _host_row("old", MEMBERS, [{"host": "old"}], [], [], [])
    assert busy["active"] and busy["runs"] == 1
