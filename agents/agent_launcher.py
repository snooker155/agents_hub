"""
RunLauncher: launches agent task runs as subprocesses or Docker containers,
and records each run in the shared ``runs`` table (``common.db``).

This module owns everything related to *launching* a run. Lifecycle *tracking*
(status polling, stop, failure propagation) lives in run_manager.py — the two
are the launch/track halves of run management, kept separate so run_manager
stays free of the agent-build / spec-building dependency stack.
Run record creation and completion updates for local subprocess runs are
handled by agent_run.py (which receives --run-id and manages its own lifecycle).

This module resolves the workspace/project, assembles the environment and the
``python agent_run.py ...`` command line, then spawns it. It never builds or
invokes an agent — that happens in the spawned subprocess (which calls
``agents.agent_factory.create_agent``).

Public API:
- preregister_run(task_id, agent_id, session_id) -> run_id
- start_run(task_id, agent_id, params, run_id) -> (run_id, session_id)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from common.paths import AGENTS_HUB_ROOT
from managers.run_manager import preopen_run, _utc_now_iso, _update_run, finalize_task_from_run

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def preregister_run(task_id: str, agent_id: str, session_id: Optional[str] = None) -> str:
    """Pre-create a run record before the subprocess is launched.

    Returns the pre-allocated run_id.  Call start_run(..., run_id=<id>) later to
    actually spawn the subprocess using the same run_id.
    """
    run_id = str(uuid4())
    return preopen_run(
        run_id,
        agent_id,
        task_id=str(task_id),
        session_id=session_id or None,
        status="awaiting_approval",
        channel="local",
    )

def start_run(
    task_id: str,
    agent_id: str,
    params: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Launch an agent run (subprocess, Docker, or remote) and record its state.

    Creates or reuses a session for the task, then spawns the subprocess.
    Returns (run_id, session_id). For local subprocess runs, the run record is
    created by agent_run.py at startup (it receives --run-id). For remote runs
    the record is created here since agent_run.py is not involved.
    """
    from tasks import service as _ts
    from agents.registry import get_agent
    from workspace import as_param_dict, resolve_task_workspace
    from common.session_service import get_or_create_task_session


    if not get_agent(agent_id):
        raise ValueError(f"Unknown agent_id: {agent_id}")

    task = _ts.get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    # ws_name is the workspace; ws_path is where the agent runs (project subfolder
    # when set, else workspace root, else cwd). The agent operates in ws_path;
    # the workspace root holds .logs/ (metadata lives in workspaces.json).
    params = as_param_dict(params)
    ws_name, ws_path = resolve_task_workspace(task, params)

    # Budget gate: refuse to launch when the workspace has hit its hard cost cap.
    # Opt-in (only enforced when a hard cap is configured) and fail-open on any
    # lookup/pricing error, so a pricing hiccup never wedges a workspace.
    from common.budget import check_budget
    check_budget(ws_name)

    # Ensure a session exists for this task
    session_id = get_or_create_task_session(
        title=task.title,
        workspace=ws_name,
        task_id=str(task_id),
    )
    try:
        _ts.update_task(task_id, session_id=session_id)
    except Exception:
        pass

    run_id = run_id or str(uuid4())

    # Register the live copy of the agent this launch creates. One task run =
    # one instance: it keeps its own context after finishing, so the operator
    # can still open it among a thousand siblings and write to it.
    from instances import registry as instance_registry
    instance = instance_registry.ensure_instance(
        agent_id,
        kind="task",
        workspace=ws_name,
        session_id=session_id,
        task_id=str(task_id),
        hint=(params.get("description") or task.title or ""),
        state="starting",
    )
    instance_id = instance["instance_id"]

    log_dir = AGENTS_HUB_ROOT / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"agent_run_{run_id}.log"

    # Pre-create a "pending" placeholder so the dashboard sees the run (and its
    # log_file) before the subprocess has had time to start and call open_run
    # itself — the log lets the message Logs tab show something even if the
    # subprocess crashes before open_run(). preopen_run also links it to the
    # session; open_run() later fills in pid/started_at and the task log entry.
    preopen_run(
        run_id,
        agent_id,
        task_id=str(task_id),
        session_id=session_id,
        status="pending",
        log_file=str(log_file),
        channel="local",
        instance_id=instance_id,
        workspace=ws_name,
    )

    env = _build_env(ws_name, session_id, str(log_file), instance_id)

    # agent_run.py's CLI: positional `agent` and optional positional `action`,
    # plus --desc/--task-id/--run-id/--workspace flags. The session id is passed
    # via AGENT_SESSION_ID in the env (see _build_env), not as a flag. Built once
    # and shared by both launch modes below — only the interpreter/module
    # prefix differs (a script path locally, "-m runtime.agent_run" in Docker,
    # the same module form node_manager already uses for node_run).
    cli_args = [agent_id]

    action = params.get("action") or ""
    if action:
        cli_args.append(action)

    cli_args += [
        "--workspace", str(ws_path),
        "--task-id", str(task_id),
        "--run-id", run_id,
    ]

    desc = params.get("description") or ""
    if desc:
        cli_args.extend(["--desc", desc])

    # Continuing a paused run rather than starting one. Passed as flags like
    # everything else the subprocess needs to know, so nothing has to be read
    # back out of the task record on the other side.
    resume = params.get("resume") or {}
    if resume.get("run_id"):
        import json as _json
        cli_args.extend(["--resume-run", str(resume["run_id"]),
                         "--resume-value", _json.dumps(resume.get("value"))])
        if resume.get("key"):
            cli_args.extend(["--resume-key", str(resume["key"])])

    # Execution mode resolution mirrors node_manager.start_node exactly: a
    # workspace's own override (Settings → workspace → agent_mode) wins over
    # the global setting, both read live so a Settings-page change reaches the
    # next run without a restart.
    from common.config import agent_execution_mode
    _ws_agent_mode: Optional[str] = None
    if ws_name:
        try:
            from workspace import get_workspace_metadata
            _ws_agent_mode = (get_workspace_metadata(ws_name).get("settings") or {}).get("agent_mode") or None
        except Exception:
            pass
    execution_mode = _ws_agent_mode or agent_execution_mode()

    if execution_mode == "docker":
        _start_run_in_docker(run_id, agent_id, cli_args, ws_name, ws_path, env, log_file, instance_id)
        return run_id, session_id

    args = [sys.executable, str(PROJECT_ROOT / "runtime" / "agent_run.py")] + cli_args

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(
            f"--- Run started at {_utc_now_iso()} ---\n"
            f"Agent ID : {agent_id}\n"
        )
        lf.write(f"Command: {args}\nCWD: {str(ws_path)}\n\n")
        lf.flush()

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True

        proc = subprocess.Popen(
            args,
            start_new_session=start_new_session,
            creationflags=creationflags,
            cwd=str(ws_path),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )

    _update_run(run_id, {"pid": proc.pid, "status": "running", "started_at": _utc_now_iso()})
    instance_registry.ensure_instance(
        agent_id, instance_id=instance_id, workspace=ws_name, pid=proc.pid, state="active")
    return run_id, session_id


def _start_run_in_docker(
    run_id: str,
    agent_id: str,
    cli_args: list,
    ws_name: str,
    ws_path: Path,
    env: Dict[str, str],
    log_file: Path,
    instance_id: Optional[str],
) -> None:
    """Launch a task run in a sandboxed container instead of a subprocess.

    Mirrors node_manager.start_node's Docker branch: the module form
    ("-m runtime.agent_run"), not a script path, so container_manager's path
    translation only ever has to deal with arguments, never the interpreter
    line (a bare host script path would not exist inside the container).

    On success the run record gets execution_mode="docker" and a
    container_name instead of a pid. On failure the run is closed as failed
    and control returns normally — the operator chose Docker, so this never
    silently falls back to a local subprocess.
    """
    from instances import registry as instance_registry
    from managers.container_manager import CONTAINER_STATE_DIR
    from runtime import docker_runner

    inner_cmd = [sys.executable, "-m", "runtime.agent_run"] + cli_args

    # AGENT_LOG_FILE in `env` is the *host* path agent_launcher just created,
    # meant for the Popen stdout redirection the local branch uses below —
    # there is no equivalent redirection for a detached container. Point it
    # instead at that same file's container-mounted path, so open_run() (which
    # agent_run.py calls on startup) records a log_file the dashboard can
    # open. agent_run.py does not tee its own stdout into it in Docker mode —
    # that tee only activates when AGENT_LOG_FILE is unset (the bare-CLI
    # case) — so the file itself stays just the header written below; full
    # run output lives in `docker logs <container_name>`. See
    # docs/containers.md for this as a documented gap rather than a bug.
    docker_env = dict(env)
    docker_env["AGENT_LOG_FILE"] = f"{CONTAINER_STATE_DIR}/run_logs/{log_file.name}"

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(
            f"--- Run started at {_utc_now_iso()} ---\n"
            f"Agent ID : {agent_id}\n"
        )
        lf.write(f"Command (in container): {inner_cmd}\nWorkspace: {str(ws_path)}\n\n")

    result = docker_runner.start_run_container(
        run_id, agent_id, inner_cmd, cwd=str(ws_path), env=docker_env,
    )

    if not result.get("success"):
        error = f"Failed to start Docker container: {result.get('error') or 'unknown error'}"
        _update_run(run_id, {
            "status": "failed",
            "finished_at": _utc_now_iso(),
            "exit_code": 1,
            "error": error,
        })
        if instance_id:
            instance_registry.mark_failed(instance_id, error)
        try:
            finalize_task_from_run(run_id, "failed", 1)
        except Exception:
            pass
        return

    container_name = result.get("container_name")
    _update_run(run_id, {
        "execution_mode": "docker",
        "container_name": container_name,
        "status": "running",
        "started_at": _utc_now_iso(),
    })
    if instance_id:
        instance_registry.ensure_instance(
            agent_id, instance_id=instance_id, workspace=ws_name,
            container_name=container_name, state="active")


def _build_env(ws_name: str, session_id: str, log_file: str,
               instance_id: Optional[str] = None) -> Dict[str, str]:
    # base env + run metadata only. No model override is injected: agent_run.py
    # resolves the model via create_agent's cascade (agent definition → workspace
    # override → workspace settings → global), so a single run honours the agent's
    # own model. The flow launcher does inject an override to keep every node on
    # one model — that's the deliberate difference between the two launchers.
    from common.subprocess_env import base_subprocess_env, add_run_env
    from instances.registry import ENV_INSTANCE_ID
    env = base_subprocess_env(ws_name)
    add_run_env(env, session_id=session_id, log_file=log_file)
    if instance_id:
        env[ENV_INSTANCE_ID] = str(instance_id)
    return env


