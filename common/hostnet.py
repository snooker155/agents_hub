"""
Reaching a service on the Docker host from inside a container.

`localhost` means something different on either side of a container boundary:
to a containerized process it is that container, not the machine running it. A
local model server — Ollama, LM Studio, vLLM, llama.cpp — listens on the host,
so every URL that names a loopback address has to be rewritten to the gateway
alias before a container can use it.

Two directions, and they are not the same:

* :func:`to_host_gateway` rewrites unconditionally. The caller is on the host,
  preparing an environment *for* a container it is about to start.
* :func:`host_service_url` rewrites only when this process is itself inside a
  container. The caller is resolving a URL to use right now, and on a host-run
  backend `localhost` is already correct and must be left alone.

Neither can help if the server on the other side only listens on loopback:
Ollama needs OLLAMA_HOST=0.0.0.0, LM Studio its local-network switch.
"""
import os
from pathlib import Path

# The alias Docker Desktop provides, and that Linux gets via
# `--add-host host.docker.internal:host-gateway`.
HOST_GATEWAY = "host.docker.internal"

# Written with the scheme separator so only the host part matches: a path or a
# query string that happens to contain "localhost" is left alone.
_LOOPBACK = ("://localhost", "://127.0.0.1", "://0.0.0.0", "://[::1]")


def to_host_gateway(url: str) -> str:
    """Point a loopback URL at the Docker host. Rewrites unconditionally."""
    if not url:
        return url
    for loopback in _LOOPBACK:
        url = url.replace(loopback, f"://{HOST_GATEWAY}")
    return url


def in_container() -> bool:
    """Whether this process is running inside a container.

    Checked per call, not cached: tests set it, and so does a process restarted
    with different env. AGENTS_HUB_IN_CONTAINER overrides the detection in both
    directions, for the setups the markers below do not cover.
    """
    override = os.environ.get("AGENTS_HUB_IN_CONTAINER", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if override in {"0", "false", "no"}:
        return False
    # HOST_PROJECT_ROOT is set by our own compose file; the two marker files are
    # what Docker and Podman leave in place.
    if os.environ.get("HOST_PROJECT_ROOT", "").strip():
        return True
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def host_service_url(url: str) -> str:
    """Rewrite a loopback URL, but only when we are the one in the container.

    Use on any address that is expected to name a service on the machine rather
    than a peer inside the container network. On a host-run backend this is a
    no-op, which is what keeps the same configuration working in both places.
    """
    return to_host_gateway(url) if in_container() else url


def hub_base_url() -> str:
    """The hub's base URL for a process calling back into it (relays, run
    state, a chat replica's turn events): ``AGENTS_HUB_CALLBACK_URL`` when
    set, else ``localhost`` on the API port, rewritten to the host gateway
    alias when this process is itself containerized.

    The override is for the ``ha`` compose profile: ``backend-api`` publishes
    no port on the host, so the gateway alias reaches nothing there, and a
    worker has no API of its own to reach on ``localhost``; both name
    ``http://backend-api:8000`` instead. Run containers are not on the compose
    network and never get it (managers/container_manager.py)."""
    import os
    explicit = (os.environ.get("AGENTS_HUB_CALLBACK_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("DASHBOARD_PORT", "8000")
    return host_service_url(f"http://localhost:{port}").rstrip("/")


def state_base_url() -> str:
    """Where an agent container's state calls go (``/api/run-state/...``: its
    store calls, its run records, its model calls, its wait for mail):
    ``AGENTS_HUB_STATE_URL``, the process that launched it (a worker, which
    has the database and the keys, so the API serves people), else the hub
    (:func:`hub_base_url`). Live events still go to the hub."""
    import os
    explicit = (os.environ.get("AGENTS_HUB_STATE_URL") or "").strip()
    return explicit.rstrip("/") if explicit else hub_base_url()
