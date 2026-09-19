"""Persistence for scenarios, simulation runs and the tick log."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from playground.models import (
    AgentDecision, ActionResult, Role, Scenario, SimRun, TickRecord, utc_iso,
)
from playground.worlds import WorldSpec

def _notify(resource: str, **meta) -> None:
    """Coarse ``<resource>.changed`` invalidation for the open tabs.

    Only on the events a list is actually keyed off — a scenario changing, a
    run starting, ending or being stopped. Ticks are not among them: the
    scenario page follows its own ``sim:<id>`` channel, and invalidating the
    catalogue once a second would be a refetch storm for a badge.
    """
    try:
        from common.session_broker import notify_change
        notify_change(resource, **meta)
    except Exception:
        pass


# Run-level knobs stored together in the ``config`` JSON column rather than as
# columns, so adding a limit does not need a migration.
_CONFIG_FIELDS = (
    # ``narrative`` rides here for the same reason: it is prose the reading
    # surfaces use, and a text column for it would be a migration.
    "narrative",
    "activation", "max_ticks", "stall_timeout", "max_turn_seconds", "seed",
    "max_concurrent", "cost_ceiling", "default_model", "default_provider",
    "max_wall_seconds", "idle_grace_seconds",
)


# ── Scenarios ─────────────────────────────────────────────────────────────────

def save_scenario(scenario: Scenario) -> Scenario:
    scenario.updated_at = utc_iso()
    config = {f: getattr(scenario, f) for f in _CONFIG_FIELDS}
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO scenarios
               (scenario_id, name, description, workspace, environment,
                env_params, roles, config, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                scenario.scenario_id, scenario.name, scenario.description,
                scenario.workspace, scenario.environment,
                db.dumps(scenario.env_params),
                db.dumps([r.to_dict() for r in scenario.roles]),
                db.dumps(config), scenario.created_at, scenario.updated_at,
            ),
        )
    _notify("scenarios", scenario_id=scenario.scenario_id)
    return scenario


def _row_to_scenario(row) -> Scenario:
    """Rebuild a scenario from its row.

    The config blob goes through ``Scenario.from_dict`` rather than being
    unpacked field by field here, so defaults and the compatibility reads for
    renamed knobs live in exactly one place — a row written before a knob
    existed loads the same way an old API payload does.
    """
    config = db.loads(row["config"], {}) or {}
    return Scenario.from_dict({
        **config,
        "scenario_id": row["scenario_id"],
        "name": row["name"] or "",
        "description": row["description"] or "",
        "workspace": row["workspace"],
        "environment": row["environment"] or "market",
        "env_params": db.loads(row["env_params"], {}) or {},
        "roles": db.loads(row["roles"], []) or [],
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    })


def get_scenario(scenario_id: str) -> Optional[Scenario]:
    row = db.get_conn().execute(
        "SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)
    ).fetchone()
    return _row_to_scenario(row) if row else None


def list_scenarios(workspace: Optional[str] = None) -> List[Scenario]:
    conn = db.get_conn()
    if workspace:
        rows = conn.execute(
            "SELECT * FROM scenarios WHERE workspace = ? OR workspace IS NULL "
            "ORDER BY updated_at DESC",
            (workspace,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM scenarios ORDER BY updated_at DESC").fetchall()
    return [_row_to_scenario(r) for r in rows]


def delete_scenario(scenario_id: str) -> bool:
    with db.transaction() as conn:
        run_ids = [
            r["sim_run_id"] for r in conn.execute(
                "SELECT sim_run_id FROM sim_runs WHERE scenario_id = ?", (scenario_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM sim_ticks WHERE sim_run_id = ?", (rid,))
        conn.execute("DELETE FROM sim_runs WHERE scenario_id = ?", (scenario_id,))
        cur = conn.execute("DELETE FROM scenarios WHERE scenario_id = ?", (scenario_id,))
        deleted = cur.rowcount > 0
    if deleted:
        _notify("scenarios", scenario_id=scenario_id)
    return deleted


# ── Worlds ────────────────────────────────────────────────────────────────────

def save_world(spec: WorldSpec) -> WorldSpec:
    """Store an authored world. Name, description and workspace are columns as
    well as spec fields: the catalogue lists and filters on them, and a list
    page should not have to parse every world's JSON to draw a card."""
    spec.updated_at = utc_iso()
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO worlds
               (world_id, name, description, workspace, spec, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (spec.world_id, spec.name, spec.description, spec.workspace,
             db.dumps(spec.to_dict()), spec.created_at, spec.updated_at),
        )
    _notify("worlds", world_id=spec.world_id)
    return spec


def _row_to_world(row) -> WorldSpec:
    """Rebuild a world from its row, columns winning over the stored copy.

    The JSON holds the same name and workspace the columns do; the columns are
    what a rename or a move actually wrote, so they are what a reader gets.
    """
    spec = WorldSpec.from_dict(db.loads(row["spec"], {}) or {})
    spec.world_id = row["world_id"]
    spec.name = row["name"] or spec.name
    spec.description = row["description"] or spec.description
    spec.workspace = row["workspace"]
    spec.created_at = row["created_at"] or spec.created_at
    spec.updated_at = row["updated_at"] or spec.updated_at
    return spec


def get_world(world_id: str) -> Optional[WorldSpec]:
    row = db.get_conn().execute(
        "SELECT * FROM worlds WHERE world_id = ?", (world_id,)
    ).fetchone()
    return _row_to_world(row) if row else None


def list_worlds(workspace: Optional[str] = None) -> List[WorldSpec]:
    """Every world this workspace can run scenarios in.

    A world with no workspace is shared by all of them — the same rule
    scenarios follow, and the reason a world built once can be cast again.
    """
    conn = db.get_conn()
    if workspace:
        rows = conn.execute(
            "SELECT * FROM worlds WHERE workspace = ? OR workspace IS NULL "
            "ORDER BY updated_at DESC",
            (workspace,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM worlds ORDER BY updated_at DESC").fetchall()
    return [_row_to_world(r) for r in rows]


def scenarios_using_world(world_id: str) -> List[Dict[str, str]]:
    """Which scenarios are cast in this world — what a delete has to warn about.

    A scenario pointing at a world that no longer exists does not fail until
    somebody presses Run, and a failure at Run is the worst possible time to
    learn that the world was deleted last week.
    """
    from playground.worlds import CUSTOM_PREFIX
    rows = db.get_conn().execute(
        "SELECT scenario_id, name FROM scenarios WHERE environment = ?",
        (f"{CUSTOM_PREFIX}{world_id}",),
    ).fetchall()
    return [{"scenario_id": r["scenario_id"], "name": r["name"] or ""} for r in rows]


def delete_world(world_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM worlds WHERE world_id = ?", (world_id,))
        deleted = cur.rowcount > 0
    if deleted:
        _notify("worlds", world_id=world_id)
    return deleted


# ── Sim runs ──────────────────────────────────────────────────────────────────

def save_sim_run(run: SimRun) -> SimRun:
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO sim_runs
               (sim_run_id, scenario_id, workspace, environment, status,
                activation, stop_reason, ticks_done, total_cost, error,
                scores, final_state, config, started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run.sim_run_id, run.scenario_id, run.workspace, run.environment,
                run.status, run.activation, run.stop_reason, run.ticks_done,
                run.total_cost, run.error,
                db.dumps(run.scores), db.dumps(run.final_state),
                db.dumps(run.config),
                run.started_at, run.finished_at,
            ),
        )
    _notify("sim_runs", sim_run_id=run.sim_run_id, scenario_id=run.scenario_id,
            status=run.status)
    return run


def _row_to_sim_run(row) -> SimRun:
    return SimRun(
        sim_run_id=row["sim_run_id"],
        scenario_id=row["scenario_id"] or "",
        workspace=row["workspace"],
        environment=row["environment"] or "",
        status=row["status"] or "running",
        activation=row["activation"] or "synchronous",
        stop_reason=row["stop_reason"] or "",
        ticks_done=int(row["ticks_done"] or 0),
        total_cost=float(row["total_cost"] or 0.0),
        error=row["error"],
        scores=db.loads(row["scores"], {}) or {},
        final_state=db.loads(row["final_state"], {}) or {},
        config=db.loads(row["config"], {}) or {},
        started_at=row["started_at"] or "",
        finished_at=row["finished_at"],
    )


def get_sim_run(sim_run_id: str) -> Optional[SimRun]:
    row = db.get_conn().execute(
        "SELECT * FROM sim_runs WHERE sim_run_id = ?", (sim_run_id,)
    ).fetchone()
    return _row_to_sim_run(row) if row else None


def save_story(sim_run_id: str, story: Dict[str, Any]) -> bool:
    """Keep a model's retelling of a run.

    Stored rather than recomposed because it costs a model call: reopening the
    tab is not a reason to pay for the same paragraphs again. The chronicle it
    was written from is *not* stored — that one is a pure function of the tick
    log, so keeping it would only create a second version of the truth.
    """
    with db.transaction() as conn:
        cur = conn.execute("UPDATE sim_runs SET story = ? WHERE sim_run_id = ?",
                           (db.dumps(story), sim_run_id))
    return cur.rowcount > 0


def get_story(sim_run_id: str) -> Dict[str, Any]:
    row = db.get_conn().execute(
        "SELECT story FROM sim_runs WHERE sim_run_id = ?", (sim_run_id,)
    ).fetchone()
    return (db.loads(row["story"], {}) or {}) if row else {}


def list_sim_runs(scenario_id: Optional[str] = None, limit: int = 50,
                  workspace: Optional[str] = None) -> List[SimRun]:
    """Run history, newest first — one scenario's, or every scenario's.

    The scenario-less listing is the cross-scenario history page: without a
    workspace it is everything this install has run, with one it is that
    workspace's runs plus the workspace-less ones, the same rule the scenario
    catalogue uses.
    """
    where: List[str] = []
    params: List[Any] = []
    if scenario_id:
        where.append("scenario_id = ?")
        params.append(scenario_id)
    if workspace:
        where.append("(workspace = ? OR workspace IS NULL)")
        params.append(workspace)
    sql = "SELECT * FROM sim_runs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    rows = db.get_conn().execute(sql, tuple(params)).fetchall()
    return [_row_to_sim_run(r) for r in rows]


def scenario_names(scenario_ids: List[str]) -> Dict[str, str]:
    """``{scenario_id: name}`` for a batch of ids — what a run needs to say
    which scenario it belongs to, without loading whole scenarios."""
    ids = [i for i in dict.fromkeys(scenario_ids) if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = db.get_conn().execute(
        f"SELECT scenario_id, name FROM scenarios WHERE scenario_id IN ({marks})",
        tuple(ids),
    ).fetchall()
    return {r["scenario_id"]: (r["name"] or "") for r in rows}


def latest_runs_by_scenario(scenario_ids: Optional[List[str]] = None) -> Dict[str, SimRun]:
    """The most recent run of each scenario, keyed by scenario id.

    One windowed query rather than one query per card: the catalogue page shows
    every scenario's last run, and "is this one running right now" is the first
    thing you look for there.
    """
    sql = """
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY scenario_id ORDER BY started_at DESC
            ) AS rn
            FROM sim_runs
        ) WHERE rn = 1
    """
    rows = db.get_conn().execute(sql).fetchall()
    wanted = set(scenario_ids) if scenario_ids is not None else None
    latest: Dict[str, SimRun] = {}
    for row in rows:
        scenario_id = row["scenario_id"] or ""
        if not scenario_id or (wanted is not None and scenario_id not in wanted):
            continue
        latest[scenario_id] = _row_to_sim_run(row)
    return latest


def request_stop(sim_run_id: str) -> bool:
    """Mark a running sim as stopping — the durable half of a stop.

    This row is the cross-process record and what a reloaded page reads. It is
    *not* what makes the button feel immediate: :mod:`playground.control` holds
    an in-memory event that interrupts the model calls already in flight, and
    ``runner.stop_simulation`` sets both.
    """
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE sim_runs SET status = 'stopping' "
            "WHERE sim_run_id = ? AND status IN ('starting', 'running')",
            (sim_run_id,),
        )
        stopping = cur.rowcount > 0
    if stopping:
        _notify("sim_runs", sim_run_id=sim_run_id, status="stopping")
    return stopping


def mark_running(sim_run_id: str) -> bool:
    """Promote a starting run to running — what the first tick means.

    Conditional on the row still being ``starting`` so that a stop requested
    during the first tick is not overwritten by the tick that finished after it.
    """
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE sim_runs SET status = 'running' "
            "WHERE sim_run_id = ? AND status = 'starting'",
            (sim_run_id,),
        )
        started = cur.rowcount > 0
    if started:
        _notify("sim_runs", sim_run_id=sim_run_id, status="running")
    return started


def stop_requested(sim_run_id: str) -> bool:
    row = db.get_conn().execute(
        "SELECT status FROM sim_runs WHERE sim_run_id = ?", (sim_run_id,)
    ).fetchone()
    return bool(row and row["status"] == "stopping")


# ── Ticks ─────────────────────────────────────────────────────────────────────

def save_tick(record: TickRecord) -> TickRecord:
    with db.transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO sim_ticks
               (sim_run_id, tick, ts, decisions, resolutions, frame, events,
                idle, cost)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                record.sim_run_id, record.tick, record.ts,
                db.dumps([d.to_dict() for d in record.decisions]),
                db.dumps([r.to_dict() for r in record.resolutions]),
                db.dumps(record.frame), db.dumps(record.events),
                db.dumps(record.idle), record.cost,
            ),
        )
    return record


def _row_to_tick(row) -> Dict[str, Any]:
    return {
        "sim_run_id": row["sim_run_id"],
        "tick": int(row["tick"]),
        "ts": row["ts"],
        "decisions": db.loads(row["decisions"], []) or [],
        "resolutions": db.loads(row["resolutions"], []) or [],
        "frame": db.loads(row["frame"], {}) or {},
        "events": db.loads(row["events"], []) or [],
        "idle": db.loads(row["idle"], []) or [],
        "cost": float(row["cost"] or 0.0),
    }


def list_ticks(sim_run_id: str, since: int = -1) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM sim_ticks WHERE sim_run_id = ? AND tick > ? ORDER BY tick",
        (sim_run_id, since),
    ).fetchall()
    return [_row_to_tick(r) for r in rows]


def get_tick(sim_run_id: str, tick: int) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM sim_ticks WHERE sim_run_id = ? AND tick = ?", (sim_run_id, tick)
    ).fetchone()
    return _row_to_tick(row) if row else None


__all__ = [
    "save_world", "get_world", "list_worlds", "delete_world",
    "scenarios_using_world",
    "save_scenario", "get_scenario", "list_scenarios", "delete_scenario",
    "save_sim_run", "get_sim_run", "list_sim_runs", "latest_runs_by_scenario",
    "scenario_names",
    "request_stop", "stop_requested",
    "save_tick", "list_ticks", "get_tick",
]
