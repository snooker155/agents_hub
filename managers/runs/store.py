"""
Persistence for run records: the ``runs`` table and its ``run_payloads``
sidecar table.

This is the only module that reads or writes those two tables. It owns the
row ↔ record mapping (standard columns + the ``extra`` JSON bag + the slim
``process`` stats projection), the single atomic read-modify-write
(:func:`_apply`), the paginated queries the dashboard lists run from, and the
heavy structured payload accessors.

Record shape and the public API are documented in :mod:`managers.run_manager`,
which re-exports everything here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from common import db
from common import run_payloads as rp
from common.db_migrate import RUN_COLUMNS
from common.paths import AGENTS_HUB_ROOT
from common.session_broker import notify_change

from .notifications import (_notify_task_run_finished, _publish_run_delta,
                            _sync_instance)

AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)

# Legacy sidecar dir — only read as a fallback for payloads written by a
# not-yet-restarted old process; new payloads live in the run_payloads table.
RUN_PROCESS_DIR = AGENTS_HUB_ROOT / "run_process"


_TOKEN_COLUMNS = ("prompt_tokens", "cached_prompt_tokens", "completion_tokens",
                  "total_tokens", "duration_ms")


# -------------------- Private state helpers --------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row) -> Dict[str, Any]:
    """Rebuild the public run-record dict from a ``runs`` row: standard columns
    (always present, possibly None) + merged extra keys + the slim ``process``
    stats projection when the run has recorded any."""
    rec: Dict[str, Any] = {k: row[k] for k in RUN_COLUMNS}
    extra = db.loads(row["extra"], {}) or {}
    if isinstance(extra, dict):
        rec.update(extra)
    has_stats = row["duration_ms"] is not None or any(
        row[k] is not None for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    if has_stats:
        rec["process"] = {
            "token_usage": {
                "inbound_tokens": int(row["prompt_tokens"] or 0),
                "outbound_tokens": int(row["completion_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "cached_tokens": int(row["cached_prompt_tokens"] or 0),
            },
            "duration_ms": int(row["duration_ms"] or 0),
        }
    return rec


def _write_payload_row(conn, run_id: str, proc: Dict[str, Any]) -> None:
    """Canonicalize and store a heavy process payload for a run."""
    c = rp.canonicalize(proc)
    conn.execute(
        db.upsert_sql("run_payloads", ("run_id", "input_context", "response", "tool_calls",
                                       "reasoning", "llm_invocations", "llm_raw_responses",
                                       "artifacts", "updated_at"), ("run_id",)),
        (str(run_id), db.dumps(c["input_context"]), db.dumps(c["response"]),
         db.dumps(c["tool_calls"]), db.dumps(c["reasoning"]),
         db.dumps(c["llm_invocations"]), db.dumps(c["llm_raw_responses"]),
         db.dumps(c["artifacts"]), _utc_now_iso()),
    )


def _write_record(conn, merged: Dict[str, Any], tokens: Dict[str, Any]) -> None:
    """Persist a full merged record dict into its ``runs`` row."""
    data = dict(merged)
    data.pop("process", None)
    cols = {k: data.pop(k, None) for k in RUN_COLUMNS}
    conn.execute(
        db.upsert_sql("runs", RUN_COLUMNS + _TOKEN_COLUMNS + ("extra",), ("run_id",)),
        [cols[k] for k in RUN_COLUMNS]
        + [tokens.get(k) for k in _TOKEN_COLUMNS]
        + [db.dumps(data)],
    )


def _merge_tokens(row, proc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Existing token/duration columns, overridden by fields the new payload
    actually carries (a payload without usage never zeroes recorded stats)."""
    tokens = {k: (row[k] if row is not None else None) for k in _TOKEN_COLUMNS}
    if isinstance(proc, dict):
        tu = proc.get("token_usage") or {}
        if tu:
            tokens["prompt_tokens"] = int(tu.get("inbound_tokens") or tu.get("prompt_tokens") or 0)
            tokens["completion_tokens"] = int(tu.get("outbound_tokens") or tu.get("completion_tokens") or 0)
            tokens["total_tokens"] = int(tu.get("total_tokens") or 0)
            tokens["cached_prompt_tokens"] = int(tu.get("cached_tokens") or 0)
        if proc.get("duration_ms") is not None:
            tokens["duration_ms"] = int(proc.get("duration_ms") or 0)
    return tokens


def _apply(run_id: str, updates: Dict[str, Any], *, insert_if_missing: bool
           ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Atomic read-modify-write of one run record.

    Merge semantics match the old JSON store ({**existing, **updates}); the
    whole operation runs in one transaction so concurrent writers can never
    drop each other's fields. Returns (old_record, new_record).
    """
    updates = dict(updates or {})
    proc = updates.pop("process", None)
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
        old = _row_to_record(row) if row is not None else None
        if old is None and not insert_if_missing:
            return None, None
        base = dict(old or {})
        base.pop("process", None)
        merged = {**base, **updates}
        merged["run_id"] = str(run_id)
        tokens = _merge_tokens(row, proc if isinstance(proc, dict) else None)
        _write_record(conn, merged, tokens)
        if isinstance(proc, dict) and rp.has_heavy_data(proc):
            _write_payload_row(conn, str(run_id), proc)
        new = dict(merged)
        if tokens["duration_ms"] is not None or any(
            tokens[k] is not None for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            new["process"] = {
                "token_usage": {
                    "inbound_tokens": int(tokens["prompt_tokens"] or 0),
                    "outbound_tokens": int(tokens["completion_tokens"] or 0),
                    "total_tokens": int(tokens["total_tokens"] or 0),
                    "cached_tokens": int(tokens["cached_prompt_tokens"] or 0),
                },
                "duration_ms": int(tokens["duration_ms"] or 0),
            }
    return old, new


# -------------------- Paginated queries --------------------
# The Messages list and every instance journal read through here. They must not
# call load_runs(): with a thousand live copies the run table is the biggest in
# the database, and filtering it in Python means loading all of it on every
# refresh of every open tab.

_RUN_LIST_COLUMNS = (
    "run_id", "task_id", "agent_id", "session_id", "session_type", "channel",
    "execution_mode", "node_id", "container_name", "workspace", "title",
    "provider", "model", "status", "message_origin", "pid", "exit_code",
    "error", "created_at", "started_at", "finished_at", "log_file",
    "input", "output", "instance_id", "heartbeat_at",
    "prompt_tokens", "cached_prompt_tokens", "completion_tokens", "total_tokens",
    "duration_ms", "extra",
)


def _list_row_to_record(row) -> Dict[str, Any]:
    """A run record for list views — same shape as ``_row_to_record`` but built
    from an explicit column list so the query never has to ``SELECT *``."""
    rec: Dict[str, Any] = {k: row[k] for k in RUN_COLUMNS}
    extra = db.loads(row["extra"], {}) or {}
    if isinstance(extra, dict):
        rec.update(extra)
    if row["duration_ms"] is not None or any(
        row[k] is not None for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        rec["process"] = {
            "token_usage": {
                "inbound_tokens": int(row["prompt_tokens"] or 0),
                "outbound_tokens": int(row["completion_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "cached_tokens": int(row["cached_prompt_tokens"] or 0),
            },
            "duration_ms": int(row["duration_ms"] or 0),
        }
    return rec


def query_runs(
    *,
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
    session_type: Optional[str] = None,
    exclude_session_type: Optional[str] = None,
    instance_id: Optional[str] = None,
    task_id: Optional[str] = None,
    node_id: Optional[str] = None,
    channel: Optional[str] = None,
    flow_run_id: Optional[str] = None,
    is_flow: Optional[bool] = None,
    flow_agent_ids: Sequence[str] = (),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    ascending: bool = False,
) -> Dict[str, Any]:
    """A filtered, ordered page of run records plus the total match count.

    Returns ``{items, total, limit, offset}``. Ordering is by ``started_at``
    (falling back to ``created_at``), newest first unless ``ascending``.
    """
    clauses: List[str] = []
    params: List[Any] = []

    if workspace:
        if workspace == "default":
            clauses.append("(workspace IS NULL OR workspace = '' OR workspace = 'default')")
        else:
            clauses.append("workspace = ?")
            params.append(workspace)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if session_id:
        clauses.append("session_id = ?")
        params.append(str(session_id))
    if instance_id:
        clauses.append("instance_id = ?")
        params.append(str(instance_id))
    if task_id:
        clauses.append("task_id = ?")
        params.append(str(task_id))
    if node_id:
        clauses.append("node_id = ?")
        params.append(str(node_id))
    if session_type:
        clauses.append("session_type = ?")
        params.append(str(session_type))
    elif exclude_session_type:
        clauses.append("COALESCE(session_type, '') != ?")
        params.append(str(exclude_session_type))
    if channel:
        clauses.append("channel = ?")
        params.append(channel)
    if flow_run_id:
        # A node run records the flow execution it belongs to in `extra`, not in
        # a column: it is the only grouping key that is not shared by every run
        # channel, and the run group adapters are the only readers of it.
        clauses.append(f"{db.json_text('extra', 'flow_run_id')} = ?")
        params.append(str(flow_run_id))
    if from_date:
        clauses.append("COALESCE(started_at, created_at) >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("COALESCE(started_at, created_at) <= ?")
        params.append(to_date)
    if q:
        clauses.append("(title LIKE ? OR input LIKE ? OR agent_id LIKE ? OR run_id LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if is_flow is not None:
        # A run is a flow run when it says so in `extra` or when its agent is one
        # of the flow pseudo-agents — the same rule the UI applied in Python.
        ids = list(flow_agent_ids or ())
        placeholders = ", ".join("?" * len(ids)) if ids else "NULL"
        expr = (f"({db.json_truthy('extra', 'is_flow')} "
                f"OR agent_id IN ({placeholders}))")
        clauses.append(expr if is_flow else f"NOT {expr}")
        params.extend(ids)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "ASC" if ascending else "DESC"
    conn = db.get_conn()
    total = conn.execute(f"SELECT COUNT(*) FROM runs{where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {', '.join(_RUN_LIST_COLUMNS)} FROM runs{where} "
        f"ORDER BY COALESCE(started_at, created_at) {order} LIMIT ? OFFSET ?",
        list(params) + [int(limit), int(offset)],
    ).fetchall()
    return {
        "items": [_list_row_to_record(r) for r in rows],
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
    }


def get_runs_by_ids(run_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch several run records in one query, keyed by run_id.

    For enriching a page of sessions with the runs they contain, instead of
    loading the whole run table to pick a handful out of it.
    """
    ids = [str(r) for r in run_ids if r]
    if not ids:
        return {}
    conn = db.get_conn()
    out: Dict[str, Dict[str, Any]] = {}
    # Chunked to stay under SQLite's bound-variable limit.
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        rows = conn.execute(
            f"SELECT {', '.join(_RUN_LIST_COLUMNS)} FROM runs "
            f"WHERE run_id IN ({', '.join('?' * len(chunk))})",
            chunk,
        ).fetchall()
        for row in rows:
            out[str(row["run_id"])] = _list_row_to_record(row)
    return out


def _session_status_from_counts(row) -> str:
    """Overall status of a session from its runs' status tally.

    Mirrors the rule the dashboard applied in Python — any running wins, then
    all-completed, then any failure, then all-stopped — but reads it off a
    grouped query so it never costs a scan of the run table.
    """
    if not row["total"]:
        return "pending"
    if row["n_running"]:
        return "running"
    if row["n_completed"] == row["total"]:
        return "completed"
    if row["n_failed"]:
        return "failed"
    if row["n_stopped"] == row["total"]:
        return "stopped"
    if row["n_pending"] == row["total"]:
        return "pending"
    last = str(row["last_status"] or "")
    if last == "stop":
        return "stopped"
    if last in ("awaiting_approval", "pending"):
        return "pending"
    return last or "unknown"


def session_run_stats(session_ids: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Per-session run tallies: ``{session_id: {status, agents, message_count,
    finished_at}}``.

    One grouped query over ``runs`` (served by ``idx_runs_session``) replaces
    loading every run record to derive what a session list shows.
    ``session_ids=None`` covers every session — used by the status filter, which
    has to rank the whole set before paging it.
    """
    params: List[Any] = []
    where = "WHERE session_id IS NOT NULL AND session_id != ''"
    ids = [str(s) for s in (session_ids or []) if s]
    if session_ids is not None:
        if not ids:
            return {}
        where += f" AND session_id IN ({', '.join('?' * len(ids))})"
        params.extend(ids)

    # The last status is taken from the newest run of each session via a window
    # function, so it stays exact alongside the other aggregates.
    rows = db.get_conn().execute(
        f"""
        SELECT session_id,
               COUNT(*)                                              AS total,
               {db.sum_if("status = 'running'")}                     AS n_running,
               {db.sum_if("status = 'completed'")}                   AS n_completed,
               {db.sum_if("status IN ('failed', 'error')")}          AS n_failed,
               {db.sum_if("status IN ('stop', 'stopped')")}          AS n_stopped,
               {db.sum_if("status IN ('pending', 'awaiting_approval')")} AS n_pending,
               MAX(finished_at)                                       AS finished_at,
               {db.group_concat("DISTINCT agent_id")}                 AS agents,
               (SELECT r2.status FROM runs r2
                 WHERE r2.session_id = runs.session_id
                 ORDER BY COALESCE(r2.started_at, r2.created_at) DESC LIMIT 1) AS last_status
          FROM runs {where}
         GROUP BY session_id
        """,
        params,
    ).fetchall()

    stats: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        stats[str(row["session_id"])] = {
            "status": _session_status_from_counts(row),
            "agents": [a for a in str(row["agents"] or "").split(",") if a],
            "message_count": int(row["total"] or 0),
            "finished_at": row["finished_at"],
            "running_count": int(row["n_running"] or 0),
        }
    return stats


def _load_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY COALESCE(created_at, started_at, ''), run_id").fetchall()
    return [_row_to_record(r) for r in rows]


def _save_runs(runs: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    """Replace the entire run list (bulk mutation compat path).

    Payload rows are kept for surviving run_ids and dropped for removed ones.
    """
    with db.transaction() as conn:
        keep_ids = {str(r.get("run_id")) for r in runs if r.get("run_id")}
        existing = {row["run_id"] for row in conn.execute("SELECT run_id FROM runs").fetchall()}
        for gone in existing - keep_ids:
            conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (gone,))
        conn.execute("DELETE FROM runs")
        for rec in runs:
            if not rec.get("run_id"):
                continue
            rec = dict(rec)
            proc = rec.pop("process", None)
            tokens = _merge_tokens(None, proc if isinstance(proc, dict) else None)
            _write_record(conn, rec, tokens)
            if isinstance(proc, dict) and rp.has_heavy_data(proc):
                _write_payload_row(conn, str(rec["run_id"]), proc)


def _upsert_run(run: Dict[str, Any]) -> None:
    run_id = str(run.get("run_id") or "")
    if not run_id:
        return
    old, new = _apply(run_id, run, insert_if_missing=True)
    if new is not None:
        _sync_instance(old, new)


def _export_run_finished(old: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Send a finished run to the optional OTel export target
    (common/otel_export.py). Guarded here rather than left to that module
    alone, so a missing or misconfigured export target is a no-op for run
    recording, the same guarantee _notify_task_run_finished gives."""
    try:
        from common.otel_export import export_run_finished
        export_run_finished(old, new)
    except Exception:
        # Export must never break run recording.
        pass


def _update_run(run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    old, new = _apply(str(run_id), updates, insert_if_missing=False)
    if new is None:
        return None
    _sync_instance(old, new)
    _notify_task_run_finished(old or {}, new)
    _export_run_finished(old or {}, new)
    return new


def touch_heartbeat(run_id: str, when: Optional[str] = None) -> Optional[str]:
    """Stamp ``heartbeat_at`` and return the run's current status, or None
    when there is no such run. One UPDATE, no merge, no notifications: this
    runs every few seconds for every live run and must cost nothing a tab
    can notice."""
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET heartbeat_at = ? WHERE run_id = ?",
                     (when or _utc_now_iso(), str(run_id)))
        row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
    return str(row["status"]) if row is not None and row["status"] is not None else None


def save_run_checkpoint(run_id: str, checkpoint: Dict[str, Any]) -> None:
    """Write the agent loop's checkpoint (agents/checkpoint.py) beside the
    run's payload, and note on the record that one exists."""
    now = _utc_now_iso()
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql("run_payloads", ("run_id", "checkpoint", "updated_at"), ("run_id",)),
            (str(run_id), db.dumps(checkpoint), now))
        row = conn.execute("SELECT extra FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
        if row is not None:
            extra = db.loads(row["extra"], {}) or {}
            extra["checkpoint_at"] = now
            extra["checkpoint_step"] = int(checkpoint.get("step") or 0)
            conn.execute("UPDATE runs SET extra = ? WHERE run_id = ?",
                         (db.dumps(extra), str(run_id)))


def load_run_checkpoint(run_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT checkpoint FROM run_payloads WHERE run_id = ?", (str(run_id),)).fetchone()
    if row is None:
        return None
    data = db.loads(row["checkpoint"], None)
    return data if isinstance(data, dict) else None


def clear_run_checkpoint(run_id: str) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE run_payloads SET checkpoint = NULL WHERE run_id = ?", (str(run_id),))


# -------------------- Structured payload access --------------------

def get_run_process(run_id: str) -> Dict[str, Any]:
    """Return the full structured payload for a run.

    Canonical keys (input_context / response / tool_calls / reasoning /
    llm_invocations / llm_raw_responses / artifacts / token_usage /
    duration_ms) plus legacy aliases (llm_input_context / thinking /
    llm_invoke_responses) for existing readers.
    """
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM run_payloads WHERE run_id = ?", (str(run_id),)).fetchone()
    if row is not None:
        canonical = {
            "input_context": db.loads(row["input_context"], {}) or {},
            "response": db.loads(row["response"], {}) or {},
            "tool_calls": db.loads(row["tool_calls"], []) or [],
            "reasoning": db.loads(row["reasoning"], []) or [],
            "llm_invocations": db.loads(row["llm_invocations"], []) or [],
            "llm_raw_responses": db.loads(row["llm_raw_responses"], []) or [],
            "artifacts": db.loads(row["artifacts"], []) or [],
        }
        run_row = conn.execute(
            "SELECT prompt_tokens, cached_prompt_tokens, completion_tokens, "
            "total_tokens, duration_ms FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
        canonical["token_usage"] = {
            "inbound_tokens": int((run_row["prompt_tokens"] if run_row else 0) or 0),
            "outbound_tokens": int((run_row["completion_tokens"] if run_row else 0) or 0),
            "total_tokens": int((run_row["total_tokens"] if run_row else 0) or 0),
            "cached_tokens": int((run_row["cached_prompt_tokens"] if run_row else 0) or 0),
        }
        canonical["duration_ms"] = int((run_row["duration_ms"] if run_row else 0) or 0)
        return rp.with_legacy_aliases(canonical)

    # Legacy fallbacks: a sidecar file written by a not-yet-restarted old
    # process, then an inline `process` on the record (pre-sidecar era).
    p = RUN_PROCESS_DIR / f"{run_id}.json"
    if p.exists():
        try:
            legacy = json.loads(p.read_text(encoding="utf-8")) or {}
            return rp.with_legacy_aliases(rp.canonicalize(legacy))
        except Exception:
            return {}
    # Imported here, not at module import time: the lifecycle module sits on
    # top of this one, so a top-level import would close the cycle.
    from .lifecycle import get_run_by_id
    rec = get_run_by_id(run_id) or {}
    proc = rec.get("process") or {}
    if rp.has_heavy_data(proc):
        return rp.with_legacy_aliases(rp.canonicalize(proc))
    return proc


def delete_run_process(run_id: str) -> None:
    """Remove a run's structured payload. Best-effort."""
    try:
        with db.transaction() as conn:
            conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (str(run_id),))
        p = RUN_PROCESS_DIR / f"{run_id}.json"
        if p.exists():
            p.unlink()
    except Exception:
        pass


def seed_run_input_context(run_id: str, system_prompt: str, user_message: str) -> None:
    """Persist a minimal input context when a run starts.

    The full payload is only written at run close, so without this seed the
    dashboard's Input Context shows nothing but the raw task input while a
    worker is still executing — and forever for runs that never reach a clean
    close (crash, kill, stop). The close-time payload replaces the seed
    wholesale. Best-effort: a failed seed must never break the run itself.
    """
    try:
        update_run(str(run_id), {
            "process": {
                "input_context": {
                    "system_prompt": system_prompt or "",
                    "history": [],
                    "user_message": user_message or "",
                },
            },
        })
    except Exception:
        pass


def compact_runs() -> int:
    """Legacy no-op: heavy payloads now always live in the run_payloads table."""
    return 0


# -------------------- Public shared state API --------------------

def load_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Return all run records from shared state."""
    return _load_runs(timeout)


def save_runs(runs: List[Dict[str, Any]], timeout: float = 10.0) -> None:
    """Overwrite the entire run list. Use only when bulk mutations are needed."""
    _save_runs(runs, timeout)
    notify_change("runs")


def upsert_run(run: Dict[str, Any]) -> None:
    """Insert or update a run record by run_id."""
    _upsert_run(run)
    _publish_run_delta(run, str(run.get("run_id")))


def update_run(run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply partial updates to a run record and return the merged record."""
    merged = _update_run(run_id, updates)
    if merged:
        _publish_run_delta(merged, str(run_id))
    return merged


def delete_run(run_id: str) -> bool:
    """Delete one run record and its structured payload. Returns True if removed."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (str(run_id),))
        cur = conn.execute("DELETE FROM runs WHERE run_id = ?", (str(run_id),))
        removed = cur.rowcount > 0
    if removed:
        notify_change("runs", run_id=str(run_id))
    return removed
