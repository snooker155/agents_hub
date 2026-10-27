"""
The carrier of a resident instance: the process or container it runs in.

A resident instance is the copy of an agent that the agent page's Run starts
(``POST /api/instances``): one long-lived process (``runtime/instance_run.py``)
or one container running that process, waiting on its mailbox and answering
every message in its own runs. Nodes used to play this part as a record of
their own next to the instance; they are gone, and everything a node knew now
lives on the instance row (``instances`` table, carrier fields in ``extra``):

- ``carrier_mode`` (``local`` | ``docker``), ``carrier_host`` (the host that
  spawned it: signals and ``docker`` calls only work from there),
  ``pid`` / ``container_name`` (columns), ``carrier_log_file``,
  ``carrier_status`` (``starting``, ``running``, ``stopping``, ``stopped``,
  ``failed``), ``carrier_started_at``, ``carrier_finished_at``,
  ``carrier_exit_code``, ``carrier_error``, ``heartbeat_at``;
- inputs: ``take_tasks`` (also answer tasks assigned to the agent in the
  instance's workspace, the strict resource cap the worker node used to be),
  ``concurrency`` (runs at once, one per conversation);

One instance, one carrier, never several instances in one process: the
environment, ``os.environ`` and the working directory are per process.
A restart keeps the instance (id, conversations, runs) and replaces the
process; every process an instance ever had is a row of ``instance_carriers``.

The instance ``state`` is the one the operator reads; ``carrier_status`` is
the process underneath. ``update_from_process`` maps one onto the other.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import db
from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT
from instances import registry, store

log = logging.getLogger(__name__)

CARRIER_LOGS_DIR = AGENTS_HUB_ROOT / "instance_logs"

LIVE_CARRIER_STATUSES = ("starting", "running", "stopping")
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_concurrency() -> int:
    try:
        value = int(os.environ.get("AGENTS_HUB_INSTANCE_CONCURRENCY", DEFAULT_CONCURRENCY))
    except ValueError:
        value = DEFAULT_CONCURRENCY
    return clamp_concurrency(value)


def clamp_concurrency(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_CONCURRENCY
    return min(max(n, 1), MAX_CONCURRENCY)


def is_resident(instance: Optional[Dict[str, Any]]) -> bool:
    return bool(instance) and instance.get("kind") in store.CARRIER_KINDS


def append_log(instance: Optional[Dict[str, Any]], message: str) -> None:
    """One timestamped line in the carrier's own log (not a run's log)."""
    log_file = (instance or {}).get("carrier_log_file")
    if not log_file:
        return
    try:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"[{_log_now()}] {message}\n")
    except OSError:
        log.debug("carrier log append failed for %s", (instance or {}).get("instance_id"),
                  exc_info=True)


# ── Carrier history ──────────────────────────────────────────────────────────

def _open_carrier(instance: Dict[str, Any], reason: str) -> str:
    carrier_id = "car_" + uuid.uuid4().hex[:16]
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO instance_carriers (carrier_id, instance_id, mode, host, pid, "
            "container_name, log_file, started_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (carrier_id, instance["instance_id"], instance.get("carrier_mode"),
             instance.get("carrier_host"), instance.get("pid"), instance.get("container_name"),
             instance.get("carrier_log_file"), _now(), reason),
        )
    return carrier_id


def _close_carrier(instance_id: str, *, exit_code: Optional[int] = None,
                   error: Optional[str] = None) -> None:
    """Close the instance's open carrier row, if it has one."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE instance_carriers SET finished_at = ?, exit_code = ?, error = ? "
            "WHERE instance_id = ? AND finished_at IS NULL",
            (_now(), exit_code, (error or None) and str(error)[:2000], str(instance_id)),
        )


def carriers(instance_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Every process the instance has had, newest first."""
    rows = db.get_conn().execute(
        "SELECT * FROM instance_carriers WHERE instance_id = ? ORDER BY started_at DESC LIMIT ?",
        (str(instance_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Execution mode ───────────────────────────────────────────────────────────

def _execution_mode(workspace: Optional[str]) -> str:
    """Workspace override, else the live setting. Local where a container
    must not open the database (Docker Desktop with SQLite): an instance
    process reads and writes its records there directly."""
    from common.config import run_execution_mode
    return run_execution_mode(workspace)


def _abs_workspace(workspace: Optional[str]) -> Optional[str]:
    if not workspace:
        return None
    try:
        from workspace import create_workspace_folder
        return str(create_workspace_folder(workspace))
    except Exception:  # noqa: BLE001 - the instance runs without a resolved workspace path
        log.debug("create_workspace_folder failed for %s", workspace, exc_info=True)
        return None


# ── Start ────────────────────────────────────────────────────────────────────

class CapacityReached(ValueError):
    """The workspace already runs as many copies of the agent as it allows."""


def workspace_capacity(workspace: Optional[str], agent_id: str) -> Optional[int]:
    """The workspace's number for ``agent_id``: how many live resident copies
    of the agent it allows.

    None (no limit) in the default workspace; elsewhere the workspace's
    capacity override for the agent, 1 when it has none (the same rule the
    agent's page shows).
    """
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace) or "default"
    if ws == "default":
        return None
    try:
        from workspace import get_workspace_metadata
        overrides = get_workspace_metadata(ws).get("agent_capacity_overrides") or {}
    except Exception:  # noqa: BLE001 - unreadable metadata: the documented default
        overrides = {}
    try:
        return max(0, int(overrides.get(agent_id, 1)))
    except (TypeError, ValueError):
        return 1


def _check_capacity(workspace: Optional[str], agent_id: str) -> None:
    cap = workspace_capacity(workspace, agent_id)
    if cap is None:
        return
    live = list_resident(workspace=workspace, agent_id=agent_id, live=True)
    if len(live) >= cap:
        raise CapacityReached(
            f"Workspace '{workspace}' allows {cap} running instance(s) of '{agent_id}'; "
            "stop one or raise the agent's capacity in the workspace first")


def start(
    agent_id: str,
    *,
    workspace: Optional[str] = None,
    label: Optional[str] = None,
    take_tasks: bool = False,
    concurrency: Optional[int] = None,
    started_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a resident instance of ``agent_id`` and return its record.

    The row is written first (``starting``) so the page has something to open
    while the process boots; the process marks it ``standby`` once its loop is
    up. Raises ValueError for an unknown agent,
    CapacityReached when the workspace already runs as many copies of the
    agent as it allows, and RuntimeError when the process or container cannot
    be started.
    """
    from agents.registry import get_agent

    agent_id = (agent_id or "").strip()
    if not agent_id or not get_agent(agent_id):
        raise ValueError(f"Unknown agent: {agent_id}")
    _check_capacity(workspace, agent_id)
    instance = registry.ensure_instance(
        agent_id, kind=store.RESIDENT_KIND, workspace=workspace, label=label or None,
        state="starting",
    )
    instance_id = instance["instance_id"]
    fields: Dict[str, Any] = {
        "take_tasks": bool(take_tasks),
        "concurrency": clamp_concurrency(concurrency) if concurrency else default_concurrency(),
        "carrier_log_file": str(CARRIER_LOGS_DIR / f"instance_{instance_id}.log"),
        "started_by": started_by,
    }
    instance = store.update(instance_id, **fields) or {**instance, **fields}
    try:
        instance = _spawn(instance, reason="start")
    except Exception as exc:
        store.update(instance_id, carrier_status="failed", carrier_error=str(exc)[:2000])
        registry.mark_failed(instance_id, str(exc))
        raise
    return instance


def _spawn(instance: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    """Start the carrier process (or container) for ``instance``."""
    instance_id = instance["instance_id"]
    agent_id = str(instance.get("agent_id") or "")
    if not agent_id:
        raise RuntimeError(f"Instance {instance_id} has no agent to run")
    workspace = instance.get("workspace")
    log_file = Path(instance.get("carrier_log_file")
                    or CARRIER_LOGS_DIR / f"instance_{instance_id}.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    inner_cmd = [
        sys.executable, "-m", "runtime.instance_run",
        "--instance-id", instance_id,
        "--log-file", str(log_file),
        "--agent-id", agent_id,
    ]
    abs_ws = _abs_workspace(workspace)
    if abs_ws:
        inner_cmd.extend(["--workspace", abs_ws])

    env = os.environ.copy()
    # A provider key saved since the backend started (common/provider_env.py).
    from common import provider_env
    provider_env.overlay(env)
    from common.workspace_context import normalize_workspace_name
    env["AGENT_WORKSPACE"] = normalize_workspace_name(workspace) or "default"
    from common.subprocess_env import strip_hub_credentials
    strip_hub_credentials(env)
    env[registry.ENV_INSTANCE_ID] = instance_id
    mode = _execution_mode(workspace)

    pid: Optional[int] = None
    container_name: Optional[str] = None
    if mode == "docker":
        from managers.container_manager import container_name_for_node, start_node_container
        container_name = container_name_for_node(instance_id)
        result = start_node_container(
            instance_id, agent_id, inner_cmd + ["--write-stdout-to-log"], workspace, env,
        )
        if not result.get("success"):
            raise RuntimeError(f"Failed to start Docker container: {result.get('error')}")
    else:
        popen_kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        # Binary append so the child inherits a plain fd; the parent's handle
        # closes right after Popen and the child keeps its own copy.
        with open(log_file, "ab") as log_fh:
            proc = subprocess.Popen(
                inner_cmd, cwd=str(PROJECT_ROOT), env=env,
                stdout=log_fh, stderr=subprocess.STDOUT, **popen_kwargs,
            )
        pid = proc.pid

    now = _now()
    updates: Dict[str, Any] = {
        "carrier_mode": mode,
        "carrier_host": socket.gethostname(),
        "carrier_status": "starting",
        "carrier_started_at": now,
        "carrier_finished_at": None,
        "carrier_exit_code": None,
        "carrier_error": None,
        "carrier_log_file": str(log_file),
        "pid": pid,
        "container_name": container_name,
        "started_at": now,
        "finished_at": None,
        "error": None,
        "stop_requested_at": None,
        "state": "starting",
    }
    instance = store.update(instance_id, **updates) or {**instance, **updates}
    registry.ensure_instance(agent_id or "", instance_id=instance_id, state="starting")
    _open_carrier(instance, reason)
    append_log(instance, f"[carrier_start] reason={reason} mode={mode} "
                         f"pid={pid or container_name or '-'} agent={agent_id} "
                         f"workspace={workspace or '-'}")
    return store.get(instance_id) or instance


# ── Liveness ─────────────────────────────────────────────────────────────────

def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # Reap a zombie child first, or it would count as alive forever.
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - unexpected errors read as alive, the safer mistake
        return True
    return True


def _carried_here(instance: Dict[str, Any]) -> bool:
    host = instance.get("carrier_host")
    return not host or host == socket.gethostname()


def process_alive(instance: Dict[str, Any]) -> bool:
    """Whether the carrier process or container still exists.

    A carrier on another host cannot be probed from here; its heartbeat is the
    evidence instead (``heartbeat_at`` younger than ``stale_after``).
    """
    if instance.get("carrier_mode") == "docker" or instance.get("kind") == "container":
        cname = instance.get("container_name")
        if not cname:
            return False
        if not _carried_here(instance):
            return _heartbeat_fresh(instance)
        from managers.container_manager import container_running
        return container_running(cname)
    pid = int(instance.get("pid") or 0)
    if pid <= 0:
        return False
    if not _carried_here(instance):
        return _heartbeat_fresh(instance)
    return _pid_exists(pid)


def _heartbeat_fresh(instance: Dict[str, Any], stale_after: float = 120.0) -> bool:
    ts = instance.get("heartbeat_at") or instance.get("carrier_started_at")
    if not ts:
        return True
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() < stale_after


def sync(instance: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Correct a resident instance whose carrier died without saying so."""
    if not is_resident(instance):
        return instance
    assert instance is not None
    status = instance.get("carrier_status")
    if status is None and instance.get("kind") in ("node", "container"):
        # A row migrated from a node without a carrier status: judge by state.
        status = "running" if instance.get("state") in store.LIVE_STATES else "stopped"
    if status not in LIVE_CARRIER_STATUSES:
        return instance
    if process_alive(instance):
        return instance
    reason = "carrier process exited"
    updated = update_from_process(instance["instance_id"], "stopped", exit_code=-1)
    fail_in_progress_runs(instance["instance_id"], reason)
    return updated or instance


def get(instance_id: str) -> Optional[Dict[str, Any]]:
    return sync(store.get(instance_id))


def list_resident(*, workspace: Optional[str] = None, agent_id: Optional[str] = None,
                  live: Optional[bool] = None, limit: int = 500) -> List[Dict[str, Any]]:
    page = store.list_instances(limit=limit, workspace=workspace, agent_id=agent_id,
                                live=live, kinds=store.CARRIER_KINDS)
    return [sync(i) or i for i in page["items"]]


# ── Status reported by the process ───────────────────────────────────────────

def update_from_process(instance_id: str, carrier_status: str, *,
                        exit_code: Optional[int] = None,
                        error: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Record a carrier status and move the instance state with it.

    ``running`` is standby unless a run is going; ``stopped`` and ``failed``
    end the instance and close its carrier row.
    """
    before = store.get(instance_id)
    if before is None:
        return None
    updates: Dict[str, Any] = {"carrier_status": carrier_status}
    if carrier_status in ("stopped", "failed"):
        updates["carrier_finished_at"] = _now()
        updates["carrier_exit_code"] = exit_code
        if error:
            updates["carrier_error"] = str(error)[:2000]
    inst = store.update(instance_id, **updates)
    if before.get("carrier_status") != carrier_status:
        detail = []
        if exit_code is not None:
            detail.append(f"exit_code={exit_code}")
        if error:
            detail.append(f"error={error}")
        append_log(inst or before, f"[carrier_state] {before.get('carrier_status') or 'unknown'} "
                                   f"-> {carrier_status}" + (f" ({', '.join(detail)})" if detail else ""))
    if carrier_status == "running":
        if (inst or before).get("state") != "active":
            inst = registry.mark_standby(instance_id, "waiting for messages") or inst
    elif carrier_status == "stopped":
        _close_carrier(instance_id, exit_code=exit_code, error=error)
        inst = registry.mark_stopped(instance_id, error or "stopped") or inst
    elif carrier_status == "failed":
        _close_carrier(instance_id, exit_code=exit_code, error=error)
        inst = registry.mark_failed(instance_id, error or "carrier failed") or inst
    return inst


def heartbeat(instance_id: str, **fields: Any) -> None:
    """Stamp the instance's heartbeat, with whatever else the carrier
    reports on each beat (``fields``)."""
    try:
        store.update(instance_id, heartbeat_at=_now(), **fields)
    except Exception:  # noqa: BLE001 - a missed beat is retried on the next one
        log.debug("heartbeat failed for %s", instance_id, exc_info=True)


def fail_in_progress_runs(instance_id: str, reason: str) -> int:
    """Close the runs a dead or stopped carrier left open."""
    try:
        from managers.run_manager import fail_in_progress_runs_for_instance
        return fail_in_progress_runs_for_instance(instance_id, reason)
    except Exception:  # noqa: BLE001 - best-effort cleanup
        log.debug("fail_in_progress_runs_for_instance failed for %s", instance_id, exc_info=True)
        return 0


# ── Stop, restart, remove ────────────────────────────────────────────────────

def _signal_process(pid: int, sig: int) -> bool:
    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            try:
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
            except Exception:  # noqa: BLE001 - reported as not sent
                log.debug("TerminateProcess failed for pid %s", pid, exc_info=True)
            return False
    try:
        os.killpg(pid, sig)
        return True
    except OSError:
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False


def stop(instance_id: str, *, reason: str = "stopped by operator") -> bool:
    """Stop the carrier. The instance ends ``stopped`` and keeps its history."""
    instance = get(instance_id)
    if not is_resident(instance):
        return False
    assert instance is not None
    if instance.get("carrier_status") not in LIVE_CARRIER_STATUSES:
        return False
    if not _carried_here(instance):
        # No signal reaches another host; the process reads this on its next
        # heartbeat and stops itself (runtime/instance_run.py).
        store.update(instance_id, stop_requested_at=_now(), carrier_status="stopping")
        append_log(instance, f"[carrier_stop] requested from {socket.gethostname()}")
        return True

    append_log(instance, f"[carrier_stop] {reason}")
    store.update(instance_id, carrier_status="stopping")
    if instance.get("carrier_mode") == "docker" or instance.get("container_name"):
        cname = instance.get("container_name")
        from managers.container_manager import stop_container
        sent = bool(cname) and stop_container(cname)
        if sent:
            update_from_process(instance_id, "stopped", exit_code=0, error=reason)
    else:
        pid = int(instance.get("pid") or 0)
        sent = pid > 0 and _signal_process(pid, signal.SIGTERM)
        if sent:
            # The process writes its own "stopped" on SIGTERM; make sure the
            # row ends there even if it dies before it can.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and _pid_exists(pid):
                time.sleep(0.1)
            current = store.get(instance_id) or {}
            if current.get("carrier_status") not in ("stopped", "failed"):
                update_from_process(instance_id, "stopped", exit_code=0, error=reason)
    if sent:
        fail_in_progress_runs(instance_id, "Instance was stopped")
    else:
        store.update(instance_id, carrier_status=instance.get("carrier_status"))
        append_log(instance, "[carrier_stop] could not reach the process")
    return bool(sent)


def restart(instance_id: str) -> Optional[Dict[str, Any]]:
    """Replace the carrier with a fresh process; the instance stays the same.

    A container restarts in place (same name); a local process is stopped and
    a new one spawned. Returns the updated instance, or None when it cannot
    be restarted from here.
    """
    instance = get(instance_id)
    if not is_resident(instance):
        return None
    assert instance is not None
    if not _carried_here(instance) and instance.get("carrier_status") in LIVE_CARRIER_STATUSES:
        return None
    append_log(instance, "[carrier_restart] requested")
    # In place only while docker mode still applies: an instance started in a
    # container before the database could no longer be shared with one
    # (Docker Desktop, see _execution_mode) is stopped and respawned locally.
    if (instance.get("carrier_mode") == "docker" and instance.get("container_name")
            and instance.get("carrier_status") in LIVE_CARRIER_STATUSES
            and _execution_mode(instance.get("workspace")) == "docker"):
        from managers.container_manager import restart_container
        store.update(instance_id, carrier_status="starting", state="starting")
        _close_carrier(instance_id, error="restarted")
        if not restart_container(instance["container_name"]):
            update_from_process(instance_id, "failed", error="docker restart failed")
            return store.get(instance_id)
        fail_in_progress_runs(instance_id, "Instance was restarted")
        current = store.get(instance_id) or instance
        _open_carrier(current, "restart")
        return current
    if instance.get("carrier_status") in LIVE_CARRIER_STATUSES:
        stop(instance_id, reason="restart")
    current = store.get(instance_id) or instance
    try:
        return _spawn(current, reason="restart")
    except Exception as exc:
        update_from_process(instance_id, "failed", error=str(exc))
        raise


def remove(instance_id: str) -> bool:
    """Delete a resident instance that is not running, with its carrier log."""
    instance = get(instance_id)
    if instance is None:
        return False
    if instance.get("carrier_status") in LIVE_CARRIER_STATUSES:
        return False
    removed = store.delete(instance_id)
    if removed:
        log_file = instance.get("carrier_log_file")
        if log_file:
            try:
                Path(log_file).unlink(missing_ok=True)
            except OSError:
                pass
    return removed


# ── Inputs ───────────────────────────────────────────────────────────────────

def set_inputs(instance_id: str, *, take_tasks: Optional[bool] = None,
               concurrency: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Change what the running instance listens to. The process reads its
    row on every loop, so this applies without a restart."""
    updates: Dict[str, Any] = {}
    if take_tasks is not None:
        updates["take_tasks"] = bool(take_tasks)
    if concurrency is not None:
        updates["concurrency"] = clamp_concurrency(concurrency)
    if not updates:
        return store.get(instance_id)
    inst = store.update(instance_id, **updates)
    if inst:
        append_log(inst, "[inputs] " + ", ".join(f"{k}={v}" for k, v in updates.items()))
    return inst


#: Fields an older install may still carry on an instance row from when a
#: copy could be published outside the hub; never shown.
_HIDDEN_FIELDS = ("inbound_secret", "expose_token")


def public_view(instance: Dict[str, Any]) -> Dict[str, Any]:
    """The record without secrets or the fields of the removed publication."""
    return {k: v for k, v in instance.items() if k not in _HIDDEN_FIELDS}
