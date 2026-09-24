"""What the three container nodes share: find or launch the child run, record
it durably, wait for it, and read its result.

A container node runs a whole entity (a team, a loop, a flow) as a child of the
flow run it belongs to. The child is an ordinary entity run in
``common.entity_runs`` with ``parent_run_id`` set to the flow run, so the run
tree, a recursive stop (managers/runs/groups.stop_tree) and the summed cost all
reach it without anything special here.

**Durable link.** Right after the launch the node writes ``flow_node_id`` (and
``run_depth``) on the child's record and adds ``{node_id: child_run_id}`` to
the parent's ``children`` map. A flow resume re-runs every node the checkpoint
does not record as done, so a re-run first looks for that child: still live, it
waits for it; stopped or failed, it resumes it through the kind's launcher;
completed, it takes its result. Only when there is no child at all (or the old
one cannot be resumed) is a new one launched.

**Depth.** A flow that runs a flow that runs a flow could go on for ever even
without a cycle. The current depth is the larger of ``AGENTS_HUB_RUN_DEPTH``
(set by the parent that launched this process) and the length of the
``parent_run_id`` chain above this run. The child gets depth plus one in its
environment (runtime.entity_launch.child_env), and a node refuses to launch
past ``flow.validate.MAX_NESTING_DEPTH``.

**Tasks.** A child gets a task of its own, not the parent's: a team or loop
claims the task it is given and closes it when it finishes, and a flow closes
its task on its last node, which would end the parent's task while the parent
flow is still running.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from common import entity_runs
from common.run_status import TERMINAL_STATUSES

log = logging.getLogger(__name__)

#: Seconds between two reads of the child's status.
POLL_SECONDS = 2.0

#: The environment variable that carries the nesting depth to a child process.
DEPTH_ENV = "AGENTS_HUB_RUN_DEPTH"

#: Replaced by tests so a wait does not really sleep.
_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic


class NestedRunError(RuntimeError):
    """The child could not be launched, or it did not complete."""


@dataclass
class ChildKind:
    """How one kind of child is launched, resumed, stopped and read."""

    kind: str
    #: () -> child run id. Called inside child_env, so the child gets the depth.
    launch: Callable[[], str]
    #: (child run id) -> None. Raises when the run cannot be resumed.
    resume: Callable[[str], Any]
    #: (child run id) -> bool
    stop: Callable[[str], Any]
    #: (child record) -> the node's result
    result: Callable[[Dict[str, Any]], Any]
    #: The exception types ``resume`` raises for "nothing to resume from".
    resume_errors: tuple = (Exception,)


# ── Templates and state ─────────────────────────────────────────────────────

def fill(template: str, state: Any) -> str:
    """Fill ``{key}`` references from flow state (unknown keys stay as written),
    the same rule the human_interrupt question uses."""
    from flow.entities.interrupts.human_interrupt import _fill
    return _fill(str(template or ""), state)


def as_keys(raw: Any) -> List[str]:
    """A list of state keys from a list or a comma separated string."""
    if isinstance(raw, str):
        return [k.strip() for k in raw.split(",") if k.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(k).strip() for k in raw if str(k).strip()]
    return []


def seed_from(state: Any, keys: Iterable[str], params: Any = None) -> Dict[str, Any]:
    """The child's seed state: fixed ``params`` first, then the named keys of
    the parent's state laid over them."""
    seed: Dict[str, Any] = dict(params) if isinstance(params, dict) else {}
    if hasattr(state, "slice"):
        seed.update(state.slice(list(keys)))
    return seed


def workspace_name(ctx: Any) -> Optional[str]:
    """The workspace name of the parent run (``ctx.workspace`` is a path)."""
    from common.workspace_context import workspace_name_from_path
    return workspace_name_from_path(getattr(ctx, "workspace", "") or None)


# ── Depth ───────────────────────────────────────────────────────────────────

def _env_depth() -> int:
    try:
        return max(0, int(os.environ.get(DEPTH_ENV, "0") or 0))
    except ValueError:
        return 0


def _chain_depth(run_id: Optional[str], limit: int = 32) -> int:
    """How many runs sit above ``run_id`` through ``parent_run_id``."""
    depth = 0
    seen = set()
    current = run_id
    while current and depth < limit:
        if current in seen:
            break
        seen.add(current)
        rec = entity_runs.get(current)
        parent = (rec or {}).get("parent_run_id")
        if not parent:
            break
        depth += 1
        current = str(parent)
    return depth


def current_depth(ctx: Any) -> int:
    """The nesting depth of the run this node belongs to (a top level run is 0)."""
    return max(_env_depth(), _chain_depth(getattr(ctx, "run_id", "") or None))


def check_depth(ctx: Any) -> int:
    """The depth a child of this node would have; raises when it is too deep."""
    from flow.validate import MAX_NESTING_DEPTH

    child_depth = current_depth(ctx) + 1
    if child_depth > MAX_NESTING_DEPTH:
        raise NestedRunError(
            f"nesting depth limit reached: a child of this node would be at depth "
            f"{child_depth}, the limit is {MAX_NESTING_DEPTH}"
        )
    return child_depth


# ── Parent and child records ────────────────────────────────────────────────

def parent_run_id(ctx: Any) -> Optional[str]:
    """The flow run id to hang the child under, when it is a real entity run.
    Flow chat passes a conversation id as its run id; a child of that has no
    parent record to point at."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    if run_id and entity_runs.get(run_id) is not None:
        return run_id
    return None


def find_child(kind: str, parent_id: Optional[str], node_id: str) -> Optional[Dict[str, Any]]:
    """The child a previous attempt of this node launched, newest first."""
    if not parent_id:
        return None
    from flow import run_store

    mapped = run_store.flow_run_child(parent_id, node_id)
    if mapped:
        rec = entity_runs.get(mapped)
        if rec is not None and rec.get("kind") == kind:
            return rec
    candidates = [
        r for r in entity_runs.list_runs(kind=kind, parent_run_id=parent_id)
        if str(r.get("flow_node_id") or "") == str(node_id)
    ]
    return candidates[-1] if candidates else None


def record_child(parent_id: Optional[str], node_id: str, child_id: str, depth: int) -> None:
    """Link the child to its node on both records, before any waiting."""
    try:
        entity_runs.update(child_id, {"flow_node_id": node_id, "run_depth": depth}, notify=False)
    except Exception:  # noqa: BLE001 - the parent_run_id link still stands
        log.warning("could not tag child run %s with its node", child_id, exc_info=True)
    if parent_id:
        try:
            from flow import run_store
            run_store.add_flow_run_child(parent_id, node_id, child_id)
        except Exception:  # noqa: BLE001 - the child's own flow_node_id still finds it
            log.warning("could not add child %s to flow run %s", child_id, parent_id, exc_info=True)


# ── Waiting ─────────────────────────────────────────────────────────────────

def _timeout(config: Dict[str, Any]) -> Optional[float]:
    raw = (config or {}).get("_timeout_seconds")
    try:
        value = float(raw) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def wait_for(child: ChildKind, child_id: str, ctx: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    """Poll the child until it is terminal. Stops it when the parent run is
    asked to stop or the node's timeout passes. Returns the final record."""
    deadline = None
    timeout = _timeout(config)
    if timeout:
        deadline = _monotonic() + timeout
    parent_id = str(getattr(ctx, "run_id", "") or "")
    while True:
        rec = entity_runs.get(child_id)
        if rec is None:
            raise NestedRunError(f"{child.kind} run {child_id} disappeared")
        if rec.get("status") in TERMINAL_STATUSES:
            return rec
        if parent_id and entity_runs.stop_requested(parent_id):
            _stop(child, child_id)
            raise NestedRunError(
                f"flow run was stopped; stopped its {child.kind} run {child_id}")
        if deadline is not None and _monotonic() >= deadline:
            _stop(child, child_id)
            raise NestedRunError(
                f"node timed out after {timeout:g}s; stopped its {child.kind} run {child_id}")
        _sleep(POLL_SECONDS)


def _stop(child: ChildKind, child_id: str) -> None:
    try:
        child.stop(child_id)
    except Exception:  # noqa: BLE001 - the recursive stop of the tree is the backstop
        log.warning("could not stop %s run %s", child.kind, child_id, exc_info=True)


# ── The whole node ──────────────────────────────────────────────────────────

def run_nested(child: ChildKind, ctx: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    """Find or launch the child, wait for it and return the node's payload."""
    from runtime.entity_launch import child_env

    node_id = str(getattr(ctx, "node_id", "") or "")
    parent_id = parent_run_id(ctx)
    depth = check_depth(ctx)

    child_id = ""
    existing = find_child(child.kind, parent_id, node_id)
    if existing is not None:
        child_id = str(existing["run_id"])
        status = existing.get("status")
        if status in ("stopped", "failed"):
            try:
                with child_env({DEPTH_ENV: depth}):
                    child.resume(child_id)
                log.info("resumed %s run %s for node %s", child.kind, child_id, node_id)
            except child.resume_errors as e:
                # Nothing to resume from (it failed before its first
                # checkpoint): a fresh child is the only way forward.
                log.info("could not resume %s run %s (%s); launching a new one",
                         child.kind, child_id, e)
                child_id = ""
    if not child_id:
        with child_env({DEPTH_ENV: depth}):
            child_id = str(child.launch())
        record_child(parent_id, node_id, child_id, depth)

    rec = wait_for(child, child_id, ctx, config)
    status = rec.get("status")
    if status != "completed":
        detail = rec.get("error") or rec.get("stop_reason") or ""
        raise NestedRunError(
            f"{child.kind} run {child_id} ended {status}" + (f": {detail}" if detail else ""))
    return {"result": child.result(rec), "child_run_id": child_id, "child_kind": child.kind}


def config_value(config: Dict[str, Any], key: str) -> str:
    value = str((config or {}).get(key) or "").strip()
    if not value:
        raise NestedRunError(f"node config needs {key}")
    return value
