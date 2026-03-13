"""
Node Manager – manages long-running agent nodes (independent of tasks).

A Node is a persistent subprocess that runs an agent in service/worker mode.
Unlike task-based runs (run_manager), nodes persist until explicitly stopped.

State is stored in agents/state/nodes.json.

Public API
----------
start_node(agent_id, workspace, label)  -> node_id
stop_node(node_id)                      -> bool
delete_node(node_id)                    -> bool   (remove stopped/failed record)
list_nodes()                            -> List[dict]
get_node(node_id)                       -> Optional[dict]
update_node(node_id, updates)           -> Optional[dict]  (also called by node_runner subprocess)
get_running_nodes_for_agent(agent_id)   -> List[dict]
ensure_default_node()                   -> Optional[str]   (auto-start orchestrator)
expose_node(node_id)                    -> Optional[dict]  (generate token, mark exposed)
unexpose_node(node_id)                  -> bool
get_node_by_token(token)               -> Optional[dict]
log_connection(node_id, record)         -> None
get_connections(node_id)               -> List[dict]
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from filelock import FileLock

from .registry import get_agent

# ── Paths ────────────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
STATE_DIR = PROJECT_ROOT / "agents" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

NODES_FILE = STATE_DIR / "nodes.json"
NODES_LOCK = STATE_DIR / "nodes.json.lock"
NODE_LOGS_DIR = STATE_DIR / "node_logs"
NODE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
NODE_CONNECTIONS_DIR = STATE_DIR / "node_connections"
NODE_CONNECTIONS_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_node_log(node: Dict[str, Any], message: str) -> None:
    log_file = node.get("log_file")
    if not log_file:
        return
    try:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"[{_log_now()}] {message}\n")
    except Exception:
        pass


def _load_nodes(timeout: float = 10.0) -> List[Dict[str, Any]]:
    if not NODES_FILE.exists():
        return []
    with FileLock(str(NODES_LOCK), timeout=timeout):
        try:
            txt = NODES_FILE.read_text(encoding="utf-8")
            return json.loads(txt) if txt.strip() else []
        except Exception:
            return []


def _save_nodes(nodes: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(nodes, ensure_ascii=False, indent=2)
    with FileLock(str(NODES_LOCK), timeout=timeout):
        tmp = NODES_FILE.with_suffix(NODES_FILE.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, NODES_FILE)


def _upsert_node(node: Dict[str, Any]) -> None:
    nodes = _load_nodes()
    for i, n in enumerate(nodes):
        if n.get("node_id") == node.get("node_id"):
            nodes[i] = {**n, **node}
            _save_nodes(nodes)
            return
    nodes.append(node)
    _save_nodes(nodes)


def update_node(node_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update fields on a node record.  Called by the node_runner subprocess."""
    nodes = _load_nodes()
    for i, n in enumerate(nodes):
        if n.get("node_id") == node_id:
            merged = {**n, **updates}
            nodes[i] = merged
            _save_nodes(nodes)
            prev_status = str(n.get("status") or "")
            next_status = str(merged.get("status") or "")
            if prev_status != next_status and next_status:
                details = []
                if merged.get("finished_at"):
                    details.append(f"finished_at={merged.get('finished_at')}")
                if merged.get("exit_code") is not None:
                    details.append(f"exit_code={merged.get('exit_code')}")
                if merged.get("error"):
                    details.append(f"error={merged.get('error')}")
                detail_text = f" ({', '.join(details)})" if details else ""
                _append_node_log(merged, f"[node_state] {prev_status or 'unknown'} -> {next_status}{detail_text}")
            return merged
    return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return True
    return True


def _sync_status(node: Dict[str, Any]) -> Dict[str, Any]:
    """If a node is marked running/starting/stopping but its PID is gone, mark it stopped."""
    if node.get("status") in ("running", "starting", "stopping"):
        pid = int(node.get("pid") or 0)
        if pid > 0 and not _pid_exists(pid):
            updates = {"status": "stopped", "finished_at": _utc_now_iso(), "exit_code": -1}
            patched = update_node(node["node_id"], updates)
            return patched if patched else {**node, **updates}
    return node


# ── Public API ────────────────────────────────────────────────────────────────

def list_nodes() -> List[Dict[str, Any]]:
    """Return all nodes, auto-syncing statuses for dead processes."""
    nodes = _load_nodes()
    return [_sync_status(n) for n in nodes]


def get_node(node_id: str) -> Optional[Dict[str, Any]]:
    for n in _load_nodes():
        if n.get("node_id") == node_id:
            return _sync_status(n)
    return None


def get_running_nodes_for_agent(agent_id: str) -> List[Dict[str, Any]]:
    return [n for n in list_nodes() if n.get("agent_id") == agent_id and n.get("status") == "running"]


def start_node(
    agent_id: str,
    workspace: Optional[str] = None,
    label: Optional[str] = None,
    is_default: bool = False,
) -> str:
    """Launch a new agent node subprocess.  Returns node_id."""
    spec = get_agent(agent_id)
    if not spec:
        raise ValueError(f"Unknown agent: {agent_id}")
    if getattr(spec, "is_remote", False):
        raise ValueError("Cannot start a node for a remote agent")

    node_id = str(uuid4())
    log_file = NODE_LOGS_DIR / f"node_{node_id}.log"

    # Resolve workspace name → absolute path
    abs_workspace: Optional[str] = None
    if workspace:
        try:
            from common.workspace import create_workspace_folder
            abs_workspace = str(create_workspace_folder(workspace))
        except Exception:
            abs_workspace = None

    cmd = [
        sys.executable, "-m", "agents.node_runner",
        "--node-id", node_id,
        "--agent-id", agent_id,
        "--log-file", str(log_file),
    ]
    if abs_workspace:
        cmd.extend(["--workspace", abs_workspace])

    env = os.environ.copy()
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    # Open in binary mode so the fd can be safely inherited by the child.
    # Close the parent's handle immediately after Popen — the child keeps its own copy.
    with open(log_file, "wb") as log_fh:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )

    node_rec: Dict[str, Any] = {
        "node_id": node_id,
        "agent_id": agent_id,
        "workspace": workspace,
        "label": label or agent_id,
        "is_default": is_default,
        "pid": proc.pid,
        "status": "starting",
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "exit_code": None,
        "error": None,
        "log_file": str(log_file),
    }
    _upsert_node(node_rec)
    _append_node_log(
        node_rec,
        (
            f"[node_start] agent={agent_id} pid={proc.pid} "
            f"workspace={workspace or '—'} label={label or agent_id}"
        ),
    )
    return node_id


def stop_node(node_id: str) -> bool:
    """Send SIGTERM to a running node.  Returns True if signal was sent."""
    node = get_node(node_id)
    if not node or node.get("status") not in ("running", "starting"):
        return False

    pid = int(node.get("pid") or 0)
    if pid <= 0:
        _append_node_log(node, "[node_stop] stop requested but pid is missing/invalid")
        return False

    _append_node_log(node, f"[node_stop] stop requested pid={pid}")
    sent = False
    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            sent = True
        except Exception:
            try:
                import ctypes
                PROCESS_TERMINATE = 0x0001
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    sent = True
            except Exception:
                pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
            sent = True
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                sent = True
            except Exception:
                pass

    if sent:
        _append_node_log(node, f"[node_stop] stop signal sent pid={pid}")
        update_node(node_id, {"status": "stopping"})
    else:
        _append_node_log(node, f"[node_stop] failed to send stop signal pid={pid}")
    return sent


def delete_node(node_id: str) -> bool:
    """Remove a stopped/failed node record.  Returns True if removed."""
    nodes = _load_nodes()
    original = len(nodes)
    nodes = [
        n for n in nodes
        if not (n.get("node_id") == node_id and n.get("status") not in ("running", "starting"))
    ]
    if len(nodes) < original:
        _save_nodes(nodes)
        return True
    return False


def ensure_default_node() -> Optional[str]:
    """Start the default orchestrator node if none is running.

    Returns the new node_id, or None if already running / agent unavailable.
    """
    spec = get_agent("orchestrator")
    if not spec or getattr(spec, "is_remote", False):
        return None  # orchestrator not registered or is remote

    running = get_running_nodes_for_agent("orchestrator")
    if running:
        return None  # already up

    try:
        return start_node("orchestrator", label="orchestrator-default", is_default=True)
    except Exception as e:
        print(f"[node_manager] Failed to start default orchestrator node: {e}")
        return None


# ── Expose / External access ──────────────────────────────────────────────────

def expose_node(node_id: str) -> Optional[Dict[str, Any]]:
    """Generate an access token and mark the node as externally exposed.

    Returns the updated node record, or None if not found.
    """
    node = get_node(node_id)
    if not node:
        return None
    token = secrets.token_hex(32)
    updates: Dict[str, Any] = {
        "is_exposed": True,
        "expose_token": token,
        "exposed_at": _utc_now_iso(),
    }
    updated = update_node(node_id, updates)
    if updated:
        _append_node_log(updated, f"[expose] node exposed, token={token[:8]}…")
    return updated


def unexpose_node(node_id: str) -> bool:
    """Remove the external access token and mark the node as not exposed."""
    node = get_node(node_id)
    if not node:
        return False
    updates: Dict[str, Any] = {
        "is_exposed": False,
        "expose_token": None,
        "exposed_at": None,
    }
    updated = update_node(node_id, updates)
    if updated:
        _append_node_log(updated, "[expose] node unexposed")
    return updated is not None


def get_node_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Find an exposed node by its access token."""
    if not token:
        return None
    for n in list_nodes():
        if n.get("is_exposed") and n.get("expose_token") == token:
            return n
    return None


# ── Connection logging ────────────────────────────────────────────────────────

def _connections_file(node_id: str) -> Path:
    return NODE_CONNECTIONS_DIR / f"{node_id}.json"


def _connections_lock(node_id: str) -> str:
    return str(NODE_CONNECTIONS_DIR / f"{node_id}.json.lock")


def log_connection(node_id: str, record: Dict[str, Any]) -> None:
    """Append a connection record to the node's connection history."""
    cf = _connections_file(node_id)
    lock_path = _connections_lock(node_id)
    with FileLock(lock_path, timeout=10.0):
        existing: List[Dict[str, Any]] = []
        if cf.exists():
            try:
                existing = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append(record)
        # Keep last 500 connection records per node
        if len(existing) > 500:
            existing = existing[-500:]
        cf.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def get_connections(node_id: str) -> List[Dict[str, Any]]:
    """Return connection history for a node, newest first."""
    cf = _connections_file(node_id)
    if not cf.exists():
        return []
    try:
        records = json.loads(cf.read_text(encoding="utf-8"))
        return list(reversed(records))
    except Exception:
        return []
