"""
How busy the machine under a process is, and every agent container on the
Docker daemon it talks to.

Each member of the cluster (common/members.py: a backend replica, a worker)
runs one :class:`Sampler` thread. Every beat it takes:

- **the machine**: CPU in use (from ``/proc/stat`` between two samples on
  Linux, the sum of ``ps`` on macOS), the load average, memory in use and the
  disk under the state root, plus a short history for the page's sparklines.
  Inside a container on Linux ``/proc`` is the host kernel's, so a backend in
  compose reports the machine (or Docker Desktop's VM) its containers run on.
  ``machine_id`` is the kernel's boot id there, the same for every container
  of one machine, so the map shows the machine once, not once per process.
- **its Docker daemon**: ``docker info`` (CPUs, memory, running containers)
  and ``docker stats --no-stream`` for the agents-hub containers. Several
  members usually share a daemon; only one samples it (the first whose
  report is fresh, see ``skip_docker``), since ``docker stats`` waits a
  second or two for its numbers.

The sampler runs off the heartbeat thread: a daemon that stops answering
holds up this thread, never the beat that keeps the member live. The map
(dashboard/backend/routes/cluster.py) reads the snapshots from the members
table.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Samples kept for the sparklines: ten minutes at the default beat.
HISTORY = 40
#: How long a docker failure (no CLI, no daemon) is remembered before trying again.
DOCKER_RETRY_SECONDS = 60.0
#: ``docker info`` changes slowly; asked again after this long.
DOCKER_INFO_TTL = 60.0
STATS_TIMEOUT = 20

_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([a-zA-Z]*)\s*$")
_UNITS = {
    "": 1, "b": 1,
    "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
    "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_size(text: str) -> Optional[int]:
    """``"12.5MiB"`` → bytes; None when it is not a size."""
    m = _SIZE_RE.match(str(text or ""))
    if not m:
        return None
    factor = _UNITS.get(m.group(2).lower())
    if factor is None:
        return None
    try:
        return int(float(m.group(1)) * factor)
    except ValueError:
        return None


def parse_pair(text: str) -> Tuple[Optional[int], Optional[int]]:
    """``"1.2MB / 3kB"`` → (bytes, bytes)."""
    left, _, right = str(text or "").partition("/")
    return parse_size(left), parse_size(right)


def parse_percent(text: str) -> Optional[float]:
    try:
        return round(float(str(text or "").strip().rstrip("%")), 2)
    except ValueError:
        return None


# ── the machine ──────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _cmd(argv: List[str], timeout: float = 5.0) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def machine_id() -> str:
    """One id per running kernel: the boot id on Linux (shared by every
    container on the machine), the kernel's UUID on macOS, else the host name."""
    boot = _read("/proc/sys/kernel/random/boot_id").strip()
    if boot:
        return boot
    if sys.platform == "darwin":
        uuid = _cmd(["sysctl", "-n", "kern.uuid"]).strip()
        if uuid:
            return uuid
    return socket.gethostname()


def in_container() -> bool:
    return Path("/.dockerenv").exists() or "docker" in _read("/proc/1/cgroup")


def _proc_cpu_times() -> Optional[Tuple[int, int]]:
    """(idle, total) jiffies since boot from ``/proc/stat``."""
    line = _read("/proc/stat").split("\n", 1)[0]
    parts = line.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        nums = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
    # guest time is already counted in user/nice
    return idle, sum(nums[:8])


def _meminfo() -> Optional[Tuple[int, int]]:
    """(total, available) bytes from ``/proc/meminfo``."""
    text = _read("/proc/meminfo")
    if not text:
        return None
    vals: Dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        num = rest.strip().split(" ")[0]
        if num.isdigit():
            vals[key] = int(num) * 1024
    total = vals.get("MemTotal")
    if not total:
        return None
    avail = vals.get("MemAvailable")
    if avail is None:
        avail = vals.get("MemFree", 0) + vals.get("Buffers", 0) + vals.get("Cached", 0)
    return total, avail


def _darwin_memory() -> Optional[Tuple[int, int]]:
    total = _cmd(["sysctl", "-n", "hw.memsize"]).strip()
    stat = _cmd(["vm_stat"])
    if not total.isdigit() or not stat:
        return None
    page = 4096
    m = re.search(r"page size of (\d+) bytes", stat)
    if m:
        page = int(m.group(1))
    pages: Dict[str, int] = {}
    for line in stat.splitlines()[1:]:
        key, _, rest = line.partition(":")
        num = rest.strip().rstrip(".")
        if num.isdigit():
            pages[key.strip()] = int(num)
    avail = (pages.get("Pages free", 0) + pages.get("Pages inactive", 0)
             + pages.get("Pages speculative", 0) + pages.get("Pages purgeable", 0)) * page
    return int(total), avail


def _darwin_cpu_percent(cpus: int) -> Optional[float]:
    out = _cmd(["ps", "-A", "-o", "%cpu="])
    if not out:
        return None
    total = 0.0
    for line in out.split():
        try:
            total += float(line.replace(",", "."))
        except ValueError:
            continue
    return round(min(100.0, total / max(1, cpus)), 1)


class MachineProbe:
    """Takes the machine's numbers; keeps the previous CPU reading so the
    next one can say how much of the time in between was busy."""

    def __init__(self, disk_path: Optional[Path] = None) -> None:
        self.disk_path = disk_path
        self.prev_cpu: Optional[Tuple[int, int]] = None
        self.history: Deque[List[Any]] = deque(maxlen=HISTORY)
        self.id = machine_id()
        self.container = in_container()

    def cpu_percent(self, cpus: int) -> Optional[float]:
        now = _proc_cpu_times()
        if now is None:
            return _darwin_cpu_percent(cpus) if sys.platform == "darwin" else None
        prev, self.prev_cpu = self.prev_cpu, now
        if prev is None:
            return None
        d_idle, d_total = now[0] - prev[0], now[1] - prev[1]
        if d_total <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))), 1)

    def sample(self) -> Dict[str, Any]:
        cpus = os.cpu_count() or 1
        snap: Dict[str, Any] = {
            "machine_id": self.id, "host": socket.gethostname(), "platform": sys.platform,
            "kernel": platform.release(), "in_container": self.container, "cpus": cpus, "sampled_at": _now_iso(),
            "cpu_percent": self.cpu_percent(cpus),
        }
        try:
            snap["load"] = [round(x, 2) for x in os.getloadavg()]
        except (OSError, AttributeError):
            snap["load"] = None
        mem = _meminfo() or (_darwin_memory() if sys.platform == "darwin" else None)
        if mem:
            total, avail = mem
            snap["mem_total"] = total
            snap["mem_used"] = max(0, total - avail)
            snap["mem_percent"] = round(100.0 * snap["mem_used"] / total, 1) if total else None
        if self.disk_path is not None:
            try:
                du = shutil.disk_usage(str(self.disk_path))
                snap["disk_total"], snap["disk_used"] = du.total, du.used
                snap["disk_percent"] = round(100.0 * du.used / du.total, 1) if du.total else None
            except OSError:
                pass
        self.history.append([int(time.time()), snap.get("cpu_percent"), snap.get("mem_percent")])
        snap["history"] = list(self.history)
        return snap


# ── the Docker daemon ────────────────────────────────────────────────────────

def _docker(argv: List[str], timeout: float) -> Optional[str]:
    """stdout of a docker command, None when docker is missing or failed."""
    try:
        out = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def parse_stats_line(raw: Dict[str, Any]) -> Dict[str, Any]:
    """One ``docker stats --format '{{json .}}'`` row in numbers."""
    mem_used, mem_limit = parse_pair(raw.get("MemUsage", ""))
    net_rx, net_tx = parse_pair(raw.get("NetIO", ""))
    blk_r, blk_w = parse_pair(raw.get("BlockIO", ""))
    pids = str(raw.get("PIDs") or "").strip()
    return {
        "name": raw.get("Name") or raw.get("Container") or "",
        "cpu_percent": parse_percent(raw.get("CPUPerc", "")),
        "mem_used": mem_used, "mem_limit": mem_limit,
        "mem_percent": parse_percent(raw.get("MemPerc", "")),
        "net_rx": net_rx, "net_tx": net_tx, "block_read": blk_r, "block_write": blk_w,
        "pids": int(pids) if pids.isdigit() else None,
    }


class DockerProbe:
    """``docker info`` and ``docker stats`` of the daemon this process uses."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.failed_at = 0.0
        self.info: Optional[Dict[str, Any]] = None
        self.info_at = 0.0

    def daemon(self) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        if self.failed_at and now - self.failed_at < DOCKER_RETRY_SECONDS:
            return None
        if self.info is not None and now - self.info_at < DOCKER_INFO_TTL:
            return self.info
        out = _docker(["info", "--format", "{{json .}}"], timeout=15)
        try:
            raw = json.loads(out or "")
        except ValueError:
            raw = None
        if not isinstance(raw, dict) or not raw.get("ID"):
            self.failed_at, self.info = now, None
            return None
        self.failed_at = 0.0
        self.info_at = now
        self.info = {
            "daemon_id": raw.get("ID"), "name": raw.get("Name") or "",
            "cpus": raw.get("NCPU"), "mem_total": raw.get("MemTotal"),
            "server_version": raw.get("ServerVersion") or "",
            "os": raw.get("OperatingSystem") or "", "kernel": raw.get("KernelVersion") or "",
            "containers_running": raw.get("ContainersRunning"),
            "containers": raw.get("Containers"),
        }
        return self.info

    def sample(self, info: Dict[str, Any]) -> Dict[str, Any]:
        names_out = _docker(["ps", "--filter", f"label={self.label}", "--format", "{{.Names}}"],
                            timeout=15)
        names = [n.strip() for n in (names_out or "").splitlines() if n.strip()]
        rows: List[Dict[str, Any]] = []
        if names:
            out = _docker(["stats", "--no-stream", "--format", "{{json .}}", *names],
                          timeout=STATS_TIMEOUT) or ""
            for line in out.splitlines():
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if isinstance(raw, dict):
                    rows.append(parse_stats_line(raw))
        cpu_sum = round(sum(r["cpu_percent"] or 0 for r in rows), 2)
        mem_sum = sum(r["mem_used"] or 0 for r in rows)
        return {**info, "sampled_at": _now_iso(), "containers_sampled": rows,
                "agent_containers": len(names), "containers_cpu_percent": cpu_sum,
                "containers_mem_used": mem_sum}


# ── the sampler thread ───────────────────────────────────────────────────────

class Sampler(threading.Thread):
    """Samples the machine and the daemon every ``interval`` seconds and
    keeps the latest snapshot for the heartbeat to send.

    ``skip_docker(daemon_id)`` answers whether another live member already
    reports that daemon; then this one sends only the daemon's id."""

    def __init__(self, *, interval: float, label: str, disk_path: Optional[Path] = None,
                 skip_docker: Optional[Callable[[str], bool]] = None) -> None:
        super().__init__(name="host-metrics", daemon=True)
        self.interval = interval
        self.machine = MachineProbe(disk_path)
        self.docker = DockerProbe(label)
        self.skip_docker = skip_docker
        self._latest: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._halt = threading.Event()

    def latest(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def sample_once(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {"machine": self.machine.sample()}
        info = self.docker.daemon()
        if info:
            shared = False
            if self.skip_docker is not None:
                try:
                    shared = bool(self.skip_docker(str(info["daemon_id"])))
                except Exception:  # noqa: BLE001 - when unsure, sample it ourselves
                    log.debug("skip_docker failed", exc_info=True)
            snap["docker"] = ({"daemon_id": info["daemon_id"], "shared": True} if shared
                              else self.docker.sample(info))
        with self._lock:
            self._latest = snap
        return snap

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                self.sample_once()
            except Exception:  # noqa: BLE001 - background loop, must keep running until stop()
                log.debug("host metrics sample failed", exc_info=True)
            self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()


__all__ = [
    "DockerProbe", "MachineProbe", "Sampler", "in_container", "machine_id",
    "parse_pair", "parse_percent", "parse_size", "parse_stats_line",
]
