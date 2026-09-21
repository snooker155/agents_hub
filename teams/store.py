"""Persistence for teams, team runs and the message bus."""
from __future__ import annotations

from typing import Any, List, Optional

from common import db
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
            """INSERT OR REPLACE INTO teams
               (team_id, name, description, workspace, mode, charter,
                leader_agent_id, members, config, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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

def save_run(run: TeamRun) -> TeamRun:
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO team_runs
               (team_run_id, team_id, workspace, mode, status, goal, task_id,
                session_id, conversation_id, rounds_done, total_cost, result,
                stop_reason, error, started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run.team_run_id, run.team_id, run.workspace, run.mode, run.status,
                run.goal, run.task_id, run.session_id, run.conversation_id,
                run.rounds_done, run.total_cost, run.result, run.stop_reason,
                run.error, run.started_at, run.finished_at,
            ),
        )
    _notify("team_runs", team_run_id=run.team_run_id, team_id=run.team_id)
    return run


#: Columns :func:`update_progress` may touch — ``status`` is deliberately absent
#: so a stop request that landed mid-round is not overwritten by progress.
_PROGRESS_FIELDS = frozenset({"rounds_done", "total_cost", "result", "error"})


def update_progress(team_run_id: str, **fields: Any) -> None:
    """Write mid-run progress without touching ``status``.

    Saving the whole record would resurrect the in-memory ``running`` status
    over a ``stopping`` one written by :func:`request_stop`, and the team would
    keep talking after the user pressed stop.
    """
    updates = {k: v for k, v in fields.items() if k in _PROGRESS_FIELDS}
    if not updates:
        return
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with db.transaction() as conn:
        conn.execute(
            f"UPDATE team_runs SET {assignments} WHERE team_run_id = ?",
            (*updates.values(), team_run_id),
        )
    _notify("team_runs", team_run_id=team_run_id)


def _row_to_run(row) -> TeamRun:
    return TeamRun(
        team_run_id=row["team_run_id"],
        team_id=row["team_id"] or "",
        workspace=row["workspace"],
        mode=row["mode"] or "centralized",
        status=row["status"] or "running",
        goal=row["goal"] or "",
        task_id=row["task_id"],
        session_id=row["session_id"],
        conversation_id=row["conversation_id"],
        rounds_done=int(row["rounds_done"] or 0),
        total_cost=float(row["total_cost"] or 0.0),
        result=row["result"] or "",
        stop_reason=row["stop_reason"] or "",
        error=row["error"],
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"],
    )


def get_run(team_run_id: str) -> Optional[TeamRun]:
    row = db.get_conn().execute(
        "SELECT * FROM team_runs WHERE team_run_id = ?", (team_run_id,)
    ).fetchone()
    return _row_to_run(row) if row else None


def list_runs(team_id: Optional[str] = None, limit: int = 50) -> List[TeamRun]:
    conn = db.get_conn()
    if team_id:
        rows = conn.execute(
            "SELECT * FROM team_runs WHERE team_id = ? ORDER BY started_at DESC LIMIT ?",
            (team_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM team_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def request_stop(team_run_id: str) -> bool:
    """Record that a running team was asked to stop.

    The durable half of a stop — what a reloaded page, another process, or a
    restarted server reads. The half that actually interrupts the turns in
    flight is :mod:`teams.control`; :func:`teams.runner.stop_run` does both.
    """
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE team_runs SET status = 'stopping' "
            "WHERE team_run_id = ? AND status = 'running'",
            (team_run_id,),
        )
        stopped = cur.rowcount > 0
    if stopped:
        _notify("team_runs", team_run_id=team_run_id)
    return stopped


def stop_requested(team_run_id: str) -> bool:
    row = db.get_conn().execute(
        "SELECT status FROM team_runs WHERE team_run_id = ?", (team_run_id,)
    ).fetchone()
    return bool(row and row["status"] in ("stopping", "stopped"))


# ── Messages ─────────────────────────────────────────────────────────────────

def append_message(msg: TeamMessage) -> TeamMessage:
    """Append one entry to the board and return it with its assigned ``seq``."""
    with db.transaction() as conn:
        cur = conn.execute(
            """INSERT INTO team_messages
               (team_run_id, round, ts, sender, recipients, kind, content,
                run_id, cost, tokens, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                msg.team_run_id, msg.round, msg.ts or utc_iso(), msg.sender,
                db.dumps(list(msg.recipients or [BROADCAST])), msg.kind,
                msg.content, msg.run_id, msg.cost, msg.tokens, msg.error,
            ),
        )
        msg.seq = int(cur.lastrowid or 0)
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
