"""
AgentRunManager: shared run state, lifecycle tracking, and stop coordination.

Run records live in SQLite (``.agents_hub/agents_hub.db``, see ``common.db``).
Every execution channel — chat SSE, local subprocess, node worker, docker
container, flow node — records runs through this module, so all records share
ONE structure regardless of mode:

Run record (the ``runs`` table, returned as a dict):
- meta:   run_id, task_id, agent_id, session_id, session_type, channel,
          execution_mode, node_id, container_name, workspace, title,
          provider, model, message_origin
- state:  status (running|stop|completed|stopped|failed|error|pending|
          assigned|awaiting_approval), pid, exit_code, error,
          created_at / started_at / finished_at
- I/O:    input (raw instruction), output (final text),
          log_file (plain-text log on disk, linked — never stored inline)
- stats:  process = {token_usage, duration_ms}  (slim projection)

Structured heavy payload (the ``run_payloads`` table, via get_run_process):
- input_context   {system_prompt, history[], user_message}
- response        {text, structured}        — structured = AgentResponse JSON
- tool_calls      [{step, tool, input, output}, ...]
- reasoning       [trace lines]
- llm_invocations / llm_raw_responses / artifacts
Writers keep passing ``process=...`` payloads in any historical shape; they are
normalised via ``common.run_payloads.canonicalize`` at this single chokepoint.

Public API (state):
- load_runs() / save_runs(runs)
- upsert_run(run) / update_run(run_id, updates) / delete_run(run_id)
- get_run_by_id(run_id) / utc_now_iso()

Public API (lifecycle):
- get_status(task_id, run_id) / stop_run(task_id, run_id) / stop_run_by_id(run_id)
- get_in_progress_runs_for_node(node_id) / fail_in_progress_runs_for_node(...)
- run_log_path(run_id)
"""
from __future__ import annotations

import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from common import db
from common import run_payloads as rp
from common.db_migrate import RUN_COLUMNS
from common.paths import AGENTS_HUB_ROOT
from common.session_broker import notify_change, notify_delta

# -------------------- Paths & constants --------------------
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
AGENTS_HUB_ROOT.mkdir(parents=True, exist_ok=True)
RUN_LOGS_DIR = AGENTS_HUB_ROOT / "run_logs"
# Legacy sidecar dir — only read as a fallback for payloads written by a
# not-yet-restarted old process; new payloads live in the run_payloads table.
RUN_PROCESS_DIR = AGENTS_HUB_ROOT / "run_process"

_TOKEN_COLUMNS = ("prompt_tokens", "cached_prompt_tokens", "completion_tokens",
                  "total_tokens", "duration_ms")


def run_log_path(run_id: str) -> Path:
    """Canonical log file path for an agent run. Every run channel writes here
    so the UI can treat every run uniformly."""
    RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return RUN_LOGS_DIR / f"agent_run_{run_id}.log"


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
        "INSERT OR REPLACE INTO run_payloads (run_id, input_context, response, "
        "tool_calls, reasoning, llm_invocations, llm_raw_responses, artifacts, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        f"INSERT OR REPLACE INTO runs ({', '.join(RUN_COLUMNS)}, "
        "prompt_tokens, cached_prompt_tokens, completion_tokens, total_tokens, "
        "duration_ms, extra) "
        f"VALUES ({', '.join('?' * len(RUN_COLUMNS))}, ?, ?, ?, ?, ?, ?)",
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
    "input", "output", "instance_id",
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
        expr = (f"(COALESCE(json_extract(extra, '$.is_flow'), 0) IN (1, 'true') "
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
               SUM(status = 'running')                               AS n_running,
               SUM(status = 'completed')                              AS n_completed,
               SUM(status IN ('failed', 'error'))                     AS n_failed,
               SUM(status IN ('stop', 'stopped'))                     AS n_stopped,
               SUM(status IN ('pending', 'awaiting_approval'))        AS n_pending,
               MAX(finished_at)                                       AS finished_at,
               GROUP_CONCAT(DISTINCT agent_id)                        AS agents,
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


# -------------------- Instance bookkeeping --------------------
# A run is one unit of work; the *instance* is the agent copy that performed it.
# Every execution channel eventually writes its run through _apply(), so hooking
# the status transition here keeps instance state correct for chat, subprocess,
# node, container and flow runs without each of them remembering to do it.

_TERMINAL_RUN_STATUSES = ("completed", "stopped", "failed", "error")


def _sync_instance(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> None:
    """Move the run's instance along with the run's status transition."""
    instance_id = str(new.get("instance_id") or "")
    if not instance_id:
        return
    old_status = str((old or {}).get("status") or "")
    new_status = str(new.get("status") or "")
    if old_status == new_status:
        return
    try:
        from instances import registry as ireg
        from instances import store as istore

        if new_status == "running":
            hint = new.get("title") or new.get("input") or ""
            ireg.mark_active(instance_id, str(new.get("run_id") or ""), str(hint)[:200] or None)
            return
        if new_status not in _TERMINAL_RUN_STATUSES:
            return

        proc = new.get("process") or {}
        usage = (proc.get("token_usage") or {}) if isinstance(proc, dict) else {}
        istore.add_run_stats(
            instance_id,
            tokens=int(usage.get("total_tokens") or 0),
            duration_ms=int((proc or {}).get("duration_ms") or 0),
        )
        # A node or container outlives the run it just executed: it goes back to
        # standby, not away. Only a one-shot carrier actually finishes.
        if new.get("node_id") or new.get("container_name"):
            ireg.mark_standby(instance_id, "idle — waiting for work")
        elif new_status in ("failed", "error"):
            ireg.mark_failed(instance_id, str(new.get("error") or "run failed"))
        else:
            ireg.mark_finished(instance_id, "run finished")
    except Exception:
        # Instance bookkeeping must never break run recording.
        pass


def _load_runs(timeout: float = 10.0) -> List[Dict[str, Any]]:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM runs ORDER BY rowid").fetchall()
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


def _update_run(run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    old, new = _apply(str(run_id), updates, insert_if_missing=False)
    if new is None:
        return None
    _sync_instance(old, new)
    _notify_task_run_finished(old or {}, new)
    return new


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


# Fields a list row needs to repaint itself. A run update publishes these as a
# delta so an open Messages page patches one row, instead of every open tab
# refetching the whole list — which, with a thousand runs in flight, is the
# difference between a live page and a permanently loading one.
_RUN_DELTA_FIELDS = (
    "run_id", "agent_id", "status", "workspace", "session_id", "instance_id",
    "task_id", "title", "started_at", "finished_at", "error", "channel",
    "model", "provider", "total_tokens", "duration_ms",
)


def _publish_run_delta(record: Optional[Dict[str, Any]], run_id: str) -> None:
    if not record:
        notify_change("runs", run_id=str(run_id))
        return
    proc = record.get("process") or {}
    usage = (proc.get("token_usage") or {}) if isinstance(proc, dict) else {}
    delta = {k: record.get(k) for k in _RUN_DELTA_FIELDS}
    delta["run_id"] = str(run_id)
    delta["total_tokens"] = usage.get("total_tokens", record.get("total_tokens"))
    delta["duration_ms"] = (proc or {}).get("duration_ms", record.get("duration_ms"))
    notify_delta("runs", str(run_id), delta)


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


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return _utc_now_iso()


def new_unique_run_id() -> str:
    """Return a UUID that does not collide with any existing run_id in state."""
    conn = db.get_conn()
    rid = str(uuid4())
    while conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (rid,)).fetchone():
        rid = str(uuid4())
    return rid


def preopen_run(
    run_id: str,
    agent_id: str,
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    session_type: str = "task",
    status: str = "pending",
    log_file: Optional[str] = None,
    link_to_session: bool = True,
    **extra: Any,
) -> str:
    """Pre-create a placeholder run record before the subprocess is spawned.

    The launcher (parent process) writes this so the dashboard sees the run — and
    its log_file — immediately, before the subprocess starts and calls open_run().
    It deliberately leaves started_at/pid null and writes no task execution-log
    entry: those are open_run()'s job once the run is actually running. Used for
    both pre-approval (status="awaiting_approval") and pre-spawn (status="pending")
    placeholders. Extra kwargs are merged into the record. Returns run_id.
    """
    record: Dict[str, Any] = {
        "run_id": run_id,
        "task_id": str(task_id) if task_id is not None else None,
        "agent_id": agent_id,
        "status": status,
        "session_type": session_type,
        "session_id": session_id or None,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
        **extra,
    }
    if log_file is not None:
        record["log_file"] = str(log_file)
    _upsert_run(record)
    if link_to_session and session_id:
        try:
            from common.session_service import add_run_to_session as _link
            _link(session_id, run_id)
        except Exception:
            pass
    return run_id


def open_run(
    run_id: str,
    agent_id: str,
    *,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    session_type: Optional[str] = None,
    log_file: Optional[str] = None,
    workspace: Optional[str] = None,
    title: Optional[str] = None,
    message_origin: Optional[str] = None,
    pid: Optional[int] = None,
    status: str = "running",
    link_to_session: bool = True,
    **extra: Any,
) -> str:
    """Create a run record and optionally link it to a session.

    Extra keyword arguments are merged directly into the record (e.g.
    execution_mode, provider, model, is_flow).  Returns run_id.
    """
    record: Dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "pid": pid,
        "status": status,
        "session_id": session_id,
        "started_at": _utc_now_iso(),
        "finished_at": None,
        "exit_code": None,
        "error": None,
        **extra,
    }
    if session_type is not None:
        record["session_type"] = session_type
    if log_file is not None:
        record["log_file"] = str(log_file)
    if workspace is not None:
        record["workspace"] = workspace
    if title is not None:
        record["title"] = title
    if message_origin is not None:
        record["message_origin"] = message_origin
    _upsert_run(record)
    if link_to_session and session_id:
        try:
            from common.session_service import add_run_to_session as _link
            _link(session_id, run_id)
        except Exception:
            pass
    _notify_task_run_started(record)
    return run_id


def close_run(
    run_id: str,
    *,
    status: str,
    exit_code: int,
    error: Optional[str] = None,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """Mark a run as finished and return the updated record.

    Extra keyword arguments (e.g. process=...) are merged into the update.
    """
    return _update_run(run_id, {
        "status": status,
        "finished_at": _utc_now_iso(),
        "exit_code": exit_code,
        "error": error,
        **extra,
    })


def close_run_from_result(
    run_id: str,
    result: Any,
    **extra: Any,
) -> Optional[Dict[str, Any]]:
    """Close a run, deriving status/exit_code/error/output from an agent result.

    ``result`` is any object exposing ``.ok``, ``.agent_output`` and ``.error``
    (the shape returned by ``agents.agent_invoke.invoke_agent``). On success the
    stripped output is recorded; on failure the error string is. Extra kwargs
    (e.g. ``process=...``) are forwarded to ``close_run``. This is the shared
    result→run-record derivation used by the single-agent and HTTP entry points.

    When the result carries a structured response (AgentResult.response), it is
    folded into the process payload so the run's ``response`` block stores both
    the plain text and the structured JSON.
    """
    ok = bool(getattr(result, "ok", False))
    output = (getattr(result, "agent_output", None) or "").strip()
    proc = extra.pop("process", None)
    if isinstance(proc, dict):
        structured = None
        resp_obj = getattr(result, "response", None)
        if resp_obj is not None:
            try:
                structured = resp_obj.to_payload() if hasattr(resp_obj, "to_payload") else None
            except Exception:
                structured = None
        proc = {**proc, "response_text": output if ok else "", "response_obj": structured}
        extra["process"] = proc
    if ok:
        return close_run(
            run_id,
            status="completed",
            exit_code=0,
            output=output,
            **extra,
        )
    return close_run(
        run_id,
        status="failed",
        exit_code=1,
        error=str(getattr(result, "error", None) or "agent error"),
        **extra,
    )


_NOTIFY_TERMINAL_STATUSES = {"completed", "failed"}
# The orchestrator only routes work; its runs never announce a task start.
_ROUTING_AGENT_ID = "orchestrator"


def _notify_task_run_finished(old: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Push an inbox notification when a user task's agent run reaches a terminal state.

    Every run-finalization path (subprocess close_run, node-mode workers, flow
    drivers) funnels through _update_run, so this single hook covers all
    execution modes without relying on the LLM to report completion. Scope is
    deliberately narrow: only task-bound runs (chat/delegation runs are
    excluded — the user is watching those), only user/external-created tasks
    (skips internal orchestrator-created tasks), and never the orchestrator's
    own routing runs. Stopped runs are skipped too: the user stopped them.
    """
    try:
        if str(new.get("status") or "") not in _NOTIFY_TERMINAL_STATUSES:
            return
        if str(old.get("status") or "") in _NOTIFY_TERMINAL_STATUSES:
            return  # already finalized — don't notify twice
        task_id = new.get("task_id")
        agent_id = str(new.get("agent_id") or "")
        if not task_id or agent_id == _ROUTING_AGENT_ID:
            return

        from uuid import UUID as _UUID
        from tasks import service as _ts
        task = _ts.get_task(_UUID(str(task_id)))
        # created_by is a str-mixin enum, so direct string comparison works.
        if not task or getattr(task, "created_by", None) not in ("user", "external"):
            return

        from plans import service as _plan_service
        ok = str(new.get("status")) == "completed"
        body = (
            f"Agent '{agent_id}' finished the task."
            if ok
            else f"Agent '{agent_id}' failed: {new.get('error') or 'unknown error'}"
        )
        _plan_service.create_notification(
            title=f"Task {'completed' if ok else 'failed'}: {task.title}",
            body=body,
            severity="success" if ok else "error",
            source={"task_id": str(task_id), "run_id": str(new.get("run_id") or "")},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:
        # Notification delivery must never break run bookkeeping.
        pass


def _task_already_announced(task_id: str, run_id: str) -> bool:
    """True if an earlier executor run already announced this task's start.

    A task is re-run many times over its life: the orchestrator re-routes it,
    the executor retries, a reviewer picks it up. Each of those calls open_run,
    and every one of them used to emit its own "Task started: <title>" entry —
    identical text, fresh unread row, so the inbox looked like read messages
    were lighting up again. Announce a task once, on its first executor run.
    """
    try:
        conn = db.get_conn()
        row = conn.execute(
            "SELECT 1 FROM runs WHERE task_id = ? AND run_id != ? "
            "AND COALESCE(agent_id, '') != ? LIMIT 1",
            (str(task_id), str(run_id), _ROUTING_AGENT_ID),
        ).fetchone()
        return row is not None
    except Exception:
        # Can't tell — stay quiet rather than risk another duplicate.
        return True


def _notify_task_run_started(record: Dict[str, Any]) -> None:
    """Push an inbox notification when a task's agent run begins executing.

    Called from open_run on the run→running transition. Fires for every task
    regardless of who created it — including orchestrator-created/delegated
    tasks — but only ONCE per task, on the first run by an actual executor.
    The orchestrator's own routing run is skipped (it announces nothing the
    user did not already trigger), and so is every later re-route, retry and
    review run, which is what `_task_already_announced` checks.
    """
    try:
        if str(record.get("status") or "") != "running":
            return
        task_id = record.get("task_id")
        agent_id = str(record.get("agent_id") or "")
        if not task_id or agent_id == _ROUTING_AGENT_ID:
            return
        if _task_already_announced(str(task_id), str(record.get("run_id") or "")):
            return

        from uuid import UUID as _UUID
        from tasks import service as _ts
        task = _ts.get_task(_UUID(str(task_id)))
        if not task:
            return

        from plans import service as _plan_service
        _plan_service.create_notification(
            title=f"Task started: {task.title}",
            body=f"Agent '{agent_id}' started the task.",
            severity="info",
            source={"task_id": str(task_id), "run_id": str(record.get("run_id") or "")},
            workspace=getattr(task, "workspace", None),
        )
    except Exception:
        # Notification delivery must never break run bookkeeping.
        pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return True
    else:
        return True


def finalize_flow_task(task_id: str, status: str, exit_code: int, *, error: str | None = None) -> None:
    """Update a task's status after its flow run finishes.

    A flow is not an agent run — it has no meta run record — so this resolves
    the task directly by id and applies the same terminal-status rules a
    resolving agent would (advance to resolved / continuous-followup on success,
    block on failure), then fires any pending session continuations.
    """
    try:
        from tasks import service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id))

        if status == "completed":
            agent_set_statuses = {
                _ts.TaskStatus.reviewing,
                _ts.TaskStatus.reviewed,
                _ts.TaskStatus.blocked,
                _ts.TaskStatus.done,
            }
            current_task = _ts.get_task(tid)
            if current_task and current_task.status not in agent_set_statuses:
                ws_name = str(getattr(current_task, "workspace", "") or "default")
                try:
                    from workspace import get_workspace_metadata
                    followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                except Exception:
                    followup_mode = "single"
                if followup_mode == "continuous":
                    _ts.update_task(tid, status=_ts.TaskStatus.in_progress)
                else:
                    _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                _ts.clear_agent(tid)
        else:
            reason = (error or "").strip() or f"flow exited with code {exit_code}"
            _ts.block_task(tid, reason=reason)
            _ts.clear_agent(tid)
            try:
                _ts.append_task_activity_log(tid, "run_failed", f"Flow failed: {reason}")
            except Exception:
                pass

        # Trigger any session continuations waiting for this task
        try:
            from common.session_service import pop_continuations_for_task
            for cont in pop_continuations_for_task(str(task_id)):
                try:
                    _trigger_session_continuation(cont, status)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def park_task_awaiting_input(run_id: str, question: Dict[str, Any], agent_id: str = "") -> None:
    """Pause a task because its agent called ``ask_user``.

    Sets the owning task to ``awaiting_input`` with the pending question stored on
    it, keeps the agent assignment (so the resume path knows who to re-run), and
    notifies the user. Crucially it does NOT run the normal completion path
    (``finalize_task_from_run``): the task is paused, not resolved, so any session
    continuation waiting on this task stays pending until the user answers and the
    resumed run finishes. Best-effort — failures are swallowed.
    """
    try:
        run = get_run_by_id(run_id)
        task_id_str = (run or {}).get("task_id")
        if not task_id_str:
            return
        from tasks import service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id_str))

        q_text = str((question or {}).get("question") or "").strip()
        pending = {
            "question": q_text,
            "choices": list((question or {}).get("choices") or []),
            "agent_id": agent_id or str((run or {}).get("agent_id") or ""),
            "run_id": run_id,
            "asked_at": _utc_now_iso(),
        }
        # An imported agent that suspended keeps its own handle on the question
        # and knows which node it stopped in. Both are its to interpret, and the
        # resume call hands them straight back, so they travel with the question
        # rather than being dropped here.
        for extra in ("key", "node"):
            value = (question or {}).get(extra)
            if value:
                pending[extra] = str(value)
        _ts.update_task(tid, status=_ts.TaskStatus.awaiting_input, pending_question=pending)
        try:
            _ts.append_task_activity_log(tid, "awaiting_input", f"Agent asked: {q_text}", run_id=run_id, agent_id=pending["agent_id"])
        except Exception:
            pass

        # Surface the question to the user (inbox + dashboard bell).
        try:
            from plans import service as _plan_service
            current_task = _ts.get_task(tid)
            _plan_service.create_notification(
                title="A task needs your input",
                body=q_text or "The agent is waiting for your answer.",
                severity="info",
                source={"origin": "agent", "task_id": str(task_id_str)},
                workspace=str(getattr(current_task, "workspace", "") or "") or None,
                channels=["dashboard"],
            )
        except Exception:
            pass
    except Exception:
        pass


def _auto_start_review(tid, task) -> bool:
    """Deterministically start a code_reviewer run on a freshly resolved task.

    Called when a continuation orchestrator run finishes with the task resolved:
    the orchestrator is the last LLM actor on the task, so if it did not chain
    the reviewer itself, nothing else would. Skipped (returns False) when the
    reviewer is not registered, not allowed in the workspace, or the workspace
    is not in subprocess mode (node mode dispatches through its polling loop).
    """
    try:
        from tasks import service as _ts
        from agents.registry import get_agent
        from workspace import get_workspace_metadata

        if not get_agent("code_reviewer"):
            return False
        ws_name = str(getattr(task, "workspace", "") or "default")
        metadata = get_workspace_metadata(ws_name)
        allowed = metadata.get("allowed_agents")  # None means unrestricted
        if allowed is not None and "code_reviewer" not in allowed:
            return False
        if metadata.get("orchestrator", {}).get("execution_mode", "subprocess") == "node":
            return False

        from agents import agent_launcher
        review_run_id, _sess = agent_launcher.start_run(str(tid), "code_reviewer", None)
        _ts.assign_agent(tid, "code_reviewer", None, run_id=review_run_id)
        _ts.update_task(tid, status=_ts.TaskStatus.reviewing)
        _ts.append_task_activity_log(
            tid,
            "auto_review",
            "Task resolved — code_reviewer started automatically",
            run_id=review_run_id,
            agent_id="code_reviewer",
        )
        return True
    except Exception:
        return False


def _maybe_retry_failed_run(tid, run: Dict[str, Any], reason: str) -> bool:
    """Re-dispatch a failed worker run instead of blocking, when the workspace's
    orchestrator ``max_retries`` still allows it.

    Opt-in (default ``max_retries=0`` disables it) and deliberately conservative:
    only worker-agent runs are retried (never orchestrator/reviewer control runs
    or a run the user stopped), the failure reason is appended to the retry
    instruction, and the per-task ``retry_count`` is capped hard so a persistently
    failing task blocks after N attempts rather than looping forever. Returns True
    when a retry was dispatched (caller then skips blocking the task).
    """
    try:
        from tasks import service as _ts

        # Never retry a run the user stopped.
        run_status = str((run or {}).get("status") or "").lower()
        err_text = str((run or {}).get("error") or "").lower()
        if run_status == "stopped" or "stopped by user" in err_text:
            return False

        agent_id = str((run or {}).get("agent_id") or "")
        if not agent_id or agent_id in ("orchestrator", "code_reviewer"):
            return False

        task = _ts.get_task(tid)
        if not task:
            return False

        ws_name = str(getattr(task, "workspace", "") or "default")
        try:
            from workspace import get_workspace_metadata
            max_retries = int(get_workspace_metadata(ws_name).get("orchestrator", {}).get("max_retries", 0) or 0)
        except Exception:
            max_retries = 0
        if max_retries <= 0:
            return False

        attempts = int(getattr(task, "retry_count", 0) or 0)
        if attempts >= max_retries:
            return False

        # Feed the failure forward so the retry can react to what went wrong.
        params = dict(getattr(task, "assigned_agent_params", None) or {})
        prev_desc = str(params.get("description") or getattr(task, "description", "") or "").strip()
        params["description"] = (
            f"{prev_desc}\n\n[Retry {attempts + 1}/{max_retries}] The previous attempt failed: {reason}"
        ).strip()

        # Dispatch first — if launching raises (e.g. a budget cap), we fall through
        # to blocking without having mutated the task's retry bookkeeping.
        from agents import agent_launcher
        new_run_id, _sess = agent_launcher.start_run(str(tid), agent_id, params)
        _ts.assign_agent(tid, agent_id, params, run_id=new_run_id)
        _ts.update_task(tid, status=_ts.TaskStatus.in_progress, retry_count=attempts + 1)
        _ts.append_task_activity_log(
            tid,
            "run_retry",
            f"Retry {attempts + 1}/{max_retries} after failure: {reason}",
            run_id=new_run_id,
            agent_id=agent_id,
        )
        return True
    except Exception:
        return False


def finalize_task_from_run(run_id: str, status: str, exit_code: int) -> None:
    """Update the owning task's status after a run completes or fails."""
    try:
        run = get_run_by_id(run_id)
        task_id_str = (run or {}).get("task_id")
        if not task_id_str:
            return
        from tasks import service as _ts
        from uuid import UUID as _UUID
        tid = _UUID(str(task_id_str))

        # Final status / tokens are already persisted on the run record itself
        # (status, finished_at, process.token_usage); no separate sidecar update.

        # Record a 'task' episode for agents with shared memory (best-effort).
        try:
            agent_id_for_run = str((run or {}).get("agent_id") or "")
            if agent_id_for_run:
                from agents import registry as _registry
                spec = _registry.get_agent(agent_id_for_run)
                from memory.binding import effective_memory_pools
                _mem_pools = effective_memory_pools(spec, (run or {}).get("workspace")) if spec else []
                if _mem_pools:
                    from memory.tool import silent_task_episode
                    silent_task_episode(
                        _mem_pools[0],
                        agent_id_for_run,
                        task_id=str(task_id_str),
                        run_id=run_id,
                        status=status,
                        exit_code=exit_code,
                        error=str((run or {}).get("error") or "") or None,
                        workspace=str((run or {}).get("workspace") or "") or None,
                    )
        except Exception:
            pass

        if status == "completed":
            # Only advance to 'resolved' if the agent didn't already set a
            # terminal status itself (e.g. code_reviewer sets 'reviewed' or 'blocked').
            # Orchestrator and code_reviewer are not workers — they must not set 'resolved'.
            agent_id_for_run = str((run or {}).get("agent_id") or "")
            non_resolving_agents = {"orchestrator", "code_reviewer"}
            agent_set_statuses = {
                _ts.TaskStatus.reviewing,
                _ts.TaskStatus.reviewed,
                _ts.TaskStatus.blocked,
                _ts.TaskStatus.done,
            }
            if agent_id_for_run not in non_resolving_agents:
                current_task = _ts.get_task(tid)
                if current_task and current_task.status not in agent_set_statuses:
                    ws_name = str(getattr(current_task, "workspace", "") or "default")
                    try:
                        from workspace import get_workspace_metadata
                        followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                    except Exception:
                        followup_mode = "single"
                    if followup_mode == "continuous":
                        # Keep in_progress so the UI shows work is ongoing, but clear
                        # the agent assignment (agent_state=none) so the orchestrator's
                        # _followup filter picks it up to decide the next step.
                        _ts.update_task(tid, status=_ts.TaskStatus.in_progress)
                        _ts.clear_agent(tid)
                    else:
                        _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                        _ts.clear_agent(tid)
            elif agent_id_for_run == "orchestrator":
                # Orchestrator is a non-resolving agent: it must set the task
                # status itself via update_task when the work is complete. But a
                # continuation ([MONITOR]) orchestrator run is the *last* actor on
                # the task — if it ends without acting (small models sometimes
                # narrate "marking as resolved" without calling the tool), nothing
                # else will ever move the task. Deterministic fallback: a completed
                # continuation run that still owns the assignment resolves an
                # in_progress task and always releases the stale assignment.
                if str((run or {}).get("channel") or "") == "continuation":
                    current_task = _ts.get_task(tid)
                    if current_task and str(getattr(current_task, "assigned_agent_run_id", "") or "") == str(run_id):
                        if current_task.status == _ts.TaskStatus.in_progress:
                            _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                            _ts.append_task_activity_log(
                                tid,
                                "continuation_fallback",
                                "Continuation orchestrator finished without setting a task status — auto-resolved",
                                run_id=run_id,
                            )
                        _ts.clear_agent(tid)
                        # The continuation is the last LLM actor: if the task
                        # ended up resolved and the orchestrator did not chain a
                        # reviewer itself, start the review deterministically so
                        # the pipeline (resolved → reviewing → reviewed → done →
                        # next subtask) never stalls on a skipped tool call.
                        current_task = _ts.get_task(tid)
                        if current_task and current_task.status == _ts.TaskStatus.resolved:
                            _auto_start_review(tid, current_task)
            elif agent_id_for_run == "code_reviewer":
                # Deterministic review outcome handling. The reviewer records its
                # verdict itself via tools (reviewed / blocked); finalize turns
                # that verdict into pipeline progress instead of relying on yet
                # another orchestrator turn:
                #   reviewed  → done (releases dependents, dispatches the next
                #               container subtask, may complete the parent);
                #   reviewing → the reviewer never recorded a verdict — park the
                #               task back at resolved for the user (no auto-retry,
                #               so a silent reviewer cannot cause a review loop);
                #   blocked   → keep the block for a fix cycle.
                # In every case release the reviewer's stale assignment. Only
                # active in continuous followup mode — in single mode the user
                # inspects the verdict and decides the next step themselves.
                current_task = _ts.get_task(tid)
                ws_name = str(getattr(current_task, "workspace", "") or "default") if current_task else "default"
                try:
                    from workspace import get_workspace_metadata
                    followup_mode = get_workspace_metadata(ws_name).get("orchestrator", {}).get("followup_mode", "single")
                except Exception:
                    followup_mode = "single"
                if (
                    followup_mode == "continuous"
                    and current_task
                    and str(getattr(current_task, "assigned_agent_run_id", "") or "") == str(run_id)
                ):
                    if current_task.status == _ts.TaskStatus.reviewed:
                        _ts.update_task(tid, status=_ts.TaskStatus.done)
                        _ts.append_task_activity_log(
                            tid,
                            "review_passed",
                            "Review passed — task finalized as done",
                            run_id=run_id,
                            agent_id="code_reviewer",
                        )
                    elif current_task.status == _ts.TaskStatus.reviewing:
                        _ts.update_task(tid, status=_ts.TaskStatus.resolved)
                        _ts.append_task_activity_log(
                            tid,
                            "review_no_verdict",
                            "Reviewer finished without recording a verdict — task returned to resolved",
                            run_id=run_id,
                            agent_id="code_reviewer",
                        )
                    _ts.clear_agent(tid)
        else:
            error_msg = str((run or {}).get("error") or "").strip()
            reason = error_msg or f"process exited with code {exit_code}"
            # Retry policy: re-dispatch instead of blocking when the workspace
            # allows it and this task hasn't exhausted its retries. A dispatched
            # retry leaves the task in_progress, so skip blocking + continuations.
            if _maybe_retry_failed_run(tid, run or {}, reason):
                return
            _ts.block_task(tid, reason=reason)
            _ts.clear_agent(tid)
            # Surface the error in the activity log so the task page shows it
            try:
                agent_id_str = str((run or {}).get("agent_id") or "")
                _ts.append_task_activity_log(
                    tid,
                    "run_failed",
                    f"Run failed: {reason}",
                    run_id=run_id,
                    agent_id=agent_id_str,
                    exit_code=exit_code,
                )
            except Exception:
                pass

        # Trigger session continuations bound to this run (run-bound entries
        # ignore other runs on the task, e.g. the orchestrator's own run).
        try:
            from common.session_service import pop_continuations_for_task
            continuations = pop_continuations_for_task(str(task_id_str), run_id=run_id)
            for cont in continuations:
                try:
                    _trigger_session_continuation(cont, status)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def _trigger_session_continuation(cont: dict, finished_status: str) -> None:
    """Spawn the orchestrator as a background subprocess to continue the session."""
    import subprocess
    import sys
    from pathlib import Path as _Path

    task_id = cont.get("task_id")
    session_id = cont.get("session_id")
    workspace = cont.get("workspace") or ""
    agent_id = cont.get("agent_id", "orchestrator")
    if not task_id or not session_id:
        return

    project_root = _Path(__file__).resolve().parents[1]
    env = dict(__import__("os").environ)
    env["AGENT_SESSION_ID"] = session_id
    if workspace:
        env["AGENT_WORKSPACE"] = workspace

    # Pre-register the orchestrator run and assign it to the task so it is
    # visible in the UI as the active agent during continuation processing.
    run_id = str(uuid4())
    log_file = run_log_path(run_id)
    _upsert_run({
        "run_id": run_id,
        "task_id": str(task_id),
        "agent_id": agent_id,
        "status": "pending",
        "session_type": "task",
        "channel": "continuation",
        "session_id": session_id,
        "log_file": str(log_file),
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "error": None,
    })
    try:
        from tasks import service as _ts
        from uuid import UUID as _UUID
        _ts.assign_agent(_UUID(task_id), agent_id, run_id=run_id)
        # Keep the UI showing ongoing work, but never overwrite a verdict the
        # finishing run already set — the continuation orchestrator needs to see
        # 'reviewed'/'blocked' to decide between done, fix and resolve.
        _verdict_statuses = {
            _ts.TaskStatus.reviewed,
            _ts.TaskStatus.blocked,
            _ts.TaskStatus.done,
            _ts.TaskStatus.stopped,
        }
        _current = _ts.get_task(_UUID(task_id))
        if _current and _current.status not in _verdict_statuses:
            _ts.update_task(_UUID(task_id), status=_ts.TaskStatus.in_progress)
    except Exception:
        pass

    env["AGENT_LOG_FILE"] = str(log_file)  # reuse the unified run_logs/ file
    env["AGENT_RUN_CHANNEL"] = "continuation"

    # Build a minimal instruction file so the agent gets context.
    # [MONITOR] prefix tells the orchestrator to skip Steps 1-3 and go directly
    # to Step 4 (get_agent_status_tool) so it does not re-assign the same task.
    instruction = (
        f"[MONITOR] Task ID: {task_id}\n\n"
        "A previously started agent has finished. "
        "Skip Steps 1-3. Go directly to Step 4 of your instructions."
    )
    args = [
        sys.executable, str(project_root / "runtime" / "agent_run.py"),
        agent_id,       # positional: agent
        instruction,    # positional: action
        "--task-id", task_id,
        "--run-id", run_id,
    ]
    if workspace:
        args += ["--workspace", workspace]

    with open(log_file, "w", encoding="utf-8") as lf:
        lf.write(f"[continuation] task={task_id} session={session_id} trigger_status={finished_status}\n\n")
        subprocess.Popen(
            args,
            cwd=str(project_root),
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


# -------------------- Public lifecycle API --------------------

def get_run_by_id(run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return a run record by run_id, or None."""
    if not run_id:
        return None
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (str(run_id),)).fetchone()
    return _row_to_record(row) if row is not None else None


def _delete_runs_by_task_status(task_id: str, status: str) -> bool:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE task_id = ? AND status = ?",
            (str(task_id), status)).fetchall()
        if not rows:
            return False
        for r in rows:
            conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (r["run_id"],))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (r["run_id"],))
    return True


def delete_awaiting_approval_run(task_id: str) -> bool:
    """Delete the awaiting_approval run record for a task. Returns True if one was removed."""
    return _delete_runs_by_task_status(task_id, "awaiting_approval")


def delete_assigned_run(task_id: str) -> bool:
    """Delete the assigned (node-queued) run record for a task. Returns True if one was removed."""
    return _delete_runs_by_task_status(task_id, "assigned")


def get_all_runs_for_node(node_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return all runs for a node, newest first."""
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM runs WHERE node_id = ?", (str(node_id),)).fetchall()
    runs = [_row_to_record(r) for r in rows]
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs[:limit]


def get_in_progress_runs_for_node(node_id: str) -> List[Dict[str, Any]]:
    """Return node-bound runs that are currently in progress."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE node_id = ? AND status IN ('running', 'stop')",
        (str(node_id),)).fetchall()
    return [_row_to_record(r) for r in rows]


def fail_in_progress_runs_for_node(node_id: str, reason: str) -> int:
    """Mark all in-progress runs on a node as failed and return count."""
    runs = get_in_progress_runs_for_node(node_id)
    if not runs:
        return 0

    finished_at = _utc_now_iso()
    failed_count = 0
    for run in runs:
        run_id = run.get("run_id")
        if not run_id:
            continue
        updated = _update_run(run_id, {
            "status": "failed",
            "finished_at": finished_at,
            "exit_code": 1,
            "error": reason,
        })
        if updated:
            failed_count += 1
            try:
                from tasks import service as tasks_service
                from uuid import UUID
                task_id = updated.get("task_id")
                if task_id:
                    tid = UUID(str(task_id))
                    task = tasks_service.get_task(tid)
                    if task and getattr(task, "status", None) == "in_progress":
                        tasks_service.block_task(tid, reason=reason)
            except Exception:
                pass

    return failed_count


def _find_active_run_for_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Newest in-progress run for a task — the stop_run fallback when the
    caller knows only the task id."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM runs WHERE task_id = ? AND status IN ('running', 'stop')",
        (str(task_id),)).fetchall()
    runs = [_row_to_record(r) for r in rows]
    if not runs:
        return None
    return max(runs, key=lambda r: r.get("started_at") or r.get("created_at") or "")


def stop_run(task_id: str, run_id: Optional[str] = None) -> bool:
    """Attempt to terminate the running run for a task.

    If run_id is provided it is looked up directly (preferred).  Falls back to
    the task's newest in-progress run when run_id is not known.
    """
    rec = get_run_by_id(run_id) if run_id else None
    if not rec and task_id:
        rec = _find_active_run_for_task(str(task_id))
    if not rec:
        return False
    return _stop_run_record(rec)


def stop_run_by_id(run_id: str) -> bool:
    """Attempt to terminate a specific running run by run_id."""
    rec = get_run_by_id(run_id)
    if not rec or rec.get("status") not in {"running", "stop"}:
        return False
    return _stop_run_record(rec)


def _stop_run_record(rec: Dict[str, Any]) -> bool:
    """Best-effort stop for local, docker, and node-managed runs."""
    task_id = str(rec.get("task_id") or "")
    run_id = str(rec.get("run_id") or "")

    def _mark_task_stopped() -> None:
        if not task_id:
            return
        try:
            from tasks import service as tasks_service
            from uuid import UUID
            tasks_service.clear_agent(UUID(task_id))
            tasks_service.stop_task(UUID(task_id))
        except Exception:
            pass
        # Stopping the run stops the task: cancel its pending continuations too,
        # or a follow-up orchestrator would either fire on the stopped task or
        # (if bound to this run) sit in the queue forever.
        try:
            from common.session_service import drop_continuations_for_task
            dropped = drop_continuations_for_task(task_id)
            if dropped:
                from tasks import service as tasks_service
                from uuid import UUID
                tasks_service.append_task_activity_log(
                    UUID(task_id),
                    "continuation_cancelled",
                    f"Run stopped — cancelled {dropped} pending continuation(s)",
                    run_id=run_id,
                )
        except Exception:
            pass

    # A run executing *inside this process* — a team turn, a playground
    # decision — carries the server's own pid, because that is whose thread it
    # is running on. Signalling that pid would take the backend down with it,
    # so these are stopped the way an in-process chat turn is: mark the record
    # and let the run's stop callback abort at the next model or tool boundary.
    if rec.get("in_process"):
        _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(),
                             "error": "stop requested by user"})
        _mark_task_stopped()
        return True

    if rec.get("execution_mode") == "docker" and rec.get("container_name"):
        from .container_manager import stop_container
        sent = stop_container(rec["container_name"])
        if sent:
            _update_run(rec["run_id"], {"status": "stop", "finished_at": _utc_now_iso()})
            _mark_task_stopped()
        return sent

    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        # Node-managed run: mark stop request and let node_run finalize.
        if rec.get("node_id"):
            _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(), "error": "stop requested by user"})
            log_file_path = rec.get("log_file")
            if log_file_path:
                try:
                    with open(log_file_path, "a", encoding="utf-8") as _lf:
                        _lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                except Exception:
                    pass
            _mark_task_stopped()
            return True
        # In-process chat run: mark stop so the streaming handler can finalize.
        if str(rec.get("session_type") or "") == "chat":
            _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso(), "error": "stop requested by user"})
            # Delegated workers (run_agent_tool) execute in-process under this
            # chat turn with their own run records; mark them too so their
            # RunStopCallback aborts at the next LLM/tool boundary.
            sess = str(rec.get("session_id") or "")
            if sess:
                conn = db.get_conn()
                rows = conn.execute(
                    "SELECT * FROM runs WHERE session_id = ? AND status = 'running' "
                    "AND message_origin = 'delegation'", (sess,)).fetchall()
                for r in rows:
                    _update_run(str(r["run_id"]), {"status": "stop", "error": "stop requested by user"})
            _mark_task_stopped()
            return True
        return False

    # If the process has already exited, clean up the stale "running" record.
    if not _pid_exists(pid):
        _update_run(run_id, {
            "status": "stopped",
            "finished_at": _utc_now_iso(),
            "exit_code": 0,
        })
        _mark_task_stopped()
        return True

    sent = False
    if os.name == "nt":
        try:
            os.kill(pid, signal.SIGTERM)
            sent = True
        except Exception:
            try:
                import ctypes  # type: ignore
                PROCESS_TERMINATE = 0x0001
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    sent = True
            except Exception:
                sent = False
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
            sent = True
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
                sent = True
            except Exception:
                sent = False

    if sent:
        _update_run(run_id, {"status": "stop", "finished_at": _utc_now_iso()})
        _mark_task_stopped()
    elif not _pid_exists(pid):
        # Process exited between our existence check and the signal attempt.
        _update_run(run_id, {
            "status": "stopped",
            "finished_at": _utc_now_iso(),
            "exit_code": 0,
        })
        _mark_task_stopped()
        return True
    return sent


def get_status(task_id: str, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return run status for a task, or None.

    If run_id is provided it is looked up directly (preferred — avoids scanning
    all runs).  Falls back to scanning by task_id when run_id is not known.
    Auto-corrects the status if a process/container has exited without updating state.
    """
    rec = get_run_by_id(run_id)
    if rec:
        if rec.get("execution_mode") == "docker" and rec.get("container_name"):
            from .container_manager import container_running
            if not container_running(rec["container_name"]):
                rec = _update_run(rec["run_id"], {
                    "status": "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 1,
                    "error": rec.get("error") or "container exited unexpectedly",
                }) or rec
        else:
            pid = int(rec.get("pid") or 0)
            if pid > 0 and not _pid_exists(pid):
                was_stopped = rec.get("status") == "stop"
                if was_stopped:
                    log_file_path = rec.get("log_file")
                    if log_file_path:
                        try:
                            with open(log_file_path, "a", encoding="utf-8") as _lf:
                                _lf.write(f"\n[stopped] Run stopped by user at {_utc_now_iso()}\nStatus  : stopped\n")
                        except Exception:
                            pass
                rec = _update_run(rec["run_id"], {
                    "status": "stopped" if was_stopped else "failed",
                    "finished_at": _utc_now_iso(),
                    "exit_code": 0 if was_stopped else 1,
                    "error": None if was_stopped else (rec.get("error") or "process exited unexpectedly"),
                }) or rec
        return rec

    # Not running — return the most recent completed/failed record for this task.
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM runs WHERE task_id = ?", (str(task_id),)).fetchall()
    by_task = [_row_to_record(r) for r in rows]
    if not by_task:
        return None
    return max(by_task, key=lambda r: r.get("finished_at") or r.get("started_at") or "")
