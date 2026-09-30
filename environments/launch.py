"""What an environment adds to a launch.

Two callers, one shape. agents/agent_launcher.py calls :func:`launch_fields`
for a task run while it prepares the launch (``_launch_extras``);
instances/carrier.py calls :func:`fields_for` when it starts a resident
instance. Both get::

    {
      "env": {...},                 # variables for the child process
      "docker": {...} | None,       # docker options, only the keys that are set
      "environment_id": "...",
      "environment_name": "...",
      "execution_mode": "local" | "docker" | None,   # None = the workspace's
    }

or ``{}`` when no environment applies. Everything in it is plain strings and
JSON, because a prepared launch travels through the run queue to a worker that
may be on another host.

``sandbox_provider`` (docs/sandboxes.md) is deliberately not part of this
shape: it only governs where a ``run_code`` call or a Chat code-panel snippet
runs, not the agent process itself, and ``tools/run_code.py`` reads it
straight from ``AGENTS_HUB_ENVIRONMENT_ID`` (already set below) through
``environments.service.get_environment`` rather than through a launch field.

``env``:
  - the environment's own variables;
  - ``AGENTS_HUB_ENVIRONMENT_ID`` / ``AGENTS_HUB_ENVIRONMENT_NAME``;
  - network ``limited``: ``AGENTS_HUB_NETWORK=limited`` and
    ``AGENTS_HUB_ALLOWED_HOSTS`` (comma list the hub's tools enforce), and
    when the egress proxy is enabled (environments/egress.py) the proxy
    variables pointing at it with a token for this launch, and ``NO_PROXY``
    for loopback and the docker host;
  - network ``none``: the same as limited with an empty list, marked
    ``AGENTS_HUB_NETWORK=none``: the hub's web and browser tools refuse every
    host, and the proxy (when enabled) lets through only the model providers
    and the hub's own services (egress.infrastructure_hosts), so an LLM agent
    can still reach its model. No docker ``--network none``.

``docker`` (read by runtime/docker_runner.py and
managers/container_manager.py): ``memory``, ``cpus``, ``pids_limit``,
``image``, ``packages``. There is no network key: every network type keeps
the container on the agents-hub bridge and relies on the proxy and the hub
tools' own checks.

``execution_mode``: the environment's mode unless it is ``inherit``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .models import Environment

log = logging.getLogger(__name__)

NO_PROXY = "localhost,127.0.0.1,host.docker.internal"


def _workspace_mode(ws_name: Optional[str]) -> str:
    """The execution mode a run in ``ws_name`` gets without an environment:
    the workspace's own setting, else the global one (same order as the
    launcher and instances.carrier)."""
    mode: Optional[str] = None
    if ws_name:
        try:
            from workspace import get_workspace_metadata
            mode = (get_workspace_metadata(ws_name).get("settings") or {}).get("agent_mode") or None
        except Exception:  # noqa: BLE001 - falls back to the global mode
            mode = None
    if mode not in ("local", "docker"):
        from common.config import agent_execution_mode
        mode = agent_execution_mode()
    return mode


def effective_mode(env: Environment, ws_name: Optional[str]) -> str:
    return env.mode if env.mode in ("local", "docker") else _workspace_mode(ws_name)


def docker_options(env: Environment) -> Optional[Dict[str, Any]]:
    """The docker profile of ``env``: only the keys it sets, None when none."""
    opts: Dict[str, Any] = {}
    if env.limits.memory:
        opts["memory"] = env.limits.memory
    if env.limits.cpus:
        opts["cpus"] = env.limits.cpus
    if env.limits.pids_limit:
        opts["pids_limit"] = int(env.limits.pids_limit)
    if env.image:
        opts["image"] = env.image
    if env.packages:
        opts["packages"] = list(env.packages)
    return opts or None


def fields_for(env: Optional[Environment], ws_name: Optional[str], *,
               owner: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The launch fields for ``env`` (see the module docstring)."""
    if env is None:
        return {}
    mode = effective_mode(env, ws_name)
    child_env: Dict[str, str] = dict(env.env)
    child_env["AGENTS_HUB_ENVIRONMENT_ID"] = env.id
    child_env["AGENTS_HUB_ENVIRONMENT_NAME"] = env.name
    net = env.network.type
    if net in ("none", "limited"):
        # "none" is "limited" with nothing on the list: the proxy then passes
        # only the model providers and the hub's services.
        hosts = env.network.effective_hosts() if net == "limited" else []
        child_env["AGENTS_HUB_NETWORK"] = net
        if net == "limited":
            child_env["AGENTS_HUB_ALLOWED_HOSTS"] = ",".join(hosts)
        try:
            from . import egress
            if egress.enabled():
                token = egress.register(hosts, environment_id=env.id, owner=owner)
                url = egress.proxy_url(token, mode)
                for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                    child_env[key] = url
                child_env["NO_PROXY"] = NO_PROXY
                child_env["no_proxy"] = NO_PROXY
        except Exception:  # noqa: BLE001 - the hub's own tools still enforce the list without the proxy
            log.warning("egress proxy registration failed for environment %s", env.id, exc_info=True)
    return {
        "env": child_env,
        "docker": docker_options(env),
        "environment_id": env.id,
        "environment_name": env.name,
        "execution_mode": env.mode if env.mode in ("local", "docker") else None,
    }


def launch_fields(task: Any, ws_name: Optional[str]) -> Dict[str, Any]:
    """The environment's contribution to a task run's launch.

    The task's own ``environment_id`` wins (an archived one still applies: the
    task was created with it and its fence must hold), else the workspace's
    default, else the global default; ``{}`` when none applies. An id that no
    longer exists falls back to the defaults with a warning rather than
    refusing the run, the same fail-open rule the launcher applies to every
    addition.
    """
    from . import service
    env_id = getattr(task, "environment_id", None) or None
    env: Optional[Environment] = None
    if env_id:
        env = service.get_environment(str(env_id))
        if env is None:
            log.warning("task %s names environment %s which does not exist; using the default",
                        getattr(task, "id", None), env_id)
    if env is None:
        env = service.default_for(ws_name)
    owner = {"kind": "task", "task_id": str(getattr(task, "id", "") or "")}
    return fields_for(env, ws_name, owner=owner)


__all__ = ["launch_fields", "fields_for", "docker_options", "effective_mode", "NO_PROXY"]
