"""The decomposer behind a task's Decompose button and the create form's
"decompose" box.

Two passes, one after the other, in a backend thread:

1. split: the decomposer breaks the task into subtasks (``add_subtask`` with
   the task as parent);
2. review: it is shown the task with the tree pass 1 left and goes over it once
   more, splitting a subtask that is still too big for one agent run
   (``add_subtask`` under that subtask) and adding what is missing under the
   right parent. Skipped when pass 1 created nothing.

Each pass is a run of its own, recorded with the task id so both show on the
task's Execution tab. Given a channel, a pass streams its text, thinking and
tool calls there for a ``LiveRunStream`` (``ChannelPublishCallback``, as a
replay does). Where the whole decomposition stands (which pass, done or failed)
is kept per task in this process, read by ``GET /api/tasks/<id>/decompose``
and published on the same channel as a ``decompose_status`` event.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.agent_factory import create_agent
from tasks import service as tasks_service

log = logging.getLogger(__name__)

PASSES = ("split", "review")

# The description of each task in the review prompt is cut to this many
# characters: the decomposer needs to recognise the task, not reread it.
_REVIEW_DESCRIPTION_CHARS = 300

_state: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class DecomposeBusy(RuntimeError):
    """A decomposition of this task is already running."""


# What the agents who may take the subtasks can do, by kind of tool. The
# decomposer runs before anyone is assigned, so it is shown the workspace's
# agents; without this it planned "check the page in a browser" for agents
# that have no browser, and the subtask could never be done.
_CAPABILITIES = (
    ("change files", {"write_file", "create_file", "apply_unified_diff", "edit_file"}),
    ("run commands or code", {"run_shell", "run_code"}),
    ("read web pages", {"fetch_url", "web_search", "search_web"}),
    ("use a web browser", None),   # any tool with "browser" in its name
)
# Agents that route, split, review or build other things: not who does a subtask.
_NOT_WORKERS = frozenset({
    "orchestrator", "decomposer", "code_reviewer", "outcome_grader", "planner", "task_manager",
    "memory_extractor", "agent_creator", "flow_creator", "loop_creator", "team_creator",
    "prompt-engineer",
})
#: The heading under which the decomposer lists checks only a person can make.
PERSON_CHECKS_HEADING = "For a person to check"


def _workspace_agents(workspace: Optional[str]) -> List[Dict[str, Any]]:
    """The agents a subtask of this workspace can go to, as the Agents page lists them."""
    import asyncio
    from routes.agents.listing import list_agents
    agents = asyncio.run(list_agents(workspace=workspace or None))
    return [a for a in agents if isinstance(a, dict) and a.get("id") not in _NOT_WORKERS]


def capabilities_section(workspace: Optional[str]) -> str:
    """Who can do the work here and with what, for the decomposer's prompt;
    "" when the agents cannot be read."""
    try:
        agents = _workspace_agents(workspace)
    except Exception:  # noqa: BLE001 - the prompt stands without the section
        log.debug("decompose: listing the workspace's agents failed", exc_info=True)
        return ""
    if not agents:
        return ""
    lines = []
    missing = []
    for label, names in _CAPABILITIES:
        who = [a["id"] for a in agents if any(
            ("browser" in t) if names is None else (t in names) for t in (a.get("tools") or []))]
        if who:
            lines.append(f"- {label}: {', '.join(who)}")
        else:
            missing.append(label)
    if missing:
        lines.append(f"- no agent here can: {', '.join(missing)}")
    return (
        "WHO WILL DO THE WORK (the agents of this workspace and what they can do):\n"
        + "\n".join(lines) + "\n\n"
        "Make only subtasks one of these agents can finish with what it has. A check an "
        "agent can make by reading the files (the markup is complete, the prices are there) "
        "belongs in the subtask that makes the change, not in a subtask of its own. A check "
        "that needs what no agent here has (looking at the page in a browser, clicking "
        "through it) is not a subtask: list it at the end of your summary under the heading "
        f"\"{PERSON_CHECKS_HEADING}:\", one line each.\n\n"
    )


_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")


def person_checks(summary: str) -> List[str]:
    """The list items the decomposer put under the person-checks heading."""
    out: List[str] = []
    inside = False
    for line in (summary or "").splitlines():
        if not inside:
            bare = line.strip().strip("#*_ ")
            if bare.lower().startswith(PERSON_CHECKS_HEADING.lower()):
                inside = True
            continue
        if not line.strip():
            if out:
                break
            continue
        m = _LIST_ITEM.match(line)
        if not m:
            break
        out.append(m.group(1).strip("*_ "))
    return out


def status(task_id) -> Optional[dict]:
    """Where the latest decomposition of ``task_id`` stands, None if none ran
    since the backend started."""
    with _lock:
        state = _state.get(str(task_id))
        return dict(state) if state else None


def _set(task_id, channel: Optional[str], **fields) -> dict:
    with _lock:
        state = _state.setdefault(str(task_id), {})
        state.update(fields)
        snapshot = dict(state)
    if channel:
        try:
            from common.session_broker import publish_event
            publish_event(channel, {"type": "decompose_status", **snapshot})
        except Exception:  # noqa: BLE001 - the status endpoint still has it
            log.debug("decompose status publish failed", exc_info=True)
    return snapshot


def subtree(task_id) -> List[dict]:
    """The tasks under ``task_id`` depth first, each with its ``depth`` (1 for a
    direct subtask) — what the review pass is shown and the create form lists."""
    children: Dict[str, list] = {}
    for t in tasks_service.list_tasks():
        if t.parent_id:
            children.setdefault(str(t.parent_id), []).append(t)
    out: List[dict] = []

    def walk(parent: str, depth: int) -> None:
        for t in sorted(children.get(parent, []), key=lambda x: x.created_at):
            out.append({"id": str(t.id), "key": t.key, "title": t.title,
                        "description": t.description or "", "status": t.status.value,
                        "parent_id": parent, "depth": depth})
            walk(str(t.id), depth + 1)

    walk(str(task_id), 1)
    return out


def split_prompt(task) -> str:
    return (
        f"Task ID: {task.id}\nTask: {task.title}\n\n"
        + capabilities_section(getattr(task, "workspace", None)) +
        "Decompose this high-level task into concrete, small, and verifiable subtasks. "
        f"Create subtasks ONLY using the add_subtask tool with parent_id={task.id}. "
        "Do not create other high-level tasks. When a subtask must wait for others, "
        "pass their IDs in the depends parameter of add_subtask so execution order is enforced.\n\n"
        f"DESCRIPTION:\n{task.description or ''}\n\n"
        "Provide a brief summary of the created subtasks at the end."
    )


def review_prompt(task, tree: List[dict]) -> str:
    lines = []
    for node in tree:
        desc = " ".join(node["description"].split())
        if len(desc) > _REVIEW_DESCRIPTION_CHARS:
            desc = desc[:_REVIEW_DESCRIPTION_CHARS] + "…"
        lines.append(f"{'  ' * (node['depth'] - 1)}- {node['key'] or ''} (ID {node['id']}) "
                     f"{node['title']}" + (f": {desc}" if desc else ""))
    return (
        f"Task ID: {task.id}\nTask: {task.title}\n\n"
        "REVIEW PASS. This task has already been decomposed. Below is its tree as it "
        "stands now; indentation shows nesting.\n\n"
        f"DESCRIPTION:\n{task.description or ''}\n\n"
        "TREE:\n" + "\n".join(lines) + "\n\n"
        "Go over the task and every subtask once more:\n"
        "- A subtask that is still too large or too vague for a single agent run: "
        "decompose it with add_subtask(parent_id=<that subtask's ID>).\n"
        "- A step the plan is missing: add it with add_subtask under the task or under "
        "the subtask it belongs to, with depends where the order matters.\n"
        "- Do not recreate, rename or duplicate a task that already exists, and leave "
        "the tasks that are fine alone.\n"
        "If nothing needs to change, create nothing and say that the plan is complete. "
        "End with a brief summary of what you added."
    )


def _run_pass(task, kind: str, prompt: str, *, root: Path, channel: Optional[str],
              overrides: dict, run_id: str, outputs: Optional[List[str]] = None) -> bool:
    """One decomposer run, recorded, logged to the workspace and streamed.
    Its answer is appended to ``outputs`` when given."""
    from agents.agent_invoke import invoke_agent
    from managers import run_manager as rm

    log_file = root / ".logs" / f"agent_run_{run_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Built before the run record, so the record names the model it ran on.
    agent, build_error = None, None
    try:
        agent = create_agent("decomposer", workspace=str(root), **overrides)
    except Exception as e:  # noqa: BLE001 - recorded on the run below
        build_error = e
    rm.open_run(run_id, "decomposer", task_id=str(task.id), workspace=task.workspace,
                title=f"Decompose ({kind}): {task.title}", log_file=str(log_file),
                link_to_session=False, input=prompt,
                provider=getattr(agent, "provider", "") or "", model=getattr(agent, "model", "") or "")
    publisher = None
    if channel:
        from agents.callbacks.streaming import ChannelPublishCallback
        publisher = ChannelPublishCallback(channel, run_id, "decomposer")
        publisher._post({"type": "meta", "run_id": run_id, "session_id": channel,
                         "agent_id": "decomposer"})
    with log_file.open("w", encoding="utf-8") as fh:
        fh.write(f"[decomposer] {kind} pass start\n")
        fh.flush()
        result = None
        invocation = None
        try:
            if build_error is not None:
                raise build_error
            invocation = invoke_agent(agent, prompt, run_id=run_id,
                                      extra_callbacks=[publisher] if publisher else ())
            result = invocation.result
        except Exception as e:  # noqa: BLE001 - a failed pass is written to its log and run
            fh.write(f"error: {e}\n")
            if publisher:
                publisher._post({"type": "error", "run_id": run_id, "error": str(e)})
        if result is not None:
            fh.write((str(result.agent_output) if result.ok else f"error: {result.error}") + "\n")
    if result is not None:
        try:
            rm.close_run_from_result(run_id, result, process=invocation.process)
        except Exception:  # noqa: BLE001 - the log already holds the outcome
            log.debug("decompose: closing the run record failed", exc_info=True)
        if publisher:
            publisher.publish_done(result, invocation)
        if outputs is not None and result.ok:
            outputs.append(str(result.agent_output or ""))
        return bool(result.ok)
    try:
        rm.update_run(run_id, {"status": "failed"})
    except Exception:  # noqa: BLE001
        log.debug("decompose: marking the run failed failed", exc_info=True)
    return False


def _record_person_checks(task_id, outputs: List[str]) -> None:
    """Keep the checks only a person can make in the task's activity, where
    they stay once the decompose window is closed."""
    checks: List[str] = []
    for out in outputs:
        for item in person_checks(out):
            if item not in checks:
                checks.append(item)
    if not checks:
        return
    try:
        tasks_service.append_task_activity_log(
            task_id, "person_checks",
            f"{PERSON_CHECKS_HEADING}: " + "; ".join(checks), checks=checks)
    except Exception:  # noqa: BLE001 - the summary is still in the decomposer's run
        log.debug("decompose: recording the person checks failed", exc_info=True)


def start(task, *, root: Path, channel: Optional[str] = None, overrides: Optional[dict] = None,
          thread_factory=threading.Thread) -> dict:
    """Start the two passes in a thread; returns the first pass's run id and
    the initial status. Raises ``DecomposeBusy`` when one is already running."""
    from common import secrets as _secrets
    from common.identity import current_user_id
    from managers import run_manager as rm

    task_id = task.id
    with _lock:
        current = _state.get(str(task_id))
        if current and current.get("state") == "running":
            raise DecomposeBusy("This task is already being decomposed")
        _state[str(task_id)] = {"task_id": str(task_id), "state": "running", "pass": PASSES[0],
                                "run_ids": [], "channel": channel, "started_at": time.time()}
    run_id = rm.new_unique_run_id()
    _set(task_id, channel, run_ids=[run_id])
    try:
        tasks_service.assign_agent(task_id, "decomposer", None, run_id=run_id)
    except Exception:  # noqa: BLE001 - the mark is for the UI only
        log.debug("decompose: assigning the decomposer failed", exc_info=True)
    launched_by = current_user_id()
    overrides = dict(overrides or {})

    def body():
        outputs: List[str] = []
        ok = _run_pass(task, PASSES[0], split_prompt(task), root=root, channel=channel,
                       overrides=overrides, run_id=run_id, outputs=outputs)
        tree = subtree(task_id)
        if ok and tree:
            review_id = rm.new_unique_run_id()
            _set(task_id, channel, run_ids=[run_id, review_id], **{"pass": PASSES[1]})
            fresh = tasks_service.get_task(task_id) or task
            ok = _run_pass(fresh, PASSES[1], review_prompt(fresh, tree), root=root,
                           channel=channel, overrides=overrides, run_id=review_id,
                           outputs=outputs)
        _record_person_checks(task_id, outputs)
        # Decomposing does not execute the task, so it is left without an
        # executor and can be assigned to whoever should run it.
        try:
            current_task = tasks_service.get_task(task_id)
            if current_task and current_task.assigned_agent_type == "decomposer":
                tasks_service.clear_agent(task_id)
        except Exception:  # noqa: BLE001
            log.debug("decompose: clearing the decomposer failed", exc_info=True)
        _set(task_id, channel, state="done" if ok else "failed", finished_at=time.time(),
             created=len(subtree(task_id)))

    def worker():
        try:
            with _secrets.activate(task.workspace or root.name, "decomposer", launched_by):
                body()
        except Exception as e:  # noqa: BLE001 - the status must not stay "running"
            log.warning("decompose of %s failed: %s", task_id, e, exc_info=True)
            _set(task_id, channel, state="failed", error=str(e), finished_at=time.time())

    thread_factory(target=worker, daemon=True).start()
    return {"run_id": run_id, **(status(task_id) or {})}
