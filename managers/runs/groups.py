"""
One interface over everything that owns a set of agent runs.

A flow execution, a loop run, a team run and a task container are four
different things with four different stores, but the questions asked of them
are always the same four: is it still going, when did it start and finish, what
did it cost, and stop it. Before this module each store answered them its own
way, so every caller that wanted "the cost of this run group" wrote the
arithmetic again, and every caller that wanted to stop one had to know which
store to call.

:class:`RunGroup` is that shared answer, and the adapters below are thin
readers over the existing stores — ``flow.run_store``, ``loops.store``,
``teams.store`` and the task tree. Nothing here owns state: a group's status
comes from its own store, and a stop is delegated to the stop function that
store already had. Rewriting those stores behind one schema would be a much
larger change with no payoff for the caller, who only ever wanted one verb.

Cost is the exception, and the reason this module computes rather than reads:
a group's spend is the catalog-priced sum of its child *agent runs*, which all
live in the ``runs`` table whatever the group kind. Doing it in one place means
a flow, a loop and a team are priced by the same rule.

Children are run ids, except for a loop, whose children are flow run ids — a
loop's work happens inside the flow it re-runs, so its children are groups of
their own and its cost is the sum of theirs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

KINDS = ("flow", "loop", "team", "container")

# Statuses that mean "still going" across all four stores. Each store spells
# its own set slightly differently (a flow run is pending before it is running,
# a loop or team run goes through stopping on its way out), so the union is
# kept here rather than in each adapter.
ACTIVE_STATUSES = frozenset({"running", "pending", "stopping", "in_progress"})


@dataclass
class RunGroup:
    """A set of agent runs executed under one owner.

    ``id`` is the owning record's id in its own store: a flow_run_id, a
    loop_run_id, a team_run_id or a container task id. ``children`` holds run
    ids, except for a loop, where it holds the flow run ids of its iterations.
    """

    kind: str
    id: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_cost: float = 0.0
    error: Optional[str] = None
    children: List[str] = field(default_factory=list)
    workspace: Optional[str] = None
    title: Optional[str] = None
    # The owning record's own id in its definition store (flow_id, loop_id,
    # team_id); None for a container, whose id is already the task.
    parent_id: Optional[str] = None

    @property
    def active(self) -> bool:
        return str(self.status or "") in ACTIVE_STATUSES

    def stop(self) -> bool:
        """Stop this group through its own store. True when something was
        signalled or marked; False when it was already finished."""
        return stop_group(self.kind, self.id)

    def to_dict(self) -> Dict[str, Any]:
        """JSON shape for the API. Field names match the dataclass so a reader
        can move between the two without a translation table."""
        return {
            "kind": self.kind,
            "id": self.id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_cost": round(float(self.total_cost or 0.0), 6),
            "error": self.error,
            "children": list(self.children),
            "workspace": self.workspace,
            "title": self.title,
            "parent_id": self.parent_id,
            "active": self.active,
        }


# ── Cost ─────────────────────────────────────────────────────────────────────

def runs_cost(run_ids: List[str]) -> float:
    """Catalog-priced spend of a set of agent runs.

    Best effort by design: an unknown (provider, model) pair prices at zero and
    a broken price catalog returns zero, because a cost readout must never be
    the thing that fails a page or a stop.

    The one pricing loop over run records. loops.runner and teams.runner used
    to keep their own copies of this same arithmetic; both now call here so a
    flow, a loop and a team are priced by the same rule.
    """
    ids = [str(r) for r in run_ids if r]
    if not ids:
        return 0.0
    try:
        from common.pricing import load_price_map, run_cost_usd

        from .store import get_runs_by_ids

        prices = load_price_map()
        records = get_runs_by_ids(ids)
        return round(sum(run_cost_usd(rec, prices) for rec in records.values()), 6)
    except Exception:
        return 0.0


# Kept as a private alias: this module's own adapters below were written
# against the old name.
_runs_cost = runs_cost


def turn_cost(provider: str, model: str, inbound: int, outbound: int) -> float:
    """Catalog-priced spend of one agent turn, priced from token counts rather
    than a run record.

    A team turn is priced before any run record exists (the member's run is
    opened and closed around the same call), so it cannot go through
    :func:`runs_cost`. This is the same ``run_cost_usd`` path, given a
    synthetic record shaped like the real ones, so there is still exactly one
    place that knows how to price a (provider, model, tokens) triple.
    """
    try:
        from common.pricing import load_price_map, run_cost_usd
        return round(run_cost_usd(
            {"provider": provider, "model": model,
             "process": {"token_usage": {"inbound_tokens": inbound,
                                         "outbound_tokens": outbound}}},
            load_price_map(),
        ), 6)
    except Exception:
        return 0.0


# ── Flow runs ────────────────────────────────────────────────────────────────

def _flow_children(flow_run_id: str) -> List[str]:
    """Run ids of the per-node agent runs of one flow execution. They carry the
    flow_run_id in their record, written by flow/task_driver.py at open_run."""
    from .store import query_runs
    page = query_runs(flow_run_id=str(flow_run_id), limit=10_000, ascending=True)
    return [str(r.get("run_id")) for r in page["items"] if r.get("run_id")]


def _flow_group(flow_run_id: str) -> Optional[RunGroup]:
    from flow import run_store
    rec = run_store.get_flow_run(str(flow_run_id))
    if not rec:
        return None
    return _flow_group_from_record(rec)


def _flow_group_from_record(rec: Dict[str, Any]) -> RunGroup:
    children = _flow_children(str(rec.get("flow_run_id")))
    return RunGroup(
        kind="flow",
        id=str(rec.get("flow_run_id")),
        status=str(rec.get("status") or ""),
        started_at=rec.get("started_at") or rec.get("created_at"),
        finished_at=rec.get("finished_at"),
        total_cost=_runs_cost(children),
        error=rec.get("error"),
        children=children,
        workspace=rec.get("workspace"),
        title=rec.get("title"),
        parent_id=rec.get("flow_id"),
    )


def _flow_list(workspace: Optional[str], limit: int) -> List[RunGroup]:
    from flow import run_store
    out: List[RunGroup] = []
    for rec in reversed(run_store.load_flow_runs()):
        if workspace and str(rec.get("workspace") or "") != workspace:
            continue
        out.append(_flow_group_from_record(rec))
        if len(out) >= limit:
            break
    return out


def _flow_stop(flow_run_id: str) -> bool:
    """Stop one flow execution: the orchestrator subprocess, then every node run
    it still has in flight, then the record itself.

    The flow launcher starts a run but has no stop of its own (the dashboard's
    stop route does it inline), so the sequence is reproduced here against the
    same stores rather than calling into a route.
    """
    import os
    import signal

    from flow import run_store

    from .lifecycle import stop_run_by_id

    rec = run_store.get_flow_run(str(flow_run_id))
    if not rec or str(rec.get("status") or "") not in ACTIVE_STATUSES:
        return False

    stopped = False
    run_store.close_flow_run(str(flow_run_id), status="stopped", exit_code=1,
                             error="Stopped by user")
    pid = rec.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
            stopped = True
        except Exception:
            pass
    for run_id in _flow_children(str(flow_run_id)):
        try:
            if stop_run_by_id(run_id):
                stopped = True
        except Exception:
            pass
    # The record is closed either way: an orchestrator that already died still
    # leaves a run marked running, and that is exactly what a stop must clear.
    return True if not stopped else stopped


# ── Loop runs ────────────────────────────────────────────────────────────────

def _loop_children(loop_run_id: str) -> List[str]:
    """The flow run id of each iteration — child *groups*, not runs."""
    from loops import store as loop_store
    out: List[str] = []
    for it in loop_store.list_iterations(str(loop_run_id)):
        frid = it.get("flow_run_id")
        if frid:
            out.append(str(frid))
    return out


def _loop_group_from_run(run) -> RunGroup:
    children = _loop_children(str(run.loop_run_id))
    return RunGroup(
        kind="loop",
        id=str(run.loop_run_id),
        status=str(getattr(run, "status", "") or ""),
        started_at=getattr(run, "started_at", None),
        finished_at=getattr(run, "finished_at", None),
        # Summed from the iterations' flow runs rather than read off
        # loop_runs.total_cost: the stored figure is only written when the
        # runner reaches its bookkeeping, so a crashed or still-running loop
        # would report zero.
        total_cost=sum(group_cost("flow", frid) for frid in children),
        error=getattr(run, "error", None),
        children=children,
        workspace=getattr(run, "workspace", None),
        title=getattr(run, "goal", None),
        parent_id=str(getattr(run, "loop_id", "") or "") or None,
    )


def _loop_group(loop_run_id: str) -> Optional[RunGroup]:
    from loops import store as loop_store
    run = loop_store.get_run(str(loop_run_id))
    return _loop_group_from_run(run) if run else None


def _loop_list(workspace: Optional[str], limit: int) -> List[RunGroup]:
    from loops import store as loop_store
    runs = loop_store.list_runs(limit=limit if not workspace else max(limit, 200))
    out = [_loop_group_from_run(r) for r in runs
           if not workspace or str(getattr(r, "workspace", "") or "") == workspace]
    return out[:limit]


def _loop_stop(loop_run_id: str) -> bool:
    from loops import store as loop_store
    return bool(loop_store.request_stop(str(loop_run_id)))


# ── Team runs ────────────────────────────────────────────────────────────────

def _team_children(team_run_id: str) -> List[str]:
    """Run ids of the member turns. A member run records its team_id but not
    which *execution* it belonged to; the message bus does, and every turn
    writes exactly one message carrying its run id."""
    from common import db
    rows = db.get_conn().execute(
        "SELECT run_id, MIN(seq) AS first_seq FROM team_messages "
        "WHERE team_run_id = ? AND run_id IS NOT NULL AND run_id != '' "
        "GROUP BY run_id ORDER BY first_seq",
        (str(team_run_id),)).fetchall()
    return [str(r["run_id"]) for r in rows]


def _team_group_from_run(run) -> RunGroup:
    children = _team_children(str(run.team_run_id))
    return RunGroup(
        kind="team",
        id=str(run.team_run_id),
        status=str(getattr(run, "status", "") or ""),
        started_at=getattr(run, "started_at", None),
        finished_at=getattr(run, "finished_at", None),
        total_cost=_runs_cost(children),
        error=getattr(run, "error", None),
        children=children,
        workspace=getattr(run, "workspace", None),
        title=getattr(run, "goal", None),
        parent_id=str(getattr(run, "team_id", "") or "") or None,
    )


def _team_group(team_run_id: str) -> Optional[RunGroup]:
    from teams import store as team_store
    run = team_store.get_run(str(team_run_id))
    return _team_group_from_run(run) if run else None


def _team_list(workspace: Optional[str], limit: int) -> List[RunGroup]:
    from teams import store as team_store
    runs = team_store.list_runs(limit=limit if not workspace else max(limit, 200))
    out = [_team_group_from_run(r) for r in runs
           if not workspace or str(getattr(r, "workspace", "") or "") == workspace]
    return out[:limit]


def _team_stop(team_run_id: str) -> bool:
    from teams import store as team_store
    return bool(team_store.request_stop(str(team_run_id)))


# ── Task containers ──────────────────────────────────────────────────────────
# A container is a parent task executing its subtasks one at a time. It has no
# record of its own beyond the task tree, so its group is derived from it.

def _container_task_ids(parent_id: str) -> List[str]:
    from uuid import UUID

    from tasks import service as ts
    subtasks = ts.get_subtasks(UUID(str(parent_id)))
    return [str(t.id) for t in subtasks]


def _container_children(parent_id: str) -> List[str]:
    """Every agent run performed on the container's subtasks, plus any run on
    the parent itself (the orchestrator that decomposed it)."""
    from .store import query_runs
    out: List[str] = []
    for task_id in [str(parent_id)] + _container_task_ids(parent_id):
        page = query_runs(task_id=task_id, limit=10_000, ascending=True)
        out.extend(str(r.get("run_id")) for r in page["items"] if r.get("run_id"))
    return out


def _container_group(parent_id: str) -> Optional[RunGroup]:
    from uuid import UUID

    from tasks import service as ts
    try:
        task = ts.get_task(UUID(str(parent_id)))
    except Exception:
        return None
    if task is None:
        return None
    children = _container_children(str(parent_id))
    status = str(getattr(task, "status", "") or "")
    finished = status in ("done", "stopped", "resolved", "reviewed")
    return RunGroup(
        kind="container",
        id=str(parent_id),
        status=status,
        started_at=_iso(getattr(task, "created_at", None)),
        finished_at=_iso(getattr(task, "updated_at", None)) if finished else None,
        total_cost=_runs_cost(children),
        error=getattr(task, "blocked_reason", None),
        children=children,
        workspace=getattr(task, "workspace", None),
        title=getattr(task, "title", None),
    )


def _container_list(workspace: Optional[str], limit: int) -> List[RunGroup]:
    """Every task that has subtasks is a container. Listed newest first."""
    from tasks import service as ts
    tasks = ts.list_tasks()
    parents = {str(t.parent_id) for t in tasks if t.parent_id}
    candidates = [t for t in tasks
                  if str(t.id) in parents
                  and (not workspace or str(getattr(t, "workspace", "") or "") == workspace)]
    candidates.sort(key=lambda t: _iso(getattr(t, "created_at", None)) or "", reverse=True)
    groups = [_container_group(str(t.id)) for t in candidates[:limit]]
    return [g for g in groups if g is not None]


def _container_stop(parent_id: str) -> bool:
    """Pause the container: the existing stop path for a task tree, which stops
    the active subtask's run and freezes dispatch."""
    from uuid import UUID

    from tasks import service as ts
    try:
        result = ts.pause_container(UUID(str(parent_id)))
    except Exception:
        return False
    return bool(result.get("paused_subtasks") is not None)


def _iso(value) -> Optional[str]:
    """Datetimes come out of the task store as objects and out of every other
    store as ISO strings; the API shape is the string."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# ── Adapter table ────────────────────────────────────────────────────────────

_GET: Dict[str, Callable[[str], Optional[RunGroup]]] = {
    "flow": _flow_group,
    "loop": _loop_group,
    "team": _team_group,
    "container": _container_group,
}

_LIST: Dict[str, Callable[[Optional[str], int], List[RunGroup]]] = {
    "flow": _flow_list,
    "loop": _loop_list,
    "team": _team_list,
    "container": _container_list,
}

_STOP: Dict[str, Callable[[str], bool]] = {
    "flow": _flow_stop,
    "loop": _loop_stop,
    "team": _team_stop,
    "container": _container_stop,
}


# ── Public API ───────────────────────────────────────────────────────────────

def get_group(kind: str, group_id: str) -> Optional[RunGroup]:
    """Return one run group, or None when that store has no such record."""
    adapter = _GET.get(str(kind))
    if adapter is None:
        raise ValueError(f"Unknown run group kind: {kind!r} (expected one of {', '.join(KINDS)})")
    return adapter(str(group_id))


def list_groups(kind: Optional[str] = None, workspace: Optional[str] = None,
                limit: int = 50) -> List[RunGroup]:
    """Run groups, newest first. ``kind=None`` covers all four kinds, each
    limited to ``limit`` records so one busy kind cannot crowd out the rest."""
    kinds = [str(kind)] if kind else list(KINDS)
    for k in kinds:
        if k not in _LIST:
            raise ValueError(f"Unknown run group kind: {k!r} (expected one of {', '.join(KINDS)})")
    out: List[RunGroup] = []
    for k in kinds:
        try:
            out.extend(_LIST[k](workspace, int(limit)))
        except Exception:
            # One unavailable store must not blank the whole list.
            continue
    out.sort(key=lambda g: g.started_at or "", reverse=True)
    return out


def stop_group(kind: str, group_id: str) -> bool:
    """Stop a run group through its own store's stop path."""
    adapter = _STOP.get(str(kind))
    if adapter is None:
        raise ValueError(f"Unknown run group kind: {kind!r} (expected one of {', '.join(KINDS)})")
    return bool(adapter(str(group_id)))


def group_cost(kind: str, group_id: str) -> float:
    """Catalog-priced spend of a run group, in USD. Zero for an unknown group,
    never an exception: this is a readout, not a gate."""
    group = get_group(kind, group_id)
    return round(float(group.total_cost), 6) if group else 0.0


__all__ = ["RunGroup", "KINDS", "ACTIVE_STATUSES",
           "get_group", "list_groups", "stop_group", "group_cost",
           "runs_cost", "turn_cost"]
