"""
RunLauncher: launches agent task runs as subprocesses or Docker containers,
and records each run in the shared ``runs`` table (``common.db``).

This module owns everything related to *launching* a run. Lifecycle *tracking*
(status polling, stop, failure propagation) lives in run_manager.py — the two
are the launch/track halves of run management, kept separate so run_manager
stays free of the agent-build / spec-building dependency stack.
Run record creation and completion updates for local subprocess runs are
handled by agent_run.py (which receives --run-id and manages its own lifecycle).

A launch has two halves, and since stage 2 of the scaling plan they may run
in different processes on different hosts (docs/workers.md):

- :func:`prepare_run` does the database side: resolves the workspace and
  project, the session, the instance, the log path, and writes the
  ``pending`` run record. It returns a JSON-serialisable *launch spec*.
- :func:`launch_prepared` does the process side: builds the child's
  environment from this process's own configuration and spawns the
  subprocess or the container.

:func:`start_run` is both halves in order. With the default role
(``AGENTS_HUB_ROLE=all``) it spawns right here, exactly as it always has.
In the ``api`` role it puts the spec on the launch queue
(``common/run_queue.py``) instead, and a worker anywhere calls
:func:`launch_prepared` with it.

Public API:
- preregister_run(task_id, agent_id, session_id) -> run_id
- start_run(task_id, agent_id, params, run_id) -> (run_id, session_id)
- prepare_run(...) -> spec; launch_prepared(spec) -> None
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Sequence
from uuid import uuid4

from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT
from managers.run_manager import preopen_run, _utc_now_iso, _update_run, finalize_task_from_run

#: What a task launch is called on the queue.
QUEUE_KIND = "task"


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

    In the ``api`` role the spawn is handed to a worker through the launch
    queue; the run record then reads ``queued`` until a worker picks it up.
    """
    spec = prepare_run(task_id, agent_id, params, run_id)
    from common.config import hub_role

    if hub_role() == "api":
        enqueue_prepared(spec)
    else:
        launch_prepared(spec)
    return spec["run_id"], spec.get("session_id")


def prepare_run(
    task_id: str,
    agent_id: str,
    params: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything a launch needs from the database, as a launch spec.

    The spec is plain JSON (paths as strings, no environment, no secrets): the
    process that spawns the run rebuilds the child's environment from its own
    configuration in :func:`launch_prepared`.
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

    # Agent version pin (agents/versions.py, docs/agent-versions.md): the
    # task's own pin, or a one-off override for this launch alone
    # (params["agent_version"], the same "one launch" convention as the
    # provider/model override below). None means the live definition.
    agent_version_pin: Optional[int] = None
    _raw_pin = params.get("agent_version") if params.get("agent_version") is not None else getattr(task, "agent_version", None)
    if _raw_pin is not None:
        try:
            agent_version_pin = int(_raw_pin)
        except (TypeError, ValueError):
            agent_version_pin = None

    # Per-run overrides (agents/run_overrides.py): params["overrides"], with
    # the older separate params tool_policy and output_schema folded in as
    # aliases. Validated before anything is written, so a bad key or a tool
    # set the capability guard refuses never becomes a run (ValueError /
    # CapabilityViolation to the caller).
    from agents import run_overrides as _run_overrides
    run_overrides = _run_overrides.validate_for_agent(
        agent_id, params.get("overrides"),
        tool_policy=params.get("tool_policy"), output_schema=params.get("output_schema"))

    # How many of this run's own delegated subtasks delegate_task_tool may
    # have running at once (agents/registry.py max_concurrent_delegates, or
    # this run's own overrides key): resolved once, here, and carried to the
    # run's own environment below so a container run enforces it without a
    # database round trip, the same way the delegation depth travels.
    from agents.registry import get_agent as _get_agent_for_launch
    max_concurrent_delegates = _run_overrides.effective_max_concurrent_delegates(
        _get_agent_for_launch(agent_id), run_overrides)

    # Budget gate: refuse to launch when the workspace has hit its hard cost cap.
    # Opt-in (only enforced when a hard cap is configured) and fail-open on any
    # lookup/pricing error, so a pricing hiccup never wedges a workspace.
    from common.budget import check_budget
    check_budget(ws_name)

    # A personal key's own money cap (common/api_keys.py
    # budget_usd_per_month, docs/api-keys.md "Money quota"): a task run
    # launched with a key that already spent its month refuses to start,
    # the same as the workspace's own hard cap above.
    from common.attribution import check_launch_budget
    check_launch_budget()

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
        agent_version_pin=agent_version_pin,
        # What this run was asked to build differently (the run page shows it).
        **({"overrides": _run_overrides.record_view(run_overrides)} if run_overrides else {}),
    )

    # agent_run.py's CLI: positional `agent` and optional positional `action`,
    # plus --desc/--task-id/--run-id/--workspace flags. The session id is passed
    # via AGENT_SESSION_ID in the env (see _build_env), not as a flag. Built once
    # and shared by both launch modes below — only the interpreter/module
    # prefix differs (a script path locally, "-m runtime.agent_run" in Docker,
    # the same module form instances.carrier already uses for instance_run).
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

    # A model picked for this run alone (a delegation choosing one from the
    # catalog, tools/delegation.py): passed as flags, so the child needs no
    # lookup and the run record shows what was asked for.
    for key, flag in (("provider", "--provider"), ("model", "--model")):
        value = str(params.get(key) or "").strip()
        if value:
            cli_args.extend([flag, value])

    # The agent version this run is pinned to (see agent_version_pin above):
    # passed as a flag like provider/model, so runtime/agent_run.py needs no
    # lookup back into the task record to know what to build.
    if agent_version_pin is not None:
        cli_args.extend(["--definition-version", str(agent_version_pin)])

    # A deployment's resources for this task's runs only (docs/deployments.md,
    # "Resources"): the memory pools bound instead of the agent's own, and
    # the extra secret names, passed as flags like the version pin so the
    # child builds from them without reading the task back.
    _task_pools = [str(p).strip() for p in (getattr(task, "memory_pool_ids", None) or []) if str(p or "").strip()]
    if _task_pools:
        cli_args.extend(["--memory-pool", ",".join(_task_pools)])
        cli_args.extend(["--memory-access", str(getattr(task, "memory_access", None) or "write")])
    for _name in (getattr(task, "secrets", None) or []):
        if str(_name or "").strip():
            cli_args.extend(["--extra-secret", str(_name).strip()])

    # The run's overrides as one flag (an answer schema and a tool policy of
    # a proactive tick included, proactive/service.py): the child folds them
    # into the build (agents/run_overrides.py).
    if run_overrides:
        cli_args.extend(["--overrides", json.dumps(run_overrides, ensure_ascii=False, sort_keys=True)])

    # Continuing a paused run rather than starting one. Passed as flags like
    # everything else the subprocess needs to know, so nothing has to be read
    # back out of the task record on the other side.
    resume = params.get("resume") or {}
    if resume.get("run_id"):
        cli_args.extend(["--resume-run", str(resume["run_id"]),
                         "--resume-value", json.dumps(resume.get("value"))])
        if resume.get("key"):
            cli_args.extend(["--resume-key", str(resume["key"])])

    # Continuing a run whose process died, from the checkpoint it wrote after
    # its last tool call (agents/checkpoint.py; the watchdog sets this).
    if params.get("resume_checkpoint"):
        cli_args.extend(["--resume-checkpoint", str(params["resume_checkpoint"])])

    # Execution mode resolution mirrors instances.carrier.start exactly: a
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

    # Two optional contributions to the launch, each a module that may be
    # absent or fail without changing the launch: the per-run money cap
    # (common/run_budget.py, docs/costs.md) puts the cap and the task's
    # spend so far into the child's environment; the environment the run
    # executes in (environments/launch.py, docs/environments.md) may add
    # environment variables, replace the execution mode and shape the docker
    # profile. Everything they return travels on the spec, so a worker on
    # another host applies it in launch_prepared.
    launch_env, docker_options, environment_id, env_mode = _launch_extras(task, ws_name)
    if env_mode in ("local", "docker"):
        execution_mode = env_mode

    # Audit trail (common/audit.py): who launched this run. No HTTP request is
    # in flight here (a launch may be re-queued by a worker), so the actor
    # comes from the current-user contextvar rather than a principal, "local"
    # when nobody is signed in (single/token mode, or the system itself).
    try:
        from common import audit
        from common.auth import LOCAL_OPERATOR_ID
        from common.identity import current_user_id
        _actor_id = current_user_id()
        audit.record(
            "run.launch",
            actor={"actor_id": _actor_id,
                   "actor_kind": "local" if _actor_id == LOCAL_OPERATOR_ID else "user",
                   "actor_name": None},
            workspace=ws_name, object_type="run", object_id=run_id,
            details={"agent_id": agent_id, "task_id": str(task_id),
                     "execution_mode": execution_mode},
        )
    except Exception:
        pass

    if environment_id:
        try:
            _update_run(run_id, {"environment_id": environment_id})
        except Exception:  # noqa: BLE001 - the run still launches without the environment note on its record
            pass

    # The sandbox size (environments/models.py SIZE_PRESETS) and effective
    # cpu limit of a docker-mode run's container, so common.pricing can price
    # the hours it lived once it ends (docs/costs.md "Container hours"). A
    # run without a size is still priced, per its effective cpus, once it is
    # known to execute in docker.
    container_profile: Dict[str, Any] = {}
    if docker_options and docker_options.get("size"):
        container_profile["container_size"] = str(docker_options["size"])
    if docker_options and docker_options.get("cpus"):
        container_profile["container_cpus"] = str(docker_options["cpus"])
    elif execution_mode == "docker":
        try:
            from common.config import live_setting
            from managers.container_manager import DEFAULT_RUN_CPUS
            container_profile["container_cpus"] = live_setting("AGENT_DOCKER_CPUS", DEFAULT_RUN_CPUS)
        except Exception:  # noqa: BLE001 - no cpu figure recorded is priced as "unknown", never blocks the launch
            logging.getLogger(__name__).debug("launch: no cpu figure for run %s", run_id, exc_info=True)
    if container_profile:
        try:
            _update_run(run_id, container_profile)
        except Exception:  # noqa: BLE001 - same as the environment_id note above
            logging.getLogger(__name__).debug("launch: container profile not recorded on run %s", run_id, exc_info=True)

    spec: Dict[str, Any] = {
        "kind": QUEUE_KIND,
        "run_id": run_id,
        # Contributions from the run budget and the environment (see
        # _launch_extras): plain strings and JSON, applied in launch_prepared.
        "launch_env": launch_env,
        "docker_options": docker_options,
        "environment_id": environment_id,
        # This run's own delegate concurrency limit (see max_concurrent_delegates
        # above), applied to the child's environment in launch_prepared.
        "max_concurrent_delegates": max_concurrent_delegates,
        "task_id": str(task_id),
        "agent_id": agent_id,
        "session_id": session_id,
        "instance_id": instance_id,
        "workspace": ws_name,
        # Who asked for this run, so the process that spawns it (maybe a
        # worker with no request in flight) can resolve user-scoped secrets.
        "launched_by": _current_user_id(),
        # The personal key it is charged to (common/attribution.py), handed to
        # the child so the runs it creates count against the same key.
        "key_id": _launching_key(),
        "ws_path": str(ws_path),
        "log_file": str(log_file),
        "cli_args": cli_args,
        "execution_mode": execution_mode,
        "priority": int(params.get("priority") or 0) if str(params.get("priority") or "").lstrip("-").isdigit() else 0,
    }
    # Extra variables the caller asked every launch in its context to carry
    # (runtime.entity_launch.child_env): a browser session handed to the
    # agent, for one. Plain strings and never a secret, so they may travel
    # through the queue; the key is absent when there are none.
    extra = _child_env_extra()
    if extra:
        spec["env"] = extra
    return spec


def _child_env_extra() -> Dict[str, str]:
    try:
        from runtime.entity_launch import _CHILD_ENV
        extra = _CHILD_ENV.get() or {}
    except Exception:
        return {}
    return {str(k): str(v) for k, v in extra.items()}


def enqueue_prepared(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Hand a prepared launch to the workers (``api`` role)."""
    from common import run_queue

    _update_run(spec["run_id"], {"status": "queued"})
    return run_queue.enqueue(
        spec["run_id"], QUEUE_KIND, spec,
        workspace=spec.get("workspace"), execution_mode=spec.get("execution_mode"),
        priority=int(spec.get("priority") or 0),
    )


def launch_prepared(spec: Dict[str, Any]) -> None:
    """Spawn the process for a prepared launch, on this host.

    Rebuilds the environment here (it carries provider keys and the relay
    token, which never travel through the queue) and records on the run which
    host started it, so only this host ever probes its pid or container.
    """
    run_id = str(spec["run_id"])
    agent_id = str(spec["agent_id"])
    ws_name = str(spec.get("workspace") or "")
    ws_path = Path(str(spec.get("ws_path") or "."))
    log_file = Path(str(spec["log_file"]))
    instance_id = spec.get("instance_id")
    cli_args = list(spec.get("cli_args") or [])
    session_id = str(spec.get("session_id") or "")
    execution_mode = str(spec.get("execution_mode") or "local")

    from instances import registry as instance_registry

    log_file.parent.mkdir(parents=True, exist_ok=True)
    # The extra secret names travel in the spec's args as --extra-secret flags
    # (see the launcher above), so the environment needs none here.
    env = _build_env(ws_name, session_id, str(log_file), instance_id,
                     agent_id=agent_id, user_id=str(spec.get("launched_by") or "") or None,
                     key_id=str(spec.get("key_id") or "") or None)
    # What the caller put on the spec through child_env fills the gaps only:
    # the launcher's own values (credentials, relay token, run ids) always win.
    extra = spec.get("env")
    if isinstance(extra, dict):
        for key, value in extra.items():
            env.setdefault(str(key), str(value))
    # The launcher's own contributions (run budget, environment) are not
    # caller hints, so they win over the base environment.
    launch_env = spec.get("launch_env")
    if isinstance(launch_env, dict):
        for key, value in launch_env.items():
            env[str(key)] = str(value)
    # This run's own delegate concurrency limit (tools/delegation.py
    # MAX_CONCURRENT_ENV): read directly off the environment by
    # delegate_task_tool, so a container run enforces it with no database
    # round trip, exactly like the delegation depth.
    from tools.delegation import MAX_CONCURRENT_ENV
    env[MAX_CONCURRENT_ENV] = str(int(spec.get("max_concurrent_delegates") or 6))
    # Host-bound secrets ride the token the run actually uses, now that the
    # environment may have replaced the proxy URL (environments/secret_egress.py).
    from common.subprocess_env import route_secrets
    route_secrets(env, execution_mode=execution_mode, workspace=ws_name)

    if execution_mode == "docker":
        _start_run_in_docker(run_id, agent_id, cli_args, ws_name, ws_path, env, log_file, instance_id,
                             docker_options=spec.get("docker_options") or None)
        return

    args = [sys.executable, str(PROJECT_ROOT / "runtime" / "agent_run.py")] + cli_args

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(
            f"--- Run started at {_utc_now_iso()} ---\n"
            f"Agent ID : {agent_id}\n"
        )
        lf.write(f"Command: {args}\nCWD: {str(ws_path)}\nHost: {socket.gethostname()}\n\n")
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

    _update_run(run_id, {"pid": proc.pid, "status": "running", "started_at": _utc_now_iso(),
                         "host": socket.gethostname()})
    instance_registry.ensure_instance(
        agent_id, instance_id=instance_id, workspace=ws_name, pid=proc.pid, state="active")


def _start_run_in_docker(
    run_id: str,
    agent_id: str,
    cli_args: list,
    ws_name: str,
    ws_path: Path,
    env: Dict[str, str],
    log_file: Path,
    instance_id: Optional[str],
    docker_options: Optional[Dict[str, Any]] = None,
) -> None:
    """Launch a task run in a sandboxed container instead of a subprocess.

    Mirrors instances.carrier.start's Docker branch: the module form
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

    # --write-stdout-to-log: the inner command's own flag (runtime/agent_run.py),
    # never added to `cli_args` itself since that list is shared with the local
    # subprocess branch above, which pipes Popen's stdout into the log file
    # directly and would tee into it twice if the flag reached that path too.
    inner_cmd = [sys.executable, "-m", "runtime.agent_run"] + cli_args + ["--write-stdout-to-log"]

    # AGENT_LOG_FILE in `env` is the *host* path agent_launcher just created,
    # meant for the Popen stdout redirection the local branch uses below —
    # there is no equivalent redirection for a detached container. Point it
    # instead at that same file's container-mounted path, so open_run() (which
    # agent_run.py calls on startup) records a log_file the dashboard can
    # open, and so --write-stdout-to-log above tees into the same file. See
    # docs/containers.md, "Logs in Docker mode".
    docker_env = dict(env)
    docker_env["AGENT_LOG_FILE"] = f"{CONTAINER_STATE_DIR}/run_logs/{log_file.name}"

    # How the container reaches its own run/task records: direct SQLite access
    # by default (the state dir mount is read-write, same as always), or the
    # HTTP relay when the operator opted into AGENT_RUN_STATE_TRANSPORT=http,
    # which is also what pins the state dir mount read-only for this run (see
    # managers.container_manager.build_run_command). Resolved live, like
    # AGENT_EXECUTION_MODE, so a Settings change applies to the next run.
    from common.config import run_state_transport as _run_state_transport
    docker_env["AGENT_RUN_STATE_TRANSPORT"] = _run_state_transport()

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(
            f"--- Run started at {_utc_now_iso()} ---\n"
            f"Agent ID : {agent_id}\n"
        )
        lf.write(f"Command (in container): {inner_cmd}\nWorkspace: {str(ws_path)}\n"
                 f"Host: {socket.gethostname()}\n\n")

    # docker_options is what the run's environment asks of the container
    # (environments/launch.py: network, limits, image, see docs/environments.md);
    # None keeps the plain hardened profile and the call exactly as before.
    _runner_kwargs: Dict[str, Any] = {"options": docker_options} if docker_options else {}
    result = docker_runner.start_run_container(
        run_id, agent_id, inner_cmd, cwd=str(ws_path), env=docker_env, **_runner_kwargs,
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
        "host": socket.gethostname(),
    })
    if instance_id:
        instance_registry.ensure_instance(
            agent_id, instance_id=instance_id, workspace=ws_name,
            container_name=container_name, state="active")
    try:
        from managers.container_manager import register_container
        register_container(str(container_name), kind="run", agent_id=agent_id, run_id=run_id,
                           image=result.get("image"))
    except Exception:
        pass


def _launching_key() -> Optional[str]:
    from common.attribution import launching_key
    return launching_key()


def _current_user_id() -> str:
    # The request's user, or in a launched run's own process the user that
    # run was launched by (common/attribution.py).
    from common.attribution import launching_user
    return launching_user()


def _launch_extras(task: Any, ws_name: Optional[str]) -> Tuple[Dict[str, str], Optional[Dict[str, Any]],
                                                              Optional[str], Optional[str]]:
    """What the run budget and the environment add to a launch.

    Returns ``(env, docker_options, environment_id, execution_mode)``:

    - ``env``: environment variables for the child, from
      ``common.run_budget.launch_env(task, ws_name)`` (the money cap and the
      task's spend so far) and ``environments.launch.launch_fields(...)["env"]``.
    - ``docker_options``: the environment's docker profile (network, limits,
      image), or None.
    - ``environment_id``: the environment the run executes in, or None.
    - ``execution_mode``: ``"local"`` / ``"docker"`` when the environment pins
      one, else None.

    Either module being absent, or raising, leaves the launch as it was:
    these are additions, never a reason not to start a run.
    """
    import logging
    _log = logging.getLogger(__name__)
    env: Dict[str, str] = {}
    docker_options: Optional[Dict[str, Any]] = None
    environment_id: Optional[str] = None
    execution_mode: Optional[str] = None
    try:
        from common.run_budget import RunBudgetUnavailableError, launch_env as _budget_env
    except ImportError:
        RunBudgetUnavailableError = None  # type: ignore[assignment,misc]
        _budget_env = None  # type: ignore[assignment]
    if _budget_env is not None:
        try:
            env.update({str(k): str(v) for k, v in (_budget_env(task, ws_name) or {}).items()})
        except Exception as exc:  # noqa: BLE001 - the cap is an addition; a failure to compute it never blocks the launch, unless the workspace asked for fail closed
            if RunBudgetUnavailableError is not None and isinstance(exc, RunBudgetUnavailableError):
                # The workspace's budget is fail closed (docs/costs.md): a cap
                # that cannot be computed refuses the launch the same way
                # check_budget does, so every caller's budget handling applies.
                from common.budget import BudgetUnavailableError
                raise BudgetUnavailableError(ws_name or "", "run", str(exc)) from exc
            _log.debug("run budget launch env failed for task %s", getattr(task, "id", None), exc_info=True)
    try:
        from environments.launch import launch_fields
        fields = launch_fields(task, ws_name) or {}
        env.update({str(k): str(v) for k, v in (fields.get("env") or {}).items()})
        docker_options = fields.get("docker") or None
        environment_id = fields.get("environment_id") or None
        execution_mode = fields.get("execution_mode") or None
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - same: an environment that cannot be resolved falls back to the workspace's mode
        _log.debug("environment launch fields failed for task %s", getattr(task, "id", None), exc_info=True)
    return env, docker_options, environment_id, execution_mode


def _build_env(ws_name: str, session_id: str, log_file: str,
               instance_id: Optional[str] = None, *, agent_id: Optional[str] = None,
               user_id: Optional[str] = None, key_id: Optional[str] = None,
               extra_secret_names: Optional[Sequence[str]] = None) -> Dict[str, str]:
    # base env + run metadata only. No model override is injected: agent_run.py
    # resolves the model via create_agent's cascade (agent definition → workspace
    # override → workspace settings → global), so a single run honours the agent's
    # own model. The flow launcher does inject an override to keep every node on
    # one model — that's the deliberate difference between the two launchers.
    from common.subprocess_env import base_subprocess_env, add_run_env
    from instances.registry import ENV_INSTANCE_ID
    env = base_subprocess_env(ws_name, agent_id=agent_id, user_id=user_id, key_id=key_id,
                              extra_secret_names=extra_secret_names)
    add_run_env(env, session_id=session_id, log_file=log_file)
    if instance_id:
        env[ENV_INSTANCE_ID] = str(instance_id)
    return env
