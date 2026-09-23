"""ContainerManager — Docker container lifecycle for agents.

Architecture
------------
            ┌─────────────────────────────┐
            │       Host process           │
            │   (node_manager /           │
            │    run_manager)             │
            │                             │
            │  ContainerManager           │
            │   ├─ build_base_image()     │
            │   ├─ build_image(agent_id)  │──► docker build
            │   ├─ start_container(...)   │──► docker run -d
            │   ├─ stop_container(name)   │──► docker stop
            │   └─ get_logs(name)         │──► docker logs
            └──────────────┬──────────────┘
                           │ agents-hub bridge network
               ┌───────────┼───────────┐
          ┌────▼──┐   ┌────▼──┐   ┌───▼───┐
          │swe-   │   │orch-  │   │decomp-│
          │agent  │   │agent  │   │agent  │
          │  ctr  │   │  ctr  │   │  ctr  │
          └───────┘   └───────┘   └───────┘
         (no Docker socket — cannot spawn containers)

Security model
--------------
• The Docker socket is NEVER mounted into agent containers.
• Containers have no --privileged flag.
• All containers share the "agents-hub" bridge network and can
  reach each other by container name, but cannot modify the
  Docker daemon.
• ContainerManager is the sole authority for starting and
  stopping containers.  It usually runs on the host; under
  docker-compose it runs in the backend container, which is
  given the host socket for exactly this purpose.

Volume mounts
-------------
Only what an agent needs is mounted, over the source baked into
its image:

  /app/.agents_hub  ← state, run records, logs
  /app/tasks        ← task storage
  /workspace        ← the run's workspace, when it has one

Bind mounts are resolved by the daemon, so when this code runs
inside a container the paths are rebased from /app onto
HOST_PROJECT_ROOT first (see _host_path).

Image naming
------------
  Base image:        agents-hub/base:latest
  Per-agent image:   agents-hub/<agent_id>:latest

Container naming
----------------
  Node containers:   agents-hub-node-<node_id[:12]>
  Run containers:    agents-hub-run-<run_id[:12]>

Dockerfile storage
------------------
  Generated Dockerfiles are saved to:
    .agents_hub/dockerfiles/<agent_id>.Dockerfile
  These can be inspected or committed to source control.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from common.hostnet import to_host_gateway
from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT
from common.snapshot import SNAPSHOT_DIR_ENV

logger = logging.getLogger(__name__)

DOCKERFILE_DIR = AGENTS_HUB_ROOT / "dockerfiles"
DOCKERFILE_DIR.mkdir(parents=True, exist_ok=True)

NETWORK_NAME = "agents-hub"
BASE_IMAGE = "agents-hub/base:latest"
IMAGE_PREFIX = "agents-hub"
LABEL_MANAGED = "agents-hub.managed=true"

# Mount points inside every agent container (nodes and runs alike).
CONTAINER_STATE_DIR = "/app/.agents_hub"
CONTAINER_TASKS_DIR = "/app/tasks"
CONTAINER_WORKSPACE_DIR = "/workspace"

# Resource limits for one-shot *run* containers only (nodes are unaffected —
# they keep calling start_container with hardened=False, the default).
DEFAULT_RUN_MEMORY = "2g"
DEFAULT_RUN_CPUS = "2"
DEFAULT_RUN_PIDS_LIMIT = 512


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_project_root() -> str:
    """Where this project lives *on the Docker host*, or "" when that is here.

    Set by docker-compose to the host path it mounts at /app. It matters
    because bind mounts are resolved by the daemon, not by us: when this
    process is itself containerized, ``-v /app/.agents_hub:...`` would point
    the daemon at a host directory that does not exist. Read per call so tests
    and a restarted-with-different-env process both see the current value.
    """
    return os.environ.get("HOST_PROJECT_ROOT", "").strip().rstrip("/")


def _host_path(path: str | Path) -> str:
    """Translate a path in *our* filesystem to the host path to bind-mount.

    A no-op on a host-run backend. Inside a container it rebases paths under
    the project root onto HOST_PROJECT_ROOT; anything outside that root is
    passed through unchanged, since we have no way to map it and the daemon
    may well resolve it correctly anyway.
    """
    resolved = Path(path).resolve()
    host_root = _host_project_root()
    if not host_root:
        return str(resolved)
    try:
        relative = resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return str(resolved)
    # PurePosixPath: the daemon we talk to runs Linux even when we do not.
    return str(PurePosixPath(host_root) / relative) if str(relative) != "." else host_root


# What a local model server is called on the host, before the container sees it.
_LOCAL_MODEL_URLS = {
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "LMSTUDIO_BASE_URL": "http://localhost:1234",
}


def _point_local_models_at_the_host(env: Dict[str, str]) -> Dict[str, str]:
    """Rewrite the local-model URLs an agent container will inherit.

    Unconditional, and set even when the variable is absent: the compiled-in
    default is `localhost`, which inside the container means the container. The
    counterpart on the receiving side is the `--add-host` above, which is what
    makes the alias resolve on Linux.
    """
    out = dict(env)
    for key, default in _LOCAL_MODEL_URLS.items():
        out[key] = to_host_gateway(out.get(key) or default)
    return out


def _run(cmd: List[str], timeout: int = 300, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a docker CLI command, raising on non-zero exit."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


# ── Env forwarding ────────────────────────────────────────────────────────────

_PASS_PREFIXES = (
    "OPENAI_", "ANTHROPIC_", "GOOGLE_", "OLLAMA_", "LMSTUDIO_",
    "LANGFUSE_", "RAG_", "LLM_", "ORCH_",
)
_PASS_EXACT = {
    "DEFAULT_PROVIDER",
}


def _env_flags(env: Optional[Dict[str, str]] = None) -> List[str]:
    source = env if env is not None else dict(os.environ)
    flags: List[str] = []
    for key, value in source.items():
        if not value:
            continue
        if key in _PASS_EXACT or any(key.startswith(p) for p in _PASS_PREFIXES):
            flags.extend(["-e", f"{key}={value}"])
    # Security: never pass Docker-related env vars
    flags = [f for f in flags if "DOCKER" not in f]
    return flags


# A run container is a different case from a node container: agent_run.py
# reads most of its own bookkeeping (workspace name, session id, log file,
# instance id, the relay token) straight out of os.environ the same way for a
# subprocess or a container, so a run needs close to the full environment a
# local subprocess would get — not just the provider-key allowlist above.
# What it must never see is anything that describes *this host* rather than
# the run: Docker's own control variables, the HOST_PROJECT_ROOT bind-mount
# translation, the host's SSH agent socket, and interpreter/venv paths that
# are meaningless inside the image (the image bakes its own Python, HOME and
# PATH). AGENTS_HUB_API_TOKEN is deliberately kept — the run's relay calls
# back over HTTP and need it to authenticate.
_HOST_ONLY_EXACT = {"HOST_PROJECT_ROOT", "SSH_AUTH_SOCK", "HOME", "PATH"}
_HOST_ONLY_PREFIXES = ("DOCKER_", "npm_", "VIRTUAL_ENV", "CONDA_")


def container_env(env: Dict[str, str]) -> Dict[str, str]:
    """Return ``env`` minus variables that only make sense on the host.

    Denylist, not allowlist: a run container needs almost everything a local
    subprocess run gets, so this drops the handful of host-only variables
    instead of re-deriving the whole environment a run needs.
    """
    out: Dict[str, str] = {}
    for key, value in env.items():
        if key in _HOST_ONLY_EXACT:
            continue
        if any(key.startswith(p) for p in _HOST_ONLY_PREFIXES):
            continue
        out[key] = value
    return out


# ── Network management ────────────────────────────────────────────────────────

def get_or_create_network() -> str:
    """Ensure the agents-hub bridge network exists. Returns network name."""
    result = _run(["docker", "network", "ls", "--filter", f"name=^{NETWORK_NAME}$", "--format", "{{.Name}}"])
    if NETWORK_NAME not in (result.stdout or "").splitlines():
        _run(["docker", "network", "create", "--driver", "bridge", NETWORK_NAME])
    _attach_self_to_network()
    return NETWORK_NAME


def _attach_self_to_network() -> None:
    """Put our own container on the agents-hub network, if we are one.

    A containerized backend starts agents on a network it is not itself on, and
    then cannot reach the node HTTP servers it just started. Joining lazily,
    here, rather than declaring the network in docker-compose.yml: this network
    outlives any one compose project (a natively-run backend creates it too),
    and compose refuses to adopt a network it did not create.
    """
    if not _host_project_root():
        return  # not containerized; the host is already on every bridge
    # Compose leaves the hostname as the container id, which docker accepts
    # wherever it accepts a name.
    me = socket.gethostname().strip()
    if not me:
        return
    result = _run(["docker", "network", "connect", NETWORK_NAME, me], timeout=15)
    err = (result.stderr or "")
    if result.returncode != 0 and "already exists in network" not in err:
        logger.debug("could not join %s as %s: %s", NETWORK_NAME, me, err.strip())


# ── Dockerfile generation ─────────────────────────────────────────────────────

def generate_dockerfile(
    agent_id: str,
    agent_name: str = "",
    http_expose: bool = False,
    http_port: int = 8080,
) -> str:
    """Return a per-agent Dockerfile that extends the unified base image.

    The Dockerfile is minimal — it only sets the agent identity labels,
    the AGENT_ID env var, and the default CMD.  All actual code and
    dependencies come from the base image (agents-hub/base:latest).

    When http_expose=True an EXPOSE directive is added and the default CMD
    includes --http-port so the node_run starts the HTTP server.
    """
    label_name = agent_name or agent_id
    expose_line = f"\nEXPOSE {http_port}" if http_expose else ""
    http_cmd = f', "--http-port", "{http_port}"' if http_expose else ""
    return f"""\
# Auto-generated by agents-hub ContainerManager
# Agent: {label_name} ({agent_id})
#
# Build:
#   docker build -t {IMAGE_PREFIX}/{agent_id}:latest \\
#     -f .agents_hub/dockerfiles/{agent_id}.Dockerfile .
#
# The base image must exist first:
#   docker build -t {BASE_IMAGE} -f Dockerfile.agents .

FROM {BASE_IMAGE}

LABEL agents-hub.managed="true"
LABEL agents-hub.agent-id="{agent_id}"
LABEL agents-hub.agent-name="{label_name}"

# Force local execution inside the container so agents never try
# to spin up further Docker containers.
ENV AGENT_ID="{agent_id}"
ENV AGENT_EXECUTION_MODE="local"
{expose_line}
CMD ["python", "-m", "runtime.node_run", "--agent-id", "{agent_id}"{http_cmd}]
"""


def save_dockerfile(
    agent_id: str,
    agent_name: str = "",
    http_expose: bool = False,
    http_port: int = 8080,
) -> Path:
    """Write the generated Dockerfile to disk and return its path."""
    content = generate_dockerfile(agent_id, agent_name, http_expose=http_expose, http_port=http_port)
    path = DOCKERFILE_DIR / f"{agent_id}.Dockerfile"
    path.write_text(content, encoding="utf-8")
    return path


# ── Image management ──────────────────────────────────────────────────────────

def build_base_image(no_cache: bool = False) -> Dict[str, Any]:
    """Build the unified base image (agents-hub/base:latest).

    Returns a result dict with keys: success, image, log, error.
    """
    cmd = ["docker", "build", "-t", BASE_IMAGE]
    if no_cache:
        cmd.append("--no-cache")
    cmd += ["-f", "Dockerfile.agents", "."]
    try:
        result = _run(cmd, timeout=600)
        success = result.returncode == 0
        log = (result.stdout or "") + (result.stderr or "")
        return {
            "success": success,
            "image": BASE_IMAGE if success else None,
            "log": log,
            "error": None if success else log.splitlines()[-1] if log else "build failed",
            "built_at": _utc_now_iso() if success else None,
        }
    except Exception as exc:
        return {"success": False, "image": None, "log": "", "error": str(exc), "built_at": None}


def build_image(
    agent_id: str,
    agent_name: str = "",
    no_cache: bool = False,
    http_expose: bool = False,
    http_port: int = 8080,
) -> Dict[str, Any]:
    """Build a per-agent image (agents-hub/<agent_id>:latest).

    Generates the Dockerfile, saves it to .agents_hub/dockerfiles/,
    then runs docker build.  The base image must exist first.

    Returns a result dict with keys: success, image, dockerfile, log, error.
    """
    image_tag = f"{IMAGE_PREFIX}/{agent_id}:latest"
    dockerfile_path = save_dockerfile(agent_id, agent_name, http_expose=http_expose, http_port=http_port)

    cmd = ["docker", "build", "-t", image_tag]
    if no_cache:
        cmd.append("--no-cache")
    # Use the project root as build context even though the Dockerfile barely
    # needs it; this keeps the build simple and consistent.
    cmd += ["-f", str(dockerfile_path), "."]

    try:
        result = _run(cmd, timeout=600)
        success = result.returncode == 0
        log = (result.stdout or "") + (result.stderr or "")
        return {
            "success": success,
            "image": image_tag if success else None,
            "dockerfile": str(dockerfile_path),
            "log": log,
            "error": None if success else log.splitlines()[-1] if log else "build failed",
            "built_at": _utc_now_iso() if success else None,
        }
    except Exception as exc:
        return {
            "success": False, "image": None,
            "dockerfile": str(dockerfile_path),
            "log": "", "error": str(exc), "built_at": None,
        }


def list_images() -> List[Dict[str, Any]]:
    """Return all agents-hub Docker images on the host."""
    result = _run([
        "docker", "images",
        "--filter", f"label={LABEL_MANAGED}",
        "--format", "{{json .}}",
    ])
    images: List[Dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            images.append({
                "repository": raw.get("Repository", ""),
                "tag": raw.get("Tag", ""),
                "id": raw.get("ID", ""),
                "size": raw.get("Size", ""),
                "created": raw.get("CreatedAt", ""),
            })
        except Exception:
            pass
    return images


def image_exists(image_tag: str) -> bool:
    """Check whether a specific image tag exists locally."""
    result = _run(["docker", "images", "-q", image_tag])
    return bool((result.stdout or "").strip())


def image_tag_for_agent(agent_id: str) -> str:
    """Return the expected image tag for an agent, preferring per-agent then base."""
    per_agent = f"{IMAGE_PREFIX}/{agent_id}:latest"
    if image_exists(per_agent):
        return per_agent
    if image_exists(BASE_IMAGE):
        return BASE_IMAGE
    # Fall back to the globally configured image, read live so the Settings
    # page applies without a restart (common.config.live_setting).
    from common.config import live_setting
    return live_setting("AGENT_DOCKER_IMAGE", BASE_IMAGE)


# ── Container management ──────────────────────────────────────────────────────

def build_run_command(
    *,
    container_name: str,
    agent_id: str,
    image: str,
    network: str,
    translated_cmd: List[str],
    state_dir: str,
    tasks_dir: str,
    workspace: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    extra_args: Optional[str] = None,
    memory: Optional[str] = None,
    cpus: Optional[str] = None,
    pids_limit: Optional[int] = None,
    snapshot_dir: Optional[str] = None,
) -> List[str]:
    """Build the ``docker run`` argv for a sandboxed, one-shot run container.

    Pure: takes the image tag and network name as plain strings (callers
    resolve those against the daemon separately) and does no I/O of its own
    beyond reading the two live-settings memory/cpu defaults, so tests can
    assert on the produced command line without a Docker daemon.

    Security posture, on top of what a node container already gets (no Docker
    socket, no --privileged, its own bridge network):
      --cap-drop ALL / --security-opt no-new-privileges — no Linux
        capabilities and no privilege escalation via setuid binaries.
      --read-only + --tmpfs /tmp — the image's own filesystem cannot be
        written to; only /tmp, the state dir and the workspace are writable.
      --memory / --cpus / --pids-limit — a single run cannot exhaust the host.
      snapshot_dir, when given, is the registry snapshot the launcher wrote
        for this run (common/snapshot.py: agents.json, custom_providers.json,
        models.json), re-mounted read-only *inside* the state dir mount and
        named in AGENTS_HUB_SNAPSHOT_DIR, so the run reads its agent
        definitions and provider credentials from a frozen copy and cannot
        edit them, whatever the state dir mount allows. The state dir itself
        mounts read-write, so a run can still write its own logs and run
        records there, unless AGENT_RUN_STATE_TRANSPORT is "http" in
        `env`, in which case the state dir is read-only wholesale (the run
        reaches the database through the backend's /api/run-state routes
        instead of opening it directly, see common/state_transport.py) and
        only run_logs/ is re-mounted read-write on top, for the run's own log
        file. The default ("db", direct SQLite access) mounts the state dir
        read-write, unchanged from before this mode existed. See
        docs/containers.md.
    """
    from common.config import live_setting
    mem = memory or live_setting("AGENT_DOCKER_MEMORY", DEFAULT_RUN_MEMORY)
    cpu = cpus or live_setting("AGENT_DOCKER_CPUS", DEFAULT_RUN_CPUS)
    pids = pids_limit or DEFAULT_RUN_PIDS_LIMIT
    http_transport = (env or {}).get("AGENT_RUN_STATE_TRANSPORT") == "http"

    docker_cmd = [
        "docker", "run",
        "--detach",
        "--rm",
        "--name", container_name,
        "--network", network,
        "--label", LABEL_MANAGED,
        "--label", f"agents-hub.agent-id={agent_id}",
        "--label", "agents-hub.container-type=run",
        "--memory", str(mem),
        "--cpus", str(cpu),
        "--pids-limit", str(pids),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp",
        "-v", f"{_host_path(state_dir)}:{CONTAINER_STATE_DIR}" + (":ro" if http_transport else ""),
        "-v", f"{_host_path(tasks_dir)}:{CONTAINER_TASKS_DIR}",
    ]

    if http_transport:
        # Re-mounted read-write on top of the now read-only state dir: the run
        # still writes its own log file directly (the log tee in
        # runtime/agent_run.py has no HTTP equivalent), everything else about
        # its state goes through /api/run-state.
        run_logs_dir = str(Path(state_dir) / "run_logs")
        docker_cmd += ["-v", f"{_host_path(run_logs_dir)}:{CONTAINER_STATE_DIR}/run_logs"]

    snapshot_in_container: Optional[str] = None
    if snapshot_dir:
        snapshot_in_container = f"{CONTAINER_STATE_DIR}/run_snapshots/{Path(snapshot_dir).name}"
        docker_cmd += ["-v", f"{_host_path(snapshot_dir)}:{snapshot_in_container}:ro"]

    docker_cmd += ["-w", "/app"]

    if workspace:
        docker_cmd.extend(["-v", f"{_host_path(workspace)}:{CONTAINER_WORKSPACE_DIR}"])

    # Same host-service reachability as a node container (Ollama, LM Studio).
    docker_cmd.extend(["--add-host", "host.docker.internal:host-gateway"])

    merged_env = container_env(dict(env or {}))
    if snapshot_in_container:
        merged_env[SNAPSHOT_DIR_ENV] = snapshot_in_container
    merged_env["AGENT_EXECUTION_MODE"] = "local"
    merged_env = _point_local_models_at_the_host(merged_env)
    for key, value in merged_env.items():
        if value:
            docker_cmd.extend(["-e", f"{key}={value}"])

    if extra_args:
        docker_cmd.extend(shlex.split(extra_args))

    docker_cmd.append(image)
    docker_cmd.extend(translated_cmd)
    return docker_cmd


def container_name_for_node(node_id: str) -> str:
    return f"agents-hub-node-{node_id.replace('-', '')[:12]}"


def container_name_for_run(run_id: str) -> str:
    return f"agents-hub-run-{run_id.replace('-', '')[:12]}"


def container_running(container_name: str) -> bool:
    """Return True if the named container is currently in 'running' state."""
    try:
        result = _run([
            "docker", "ps", "-q",
            "--filter", f"name=^{container_name}$",
            "--filter", "status=running",
        ], timeout=10)
        return bool((result.stdout or "").strip())
    except Exception:
        return False


def start_container(
    container_name: str,
    agent_id: str,
    cmd: List[str],
    workspace: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    extra_args: Optional[str] = None,
    http_expose: bool = False,
    http_port: int = 8080,
    http_host_port: Optional[int] = None,
    *,
    hardened: bool = False,
    memory: Optional[str] = None,
    cpus: Optional[str] = None,
    pids_limit: Optional[int] = None,
    snapshot_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a detached container for an agent.

    The container runs on the agents-hub network.  Only specific directories
    are mounted (state, tasks, workspace) rather than the entire project root.
    No Docker socket is mounted.

    Mount layout inside container:
      /app/.agents_hub  ← .agents_hub  (run records, logs, dockerfiles, state)
      /app/tasks        ← tasks        (task storage)
      /workspace        ← workspace dir (if provided)

    When http_expose=True the container port http_port is published to
    http_host_port on the host (defaults to the same port number).

    ``hardened`` (used only by start_run_container, one-shot task runs — nodes
    keep the default, unchanged shape above) additionally applies: a scrubbed
    environment (container_env, not the provider-key allowlist), --memory /
    --cpus / --pids-limit, --cap-drop ALL, --security-opt no-new-privileges,
    a --read-only root with --tmpfs /tmp, and the registry snapshot
    (``snapshot_dir``, see common/snapshot.py) mounted read-only inside the
    (still read-write) state directory. A node container gets the snapshot
    too, through AGENTS_HUB_SNAPSHOT_DIR, since the registries live in the
    database and a container has no database of its own. See
    docs/containers.md for the full mount table.

    Returns: {success, container_id, container_name, image, error,
              http_url (if http_expose)}
    """
    network = get_or_create_network()
    image = image_tag_for_agent(agent_id)

    # Build path mapping: our path → container path. These are the paths the
    # command line carries, so they are translated as we see them; the bind
    # mounts below instead need the path as the *daemon* sees it (_host_path).
    state_dir = str(AGENTS_HUB_ROOT)
    tasks_dir = str(PROJECT_ROOT / "tasks")
    path_mappings = {
        state_dir: CONTAINER_STATE_DIR,
        tasks_dir: CONTAINER_TASKS_DIR,
    }
    if workspace:
        path_mappings[str(Path(workspace).resolve())] = CONTAINER_WORKSPACE_DIR

    translated_cmd = _translate_paths(cmd, path_mappings)

    from common.config import live_setting
    extra = extra_args or live_setting("AGENT_DOCKER_EXTRA_ARGS")

    if hardened:
        docker_cmd = build_run_command(
            container_name=container_name,
            agent_id=agent_id,
            image=image,
            network=network,
            translated_cmd=translated_cmd,
            state_dir=state_dir,
            tasks_dir=tasks_dir,
            workspace=workspace,
            env=env,
            extra_args=extra,
            memory=memory,
            cpus=cpus,
            pids_limit=pids_limit,
            snapshot_dir=snapshot_dir,
        )
    else:
        docker_cmd = [
            "docker", "run",
            "--detach",
            "--rm",
            "--name", container_name,
            "--network", network,
            "--label", LABEL_MANAGED,
            "--label", f"agents-hub.agent-id={agent_id}",
            # Mount only what the agent needs
            "-v", f"{_host_path(state_dir)}:{CONTAINER_STATE_DIR}",
            "-v", f"{_host_path(tasks_dir)}:{CONTAINER_TASKS_DIR}",
            "-w", "/app",
        ]

        # Mount workspace if provided
        if workspace:
            docker_cmd.extend(["-v", f"{_host_path(workspace)}:{CONTAINER_WORKSPACE_DIR}"])

        # Publish HTTP port when the agent exposes an HTTP server
        if http_expose:
            resolved_host_port = http_host_port if http_host_port is not None else http_port
            docker_cmd.extend(["-p", f"{resolved_host_port}:{http_port}"])

        # Allow containers to reach host services (e.g. Ollama, LM Studio) via
        # host.docker.internal.  On Linux the gateway alias must be added explicitly;
        # on macOS/Windows it is provided by Docker Desktop automatically.
        docker_cmd.extend(["--add-host", "host.docker.internal:host-gateway"])

        # Forward env vars; force local execution mode inside container
        merged_env = dict(os.environ if env is None else env)
        merged_env["AGENT_EXECUTION_MODE"] = "local"
        merged_env = _point_local_models_at_the_host(merged_env)
        if snapshot_dir:
            # Inside the state dir mount already; only the pointer is needed.
            merged_env[SNAPSHOT_DIR_ENV] = (
                f"{CONTAINER_STATE_DIR}/run_snapshots/{Path(snapshot_dir).name}")
        docker_cmd.extend(_env_flags(merged_env))

        # Extra user-configured docker flags, same live resolution as the image
        if extra:
            docker_cmd.extend(shlex.split(extra))

        docker_cmd.append(image)
        docker_cmd.extend(translated_cmd)

    try:
        result = _run(docker_cmd, timeout=30)
        if result.returncode == 0:
            container_id = (result.stdout or "").strip()
            resolved_host_port = http_host_port if http_host_port is not None else http_port
            out: Dict[str, Any] = {
                "success": True,
                "container_id": container_id,
                "container_name": container_name,
                "image": image,
                "error": None,
                "started_at": _utc_now_iso(),
            }
            if http_expose:
                out["http_host_port"] = resolved_host_port
                out["http_url"] = f"http://localhost:{resolved_host_port}"
            return out
        return {
            "success": False,
            "container_id": None,
            "container_name": container_name,
            "image": image,
            "error": (result.stderr or result.stdout or "docker run failed").strip(),
        }
    except Exception as exc:
        return {
            "success": False,
            "container_id": None,
            "container_name": container_name,
            "image": image,
            "error": str(exc),
        }


def start_node_container(
    node_id: str,
    agent_id: str,
    inner_cmd: List[str],
    workspace: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    http_expose: bool = False,
    http_port: int = 8080,
    http_host_port: Optional[int] = None,
) -> Dict[str, Any]:
    """Start a detached container for a persistent agent node."""
    return start_container(
        container_name=container_name_for_node(node_id),
        agent_id=agent_id,
        cmd=inner_cmd,
        workspace=workspace,
        env=env,
        http_expose=http_expose,
        http_port=http_port,
        http_host_port=http_host_port,
    )


def stop_container(container_name: str, timeout: int = 15) -> bool:
    """Send docker stop to a running container. Returns True if stopped."""
    try:
        result = _run(["docker", "stop", "--time", str(timeout), container_name], timeout=timeout + 10)
        return result.returncode == 0
    except Exception:
        return False


def restart_container(container_name: str, timeout: int = 15) -> bool:
    """Restart a container in place (docker restart). Returns True if successful.

    The container keeps the same ID and name — no new image pull or volume
    remount is needed.  The agent process inside restarts from scratch.
    """
    try:
        result = _run(["docker", "restart", "--time", str(timeout), container_name], timeout=timeout + 30)
        return result.returncode == 0
    except Exception:
        return False


def remove_container(container_name: str) -> bool:
    """Remove a stopped container. Returns True if removed."""
    try:
        result = _run(["docker", "rm", "-f", container_name], timeout=15)
        return result.returncode == 0
    except Exception:
        return False


def get_logs(container_name: str, tail: int = 200) -> str:
    """Return the last *tail* lines of a container's logs."""
    try:
        result = _run(["docker", "logs", "--tail", str(tail), container_name], timeout=15)
        return (result.stdout or "") + (result.stderr or "")
    except Exception as exc:
        return f"[error fetching logs: {exc}]"


def list_containers() -> List[Dict[str, Any]]:
    """Return all agents-hub containers (running and stopped)."""
    # A short timeout, not the five-minute default: this is a read-only listing
    # that callers treat as cheap. An installed CLI with no daemon behind it
    # blocks until the timeout rather than failing, so the default would stall
    # a diagnostic, and the suite, for minutes.
    result = _run([
        "docker", "ps", "-a",
        "--filter", f"label={LABEL_MANAGED}",
        "--format", "{{json .}}",
    ], timeout=15)
    containers: List[Dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            # Docker returns Labels as "key=val,key=val" string, not a dict
            labels_raw = raw.get("Labels", "")
            labels: Dict[str, str] = {}
            if isinstance(labels_raw, dict):
                labels = labels_raw
            elif isinstance(labels_raw, str) and labels_raw:
                for part in labels_raw.split(","):
                    k, sep, v = part.partition("=")
                    if sep:
                        labels[k.strip()] = v.strip()
            containers.append({
                "id": raw.get("ID", ""),
                "name": raw.get("Names", ""),
                "image": raw.get("Image", ""),
                "status": raw.get("Status", ""),
                "state": raw.get("State", ""),
                "created": raw.get("CreatedAt", ""),
                "agent_id": labels.get("agents-hub.agent-id", ""),
            })
        except Exception:
            pass
    return containers


# ── Path translation ───────────────────────────────────────────────────────────

def _translate_paths(cmd: List[str], mappings: dict) -> List[str]:
    """Translate a host command for execution inside the container.

    - First token (interpreter): replaced with "python"
    - Absolute paths matching any key in mappings: replaced with container path

    mappings: {host_path: container_path}, longest-match wins.
    """
    # Sort by length descending so longer (more specific) prefixes match first
    sorted_mappings = sorted(mappings.items(), key=lambda kv: len(kv[0]), reverse=True)
    result: List[str] = []
    for i, part in enumerate(cmd):
        if i == 0:
            result.append("python")
        else:
            translated = part
            for host_path, container_path in sorted_mappings:
                if part == host_path:
                    translated = container_path
                    break
                if part.startswith(host_path + "/") or part.startswith(host_path + os.sep):
                    rel = part[len(host_path):].lstrip("/\\")
                    translated = container_path.rstrip("/") + "/" + rel
                    break
            result.append(translated)
    return result
