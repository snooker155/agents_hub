"""A/B experiments between stored versions of one agent's definition.

An experiment names two or more rows of ``agent_versions`` (arms) and the
share of runs each should get. While it is enabled, every run of the agent
that opens a run record is routed to one arm, and the factory builds the
agent from that arm's snapshot instead of the live definition. See
``docs/experiments.md``.

Routing has two halves, because the place that knows the run and the place
that builds the agent are different calls:

1. :func:`route_run` is called by ``managers.runs.lifecycle.open_run``. It
   picks the arm by a deterministic weighted choice on a routing key (the
   conversation id for a chat turn, so a conversation stays on one arm; the
   run id otherwise) and *pins* it for this agent in a context variable.
2. ``AgentFactory.create_agent`` asks :func:`take_pin` for this agent. When
   there is a pin whose version row still exists, it builds with
   ``definition_version=<n>`` (which also keys the build cache apart) and
   records the assignment with :func:`record_assignment`.

Every run path opens its run record before it builds the agent in the same
thread or in a copy of its context (task subprocesses, chat turns, entity
chats, flow nodes, node runs), so the pin reaches the build. A run container
that reaches its run record over HTTP (``AGENT_RUN_STATE_TRANSPORT=http``)
cannot read the experiment and builds the live definition; since the
assignment is only written by the build, such a run is simply outside the
experiment rather than counted in an arm it did not use.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from common import db

log = logging.getLogger(__name__)

#: Channels an experiment never routes: an eval or a replay pins what it runs
#: on purpose, and its spend is not production spend either.
_SKIP_CHANNELS = frozenset({"eval", "replay"})

_SHARE_TOLERANCE = 1e-6

# agent_id -> {"run_id", "experiment_id", "version", "routing_key"}
_PINS: contextvars.ContextVar[Optional[Dict[str, Dict[str, Any]]]] = contextvars.ContextVar(
    "agents_hub_experiment_pins", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Validation and storage ──────────────────────────────────────────────────

def normalize_arms(agent_id: str, arms: Any) -> List[Dict[str, Any]]:
    """Validate ``arms`` and return them as ``[{version, share}]``.

    Raises ``ValueError`` when there are fewer than two arms, a version is
    repeated or has no row in ``agent_versions``, a share is outside (0, 1],
    or the shares do not sum to 1.
    """
    from agents import versions as agent_versions

    if not isinstance(arms, list) or len(arms) < 2:
        raise ValueError("an experiment needs at least two arms")
    out: List[Dict[str, Any]] = []
    seen = set()
    for arm in arms:
        if not isinstance(arm, dict):
            raise ValueError("each arm is an object with version and share")
        try:
            version = int(arm.get("version"))
            share = float(arm.get("share"))
        except (TypeError, ValueError):
            raise ValueError("each arm needs a numeric version and share")
        if version in seen:
            raise ValueError(f"version {version} appears in two arms")
        if not (0.0 < share <= 1.0):
            raise ValueError("each share must be above 0 and at most 1")
        if agent_versions.get_version_row(agent_id, version) is None:
            raise ValueError(f"agent '{agent_id}' has no version {version}")
        seen.add(version)
        out.append({"version": version, "share": share})
    total = sum(a["share"] for a in out)
    if abs(total - 1.0) > _SHARE_TOLERANCE:
        raise ValueError(f"shares must sum to 1 (they sum to {total:.4f})")
    return out


def _row_to_experiment(row) -> Dict[str, Any]:
    return {
        "experiment_id": row["experiment_id"],
        "agent_id": row["agent_id"],
        "enabled": bool(row["enabled"]),
        "arms": db.loads(row["arms"], []) or [],
        "note": row["note"] or "",
        "actor": row["actor"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "ended_at": row["ended_at"],
    }


def get_active(agent_id: str) -> Optional[Dict[str, Any]]:
    """The agent's open experiment (enabled or paused), or None."""
    row = db.get_conn().execute(
        "SELECT * FROM agent_experiments WHERE agent_id = ? AND ended_at IS NULL",
        (str(agent_id),)).fetchone()
    return _row_to_experiment(row) if row is not None else None


def get_latest(agent_id: str) -> Optional[Dict[str, Any]]:
    """The open experiment, else the most recently ended one."""
    active = get_active(agent_id)
    if active is not None:
        return active
    row = db.get_conn().execute(
        "SELECT * FROM agent_experiments WHERE agent_id = ? "
        "ORDER BY ended_at DESC, created_at DESC LIMIT 1",
        (str(agent_id),)).fetchone()
    return _row_to_experiment(row) if row is not None else None


def get_experiment(experiment_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM agent_experiments WHERE experiment_id = ?",
        (str(experiment_id),)).fetchone()
    return _row_to_experiment(row) if row is not None else None


def _same_arms(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    def key(arms):
        return sorted((int(x["version"]), round(float(x["share"]), 6)) for x in arms)
    return key(a) == key(b)


def put_experiment(agent_id: str, *, enabled: bool, arms: Any, note: str = "",
                   actor: Optional[str] = None) -> Dict[str, Any]:
    """Start or change the agent's experiment.

    Same arms as the open experiment: only ``enabled`` and ``note`` change,
    and its report carries on. Different arms: the open experiment is ended
    and a new one starts, so a report never mixes two arm layouts.
    """
    clean = normalize_arms(agent_id, arms)
    now = _now()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM agent_experiments WHERE agent_id = ? AND ended_at IS NULL",
            (str(agent_id),)).fetchone()
        current = _row_to_experiment(row) if row is not None else None
        if current is not None and _same_arms(current["arms"], clean):
            conn.execute(
                "UPDATE agent_experiments SET enabled = ?, note = ?, updated_at = ? "
                "WHERE experiment_id = ?",
                (1 if enabled else 0, note or "", now, current["experiment_id"]))
            experiment_id = current["experiment_id"]
        else:
            if current is not None:
                conn.execute(
                    "UPDATE agent_experiments SET ended_at = ?, enabled = 0, updated_at = ? "
                    "WHERE experiment_id = ?", (now, now, current["experiment_id"]))
            experiment_id = f"exp_{uuid4().hex[:16]}"
            conn.execute(
                "INSERT INTO agent_experiments "
                "(experiment_id, agent_id, enabled, arms, note, actor, created_at, updated_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (experiment_id, str(agent_id), 1 if enabled else 0, db.dumps(clean),
                 note or "", actor, now, now))
    return get_experiment(experiment_id) or {}


def end_experiment(agent_id: str) -> Optional[Dict[str, Any]]:
    """End the open experiment. Returns it, or None when there was none."""
    current = get_active(agent_id)
    if current is None:
        return None
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            "UPDATE agent_experiments SET ended_at = ?, enabled = 0, updated_at = ? "
            "WHERE experiment_id = ?", (now, now, current["experiment_id"]))
    return get_experiment(current["experiment_id"])


# ── Arm choice ──────────────────────────────────────────────────────────────

def _unit(*parts: str) -> float:
    """A stable number in [0, 1) from ``parts``: the same inputs give the
    same number in every process, which is what makes a choice repeatable."""
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:15], 16) / float(16 ** 15)


def choose_arm(experiment: Dict[str, Any], routing_key: str) -> Dict[str, Any]:
    """Weighted deterministic choice of an arm for ``routing_key``."""
    arms = list(experiment.get("arms") or [])
    if not arms:
        raise ValueError("experiment has no arms")
    u = _unit(str(experiment.get("experiment_id") or ""), str(routing_key))
    total = sum(float(a.get("share") or 0.0) for a in arms) or 1.0
    acc = 0.0
    for arm in arms:
        acc += float(arm.get("share") or 0.0) / total
        if u < acc:
            return arm
    return arms[-1]


def routing_key_for(run_id: str, *, task_id: Optional[str] = None,
                    session_type: Optional[str] = None) -> str:
    """The conversation for a chat turn (task_id carries the conversation id
    on chat runs), the run itself for everything else."""
    if (session_type or "") == "chat" and task_id:
        return f"conv:{task_id}"
    return f"run:{run_id}"


# ── Pins: open_run -> factory ───────────────────────────────────────────────

def _set_pin(agent_id: str, pin: Optional[Dict[str, Any]]) -> None:
    current = dict(_PINS.get() or {})
    if pin is None:
        if agent_id not in current:
            return
        current.pop(agent_id, None)
    else:
        current[agent_id] = pin
    _PINS.set(current)


def clear_pins() -> None:
    _PINS.set(None)


def route_run(run_id: str, agent_id: Optional[str], *, task_id: Optional[str] = None,
              session_type: Optional[str] = None, channel: Optional[str] = None
              ) -> Optional[Dict[str, Any]]:
    """Pick the arm for a run that is opening and pin it for the build.

    Called by ``open_run``. Always replaces any earlier pin for the agent (a
    run that is not routed clears it, so a pin never outlives its run into
    the next one built in the same context). Never raises: a failure here
    means the run builds from the live definition.
    """
    if not agent_id:
        return None
    try:
        _set_pin(agent_id, None)
        if (channel or "") in _SKIP_CHANNELS:
            return None
        experiment = get_active(agent_id)
        if experiment is None or not experiment["enabled"]:
            return None
        key = routing_key_for(run_id, task_id=task_id, session_type=session_type)
        arm = choose_arm(experiment, key)
        pin = {
            "run_id": str(run_id),
            "agent_id": str(agent_id),
            "experiment_id": experiment["experiment_id"],
            "version": int(arm["version"]),
            "routing_key": key,
        }
        _set_pin(agent_id, pin)
        return pin
    except Exception:
        log.warning("experiments: routing run %s of '%s' failed, using the live definition",
                    run_id, agent_id, exc_info=True)
        return None


def peek_pin(agent_id: str) -> Optional[Dict[str, Any]]:
    return (_PINS.get() or {}).get(str(agent_id))


def take_pin(agent_id: str) -> Optional[Dict[str, Any]]:
    """The pin for ``agent_id`` when its version row still exists.

    A pin whose version has disappeared is logged and dropped: the build
    falls back to the live definition and nothing is recorded for the run.
    """
    pin = peek_pin(agent_id)
    if pin is None:
        return None
    try:
        from agents import versions as agent_versions
        row = agent_versions.get_version_row(agent_id, int(pin["version"]))
    except Exception:
        row = None
    if row is None:
        log.warning("experiments: version %s of '%s' is gone, run %s uses the live definition",
                    pin.get("version"), agent_id, pin.get("run_id"))
        _set_pin(agent_id, None)
        return None
    return {**pin, "hash": row.get("hash")}


def record_assignment(pin: Dict[str, Any]) -> None:
    """Write the run's arm (idempotent) and stamp the arm's definition hash
    on the run record, so the run says which definition it actually ran."""
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO experiment_assignments "
                "(run_id, agent_id, experiment_id, version, routing_key, assigned_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (run_id) DO NOTHING",
                (pin["run_id"], pin["agent_id"], pin["experiment_id"], int(pin["version"]),
                 pin.get("routing_key"), _now()))
    except Exception:
        log.warning("experiments: could not record the assignment of run %s",
                    pin.get("run_id"), exc_info=True)
        return
    if pin.get("hash"):
        try:
            from managers import run_manager as rm
            rm.update_run(pin["run_id"], {"definition_hash": pin["hash"]})
        except Exception:
            log.debug("experiments: could not stamp definition_hash on run %s",
                      pin.get("run_id"), exc_info=True)


def assignment_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT run_id, agent_id, experiment_id, version, routing_key, assigned_at "
        "FROM experiment_assignments WHERE run_id = ?", (str(run_id),)).fetchone()
    return dict(row) if row is not None else None


# ── Report ──────────────────────────────────────────────────────────────────

def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def report(experiment: Dict[str, Any]) -> Dict[str, Any]:
    """Per-arm comparison: runs, outcomes, cost, tokens, duration and the
    online eval score of the runs routed to each arm."""
    from common.pricing import load_price_map, run_cost_usd, run_tokens
    from managers.runs.store import get_runs_by_ids

    experiment_id = experiment["experiment_id"]
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT run_id, version FROM experiment_assignments WHERE experiment_id = ?",
        (experiment_id,)).fetchall()
    by_version: Dict[int, List[str]] = {}
    for row in rows:
        by_version.setdefault(int(row["version"]), []).append(str(row["run_id"]))

    eval_rows = conn.execute(
        "SELECT a.version AS version, r.score AS score, r.passed AS passed "
        "FROM experiment_assignments a JOIN online_eval_results r ON r.run_id = a.run_id "
        "WHERE a.experiment_id = ?", (experiment_id,)).fetchall()
    evals_by_version: Dict[int, List[Any]] = {}
    for row in eval_rows:
        evals_by_version.setdefault(int(row["version"]), []).append(row)

    prices = load_price_map()
    arms_out = []
    for arm in experiment.get("arms") or []:
        version = int(arm["version"])
        run_ids = by_version.get(version, [])
        runs = get_runs_by_ids(run_ids) if run_ids else {}
        completed = failed = 0
        costs: List[float] = []
        tokens: List[float] = []
        durations: List[float] = []
        for run in runs.values():
            status = str(run.get("status") or "")
            if status == "completed":
                completed += 1
            elif status in ("failed", "error"):
                failed += 1
            costs.append(run_cost_usd(run, prices))
            inbound, outbound = run_tokens(run)
            tokens.append(float(inbound + outbound))
            duration = _duration_ms(run)
            if duration is not None:
                durations.append(float(duration))
        graded = evals_by_version.get(version, [])
        scores = [float(r["score"] or 0.0) for r in graded]
        passes = [1.0 if r["passed"] else 0.0 for r in graded]
        arms_out.append({
            "version": version,
            "share": arm.get("share"),
            "runs": len(run_ids),
            "completed": completed,
            "failed": failed,
            "mean_cost_usd": _mean(costs),
            "mean_tokens": _mean(tokens),
            "mean_duration_ms": _mean(durations),
            "graded": len(graded),
            "mean_score": _mean(scores),
            "pass_rate": _mean(passes),
        })
    return {"experiment": experiment, "arms": arms_out}


def _duration_ms(run: Dict[str, Any]) -> Optional[float]:
    proc = run.get("process") if isinstance(run.get("process"), dict) else {}
    if proc and proc.get("duration_ms"):
        return float(proc["duration_ms"])
    started, finished = run.get("started_at"), run.get("finished_at")
    if not started or not finished:
        return None
    try:
        a = datetime.fromisoformat(str(started))
        b = datetime.fromisoformat(str(finished))
    except ValueError:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return max(0.0, (b - a).total_seconds() * 1000.0)


__all__ = [
    "normalize_arms", "get_active", "get_latest", "get_experiment", "put_experiment",
    "end_experiment", "choose_arm", "routing_key_for", "route_run", "peek_pin",
    "take_pin", "record_assignment", "assignment_for_run", "clear_pins", "report",
]
