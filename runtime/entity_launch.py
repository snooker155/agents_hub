"""
The one launch envelope for the runs of flows, teams and scenarios.

An agent's task run has had this shape since stage 2 of the scaling plan
(agents/agent_launcher.py): a *database half* prepares the run (its record,
session, log path) and builds a plain JSON spec; a *process half* turns the
spec into a process on whichever host does the spawning, here in the default
role or on a worker in the ``api`` role (docs/workers.md). Flows had their
own copy of that shape; teams and scenarios ran on daemon threads of the API
process with no sandbox, no heartbeat, no place in the queue, and stayed
``running`` forever after a restart.

This module is the process half for every entity kind. A launcher
(flow/launcher.py, teams/launcher.py, playground/launcher.py) prepares its
record in ``entity_runs`` (common/entity_runs.py) and calls :func:`dispatch`
with a spec; from there the run is queued or spawned, locally or in a
container, exactly the way a task run is.

The spec, plain JSON and nothing secret::

    {
      "kind": "team",                 # flow | team | scenario
      "run_id": "...",                # the entity_runs key
      "entity_id": "...",             # flow_id, team_id, scenario_id
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
(provider keys, the API token, declared secrets) and never travels through
the queue. A launched process records its pid (or container) and host on the
run and beats its heartbeat from then on; the watchdog judges it by that beat
and never by a pid on another host.

Docker applies to a team or a scenario by the same rule as to an agent:
``execution_mode`` on the spec, decided by the launcher from the workspace's
agent execution mode, and the same hardened run container
(runtime/docker_runner.py) with the same state transport. Flows always run as
a local subprocess: their nodes execute agents in-process, and the flow
process is the orchestrator, not the sandbox.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from common.paths import PROJECT_ROOT

log = logging.getLogger("runtime.entity_launch")

#: Kind -> the module under ``runtime/`` that runs it.
ENTRYPOINTS: Dict[str, str] = {
    "flow": "flow_run",
    "team": "team_run",
    "scenario": "scenario_run",
}

#: Kind -> the launcher module whose ``launch_prepared`` the worker calls. A
#: launcher wraps :func:`launch_prepared` here with its kind's own after-spawn
#: bookkeeping (a flow flips its definition's running marker, say).
LAUNCHERS: Dict[str, str] = {
    "flow": "flow.launcher",
    "team": "teams.launcher",
    "scenario": "playground.launcher",
}


def utc_now_iso() -> str:
    from common.entity_runs import utc_now_iso as _now
    return _now()


# ── Dispatch ─────────────────────────────────────────────────────────────────

def dispatch(spec: Dict[str, Any], launch: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    """Spawn here, or hand the spec to a worker, by this process's role.

    ``launch`` is the kind's own ``launch_prepared`` (defaults to the generic
    one here); in the ``api`` role it is not called at all, the worker calls
    the launcher named in :data:`LAUNCHERS` for the spec's kind.
    """
    from common.config import hub_role
    from common.identity import current_user_id

    kind = str(spec.get("kind") or "")
    if kind not in ENTRYPOINTS:
        raise ValueError(f"unknown entity launch kind {kind!r}")
    # Who asked for this run, so the process that spawns it (maybe a worker
    # with no request in flight) can resolve user-scoped secrets.
    spec.setdefault("launched_by", current_user_id())
    spec.setdefault("execution_mode", "local")
    if hub_role() == "api":
        from common import run_queue
        run_queue.enqueue(str(spec["run_id"]), kind, spec, workspace=spec.get("workspace"),
                          execution_mode=str(spec.get("execution_mode") or "local"))
        return
    (launch or launch_prepared)(spec)


def launcher_for(kind: str) -> Callable[[Dict[str, Any]], None]:
    """The ``launch_prepared`` of a kind's launcher module, for the worker."""
    import importlib
    module_name = LAUNCHERS.get(str(kind))
    if not module_name:
        raise ValueError(f"unknown launch kind {kind!r}")
    return getattr(importlib.import_module(module_name), "launch_prepared")


# ── The process half ─────────────────────────────────────────────────────────

def execution_mode_for(workspace: Optional[str]) -> str:
    """``docker`` when the workspace (or the installation) runs agents in
    containers, else ``local``: the same rule agent_launcher.prepare_run
    applies to a task run."""
    from common.config import agent_execution_mode
    ws_mode = None
    if workspace:
        try:
            from workspace import get_workspace_metadata
            ws_mode = (get_workspace_metadata(workspace).get("orchestrator") or {}).get("agent_execution_mode")
        except Exception:  # noqa: BLE001 - an unreadable workspace falls back to the global setting
            log.debug("workspace execution mode lookup failed for %s", workspace, exc_info=True)
    mode = str(ws_mode or agent_execution_mode() or "local").lower()
    return mode if mode in ("local", "docker") else "local"


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
    )
    add_run_env(env, session_id=str(spec.get("session_id") or "") or None,
                log_file=str(spec.get("log_file") or "") or None)
    env["AGENTS_HUB_ENTITY_RUN_ID"] = str(spec["run_id"])
    env["AGENTS_HUB_ENTITY_RUN_KIND"] = kind
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
    from managers.container_manager import CONTAINER_STATE_DIR
    from runtime import docker_runner
    from common.config import run_state_transport

    argv = _argv(spec, in_container=True)
    _write_header(spec, argv, where=" (in container)")
    docker_env = dict(env)
    docker_env["AGENT_LOG_FILE"] = f"{CONTAINER_STATE_DIR}/run_logs/{Path(str(spec['log_file'])).name}"
    docker_env["AGENT_RUN_STATE_TRANSPORT"] = run_state_transport()
    agent_id = str(spec.get("agent_id") or f"{spec.get('kind')}:{spec.get('entity_id')}")
    return docker_runner.start_run_container(
        str(spec["run_id"]), agent_id, argv,
        cwd=str(spec.get("ws_path") or PROJECT_ROOT), env=docker_env,
    )


def launch_prepared(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The process half of an entity launch: spawn the entrypoint on this
    host, locally or in a container, and record on the run where it lives.

    Returns the fields written on the run (``pid`` or ``container_name``,
    ``host``, ``execution_mode``). A container that fails to start closes
    the run as failed and raises, so the worker hands the launch back to the
    queue; the operator chose Docker, so this never falls back to a local
    subprocess.
    """
    from common import entity_runs

    run_id = str(spec["run_id"])
    env = build_env(spec)
    mode = str(spec.get("execution_mode") or "local")
    host = socket.gethostname()

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
    "ENTRYPOINTS", "LAUNCHERS", "dispatch", "launcher_for", "execution_mode_for",
    "build_env", "spawn_local", "spawn_docker", "launch_prepared", "child_alive",
]
