"""Docker mode for an imported agent: the hub runs the container itself.

An imported agent whose manifest names a Dockerfile can run in one of two
ways, recorded on its descriptor as ``runtime_mode``:

- ``url`` (the default): the operator starts the service somewhere and pastes
  its base URL. The hub never touches Docker.
- ``docker``: the hub builds the image from the agent's clone and starts **one
  container per workspace** on this host, the first time a run from that
  workspace needs the agent. The workspace's folder is bind-mounted at its own
  host path, so the ``workspace`` the hub sends with every run (an absolute
  host path, see ``agents.remote_agent``) resolves inside the container to the
  same files. Never the root of all workspaces: a container sees the one
  workspace it was started for.

The eval runner puts a case's files under the workspace too
(``<workspace>/.eval/<run>/<case>``), so that mount covers them. When
``AGENTS_HUB_EVAL_ROOT`` moves the eval folders elsewhere
(``evals.snapshot.eval_root``), that workspace's eval folder is mounted as a
second volume, again at its host path. Both mounts are read-write: a coding
agent's whole point is to edit the files.

The container's port is published on ``127.0.0.1`` only, on a free port
picked at start, and the URL is read back from the daemon rather than stored,
so a record can never point at a port the daemon reassigned. The containers
table (``managers.container_manager.register_container``) carries the
bookkeeping the Containers page shows.

Every Docker call goes through :func:`_docker`, which tests replace.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

MODE_KEY = "runtime_mode"
MODE_URL = "url"
MODE_DOCKER = "docker"
MODES = (MODE_URL, MODE_DOCKER)

IMAGE_PREFIX = "agents-hub-import"
CONTAINER_PREFIX = "agents-hub-import"
LABEL_IMPORT = "agents-hub.import"
LABEL_WORKSPACE = "agents-hub.workspace"

#: How long a freshly started container may take to answer its health path.
HEALTH_WAIT_SECONDS = int(os.environ.get("AGENT_IMPORT_HEALTH_WAIT", "90"))
BUILD_TIMEOUT = int(os.environ.get("AGENT_IMPORT_BUILD_TIMEOUT", "1800"))

#: Env the hub always hands a managed container, next to the manifest's own:
#: the workspace path it was started for, for adapters that want a default
#: working directory without being told one per run.
ENV_WORKSPACE = "AGENTS_HUB_WORKSPACE"


class DockerRuntimeError(RuntimeError):
    """A Docker-mode operation the operator has to look at (image not built,
    daemon down, container that never became healthy)."""


# ── small helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _docker(argv: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run one docker CLI command. Raises :class:`DockerRuntimeError` when
    there is no CLI; a non-zero exit is the caller's to read."""
    try:
        return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise DockerRuntimeError("the docker CLI is not installed on this host") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerRuntimeError(f"docker {argv[0]} did not finish within {timeout}s") from exc


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", (value or "").strip()).strip("-") or "x"


def is_docker_mode(remote: Optional[Dict[str, Any]]) -> bool:
    return str((remote or {}).get(MODE_KEY) or MODE_URL).strip().lower() == MODE_DOCKER


def image_tag(agent_id: str) -> str:
    return f"{IMAGE_PREFIX}/{_slug(agent_id).lower()}:latest"


def container_name(agent_id: str, workspace: str) -> str:
    return f"{CONTAINER_PREFIX}-{_slug(agent_id)}-{_slug(workspace)}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ── the workspace a run belongs to ───────────────────────────────────────────

def workspace_for_path(path: Optional[str]) -> str:
    """The workspace name a run's operating path belongs to.

    The path is what ``create_agent`` was given: a workspace folder, a project
    folder under it, or an eval case folder. An eval folder under a custom
    ``AGENTS_HUB_EVAL_ROOT`` is outside the workspaces root, so it is asked
    first. Nothing recognisable means the default workspace.
    """
    if not path:
        return "default"
    try:
        from evals.snapshot import workspace_of_eval_dir
        found = workspace_of_eval_dir(path)
        if found:
            return found
    except Exception:  # noqa: BLE001 - evals are optional at this layer
        log.debug("workspace_for_path: eval root lookup failed", exc_info=True)
    try:
        from common.workspace_context import workspace_name_from_path
        return workspace_name_from_path(path) or "default"
    except Exception:  # noqa: BLE001 - a bare name is better than a crash
        log.debug("workspace_for_path: fallback", exc_info=True)
        return Path(str(path)).name or "default"


def mounts_for(workspace: str) -> List[Tuple[str, str]]:
    """``(host_path, container_path)`` pairs for one workspace: its folder,
    and its eval folder when that lives outside it. Container paths equal
    host paths, which is what lets the per-run ``workspace`` resolve."""
    from managers.container_manager import _host_path
    from workspace import WORKSPACES_ROOT, create_workspace_folder

    try:
        create_workspace_folder(workspace)
    except Exception:  # noqa: BLE001 - the mount is created by the daemon anyway
        log.debug("mounts_for: could not create workspace folder", exc_info=True)
    ws_dir = (Path(WORKSPACES_ROOT) / workspace).resolve()
    mounts: List[Tuple[str, str]] = [(_host_path(ws_dir), str(ws_dir))]
    try:
        from evals.snapshot import eval_root
        root = eval_root(workspace)
        if root is not None:
            root = Path(root).resolve()
            try:
                root.relative_to(ws_dir)
                inside = True
            except ValueError:
                inside = False
            if not inside:
                root.mkdir(parents=True, exist_ok=True)
                mounts.append((_host_path(root), str(root)))
    except Exception:  # noqa: BLE001 - no evals package means no second mount
        log.debug("mounts_for: eval root unavailable", exc_info=True)
    return mounts


def container_env(descriptor: Dict[str, Any], workspace: str) -> Dict[str, str]:
    """The manifest's declared env, valued from the workspace's variables
    first and the hub's environment second. Undeclared names never cross."""
    manifest = descriptor.get("manifest") if isinstance(descriptor.get("manifest"), dict) else {}
    declared = manifest.get("env") if isinstance(manifest.get("env"), list) else []
    ws_env: Dict[str, str] = {}
    try:
        from workspace import get_workspace_metadata
        meta = get_workspace_metadata(workspace) or {}
        raw = meta.get("env_vars")
        if isinstance(raw, dict):
            ws_env = {str(k): str(v) for k, v in raw.items()}
    except Exception:  # noqa: BLE001 - an unreadable workspace has no env
        log.debug("container_env: workspace env unavailable", exc_info=True)
    out: Dict[str, str] = {}
    for entry in declared:
        name = str((entry or {}).get("name") or "").strip() if isinstance(entry, dict) else ""
        if not name:
            continue
        value = (ws_env.get(name) or os.environ.get(name) or "").strip()
        if value:
            out[name] = value
    from workspace import WORKSPACES_ROOT
    out[ENV_WORKSPACE] = str((Path(WORKSPACES_ROOT) / workspace).resolve())
    return out


# ── image ────────────────────────────────────────────────────────────────────

def _image_id(tag: str) -> Optional[str]:
    result = _docker(["image", "inspect", "--format", "{{.Id}}", tag], timeout=20)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def build_image(agent_id: str, descriptor: Dict[str, Any], *, no_cache: bool = False) -> Dict[str, Any]:
    """``docker build`` the agent's clone with the Dockerfile its manifest
    names. Returns the ``docker`` block to store on the descriptor."""
    clone_path = str(descriptor.get("clone_path") or "").strip()
    clone = Path(clone_path)
    dockerfile = str(descriptor.get("dockerfile") or "")
    manifest = descriptor.get("manifest") if isinstance(descriptor.get("manifest"), dict) else {}
    context = str(manifest.get("docker_context") or ".")
    if not dockerfile:
        raise DockerRuntimeError("the manifest names no Dockerfile, so there is nothing to build")
    if not clone_path or not clone.is_dir():
        raise DockerRuntimeError("the agent's clone is missing from disk; re-import the agent")
    if not (clone / dockerfile).is_file():
        raise DockerRuntimeError(f"{dockerfile} is not in the clone")
    tag = image_tag(agent_id)
    argv = ["build", "-t", tag, "-f", str(clone / dockerfile)]
    if no_cache:
        argv.append("--no-cache")
    argv.append(str((clone / context).resolve()))
    result = _docker(argv, timeout=BUILD_TIMEOUT)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-15:])
        raise DockerRuntimeError(f"docker build failed:\n{tail}")
    return {
        "image": tag,
        "image_id": _image_id(tag),
        "built_at": _now(),
        "commit": str(descriptor.get("commit") or ""),
    }


# ── containers ───────────────────────────────────────────────────────────────

def _inspect(name: str, fmt: str) -> Optional[str]:
    result = _docker(["inspect", "--format", fmt, name], timeout=20)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _published_url(name: str, port: int) -> Optional[str]:
    """The host side of the container's published port, as a base URL."""
    result = _docker(["port", name, f"{port}/tcp"], timeout=20)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        host, _, host_port = line.rpartition(":")
        host = host.strip("[]") or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        if host_port.isdigit():
            return f"http://{host}:{host_port}"
    return None


def _port_of(descriptor: Dict[str, Any]) -> int:
    manifest = descriptor.get("manifest") if isinstance(descriptor.get("manifest"), dict) else {}
    return int(descriptor.get("port") or manifest.get("port") or 8080)


def _wait_healthy(url: str, health_path: str, name: str) -> None:
    import httpx

    path = health_path if health_path.startswith("/") else "/" + health_path
    deadline = time.monotonic() + HEALTH_WAIT_SECONDS
    last = ""
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(url + path)
            if resp.status_code < 400:
                return
            last = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001 - not up yet
            last = f"{type(exc).__name__}"
        state = _inspect(name, "{{.State.Status}}")
        if state and state != "running":
            logs = _docker(["logs", "--tail", "20", name], timeout=20)
            raise DockerRuntimeError(
                f"container {name} is {state}:\n{(logs.stdout or '') + (logs.stderr or '')}".strip())
        time.sleep(1)
    raise DockerRuntimeError(
        f"container {name} did not answer its health path within {HEALTH_WAIT_SECONDS}s ({last})")


def container_record(agent_id: str, descriptor: Dict[str, Any], workspace: str) -> Optional[Dict[str, Any]]:
    """What the daemon knows about this agent's container for one workspace,
    or None when there is none."""
    name = container_name(agent_id, workspace)
    state = _inspect(name, "{{.State.Status}}")
    if not state:
        return None
    url = _published_url(name, _port_of(descriptor)) if state == "running" else None
    return {
        "workspace": workspace,
        "name": name,
        "state": state,
        "url": url,
        "image_id": _inspect(name, "{{.Image}}"),
        "started_at": _inspect(name, "{{.State.StartedAt}}"),
    }


def ensure_container(agent_id: str, descriptor: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    """The running container for this agent and workspace, started when it
    is missing, stopped, or on an image older than the one built."""
    from managers.container_manager import LABEL_MANAGED, register_container

    tag = image_tag(agent_id)
    image_id = _image_id(tag)
    if not image_id:
        raise DockerRuntimeError(
            f"the image for '{agent_id}' is not built; build it from the agent's page first")
    name = container_name(agent_id, workspace)
    current = container_record(agent_id, descriptor, workspace)
    if current and current["state"] == "running" and current.get("image_id") == image_id and current.get("url"):
        return current
    if current:
        _docker(["rm", "-f", name], timeout=30)
    port = _port_of(descriptor)
    host_port = _free_port()
    argv = [
        "run", "-d", "--name", name,
        "--label", LABEL_MANAGED,
        "--label", f"agents-hub.agent-id={agent_id}",
        "--label", f"{LABEL_IMPORT}={agent_id}",
        "--label", f"{LABEL_WORKSPACE}={workspace}",
        "-p", f"127.0.0.1:{host_port}:{port}",
    ]
    for host_path, container_path in mounts_for(workspace):
        argv += ["-v", f"{host_path}:{container_path}"]
    for key, value in container_env(descriptor, workspace).items():
        argv += ["-e", f"{key}={value}"]
    argv.append(tag)
    result = _docker(argv, timeout=120)
    if result.returncode != 0:
        raise DockerRuntimeError(f"docker run failed: {(result.stderr or result.stdout or '').strip()}")
    url = _published_url(name, port) or f"http://127.0.0.1:{host_port}"
    try:
        _wait_healthy(url, str(descriptor.get("health_path") or "/health"), name)
    except DockerRuntimeError:
        _docker(["rm", "-f", name], timeout=30)
        raise
    register_container(name, kind="import", agent_id=agent_id, image=tag,
                       extra={"workspace": workspace, "url": url})
    record = container_record(agent_id, descriptor, workspace) or {
        "workspace": workspace, "name": name, "state": "running", "url": url,
        "image_id": image_id, "started_at": _now()}
    return record


def stop_containers(agent_id: str, descriptor: Dict[str, Any],
                    workspace: Optional[str] = None) -> List[str]:
    """Stop and remove this agent's container for one workspace, or all of
    them. Returns the names removed."""
    from managers.container_manager import forget_container

    names = [container_name(agent_id, workspace)] if workspace else [
        rec["name"] for rec in list_containers(agent_id)]
    removed: List[str] = []
    for name in names:
        result = _docker(["rm", "-f", name], timeout=60)
        if result.returncode == 0:
            removed.append(name)
        forget_container(name)
    return removed


def list_containers(agent_id: str) -> List[Dict[str, Any]]:
    """Every container of this agent the daemon knows, running or not."""
    result = _docker([
        "ps", "-a", "--filter", f"label={LABEL_IMPORT}={agent_id}",
        "--format", "{{.Names}}\t{{.State}}\t{{.Label \"" + LABEL_WORKSPACE + "\"}}",
    ], timeout=20)
    out: List[Dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3 or not parts[0]:
            continue
        out.append({"name": parts[0], "state": parts[1], "workspace": parts[2]})
    return out


def status(agent_id: str, descriptor: Dict[str, Any]) -> Dict[str, Any]:
    """What the panel shows: the mode, the image, and the containers."""
    docker_block = dict(descriptor.get("docker") or {})
    out: Dict[str, Any] = {
        "mode": MODE_DOCKER if is_docker_mode(descriptor) else MODE_URL,
        "docker_available": True,
        "image": {"tag": image_tag(agent_id), "exists": False, **docker_block},
        "containers": [],
        "mounts_note": "workspace folder, plus its eval folder when that lives elsewhere",
    }
    try:
        out["image"]["exists"] = bool(_image_id(image_tag(agent_id)))
        port = _port_of(descriptor)
        for rec in list_containers(agent_id):
            rec["url"] = _published_url(rec["name"], port) if rec["state"] == "running" else None
            rec["mounts"] = [host for host, _ in mounts_for(rec["workspace"])] if rec["workspace"] else []
            out["containers"].append(rec)
    except DockerRuntimeError as exc:
        out["docker_available"] = False
        out["error"] = str(exc)
    return out


def url_for(agent_id: str, descriptor: Dict[str, Any], workspace_path: Optional[str]) -> str:
    """The base URL a run from ``workspace_path`` talks to, starting the
    workspace's container when needed."""
    workspace = workspace_for_path(workspace_path)
    record = ensure_container(agent_id, descriptor, workspace)
    url = record.get("url")
    if not url:
        raise DockerRuntimeError(f"container {record.get('name')} publishes no port")
    return str(url)


__all__ = [
    "DockerRuntimeError", "MODE_KEY", "MODE_URL", "MODE_DOCKER", "MODES",
    "is_docker_mode", "image_tag", "container_name", "workspace_for_path",
    "mounts_for", "container_env", "build_image", "ensure_container",
    "stop_containers", "list_containers", "container_record", "status", "url_for",
]
