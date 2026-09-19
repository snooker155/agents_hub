"""
Task service module built on top of the file-based TaskStore.

Provides high-level CRUD and utility operations:
- create_task
- get_task
- list_tasks
- update_task
- add_subtask (sets parent_id and created_by=orchestrator)
- stop_task (status=stopped)
- block_task (status=blocked with a reason)
- create_sequence (assign common sequence_id and incremental order for given task IDs)
- Agent management: assign_agent, clear_agent

All functions use the module-level default_store (shared tasks/storage), but accept an optional
store argument for injection/testing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID, uuid4

from tasks.models import (
    CreatedBy,
    Task,
    TaskStatus,
)
from tasks.storage import (
    TaskStore,
    get_activity_log as _get_activity_log,
    append_activity_log as _append_activity_log,
    delete_activity_log as _delete_activity_log,
    get_task_results as _get_task_results,
    get_task_result as _get_task_result,
    get_task_result_files as _get_task_result_files,
    set_task_result as _set_task_result,
    delete_task_result as _delete_task_result,
    get_routing_log as _get_routing_log,
    append_routing_log as _append_routing_log,
)
from datetime import datetime, timezone
from common.paths import TASKS_FILE as DEFAULT_TASKS_FILE
from common.session_broker import notify_change

# The task store always lives at the fixed .agents_hub/tasks.json location.
TASKS_FILE = str(DEFAULT_TASKS_FILE)

default_store = TaskStore(TASKS_FILE)

# Marker prefix written into a subtask's blocked_reason when it was blocked
# automatically because one of its ancestors is blocked. Used to tell an
# auto-cascade block apart from a task the user/agent blocked deliberately, so
# only the former are restored when the ancestor is unblocked.
CASCADE_BLOCK_PREFIX = "[auto] Blocked by parent task"

# Descendant states a parent-block cascades into: work that has not actively
# started yet. Running, review, and terminal states are left untouched so the
# cascade never interrupts an in-flight agent or discards completed work.
_CASCADE_BLOCKABLE = {TaskStatus.todo, TaskStatus.ready, TaskStatus.pending}

# Marker prefix for a task blocked because its `depends` list has unfinished
# tasks. Distinguishes dependency blocks from manual/parent-cascade blocks so
# only dependency blocks are lifted automatically when the dependencies finish.
DEPENDENCY_BLOCK_PREFIX = "[deps] Waiting for"

# A dependency counts as satisfied only when the dependent-on task is fully
# done. Intermediate finished-looking states (resolved, reviewed) do not
# release dependents — the work must be finalised first.
DEP_SATISFIED_STATUSES = {TaskStatus.done}

# States a task may sit in while waiting to run; only these are flipped to
# blocked when unsatisfied dependencies are set on the task.
_DEP_BLOCKABLE = {TaskStatus.todo, TaskStatus.ready, TaskStatus.pending}


# -------------------- CRUD --------------------

def create_task(
    title: str,
    description: str = "",
    *,
    created_by: CreatedBy = CreatedBy.user,
    parent_id: Optional[UUID] = None,
    sequence_id: Optional[str] = None,
    order: Optional[int] = None,
    blocked_reason: Optional[str] = None,
    status: TaskStatus = TaskStatus.todo,
    workspace: Optional[str] = None,
    project: Optional[str] = None,
    project_id: Optional[str] = None,
    should_decompose: bool = False,
    external_source: Optional[Dict[str, Any]] = None,
    depends: Optional[Sequence[UUID]] = None,
    store: TaskStore = default_store,
) -> Task:
    """Create a new task and persist it in the store.

    When `depends` names tasks that are not yet completed, the new task is
    created blocked and is released automatically once they all finish.
    """
    dep_ids = [d if isinstance(d, UUID) else UUID(str(d)) for d in (depends or [])]
    if dep_ids:
        index = {t.id: t for t in store.list()}
        missing = [d for d in dep_ids if d not in index]
        if missing:
            raise ValueError(f"Dependency tasks not found: {', '.join(str(m) for m in missing)}")
        pending_deps = [index[d] for d in dep_ids if index[d].status not in DEP_SATISFIED_STATUSES]
        if pending_deps and status in _DEP_BLOCKABLE:
            status = TaskStatus.blocked
            blocked_reason = _dependency_block_reason(pending_deps)
    task = store.create(
        title=title,
        description=description,
        created_by=created_by,
        parent_id=parent_id,
        sequence_id=sequence_id,
        order=order,
        blocked_reason=blocked_reason,
        status=status,
        workspace=workspace,
        project=project,
        project_id=project_id,
        should_decompose=should_decompose,
        external_source=external_source,
        depends=dep_ids,
    )
    append_task_activity_log(task.id, "created", "Task created")
    if task.status == TaskStatus.blocked and (task.blocked_reason or "").startswith(DEPENDENCY_BLOCK_PREFIX):
        append_task_activity_log(task.id, "dependency_block", task.blocked_reason)
    notify_change("tasks", task_id=str(task.id))
    return task


def get_task(task_id: UUID, *, store: TaskStore = default_store) -> Optional[Task]:
    """Return a task by id or None if not found."""
    return store.get(task_id)


def get_tasks(task_ids, *, store: TaskStore = default_store) -> dict:
    """Several tasks by id in one query, keyed by string id (missing ids absent).

    For enriching a page of runs/instances with their task titles without
    loading the whole task table.
    """
    return store.get_many(list(task_ids))


def list_tasks(*, store: TaskStore = default_store) -> List[Task]:
    """Return all tasks from the store."""
    return store.list()


def update_task(task_id: UUID, *, store: TaskStore = default_store, **fields) -> Optional[Task]:
    """Update fields of a given task. Returns updated task or None if not found."""
    fields.pop("id", None)

    if "depends" in fields:
        dep_ids = [d if isinstance(d, UUID) else UUID(str(d)) for d in (fields["depends"] or [])]
        if task_id in dep_ids:
            raise ValueError("A task cannot depend on itself")
        all_tasks = store.list()
        index = {t.id: t for t in all_tasks}
        missing = [d for d in dep_ids if d not in index]
        if missing:
            raise ValueError(f"Dependency tasks not found: {', '.join(str(m) for m in missing)}")
        if _would_create_cycle(task_id, dep_ids, all_tasks):
            raise ValueError("Dependency cycle detected: these tasks already depend on this task")
        fields["depends"] = dep_ids

    if "status" in fields:
        new_status = fields["status"]
        if not isinstance(new_status, TaskStatus):
            new_status = TaskStatus(new_status)
            fields["status"] = new_status

        task = store.get(task_id)
        if task and task.status != new_status:
            # Clear blocked_reason when leaving blocked
            if new_status != TaskStatus.blocked and "blocked_reason" not in fields:
                fields["blocked_reason"] = None

            # Only fully reset when moving back to todo (not resolved, done, ready, etc.)
            _RESET_STATUSES = {TaskStatus.todo}

            # Clear agent assignment fields only when resetting to todo
            _CLEAR_AGENT_STATUSES = {TaskStatus.todo}

            # Clear agent assignment when resetting to todo (preserve routing history for reviewed/done)
            if new_status in _CLEAR_AGENT_STATUSES:
                for key in ("assigned_agent_type", "assigned_agent_params", "assigned_agent_run_id", "pre_assignment_status"):
                    if key not in fields:
                        fields[key] = None
            # Only clear session on full todo reset (keep session history for reviewed/done)
            if new_status in _RESET_STATUSES:
                if "session_id" not in fields:
                    fields["session_id"] = None

            # Clear results only when explicitly moving back to todo. Run history
            # lives on the session (message_ids) and is not reset here.
            if new_status in _RESET_STATUSES:
                try:
                    delete_task_result_file(task_id)
                except Exception:
                    pass

    # Load current task to detect changes for activity log
    current = store.get(task_id)
    updated = store.update(task_id, **fields)
    if updated and current:
        if "status" in fields and str(fields["status"]) != str(current.status):
            append_task_activity_log(
                task_id,
                "status_change",
                f"Status: {current.status} → {fields['status']}",
                **{"from": str(current.status), "to": str(fields["status"])},
            )
        new_agent = fields.get("assigned_agent_type")
        old_agent = current.assigned_agent_type
        if new_agent and new_agent != old_agent:
            append_task_activity_log(task_id, "agent_assigned", f"Agent assigned: {new_agent}", agent=new_agent)
        elif old_agent and new_agent is None and "assigned_agent_type" in fields:
            append_task_activity_log(task_id, "agent_cleared", "Agent assignment cleared")
    if updated:
        notify_change("tasks", task_id=str(task_id))
        # Cascade blocked-state to/from descendants on a status transition.
        if current and "status" in fields:
            new_status = fields["status"]  # normalized to TaskStatus above
            if new_status != current.status:
                if new_status == TaskStatus.blocked:
                    _cascade_block_descendants(task_id, updated.title, store=store)
                elif current.status == TaskStatus.blocked:
                    _cascade_unblock_descendants(task_id, store=store)
                # Subtasks share the parent's waiting status (todo/ready),
                # except for dependency blocks between siblings.
                if new_status in (TaskStatus.todo, TaskStatus.ready):
                    _cascade_status_to_subtasks(task_id, new_status, store=store)
                # A task that just completed may release tasks depending on it,
                # may leave sibling subtasks runnable, and may have been the
                # last open subtask of its parent.
                if new_status in DEP_SATISFIED_STATUSES:
                    _release_dependents(task_id, store=store)
                    done_task = store.get(task_id)
                    if done_task and done_task.parent_id:
                        parent = store.get(done_task.parent_id)
                        if parent and parent.status == TaskStatus.in_progress:
                            promote_runnable_subtasks(parent.id, store=store)
                    _maybe_complete_parent(task_id, store=store)
        # A changed dependency list must be reflected in this task's blocked state.
        if "depends" in fields:
            _reevaluate_dependency_block(task_id, store=store)
    return updated


def _collect_descendants(root_id: UUID, tasks: List[Task]) -> List[Task]:
    """Return every task whose parent chain leads back to ``root_id`` (all levels)."""
    by_parent: Dict[str, List[Task]] = {}
    for t in tasks:
        if t.parent_id is not None:
            by_parent.setdefault(str(t.parent_id), []).append(t)
    out: List[Task] = []
    seen = {str(root_id)}
    stack = [str(root_id)]
    while stack:
        pid = stack.pop()
        for child in by_parent.get(pid, []):
            cid = str(child.id)
            if cid not in seen:
                seen.add(cid)
                out.append(child)
                stack.append(cid)
    return out


def _cascade_block_descendants(parent_id: UUID, parent_title: str, *, store: TaskStore) -> int:
    """Block every not-yet-started descendant of a task that just became blocked."""
    reason = f"{CASCADE_BLOCK_PREFIX} '{parent_title}' ({parent_id})"
    count = 0
    for child in _collect_descendants(parent_id, store.list()):
        if child.status in _CASCADE_BLOCKABLE:
            store.update(child.id, status=TaskStatus.blocked, blocked_reason=reason)
            append_task_activity_log(
                child.id, "status_change",
                f"Status: {child.status} → blocked (parent blocked)",
                **{"from": str(child.status), "to": "blocked"},
            )
            notify_change("tasks", task_id=str(child.id))
            count += 1
    return count


def _cascade_unblock_descendants(parent_id: UUID, *, store: TaskStore) -> int:
    """Restore descendants that were auto-blocked by this ancestor back to todo.

    Only touches tasks whose blocked_reason carries the cascade marker, so a
    subtask the user blocked for its own reason stays blocked.
    """
    count = 0
    for child in _collect_descendants(parent_id, store.list()):
        if child.status == TaskStatus.blocked and (child.blocked_reason or "").startswith(CASCADE_BLOCK_PREFIX):
            store.update(child.id, status=TaskStatus.todo, blocked_reason=None)
            append_task_activity_log(
                child.id, "status_change",
                "Status: blocked → todo (parent unblocked)",
                **{"from": "blocked", "to": "todo"},
            )
            notify_change("tasks", task_id=str(child.id))
            count += 1
    return count


# -------------------- Dependencies --------------------

def find_task_by_key(key: str, *, store: TaskStore = default_store) -> Optional[Task]:
    """Return the task with the given Jira-style key (e.g. 'DEMO-12'), or None."""
    wanted = (key or "").strip().upper()
    if not wanted:
        return None
    for t in store.list():
        if (t.key or "").upper() == wanted:
            return t
    return None


def _task_ref(t: Task) -> str:
    """Human-readable reference for a task: its key when present, else short id."""
    return t.key or str(t.id)[:8]


def _dependency_block_reason(pending: List[Task]) -> str:
    refs = ", ".join(_task_ref(t) for t in pending)
    return f"{DEPENDENCY_BLOCK_PREFIX}: {refs}"


def _pending_dependencies(task: Task, index: Dict[UUID, Task]) -> List[Task]:
    """Return the dependency tasks of `task` that are not completed yet.

    Dependency ids that no longer resolve to a task are ignored, so a deleted
    dependency can never leave a task blocked forever.
    """
    out: List[Task] = []
    for dep_id in task.depends or []:
        dep = index.get(dep_id)
        if dep is not None and dep.status not in DEP_SATISFIED_STATUSES:
            out.append(dep)
    return out


def _release_from_dep_block(task: Task, index: Dict[UUID, Task], *, store: TaskStore, detail: str) -> None:
    """Unblock a task whose dependencies are all done.

    The task returns to todo. Standalone tasks then wait for the user; subtasks
    of an active container are picked up by the container's one-at-a-time
    dispatcher (promote_runnable_subtasks) when their turn comes.
    """
    store.update(task.id, status=TaskStatus.todo, blocked_reason=None)
    append_task_activity_log(
        task.id, "dependency_release",
        f"{detail} — task released to todo",
        **{"from": "blocked", "to": "todo"},
    )
    notify_change("tasks", task_id=str(task.id))


def _would_create_cycle(task_id: UUID, dep_ids: Sequence[UUID], tasks: List[Task]) -> bool:
    """True if setting `dep_ids` as task's dependencies creates a cycle."""
    depends_of: Dict[UUID, List[UUID]] = {t.id: list(t.depends or []) for t in tasks}
    depends_of[task_id] = list(dep_ids)
    seen: set = set()
    stack = list(dep_ids)
    while stack:
        current = stack.pop()
        if current == task_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(depends_of.get(current, []))
    return False


def _reevaluate_dependency_block(task_id: UUID, *, store: TaskStore) -> None:
    """Sync a task's blocked state with its current dependency list.

    Blocks a waiting task whose dependencies are unfinished, refreshes the
    block reason when the pending set changed, and releases a dependency-blocked
    task back to `todo` once nothing is pending anymore.
    """
    tasks = store.list()
    index = {t.id: t for t in tasks}
    task = index.get(task_id)
    if task is None:
        return
    pending = _pending_dependencies(task, index)
    is_dep_blocked = task.status == TaskStatus.blocked and (task.blocked_reason or "").startswith(DEPENDENCY_BLOCK_PREFIX)
    if pending:
        reason = _dependency_block_reason(pending)
        if task.status in _DEP_BLOCKABLE:
            store.update(task_id, status=TaskStatus.blocked, blocked_reason=reason)
            append_task_activity_log(
                task_id, "dependency_block", reason,
                **{"from": str(task.status), "to": "blocked"},
            )
            notify_change("tasks", task_id=str(task_id))
        elif is_dep_blocked and task.blocked_reason != reason:
            store.update(task_id, blocked_reason=reason)
            notify_change("tasks", task_id=str(task_id))
    elif is_dep_blocked:
        _release_from_dep_block(task, index, store=store, detail="All dependencies completed")


def _release_dependents(completed_id: UUID, *, store: TaskStore) -> int:
    """Re-check every task that depends on a just-completed task.

    Dependency-blocked tasks whose dependencies are now all done are released
    (to ready inside an active container, otherwise to todo); tasks that still
    have pending dependencies get their block reason refreshed. Returns the
    number of tasks released.
    """
    tasks = store.list()
    index = {t.id: t for t in tasks}
    released = 0
    for t in tasks:
        if completed_id not in (t.depends or []):
            continue
        if t.status != TaskStatus.blocked or not (t.blocked_reason or "").startswith(DEPENDENCY_BLOCK_PREFIX):
            continue
        pending = _pending_dependencies(t, index)
        if pending:
            reason = _dependency_block_reason(pending)
            if reason != t.blocked_reason:
                store.update(t.id, blocked_reason=reason)
                notify_change("tasks", task_id=str(t.id))
            continue
        ref = _task_ref(index[completed_id]) if completed_id in index else str(completed_id)
        _release_from_dep_block(t, index, store=store, detail=f"Dependency {ref} completed")
        released += 1
        # A released subtask may belong to a different active container than the
        # completed task — nudge that container's dispatcher too.
        if t.parent_id is not None:
            parent = index.get(t.parent_id)
            if parent is not None and parent.status == TaskStatus.in_progress:
                promote_runnable_subtasks(parent.id, store=store)
    return released


def set_dependencies(task_id: UUID, depends: Sequence[UUID], *, store: TaskStore = default_store) -> Optional[Task]:
    """Replace a task's dependency list (validated) and sync its blocked state."""
    return update_task(task_id, store=store, depends=list(depends))


# -------------------- Parent containers (tasks with subtasks) --------------------
#
# A task that has subtasks is a container: it is never routed to a worker agent
# itself. Activating the container moves the parent to in_progress and promotes
# its runnable subtasks to ready, where the orchestrator processes them like
# normal tasks (each with its own review cycle). Subtasks blocked on sibling
# dependencies are released as those siblings reach done, and when the last
# subtask is done the parent is completed automatically.

def get_subtasks(parent_id: UUID, *, store: TaskStore = default_store) -> List[Task]:
    """Return the direct subtasks of a task."""
    return [t for t in store.list() if t.parent_id == parent_id]


def _execution_mode(workspace: Optional[str]) -> str:
    """Return the workspace's orchestrator execution mode ('node' or 'subprocess')."""
    try:
        from workspace import get_workspace_metadata
        return (
            get_workspace_metadata(str(workspace or "default"))
            .get("orchestrator", {})
            .get("execution_mode", "subprocess")
        )
    except Exception:
        return "subprocess"


def _max_parallel_subtasks(workspace: Optional[str]) -> int:
    """How many subtasks of one container may run at once (>= 1).

    Reads the workspace orchestrator setting ``max_parallel_subtasks``; defaults
    to 1, which preserves the strict one-at-a-time behaviour. Values above 1 let
    dependency-free sibling subtasks run concurrently (ordering is still honoured
    through each subtask's ``depends`` list).
    """
    try:
        from workspace import get_workspace_metadata
        raw = (
            get_workspace_metadata(str(workspace or "default"))
            .get("orchestrator", {})
            .get("max_parallel_subtasks", 1)
        )
        return max(1, int(raw))
    except Exception:
        return 1


def _start_orchestrator_on_subtask(task: Task, *, store: TaskStore) -> bool:
    """Subprocess mode: hand a runnable subtask straight to the orchestrator.

    Atomically claims the subtask first (so two concurrent promoters — e.g. two
    sibling subprocesses finalizing at once — cannot both dispatch it), then
    launches an orchestrator run on it, which routes it to a worker like any
    tracked task. There is no polling loop in subprocess mode to pick ready tasks
    up, so the run is started directly. Returns True only if this caller claimed
    and launched the subtask.
    """
    claimed = store.claim(
        task.id,
        waiting_statuses=("todo", "ready"),
        assigned_agent_type="orchestrator",
        status=TaskStatus.in_progress,
    )
    if claimed is None:
        return False  # another promoter won the claim
    try:
        from agents import agent_launcher
        run_id, _session_id = agent_launcher.start_run(str(task.id), "orchestrator", None)
        assign_agent(task.id, "orchestrator", None, run_id=run_id, store=store)
        append_task_activity_log(
            task.id, "agent_assigned",
            "Orchestrator started directly on container subtask",
            agent="orchestrator",
        )
        notify_change("tasks", task_id=str(task.id))
        return True
    except Exception:
        # Launch failed after we claimed it — release the claim so it can be
        # retried on the next promotion pass instead of stranding the subtask.
        clear_agent(task.id, store=store)
        store.update(task.id, status=TaskStatus.todo)
        return False


# Subtask states that count as "being worked on": while any sibling is in one
# of these, the container does not start the next subtask (strict one-at-a-time
# execution). `ready` counts only in node mode, where it means "queued for the
# orchestrator"; in subprocess mode a ready subtask is merely parked.
_SUBTASK_ACTIVE = {
    TaskStatus.pending,
    TaskStatus.in_progress,
    TaskStatus.awaiting_input,
    TaskStatus.reviewing,
    TaskStatus.resolved,
    TaskStatus.reviewed,
}


def promote_runnable_subtasks(parent_id: UUID, *, store: TaskStore = default_store) -> int:
    """Dispatch runnable subtasks of an active container, up to the concurrency cap.

    Subtasks default to strict one-at-a-time execution
    (``max_parallel_subtasks`` = 1): while any sibling is being worked on (or
    queued), nothing new is dispatched. When the workspace raises the cap, up to
    ``cap - (currently active siblings)`` waiting subtasks are dispatched at once.
    Only dependency-free candidates are ever eligible, so ordering expressed via
    ``depends`` is always honoured; genuinely independent subtasks run in
    parallel. Candidates are taken in explicit ``order`` then creation order.
    Node mode marks them ready for the polling loop; subprocess mode starts an
    orchestrator run on each directly. Each dispatch atomically claims its
    subtask, so concurrent promoters never double-dispatch. Returns the number
    of subtasks dispatched.
    """
    parent = store.get(parent_id)
    if parent is None:
        return 0
    mode = _execution_mode(parent.workspace)
    cap = _max_parallel_subtasks(parent.workspace)
    tasks = store.list()
    index = {t.id: t for t in tasks}
    children = [t for t in tasks if t.parent_id == parent_id]

    active_states = _SUBTASK_ACTIVE if mode != "node" else (_SUBTASK_ACTIVE | {TaskStatus.ready})
    active_count = sum(
        1 for c in children
        if c.status in active_states or (c.assigned_agent_type and c.status != TaskStatus.done)
    )
    slots = cap - active_count
    if slots <= 0:
        return 0

    waiting = TaskStatus.todo if mode == "node" else (TaskStatus.todo, TaskStatus.ready)
    candidates = [
        c for c in children
        if (c.status == waiting if isinstance(waiting, TaskStatus) else c.status in waiting)
        and not c.assigned_agent_type
        and not _pending_dependencies(c, index)
    ]
    if not candidates:
        return 0
    candidates.sort(key=lambda c: (
        c.order if c.order is not None else 1_000_000,
        c.created_at.isoformat() if c.created_at else "",
    ))

    dispatched = 0
    for nxt in candidates:
        if dispatched >= slots:
            break
        if mode == "node":
            # Claim atomically (todo → ready) so a second promoter can't grab the
            # same subtask; the polling loop then picks it up.
            claimed = store.claim(nxt.id, waiting_statuses=("todo",), status=TaskStatus.ready)
            if claimed is None:
                continue
            append_task_activity_log(
                nxt.id, "status_change",
                "Status: todo → ready (next subtask in container)",
                **{"from": "todo", "to": "ready"},
            )
            notify_change("tasks", task_id=str(nxt.id))
            dispatched += 1
        else:
            if _start_orchestrator_on_subtask(nxt, store=store):
                dispatched += 1
    return dispatched


def activate_parent_container(parent_id: UUID, *, store: TaskStore = default_store) -> int:
    """Start container processing for a parent task that has subtasks.

    Clears any pending agent assignment on the parent (the parent itself is not
    work), moves it to in_progress, and promotes its runnable subtasks to ready.
    Returns the number of subtasks promoted.
    """
    parent = store.get(parent_id)
    if parent is None:
        return 0
    if parent.assigned_agent_type:
        clear_agent(parent_id, store=store)
    if parent.status != TaskStatus.in_progress:
        store.update(parent_id, status=TaskStatus.in_progress, blocked_reason=None)
        append_task_activity_log(
            parent_id, "container_activated",
            "Task has subtasks — processing them individually; the parent completes when all subtasks are done",
            **{"from": str(parent.status), "to": "in_progress"},
        )
        notify_change("tasks", task_id=str(parent_id))
    return promote_runnable_subtasks(parent_id, store=store)


# Marker written into blocked_reason when a container is paused by the user.
# Distinguishes "paused mid-execution, resume later" from a deliberate abort:
# resume_container re-queues only subtasks carrying this marker.
PAUSE_MARKER = "[paused] Container execution paused by user"


def pause_container(parent_id: UUID, *, store: TaskStore = default_store) -> Dict[str, Any]:
    """Pause a container: stop the active subtask's run and freeze dispatch.

    Subtasks run one-at-a-time, so pausing means stopping whichever child has
    a live or queued run (worker, reviewer, or continuation orchestrator) and
    marking it with PAUSE_MARKER so resume can tell it apart from a subtask
    the user aborted on purpose. Finished/waiting siblings keep their state.
    The parent is marked stopped with the same marker; nothing dispatches
    until resume_container re-activates it.
    """
    parent = store.get(parent_id)
    if parent is None:
        raise ValueError(f"Task not found: {parent_id}")
    from managers import run_manager

    paused: List[str] = []
    for child in [t for t in store.list() if t.parent_id == parent_id]:
        run_id = str(getattr(child, "assigned_agent_run_id", "") or "")
        if not run_id:
            continue
        rec = run_manager.get_run_by_id(run_id)
        run_status = str((rec or {}).get("status") or "")
        if run_status in ("running", "stop"):
            # Kills the process, marks the task stopped, clears the agent and
            # drops the task's continuations (the normal stop path).
            run_manager.stop_run(str(child.id), run_id=run_id)
        elif run_status in ("pending", "assigned", "awaiting_approval"):
            # Never started — just close the record and release the task.
            run_manager.update_run(run_id, {
                "status": "stopped",
                "finished_at": run_manager.utc_now_iso(),
                "error": "container paused by user",
            })
            clear_agent(child.id, store=store)
        else:
            continue
        update_task(child.id, store=store, status=TaskStatus.stopped, blocked_reason=PAUSE_MARKER)
        append_task_activity_log(
            child.id, "paused",
            "Container paused — subtask interrupted; it re-queues on resume",
            run_id=run_id,
        )
        paused.append(str(child.id))

    update_task(parent_id, store=store, status=TaskStatus.stopped, blocked_reason=PAUSE_MARKER)
    append_task_activity_log(
        parent_id, "paused",
        f"Container paused by user ({len(paused)} active subtask(s) interrupted)",
    )
    notify_change("tasks", task_id=str(parent_id))
    return {"paused_subtasks": paused}


def resume_container(parent_id: UUID, *, store: TaskStore = default_store) -> Dict[str, Any]:
    """Resume a paused container: re-queue paused subtasks and restart dispatch.

    Only subtasks stopped with PAUSE_MARKER return to todo — a subtask the
    user stopped deliberately stays stopped. The reset writes the store
    directly (not update_task's todo reset) so the subtask keeps its previous
    results: the re-run receives its earlier partial output as context.
    Finally the parent is re-activated, which dispatches the next runnable
    subtask through the normal one-at-a-time pipeline.
    """
    parent = store.get(parent_id)
    if parent is None:
        raise ValueError(f"Task not found: {parent_id}")

    resumed: List[str] = []
    for child in [t for t in store.list() if t.parent_id == parent_id]:
        if child.status == TaskStatus.stopped and (child.blocked_reason or "").startswith(PAUSE_MARKER):
            store.update(child.id, status=TaskStatus.todo, blocked_reason=None)
            append_task_activity_log(
                child.id, "resumed",
                "Container resumed — subtask re-queued (previous results kept)",
                **{"from": "stopped", "to": "todo"},
            )
            notify_change("tasks", task_id=str(child.id))
            resumed.append(str(child.id))

    if parent.status == TaskStatus.stopped:
        store.update(parent_id, blocked_reason=None)
    append_task_activity_log(parent_id, "resumed", "Container resumed by user")
    promoted = activate_parent_container(parent_id, store=store)
    return {"resumed_subtasks": resumed, "subtasks_promoted": promoted}


def _cascade_status_to_subtasks(parent_id: UUID, new_status: TaskStatus, *, store: TaskStore) -> None:
    """Mirror a parent's move to todo/ready onto its subtasks.

    Subtasks share the parent's waiting status; only dependency blocks between
    siblings differ, so after the cascade each subtask's dependency state is
    re-applied (chain subtasks whose dependencies are no longer done get
    re-blocked). Subtasks with an agent run in flight are left untouched.
    """
    children = [t for t in store.list() if t.parent_id == parent_id]
    for child in children:
        if child.assigned_agent_run_id:
            continue
        if child.status != new_status:
            # Through update_task so nested trees cascade and todo resets
            # agent assignment/results the same way it does for the parent.
            update_task(child.id, store=store, status=new_status)
    for child in children:
        _reevaluate_dependency_block(child.id, store=store)


def _maybe_complete_parent(task_id: UUID, *, store: TaskStore) -> None:
    """Complete the parent when the last of its subtasks reaches done.

    Goes through update_task so the parent's own dependents are released and,
    for nested trees, its parent is checked in turn.
    """
    t = store.get(task_id)
    if t is None or t.parent_id is None:
        return
    tasks = store.list()
    siblings = [x for x in tasks if x.parent_id == t.parent_id]
    if not siblings or any(x.status != TaskStatus.done for x in siblings):
        return
    parent = next((x for x in tasks if x.id == t.parent_id), None)
    if parent is None or parent.status in (TaskStatus.done, TaskStatus.stopped):
        return
    append_task_activity_log(
        parent.id, "container_completed",
        f"All {len(siblings)} subtasks are done — completing parent task",
    )
    update_task(parent.id, store=store, status=TaskStatus.done)


def _prune_dependencies(deleted_ids: set, *, store: TaskStore) -> None:
    """Drop deleted task ids from every remaining `depends` list and re-check
    blocked dependents, so deleting a dependency never strands a task."""
    deleted = {UUID(str(d)) if not isinstance(d, UUID) else d for d in deleted_ids}
    affected: List[UUID] = []
    for t in store.list():
        deps = list(t.depends or [])
        if any(d in deleted for d in deps):
            store.update(t.id, depends=[d for d in deps if d not in deleted])
            affected.append(t.id)
    for tid in affected:
        _reevaluate_dependency_block(tid, store=store)


def delete_task(task_id: UUID, *, cascade: bool = False, store: TaskStore = default_store) -> int:
    """Delete a task by id. Returns the number of deleted tasks."""
    if cascade:
        # Collect all descendant IDs before deletion to clean up sidecars
        tasks = store.list()
        to_delete = {str(task_id)}
        stack = [str(task_id)]
        while stack:
            parent = stack.pop()
            for t in tasks:
                if t.parent_id and str(t.parent_id) == parent:
                    child_id = str(t.id)
                    if child_id not in to_delete:
                        to_delete.add(child_id)
                        stack.append(child_id)
        deleted = store.delete(task_id, cascade=True)
        for tid in to_delete:
            delete_task_activity_log(_uuid_from_str(tid))
            delete_task_result_file(_uuid_from_str(tid))
        if deleted:
            _prune_dependencies(to_delete, store=store)
            notify_change("tasks", task_id=str(task_id))
        return deleted
    deleted = store.delete(task_id, cascade=False)
    if deleted:
        delete_task_activity_log(task_id)
        delete_task_result_file(task_id)
        _prune_dependencies({task_id}, store=store)
        notify_change("tasks", task_id=str(task_id))
    return deleted


def _uuid_from_str(v) -> UUID:
    import os as _os
    from uuid import UUID as _UUID
    if isinstance(v, _UUID):
        return v
    s = str(v).strip()
    try:
        return _UUID(s)
    except ValueError:
        # Try to recover: strip all hyphens and reformat as 8-4-4-4-12
        hex_only = s.replace("-", "").replace(" ", "")
        if len(hex_only) == 32 and all(c in "0123456789abcdefABCDEF" for c in hex_only):
            return _UUID(f"{hex_only[:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:]}")
        # Fall back to the env-injected task ID (set by node_run for orchestrator runs).
        # Small models sometimes truncate UUIDs; this lets the tools recover silently.
        env_id = _os.environ.get("AGENT_TASK_ID", "").strip()
        if env_id:
            try:
                return _UUID(env_id)
            except ValueError:
                pass
        raise ValueError(
            f"Invalid task ID '{s}'. Use the exact Task ID from your context without modification."
        )


# -------------------- Utilities --------------------

def add_subtask(
    parent_id: UUID,
    title: str,
    description: str = "",
    *,
    depends: Optional[Sequence[UUID]] = None,
    store: TaskStore = default_store,
) -> Task:
    """Create a subtask under the given parent with created_by=orchestrator.

    The subtask inherits workspace, project, and project_id from its parent task.
    `depends` lets a decomposition express execution order between subtasks;
    a subtask with unfinished dependencies is created blocked and released
    automatically when they complete.
    """
    parent = store.get(parent_id)
    workspace = parent.workspace if parent else None
    project = parent.project if parent else None
    project_id = parent.project_id if parent else None

    # A subtask created under an already-blocked parent inherits the block, so a
    # decomposition run cannot spawn immediately-runnable work under a blocked task.
    status = TaskStatus.todo
    blocked_reason = None
    if parent and parent.status == TaskStatus.blocked:
        status = TaskStatus.blocked
        blocked_reason = f"{CASCADE_BLOCK_PREFIX} '{parent.title}' ({parent_id})"

    dep_ids = [d if isinstance(d, UUID) else UUID(str(d)) for d in (depends or [])]
    if dep_ids and status != TaskStatus.blocked:
        index = {t.id: t for t in store.list()}
        missing = [d for d in dep_ids if d not in index]
        if missing:
            raise ValueError(f"Dependency tasks not found: {', '.join(str(m) for m in missing)}")
        pending_deps = [index[d] for d in dep_ids if index[d].status not in DEP_SATISFIED_STATUSES]
        if pending_deps:
            status = TaskStatus.blocked
            blocked_reason = _dependency_block_reason(pending_deps)

    return store.create(
        title=title,
        description=description,
        created_by=CreatedBy.orchestrator,
        parent_id=parent_id,
        workspace=workspace,
        project=project,
        project_id=project_id,
        status=status,
        blocked_reason=blocked_reason,
        depends=dep_ids,
    )


def stop_task(task_id: UUID, *, store: TaskStore = default_store) -> Optional[Task]:
    """Mark the task as stopped. Returns updated task or None if not found."""
    return store.update(task_id, status=TaskStatus.stopped)


def block_task(task_id: UUID, reason: str, *, store: TaskStore = default_store) -> Optional[Task]:
    """Mark the task as blocked with a given reason. Returns updated task or None.

    Goes through update_task so blocking a parent cascades to its descendants.
    """
    return update_task(task_id, store=store, status=TaskStatus.blocked, blocked_reason=reason)


def create_sequence(
    task_ids: Sequence[UUID],
    *,
    store: TaskStore = default_store,
    sequence_id: Optional[str] = None,
    start_order: int = 1,
) -> str:
    """Assign a common sequence_id and increasing order to the provided task IDs.

    - If sequence_id is not provided, a new UUID string is generated.
    - Order starts from `start_order` (default 1) and increases by 1 following the
      order of task_ids.
    - Raises ValueError if any task is not found.

    Returns the sequence_id used.
    """
    seq_id = sequence_id or str(uuid4())

    # Load all tasks, verify existence and update in-memory; then save once.
    tasks = store.load()
    index = {t.id: t for t in tasks}

    missing = [tid for tid in task_ids if tid not in index]
    if missing:
        raise ValueError(f"Tasks not found for sequence: {', '.join(str(x) for x in missing)}")

    for order_value, tid in enumerate(task_ids, start=start_order):
        t = index[tid]
        # mutate fields
        t.sequence_id = seq_id
        t.order = int(order_value)
        t.touch()

    # Persist all tasks in one save call
    store.save(tasks)
    return seq_id


# -------------------- Activity log --------------------

def _tasks_path():
    from pathlib import Path
    return Path(TASKS_FILE)


def get_task_activity_log(task_id: UUID) -> list:
    """Return activity log entries for a task."""
    return _get_activity_log(_tasks_path(), str(task_id))


def append_task_activity_log(task_id: UUID, entry_type: str, message: str, **extra) -> None:
    """Append an activity log entry for a task."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        "message": message,
        **extra,
    }
    _append_activity_log(_tasks_path(), str(task_id), entry)


def delete_task_activity_log(task_id: UUID) -> None:
    """Delete the activity log for a task."""
    _delete_activity_log(_tasks_path(), str(task_id))


# -------------------- Routing log --------------------

def get_routing_log(workspace: Optional[str] = None) -> list:
    """Return all orchestrator routing decisions, newest-first."""
    return _get_routing_log(workspace=workspace)


def append_routing_log_entry(
    task_id: UUID,
    task_title: str,
    agent_id: str,
    reason: Optional[str],
    workspace: Optional[str],
) -> None:
    """Record a single orchestrator routing decision."""
    from uuid import uuid4
    entry = {
        "id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": str(task_id),
        "task_title": task_title,
        "agent_id": agent_id,
        "reason": reason or "",
        "workspace": workspace or "",
    }
    _append_routing_log(entry)


# -------------------- Task runs --------------------

def get_task_runs(task_id: UUID) -> list:
    """Return the agent runs for a task, derived from its session context.

    Runs are linked to a session via ``session.message_ids``; the task points at
    that session via ``task.session_id`` (one-session-per-task invariant). This
    is the canonical source for "all runs of a task" and stays correct even
    after ``assigned_agent_run_id`` is cleared when a run completes.

    Falls back to runs filtered by ``run.task_id`` if the task has no session
    context yet (e.g. a run recorded before its session link was established).
    """
    from managers import run_manager
    from common.session_service import get_context_by_id, load_contexts

    t = get_task(task_id)
    if not t:
        return []

    ctx = None
    if getattr(t, "session_id", None):
        ctx = get_context_by_id(str(t.session_id))
    if ctx is None:
        # No session_id on the task (or stale) — locate the session by task_id.
        for c in load_contexts():
            if c.get("task_id") == str(task_id):
                ctx = c
                break

    all_runs = run_manager.load_runs()
    runs_by_id = {r.get("run_id"): r for r in all_runs}

    if ctx:
        message_ids = ctx.get("message_ids") or []
        runs = [runs_by_id[rid] for rid in message_ids if rid in runs_by_id]
        if runs:
            return runs

    # Fallback: runs that back-reference this task directly.
    return [r for r in all_runs if r.get("task_id") == str(task_id)]


# -------------------- Result --------------------

def get_task_results(task_id: UUID) -> list:
    """Return all result entries for a task (one per agent run)."""
    return _get_task_results(_tasks_path(), str(task_id))


def get_task_result(task_id: UUID) -> Optional[str]:
    """Return the latest result text for a task (backward-compat)."""
    return _get_task_result(_tasks_path(), str(task_id))


def get_task_result_files(task_id: UUID) -> list:
    """Return the files from the latest result entry."""
    return _get_task_result_files(_tasks_path(), str(task_id))


def set_task_result(
    task_id: UUID,
    result: str,
    files: Optional[list] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    """Upsert a result entry for a task, keyed by run_id."""
    _set_task_result(_tasks_path(), str(task_id), result, files=files, run_id=run_id, agent_id=agent_id)
    notify_change("tasks", task_id=str(task_id))


def delete_task_result_file(task_id: UUID) -> None:
    """Delete the result file for a task."""
    _delete_task_result(_tasks_path(), str(task_id))


# -------------------- Agent management --------------------

def assign_agent(
    task_id: UUID,
    agent_type: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    store: TaskStore = default_store,
    run_id: Optional[str] = None,
) -> Optional[Task]:
    """Assign an agent to the task.

    - agent_type: string identifier of the agent
    - params: arbitrary dict with agent configuration
    - run_id: optional external run identifier
    """
    fields: Dict[str, Any] = {
        "assigned_agent_type": agent_type,
        "assigned_agent_params": params if params is not None else None,
        "assigned_agent_run_id": run_id,
    }
    # Capture the current status so rejection can restore it
    current = store.get(task_id)
    if current and current.pre_assignment_status is None:
        fields["pre_assignment_status"] = current.status
    updated = store.update(task_id, **fields)
    if updated:
        notify_change("tasks", task_id=str(task_id))
    return updated


def clear_agent(
    task_id: UUID,
    *,
    store: TaskStore = default_store,
) -> Optional[Task]:
    """Clear agent assignment."""
    fields: Dict[str, Any] = {
        "assigned_agent_type": None,
        "assigned_agent_params": None,
        "assigned_agent_run_id": None,
        "pre_assignment_status": None,
    }
    return store.update(task_id, **fields)


__all__ = [
    "Task",
    "TaskStatus",
    "CreatedBy",
    "TaskStore",
    "create_task",
    "get_task",
    "list_tasks",
    "update_task",
    "delete_task",
    "add_subtask",
    "stop_task",
    "block_task",
    "set_dependencies",
    "find_task_by_key",
    "get_subtasks",
    "promote_runnable_subtasks",
    "activate_parent_container",
    "pause_container",
    "resume_container",
    "PAUSE_MARKER",
    "create_sequence",
    "assign_agent",
    "clear_agent",
    "get_task_activity_log",
    "append_task_activity_log",
    "delete_task_activity_log",
    "get_routing_log",
    "append_routing_log_entry",
    "get_task_runs",
    "get_task_results",
    "get_task_result",
    "get_task_result_files",
    "set_task_result",
    "delete_task_result_file",
]
