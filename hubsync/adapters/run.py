"""
Agent runs for the sync engine: finished runs as history.

A run is what happened, not something to do again. Only runs in a terminal
status are listed, and ``apply`` inserts one as a finished record with origin
``sync``. It writes the row and the payload directly (the same two writes
``managers.runs.store._apply`` does) and skips what that function fans out to
on a status change: the task inbox notification, alert rules, the OTel export,
the hub link mirror and the instance bookkeeping. Those describe a run that
happened *here*; firing them for a run that did not would notify twice and
bill an instance for work it never did. The person it is charged to is the
sync owner, and the personal API key of whoever triggered the sync is not
recorded.

The document is the run row without what points at this hub's machinery (pid,
container, node, instance, service, heartbeat, log path, launching user and
key) plus ``usage`` (tokens and duration), ``payload`` (input context,
response, tool calls, reasoning, LLM invocations, artifacts) and ``log``, the
run's text log. Raw LLM responses are kept only when they are small; the log
is capped at 1 MB, keeping the end and saying so at the top. A finished run
is immutable, so applying over a run already here replaces it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from common import db
from common.paths import AGENTS_HUB_ROOT
from hubsync.adapters.base import Adapter, AdapterError, register

TERMINAL = ("completed", "stopped", "failed", "error")
_LOCAL_KEYS = (
    "run_id", "workspace", "pid", "container_name", "node_id", "heartbeat_at",
    "log_file", "instance_id", "service_id", "carrier_run", "key_id", "launched_by",
    "origin", "checkpoint_at", "checkpoint_step", "host", "process",
)
_PAYLOAD_KEYS = ("input_context", "response", "tool_calls", "reasoning",
                 "llm_invocations", "artifacts")
_MAX_RAW_BYTES = 256_000
LOG_CAP = 1_000_000
LOG_CUT = "[... earlier output cut by sync ...]\n"


def _log_path(run_id: str) -> Path:
    return AGENTS_HUB_ROOT / "run_logs" / f"agent_run_{run_id}.log"


def _read_log(rec: Dict[str, Any]) -> str:
    """The run's text log, from its own path when that lies under run_logs."""
    root = (AGENTS_HUB_ROOT / "run_logs").resolve()
    path = _log_path(str(rec["run_id"]))
    declared = rec.get("log_file")
    if declared:
        candidate = Path(str(declared)).resolve()
        if candidate.parent == root:
            path = candidate
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # An already cut log is exactly LOG_CAP + the marker; cutting it again
    # would change the content of a record that did not change.
    if len(text) > LOG_CAP + len(LOG_CUT):
        return LOG_CUT + text[-LOG_CAP:]
    return text


class RunAdapter(Adapter):
    kind = "run"
    order = 70
    label = "Run history"
    volatile = ()

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        marks = ", ".join("?" * len(TERMINAL))
        rows = db.get_conn().execute(
            f"SELECT run_id, extra FROM runs WHERE workspace = ? AND status IN ({marks}) "
            "ORDER BY COALESCE(created_at, started_at, ''), run_id",
            (workspace, *TERMINAL)).fetchall()
        out: List[str] = []
        for row in rows:
            extra = db.loads(row["extra"], {}) or {}
            if (extra.get("launched_by") or "local") in (owner, "local"):
                out.append(str(row["run_id"]))
        return out

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from managers.runs import store as runs
        row = db.get_conn().execute("SELECT * FROM runs WHERE run_id = ?", (record_id,)).fetchone()
        if row is None:
            return None
        rec = runs._row_to_record(row)
        if rec.get("status") not in TERMINAL:
            return None
        doc = {k: v for k, v in rec.items() if k not in _LOCAL_KEYS}
        process = rec.get("process") or {}
        if process:
            doc["usage"] = {"token_usage": process.get("token_usage") or {},
                            "duration_ms": process.get("duration_ms") or 0}
        proc = runs.get_run_process(record_id)
        payload = {k: proc[k] for k in _PAYLOAD_KEYS if k in proc}
        raw = proc.get("llm_raw_responses")
        if raw:
            import json
            if len(json.dumps(raw, default=str)) <= _MAX_RAW_BYTES:
                payload["llm_raw_responses"] = raw
        if payload:
            doc["payload"] = payload
        log_text = _read_log(rec)
        if log_text:
            doc["log"] = log_text
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from common.session_broker import notify_change
        from managers.runs import store as runs
        if doc.get("status") not in TERMINAL:
            raise AdapterError("only a finished run is history; this one is still going")
        existing = db.get_conn().execute(
            "SELECT workspace FROM runs WHERE run_id = ?", (record_id,)).fetchone()
        if existing is not None and existing["workspace"] not in (workspace, None):
            raise AdapterError(f"run {record_id} lives in another workspace here")
        rec = {k: v for k, v in doc.items() if k not in ("usage", "payload", "log")}
        rec.update(run_id=record_id, workspace=workspace, origin="sync", launched_by=owner)
        log_text = doc.get("log")
        if log_text:
            path = _log_path(record_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(log_text, encoding="utf-8")
            rec["log_file"] = str(path)
        proc: Dict[str, Any] = dict(doc.get("usage") or {})
        proc.update(doc.get("payload") or {})
        tokens = runs._merge_tokens(None, proc or None)
        with db.transaction() as conn:
            conn.execute("DELETE FROM run_payloads WHERE run_id = ?", (record_id,))
            runs._write_record(conn, rec, tokens)
            if proc and runs.rp.has_heavy_data(proc):
                runs._write_payload_row(conn, record_id, proc)
        notify_change("runs", run_id=record_id)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from managers.runs import store as runs
        runs.delete_run(record_id)
        try:
            _log_path(record_id).unlink()
        except OSError:
            pass


register(RunAdapter())
