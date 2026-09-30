"""
0029: nodes fold into instances (instances/carrier.py, docs/instances.md).

A node used to be a record of its own (``nodes``) carrying an instance; the
instance is now the only record, and the process it runs in is described on
the instance row itself. This migration:

- adds ``conversation_id`` to ``instance_inbox`` and ``runs``: a resident
  instance answers several conversations, each with its own history (NULL is
  the main conversation the instance page writes to);
- creates ``instance_carriers``, one row per process an instance has had
  (a restart replaces the process, not the instance);
- moves every node into its instance: the node's process fields, inputs and
  publication land on the instance row (kind ``resident``, carrier fields in
  ``extra``), a node that never had an instance gets one, the node's runs are
  linked to the instance, and its connection history moves from the
  ``node_connections`` store to ``instance_connections`` under the instance id;
- drops ``nodes``.

A node process still running when this applies keeps running (its row now
reads as a resident instance with the same pid or container) and can be
stopped from the instance page.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from common.migrations import add_column_if_missing, execute_sql, table_exists

_DDL = """
CREATE TABLE IF NOT EXISTS instance_carriers (
    carrier_id      TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL,
    mode            TEXT,
    host            TEXT,
    pid             INTEGER,
    container_name  TEXT,
    log_file        TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    exit_code       INTEGER,
    error           TEXT,
    reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_instance_carriers_instance ON instance_carriers(instance_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_instance_conversation ON runs(instance_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_inbox_conversation ON instance_inbox(instance_id, conversation_id, delivered_at);
"""

_INSTANCE_COLUMNS = (
    "instance_id", "agent_id", "workspace", "project_id", "kind", "state",
    "label", "session_id", "node_id", "container_name", "pid",
    "current_run_id", "task_id", "provider", "model",
    "created_at", "started_at", "last_activity_at", "finished_at",
    "archived_at", "runs_count", "total_tokens", "total_duration_ms",
    "last_activity", "error",
)

_LIVE_NODE = ("starting", "running", "stopping")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(text: Any, default: Any) -> Any:
    if text is None or text == "":
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _instance_for(conn: Any, node_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM instances WHERE node_id = ? ORDER BY created_at DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    if row is None:
        return None
    rec = {k: row[k] for k in _INSTANCE_COLUMNS}
    extra = _loads(row["extra"], {})
    if isinstance(extra, dict):
        rec.update(extra)
    return rec


def _write_instance(conn: Any, rec: Dict[str, Any], exists: bool) -> None:
    data = dict(rec)
    cols = {k: data.pop(k, None) for k in _INSTANCE_COLUMNS}
    data.pop("is_live", None)
    extra = json.dumps(data, default=str)
    if exists:
        sets = ", ".join(f"{k} = ?" for k in _INSTANCE_COLUMNS if k != "instance_id")
        conn.execute(
            f"UPDATE instances SET {sets}, extra = ? WHERE instance_id = ?",
            [cols[k] for k in _INSTANCE_COLUMNS if k != "instance_id"] + [extra, cols["instance_id"]],
        )
    else:
        conn.execute(
            f"INSERT INTO instances ({', '.join(_INSTANCE_COLUMNS)}, extra) "
            f"VALUES ({', '.join('?' * (len(_INSTANCE_COLUMNS) + 1))})",
            [cols[k] for k in _INSTANCE_COLUMNS] + [extra],
        )


def _state_for(node_status: str, previous: Optional[str]) -> str:
    if node_status in ("starting",):
        return "starting"
    if node_status in ("running", "stopping"):
        return previous if previous in ("active", "standby") else "standby"
    if node_status == "failed":
        return "failed"
    return "stopped"


def _move_connections(conn: Any, node_id: str, instance_id: str) -> None:
    if not _documents_exist(conn):
        return
    row = conn.execute(
        "SELECT doc, created_at, updated_at FROM documents WHERE store = ? AND key = ?",
        ("node_connections", node_id),
    ).fetchone()
    if row is None:
        return
    exists = conn.execute(
        "SELECT 1 FROM documents WHERE store = ? AND key = ?",
        ("instance_connections", instance_id),
    ).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO documents (store, key, seq, doc, created_at, updated_at) VALUES "
            "(?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM documents WHERE store = ?), ?, ?, ?)",
            ("instance_connections", instance_id, "instance_connections", row["doc"],
             row["created_at"], row["updated_at"]),
        )
    conn.execute("DELETE FROM documents WHERE store = ? AND key = ?", ("node_connections", node_id))


def _documents_exist(conn: Any) -> bool:
    try:
        conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001 - no documents table: nothing to move
        return False


def _move_node(conn: Any, dialect: str, node: Dict[str, Any]) -> None:
    node_id = str(node.get("node_id") or "")
    if not node_id:
        return
    existing = _instance_for(conn, node_id)
    status = str(node.get("status") or "stopped")
    mode = "docker" if node.get("execution_mode") == "docker" else "local"
    now = _now()
    rec: Dict[str, Any] = dict(existing or {})
    if not existing:
        rec.update({
            "instance_id": "inst_" + uuid.uuid4().hex[:16],
            "agent_id": node.get("agent_id"),
            "workspace": node.get("workspace"),
            "created_at": node.get("started_at") or now,
            "runs_count": 0, "total_tokens": 0, "total_duration_ms": 0,
        })
    live = status in _LIVE_NODE
    rec.update({
        "kind": "resident",
        "state": _state_for(status, (existing or {}).get("state")),
        "label": rec.get("label") or node.get("label") or node.get("agent_id"),
        "node_id": node_id,
        "pid": node.get("pid"),
        "container_name": node.get("container_name"),
        "started_at": node.get("started_at") or rec.get("started_at") or now,
        "finished_at": None if live else (node.get("finished_at") or rec.get("finished_at")),
        "last_activity_at": rec.get("last_activity_at") or node.get("started_at") or now,
        "carrier_mode": mode,
        "carrier_host": node.get("host"),
        "carrier_status": status if status in _LIVE_NODE + ("stopped", "failed") else "stopped",
        "carrier_log_file": node.get("log_file"),
        "carrier_started_at": node.get("started_at"),
        "carrier_finished_at": node.get("finished_at"),
        "carrier_exit_code": node.get("exit_code"),
        "carrier_error": node.get("error"),
        # A worker node took tasks; a service node only answered HTTP.
        "take_tasks": node.get("node_type") != "service",
        # Node loops ran one thing at a time; keep that for a migrated copy.
        "concurrency": 1,
        "http_port": node.get("http_port"),
        "http_host_port": node.get("http_host_port"),
        "http_url": node.get("http_url"),
        "is_exposed": bool(node.get("is_exposed")),
        "expose_token": node.get("expose_token"),
        "exposed_at": node.get("exposed_at"),
        "inbound_secret": node.get("inbound_secret") or None,
        "environment_id": node.get("environment_id"),
        "environment_name": node.get("environment_name"),
        "migrated_from_node": node_id,
    })
    if node.get("error") and not live:
        rec["error"] = node.get("error")
    _write_instance(conn, rec, exists=existing is not None)
    instance_id = rec["instance_id"]
    conn.execute(
        "INSERT INTO instance_carriers (carrier_id, instance_id, mode, host, pid, container_name, "
        "log_file, started_at, finished_at, exit_code, error, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("car_" + uuid.uuid4().hex[:16], instance_id, mode, node.get("host"), node.get("pid"),
         node.get("container_name"), node.get("log_file"), node.get("started_at"),
         None if live else (node.get("finished_at") or now), node.get("exit_code"),
         node.get("error"), "migrated from node"),
    )
    conn.execute(
        "UPDATE runs SET instance_id = ? WHERE node_id = ? AND instance_id IS NULL",
        (instance_id, node_id),
    )
    _move_connections(conn, node_id, instance_id)


def upgrade(conn: Any, dialect: str) -> None:
    add_column_if_missing(conn, dialect, "instance_inbox", "conversation_id", "TEXT")
    add_column_if_missing(conn, dialect, "runs", "conversation_id", "TEXT")
    execute_sql(conn, dialect, _DDL)
    if not table_exists(conn, dialect, "nodes"):
        return
    for row in conn.execute("SELECT node_id, doc FROM nodes").fetchall():
        node = _loads(row["doc"], {})
        if isinstance(node, dict):
            node.setdefault("node_id", row["node_id"])
            _move_node(conn, dialect, node)
    conn.execute("DROP TABLE nodes")
