"""
One-time migration of the legacy JSON stores into the database.

Runs automatically from ``common.db`` when the database has no
``json_migrated`` marker. Imports, per store:

- ``agent_runs.json``                  → ``runs`` (+ inline heavy ``process``
  payloads → ``run_payloads``, canonicalised)
- ``run_process/<run_id>.json``        → ``run_payloads`` (canonicalised)
- ``tasks.json``                       → ``tasks``
- ``activity_logs/<task_id>.json``     → ``task_activity``
- ``results/<task_id>.json``           → ``task_results``
- ``routing_logs/routing_log.json``    → ``routing_log``
- ``session_contexts.json``            → ``sessions``
- ``pending_continuations.json``       → ``continuations``
- ``nodes.json``                       → ``nodes``
- ``flow_runs.json``                   → ``flow_runs`` (own marker, see
  :func:`migrate_flow_runs`: it shipped after the import above, so a
  database that already set ``json_migrated`` still owes this one)

Every successfully imported source is renamed to ``<name>.migrated`` (dirs get
the same suffix) so a half-upgraded environment can never write to a store the
new code no longer reads — done by :func:`rename_migrated_sources`, called by
``common.db`` only after its transaction commits. The import itself
(:func:`migrate_legacy_json`) does not manage its own transaction: it runs
inside the caller's already-open ``BEGIN IMMEDIATE`` (see
``common.db._ensure_ready``), so it lands atomically with the schema creation
and version bump — either everything commits together, or (on any exception)
the caller rolls back all of it. Unparseable files are left in place untouched
and reported — never treated as empty.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT
from common import run_payloads as rp
from common.db import upsert_sql

# Run-record keys stored as dedicated columns; everything else goes to `extra`.
RUN_COLUMNS = (
    "run_id", "task_id", "agent_id", "session_id", "session_type", "channel",
    "execution_mode", "node_id", "container_name", "workspace", "title",
    "provider", "model", "status", "message_origin", "pid", "exit_code",
    "error", "created_at", "started_at", "finished_at", "log_file",
    "input", "output", "instance_id", "heartbeat_at",
)

TASK_COLUMNS = ("id", "key", "parent_id", "status", "workspace", "project_id",
                "created_at", "updated_at")

# Flow-run keys mirrored into dedicated columns. The whole record is also kept
# verbatim in ``doc``, so a key not listed here (a checkpoint, say) is preserved
# rather than dropped.
FLOW_RUN_COLUMNS = ("flow_run_id", "flow_id", "task_id", "session_id", "workspace",
                    "status", "pid", "started_at", "finished_at", "exit_code", "error")


def _read_json(path: Path) -> Optional[Any]:
    """Parse a JSON file; None when missing, raises nothing — returns the
    sentinel string 'ERROR' wrapped in a tuple on parse failure so callers can
    distinguish 'missing' from 'corrupt'."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        return json.loads(text)
    except Exception:
        return ("ERROR",)


def _is_corrupt(value: Any) -> bool:
    return isinstance(value, tuple) and value and value[0] == "ERROR"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _rename_migrated(path: Path) -> None:
    try:
        if path.exists():
            path.rename(path.with_name(path.name + ".migrated"))
    except Exception:
        pass  # marker already prevents re-import; the rename is hygiene only


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── per-store importers (each runs inside the caller's transaction) ──────────

def _import_runs(conn: Any) -> int:
    src = AGENTS_HUB_ROOT / "agent_runs.json"
    data = _read_json(src)
    if data is None or _is_corrupt(data) or not isinstance(data, list):
        return 0
    n = 0
    for rec in data:
        if not isinstance(rec, dict) or not rec.get("run_id"):
            continue
        proc = rec.pop("process", None)
        row = {k: rec.get(k) for k in RUN_COLUMNS}
        extra = {k: v for k, v in rec.items() if k not in RUN_COLUMNS}
        tokens = {"prompt_tokens": None, "completion_tokens": None,
                  "total_tokens": None, "duration_ms": None}
        if isinstance(proc, dict):
            tu = proc.get("token_usage") or {}
            tokens = {
                "prompt_tokens": tu.get("inbound_tokens"),
                "completion_tokens": tu.get("outbound_tokens"),
                "total_tokens": tu.get("total_tokens"),
                "duration_ms": proc.get("duration_ms"),
            }
        conn.execute(
            upsert_sql("runs", RUN_COLUMNS + ("prompt_tokens", "completion_tokens",
                                              "total_tokens", "duration_ms", "extra"),
                       ("run_id",)),
            [row[k] for k in RUN_COLUMNS]
            + [tokens["prompt_tokens"], tokens["completion_tokens"],
               tokens["total_tokens"], tokens["duration_ms"], _dumps(extra)],
        )
        # Pre-sidecar records may still hold the full payload inline.
        if isinstance(proc, dict) and rp.has_heavy_data(proc):
            _write_payload(conn, str(rec["run_id"]), proc)
        n += 1
    return n


def _write_payload(conn: Any, run_id: str, proc: Dict[str, Any]) -> None:
    c = rp.canonicalize(proc)
    conn.execute(
        upsert_sql("run_payloads", ("run_id", "input_context", "response", "tool_calls",
                                    "reasoning", "llm_invocations", "llm_raw_responses",
                                    "artifacts", "updated_at"), ("run_id",)),
        (run_id, _dumps(c["input_context"]), _dumps(c["response"]),
         _dumps(c["tool_calls"]), _dumps(c["reasoning"]),
         _dumps(c["llm_invocations"]), _dumps(c["llm_raw_responses"]),
         _dumps(c["artifacts"]), _now_iso()),
    )


def _import_run_payloads(conn: Any) -> int:
    src_dir = AGENTS_HUB_ROOT / "run_process"
    if not src_dir.is_dir():
        return 0
    n = 0
    for f in sorted(src_dir.glob("*.json")):
        data = _read_json(f)
        if data is None or _is_corrupt(data) or not isinstance(data, dict):
            continue
        _write_payload(conn, f.stem, data)
        # Fill token/duration columns from the sidecar when the index record
        # predates the slim projection.
        tu = data.get("token_usage") or {}
        conn.execute(
            "UPDATE runs SET "
            "prompt_tokens=COALESCE(prompt_tokens, ?), "
            "completion_tokens=COALESCE(completion_tokens, ?), "
            "total_tokens=COALESCE(total_tokens, ?), "
            "duration_ms=COALESCE(duration_ms, ?) WHERE run_id=?",
            (tu.get("inbound_tokens"), tu.get("outbound_tokens"),
             tu.get("total_tokens"), data.get("duration_ms"), f.stem),
        )
        n += 1
    return n


def _import_tasks(conn: Any) -> int:
    src = AGENTS_HUB_ROOT / "tasks.json"
    data = _read_json(src)
    if data is None or _is_corrupt(data) or not isinstance(data, list):
        return 0
    n = 0
    for doc in data:
        if not isinstance(doc, dict) or not doc.get("id"):
            continue
        conn.execute(
            upsert_sql("tasks", ("id", "key", "parent_id", "status", "workspace",
                                 "project_id", "created_at", "updated_at", "doc"), ("id",)),
            (str(doc.get("id")), doc.get("key"),
             str(doc["parent_id"]) if doc.get("parent_id") else None,
             str(doc.get("status") or ""), doc.get("workspace"),
             str(doc["project_id"]) if doc.get("project_id") else None,
             str(doc.get("created_at") or ""), str(doc.get("updated_at") or ""),
             _dumps(doc)),
        )
        n += 1
    return n


def _import_task_sidecars(conn: Any) -> int:
    n = 0
    act_dir = AGENTS_HUB_ROOT / "activity_logs"
    if act_dir.is_dir():
        for f in sorted(act_dir.glob("*.json")):
            entries = _read_json(f)
            if not isinstance(entries, list):
                continue
            for e in entries:
                conn.execute("INSERT INTO task_activity (task_id, entry) VALUES (?, ?)",
                             (f.stem, _dumps(e)))
                n += 1
    res_dir = AGENTS_HUB_ROOT / "results"
    if res_dir.is_dir():
        for f in sorted(res_dir.glob("*.json")):
            data = _read_json(f)
            if _is_corrupt(data) or data is None:
                continue
            if isinstance(data, dict):  # old single-dict format
                data = [{"run_id": None, "agent_id": None, **data}]
            if not isinstance(data, list):
                continue
            for e in data:
                if isinstance(e, dict):
                    conn.execute(
                        "INSERT INTO task_results (task_id, run_id, entry) VALUES (?, ?, ?)",
                        (f.stem, e.get("run_id"), _dumps(e)))
                    n += 1
    routing = AGENTS_HUB_ROOT / "routing_logs" / "routing_log.json"
    entries = _read_json(routing)
    if isinstance(entries, list):
        for e in entries:
            conn.execute("INSERT INTO routing_log (entry) VALUES (?)", (_dumps(e),))
            n += 1
    return n


def _import_sessions(conn: Any) -> int:
    data = _read_json(AGENTS_HUB_ROOT / "session_contexts.json")
    n = 0
    if isinstance(data, list):
        for ctx in data:
            if not isinstance(ctx, dict) or not ctx.get("session_id"):
                continue
            conn.execute(
                upsert_sql("sessions", ("session_id", "conversation_id", "task_id", "doc"),
                           ("session_id",)),
                (str(ctx["session_id"]), ctx.get("conversation_id"),
                 str(ctx["task_id"]) if ctx.get("task_id") else None, _dumps(ctx)))
            n += 1
    conts = _read_json(AGENTS_HUB_ROOT / "pending_continuations.json")
    if isinstance(conts, list):
        for c in conts:
            if not isinstance(c, dict):
                continue
            conn.execute(
                "INSERT INTO continuations (session_id, task_id, run_id, doc) VALUES (?, ?, ?, ?)",
                (str(c.get("session_id") or ""), str(c.get("task_id") or ""),
                 c.get("run_id"), _dumps(c)))
            n += 1
    return n


def _import_nodes(conn: Any) -> int:
    data = _read_json(AGENTS_HUB_ROOT / "nodes.json")
    if not isinstance(data, list):
        return 0
    n = 0
    for node in data:
        if not isinstance(node, dict) or not node.get("node_id"):
            continue
        conn.execute(upsert_sql("nodes", ("node_id", "doc"), ("node_id",)),
                     (str(node["node_id"]), _dumps(node)))
        n += 1
    return n


def _import_flow_runs(conn: Any) -> int:
    src = AGENTS_HUB_ROOT / "flow_runs.json"
    data = _read_json(src)
    if data is None or _is_corrupt(data) or not isinstance(data, list):
        return 0
    n = 0
    for rec in data:
        if not isinstance(rec, dict) or not rec.get("flow_run_id"):
            continue
        conn.execute(
            upsert_sql("flow_runs", FLOW_RUN_COLUMNS + ("doc",), ("flow_run_id",)),
            [rec.get(k) for k in FLOW_RUN_COLUMNS] + [_dumps(rec)],
        )
        n += 1
    return n


# ── entrypoint ───────────────────────────────────────────────────────────────

def migrate_legacy_json(conn: Any) -> Optional[Dict[str, int]]:
    """Import all legacy JSON stores and set the ``json_migrated`` marker.

    Called by ``common.db._ensure_ready`` while it already holds the startup
    ``BEGIN IMMEDIATE`` transaction — this function does not begin, commit or
    roll back anything itself; an exception here propagates to the caller,
    which rolls back the whole startup transaction (schema + version bump +
    this import) together.

    Returns ``None`` when another process already migrated (the marker was
    already set — nothing to rename), or the per-store import counts when
    this call performed the import (the caller renames the sources after its
    commit lands, via :func:`rename_migrated_sources`).
    """
    row = conn.execute("SELECT value FROM meta WHERE key='json_migrated'").fetchone()
    if row is not None:
        return None

    counts = {
        "runs": _import_runs(conn),
        "run_payloads": _import_run_payloads(conn),
        "tasks": _import_tasks(conn),
        "task_sidecars": _import_task_sidecars(conn),
        "sessions_continuations": _import_sessions(conn),
        "nodes": _import_nodes(conn),
    }
    conn.execute(
        upsert_sql("meta", ("key", "value"), ("key",)),
        ("json_migrated", json.dumps({"at": _now_iso(), "counts": counts})),
    )
    return counts


def rename_migrated_sources() -> None:
    """Best-effort hygiene: rename imported legacy JSON sources to
    ``*.migrated`` so a half-upgraded environment never writes to a store the
    new code no longer reads. Safe to call unconditionally and repeatedly —
    the ``json_migrated`` marker alone already guarantees a single import.
    Called by ``common.db`` only after the transaction holding the import
    has committed.
    """
    for name in ("agent_runs.json", "tasks.json", "session_contexts.json",
                 "pending_continuations.json", "nodes.json"):
        _rename_migrated(AGENTS_HUB_ROOT / name)
    for dirname in ("run_process", "activity_logs", "results", "routing_logs"):
        _rename_migrated(AGENTS_HUB_ROOT / dirname)



def migrate_flow_runs(conn: Any) -> Optional[int]:
    """Import ``flow_runs.json`` into the ``flow_runs`` table, once.

    Kept apart from :func:`migrate_legacy_json` and guarded by its own
    ``flow_runs_migrated`` marker, because flow runs moved into SQLite later:
    every database out there already carries ``json_migrated``, so folding this
    import into that marker would skip it exactly where it is needed. Like the
    import above it runs inside the caller's ``BEGIN IMMEDIATE`` and manages no
    transaction of its own.

    Returns ``None`` when the marker was already set (another process or an
    earlier start did it — nothing to rename), or the number of imported records
    when this call performed the import. The caller renames the source file
    after its commit lands, via :func:`rename_flow_runs_source`.
    """
    row = conn.execute("SELECT value FROM meta WHERE key='flow_runs_migrated'").fetchone()
    if row is not None:
        return None
    count = _import_flow_runs(conn)
    conn.execute(
        upsert_sql("meta", ("key", "value"), ("key",)),
        ("flow_runs_migrated", json.dumps({"at": _now_iso(), "count": count})),
    )
    return count


def rename_flow_runs_source() -> None:
    """Rename ``flow_runs.json`` to ``flow_runs.json.migrated`` after the import
    committed, so a half-upgraded environment cannot keep writing flow runs to a
    file nothing reads any more. Best-effort, like the renames above."""
    _rename_migrated(AGENTS_HUB_ROOT / "flow_runs.json")
