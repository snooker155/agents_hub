"""
Workers started and stopped from the hub (common/worker_fleet.py): a new
worker container copies one the hub already has, a hub on the host starts a
process, and a stop asks the worker to drain, never one that carries live
instances. The worker side (runtime/worker.py) stops claiming and exits once
nothing of its own runs.
"""
from __future__ import annotations

import threading
import time

import pytest

from common import members, worker_fleet
from instances import store

TEMPLATE = {
    "Image": "sha256:abc",
    "Config": {
        "Image": "agents_hub-worker",
        "Cmd": ["python", "-m", "runtime.worker"],
        "Env": ["HOSTNAME=7468d86dffdb", "AGENTS_HUB_ROLE=worker", "AGENTS_HUB_INSTANCE_ID=pinned",
                "AGENTS_HUB_DATABASE_URL=postgresql://hub@postgres/hub", "PYTHONPATH=/app"],
        "Labels": {"com.docker.compose.project": "agents_hub", "com.docker.compose.service": "worker"},
        "WorkingDir": "/app",
    },
    "HostConfig": {"GroupAdd": ["0"], "ExtraHosts": ["host.docker.internal:host-gateway"],
                   "Memory": 0, "NanoCpus": 2_000_000_000},
    "Mounts": [
        {"Type": "bind", "Source": "/Users/me/agents_hub", "Destination": "/app", "RW": True},
        {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": True},
        {"Type": "volume", "Name": "agents_hub_models_data", "Destination": "/models-data", "RW": False},
    ],
    "NetworkSettings": {"Networks": {"agents_hub_default": {}, "agents-hub": {}}},
}


def test_a_new_worker_container_copies_the_pattern():
    plan = worker_fleet.docker_run_args(TEMPLATE, "agents_hub-worker-hub-1a2b3c")
    args = plan["args"]
    assert args[:2] == ["run", "-d"] and "--rm" in args
    assert args[args.index("--restart") + 1] == "no"
    assert "agents-hub.fleet=worker" in args and "com.docker.compose.project=agents_hub" in args
    # Not the compose service label: compose would scale it back to its own count.
    assert not any(a.startswith("com.docker.compose.service") for a in args)
    assert "type=bind,source=/Users/me/agents_hub,target=/app" in args
    assert "type=volume,source=agents_hub_models_data,target=/models-data,readonly" in args
    assert args[args.index("--group-add") + 1] == "0"
    assert args[args.index("--add-host") + 1] == "host.docker.internal:host-gateway"
    assert args[args.index("--cpus") + 1] == "2" and "--memory" not in args
    # The agents' network it joins itself; the compose one it starts on.
    assert args[args.index("--network") + 1] == "agents_hub_default" and plan["networks"] == []
    assert args[-4:] == ["agents_hub-worker", "python", "-m", "runtime.worker"]
    env = plan["env"]
    assert env["AGENTS_HUB_ROLE"] == "worker" and env["AGENTS_HUB_WORKER_ORIGIN"] == "hub"
    assert "HOSTNAME" not in env and "AGENTS_HUB_INSTANCE_ID" not in env
    assert env["AGENTS_HUB_DATABASE_URL"].startswith("postgresql://")
    # Secrets travel in the env file, never on the command line.
    assert not any("postgresql://" in a for a in args)


def test_a_hub_on_the_host_starts_a_process(monkeypatch):
    seen = {}

    class FakePopen:
        pid = 4242

        def __init__(self, cmd, **kw):
            seen["cmd"], seen["env"] = cmd, kw["env"]

        def wait(self):
            return 0

    monkeypatch.setattr(worker_fleet, "launcher", lambda: "local")
    monkeypatch.setattr(worker_fleet.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("AGENTS_HUB_INSTANCE_ID", "the-hub")
    assert worker_fleet.add(2) == ["pid 4242", "pid 4242"]
    assert seen["cmd"][1:] == ["-m", "runtime.worker"]
    env = seen["env"]
    assert env["AGENTS_HUB_ROLE"] == "worker" and env["AGENTS_HUB_WORKER_ORIGIN"] == "hub"
    assert "AGENTS_HUB_INSTANCE_ID" not in env and int(env["AGENTS_HUB_WORKER_TOOL_PORT"]) > 0


def test_no_way_to_start_one(monkeypatch):
    monkeypatch.setattr(worker_fleet, "launcher", lambda: "")
    with pytest.raises(worker_fleet.FleetError):
        worker_fleet.add()


@pytest.fixture
def no_docker(monkeypatch):
    monkeypatch.setattr(worker_fleet, "_docker_reachable", lambda: False)


def test_stop_drains_a_free_worker_and_can_be_taken_back(no_docker):
    members.register("worker", member_id="w-free", capabilities={"concurrency": 4})
    out = worker_fleet.stop("w-free")
    assert out["draining"] is True
    assert members.drain_requested("w-free") and members.get("w-free")["draining"]
    assert worker_fleet.cancel_stop("w-free")
    assert not members.drain_requested("w-free")


def test_a_worker_with_live_instances_is_left_alone(no_docker):
    rec = members.register("worker", member_id="w-busy")
    store.create("helper", kind="runner", carrier_host=rec["host"], carrier_status="running")
    with pytest.raises(worker_fleet.WorkerBusy) as err:
        worker_fleet.stop("w-busy")
    assert err.value.carriers == 1
    assert not members.drain_requested("w-busy")
    listed = {w["member_id"]: w for w in worker_fleet.workers(containers=[])}
    assert listed["w-busy"]["stoppable"] is False and listed["w-busy"]["carriers"] == 1


def test_stop_needs_a_live_worker(no_docker):
    with pytest.raises(LookupError):
        worker_fleet.stop("nobody")
    members.register("api", member_id="api-1")
    with pytest.raises(LookupError):
        worker_fleet.stop("api-1")


def test_the_beat_passes_a_drain_request_on_and_back():
    seen = []
    beat = members.MemberBeat("worker", interval=0.05, on_drain=seen.append)
    beat.start()
    try:
        members.request_drain(members.owner_id())
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            time.sleep(0.05)
        members.request_drain(members.owner_id(), on=False)
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        beat._halt.set()
        beat.join(timeout=2)
    assert seen == [True, False]


def test_a_draining_worker_claims_nothing_and_goes_once_free(monkeypatch):
    from common import run_queue
    from runtime import worker as worker_mod
    claims = []
    monkeypatch.setattr(run_queue, "claim", lambda *a, **k: claims.append(1))
    w = worker_mod.Worker(concurrency=2)
    w.request_drain()
    assert w.tick() == 0 and claims == []
    w.tracked["run-1"] = {}
    assert w._drained() is False
    w.tracked.clear()
    assert w._drained() is True
    w.request_drain(False)
    assert not w.draining.is_set()
    # It runs its loop to the end on its own once drained.
    w.request_drain()
    monkeypatch.setattr(worker_mod, "TICK_SECONDS", 0.01)
    monkeypatch.setattr(worker_mod, "DRAIN_SECONDS", 0)
    done = threading.Event()
    threading.Thread(target=lambda: (w.run_forever(install_signals=False), done.set()), daemon=True).start()
    assert done.wait(10)
