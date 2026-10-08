"""Project structure graph and its generation and chat streams."""
from ._common import (_graph_store, _project_root_path, store)
import asyncio
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from tasks import service as tasks_service
from models import ProjectGraphSave, ProjectGraphChat
from chat.errors import error_event as _error_event
from chat.entity_chat import settle_tool_step


router = APIRouter(prefix="/api/projects", tags=["projects"])

# ─────────────────────────── STRUCTURE GRAPH ────────────────────────────

def _validate_view(view: str) -> None:
    if view not in ("architecture", "process"):
        raise HTTPException(status_code=400, detail="view must be 'architecture' or 'process'")


def _project_tasks_for(project_id: str):
    all_tasks = tasks_service.list_tasks()
    return [t for t in all_tasks if t.project_id == project_id]


@router.get("/{project_id}/graph")
async def get_project_graph(project_id: str, view: str = Query("architecture")):
    """Return the project's structure graph.

    Serves a saved (hand-edited or AI-generated) graph when one exists;
    otherwise builds it deterministically. ``view=architecture`` is the technical
    structure (client → service → data/dependencies) inferred from config + a
    shallow source scan; ``view=process`` is the business/process flow derived
    from the project's task hierarchy and ordering. The response carries a
    ``source`` of ``auto`` | ``manual`` | ``generated``.
    """
    _validate_view(view)
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    saved = _graph_store.get(project_id, view)
    if saved:
        from projects.graph import strip_placeholder_nodes
        saved["nodes"], saved["edges"] = strip_placeholder_nodes(
            saved.get("nodes") or [], saved.get("edges") or [])
        return saved

    from projects.graph import build_project_graph

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None
    try:
        graph = await asyncio.to_thread(build_project_graph, project, view, root, tasks)
        graph["source"] = "auto"
        return graph
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{project_id}/graph")
async def save_project_graph(project_id: str, payload: ProjectGraphSave,
                            view: str = Query("architecture")):
    """Persist hand-edited nodes/edges so they survive a rebuild."""
    _validate_view(view)
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _graph_store.save(project_id, view, payload.nodes, payload.edges, source="manual")


@router.post("/{project_id}/graph/relayout")
async def relayout_project_graph(project_id: str, payload: ProjectGraphSave,
                                 view: str = Query("architecture")):
    """Re-arrange the given nodes/edges into horizontal bands by kind (same kind
    side by side, different kinds stacked) and return them (positions only change).
    Not persisted — the caller saves."""
    _validate_view(view)
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    from projects.graph import kind_grouped_layout
    nodes = list(payload.nodes or [])
    edges = list(payload.edges or [])
    kind_grouped_layout(nodes, edges)
    return {"nodes": nodes, "edges": edges}


@router.delete("/{project_id}/graph")
async def reset_project_graph(project_id: str, view: str = Query("architecture")):
    """Discard the saved graph so the view reverts to the deterministic build."""
    _validate_view(view)
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    deleted = _graph_store.delete(project_id, view)
    return {"reset": deleted}


@router.post("/{project_id}/graph/generate")
async def generate_project_graph(project_id: str, view: str = Query("architecture")):
    """Generate the graph with an LLM (project context + detected structure),
    persist it as ``generated``, and return it."""
    _validate_view(view)
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from projects.graph import generate_graph_via_llm

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None
    try:
        graph = await asyncio.to_thread(generate_graph_via_llm, project, view, root, tasks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    saved = _graph_store.save(project_id, view, graph["nodes"], graph["edges"],
                              source=graph.get("source", "generated"))
    return saved


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# Tool-call / reasoning channel artifacts some local models (gpt-oss "harmony"
# format) leak into their final text. When present the reply is unusable, so we
# fall back to a summary of what the agent actually changed.
_REPLY_GARBAGE_MARKERS = (
    "to=functions.", "<|constrain|>", "<|channel|>", "<|call|>",
    "<|start|>", "<|end|>", "commentary<|", "assistantfinal",
)


def _clean_agent_reply(text: str) -> str:
    t = (text or "").strip()
    if not t or any(m in t for m in _REPLY_GARBAGE_MARKERS):
        return ""
    return t


# ─────────────────── Disconnect-proof SSE agent runs ───────────────────
# When the user leaves the page (or just switches tabs) mid-run, the browser
# aborts the fetch, which cancels the StreamingResponse generator. We must NOT
# let that abort the agent or skip the run's finalization (persisting the
# assistant reply, closing the run record). So the agent + all persistence run
# in a *detached* task that pushes events into a queue; the SSE generator only
# relays them. If the client disconnects, the relay stops but the worker keeps
# going to completion — the result is there when the user returns (the page
# reloads the graph + chat history on mount).
_STREAM_DONE = object()
_BG_RUNS: set = set()

# Active agent tasks per project, so a "Stop" request can cancel an in-flight
# architect-chat / planner run. Keyed by f"{project_id}:{slot}" (slot = the view
# for chat, or "__plan__" for task generation). Detached runs survive client
# disconnects, so cancelling the underlying agent task is the only way to stop one.
_ACTIVE_GRAPH_RUNS: dict = {}


def _register_run(project_id: str, slot: str, task: asyncio.Task) -> None:
    key = f"{project_id}:{slot}"
    _ACTIVE_GRAPH_RUNS[key] = task
    task.add_done_callback(
        lambda _t, k=key: (_ACTIVE_GRAPH_RUNS.pop(k, None) if _ACTIVE_GRAPH_RUNS.get(k) is _t else None))


def _cancel_runs(project_id: str) -> int:
    """Cancel every in-flight run for a project. Returns how many were cancelled."""
    cancelled = 0
    for key, task in list(_ACTIVE_GRAPH_RUNS.items()):
        if key.startswith(f"{project_id}:") and task and not task.done():
            task.cancel()
            cancelled += 1
    return cancelled


def _spawn_detached(coro) -> asyncio.Task:
    """Run *coro* as a task that survives client disconnects (strong ref held)."""
    task = asyncio.create_task(coro)
    _BG_RUNS.add(task)
    task.add_done_callback(_BG_RUNS.discard)
    return task


class _RecordingQueue(asyncio.Queue):
    """An asyncio.Queue that also keeps every item put into it.

    Lets the detached worker persist the full event trace after the run — even
    if the client disconnected mid-run and the relay stopped reading.
    ``_put`` is the single choke point for both ``put`` and ``put_nowait``.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.recorded: list = []

    def _put(self, item):
        super()._put(item)
        try:
            self.recorded.append(item)
        except Exception:
            pass


def _trace_item_for(ev: dict):
    """Map one streamed SSE event to a chat feed item (mirrors the UI), or None.

    Kept in sync with the onEvent switch in ProjectGraph.jsx so a reloaded
    session renders identically to the live run.
    """
    t = ev.get("type")
    if t == "think":
        c = ev.get("content")
        return {"k": "thinking", "text": c} if c else None
    if t in ("thinking", "native_reasoning"):
        txt = ev.get("message") or ev.get("content") or ""
        return {"k": "thinking", "text": txt} if txt and not txt.startswith("[") else None
    if t == "tool_start":
        tool = str(ev.get("tool") or "")
        if tool.startswith("add_graph") or tool.startswith("delete_graph") or tool == "clear_graph":
            return None  # graph tools surface as their own node/edge events
        return {"k": "tool", "tool": tool, "status": "running"}
    if t == "graph_node":
        node = ev.get("node") or {}
        return {"k": "node", "label": (node.get("data") or {}).get("label") or node.get("id")}
    if t == "graph_edge":
        e = ev.get("edge") or {}
        return {"k": "node", "label": "", "edge": f"{e.get('source')} → {e.get('target')}"}
    if t == "graph_node_delete":
        return {"k": "tool", "tool": f"removed {ev.get('id')}", "status": "done"}
    if t == "graph_edge_delete":
        return {"k": "tool", "tool": f"removed edge {ev.get('id')}", "status": "done"}
    if t == "graph_clear":
        return {"k": "tool", "tool": "cleared the graph", "status": "done"}
    if t == "stopped":
        return {"k": "tool", "tool": "stopped by you", "status": "done"}
    if t == "message":
        c = ev.get("content")
        return {"k": "assistant", "text": c} if c else None
    if t == "error":
        return {"k": "error", "text": ev.get("error") or "error"}
    return None


async def _relay_queue(queue: "asyncio.Queue"):
    """Yield SSE frames from *queue* until the ``_STREAM_DONE`` sentinel.

    Tolerates client disconnect: if the consuming generator is cancelled we stop
    relaying but leave the producing worker running (it is a detached task).
    """
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_DONE:
                break
            yield _sse(item)
    except (asyncio.CancelledError, GeneratorExit):
        # Client went away — let the detached worker finish on its own.
        return


@router.get("/{project_id}/graph/generate/stream")
async def generate_project_graph_stream(project_id: str, view: str = Query("architecture")):
    """Stream the Architect Agent generating the graph (SSE).

    Forwards the agent's live execution events — ``tool_start`` / ``tool_end`` /
    ``thinking`` / ``token`` / ``error`` — then emits the final ``graph`` (also
    persisted as ``generated``) and a ``done`` marker. Falls back to the
    deterministic build if the agent is unavailable or returns no graph.
    """
    _validate_view(view)
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None

    async def event_stream():
        import tempfile
        from pathlib import Path as _Path
        from projects.graph import (
            build_generation_prompt, agent_workspace_path,
            finalize_generated_output, _ensure_architect_agent, _ARCHITECT_AGENT_ID,
        )

        yield _sse({"type": "meta", "view": view})

        prompt = build_generation_prompt(project, view, root, tasks)
        text = None

        if _ensure_architect_agent():
            try:
                from agents.agent_factory import create_agent
                from agents.callbacks import ChatStreamCallback

                loop = asyncio.get_running_loop()
                queue: asyncio.Queue = asyncio.Queue()
                log_file = _Path(tempfile.gettempdir()) / f"arch_gen_{project_id}_{view}.log"
                callback = ChatStreamCallback(loop, queue, [], log_file)

                # No model override — create_agent resolves the provider/model
                # through its canonical chain (workspace model_override /
                # settings → global .env), matching the user's selection.
                # No repetition ceiling (0 = UNLIMITED_TOOL_REPEATS): reading
                # file after file is normal analysis, not a loop (the guard
                # counts by tool name).
                agent = await asyncio.to_thread(
                    create_agent, _ARCHITECT_AGENT_ID,
                    workspace=agent_workspace_path(project, root), streaming=True,
                    max_tool_repeats=0,
                )
                callback.bind_model(agent.provider or "", agent.model or "")
                yield _sse({"type": "agent", "agent_id": _ARCHITECT_AGENT_ID,
                            "provider": agent.provider or "", "model": agent.model or ""})

                task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))

                # Forward callback events as they arrive; drain anything left
                # once the run finishes.
                while not task.done():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.2)
                        yield _sse(event)
                    except asyncio.TimeoutError:
                        continue
                while not queue.empty():
                    yield _sse(queue.get_nowait())

                res = await task
                if getattr(res, "ok", False):
                    text = str(res.agent_output)
                else:
                    yield _sse(_error_event(
                        "agent", getattr(res, "error", None) or "agent returned no output"))
            except Exception as e:  # noqa: BLE001
                yield _sse(_error_event("agent", str(e)))
        else:
            yield _sse(_error_event("registry", "architect agent unavailable"))

        graph = await asyncio.to_thread(finalize_generated_output, text, project, view, root, tasks)
        saved = _graph_store.save(project_id, view, graph["nodes"], graph["edges"],
                                  source=graph.get("source", "generated"))
        yield _sse({"type": "graph", **saved})
        yield _sse({"type": "done", "source": saved["source"]})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/{project_id}/graph/messages")
async def get_graph_messages(project_id: str, view: str = Query("architecture")):
    """Return the interactive build chat transcript for a view.

    ``messages`` is the plain user/assistant transcript; ``trace`` is the rich
    feed (thinking + tool/graph steps interleaved) the UI replays on reload so
    the full session — not just the bare conversation — is restored.
    """
    _validate_view(view)
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "messages": _graph_store.get_messages(project_id, view),
        "trace": _graph_store.get_trace(project_id, view),
    }


@router.delete("/{project_id}/graph/messages")
async def clear_graph_messages(project_id: str, view: str = Query("architecture")):
    """Clear the build chat transcript and start a fresh chat session.

    Wipes the conversation and advances the session epoch so the next turn opens
    a new backend session (a clean run-thread in Messages). The graph itself is
    left untouched.
    """
    _validate_view(view)
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    epoch = _graph_store.clear_messages(project_id, view, new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/{project_id}/graph/chat")
async def chat_project_graph(project_id: str, payload: ProjectGraphChat,
                             view: str = Query("architecture")):
    """Interactive graph build (SSE).

    Runs the Architect Agent on the user's message with the graph-builder tools
    bound to a live sink: each ``add_graph_node`` / ``add_graph_edge`` /
    ``clear_graph`` is persisted and streamed as a ``graph_node`` / ``graph_edge``
    / ``graph_clear`` event, so the canvas updates step by step. Also forwards
    the agent's ``tool_*`` / ``thinking`` / ``token`` events and a final
    ``message`` (assistant reply) + ``done``.
    """
    _validate_view(view)
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    root = _project_root_path(project)
    tasks = _project_tasks_for(project_id) if view == "process" else None

    async def run_turn(queue: asyncio.Queue):
        from projects.graph import (
            build_chat_turn_prompt, agent_workspace_path,
            _ensure_architect_agent, _ARCHITECT_AGENT_ID,
        )
        from projects.graph_chat import GraphStreamSink
        from common import graph_sink
        from managers.run_manager import (
            open_run as _open_run, update_run as _update_run,
            new_unique_run_id as _new_run_id, run_log_path as _run_log_path,
        )
        from datetime import datetime, timezone

        def _iso():
            return datetime.now(timezone.utc).isoformat()

        async def emit(payload):
            await queue.put(payload)

        # Record the user turn, then build the prompt from the live graph + history.
        _graph_store.append_message(project_id, view, "user", user_message)
        history = _graph_store.get_messages(project_id, view)
        current = _graph_store.get(project_id, view) or {"nodes": [], "edges": []}
        prompt = build_chat_turn_prompt(project, view, root, tasks, current, history, user_message)

        # Open a run record so the turn appears in Messages, grouped per
        # project+view+session-epoch. The epoch lets "Clear chat" start a fresh
        # session (epoch 0 keeps the legacy un-suffixed id for continuity).
        run_id = _new_run_id()
        log_file = _run_log_path(run_id)
        epoch = _graph_store.get_session_epoch(project_id, view)
        conv_id = f"projgraph:{project_id}:{view}" + (f":{epoch}" if epoch else "")
        session_id = None
        try:
            from common.session_service import get_or_create_chat_session
            session_id = get_or_create_chat_session(
                conversation_id=conv_id,
                title=f"{project.name} · {view} graph" + (f" #{epoch + 1}" if epoch else ""),
                workspace=project.workspace,
                agent_id=_ARCHITECT_AGENT_ID,
            )
        except Exception:
            session_id = None
        _open_run(run_id, _ARCHITECT_AGENT_ID, task_id=conv_id, session_id=session_id,
                  session_type="chat", message_origin="architect-chat", channel="chat",
                  workspace=project.workspace, title=user_message[:60], log_file=str(log_file))

        # Write the same chat-log scaffold every chat run uses (=== Message block,
        # user message, response section). The insights endpoint parses chat runs
        # from this log via _extract_chat_message_runs, which keys on these
        # headers — without the scaffold the run shows no process trace. The
        # ChatStreamCallback appends its markers onto the shared log_lines list
        # and rewrites the file, so the scaffold is preserved (mirrors
        # chat.runs.create_chat_run + chat.pipelines.run_chat_pipeline).
        import time as _time
        from agents.callbacks import write_log as _write_log, append_log as _append_log

        msg_id = run_id[:8]
        started = _iso()
        log_lines = [
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {_ARCHITECT_AGENT_ID}",
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

        reply, status, err = "", "completed", None
        sink = None
        callback = None
        provider = model = ""

        if _ensure_architect_agent():
            try:
                from agents.agent_factory import create_agent
                from agents.callbacks import ChatStreamCallback

                loop = asyncio.get_running_loop()
                callback = ChatStreamCallback(loop, queue, log_lines, log_file, session_id=session_id)

                # Building a large graph can mean dozens of node/edge tool calls in
                # one run, so give the architect plenty of headroom: no repetition
                # ceiling at all (0 = UNLIMITED_TOOL_REPEATS), and a high
                # max_iterations keeps a big build from being cut off mid-way (60
                # is the default ceiling).
                agent = await asyncio.to_thread(
                    create_agent, _ARCHITECT_AGENT_ID,
                    workspace=agent_workspace_path(project, root), streaming=True,
                    max_tool_repeats=0, max_iterations=400,
                )
                provider, model = agent.provider or "", agent.model or ""
                _update_run(run_id, {"provider": provider, "model": model})
                callback.bind_model(provider, model)
                await emit({"type": "agent", "agent_id": _ARCHITECT_AGENT_ID,
                            "provider": provider, "model": model})

                sink = GraphStreamSink(loop, queue, _graph_store, project_id, view)
                # Install the sink in the current context BEFORE create_task so it
                # propagates into the agent run (and the threads its sync tools use),
                # mirroring the artifact_sink pattern in the chat pipeline.
                token = graph_sink.set_handler(sink)
                task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))
                graph_sink.reset_handler(token)
                _register_run(project_id, view, task)

                # The callback/sink push token/tool/graph events straight onto
                # `queue`; the SSE relay drains it concurrently, so here we just
                # await the agent. The relay (and thus this run) is no longer tied
                # to the client connection — a disconnect can't interrupt it.
                try:
                    res = await task
                except asyncio.CancelledError:
                    # Stop button: keep whatever the agent already built and close
                    # the run cleanly (the worker itself is not cancelled).
                    status, reply = "stopped", (sink.summary() if sink and sink.touched else "Stopped.")
                    await emit({"type": "stopped"})
                    res = None
                if res is not None:
                    if getattr(res, "ok", False):
                        reply = _clean_agent_reply(str(res.agent_output))
                    else:
                        status, err = "failed", (getattr(res, "error", None) or "agent returned no output")
                        await emit(_error_event("agent", err))
            except Exception as e:  # noqa: BLE001
                status, err = "failed", str(e)
                await emit(_error_event("agent", err))
        else:
            status, err = "failed", "architect agent unavailable"
            await emit(_error_event("registry", err))

        # Always produce a result: prefer the agent's words, otherwise summarize
        # what it changed (so a run that e.g. only cleared the graph isn't silent).
        if not reply:
            reply = (sink.summary() if sink else "") or (
                f"Couldn't complete the request: {err}" if err
                else "I didn't change the graph — could you clarify what you'd like to build?")

        # Finalize the run record with the response + tool/usage process payload.
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
        # Close out the chat-log scaffold: the assistant reply, a summary marker
        # (token totals + tool count + duration the insights parser reads), and
        # the Finished/Status footer — matching the normal chat pipeline so this
        # run's process trace renders in the Messages insights view.
        finished = _iso()
        duration_ms = int((_time.perf_counter() - message_started) * 1000)
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

        _graph_store.append_message(project_id, view, "assistant", reply)
        await emit({"type": "message", "role": "assistant", "content": reply, "run_id": run_id})
        await emit({"type": "done", "run_id": run_id, "usage": usage})

        # Persist this turn's rich display trace (the user turn + every thinking /
        # tool / graph step + the reply) so a reload restores the full session.
        # Built from the recording queue, so it's captured even if the client
        # disconnected mid-run (the worker still runs to completion here).
        recorded = list(getattr(queue, "recorded", []))
        turn_items = [{"k": "user", "text": user_message}]
        for ev in recorded:
            if settle_tool_step(turn_items, ev):
                continue
            item = _trace_item_for(ev)
            if item:
                turn_items.append(item)
        try:
            _graph_store.append_trace(project_id, view, turn_items)
        except Exception:
            pass

    async def event_stream():
        queue = _RecordingQueue()
        yield _sse({"type": "meta", "view": view})
        # Run the agent + persistence detached from this connection, then relay.
        worker = _spawn_detached(_run_turn_guarded(run_turn, queue))
        async for frame in _relay_queue(queue):
            yield frame
        await worker  # connected client: surface a crash in the worker

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{project_id}/graph/chat/stop")
async def stop_project_graph_chat(project_id: str):
    """Stop any in-flight architect-chat / generate / planner run for the project.

    The agent runs detached from the SSE connection, so aborting the browser
    request can't stop it — this cancels the underlying agent task. Whatever the
    agent already built on the canvas is kept and the run closes cleanly.
    """
    if not store().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    cancelled = _cancel_runs(project_id)
    return {"stopped": cancelled > 0, "cancelled": cancelled}


async def _run_turn_guarded(run_turn, queue: asyncio.Queue):
    """Drive a detached run worker, always closing the relay with ``_STREAM_DONE``.

    The sentinel terminates the SSE relay even if the worker raises; if the
    client already disconnected it simply sits unread on the queue (harmless).
    """
    try:
        await run_turn(queue)
    except Exception as exc:  # noqa: BLE001 — last-resort so the stream still closes
        try:
            await queue.put({"type": "error", "source": "server", "error": str(exc)})
        except Exception:
            pass
    finally:
        await queue.put(_STREAM_DONE)


