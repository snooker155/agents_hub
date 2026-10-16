"""
Flow, loop, team and scenario runs for the sync engine: finished runs as
history, with what they recorded along the way.

Like agent runs (hubsync/adapters/run.py) these are what happened, so only
terminal runs are listed and ``apply`` writes them straight into the
``entity_runs`` table. ``common.entity_runs.upsert`` is not used: it also
evaluates alert rules on the move into a terminal status, and a run that did
not happen here must not raise a ``run_failed`` alert or an online eval. The
status transition check is skipped for the same reason; a finished run is
never resumable (no checkpoint, no pid, no lease), so nothing here can
relaunch it. The run is charged to the sync owner.

The document is the record minus this hub's machinery (pid, host, container,
heartbeat, resume attempts, log path, checkpoint, launching user and key, and
the per-kind id aliases that the run id and entity id already say) plus the
children of its kind, under ``children``:

* flow: the events of ``flow_logs/<flow_id>/<run>.json``
* loop: the iterations
* team: the message board, in order (the sequence numbers are local)
* scenario: the tick log (the story is part of the record already)

Children are capped at 4 MB of JSON, keeping the newest, so one huge
simulation cannot make a record the other side refuses. Applying over a run
already here replaces it and its children.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from common import db
from common import entity_runs as er
from hubsync.adapters.base import Adapter, AdapterError, register

_LOCAL_KEYS = (
    "run_id", "workspace", "pid", "host", "container_name", "heartbeat_at",
    "resume_attempts", "log_file", "checkpoint", "launched_by", "key_id",
)
_MAX_BYTES = 4_000_000


def _fit(entries: List[Any]) -> List[Any]:
    kept = list(entries)
    size = len(json.dumps(kept, default=str))
    while kept and size > _MAX_BYTES:
        size -= len(json.dumps(kept[0], default=str)) + 1
        kept = kept[1:]
    return kept


# ── children, per kind ───────────────────────────────────────────────────────

def _flow_log_path(rec: Dict[str, Any]):
    from flow.run_store import flow_log_path
    return flow_log_path(str(rec.get("entity_id") or ""), str(rec["run_id"]))


def _read_flow(rec: Dict[str, Any]) -> List[Any]:
    path = _flow_log_path(rec)
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return events if isinstance(events, list) else []


def _write_flow(rec: Dict[str, Any], events: List[Any]) -> None:
    path = _flow_log_path(rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_flow(rec: Dict[str, Any]) -> None:
    try:
        _flow_log_path(rec).unlink()
    except OSError:
        pass


def _read_loop(rec: Dict[str, Any]) -> List[Any]:
    from loops import store
    return [{k: v for k, v in it.items() if k != "loop_run_id"}
            for it in store.list_iterations(rec["run_id"])]


def _write_loop(rec: Dict[str, Any], items: List[Any]) -> None:
    from loops import store
    from loops.models import Iteration
    for item in items:
        store.save_iteration(Iteration(**{**item, "loop_run_id": rec["run_id"]}))


def _clear_loop(rec: Dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM loop_iterations WHERE loop_run_id = ?", (rec["run_id"],))


def _read_team(rec: Dict[str, Any]) -> List[Any]:
    from teams import store
    out = []
    for msg in store.list_messages(rec["run_id"]):
        d = dict(vars(msg))
        d.pop("seq", None)
        d.pop("team_run_id", None)
        out.append(d)
    return out


def _write_team(rec: Dict[str, Any], items: List[Any]) -> None:
    from teams import store
    from teams.models import TeamMessage
    for item in items:
        store.append_message(TeamMessage(**{**item, "team_run_id": rec["run_id"]}))


def _clear_team(rec: Dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM team_messages WHERE team_run_id = ?", (rec["run_id"],))


def _read_scenario(rec: Dict[str, Any]) -> List[Any]:
    from playground import store
    return [{k: v for k, v in t.items() if k != "sim_run_id"}
            for t in store.list_ticks(rec["run_id"])]


def _write_scenario(rec: Dict[str, Any], items: List[Any]) -> None:
    for item in items:
        # Ticks go in as stored rows: the dataclass form would need the
        # decision and resolution classes rebuilt only to be dumped again.
        with db.transaction() as conn:
            conn.execute(
                db.upsert_sql("sim_ticks", ("sim_run_id", "tick", "ts", "decisions", "resolutions",
                                            "frame", "events", "idle", "cost"),
                              ("sim_run_id", "tick")),
                (rec["run_id"], item["tick"], item.get("ts"),
                 db.dumps(item.get("decisions") or []), db.dumps(item.get("resolutions") or []),
                 db.dumps(item.get("frame") or {}), db.dumps(item.get("events") or []),
                 db.dumps(item.get("idle") or []), item.get("cost") or 0.0))


def _clear_scenario(rec: Dict[str, Any]) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM sim_ticks WHERE sim_run_id = ?", (rec["run_id"],))


_CHILDREN: Dict[str, tuple] = {
    "flow": (_read_flow, _write_flow, _clear_flow),
    "loop": (_read_loop, _write_loop, _clear_loop),
    "team": (_read_team, _write_team, _clear_team),
    "scenario": (_read_scenario, _write_scenario, _clear_scenario),
}


class EntityRunAdapter(Adapter):
    kind = "entity_run"
    order = 72
    label = "Flow, loop, team and scenario runs"
    volatile = ()

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        runs = er.list_runs(workspace=workspace, statuses=er.run_status.TERMINAL_STATUSES,
                            order_by="COALESCE(created_at, ''), run_id")
        return [str(r["run_id"]) for r in runs
                if (r.get("launched_by") or "local") in (owner, "local")]

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        rec = er.get(record_id)
        if rec is None or rec.get("status") not in er.run_status.TERMINAL_STATUSES:
            return None
        id_field, entity_field = er.KIND_FIELDS[rec["kind"]]
        doc = {k: v for k, v in rec.items()
               if k not in _LOCAL_KEYS and k not in (id_field, entity_field)}
        read: Callable = _CHILDREN[rec["kind"]][0]
        doc["children"] = _fit(read(rec))
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        kind = str(doc.get("kind") or "")
        if kind not in _CHILDREN:
            raise AdapterError(f"unknown run kind {kind!r}")
        if doc.get("status") not in er.run_status.TERMINAL_STATUSES:
            raise AdapterError("only a finished run is history; this one is still going")
        prev = er.get(record_id)
        if prev is not None and prev.get("workspace") not in (workspace, None):
            raise AdapterError(f"run {record_id} lives in another workspace here")
        rec = er._normalise(
            {**{k: v for k, v in doc.items() if k != "children"},
             "run_id": record_id, "workspace": workspace, "launched_by": owner}, kind)
        _read, write, clear = _CHILDREN[kind]
        if prev is not None:
            clear(prev)
        with db.transaction() as conn:
            conn.execute(f"DELETE FROM {er.TABLE} WHERE run_id = ?", (record_id,))
            rec.setdefault("created_at", er.utc_now_iso())
            er._write(conn, rec)
        write(rec, doc.get("children") or [])
        er.notify(kind, run_id=record_id)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        rec = er.get(record_id)
        if rec is None:
            return
        _CHILDREN[rec["kind"]][2](rec)
        er.delete(record_id)


register(EntityRunAdapter())
