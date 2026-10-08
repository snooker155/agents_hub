"""Planner chat: generating project tasks from the graph views."""
from ._common import (_graph_store, _project_root_path, store)
from .graph import (_RecordingQueue, _SSE_HEADERS, _clean_agent_reply, _project_tasks_for,
    _register_run, _relay_queue, _run_turn_guarded, _spawn_detached, _sse, _trace_item_for)
import asyncio
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

_PLANNER_AGENT_ID = planner_service.PLANNER_AGENT_ID
# Store key for the planner chat (its trace/messages/session live on the Tasks
# tab, decoupled from the architecture/process graph views).
_TASKS_VIEW = "tasks"
_PLANNER_USER_MSG = "Generate tasks from the architecture & process graphs."


@router.get("/{project_id}/tasks/chat")
async def get_tasks_chat(project_id: str):
    """Return the planner chat transcript + rich trace for the Tasks tab."""
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "messages": _graph_store.get_messages(project_id, _TASKS_VIEW),
        "trace": _graph_store.get_trace(project_id, _TASKS_VIEW),
    }


@router.delete("/{project_id}/tasks/chat")
async def clear_tasks_chat(project_id: str):
    """Clear the planner chat and start a fresh session (tasks are left alone)."""
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    epoch = _graph_store.clear_messages(project_id, _TASKS_VIEW, new_session=True)
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

    root = _project_root_path(project)
    # The "Generate" button sends no message (use the default); the chat input
    # sends the user's typed instruction for a conversational planning turn.
    user_message = (payload.message if payload else None) or _PLANNER_USER_MSG
    user_message = user_message.strip() or _PLANNER_USER_MSG

    async def run_plan(queue: asyncio.Queue):
        from projects.graph import agent_workspace_path
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

        _graph_store.append_message(project_id, _TASKS_VIEW, "user", user_message)

        # Open a run record so the planner run appears in Messages, grouped per
        # project+session-epoch (Clear chat starts a fresh session).
        run_id = _new_run_id()
        log_file = _run_log_path(run_id)
        epoch = _graph_store.get_session_epoch(project_id, _TASKS_VIEW)
        conv_id = f"projtasks:{project_id}" + (f":{epoch}" if epoch else "")
        session_id = None
        try:
            from common.session_service import get_or_create_chat_session
            session_id = get_or_create_chat_session(
                conversation_id=conv_id,
                title=f"{project.name} · task plan" + (f" #{epoch + 1}" if epoch else ""),
                workspace=project.workspace,
                agent_id=_PLANNER_AGENT_ID,
            )
        except Exception:
            session_id = None
        _open_run(run_id, _PLANNER_AGENT_ID, task_id=conv_id, session_id=session_id,
                  session_type="chat", message_origin="planner-chat", channel="chat",
                  workspace=project.workspace, title=user_message[:60], log_file=str(log_file))

        msg_id = run_id[:8]
        started = _iso()
        log_lines = [
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {_PLANNER_AGENT_ID}",
            f"Workspace : {project.workspace or '—'}",
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
            before = len(_project_tasks_for(project_id) or [])
        except Exception:
            before = 0

        # Recent conversation so multi-turn planning ("also split X", "reprioritise
        # Y") has context. The user turn was just recorded above, so drop it here.
        history = _graph_store.get_messages(project_id, _TASKS_VIEW)[:-1]
        prompt = planner_service.build_prompt(history, user_message)

        reply, status, err = "", "completed", None
        callback = None
        provider = model = ""
        # Scope the run to the project BEFORE building the agent: create_agent
        # injects the read-only get_project_graph tool only when
        # resolve_active_project() is set, and new tasks inherit the active
        # project the same way. asyncio.to_thread / create_task both copy the
        # current context, so create_agent and the run see the scope; we reset
        # once the task has captured it (mirrors the graph_sink handler pattern).
        ws_token = _workspace_ctx.set(project.workspace)
        pj_token = _project_ctx.set(normalize_project_id(project_id))
        # The agent part on a runner replica (chat/remote_agent.py): what it
        # reported, when the turn ran there rather than in this process.
        remote = None
        if not planner_service.ensure_planner_agent():
            status, err = "failed", "planner agent unavailable"
            await emit(_error_event("registry", err))
        elif _remote_agent.enabled():
            task = asyncio.create_task(_remote_agent.stream_agent_turn(
                queue, agent_id=_PLANNER_AGENT_ID, run_id=run_id, prompt=prompt,
                workspace=project.workspace, workspace_path=agent_workspace_path(project, root),
                project_id=normalize_project_id(project_id), session_id=session_id,
                log_file=str(log_file), log_lines=log_lines,
                build={"max_tool_repeats": 0, "max_iterations": 400},
                user_message=user_message))
            _register_run(project_id, "__plan__", task)
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
                    planner_service.build_agent, agent_workspace_path(project, root))
                provider, model = agent.provider or "", agent.model or ""
                _update_run(run_id, {"provider": provider, "model": model})
                callback.bind_model(provider, model)
                await emit({"type": "agent", "agent_id": _PLANNER_AGENT_ID,
                            "provider": provider, "model": model})

                task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))
                _register_run(project_id, "__plan__", task)

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
        _project_ctx.reset(pj_token)
        _workspace_ctx.reset(ws_token)

        try:
            after = len(_project_tasks_for(project_id) or [])
        except Exception:
            after = before
        created = max(0, after - before)

        if not reply:
            reply = (f"Couldn't complete the request: {err}" if err
                     else f"Created {created} task(s) from the project's graphs.")

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

        _graph_store.append_message(project_id, _TASKS_VIEW, "assistant", reply)
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
            _graph_store.append_trace(project_id, _TASKS_VIEW, turn_items)
        except Exception:
            pass

    async def event_stream():
        queue = _RecordingQueue()
        yield _sse({"type": "meta", "view": _TASKS_VIEW})
        worker = _spawn_detached(_run_turn_guarded(run_plan, queue))
        async for frame in _relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


