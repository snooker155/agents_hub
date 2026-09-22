"""
Task context helpers shared by the agent entry points.

Two concerns live here, both previously inlined in ``agent_run.py`` and
imported back out of it by ``runtime.node_run`` and ``runtime.flow_run``:

- :func:`collect_changed_files` — list workspace/project files a run touched.
- :func:`build_task_instruction` — enrich a base instruction with parent-task,
  dependency-result, and previous-run context pulled from the task store.
- :func:`task_context_header` — the canonical "Task ID: / Task:" prompt scaffold.
- :func:`persist_task_result` — save a run's output + changed files as the task
  result (shared by single-agent and flow runs).
"""
from __future__ import annotations

from typing import Optional

from common.paths import PROJECTS_FILE

# Internal bookkeeping files that should never appear in a task's Files tab.
_INTERNAL_PREFIXES = (".logs/", ".progress.json", ".task_result")

# Routing agents produce assignment summaries, not work output — skip their
# results when looking back at "previous agent output on this task".
_ROUTING_AGENTS = {"orchestrator", "decomposer"}


def augment_params_with_block_reason(task: object, params: Optional[dict]) -> Optional[dict]:
    """Fold a blocked task's reason into the worker params' ``description``.

    Re-assigning an agent is how a blocked task gets fixed, but starting the run
    flips the task to in_progress and clears ``blocked_reason`` — so the reason
    must be baked into the worker's input at assign time, while it is still set.
    Auto-cascade parent-blocks carry no actionable feedback and are skipped.
    Returns the params unchanged when there is nothing to inject, otherwise a new
    dict with the reason prepended to ``description``.
    """
    from tasks.service import CASCADE_BLOCK_PREFIX

    reason = str(getattr(task, "blocked_reason", "") or "").strip()
    if not reason or reason.startswith(CASCADE_BLOCK_PREFIX):
        return params
    out = dict(params or {})
    note = (
        "The previous attempt on this task was BLOCKED. Address the following "
        "feedback in full before finishing:\n" + reason
    )
    existing = str(out.get("description") or "").strip()
    out["description"] = f"{note}\n\n{existing}".strip() if existing else note
    return out


def collect_changed_files(task_id: str, started_at_iso: Optional[str]) -> list:
    """Return project files modified at or after the run's started_at time.

    Scans the project subfolder (.agents_hub/workspaces/{ws}/{project}/) when a project is
    set on the task, so only project-level files appear in the task Files tab.
    Falls back to the workspace root when no project is set."""
    if not started_at_iso:
        return []
    try:
        from datetime import datetime, timezone
        from tasks import service as _ts
        from uuid import UUID as _UUID
        from workspace import resolve_project_root, project_folder_name
        task = _ts.get_task(_UUID(str(task_id)))
        ws_name = (getattr(task, "workspace", None) or "").strip() if task else ""
        if not ws_name:
            return []
        project_name = (getattr(task, "project", None) or "").strip() if task else ""
        if not project_name:
            pid = getattr(task, "project_id", None) if task else None
            if pid:
                try:
                    from projects.storage import ProjectStore as _PS
                    _pstore = _PS(PROJECTS_FILE)
                    _proj = _pstore.get(str(pid))
                    if _proj:
                        project_name = project_folder_name(_proj.name)
                except Exception:
                    pass
        root = resolve_project_root(ws_name, project_name or None)
        started_at = datetime.fromisoformat(started_at_iso)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        files = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except Exception:
                continue
            if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in _INTERNAL_PREFIXES):
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                if mtime >= started_at:
                    files.append(rel)
            except Exception:
                pass
        files.sort()
        return files
    except Exception:
        return []


def task_context_header(task_id: str, title: str = "") -> str:
    """The canonical prompt scaffold that prefixes a task's instruction.

    ``"Task ID: <id>\\nTask: <title>\\n\\n"`` when a title is given, otherwise
    ``"Task ID: <id>\\n\\n"``. Shared by build_task_instruction (single-agent
    task runs) and the flow executor so both label prompts identically.
    """
    title = (title or "").strip()
    if title:
        return f"Task ID: {task_id}\nTask: {title}\n\n"
    return f"Task ID: {task_id}\n\n"


def _dep_result_excerpt(text: str, limit: int = 1500) -> str:
    """Bounded excerpt of a prerequisite task's result.

    Dependency results are input context, not the work itself — a bounded
    excerpt keeps a long chain of dependencies from eating the worker's
    context budget before it reads a single file.
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… (result truncated, {len(text) - limit} more chars)"


def build_task_instruction(task_id: str, base_instruction: str) -> str:
    """Enrich ``base_instruction`` with context drawn from the task store.

    Layers, in order: a task title prefix + the task description, then the
    ``base_instruction`` itself, then the parent task's goal (for subtasks),
    result excerpts from the tasks this task ``depends`` on, and the most
    recent prior agent output on this same task (re-run scenario). The task
    description and base instruction are both included when both are present.
    Each layer is best-effort — a failure to load any piece leaves the
    instruction as-is. Returns the final instruction (never empty for a task_id).
    """
    instruction = base_instruction or ""
    try:
        from uuid import UUID
        from tasks import service as tasks_service
        task = tasks_service.get_task(UUID(task_id))
        if not task:
            return instruction or ""

        # Include the title only when it isn't already present in the instruction.
        _title = task.title if (task.title and task.title not in instruction) else ""
        title_prefix = task_context_header(task_id, _title)
        # Layer title prefix → task description → base instruction. The task
        # description is the standing context and the base instruction is the
        # specific ask for this run; include both when both are present (skip the
        # description if it's already contained in the instruction to avoid
        # duplication on re-runs).
        desc = (task.description or "").strip()
        if desc and desc in instruction:
            desc = ""
        instruction = (
            "\n\n".join(p for p in (f"{title_prefix}{desc}".strip(), instruction.strip()) if p)
            or f"Process task {task_id}"
        )

        # One line naming the deadline, when set, so the agent knows there is one.
        if getattr(task, "due_at", None):
            instruction = f"Deadline: {task.due_at.isoformat()}\n{instruction}"

        # If this is a subtask, prepend the parent task's title+description so the
        # agent understands the big-picture goal.
        try:
            if task.parent_id:
                parent = tasks_service.get_task(task.parent_id)
                if parent:
                    parent_ctx = f"Task: {parent.title}"
                    if parent.description:
                        parent_ctx += f"\n{parent.description}"
                    instruction = (
                        f"{'=' * 60}\n"
                        f"PARENT TASK (overall goal)\n"
                        f"{'=' * 60}\n"
                        f"{parent_ctx}\n"
                        f"{'=' * 60}\n\n"
                        f"{instruction}"
                    )
        except Exception:
            pass

        # Results of prerequisite tasks. `depends` is the explicit signal that
        # this task consumes another task's output — only those results are
        # injected. A shared sequence_id/order is a dispatch hint (which
        # sibling runs first), NOT a dependency: sibling subtasks are
        # independent units of work, so their results are not injected and the
        # task is never framed as a "next step" of them. (The old sequence
        # framing made workers "continue" unrelated siblings' work and grew
        # every prompt with all prior siblings' full outputs.)
        try:
            dep_results = []
            for dep_id in (task.depends or []):
                dep = tasks_service.get_task(dep_id)
                if not dep:
                    continue
                dep_result = tasks_service.get_task_result(dep.id)
                if dep_result and dep_result.strip():
                    dep_results.append(
                        f"[{dep.key or str(dep.id)[:8]}: {dep.title}]\n"
                        f"{_dep_result_excerpt(dep_result.strip())}"
                    )
            if dep_results:
                instruction = (
                    f"\n"
                    f"{instruction}\n\n"
                    f"{'=' * 60}\n"
                    f"RESULTS FROM PREREQUISITE TASKS (this task depends on them)\n"
                    f"{'=' * 60}\n"
                    + "\n\n".join(dep_results)
                    + f"\n{'=' * 60}\n"
                    f"\nUse these results as input where relevant. Your task is a "
                    f"separate unit of work, not a continuation of them."
                )
        except Exception:
            pass

        # Append previous agent output on this same task (re-run scenario).
        # Skip orchestrator results — they are assignment summaries, not work output.
        try:
            all_results = tasks_service.get_task_results(UUID(task_id))
            prev_entry = next(
                (e for e in reversed(all_results) if e.get("agent_id") not in _ROUTING_AGENTS),
                None,
            )
            prev_result = (prev_entry or {}).get("result", "") or ""
            if prev_result.strip():
                instruction = (
                    f"\n"
                    f"{instruction}\n\n"
                    f"{'=' * 60}\n"
                    f"OUTPUT FROM PREVIOUS AGENT RUN ON THIS TASK\n"
                    f"{'=' * 60}\n"
                    f"{prev_result.strip()}\n"
                    f"{'=' * 60}\n"
                    f"\nContinue the work based on the above output."
                )
        except Exception:
            pass
    except Exception:
        pass

    return instruction or f"Process task {task_id}"


_MAX_RESULT_CHARS = 100_000


def persist_task_result(
    task_id: str,
    run_id: str,
    output: str,
    agent_id: Optional[str] = None,
) -> None:
    """Save an agent run's output as the task result, with its changed files.

    Looks up the run's ``started_at`` to diff the workspace (collect_changed_files),
    truncates very large output, and writes the result via the task service. Only
    writes when there is output or at least one changed file. Best-effort: any
    failure is swallowed. Shared by single-agent runs (run_agent) and the flow
    executor, which differ only in *when* they call it relative to closing the run.
    """
    try:
        from uuid import UUID
        from tasks import service as tasks_service
        from managers.run_manager import get_run_by_id

        run_rec = get_run_by_id(run_id)
        started_at = (run_rec or {}).get("started_at")
        changed_files = collect_changed_files(task_id, started_at)

        text = (output or "").strip()
        if len(text) > _MAX_RESULT_CHARS:
            text = text[:_MAX_RESULT_CHARS] + "\n...[truncated]"

        if text or changed_files:
            tasks_service.set_task_result(
                UUID(task_id), text,
                files=changed_files, run_id=run_id, agent_id=agent_id or None,
            )
    except Exception:
        pass
