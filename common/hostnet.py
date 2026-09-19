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
