"""Persistence for teams, team runs and the message bus.

Team *runs* live in the ``entity_runs`` table every kind of run shares
(:mod:`common.entity_runs`, kind ``team``); this module says what a team run
looks like and keeps the team definitions and the message bus itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from common.entity_runs import EntityRunStore
from common.run_status import RunStatus
from teams.models import BROADCAST, Team, TeamMessage, TeamRun, utc_iso

# Run-level knobs share one JSON column, so tightening a ceiling never needs a
# migration (same reasoning as playground.store).
_CONFIG_FIELDS = (
    "max_rounds", "max_concurrent", "turn_timeout", "cost_ceiling",
    "max_wall_seconds", "allow_direct_messages", "synthesize",
    "default_provider", "default_model", "leader_name", "entry_agent_id",
)


def _notify(resource: str, **meta) -> None:
    try:
        from common.session_broker import notify_change
        notify_change(resource, **meta)
    except Exception:
        pass


# ── Teams ────────────────────────────────────────────────────────────────────

def save_team(team: Team) -> Team:
    team.updated_at = utc_iso()
    config = {f: getattr(team, f) for f in _CONFIG_FIELDS}
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql(
                "teams",
                ("team_id", "name", "description", "workspace", "mode", "charter",
                 "leader_agent_id", "members", "config", "created_at", "updated_at"),
                ("team_id",),
            ),
            (
                team.team_id, team.name, team.description, team.workspace,
                team.mode, team.charter, team.leader_agent_id,
                db.dumps([m.to_dict() for m in team.members]), db.dumps(config),
                team.created_at, team.updated_at,
            ),
        )
    _notify("teams", team_id=team.team_id)
    return team


def _row_to_team(row) -> Team:
    config = db.loads(row["config"], {}) or {}
    return Team.from_dict({
        "team_id": row["team_id"],
        "name": row["name"] or "",
        "description": row["description"] or "",
        "workspace": row["workspace"],
        "mode": row["mode"] or "centralized",
        "charter": row["charter"] or "",
        "leader_agent_id": row["leader_agent_id"],
        "members": db.loads(row["members"], []) or [],
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        **config,
    })


def get_team(team_id: str) -> Optional[Team]:
    row = db.get_conn().execute(
        "SELECT * FROM teams WHERE team_id = ?", (team_id,)
    ).fetchone()
    return _row_to_team(row) if row else None


def list_teams(workspace: Optional[str] = None) -> List[Team]:
    conn = db.get_conn()
    if workspace:
        rows = conn.execute(
            "SELECT * FROM teams WHERE workspace = ? OR workspace IS NULL "
            "ORDER BY updated_at DESC",
            (workspace,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM teams ORDER BY updated_at DESC").fetchall()
    return [_row_to_team(r) for r in rows]


def delete_team(team_id: str) -> bool:
    """Delete a team and every run/message it produced."""
    with db.transaction() as conn:
        run_ids = [
            r["run_id"] for r in conn.execute(
                "SELECT run_id FROM entity_runs WHERE kind = 'team' AND entity_id = ?",
                (team_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM team_messages WHERE team_run_id = ?", (rid,))
        conn.execute("DELETE FROM entity_runs WHERE kind = 'team' AND entity_id = ?",
                     (team_id,))
        cur = conn.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))
        removed = cur.rowcount > 0
    if removed:
        _notify("teams", team_id=team_id)
    return removed


# ── Runs ─────────────────────────────────────────────────────────────────────

def _to_run(rec: Dict[str, Any]) -> TeamRun:
    return TeamRun(
        team_run_id=rec["team_run_id"],
        team_id=rec.get("team_id") or "",
        workspace=rec.get("workspace"),
        mode=rec.get("mode") or "centralized",
        status=rec.get("status") or RunStatus.pending.value,
        goal=rec.get("goal") or "",
        task_id=rec.get("task_id"),
        session_id=rec.get("session_id"),
        conversation_id=rec.get("conversation_id"),
        parent_run_id=rec.get("parent_run_id"),
        rounds_done=int(rec.get("rounds_done") or 0),
        total_cost=float(rec.get("total_cost") or 0.0),
        result=rec.get("result") or "",
        stop_reason=rec.get("stop_reason") or "",
        error=rec.get("error"),
        pid=rec.get("pid"),
        host=rec.get("host"),
        heartbeat_at=rec.get("heartbeat_at"),
        resume_attempts=int(rec.get("resume_attempts") or 0),
        log_file=rec.get("log_file"),
        created_at=rec.get("created_at") or rec.get("started_at") or "",
        started_at=rec.get("started_at") or "",
        finished_at=rec.get("finished_at"),
    )


#: Team-run records: kind ``team`` of the shared table (common/entity_runs.py).
RUNS: EntityRunStore[TeamRun] = EntityRunStore(
    "team",
    convert=_to_run,
    order_by="COALESCE(started_at, created_at, '') DESC, run_id",
    live_statuses=(RunStatus.pending.value, RunStatus.running.value,
                   RunStatus.stopping.value),
)
_RUNS = RUNS


#: Fields the launcher (runtime/entity_launch.py) and the watchdog own. A
#: freshly built ``TeamRun`` carries None for all of them until something
#: sets it, and a plain dict merge would happily write that None over a real
#: value already stored — so a save from the runner leaves a field out of the
#: write entirely when its own copy does not know it, rather than merging in
#: None and blanking it.
_PROCESS_FIELDS = ("pid", "host", "heartbeat_at")


def save_run(run: TeamRun) -> TeamRun:
    """Write the run whole. ``resume_attempts``, ``pid``, ``host`` and the
    heartbeat are the launcher's and the watchdog's to write, so a save from
    the runner does not blank them: the stored values are merged in."""
    payload = run.to_dict()
    for field in _PROCESS_FIELDS:
        if payload.get(field) is None:
            payload.pop(field, None)
    _RUNS.upsert(payload, merge=True)
    return run


#: Columns :func:`update_progress` may touch. ``status`` is deliberately absent
#: so a stop request that landed mid-round is not overwritten by progress.
_PROGRESS_FIELDS = frozenset({"rounds_done", "total_cost", "result", "error"})


def update_progress(team_run_id: str, **fields: Any) -> None:
    """Write mid-run progress without touching ``status``.

    Saving the whole record would resurrect the in-memory ``running`` status
    over a ``stopping`` one written by :func:`request_stop`, and the team would
    keep talking after the user pressed stop.
    """
    updates = {k: v for k, v in fields.items() if k in _PROGRESS_FIELDS}
    if updates:
        _RUNS.update(team_run_id, updates)


def get_run(team_run_id: str) -> Optional[TeamRun]:
    return _RUNS.get(team_run_id)


def list_runs(team_id: Optional[str] = None, limit: int = 50) -> List[TeamRun]:
    return _RUNS.list({"team_id": team_id} if team_id else None, limit=limit)


def request_stop(team_run_id: str) -> bool:
    """Record that a running team was asked to stop.

    The durable half of a stop: what a reloaded page, another process, or a
    restarted server reads. The half that actually interrupts the turns in
    flight is :mod:`teams.control`; :func:`teams.runner.stop_run` does both.
    """
    return _RUNS.request_stop(team_run_id)


def stop_requested(team_run_id: str) -> bool:
    return _RUNS.stop_requested(team_run_id)


def touch_heartbeat(team_run_id: str) -> Optional[str]:
    """Stamp the run's heartbeat and return its current status (the runner's
    process reads ``stopping`` back this way)."""
    from common import entity_runs
    return entity_runs.touch_heartbeat(team_run_id)


# ── Messages ─────────────────────────────────────────────────────────────────

def append_message(msg: TeamMessage) -> TeamMessage:
    """Append one entry to the board and return it with its assigned ``seq``."""
    with db.transaction() as conn:
        cur = conn.execute(
            """INSERT INTO team_messages
               (team_run_id, round, ts, sender, recipients, kind, content,
                run_id, cost, tokens, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               RETURNING seq""",
            (
                msg.team_run_id, msg.round, msg.ts or utc_iso(), msg.sender,
                db.dumps(list(msg.recipients or [BROADCAST])), msg.kind,
                msg.content, msg.run_id, msg.cost, msg.tokens, msg.error,
            ),
        )
        msg.seq = int(cur.fetchone()[0])
    return msg


def _row_to_message(row) -> TeamMessage:
    return TeamMessage(
        team_run_id=row["team_run_id"],
        seq=int(row["seq"]),
        round=int(row["round"] or 0),
        ts=row["ts"] or "",
        sender=row["sender"] or "",
        recipients=db.loads(row["recipients"], [BROADCAST]) or [BROADCAST],
        kind=row["kind"] or "message",
        content=row["content"] or "",
        run_id=row["run_id"],
        cost=float(row["cost"] or 0.0),
        tokens=int(row["tokens"] or 0),
        error=row["error"],
    )


def list_messages(team_run_id: str, since: int = 0) -> List[TeamMessage]:
    """The board in order. ``since`` is a ``seq`` cursor so a live page polls
    only what it has not seen."""
    rows = db.get_conn().execute(
        "SELECT * FROM team_messages WHERE team_run_id = ? AND seq > ? ORDER BY seq",
        (team_run_id, since),
    ).fetchall()
    return [_row_to_message(r) for r in rows]


__all__ = [
    "save_team", "get_team", "list_teams", "delete_team",
    "save_run", "get_run", "list_runs", "update_progress",
    "request_stop", "stop_requested", "touch_heartbeat", "RUNS",
    "append_message", "list_messages",
]
