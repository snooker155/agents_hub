"""Planner chat: generating project tasks from the graph views."""
from ._common import (_graph_store, _project_root_path, store)
from .graph import (_RecordingQueue, _SSE_HEADERS, _clean_agent_reply, _project_tasks_for,
    _register_run, _relay_queue, _run_turn_guarded, _spawn_detached, _sse, _trace_item_for)
import asyncio
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from models import ProjectTasksChat
from chat.errors import error_event as _error_event
from chat.entity_chat import settle_tool_step
from projects import planner_service


router = APIRouter(prefix="/api/projects", tags=["projects"])

# ─────────────────────────── PLANNER (tasks from views) ────────────────────────────
#
# The Planner agent itself — its prompt, and how it is built and run — lives in
# projects/planner_service.py; this section is the SSE/run-record plumbing
# around one turn, shared in shape with the graph chat above.

from chat import remote_agent as _remote_agent  # noqa: E402
import logging

log = logging.getLogger(__name__)

_PLANNER_AGENT_ID = planner_service.PLANNER_AGENT_ID
# Store key for the planner chat (its trace/messages/session live on the Tasks
# tab, decoupled from the architecture/process graph views).
_TASKS_VIEW = "tasks"
_PLANNER_USER_MSG = "Generate tasks from the architecture & process graphs."

# The planner chat lives in the shared entity chat store (kind ``projtasks``),
# like every other build chat: "New chat" archives the thread, and the
# generic /api/entity-chats/sessions routes list, reopen and delete the past
# ones. It used to live in the project graph store, which kept no history;
# a chat still there is moved over the first time it is read.
_CHAT_KIND = "projtasks"


def _chats():
    from common.entity_chat_store import entity_chat_store
    return entity_chat_store()


# The Tasks page's planner works on a whole workspace: no project, so no
# graphs to read, and its chat is kept per workspace.
_WS_CHAT_KIND = "wstasks"
_WS_PLANNER_USER_MSG = "Look at the tasks in this workspace and propose how to break down the open work."


@dataclass
class _PlanScope:
    """What one planner turn works on: a project (its Tasks tab) or a
    workspace (the Tasks page)."""
    chat_kind: str
    key: str                 # the chat's entity id
    run_key: str             # what Stop cancels (_cancel_runs prefix)
    workspace: Optional[str]
    project_id: Optional[str]
    name: str
    agent_path: Optional[str]

    def task_count(self) -> int:
        try:
            if self.project_id:
                return len(_project_tasks_for(self.project_id) or [])
            from tasks import service as tasks_service
            ws = (self.workspace or "").strip()
            return sum(1 for t in tasks_service.list_tasks() if (t.workspace or "").strip() == ws)
        except Exception:  # noqa: BLE001 - an unreadable tracker counts as no tasks created
            log.debug("counting the planner's tasks failed", exc_info=True)
            return 0


def _migrate_graph_chat(project_id: str) -> None:
    """Move a planner chat saved before the move into the entity chat store."""
    chats = _chats()
    if chats.has_chat(_CHAT_KIND, project_id):
        return
    messages = _graph_store.get_messages(project_id, _TASKS_VIEW)
    trace = _graph_store.get_trace(project_id, _TASKS_VIEW)
    if not messages and not trace:
        return
    for m in messages:
        chats.append_message(_CHAT_KIND, project_id, m.get("role") or "user", m.get("content") or "")
    if trace:
        chats.append_trace(_CHAT_KIND, project_id, list(trace))
    _graph_store.clear_messages(project_id, _TASKS_VIEW, new_session=False)


@router.get("/{project_id}/tasks/chat")
async def get_tasks_chat(project_id: str):
    """Return the planner chat transcript + rich trace for the Tasks tab."""
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    _migrate_graph_chat(project_id)
    return {
        "messages": _chats().get_messages(_CHAT_KIND, project_id),
        "trace": _chats().get_trace(_CHAT_KIND, project_id),
        "chat_ref": {"kind": _CHAT_KIND, "id": project_id},
    }


@router.delete("/{project_id}/tasks/chat")
async def clear_tasks_chat(project_id: str):
    """Start a new planner chat; the current one stays in the history (tasks
    are left alone)."""
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    _migrate_graph_chat(project_id)
    epoch = _chats().clear(_CHAT_KIND, project_id, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/{project_id}/tasks/generate")
async def generate_project_tasks(project_id: str, payload: Optional[ProjectTasksChat] = None):
    """Generate a task plan from the project's structure graphs (SSE).

    Runs the Planner agent scoped to the project so it can read the architecture
    and process graphs (via the injected ``get_project_graph``) and create tasks
    that attach to the project. Opens a full run record (so the run shows in
    Messages with its tool calls / thinking / stats, like any other run) and
    persists its rich trace under the Tasks-tab chat. Streams the agent's
    tool/thinking events, then a final ``message`` + ``done`` (with how many tasks
    were created).
    """
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _migrate_graph_chat(project_id)

    from projects.graph import agent_workspace_path
    scope = _PlanScope(
        chat_kind=_CHAT_KIND, key=project_id, run_key=project_id,
        workspace=project.workspace, project_id=project_id, name=project.name,
        agent_path=agent_workspace_path(project, _project_root_path(project)),
    )
    # The "Generate" button sends no message (use the default); the chat input
    # sends the user's typed instruction for a conversational planning turn.
    user_message = (payload.message if payload else None) or _PLANNER_USER_MSG
    user_message = user_message.strip() or _PLANNER_USER_MSG
    return _plan_stream(scope, user_message)


async def _run_plan(scope: "_PlanScope", user_message: str, queue: asyncio.Queue) -> None:
    """One planner turn for *scope*: the run record, the agent run, the reply
    and the stored trace. Shared by the project's Tasks tab and the Tasks page."""
    from common.workspace_context import (
        _project_ctx, _workspace_ctx, normalize_project_id,
    )
    from managers.run_manager import (
        open_run as _open_run, update_run as _update_run,
        new_unique_run_id as _new_run_id, run_log_path as _run_log_path,
    )
    from datetime import datetime, timezone
    import time as _time
    from agents.callbacks import write_log as _write_log, append_log as _append_log

    def _iso():
        return datetime.now(timezone.utc).isoformat()

    async def emit(payload):
        await queue.put(payload)

    _chats().append_message(scope.chat_kind, scope.key, "user", user_message)

    # Open a run record so the planner run appears in Messages, grouped per
    # project+session-epoch (Clear chat starts a fresh session).
    run_id = _new_run_id()
    log_file = _run_log_path(run_id)
    epoch = _chats().get_session_epoch(scope.chat_kind, scope.key)
    conv_id = f"{scope.chat_kind}:{scope.key}" + (f":{epoch}" if epoch else "")
    session_id = None
    try:
        from common.session_service import get_or_create_chat_session
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=f"{scope.name} · task plan" + (f" #{epoch + 1}" if epoch else ""),
            workspace=scope.workspace,
            agent_id=_PLANNER_AGENT_ID,
        )
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("emit: falling back after a failure", exc_info=True)
        session_id = None
    _open_run(run_id, _PLANNER_AGENT_ID, task_id=conv_id, session_id=session_id,
              session_type="chat", message_origin="planner-chat", channel="chat",
              workspace=scope.workspace, title=user_message[:60], log_file=str(log_file))

    msg_id = run_id[:8]
    started = _iso()
    log_lines = [
        f"=== Chat message  run_id={run_id} ===",
        f"Started   : {started}",
        f"Agent     : {_PLANNER_AGENT_ID}",
        f"Workspace : {scope.workspace or '—'}",
        f"Conv ID   : {conv_id}",
        f"Session ID: {session_id or '—'}",
        f"Title     : {user_message[:60]}",
        "",
        f"=== Message at {started} id={msg_id} ===",
        "--- User message ---",
        user_message,
        "",
        "--- Agent response (stream) ---",
    ]
    _write_log(log_file, log_lines)
    message_started = _time.perf_counter()

    # Snapshot the task count so we can report how many were created.
    try:
        before = scope.task_count()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("emit: falling back after a failure", exc_info=True)
        before = 0

    # Recent conversation so multi-turn planning ("also split X", "reprioritise
    # Y") has context. The user turn was just recorded above, so drop it here.
    history = _chats().get_messages(scope.chat_kind, scope.key)[:-1]
    prompt = planner_service.build_prompt(history, user_message, project=scope.project_id is not None)

    reply, status, err = "", "completed", None
    callback = None
    provider = model = ""
    # Scope the run to the project BEFORE building the agent: create_agent
    # injects the read-only get_project_graph tool only when
    # resolve_active_project() is set, and new tasks inherit the active
    # project the same way. asyncio.to_thread / create_task both copy the
    # current context, so create_agent and the run see the scope; we reset
    # once the task has captured it (mirrors the graph_sink handler pattern).
    ws_token = _workspace_ctx.set(scope.workspace)
    pj_token = _project_ctx.set(normalize_project_id(scope.project_id)) if scope.project_id else None
    # The agent part on a runner replica (chat/remote_agent.py): what it
    # reported, when the turn ran there rather than in this process.
    remote = None
    if not planner_service.ensure_planner_agent():
        status, err = "failed", "planner agent unavailable"
        await emit(_error_event("registry", err))
    elif _remote_agent.enabled():
        task = asyncio.create_task(_remote_agent.stream_agent_turn(
            queue, agent_id=_PLANNER_AGENT_ID, run_id=run_id, prompt=prompt,
            workspace=scope.workspace, workspace_path=scope.agent_path,
            project_id=normalize_project_id(scope.project_id) if scope.project_id else None,
            session_id=session_id, log_file=str(log_file), log_lines=log_lines,
            build={"max_tool_repeats": 0, "max_iterations": 400},
            user_message=user_message))
        _register_run(scope.run_key, "__plan__", task)
        from chat.entity_chat import register_entity_run
        register_entity_run(scope.chat_kind, scope.key, task)
        try:
            remote = await task
        except asyncio.CancelledError:
            status = "stopped"
            await emit({"type": "stopped"})
        except Exception as e:  # noqa: BLE001
            status, err = "failed", str(e)
            await emit(_error_event("agent", err))
        if remote is not None:
            provider, model = remote.provider, remote.model
            _update_run(run_id, {"provider": provider, "model": model, **remote.run_fields()})
            if remote.status == "stopped":
                status = "stopped"
                await emit({"type": "stopped"})
            elif remote.ok:
                reply = _clean_agent_reply(remote.agent_output)
            else:
                status, err = "failed", (remote.error or "agent returned no output")
                await emit(_error_event("agent", err))
    else:
        try:
            from agents.callbacks import ChatStreamCallback

            loop = asyncio.get_running_loop()
            callback = ChatStreamCallback(loop, queue, log_lines, log_file, session_id=session_id)

            agent = await asyncio.to_thread(
                planner_service.build_agent, scope.agent_path)
            provider, model = agent.provider or "", agent.model or ""
            _update_run(run_id, {"provider": provider, "model": model})
            callback.bind_model(provider, model)
            await emit({"type": "agent", "agent_id": _PLANNER_AGENT_ID,
                        "provider": provider, "model": model})

            task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))  # pyright: ignore[reportAttributeAccessIssue]  # arun is on the concrete agents, not AgentBase
            _register_run(scope.run_key, "__plan__", task)
            # The session picker refuses to switch threads mid-run.
            from chat.entity_chat import register_entity_run
            register_entity_run(scope.chat_kind, scope.key, task)

            try:
                res = await task
            except asyncio.CancelledError:
                status, res = "stopped", None
                await emit({"type": "stopped"})
            if res is not None:
                if getattr(res, "ok", False):
                    reply = _clean_agent_reply(str(res.agent_output))
                else:
                    status, err = "failed", (getattr(res, "error", None) or "agent returned no output")
                    await emit(_error_event("agent", err))
        except Exception as e:  # noqa: BLE001
            status, err = "failed", str(e)
            await emit(_error_event("agent", err))
    if pj_token is not None:
        _project_ctx.reset(pj_token)
    _workspace_ctx.reset(ws_token)

    try:
        after = scope.task_count()
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        log.debug("emit: falling back after a failure", exc_info=True)
        after = before
    created = max(0, after - before)

    if not reply:
        reply = (f"Couldn't complete the request: {err}" if err
                 else f"Created {created} task(s)" + (" from the project's graphs." if scope.project_id else "."))

    # Finalize the run record with the response + tool/usage process payload,
    # so the run renders in Messages exactly like a normal chat run.
    usage = {
        "inbound_tokens": getattr(callback, "prompt_tokens", 0) if callback else 0,
        "outbound_tokens": getattr(callback, "completion_tokens", 0) if callback else 0,
        "total_tokens": getattr(callback, "total_tokens", 0) if callback else 0,
        "context_window": getattr(callback, "context_window", 0),
        "context_used": getattr(callback, "max_prompt_tokens", 0),
    }
    process_payload = {
        "llm_input_context": {
            "system_prompt": (getattr(callback, "_last_prompt_struct", {}) or {}).get("system_prompt", "") if callback else "",
            "user_message": user_message,
            "response": reply,
            "llm_invocations": getattr(callback, "llm_invocations", []) if callback else [],
        },
        "tool_calls": getattr(callback, "tool_history", []) if callback else [],
        "thinking": getattr(callback, "thinking_history", []) if callback else [],
        "llm_invoke_responses": getattr(callback, "llm_invoke_responses", []) if callback else [],
        "artifacts": getattr(callback, "artifact_history", []) if callback else [],
        "token_usage": usage,
    }
    if remote is not None:
        usage = {**usage, **remote.usage}
        process_payload = {**process_payload, **remote.process, "token_usage": usage}
        process_payload["llm_input_context"] = {
            **(process_payload.get("llm_input_context") or {}),
            "user_message": user_message, "response": reply}
    finished = _iso()
    duration_ms = int((_time.perf_counter() - message_started) * 1000)
    if remote is None:
        # The runner closes the log itself when the turn ran there.
        summary_line = (
            f"[message_summary] id={msg_id} "
            f"inbound_tokens={usage['inbound_tokens']} "
            f"outbound_tokens={usage['outbound_tokens']} "
            f"total_tokens={usage['total_tokens']} "
            f"tool_calls={getattr(callback, 'tool_calls', 0) if callback else 0} "
            f"duration_ms={duration_ms}"
        )
        _append_log(log_lines, reply, log_file)
        _append_log(log_lines, summary_line, log_file)
        _write_log(log_file, log_lines + ["", f"Finished: {finished}", f"Status  : {status}"])

    _update_run(run_id, {
        "status": status, "finished_at": finished,
        "exit_code": 0 if status == "completed" else 1,
        "error": err, "response": reply, "process": process_payload,
    })

    _chats().append_message(scope.chat_kind, scope.key, "assistant", reply)
    await emit({"type": "message", "role": "assistant", "content": reply, "run_id": run_id})
    await emit({"type": "done", "created": created, "run_id": run_id, "usage": usage})

    # Persist the rich trace under the Tasks-tab chat so a reload restores it
    # (captured in the worker → survives a client disconnect mid-run).
    recorded = list(getattr(queue, "recorded", []))
    turn_items = [{"k": "user", "text": user_message}]
    for ev in recorded:
        if settle_tool_step(turn_items, ev):
            continue
        item = _trace_item_for(ev)
        if item:
            turn_items.append(item)
    try:
        _chats().append_trace(scope.chat_kind, scope.key, turn_items)
    except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
        log.debug("emit: best-effort step failed", exc_info=True)


def _plan_stream(scope: "_PlanScope", user_message: str) -> StreamingResponse:
    """Stream one planner turn over SSE; the turn itself runs detached, so a
    closed tab does not stop it."""
    async def event_stream():
        queue = _RecordingQueue()
        yield _sse({"type": "meta", "view": _TASKS_VIEW})
        worker = _spawn_detached(_run_turn_guarded(
            lambda q: _run_plan(scope, user_message, q), queue))
        async for frame in _relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ── The Tasks page: a planner over the whole workspace ───────────────────────

ws_router = APIRouter(prefix="/api/task-planner", tags=["tasks"])


def _workspace_scope(workspace: str) -> _PlanScope:
    from workspace import get_workspace_folder
    name = (workspace or "").strip()
    folder = get_workspace_folder(name) if name else None
    if folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _PlanScope(
        chat_kind=_WS_CHAT_KIND, key=name, run_key=f"ws:{name}",
        workspace=name, project_id=None, name=name, agent_path=str(folder),
    )


@ws_router.get("/chat")
async def get_workspace_planner_chat(workspace: str):
    """The Tasks page planner's transcript and trace for one workspace."""
    scope = _workspace_scope(workspace)
    return {
        "messages": _chats().get_messages(scope.chat_kind, scope.key),
        "trace": _chats().get_trace(scope.chat_kind, scope.key),
        "chat_ref": {"kind": scope.chat_kind, "id": scope.key},
    }


@ws_router.delete("/chat")
async def clear_workspace_planner_chat(workspace: str):
    """Start a new planner chat for the workspace; the current one stays in
    the history and the tasks are left alone."""
    scope = _workspace_scope(workspace)
    epoch = _chats().clear(scope.chat_kind, scope.key, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@ws_router.post("/generate")
async def generate_workspace_tasks(workspace: str, payload: Optional[ProjectTasksChat] = None):
    """One planner turn over the workspace's tasks (SSE), the same stream the
    project's Tasks tab gets, without the project graphs."""
    scope = _workspace_scope(workspace)
    user_message = ((payload.message if payload else None) or _WS_PLANNER_USER_MSG).strip() \
        or _WS_PLANNER_USER_MSG
    return _plan_stream(scope, user_message)


@ws_router.post("/stop")
async def stop_workspace_planner(workspace: str):
    """Stop the workspace planner's run in flight; what it already created stays."""
    scope = _workspace_scope(workspace)
    from .graph import _cancel_runs
    cancelled = _cancel_runs(scope.run_key)
    return {"stopped": cancelled > 0, "cancelled": cancelled}
