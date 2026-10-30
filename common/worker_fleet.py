"""
Workers started and stopped from the hub: the Cluster page's Add and Stop,
and the autoscaler (common/worker_autoscale.py).

Two ways to start one, whichever this hub can use (:func:`launcher`):

- ``docker``: a hub in a container with the Docker socket (the compose
  stack) starts a container like a worker it already has: the same image,
  environment, mounts, groups, extra hosts and network, with the worker's
  command. With no worker container to copy, its own container serves as the
  pattern. The new container is labelled ``agents-hub.fleet=worker`` and
  belongs to the compose project (``docker compose down --remove-orphans``
  takes it away), restarts never and is removed once it exits.
- ``local``: a hub on the host starts ``python -m runtime.worker`` as a
  process of its own, on a free port for its state host.

Stopping is the same for every worker, whoever started it: the hub asks it to
drain (``members.drain_requested_at``); the worker stops claiming and exits
once nothing of its own runs any more (runtime/worker.py). A worker with live
instance carriers is never asked: their containers reach their state through
it. A container the hub can see has its restart policy turned off first, so
compose's ``restart: unless-stopped`` does not bring it straight back.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Set on a worker the hub started; the worker reports it as its origin.
ORIGIN_ENV = "AGENTS_HUB_WORKER_ORIGIN"
ORIGIN_HUB = "hub"
#: The label of a worker container the hub started.
FLEET_LABEL = "agents-hub.fleet"
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"

#: Never copied into a new worker: its own identity and role come fresh.
_DROP_ENV = frozenset({"HOSTNAME", "AGENTS_HUB_ROLE", ORIGIN_ENV, "AGENTS_HUB_INSTANCE_ID",
                       "AGENTS_HUB_WORKER_TOOL_PORT"})
#: Carrier states that keep a worker in place (their containers use it).
_LIVE_CARRIER = ("queued", "starting", "running", "stopping")

WORKER_COMMAND = ["python", "-m", "runtime.worker"]


class FleetError(RuntimeError):
    """A worker could not be started or stopped; the message says why."""


class WorkerBusy(FleetError):
    """The worker carries live instances and is left alone."""

    def __init__(self, member_id: str, carriers: int) -> None:
        super().__init__(f"worker {member_id} carries {carriers} live instance(s)")
        self.member_id = member_id
        self.carriers = carriers


def _docker(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _in_container() -> bool:
    from managers import container_manager as cm
    return bool(cm._host_project_root())


def _docker_reachable() -> bool:
    try:
        return _docker(["version", "--format", "{{.Server.Version}}"], timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def launcher() -> str:
    """``docker`` for a hub in a container that can reach the Docker daemon,
    ``local`` for a hub on the host, ``""`` when it can start none."""
    if _in_container():
        return "docker" if _docker_reachable() else ""
    return "local"


# ── what runs where ──────────────────────────────────────────────────────────

def _worker_containers() -> List[Dict[str, Any]]:
    """The worker containers this daemon has: compose's and the hub's."""
    if not _docker_reachable():
        return []
    fmt = ('{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Label "' + COMPOSE_SERVICE + '"}}\t'
           '{{.Label "' + FLEET_LABEL + '"}}\t{{.Label "' + COMPOSE_PROJECT + '"}}')
    out: List[Dict[str, Any]] = []
    for flt in (f"label={COMPOSE_SERVICE}=worker", f"label={FLEET_LABEL}=worker"):
        try:
            res = _docker(["ps", "-a", "--no-trunc", "--filter", flt, "--format", fmt], timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (res.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 6 or any(c["id"] == parts[0] for c in out):
                continue
            out.append({"id": parts[0], "name": parts[1], "state": parts[2],
                        "origin": ORIGIN_HUB if parts[4] == "worker" else "compose",
                        "project": parts[5]})
    return out


def live_carriers(host: str) -> int:
    """How many live instance carriers ``host`` started (they live in the
    instance's ``extra``, so the live instances are read and counted here)."""
    from instances import store
    rows = store.list_instances(live=True, limit=5000, include_archived=True)["items"]
    return sum(1 for r in rows
               if r.get("carrier_host") == host and r.get("carrier_status") in _LIVE_CARRIER)


def workers(*, containers: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Every live worker with who started it, what it carries and whether
    the hub may stop it."""
    from common import members
    if containers is None:
        containers = _worker_containers()
    out = []
    for m in members.list_members(include_stopped=False):
        if m.get("role") != "worker" or m.get("status") != "live":
            continue
        host = str(m.get("host") or "")
        box = next((c for c in containers if host and c["id"].startswith(host)), None)
        carriers = live_carriers(host) if host else 0
        origin = (m.get("capabilities") or {}).get("origin") or (box or {}).get("origin") or "external"
        out.append({
            "member_id": m["member_id"], "host": host, "origin": origin,
            "container": (box or {}).get("name") or "",
            "runs": int((m.get("load") or {}).get("tracked") or 0),
            "concurrency": int((m.get("capabilities") or {}).get("concurrency") or 0),
            "carriers": carriers, "draining": bool(m.get("draining")),
            "stoppable": carriers == 0,
        })
    return out


# ── starting ─────────────────────────────────────────────────────────────────

def _inspect(ref: str) -> Dict[str, Any]:
    res = _docker(["inspect", ref], timeout=15)
    if res.returncode != 0:
        raise FleetError(f"docker inspect {ref}: {(res.stderr or '').strip()[:300]}")
    data = json.loads(res.stdout or "[]")
    if not data:
        raise FleetError(f"docker inspect {ref}: nothing")
    return data[0]


def _template() -> Dict[str, Any]:
    """The container a new worker copies: a running worker, else any worker
    container, else this hub's own (with the worker's command)."""
    boxes = _worker_containers()
    pick = next((c for c in boxes if c["state"] == "running"), None) or (boxes[0] if boxes else None)
    if pick is not None:
        return _inspect(pick["id"])
    own = _inspect(socket.gethostname())
    own.setdefault("Config", {})["Cmd"] = list(WORKER_COMMAND)
    return own


def docker_run_args(template: Dict[str, Any], name: str) -> Dict[str, Any]:
    """``docker run`` arguments for a worker like ``template`` (an inspect
    record), plus its environment (passed through a file, never the command
    line) and the further networks to connect it to."""
    cfg = template.get("Config") or {}
    host = template.get("HostConfig") or {}
    labels = cfg.get("Labels") or {}
    env: Dict[str, str] = {}
    for item in cfg.get("Env") or []:
        key, _, value = str(item).partition("=")
        if key and key not in _DROP_ENV:
            env[key] = value
    env["AGENTS_HUB_ROLE"] = "worker"
    env[ORIGIN_ENV] = ORIGIN_HUB

    args = ["run", "-d", "--rm", "--restart", "no", "--name", name,
            "--label", f"{FLEET_LABEL}=worker"]
    if labels.get(COMPOSE_PROJECT):
        args += ["--label", f"{COMPOSE_PROJECT}={labels[COMPOSE_PROJECT]}"]
    for m in template.get("Mounts") or []:
        target = m.get("Destination")
        if not target:
            continue
        ro = "" if m.get("RW", True) else ",readonly"
        if m.get("Type") == "volume" and m.get("Name"):
            args += ["--mount", f"type=volume,source={m['Name']},target={target}{ro}"]
        elif m.get("Type") == "bind" and m.get("Source"):
            args += ["--mount", f"type=bind,source={m['Source']},target={target}{ro}"]
    for group in host.get("GroupAdd") or []:
        args += ["--group-add", str(group)]
    for extra in host.get("ExtraHosts") or []:
        args += ["--add-host", str(extra)]
    if cfg.get("User"):
        args += ["--user", str(cfg["User"])]
    if cfg.get("WorkingDir"):
        args += ["-w", str(cfg["WorkingDir"])]
    if host.get("Memory"):
        args += ["--memory", str(host["Memory"])]
    if host.get("NanoCpus"):
        args += ["--cpus", f"{int(host['NanoCpus']) / 1e9:g}"]
    # The worker joins the agents' own network itself at start
    # (managers/container_manager.py), under its own alias.
    from managers.container_manager import NETWORK_NAME
    networks = [n for n in ((template.get("NetworkSettings") or {}).get("Networks") or {})
                if n != NETWORK_NAME]
    if networks:
        args += ["--network", networks[0]]
    image = cfg.get("Image") or template.get("Image")
    if not image:
        raise FleetError("the pattern container names no image")
    command = cfg.get("Cmd") or list(WORKER_COMMAND)
    return {"args": [*args, image, *command], "env": env, "networks": networks[1:]}


def _start_container() -> str:
    template = _template()
    project = ((template.get("Config") or {}).get("Labels") or {}).get(COMPOSE_PROJECT) or "agents-hub"
    name = f"{project}-worker-hub-{secrets.token_hex(3)}"
    plan = docker_run_args(template, name)
    fd, env_file = tempfile.mkstemp(prefix="worker-env-", suffix=".env")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for key, value in plan["env"].items():
                if "\n" not in value:
                    fh.write(f"{key}={value}\n")
        args = plan["args"]
        res = _docker([*args[:2], "--env-file", env_file, *args[2:]], timeout=120)
    finally:
        try:
            os.unlink(env_file)
        except OSError:
            pass
    if res.returncode != 0:
        raise FleetError(f"docker run: {(res.stderr or '').strip()[:400]}")
    for net in plan["networks"]:
        _docker(["network", "connect", net, name], timeout=15)
    log.info("started worker container %s", name)
    return name


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_process() -> str:
    from common.paths import PROJECT_ROOT
    env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
    env.update({"AGENTS_HUB_ROLE": "worker", ORIGIN_ENV: ORIGIN_HUB,
                "AGENTS_HUB_WORKER_TOOL_PORT": str(_free_port())})
    proc = subprocess.Popen([sys.executable, "-m", "runtime.worker"], cwd=str(PROJECT_ROOT), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    # Reaped when it exits, so a stopped worker leaves no zombie behind.
    threading.Thread(target=proc.wait, name=f"worker-{proc.pid}", daemon=True).start()
    log.info("started worker process %s", proc.pid)
    return f"pid {proc.pid}"


def add(count: int = 1) -> List[str]:
    """Start ``count`` workers; their names (containers) or pids."""
    kind = launcher()
    if not kind:
        raise FleetError("this hub cannot start workers: it runs in a container without the Docker socket")
    start = _start_container if kind == "docker" else _start_process
    return [start() for _ in range(max(1, int(count)))]


# ── stopping ─────────────────────────────────────────────────────────────────

def stop(member_id: str) -> Dict[str, Any]:
    """Ask a worker to drain and exit. Raises LookupError for no such live
    worker, :class:`WorkerBusy` for one that carries live instances."""
    from common import members
    rec = members.get(member_id)
    if rec is None or rec.get("role") != "worker" or rec.get("status") != "live":
        raise LookupError(f"no live worker {member_id}")
    host = str(rec.get("host") or "")
    carriers = live_carriers(host) if host else 0
    if carriers:
        raise WorkerBusy(member_id, carriers)
    members.request_drain(member_id)
    box = next((c for c in _worker_containers() if host and c["id"].startswith(host)), None)
    if box is not None:
        # Else compose's restart policy would start it again once it exits.
        _docker(["update", "--restart", "no", box["id"]], timeout=15)
    log.info("asked worker %s to drain and exit", member_id)
    return {"member_id": member_id, "draining": True,
            "runs": int((rec.get("load") or {}).get("tracked") or 0)}


def cancel_stop(member_id: str) -> bool:
    """Take a drain request back, while the worker still lives."""
    from common import members
    return members.request_drain(member_id, on=False)


__all__ = ["FLEET_LABEL", "FleetError", "ORIGIN_ENV", "ORIGIN_HUB", "WorkerBusy", "add",
           "cancel_stop", "docker_run_args", "launcher", "live_carriers", "stop", "workers"]
