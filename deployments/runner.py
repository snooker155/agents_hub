"""The three ways a deployment's services actually run: docker, compose and
local. Each runner starts, inspects, stops and tails one service; the
orchestration (order, status, events, health) is deployments/service.py.

Every runner leaves the service reachable at ``http://127.0.0.1:<host_port>``
from the hub's host: docker publishes the container port to a free host port,
compose publishes whatever the file says, local simply listens there. A hub
that itself runs in a container reaches that address through
``host.docker.internal`` (common.hostnet.host_service_url), the same rewrite
the preview proxy applies; in that case the docker runner publishes on all
interfaces rather than loopback only, since loopback on the host is not
reachable from the hub's container.

Nothing here raises for a service that fails to start: the failure lands in
the returned :class:`ServiceRuntime` (``state="failed"``, ``error``), so the
service layer can journal it and move on to the next service.
"""
from __future__ import annotations

import logging
import os
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from common.hostnet import host_service_url, in_container
from common.paths import AGENTS_HUB_ROOT

from .models import DeployService, ProjectDeployment, ServiceRuntime, now_iso

log = logging.getLogger(__name__)

LOG_DIR = AGENTS_HUB_ROOT / "deployments"
BUILD_TIMEOUT = int(os.environ.get("AGENTS_HUB_DEPLOY_BUILD_TIMEOUT", "900"))
STOP_TIMEOUT = 15
MAX_LOG_CHARS = 200_000

LABEL_APP = "agents-hub.app"


# ── shared helpers ───────────────────────────────────────────────────────────

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def local_url(host_port: int) -> str:
    """Where the hub reaches a published port: loopback on the host, the
    host gateway from inside a container."""
    return host_service_url(f"http://127.0.0.1:{int(host_port)}")


def check_health(rt: ServiceRuntime, svc: DeployService, timeout: float = 3.0) -> bool:
    """A service is healthy when its port accepts a connection and, with a
    ``health_path``, answers HTTP with anything below 500 (a 404 on ``/`` is
    still a server that is up)."""
    return health_of(rt, svc, timeout=timeout)[0]


def health_of(rt: ServiceRuntime, svc: DeployService, timeout: float = 3.0) -> "tuple[bool, str]":
    """``(healthy, reason)``: the reason is what a person needs to see next
    to the service when it is not answering."""
    if not rt.host_port:
        return False, "no port was published for this service"
    url = local_url(rt.host_port)
    host = url.split("://", 1)[1].split(":")[0]
    if not port_open(rt.host_port, host=host, timeout=timeout):
        where = f"port {rt.host_port}" if rt.host_port == svc.port else f"port {rt.host_port} (container port {svc.port})"
        return False, (f"nothing answers on {where}: the app is not listening on "
                       f"port {svc.port}, or listens on localhost only")
    if not svc.health_path:
        return True, ""
    try:
        resp = httpx.get(url.rstrip("/") + svc.health_path, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        return False, f"GET {svc.health_path} failed: {exc.__class__.__name__}"
    if resp.status_code >= 500:
        return False, f"GET {svc.health_path} answered {resp.status_code}"
    return True, ""


def _process_group(pid: int) -> List[int]:
    """The pid and every process in its group (a shell and what it spawned)."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return []
    try:
        out = subprocess.run(["ps", "-o", "pid=", "-g", str(pgid)], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return [pid]
    pids = [int(x) for x in out.split() if x.strip().isdigit()]
    return pids or [pid]


def listening_ports(pid: int) -> List[int]:
    """TCP ports the process group of ``pid`` listens on, for a local service
    whose command ignored ``$PORT`` (``lsof`` on macOS and Linux, ``ss`` as
    the Linux fallback). Empty when nothing listens or neither tool exists."""
    pids = _process_group(pid)
    if not pids:
        return []
    ports: List[int] = []
    try:
        out = subprocess.run(["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-p", ",".join(map(str, pids)), "-Fn"],
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if line.startswith("n") and ":" in line:
                tail = line.rsplit(":", 1)[1]
                if tail.isdigit() and int(tail) not in ports:
                    ports.append(int(tail))
        if ports:
            return sorted(ports)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=10).stdout
        wanted = {f"pid={p}," for p in pids}
        for line in out.splitlines():
            if not any(w in line for w in wanted):
                continue
            cols = line.split()
            if len(cols) >= 4 and ":" in cols[3]:
                tail = cols[3].rsplit(":", 1)[1]
                if tail.isdigit() and int(tail) not in ports:
                    ports.append(int(tail))
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(ports)


def service_env(dep: ProjectDeployment, svc: DeployService, port: int, *,
                public_path: str) -> Dict[str, str]:
    """The variables a service gets: the deployment's, then its own, then the
    hub's (which win, since the port and the path are the hub's decision)."""
    env: Dict[str, str] = {}
    env.update(dep.env)
    env.update(svc.env)
    env.update({
        "PORT": str(port),
        "HOST": "0.0.0.0",
        "AGENTS_HUB_PUBLIC_PATH": public_path,
        "AGENTS_HUB_DEPLOYMENT_ID": dep.id,
        "AGENTS_HUB_SERVICE": svc.name,
    })
    return env


def shell_line(svc: DeployService) -> str:
    """``install && command`` as one shell line, ``$PORT`` and friends left to
    the shell."""
    parts = [p for p in (svc.install_command, svc.command) if p]
    if not parts:
        parts = ["echo 'no command configured' && exit 1"]
    return " && ".join(parts)


def _run(cmd: List[str], *, cwd: Optional[Path] = None, timeout: int = 120,
         env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(cwd) if cwd else None, env=env)


def _tail(text: str, lines: int) -> str:
    if len(text) > MAX_LOG_CHARS:
        text = text[-MAX_LOG_CHARS:]
    return "\n".join(text.splitlines()[-max(1, int(lines)):])


def _failed(error: str, **fields: Any) -> ServiceRuntime:
    return ServiceRuntime(state="failed", error=error[:4000], **fields)


# ── docker ───────────────────────────────────────────────────────────────────

class DockerRunner:
    name = "docker"

    @staticmethod
    def container_name(dep: ProjectDeployment, svc: DeployService) -> str:
        return f"agents-hub-app-{dep.id[:8]}-{svc.name}"

    @staticmethod
    def image_tag(dep: ProjectDeployment, svc: DeployService) -> str:
        return f"agents-hub/app-{dep.id[:8]}-{svc.name}:latest"

    def available(self) -> tuple:
        try:
            from managers.container_manager import _query
            _query(["docker", "info", "--format", "{{.ServerVersion}}"])
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return False, str(exc) if not isinstance(exc, ImportError) else "docker support is not installed"
        return True, ""

    def build(self, dep: ProjectDeployment, root: Path, svc: DeployService,
              log_lines: List[str]) -> Optional[str]:
        """Build the service's image from its Dockerfile. Returns an error
        string, or None."""
        context = root / svc.path if svc.path else root
        dockerfile = context / (svc.dockerfile or "Dockerfile")
        if not dockerfile.is_file():
            return f"Dockerfile not found: {dockerfile.relative_to(root)}"
        from managers.container_manager import _host_path
        cmd = ["docker", "build", "-t", self.image_tag(dep, svc), "-f", _host_path(dockerfile),
               _host_path(context)]
        try:
            result = _run(cmd, timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"docker build timed out after {BUILD_TIMEOUT}s"
        except FileNotFoundError:
            return "the docker CLI is not installed on this host"
        log_lines.append((result.stdout or "") + (result.stderr or ""))
        if result.returncode != 0:
            return _tail(result.stderr or result.stdout or "docker build failed", 40)
        return None

    def start(self, dep: ProjectDeployment, root: Path, svc: DeployService, *,
              public_path: str, limits: Optional[Dict[str, Any]] = None) -> ServiceRuntime:
        from managers.container_manager import LABEL_MANAGED, _host_path, get_or_create_network
        name = self.container_name(dep, svc)
        self._remove(name)
        try:
            network = get_or_create_network()
        except Exception as exc:  # noqa: BLE001 - docker missing or down
            return _failed(f"docker network unavailable: {exc}", container=name)
        host_port = free_port()
        bind = "" if in_container() else "127.0.0.1:"
        env = service_env(dep, svc, svc.port, public_path=public_path)
        cmd = ["docker", "run", "--detach", "--name", name, "--network", network,
               "--label", LABEL_MANAGED, "--label", f"{LABEL_APP}={dep.id}",
               "--label", f"agents-hub.app-service={svc.name}",
               "-p", f"{bind}{host_port}:{svc.port}",
               "--add-host", "host.docker.internal:host-gateway"]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        limits = limits or {}
        if limits.get("memory"):
            cmd += ["--memory", str(limits["memory"])]
        if limits.get("cpus"):
            cmd += ["--cpus", str(limits["cpus"])]
        if limits.get("pids_limit"):
            cmd += ["--pids-limit", str(limits["pids_limit"])]
        if svc.dockerfile:
            image = self.image_tag(dep, svc)
            cmd.append(image)
        else:
            image = svc.resolved_image
            workdir = root / svc.path if svc.path else root
            cmd += ["-v", f"{_host_path(workdir)}:/app", "-w", "/app", image,
                    "sh", "-lc", shell_line(svc)]
        try:
            result = _run(cmd, timeout=120)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return _failed(f"docker run failed: {exc}", container=name, image=image)
        if result.returncode != 0:
            return _failed(_tail(result.stderr or "docker run failed", 20), container=name, image=image)
        return ServiceRuntime(state="starting", container=name, host_port=host_port,
                              url=local_url(host_port), image=image, started_at=now_iso())

    def inspect(self, dep: ProjectDeployment, svc: DeployService, rt: ServiceRuntime) -> ServiceRuntime:
        name = rt.container or self.container_name(dep, svc)
        try:
            result = _run(["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", name],
                          timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return rt.model_copy(update={"state": "failed", "error": f"docker inspect failed: {exc}"})
        if result.returncode != 0:
            return rt.model_copy(update={"state": "stopped", "container": None, "healthy": None})
        status, _, code = (result.stdout or "").strip().partition(" ")
        exit_code = int(code) if code.strip().lstrip("-").isdigit() else None
        if status == "running":
            return rt.model_copy(update={"state": "running" if rt.state != "starting" else "starting",
                                         "exit_code": None})
        if status in ("exited", "dead"):
            return rt.model_copy(update={"state": "exited", "exit_code": exit_code, "healthy": False})
        return rt.model_copy(update={"state": status or "stopped"})

    def stop(self, dep: ProjectDeployment, svc: DeployService, rt: ServiceRuntime) -> None:
        self._remove(rt.container or self.container_name(dep, svc))

    def logs(self, dep: ProjectDeployment, svc: DeployService, rt: ServiceRuntime, tail: int) -> str:
        name = rt.container or self.container_name(dep, svc)
        try:
            result = _run(["docker", "logs", "--tail", str(int(tail)), name], timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return f"[error fetching logs: {exc}]"
        return (result.stdout or "") + (result.stderr or "")

    @staticmethod
    def _remove(name: str) -> None:
        try:
            _run(["docker", "rm", "-f", name], timeout=60)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.debug("could not remove container %s", name, exc_info=True)


# ── compose ──────────────────────────────────────────────────────────────────

class ComposeRunner:
    name = "compose"

    @staticmethod
    def project_name(dep: ProjectDeployment) -> str:
        return f"ah-app-{dep.id[:8]}"

    def _base(self, dep: ProjectDeployment, root: Path) -> List[str]:
        from managers.container_manager import _host_path
        compose_file = root / (dep.compose_file or "docker-compose.yml")
        return ["docker", "compose", "-p", self.project_name(dep), "-f", _host_path(compose_file)]

    def available(self) -> tuple:
        ok, reason = DockerRunner().available()
        if not ok:
            return ok, reason
        try:
            result = _run(["docker", "compose", "version"], timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return False, f"docker compose is not available: {exc}"
        if result.returncode != 0:
            return False, "docker compose is not available on this host"
        return True, ""

    def up(self, dep: ProjectDeployment, root: Path, *, build: bool, env: Dict[str, str],
           log_lines: List[str]) -> Optional[str]:
        compose_file = root / (dep.compose_file or "docker-compose.yml")
        if not compose_file.is_file():
            return f"compose file not found: {dep.compose_file or 'docker-compose.yml'}"
        cmd = self._base(dep, root) + ["up", "-d", "--remove-orphans"]
        if build:
            cmd.append("--build")
        run_env = {**os.environ, **env}
        try:
            result = _run(cmd, cwd=root, timeout=BUILD_TIMEOUT, env=run_env)
        except subprocess.TimeoutExpired:
            return f"docker compose up timed out after {BUILD_TIMEOUT}s"
        except FileNotFoundError:
            return "the docker CLI is not installed on this host"
        log_lines.append((result.stdout or "") + (result.stderr or ""))
        if result.returncode != 0:
            return _tail(result.stderr or result.stdout or "docker compose up failed", 40)
        return None

    def down(self, dep: ProjectDeployment, root: Path) -> None:
        try:
            _run(self._base(dep, root) + ["down", "--remove-orphans"], cwd=root, timeout=120)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.debug("compose down failed for %s", dep.id, exc_info=True)

    def runtime_for(self, dep: ProjectDeployment, root: Path, svc: DeployService) -> ServiceRuntime:
        """Where compose published the service's port, and whether it runs."""
        base = self._base(dep, root)
        try:
            ps = _run(base + ["ps", "--format", "json", svc.name], cwd=root, timeout=30)
            port = _run(base + ["port", svc.name, str(svc.port)], cwd=root, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return _failed(f"docker compose failed: {exc}")
        state = "stopped"
        exit_code: Optional[int] = None
        container: Optional[str] = None
        import json
        for line in (ps.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            container = row.get("Name") or container
            st = str(row.get("State") or "").lower()
            if st == "running":
                state = "running"
            elif st in ("exited", "dead"):
                state = "exited"
                code = row.get("ExitCode")
                exit_code = int(code) if isinstance(code, int) else None
            elif st:
                state = st
        host_port: Optional[int] = None
        text = (port.stdout or "").strip().splitlines()
        if text and ":" in text[-1]:
            tail = text[-1].rsplit(":", 1)[1]
            if tail.isdigit():
                host_port = int(tail)
        return ServiceRuntime(state=state, container=container, host_port=host_port,
                              url=local_url(host_port) if host_port else None, exit_code=exit_code,
                              started_at=now_iso() if state == "running" else None)

    def logs(self, dep: ProjectDeployment, root: Path, svc: DeployService, tail: int) -> str:
        try:
            result = _run(self._base(dep, root) + ["logs", "--no-color", "--tail", str(int(tail)), svc.name],
                          cwd=root, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return f"[error fetching logs: {exc}]"
        return (result.stdout or "") + (result.stderr or "")


# ── local ────────────────────────────────────────────────────────────────────

#: Processes this backend started, by pid, so their exit code can be reaped.
_procs: Dict[int, subprocess.Popen] = {}


class LocalRunner:
    name = "local"

    def available(self) -> tuple:
        return True, ""

    @staticmethod
    def log_path(dep: ProjectDeployment, svc: DeployService) -> Path:
        return LOG_DIR / dep.id / f"{svc.name}.log"

    def start(self, dep: ProjectDeployment, root: Path, svc: DeployService, *,
              public_path: str) -> ServiceRuntime:
        workdir = root / svc.path if svc.path else root
        if not workdir.is_dir():
            return _failed(f"service folder not found: {svc.path or '.'}")
        if port_open(svc.port):
            return _failed(f"port {svc.port} is already in use on this host")
        from tools.shell import scrubbed_env
        env = scrubbed_env()
        env.update(service_env(dep, svc, svc.port, public_path=public_path))
        log_file = self.log_path(dep, svc)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = open(log_file, "ab")  # noqa: SIM115 - handed to the child, closed below
            handle.write(f"\n=== {now_iso()} {shell_line(svc)}\n".encode("utf-8"))
            proc = subprocess.Popen(
                ["sh", "-lc", shell_line(svc)], cwd=str(workdir), env=env,
                stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            handle.close()
        except OSError as exc:
            return _failed(f"could not start: {exc}", log_file=str(log_file))
        _procs[proc.pid] = proc
        return ServiceRuntime(state="starting", pid=proc.pid, host_port=svc.port,
                              url=local_url(svc.port), started_at=now_iso(), log_file=str(log_file))

    def inspect(self, dep: ProjectDeployment, svc: DeployService, rt: ServiceRuntime) -> ServiceRuntime:
        if not rt.pid:
            return rt.model_copy(update={"state": "stopped"})
        proc = _procs.get(rt.pid)
        if proc is not None:
            code = proc.poll()
            if code is not None:
                _procs.pop(rt.pid, None)
                return rt.model_copy(update={"state": "exited", "exit_code": code, "healthy": False})
            return rt
        # Started by an earlier backend process: alive or not is all we know.
        try:
            os.kill(rt.pid, 0)
        except ProcessLookupError:
            return rt.model_copy(update={"state": "exited", "healthy": False})
        except PermissionError:
            pass
        return rt

    def stop(self, dep: ProjectDeployment, svc: DeployService, rt: ServiceRuntime) -> None:
        if not rt.pid:
            return
        try:
            pgid = os.getpgid(rt.pid)
        except ProcessLookupError:
            _procs.pop(rt.pid, None)
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            _procs.pop(rt.pid, None)
            return
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline:
            try:
                os.kill(rt.pid, 0)
            except ProcessLookupError:
                break
            proc = _procs.get(rt.pid)
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(0.2)
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc = _procs.pop(rt.pid, None)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001, S110 - already killed; nothing left to reap
                log.debug("wait after kill failed for pid %s", rt.pid, exc_info=True)

    def logs(self, dep: ProjectDeployment, svc: DeployService, rt: ServiceRuntime, tail: int) -> str:
        path = Path(rt.log_file) if rt.log_file else self.log_path(dep, svc)
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        return _tail(data[-MAX_LOG_CHARS:].decode("utf-8", errors="replace"), tail)


docker_runner = DockerRunner()
compose_runner = ComposeRunner()
local_runner = LocalRunner()


def quote(cmd: List[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


__all__ = ["DockerRunner", "ComposeRunner", "LocalRunner", "docker_runner", "compose_runner",
           "local_runner", "check_health", "health_of", "listening_ports", "free_port", "port_open", "local_url", "service_env",
           "shell_line", "LOG_DIR", "LABEL_APP"]
