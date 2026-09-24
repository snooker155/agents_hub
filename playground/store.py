"""Persistence for scenarios, simulation runs and the tick log."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common import db
from common.entity_runs import EntityRunStore
from common.run_status import RunStatus
from playground.models import (
    Scenario, SimRun, TickRecord, utc_iso,
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
    # Who plays a role (personas or agents), its per-tick tool call cap, the
    # task this scenario works on, and the reference documents injected into
    # every role's prompt. Rides in this JSON blob too, the same reasoning:
    # one more knob here needs no migration.
    "mode", "max_tool_calls_per_tick", "task_id", "documents",
)


# ── Scenarios ─────────────────────────────────────────────────────────────────

def save_scenario(scenario: Scenario) -> Scenario:
    scenario.updated_at = utc_iso()
    config = {f: getattr(scenario, f) for f in _CONFIG_FIELDS}
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql(
                "scenarios",
                ("scenario_id", "name", "description", "workspace", "environment",
                 "env_params", "roles", "config", "created_at", "updated_at"),
                ("scenario_id",),
            ),
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
            r["run_id"] for r in conn.execute(
                "SELECT run_id FROM entity_runs WHERE kind = 'scenario' AND entity_id = ?",
                (scenario_id,)
            ).fetchall()
        ]
        for rid in run_ids:
            conn.execute("DELETE FROM sim_ticks WHERE sim_run_id = ?", (rid,))
        conn.execute("DELETE FROM entity_runs WHERE kind = 'scenario' AND entity_id = ?",
                     (scenario_id,))
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
            db.upsert_sql(
                "worlds",
                ("world_id", "name", "description", "workspace", "spec",
                 "created_at", "updated_at"),
                ("world_id",),
            ),
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
# Scenario runs live in the ``entity_runs`` table every kind of run shares
# (common/entity_runs.py, kind ``scenario``). The columns every kind has
# (status, pid, host, heartbeat, checkpoint, cost, error, times) are the
# table's; the scenario's own fields (environment, activation, ticks,
# scores, final state, the frozen config, the story) ride in the document.

def _to_sim_run(rec: Dict[str, Any]) -> SimRun:
    return SimRun(
        sim_run_id=rec["sim_run_id"],
        scenario_id=rec.get("scenario_id") or "",
        workspace=rec.get("workspace"),
        environment=rec.get("environment") or "",
        status=rec.get("status") or RunStatus.pending.value,
        activation=rec.get("activation") or "synchronous",
        task_id=rec.get("task_id"),
        session_id=rec.get("session_id"),
        parent_run_id=rec.get("parent_run_id"),
        stop_reason=rec.get("stop_reason") or "",
        ticks_done=int(rec.get("ticks_done") or 0),
        total_cost=float(rec.get("total_cost") or 0.0),
        error=rec.get("error"),
        scores=dict(rec.get("scores") or {}),
        final_state=dict(rec.get("final_state") or {}),
        config=dict(rec.get("config") or {}),
        pid=rec.get("pid"),
        host=rec.get("host"),
        heartbeat_at=rec.get("heartbeat_at"),
        resume_attempts=int(rec.get("resume_attempts") or 0),
        log_file=rec.get("log_file"),
        created_at=rec.get("created_at") or rec.get("started_at") or "",
        started_at=rec.get("started_at") or "",
        finished_at=rec.get("finished_at"),
    )


#: Scenario-run records: kind ``scenario`` of the shared table.
RUNS: EntityRunStore[SimRun] = EntityRunStore(
    "scenario",
    convert=_to_sim_run,
    order_by="COALESCE(started_at, created_at, '') DESC, run_id",
    live_statuses=(RunStatus.pending.value, RunStatus.running.value,
                   RunStatus.stopping.value),
)
_RUNS = RUNS


def save_sim_run(run: SimRun) -> SimRun:
    """Write the run whole. The process fields the launcher and the watchdog
    own (pid, host, heartbeat, attempts) and the story are merged in from the
    stored record, so a save from the runner never blanks them."""
    rec = run.to_dict()
    _RUNS.upsert(rec, merge=True)
    return run


def _row_to_sim_run(row) -> SimRun:
    """Kept for callers that read rows themselves."""
    from common.entity_runs import record_from_row
    return _to_sim_run(record_from_row(row))


def get_sim_run(sim_run_id: str) -> Optional[SimRun]:
    return _RUNS.get(sim_run_id)


def save_story(sim_run_id: str, story: Dict[str, Any]) -> bool:
    """Keep a model's retelling of a run.

    Stored rather than recomposed because it costs a model call: reopening the
    tab is not a reason to pay for the same paragraphs again. The chronicle it
    was written from is *not* stored — that one is a pure function of the tick
    log, so keeping it would only create a second version of the truth.
    """
    return _RUNS.update(sim_run_id, {"story": dict(story or {})}, notify=False) is not None


def get_story(sim_run_id: str) -> Dict[str, Any]:
    rec = _RUNS.read(sim_run_id)
    return dict((rec or {}).get("story") or {})


def list_sim_runs(scenario_id: Optional[str] = None, limit: int = 50,
                  workspace: Optional[str] = None) -> List[SimRun]:
    """Run history, newest first — one scenario's, or every scenario's.

    The scenario-less listing is the cross-scenario history page: without a
    workspace it is everything this install has run, with one it is that
    workspace's runs plus the workspace-less ones, the same rule the scenario
    catalogue uses.
    """
    where: List[str] = ["kind = 'scenario'"]
    params: List[Any] = []
    if scenario_id:
        where.append("entity_id = ?")
        params.append(scenario_id)
    if workspace:
        where.append("(workspace = ? OR workspace IS NULL)")
        params.append(workspace)
    sql = "SELECT * FROM entity_runs WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(started_at, created_at, '') DESC, run_id LIMIT ?"
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
                PARTITION BY entity_id ORDER BY COALESCE(started_at, created_at, '') DESC
            ) AS rn
            FROM entity_runs WHERE kind = 'scenario'
        ) AS latest WHERE rn = 1
    """
    rows = db.get_conn().execute(sql).fetchall()
    wanted = set(scenario_ids) if scenario_ids is not None else None
    latest: Dict[str, SimRun] = {}
    for row in rows:
        scenario_id = row["entity_id"] or ""
        if not scenario_id or (wanted is not None and scenario_id not in wanted):
            continue
        latest[scenario_id] = _row_to_sim_run(row)
    return latest


def request_stop(sim_run_id: str) -> bool:
    """Mark a live sim as stopping — the durable half of a stop.

    This row is the cross-process record and what a reloaded page reads. It is
    *not* what makes the button feel immediate: :mod:`playground.control` holds
    an in-memory event that interrupts the model calls already in flight, and
    ``runner.stop_simulation`` sets both. A runner in another process reads
    the status back on its next heartbeat.
    """
    return _RUNS.request_stop(sim_run_id)


def mark_running(sim_run_id: str) -> bool:
    """Promote a pending run to running — what the first tick means.

    Conditional on the row still being ``pending`` so that a stop requested
    during the first tick is not overwritten by the tick that finished after it.
    """
    from common.entity_runs import utc_now_iso
    now = utc_now_iso()
    return _RUNS.set_status(sim_run_id, RunStatus.running.value,
                            from_statuses=(RunStatus.pending.value,),
                            started_at=now, heartbeat_at=now)


def stop_requested(sim_run_id: str) -> bool:
    return _RUNS.stop_requested(sim_run_id)


def touch_heartbeat(sim_run_id: str) -> Optional[str]:
    """Stamp the run's heartbeat and return its current status (the runner's
    process reads ``stopping`` back this way)."""
    from common import entity_runs
    return entity_runs.touch_heartbeat(sim_run_id)


# ── Durable triggers ─────────────────────────────────────────────────────────
#
# playground.control's trigger queue only reaches a run this process is
# actually executing — fine while every simulation ran on a daemon thread of
# the one API process, not once a scenario runs in its own subprocess
# (playground.launcher, runtime/scenario_run.py). An external poke (a webhook,
# an operator on a different dashboard replica) has to reach that process
# through the database instead, so it rides in the run's own document under
# ``pending_triggers`` — a plain list, read and rewritten inside one
# transaction so a push and a drain (or two pushes) racing each other can never
# interleave: ``db.transaction()`` serialises writers the same way every other
# write to this table does (see common/entity_runs.py).

def push_trigger(sim_run_id: str, agent: str, text: str,
                 sender: str = "(external)") -> bool:
    """Queue a message for one agent, durably, whichever process is running
    the sim. Returns whether the run exists at all; the queue accepts an entry
    for a finished run too — nothing but ``playground.runner._deliver_external``
    ever reads it, and a run that has already ended never ticks again to do so."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT doc FROM entity_runs WHERE run_id = ? AND kind = 'scenario'",
            (sim_run_id,),
        ).fetchone()
        if row is None:
            return False
        doc = db.loads(row["doc"], {}) or {}
        pending = list(doc.get("pending_triggers") or [])
        pending.append({"agent": agent, "text": text, "sender": sender})
        doc["pending_triggers"] = pending
        conn.execute("UPDATE entity_runs SET doc = ? WHERE run_id = ?",
                     (db.dumps(doc), sim_run_id))
    return True


def drain_triggers(sim_run_id: str) -> List[Dict[str, Any]]:
    """Take every durably queued trigger for this run, atomically — read and
    clear happen inside the same transaction a concurrent :func:`push_trigger`
    would need, so a trigger pushed between the two can never be lost."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT doc FROM entity_runs WHERE run_id = ? AND kind = 'scenario'",
            (sim_run_id,),
        ).fetchone()
        if row is None:
            return []
        doc = db.loads(row["doc"], {}) or {}
        pending = list(doc.get("pending_triggers") or [])
        if not pending:
            return []
        doc["pending_triggers"] = []
        conn.execute("UPDATE entity_runs SET doc = ? WHERE run_id = ?",
                     (db.dumps(doc), sim_run_id))
    return pending


def has_pending_triggers(sim_run_id: str) -> bool:
    """Peek the durable queue without draining it — what an idle loop polls to
    decide whether to wake, without consuming the trigger before its own tick's
    ``_deliver_external`` gets to it."""
    row = db.get_conn().execute(
        "SELECT doc FROM entity_runs WHERE run_id = ? AND kind = 'scenario'",
        (sim_run_id,),
    ).fetchone()
    if row is None:
        return False
    doc = db.loads(row["doc"], {}) or {}
    return bool(doc.get("pending_triggers"))


# ── Ticks ─────────────────────────────────────────────────────────────────────

def save_tick(record: TickRecord) -> TickRecord:
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql(
                "sim_ticks",
                ("sim_run_id", "tick", "ts", "decisions", "resolutions", "frame",
                 "events", "idle", "cost"),
                ("sim_run_id", "tick"),
            ),
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
    "request_stop", "stop_requested", "mark_running", "touch_heartbeat", "RUNS",
    "push_trigger", "drain_triggers", "has_pending_triggers",
    "save_story", "get_story",
    "save_tick", "list_ticks", "get_tick",
]
