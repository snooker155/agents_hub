"""ContainerManager — Docker container lifecycle for agents.

Architecture
------------
            ┌─────────────────────────────┐
            │       Host process           │
            │   (instances.carrier /       │
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


# How long a read-only listing may wait for the daemon. A Docker Desktop whose
# engine is stuck accepts the connection and never answers, so without a short
# limit a listing waits out the five-minute default and the page asking for
# it spins that long.
QUERY_TIMEOUT = 15


class DockerUnavailable(RuntimeError):
    """The Docker CLI is missing, or its daemon did not answer a listing."""


def _query(cmd: List[str]) -> subprocess.CompletedProcess:
    """A read-only docker command with a short limit, failing with
    :class:`DockerUnavailable` when there is no daemon to answer it."""
    try:
        result = _run(cmd, timeout=QUERY_TIMEOUT)
    except FileNotFoundError as exc:
        raise DockerUnavailable("The docker CLI is not installed on this host") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerUnavailable(
            f"Docker did not answer within {QUERY_TIMEOUT} s: the daemon is not responding"
        ) from exc
    if result.returncode != 0 and "daemon" in (result.stderr or "").lower():
        raise DockerUnavailable((result.stderr or "").strip().splitlines()[-1])
    return result


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
#: The hub's secret encryption key (common/config.py ``secret_key``).
_SECRET_KEY_VARS = frozenset({"AGENTS_HUB_SECRET_KEY", "secret_key"})


def container_env(env: Dict[str, str]) -> Dict[str, str]:
    """Return ``env`` minus variables that only make sense on the host.

    Denylist, not allowlist: a run container needs almost everything a local
    subprocess run gets, so this drops the handful of host-only variables
    instead of re-deriving the whole environment a run needs.
    """
    out: Dict[str, str] = {}
    # A run holding host-bound secret placeholders must not also hold the key
    # that decrypts the secrets table: the state dir (and the database in it)
    # is mounted, so with the key the container could read the real values
    # and the egress substitution would protect nothing.
    holds_placeholders = bool(env.get("AGENTS_HUB_SECRET_PLACEHOLDERS"))
    for key, value in env.items():
        if key in _HOST_ONLY_EXACT:
            continue
        if any(key.startswith(p) for p in _HOST_ONLY_PREFIXES):
            continue
        if holds_placeholders and key in _SECRET_KEY_VARS:
            continue
        if key in _STATE_PATH_VARS:
            value = _state_path_in_container(value)
        out[key] = value
    return out


#: Trust-store variables a run routed through the egress proxy gets
#: (environments/secret_egress.py). They name files under the state dir,
#: which a run container has mounted at CONTAINER_STATE_DIR.
_STATE_PATH_VARS = frozenset({"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                              "GIT_SSL_CAINFO", "NODE_EXTRA_CA_CERTS"})


def _state_path_in_container(value: str) -> str:
    """A host path under the state dir as the container sees it; anything
    else unchanged."""
    try:
        rel = Path(value).resolve().relative_to(Path(AGENTS_HUB_ROOT).resolve())
    except (ValueError, OSError):
        return value
    return str(PurePosixPath(CONTAINER_STATE_DIR) / PurePosixPath(*rel.parts))


# ── Network management ────────────────────────────────────────────────────────

def get_or_create_network() -> str:
    """Ensure the agents-hub bridge network exists. Returns network name."""
    result = _run(["docker", "network", "ls", "--filter", f"name=^{NETWORK_NAME}$", "--format", "{{.Name}}"])
    if NETWORK_NAME not in (result.stdout or "").splitlines():
        _run(["docker", "network", "create", "--driver", "bridge", NETWORK_NAME])
    _attach_self_to_network()
    return NETWORK_NAME


EGRESS_NETWORK_NAME = "agents-hub-egress"
EGRESS_GATEWAY_NAME = "agents-hub-egress-gateway"


def ensure_egress_network() -> str:
    """The internal, no-route-out network a fenced container joins instead of
    the ordinary ``agents-hub`` bridge: ``--internal`` means the daemon gives
    it no default route, so a container on it alone can reach nothing but
    another container on the same network — the egress gateway. Created on
    demand, idempotent. See docs/sandboxes.md, "Enforced network policy"."""
    result = _run(["docker", "network", "ls", "--filter", f"name=^{EGRESS_NETWORK_NAME}$",
                   "--format", "{{.Name}}"])
    if EGRESS_NETWORK_NAME not in (result.stdout or "").splitlines():
        _run(["docker", "network", "create", "--internal", "--driver", "bridge", EGRESS_NETWORK_NAME])
    return EGRESS_NETWORK_NAME


def ensure_egress_gateway() -> Optional[str]:
    """Start the egress gateway container, if it is not already running: a
    tiny relay (``socat``, or ``AGENTS_HUB_EGRESS_GATEWAY_IMAGE``) attached to
    both the ordinary bridge (its route out, to the egress proxy on this
    host) and :func:`ensure_egress_network` (so a fenced container can reach
    it, and only it). Returns the gateway's container name — what a fenced
    container's ``HTTP_PROXY``/``HTTPS_PROXY`` should point at, since the
    internal network's embedded DNS resolves container names — or ``None``
    when the egress proxy itself is off or the gateway could not be started
    (docker unavailable, image missing); a caller gets that as "the enforced
    fence is unavailable", never as permission to run unfenced instead.
    """
    try:
        from environments import egress
    except Exception:  # noqa: BLE001 - environments always importable in practice; defensive
        return None
    if not egress.enabled():
        return None
    if container_running(EGRESS_GATEWAY_NAME):
        return EGRESS_GATEWAY_NAME
    proxy_host = egress.public_host("docker")
    proxy_port = egress.port()
    bridge = get_or_create_network()
    ensure_egress_network()
    # A stopped-but-not-removed container from a previous, failed attempt
    # would otherwise make `docker run --name` fail forever.
    _run(["docker", "rm", "-f", EGRESS_GATEWAY_NAME], timeout=15)
    from common.config import live_setting
    image = live_setting("AGENTS_HUB_EGRESS_GATEWAY_IMAGE", "alpine/socat:latest")
    cmd = [
        "docker", "run", "--detach", "--rm",
        "--name", EGRESS_GATEWAY_NAME,
        "--label", LABEL_MANAGED,
        "--label", "agents-hub.container-type=egress-gateway",
        "--network", bridge,
        "--add-host", "host.docker.internal:host-gateway",
        # --entrypoint overrides whatever the image itself declares (the
        # default alpine/socat image's own ENTRYPOINT is already ["socat"],
        # which running "sh -c ..." as its command would fight rather than
        # use); this way any image with a `socat` binary on PATH works,
        # matching AGENTS_HUB_EGRESS_GATEWAY_IMAGE's own promise.
        "--entrypoint", "socat",
        image,
        f"TCP-LISTEN:{proxy_port},fork,reuseaddr",
        f"TCP:{proxy_host}:{proxy_port}",
    ]
    result = _run(cmd, timeout=30)
    if result.returncode != 0:
        logger.warning("could not start the egress gateway: %s", (result.stderr or "").strip())
        return None
    connect = _run(["docker", "network", "connect", EGRESS_NETWORK_NAME, EGRESS_GATEWAY_NAME], timeout=15)
    if connect.returncode != 0 and "already exists in network" not in (connect.stderr or ""):
        logger.warning("could not attach the egress gateway to %s: %s",
                       EGRESS_NETWORK_NAME, (connect.stderr or "").strip())
        _run(["docker", "rm", "-f", EGRESS_GATEWAY_NAME], timeout=15)
        return None
    return EGRESS_GATEWAY_NAME


def _rewrite_proxy_url(url: str, gateway_name: str) -> str:
    """The proxy URL environments/launch.py hands a run points at
    host.docker.internal (or 127.0.0.1): unreachable once the container is
    fenced to the internal network alone. Keep the token, swap the host for
    the gateway's container name."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    netloc = f"{gateway_name}:{parts.port or 80}"
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def enforce_network_policy(
    network: str, env: Optional[Dict[str, str]], extra_env: Optional[Dict[str, str]]
) -> "tuple[str, Optional[Dict[str, str]], Optional[Dict[str, str]]]":
    """Fence a container onto the no-route-out egress network when its launch
    carries a ``none``/``limited`` environment network policy
    (``AGENTS_HUB_NETWORK``, environments/launch.py) and the egress proxy is
    enabled; otherwise return the inputs unchanged — today's behaviour, the
    ordinary ``agents-hub`` bridge, policed only by the proxy and the hub
    tools' own checks, kept when the proxy is off (see the ``sandbox``
    doctor check, common/doctor.py, for whether enforcement is active).

    ``AGENTS_HUB_NETWORK`` may arrive in either dict: a run container's own
    (unfiltered) ``env``, or a node container's ``extra_env`` (its ``env`` is
    allowlist-filtered before this point and would not carry it). Wherever
    the proxy variables are set, they are rewritten to point at the gateway
    instead of the host, since the fenced network has no route to either.
    """
    combined = {**(env or {}), **(extra_env or {})}
    if combined.get("AGENTS_HUB_NETWORK") not in ("none", "limited"):
        return network, env, extra_env
    gateway = ensure_egress_gateway()
    if not gateway:
        return network, env, extra_env
    new_env = dict(env) if env else env
    new_extra = dict(extra_env) if extra_env else extra_env
    for key in _PROXY_ENV_KEYS:
        if new_env is not None and key in new_env:
            new_env[key] = _rewrite_proxy_url(new_env[key], gateway)
        if new_extra is not None and key in new_extra:
            new_extra[key] = _rewrite_proxy_url(new_extra[key], gateway)
    return EGRESS_NETWORK_NAME, new_env, new_extra


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
    includes --http-port so the carrier starts the HTTP server.
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
#   docker build -t {BASE_IMAGE} --target agents .

FROM {BASE_IMAGE}

LABEL agents-hub.managed="true"
LABEL agents-hub.agent-id="{agent_id}"
LABEL agents-hub.agent-name="{label_name}"

# Force local execution inside the container so agents never try
# to spin up further Docker containers.
ENV AGENT_ID="{agent_id}"
ENV AGENT_EXECUTION_MODE="local"
{expose_line}
CMD ["python", "-m", "runtime.instance_run", "--agent-id", "{agent_id}"{http_cmd}]
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

def _with_rag() -> str:
    """``true`` or ``false``: whether the images built here carry the RAG
    stack, from WITH_RAG in .env or the environment (default off)."""
    try:
        from common.config import live_setting
        raw = live_setting("WITH_RAG", "false")
    except Exception:  # noqa: BLE001 - no config module in a stripped-down process; the environment decides
        raw = os.environ.get("WITH_RAG", "false")
    return "true" if str(raw).strip().lower() in ("1", "true", "yes", "on") else "false"


def build_base_image(no_cache: bool = False) -> Dict[str, Any]:
    """Build the unified base image (agents-hub/base:latest): the ``agents``
    target of the repository's Dockerfile.

    Returns a result dict with keys: success, image, log, error.
    """
    cmd = ["docker", "build", "-t", BASE_IMAGE, "--target", "agents"]
    if no_cache:
        cmd.append("--no-cache")
    # The RAG extras (torch, the vector stores) are left out of the image
    # unless WITH_RAG=true, the same switch docker-compose.yml passes to the
    # backend image (Dockerfile, docs/memory.md).
    cmd += ["--build-arg", f"WITH_RAG={_with_rag()}", "."]
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
    except (OSError, subprocess.SubprocessError) as exc:
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
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "success": False, "image": None,
            "dockerfile": str(dockerfile_path),
            "log": "", "error": str(exc), "built_at": None,
        }


def list_images() -> List[Dict[str, Any]]:
    """Return all agents-hub Docker images on the host.

    Raises :class:`DockerUnavailable` when the daemon does not answer.
    """
    result = _query([
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
        except ValueError:
            logger.debug("skipping malformed docker images line: %r", line)
    return images


def image_exists(image_tag: str) -> bool:
    """Check whether a specific image tag exists locally."""
    result = _run(["docker", "images", "-q", image_tag], timeout=QUERY_TIMEOUT)
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


# ── Environment images ────────────────────────────────────────────────────────
#
# An environment (environments/models.py) may list pip packages to install on
# top of its image. Rather than installing them at every start (slow, and a
# run container's root filesystem is read-only anyway), the packages are baked
# once into a derived image whose tag is a hash of the base image and the
# sorted package list: the same environment always maps to the same tag, a
# changed list to a new one, and two environments with the same base and
# packages share a build.

ENV_IMAGE_REPO = "agents-hub-env"


def environment_image_tag(base_image: str, packages: List[str]) -> str:
    """``agents-hub-env:<12 hex>`` for this base image and package set (order
    and duplicates do not matter)."""
    import hashlib
    key = base_image.strip() + "\n" + "\n".join(sorted({p.strip() for p in packages if p.strip()}))
    return f"{ENV_IMAGE_REPO}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


def environment_dockerfile(base_image: str, packages: List[str]) -> str:
    """The derived image's Dockerfile. Package names were validated by
    environments.models (plain requirements, no options or shell syntax) and
    are quoted here as well."""
    pkgs = " ".join(shlex.quote(p) for p in sorted({p.strip() for p in packages if p.strip()}))
    return (
        "# Auto-generated by agents-hub for an environment's packages\n"
        f"FROM {base_image}\n"
        'LABEL agents-hub.managed="true"\n'
        'LABEL agents-hub.image-kind="environment"\n'
        f"RUN pip install --no-cache-dir {pkgs}\n"
    )


def environment_base_image(image: Optional[str] = None) -> str:
    """The image an environment builds on: its own ``image``, else the base
    image when it exists here, else the configured AGENT_DOCKER_IMAGE."""
    if image:
        return image
    try:
        if image_exists(BASE_IMAGE):
            return BASE_IMAGE
    except (OSError, subprocess.SubprocessError):
        pass
    from common.config import live_setting
    return live_setting("AGENT_DOCKER_IMAGE", BASE_IMAGE)


def build_environment_image(base_image: str, packages: List[str], *,
                            force: bool = False) -> Dict[str, Any]:
    """Build (or find) the derived image. Returns ``{ok, image, error, cached}``;
    ``image`` is the derived tag on success and the base image on failure."""
    tag = environment_image_tag(base_image, packages)
    try:
        if not force and image_exists(tag):
            return {"ok": True, "image": tag, "error": None, "cached": True}
    except (OSError, subprocess.SubprocessError):
        pass
    path = DOCKERFILE_DIR / f"env-{tag.split(':', 1)[1]}.Dockerfile"
    try:
        path.write_text(environment_dockerfile(base_image, packages), encoding="utf-8")
        result = _run(["docker", "build", "-t", tag, "-f", str(path), str(DOCKERFILE_DIR)], timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "image": base_image, "error": str(exc), "cached": False}
    if result.returncode != 0:
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        return {"ok": False, "image": base_image,
                "error": out.splitlines()[-1] if out else "docker build failed", "cached": False}
    return {"ok": True, "image": tag, "error": None, "cached": False}


def ensure_environment_image(base_image: str, packages: List[str]) -> str:
    """The image to run for ``base_image`` plus ``packages``: the derived
    image, built on first use and cached by tag. No packages: the base image.
    A failed build logs and falls back to the base image, so a run still
    starts (without the packages) rather than not at all."""
    if not [p for p in packages or [] if str(p).strip()]:
        return base_image
    result = build_environment_image(base_image, list(packages))
    if not result["ok"]:
        logger.warning("environment image build failed on %s (%s); running on the base image",
                       base_image, result["error"])
    return str(result["image"])


def options_to_kwargs(agent_id: str, options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn an environment's docker options into :func:`start_container`
    keyword arguments, resolving the image (building the derived image when
    packages are set). Unknown keys are ignored; an empty dict gives {}."""
    opts = dict(options or {})
    kwargs: Dict[str, Any] = {}
    for key in ("memory", "cpus"):
        if opts.get(key):
            kwargs[key] = str(opts[key])
    if opts.get("pids_limit"):
        try:
            kwargs["pids_limit"] = int(opts["pids_limit"])
        except (TypeError, ValueError):
            pass
    image = str(opts.get("image") or "").strip() or None
    packages = [str(p) for p in (opts.get("packages") or []) if str(p).strip()]
    if packages:
        image = ensure_environment_image(image or environment_base_image(None), packages)
    if image:
        kwargs["image"] = image
    return kwargs


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
      ``network`` is the network name to join; "none" gives the container no
        network at all and drops the host.docker.internal mapping with it
        (no caller passes it today). A "limited"/"none" environment network
        policy is enforced one level up, by ``start_container`` calling
        :func:`enforce_network_policy` before this function ever runs: it
        substitutes the internal, no-route-out network
        (:data:`EGRESS_NETWORK_NAME`) for whatever ``network`` this function
        was given, when the egress proxy is enabled — this function itself
        stays a pure command builder and does not know the difference.
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
    # Pointless without a network (network "none"), so
    # left out there rather than handing docker a mapping it cannot use.
    if network != "none":
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
    except (OSError, subprocess.SubprocessError):
        return False


def container_status(container_name: str) -> Optional[str]:
    """The daemon's state for a container (``running``, ``exited``,
    ``paused``...), or None when there is no such container (a run's
    container is started with ``--rm``, so a finished run's is simply gone)."""
    try:
        result = _run(["docker", "inspect", "--format", "{{.State.Status}}", container_name],
                      timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


#: The shell a terminal opens: bash when the image has it, else sh. A login
#: shell, so the image's profile (PATH, prompt) applies as it would over ssh.
TERMINAL_SHELL = "if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"


def exec_shell_argv(container_name: str) -> List[str]:
    """``docker exec -it`` into a container's shell, for common/terminal.py.

    Run with a pseudo-terminal as its stdin and stdout: the CLI then puts
    that terminal in raw mode, forwards every byte (Ctrl-C included) to the
    container's own pty, and resizes it when it gets SIGWINCH. Nothing
    else about the container changes: same user, same working directory,
    same read-only root and dropped capabilities a hardened run has.
    """
    return ["docker", "exec", "-it", "-e", "TERM=xterm-256color",
            container_name, "/bin/sh", "-c", TERMINAL_SHELL]


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
    image: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
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

    An environment (environments/launch.py, docs/environments.md) may shape
    either kind through the keyword arguments: ``image`` replaces the agent's
    image (already resolved, including a derived image with extra packages,
    see :func:`options_to_kwargs`), ``memory`` /
    ``cpus`` / ``pids_limit`` set limits (a node container only gets the ones
    given; a run container keeps its defaults for the rest), and
    ``extra_env`` forwards variables a node container's provider-key
    allowlist would otherwise drop (the environment's own variables, the
    allowlist and proxy settings).

    Returns: {success, container_id, container_name, image, error,
              http_url (if http_expose)}
    """
    network = get_or_create_network()
    image = image or image_tag_for_agent(agent_id)

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
        run_env = {**(env or {}), **(extra_env or {})} if extra_env else env
        network, run_env, _ = enforce_network_policy(network, run_env, None)
        docker_cmd = build_run_command(
            container_name=container_name,
            agent_id=agent_id,
            image=image,
            network=network,
            translated_cmd=translated_cmd,
            state_dir=state_dir,
            tasks_dir=tasks_dir,
            workspace=workspace,
            env=run_env,
            extra_args=extra,
            memory=memory,
            cpus=cpus,
            pids_limit=pids_limit,
            snapshot_dir=snapshot_dir,
        )
    else:
        network, env, extra_env = enforce_network_policy(network, env, extra_env)
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
        # Limits only when an environment asks for them: a node container has
        # none by default, unchanged from before environments existed.
        if memory:
            docker_cmd += ["--memory", str(memory)]
        if cpus:
            docker_cmd += ["--cpus", str(cpus)]
        if pids_limit:
            docker_cmd += ["--pids-limit", str(pids_limit)]

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
        for key, value in (extra_env or {}).items():
            if value and "DOCKER" not in key:
                docker_cmd.extend(["-e", f"{key}={value}"])

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
    except (OSError, subprocess.SubprocessError) as exc:
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
    options: Optional[Dict[str, Any]] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Start a detached container for a persistent agent node.

    ``options`` is the docker profile of the node's environment
    (environments/launch.py: network, limits, image, packages) and
    ``extra_env`` the variables it adds; both absent keeps the call exactly as
    it was before environments existed."""
    kwargs: Dict[str, Any] = options_to_kwargs(agent_id, options) if options else {}
    if extra_env:
        kwargs["extra_env"] = dict(extra_env)
    return start_container(
        container_name=container_name_for_node(node_id),
        agent_id=agent_id,
        cmd=inner_cmd,
        workspace=workspace,
        env=env,
        http_expose=http_expose,
        http_port=http_port,
        http_host_port=http_host_port,
        **kwargs,
    )


def stop_container(container_name: str, timeout: int = 15) -> bool:
    """Send docker stop to a running container. Returns True if stopped."""
    try:
        result = _run(["docker", "stop", "--time", str(timeout), container_name], timeout=timeout + 10)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def restart_container(container_name: str, timeout: int = 15) -> bool:
    """Restart a container in place (docker restart). Returns True if successful.

    The container keeps the same ID and name — no new image pull or volume
    remount is needed.  The agent process inside restarts from scratch.
    """
    try:
        result = _run(["docker", "restart", "--time", str(timeout), container_name], timeout=timeout + 30)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def remove_container(container_name: str) -> bool:
    """Remove a stopped container. Returns True if removed."""
    try:
        result = _run(["docker", "rm", "-f", container_name], timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def get_logs(container_name: str, tail: int = 200) -> str:
    """Return the last *tail* lines of a container's logs."""
    try:
        result = _run(["docker", "logs", "--tail", str(tail), container_name], timeout=15)
        return (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"[error fetching logs: {exc}]"


def list_containers() -> List[Dict[str, Any]]:
    """Every agents-hub container: this host's daemon's view, plus what other
    hosts registered in the ``containers`` table (``host`` says where)."""
    import socket
    local = _list_local_containers()
    host = socket.gethostname()
    for c in local:
        c["host"] = host
    seen = {c["name"] for c in local}
    for rec in registered_containers():
        name = str(rec.get("name") or "")
        if not name or name in seen:
            continue
        if str(rec.get("host") or "") == host:
            # Registered by this host but gone from its daemon: stale.
            continue
        local.append({
            "id": "", "name": name, "image": rec.get("image") or "",
            "status": rec.get("status") or "", "state": rec.get("status") or "",
            "created": rec.get("started_at") or "", "agent_id": rec.get("agent_id") or "",
            "host": rec.get("host") or "", "remote": True,
        })
    return local


def _list_local_containers() -> List[Dict[str, Any]]:
    """Return all agents-hub containers this host's daemon knows (running and stopped)."""
    # A short timeout, not the five-minute default: this is a read-only listing
    # that callers treat as cheap. An installed CLI with no daemon behind it
    # blocks until the timeout rather than failing, so the default would stall
    # a diagnostic, and the suite, for minutes.
    result = _query([
        "docker", "ps", "-a",
        "--filter", f"label={LABEL_MANAGED}",
        "--format", "{{json .}}",
    ])
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
        except ValueError:
            logger.debug("skipping malformed docker ps line: %r", line)
    return containers


# ── The containers table ──────────────────────────────────────────────────────
#
# ``docker ps`` only knows the daemon this process talks to. With workers on
# several hosts each starting containers, the Containers page needs a record
# that every host writes to: whoever starts a container registers it here
# and refreshes its status while it runs (managers/run_watchdog.py sweeps
# rows whose host stopped reporting). ``list_containers`` merges the local
# daemon's view with the table, local rows winning.

def register_container(name: str, *, kind: str, agent_id: str = "",
                       run_id: Optional[str] = None, node_id: Optional[str] = None,
                       image: Optional[str] = None, status: str = "running",
                       extra: Optional[Dict[str, Any]] = None) -> None:
    """Record a container this host started. Never raises."""
    import socket
    from common import db
    try:
        now = _utc_now_iso()
        with db.transaction() as conn:
            conn.execute(
                db.upsert_sql("containers", ("name", "host", "kind", "agent_id", "run_id", "node_id",
                                             "image", "status", "started_at", "updated_at", "extra"),
                              ("name",)),
                (name, socket.gethostname(), kind, agent_id or "", run_id, node_id, image or "",
                 status, now, now, db.dumps(extra or {})))
    except Exception:  # noqa: BLE001 - never raises (see docstring)
        logger.debug("register_container failed for %s", name, exc_info=True)


def update_container_status(name: str, status: str) -> None:
    from common import db
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE containers SET status = ?, updated_at = ? WHERE name = ?",
                         (status, _utc_now_iso(), name))
    except Exception:  # noqa: BLE001 - best-effort cross-host bookkeeping, must not break the caller
        logger.debug("update_container_status failed for %s", name, exc_info=True)


def forget_container(name: str) -> None:
    from common import db
    try:
        with db.transaction() as conn:
            conn.execute("DELETE FROM containers WHERE name = ?", (name,))
    except Exception:  # noqa: BLE001 - best-effort cross-host bookkeeping, must not break the caller
        logger.debug("forget_container failed for %s", name, exc_info=True)


def registered_containers() -> List[Dict[str, Any]]:
    """Every container any host registered, newest first."""
    from common import db
    try:
        rows = db.get_conn().execute(
            "SELECT * FROM containers ORDER BY started_at DESC LIMIT 500").fetchall()
    except Exception:  # noqa: BLE001 - falls back to no registered containers rather than breaking the caller
        logger.debug("registered_containers query failed", exc_info=True)
        return []
    out = []
    for row in rows:
        rec = dict(row)
        rec["extra"] = db.loads(rec.get("extra"), {}) or {}
        out.append(rec)
    return out


def refresh_registered_containers() -> int:
    """Reconcile this host's rows with its daemon: mark exited ones, drop the
    ones the daemon no longer has. Returns how many rows changed."""
    import socket
    host = socket.gethostname()
    mine = [c for c in registered_containers() if str(c.get("host") or "") == host]
    if not mine:
        return 0
    try:
        local = {c["name"]: c for c in _list_local_containers()}
    except Exception:  # noqa: BLE001 - a daemon that cannot be reached means nothing to reconcile this pass
        logger.debug("_list_local_containers failed during reconcile", exc_info=True)
        return 0
    changed = 0
    for rec in mine:
        name = str(rec["name"])
        seen = local.get(name)
        if seen is None:
            forget_container(name)
            changed += 1
            continue
        state = str(seen.get("state") or "").lower() or "running"
        if state != str(rec.get("status") or ""):
            update_container_status(name, state)
            changed += 1
    return changed


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
