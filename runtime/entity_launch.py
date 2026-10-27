"""
The one launch envelope for the runs of flows, loops and teams.

An agent's task run has this shape (agents/agent_launcher.py): a *database
half* prepares the run (its record, session, log path) and builds a plain
JSON spec; a *process half* turns the spec into a process. Flows had their
own copy of that shape; teams ran on daemon threads of the API
process with no sandbox, no heartbeat, and stayed ``running`` forever after a
restart.

This module is the process half for every entity kind. A launcher
(flow/launcher.py, loops/launcher.py, teams/launcher.py) prepares its
record in ``entity_runs`` (common/entity_runs.py) and calls :func:`dispatch`
with a spec; from there the run is spawned, locally or in a container,
exactly the way a task run is.

The spec, plain JSON and nothing secret::

    {
      "kind": "team",                 # flow | loop | team
      "run_id": "...",                # the entity_runs key
      "entity_id": "...",             # flow_id, loop_id, team_id
      "entrypoint": "team_run",       # runtime/<entrypoint>.py, -m runtime.<entrypoint>
      "cli_args": [...],              # the entrypoint's own arguments
      "workspace": "...",             # workspace name
      "ws_path": "...",               # directory the process runs in
      "task_id": "...", "session_id": "...",
      "log_file": "...", "header": "Team run started", "mode": "w" | "a",
      "execution_mode": "local" | "docker",
      "agent_id": "...",              # optional, whose secrets the env carries
      "launched_by": "...",           # user id, for user-scoped secrets
      "resume": false
    }

The environment is rebuilt here from this process's own configuration
(provider keys, the API token, declared secrets). A launched process records its pid (or container) and host on the
run and beats its heartbeat from then on; the watchdog judges it by that beat
and never by a pid on another host.

Docker applies to a team, a flow and a loop by the same rule as to an
agent: ``execution_mode`` on the spec, decided by the launcher from the
workspace's agent execution mode, and the same hardened run container
(runtime/docker_runner.py) with the same state transport. Their agent nodes
run in-process, so the whole run shares that one container and its image
(the base image: there is no per-agent image for a flow); a nested team,
loop or flow it starts runs as a subprocess inside the same container.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from common.paths import PROJECT_ROOT

log = logging.getLogger("runtime.entity_launch")

#: Kind -> the module under ``runtime/`` that runs it.
ENTRYPOINTS: Dict[str, str] = {
    "flow": "flow_run",
    "loop": "loop_run",
    "team": "team_run",
}

def utc_now_iso() -> str:
    from common.entity_runs import utc_now_iso as _now
    return _now()


# ── Dispatch ─────────────────────────────────────────────────────────────────

#: Extra environment for every launch dispatched inside :func:`child_env`.
_CHILD_ENV: ContextVar[Optional[Dict[str, str]]] = ContextVar("entity_launch_child_env", default=None)


@contextmanager
def child_env(extra: Mapping[str, Any]) -> Iterator[None]:
    """Give every launch dispatched inside this block extra environment
    variables. A caller that launches through another kind's launcher (a flow
    node starting a team) cannot reach the spec that launcher builds, so the
    values ride on a context variable and :func:`dispatch` copies them into the
    spec's ``env`` key."""
    merged = {**(_CHILD_ENV.get() or {}), **{str(k): str(v) for k, v in extra.items()}}
    token = _CHILD_ENV.set(merged)
    try:
        yield
    finally:
        _CHILD_ENV.reset(token)


def dispatch(spec: Dict[str, Any], launch: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    """Spawn the run here. ``launch`` is the kind's own ``launch_prepared``
    (defaults to the generic one here)."""
    from common.attribution import launching_user

    kind = str(spec.get("kind") or "")
    if kind not in ENTRYPOINTS:
        raise ValueError(f"unknown entity launch kind {kind!r}")
    # Who asked for this run, so a spawn with no request in flight can still
    # resolve user-scoped secrets.
    spec.setdefault("launched_by", launching_user())
    spec.setdefault("execution_mode", "local")
    extra_env = _CHILD_ENV.get()
    if extra_env:
        spec["env"] = {**extra_env, **(spec.get("env") or {})}
    (launch or launch_prepared)(spec)


# ── The process half ─────────────────────────────────────────────────────────

def execution_mode_for(workspace: Optional[str]) -> str:
    """``docker`` when the workspace (or the installation) runs agents in
    containers, else ``local``: the same rule agent_launcher.prepare_run
    applies to a task run (common.config.agent_execution_mode), except where a
    container must not open the database (Docker Desktop with SQLite, see
    managers.container_manager.db_shared_with_containers): a team, flow or
    loop process opens it itself, so there it runs on the hub."""
    from common.config import run_execution_mode
    return run_execution_mode(workspace)


def build_env(spec: Dict[str, Any]) -> Dict[str, str]:
    """The child's environment: the base every agent-running subprocess gets
    (workspace, keys, the API token, declared secrets) plus the run's own
    session, log file and id."""
    from common.subprocess_env import add_run_env, base_subprocess_env

    kind = str(spec.get("kind") or "")
    ws_name = str(spec.get("workspace") or "")
    env = base_subprocess_env(
        ws_name,
        agent_id=str(spec.get("agent_id") or "") or None,
        flow_id=str(spec.get("entity_id") or "") if kind == "flow" else None,
        user_id=str(spec.get("launched_by") or "") or None,
        key_id=str(spec.get("key_id") or "") or None,
        run_id=str(spec.get("run_id") or "") or None,
        session_id=str(spec.get("session_id") or "") or None,
    )
    add_run_env(env, session_id=str(spec.get("session_id") or "") or None,
                log_file=str(spec.get("log_file") or "") or None)
    env["AGENTS_HUB_ENTITY_RUN_ID"] = str(spec["run_id"])
    env["AGENTS_HUB_ENTITY_RUN_KIND"] = kind
    # Optional extra variables a launcher put on the spec (see child_env).
    extra = spec.get("env")
    if isinstance(extra, dict):
        for key, value in extra.items():
            env[str(key)] = str(value)
    return env


def _argv(spec: Dict[str, Any], *, in_container: bool) -> list:
    entry = ENTRYPOINTS[str(spec["kind"])]
    cli_args = [str(a) for a in (spec.get("cli_args") or [])]
    if in_container:
        # The module form, so the container's path translation only ever sees
        # arguments, never the interpreter line (same as a docker task run).
        return [sys.executable, "-m", f"runtime.{entry}"] + cli_args + ["--write-stdout-to-log"]
    return [sys.executable, str(PROJECT_ROOT / "runtime" / f"{entry}.py")] + cli_args


def _write_header(spec: Dict[str, Any], argv: list, *, where: str) -> None:
    log_file = Path(str(spec["log_file"]))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if str(spec.get("mode") or "w") == "a" else "w"
    with open(log_file, mode, encoding="utf-8") as lf:
        lf.write(
            f"--- {spec.get('header') or 'Run started'} at {utc_now_iso()} ---\n"
            f"Kind    : {spec.get('kind')}\n"
            f"Entity  : {spec.get('entity_id')}\n"
            f"Command{where}: {argv}\n"
            f"CWD     : {spec.get('ws_path') or PROJECT_ROOT}\n"
            f"Host    : {socket.gethostname()}\n\n"
        )


def spawn_local(spec: Dict[str, Any], env: Dict[str, str]) -> int:
    """Start the entrypoint detached (its own session) with stdout and stderr
    appended to the run's log file. Returns the pid."""
    argv = _argv(spec, in_container=False)
    _write_header(spec, argv, where=" ")
    cwd = str(spec.get("ws_path") or PROJECT_ROOT)
    if not Path(cwd).is_dir():
        cwd = str(PROJECT_ROOT)
    with open(str(spec["log_file"]), "a", encoding="utf-8") as lf:
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True
        proc = subprocess.Popen(
            argv,
            start_new_session=start_new_session,
            creationflags=creationflags,
            cwd=cwd,
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )
    return proc.pid


def spawn_docker(spec: Dict[str, Any], env: Dict[str, str]) -> Dict[str, Any]:
    """Start the entrypoint in a hardened run container (the same profile a
    docker task run gets) and return the docker_runner result, which carries
    ``container_name`` on success and ``error`` on failure."""
    from managers.container_manager import CONTAINER_STATE_DIR, container_state_transport
    from runtime import docker_runner

    argv = _argv(spec, in_container=True)
    _write_header(spec, argv, where=" (in container)")
    docker_env = dict(env)
    docker_env["AGENT_LOG_FILE"] = f"{CONTAINER_STATE_DIR}/run_logs/{Path(str(spec['log_file'])).name}"
    docker_env["AGENT_RUN_STATE_TRANSPORT"] = container_state_transport()
    agent_id = str(spec.get("agent_id") or f"{spec.get('kind')}:{spec.get('entity_id')}")
    return docker_runner.start_run_container(
        str(spec["run_id"]), agent_id, argv,
        cwd=_mount_dir(spec.get("ws_path")), env=docker_env,
    )


def _mount_dir(ws_path: Any) -> Optional[str]:
    """The folder a run container gets at /workspace: the run's workspace
    folder, or nothing. Never the project root or a folder above it (what a
    run with no workspace would otherwise fall back to), since that would
    hand the container the hub's own code and state."""
    if not ws_path:
        return None
    path = Path(str(ws_path)).resolve()
    root = Path(PROJECT_ROOT).resolve()
    if path == root or path in root.parents or not path.is_dir():
        return None
    return str(path)


def _container_may_run(spec: Dict[str, Any]) -> bool:
    """Whether this entity run may go into a container. Its process reads and
    writes its records in the database directly, so only where a container
    can share the database (managers.container_manager.db_shared_with_containers)."""
    from managers.container_manager import db_shared_with_containers
    return db_shared_with_containers()


def launch_prepared(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The process half of an entity launch: spawn the entrypoint on this
    host, locally or in a container, and record on the run where it lives.

    Returns the fields written on the run (``pid`` or ``container_name``,
    ``host``, ``execution_mode``). A container that fails to start closes
    the run as failed and raises; the operator chose Docker, so this never falls back to a local
    subprocess.
    """
    from common import entity_runs

    run_id = str(spec["run_id"])
    env = build_env(spec)
    mode = str(spec.get("execution_mode") or "local")
    host = socket.gethostname()
    if mode == "docker" and not _container_may_run(spec):
        # A team, flow or loop process opens the database itself (it has no
        # http relay), which a container must not do here: it runs on the hub.
        mode = "local"
        spec["header"] = (f"{spec.get('header') or 'Run started'} on the hub: docker mode runs "
                          "teams, flows and loops in a container only where the container can "
                          "share the database (a Linux host with local state)")

    if mode == "docker":
        result = spawn_docker(spec, env)
        if not result.get("ok", True) or result.get("error"):
            error = str(result.get("error") or "container failed to start")
            entity_runs.close(run_id, status="failed", exit_code=1, error=error)
            raise RuntimeError(error)
        container_name = str(result.get("container_name") or "")
        entity_runs.mark_running(run_id, host=host, container_name=container_name,
                                 execution_mode="docker")
        return {"container_name": container_name, "host": host, "execution_mode": "docker"}

    pid = spawn_local(spec, env)
    entity_runs.mark_running(run_id, pid=pid, host=host, execution_mode="local")
    return {"pid": pid, "host": host, "execution_mode": "local"}


def child_alive(rec: Dict[str, Any]) -> Optional[bool]:
    """Whether the process spawned for an entity run still runs on this host.
    None when the record does not say (no pid, no container, or another
    host)."""
    from common.run_status import ACTIVE_STATUSES
    if str(rec.get("status") or "") not in ACTIVE_STATUSES:
        return False
    host = str(rec.get("host") or "")
    if host and host != socket.gethostname():
        return None
    container = rec.get("container_name")
    if container:
        from managers.container_manager import container_running
        return container_running(str(container))
    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        return None
    from managers.runs.lifecycle import _pid_exists
    return _pid_exists(pid)


__all__ = [
    "ENTRYPOINTS", "dispatch", "execution_mode_for",
    "build_env", "child_env", "spawn_local", "spawn_docker", "launch_prepared", "child_alive",
]
