"""Persistence for teams, team runs and the message bus.

Team *runs* are kept by the implementation flow and loop runs share
(:mod:`common.entity_runs`); this module says what a team run looks like and
keeps the team definitions and the message bus itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from common.entity_runs import EntityRunStore
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
            r["team_run_id"] for r in conn.execute(
                "SELECT team_run_id FROM team_runs WHERE team_id = ?", (team_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM team_messages WHERE team_run_id = ?", (rid,))
        conn.execute("DELETE FROM team_runs WHERE team_id = ?", (team_id,))
        cur = conn.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))
        removed = cur.rowcount > 0
    if removed:
        _notify("teams", team_id=team_id)
    return removed


# ── Runs ─────────────────────────────────────────────────────────────────────

_RUN_COLUMNS = (
    "team_run_id", "team_id", "workspace", "mode", "status", "goal",
    "task_id", "session_id", "conversation_id", "rounds_done",
    "total_cost", "result", "stop_reason", "error", "started_at",
    "finished_at",
)


def _to_run(rec: Dict[str, Any]) -> TeamRun:
    return TeamRun(
        team_run_id=rec["team_run_id"],
        team_id=rec["team_id"] or "",
        workspace=rec["workspace"],
        mode=rec["mode"] or "centralized",
        status=rec["status"] or "running",
        goal=rec["goal"] or "",
        task_id=rec["task_id"],
        session_id=rec["session_id"],
        conversation_id=rec["conversation_id"],
        rounds_done=int(rec["rounds_done"] or 0),
        total_cost=float(rec["total_cost"] or 0.0),
        result=rec["result"] or "",
        stop_reason=rec["stop_reason"] or "",
        error=rec["error"],
        started_at=rec["started_at"] or "",
        finished_at=rec["finished_at"],
    )


#: Team-run records over the shared implementation (common/entity_runs.py).
_RUNS: EntityRunStore[TeamRun] = EntityRunStore(
    table="team_runs",
    key="team_run_id",
    columns=_RUN_COLUMNS,
    convert=_to_run,
    resource="team_runs",
    parent_key="team_id",
    order_by="started_at DESC",
    live_statuses=("running", "stopping"),
    stopping_status="stopping",
)


def save_run(run: TeamRun) -> TeamRun:
    _RUNS.upsert(run.to_dict(), merge=False)
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
    "request_stop", "stop_requested",
    "append_message", "list_messages",
]
