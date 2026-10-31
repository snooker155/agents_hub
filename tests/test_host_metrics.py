"""
Machine and container load on the Cluster page: what a member samples
(common/host_metrics.py), how its beat stores it and which member samples a
shared Docker daemon (common/members.py), and how the map turns the samples
into machines and container columns (dashboard/backend/routes/cluster.py).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from common import host_metrics, leases, members
from dashboard.backend.routes.cluster import _machines, _owned_containers


def _iso(seconds_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


# ── parsing docker's numbers ─────────────────────────────────────────────────

def test_sizes_and_stats_rows_parse_into_numbers():
    assert host_metrics.parse_size("12.5MiB") == int(12.5 * 1024 ** 2)
    assert host_metrics.parse_size("3kB") == 3000
    assert host_metrics.parse_size("0B") == 0
    assert host_metrics.parse_size("--") is None
    assert host_metrics.parse_pair("1.2MB / 3kB") == (1_200_000, 3000)
    row = host_metrics.parse_stats_line({
        "Name": "agents-hub-node-inst_a", "CPUPerc": "12.34%", "MemUsage": "100MiB / 2GiB",
        "MemPerc": "4.88%", "NetIO": "1kB / 2kB", "BlockIO": "0B / 4.1kB", "PIDs": "7"})
    assert row["name"] == "agents-hub-node-inst_a" and row["cpu_percent"] == 12.34
    assert row["mem_used"] == 100 * 1024 ** 2 and row["mem_limit"] == 2 * 1024 ** 3
    assert (row["net_rx"], row["net_tx"], row["block_write"], row["pids"]) == (1000, 2000, 4100, 7)


def test_cpu_is_the_busy_share_between_two_samples(monkeypatch, tmp_path):
    readings = iter([(100, 1000), (150, 1100)])  # 50 idle of 100 jiffies
    monkeypatch.setattr(host_metrics, "_proc_cpu_times", lambda: next(readings))
    probe = host_metrics.MachineProbe(tmp_path)
    first = probe.sample()
    second = probe.sample()
    assert first["cpu_percent"] is None and second["cpu_percent"] == 50.0
    assert second["disk_total"] > 0 and 0 <= second["disk_percent"] <= 100
    assert [p[1] for p in second["history"]] == [None, 50.0]


def test_sampler_leaves_a_daemon_another_member_reports(monkeypatch, tmp_path):
    sampler = host_metrics.Sampler(interval=1, label="x", disk_path=tmp_path,
                                   skip_docker=lambda daemon_id: daemon_id == "D1")
    monkeypatch.setattr(sampler.docker, "daemon", lambda: {"daemon_id": "D1", "name": "box"})
    monkeypatch.setattr(sampler.docker, "sample", lambda info: pytest.fail("sampled a shared daemon"))
    snap = sampler.sample_once()
    assert snap["docker"] == {"daemon_id": "D1", "shared": True}
    assert snap["machine"]["machine_id"]


def test_no_docker_means_no_docker_section(monkeypatch, tmp_path):
    monkeypatch.setattr(host_metrics, "_docker", lambda argv, timeout: None)
    sampler = host_metrics.Sampler(interval=1, label="x", disk_path=tmp_path)
    assert "docker" not in sampler.sample_once()
    # The failure is remembered: no second ``docker info`` right away.
    monkeypatch.setattr(host_metrics, "_docker", lambda argv, timeout: pytest.fail("asked again"))
    assert sampler.docker.daemon() is None


# ── members ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _owner(tmp_path, monkeypatch):
    monkeypatch.setattr(members, "SERVICE_LOGS_DIR", tmp_path / "service_logs")
    members._log_state.clear()
    yield
    members.detach_log_file()
    members._log_state.clear()
    leases.set_owner_id(None)


def _docker(daemon_id: str, seconds_ago: float = 0.0) -> dict:
    return {"docker": {"daemon_id": daemon_id, "sampled_at": _iso(seconds_ago), "containers_sampled": []}}


def test_the_beat_stores_the_metrics_and_the_first_member_keeps_a_daemon(_owner):
    leases.set_owner_id("m-first")
    members.register("worker", member_id="m-first", metrics=_docker("D1"))
    members.register("worker", member_id="m-second", metrics={})
    assert members.get("m-first")["metrics"]["docker"]["daemon_id"] == "D1"
    # The second sees the first's fresh sample and leaves the daemon to it.
    assert members.docker_reported_elsewhere("D1", member_id="m-second")
    assert not members.docker_reported_elsewhere("D2", member_id="m-second")
    # The first, started earlier, keeps sampling even when the second did too.
    members.beat({}, member_id="m-second", metrics=_docker("D1"))
    assert members.get("m-second")["metrics"]["docker"]["daemon_id"] == "D1"
    assert not members.docker_reported_elsewhere("D1", member_id="m-first")
    # An old sample does not hold the daemon.
    members.beat({}, member_id="m-first", metrics=_docker("D3", seconds_ago=600))
    assert not members.docker_reported_elsewhere("D3", member_id="m-second")
    # A beat without metrics keeps the last ones.
    members.beat({"tracked": 1}, member_id="m-first")
    assert members.get("m-first")["metrics"]["docker"]["daemon_id"] == "D3"


# ── the map ──────────────────────────────────────────────────────────────────

def _member(member_id, role, machine, docker=None, status="live"):
    metrics = {"machine": machine}
    if docker is not None:
        metrics["docker"] = docker
    return {"member_id": member_id, "role": role, "status": status, "host": machine["host"],
            "metrics": metrics}


LINUX_BOX = {"machine_id": "boot-1", "host": "api-ctr", "kernel": "6.10-linuxkit", "cpus": 8, "in_container": True,
             "cpu_percent": 40.0, "mem_total": 8 << 30, "mem_used": 4 << 30, "sampled_at": _iso(5)}
MAC = {"machine_id": "mac-uuid", "host": "laptop", "kernel": "27.0.0", "cpus": 10,
       "cpu_percent": 20.0, "sampled_at": _iso(2)}
DAEMON = {"daemon_id": "D1", "name": "docker-desktop", "kernel": "6.10-linuxkit", "cpus": 8,
          "mem_total": 8 << 30, "sampled_at": _iso(3), "containers_cpu_percent": 120.0,
          "containers_mem_used": 1 << 30,
          "containers_sampled": [{"name": "agents-hub-node-inst_a", "cpu_percent": 120.0,
                                  "mem_used": 1 << 30, "mem_limit": 2 << 30}]}


def test_members_of_one_machine_make_one_row_with_its_daemon():
    rows = _machines([
        _member("api:1", "api", LINUX_BOX, DAEMON),
        # A worker container on the same kernel: the same machine, a fresher sample.
        _member("w:1", "worker", {**LINUX_BOX, "host": "worker-ctr", "cpu_percent": 55.0,
                                  "sampled_at": _iso(1)}, {"daemon_id": "D1", "shared": True}),
        _member("old:1", "worker", MAC, status="stale"),
    ])
    assert len(rows) == 1
    row = rows[0]
    assert sorted(row["hosts"]) == ["api-ctr", "worker-ctr"]
    # Seen from containers, the machine goes by its daemon's name.
    assert row["name"] == "docker-desktop"
    assert [m["role"] for m in row["members"]] == ["api", "worker"]
    assert row["metrics"]["cpu_percent"] == 55.0
    assert row["docker"][0]["name"] == "docker-desktop" and row["docker"][0]["reported_by"] == "api:1"
    assert "containers_sampled" not in row["docker"][0]


def test_a_daemon_on_no_members_machine_gets_its_own_row():
    rows = _machines([_member("api:1", "all", MAC, DAEMON)])
    by_id = {r["id"]: r for r in rows}
    assert by_id["mac-uuid"]["docker"] == []
    vm = by_id["docker:D1"]
    assert vm["name"] == "docker-desktop" and vm["metrics"] is None and vm["members"] == []
    # The member's machine sorts first.
    assert rows[0]["id"] == "mac-uuid"


def test_containers_carry_their_sampled_usage():
    containers = [{"name": "agents-hub-node-inst_a", "host": "api-ctr", "state": "running"},
                  {"name": "agents-hub-node-inst_b", "host": "api-ctr", "state": "running"}]
    out = {c["name"]: c for c in _owned_containers(
        containers, [], [], [], [_member("api:1", "api", LINUX_BOX, DAEMON)])}
    stats = out["agents-hub-node-inst_a"]["stats"]
    assert stats["cpu_percent"] == 120.0 and stats["daemon"] == "docker-desktop"
    assert "stats" not in out["agents-hub-node-inst_b"]


def test_container_startup_output_skips_the_processes_own_lines(monkeypatch):
    from managers import container_manager

    script = ("printf '[2026-10-31T01:02:03Z] own line\\nTraceback (most recent call last):\\n"
              "ModuleNotFoundError: x\\n'")
    real = container_manager.subprocess.Popen
    monkeypatch.setattr(container_manager.subprocess, "Popen",
                        lambda argv, **kw: real(["sh", "-c", script], **kw))
    lines = []
    outcome = container_manager.follow_startup_logs("c", lines.append, lambda: False, timeout=10)
    assert outcome == "exited"
    assert lines == ["Traceback (most recent call last):", "ModuleNotFoundError: x"]


if sys.platform.startswith("linux"):
    def test_machine_id_is_the_kernels_boot_id():
        assert host_metrics.machine_id() == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
