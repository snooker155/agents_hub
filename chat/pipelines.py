"""
Chat execution pipelines.

Two async generators that drive a chat message to completion and yield SSE-style
event dicts:

- :func:`run_chat_pipeline` — a single YAML agent.
- :func:`run_chat_flow_pipeline` — a multi-agent flow, driven through the shared
  ``flow.engine`` and translated back into the per-node SSE event shapes the
  frontend expects.

Both are consumed by the ``/api/chat/stream`` SSE endpoint and directly by the
Telegram adapter, so they form the single execution path for chat.
"""
import asyncio
import time
from uuid import uuid4

from fastapi import HTTPException

from agents.callbacks import (
    ChatStreamCallback,
    append_log as _append_log,
    write_log as _write_log,
)
from agents.agent_factory import create_agent
from managers.run_manager import update_run
from chat.errors import error_code
from chat.models import ChatRequest
from common.session_service import get_or_create_chat_session
from common import artifact_sink, entity_sink as entity_sink_mod, stream_sink

from .context import (
    build_chat_context,
    build_history_lines,
    context_block_lines,
    resolve_workspace_abs,
    apply_workspace_ctx,
    project_scope_note,
)
from .attachments import materialize_attachments
from .references import resolve_references
from .runs import (
    utc_iso,
    get_pool_id,
    auto_journal,
    agent_overrides,
    validate_chat_request,
    load_flow_definition,
    create_chat_run,
)
from .broadcast import broadcast_turn
from .streaming import StreamDriveResult, drive_streaming_run


async def _run_chat_pipeline(request: ChatRequest):
    """
    Drive the full chat run lifecycle and yield events as dicts.

    Validates the request, materializes attachments, builds the prompt,
    records a run, executes the agent with streaming callbacks, and emits
    the same events that the SSE endpoint forwards to the browser. The
    Telegram adapter consumes the dict stream directly to assemble its
    own reply, so both surfaces share one execution path.

    Yielded event shapes:
    - {"type": "meta", "run_id", "session_id"}
    - callback events: token / thinking / tool_start / tool_end / tool_error / usage / error
    - {"type": "done", "ok", "response", "error", "run_id", "session_id", "usage", "tool_calls", "duration_ms", "entities"}
    """
    validate_chat_request(request)
    materialize_attachments(request)
    resolve_references(request)
    full_prompt, workspace_abs = build_chat_context(request)
    run_id, msg_id, log_file, log_lines, session_id = create_chat_run(request)

    apply_workspace_ctx(request, workspace_abs)

    # Visualization Studio binding: expose the active view to the mutation tools
    # and prepend a compact scene-context note so the agent knows what it edits.
    # Set before the agent task is created so it propagates into the run context.
    if request.view_id:
        from common.agent_context import current_view_id
        from views.studio import scene_context_note
        current_view_id.set(request.view_id)
        note = scene_context_note(request.view_id)
        if note:
            full_prompt = f"{note}\n\n---\n\n{full_prompt}"

    # Service entities this run's tools create, change or read — tasks, views,
    # flows, scheduled jobs, files. The reply carries a link to each (see
    # chat.streaming). The Studio-bound view is ignored: the user is already
    # looking at it, so re-linking it every turn is noise.
    entities_touched = entity_sink_mod.EntitySink(
        ignore=[("view", request.view_id)] if request.view_id else None)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    callback = ChatStreamCallback(loop, queue, log_lines, log_file,
                                  session_id=session_id, run_id=run_id)
    message_started = time.perf_counter()

    async def _run_agent_async():
        overrides = agent_overrides(request.agent_id)
        # Build off the event loop — create_agent is synchronous and heavyweight
        # (see chat.flow_driver), so running it inline blocks every concurrent
        # request until the agent is ready.
        agent = await asyncio.to_thread(
            create_agent, request.agent_id, workspace=workspace_abs, streaming=True, **overrides
        )
        # The model is only known here; the callback needs it to report how full
        # the context is with each usage event.
        callback.bind_model(agent.provider or "", agent.model or "")
        # Persist the model/provider actually used for this run so the message
        # record reflects what ran, not the current default at view time.
        update_run(run_id, {"provider": agent.provider or "", "model": agent.model or ""})
        return await agent.arun(full_prompt, callbacks=[callback])

    # Install the artifact recorder so filesystem tools report file changes as
    # diffs through this run's callback. create_task copies the current context,
    # so setting it here propagates into the agent execution (and any threads it
    # spawns via copy_context). Reset after the task is scheduled.
    _artifact_token = artifact_sink.set_recorder(callback.record_artifact)
    # Install the delegation stream emitter on the same context the task copies,
    # so a run_agent_tool delegation (running in the agent's worker thread) can
    # forward the child's nested tool/thought events onto this run's SSE stream.
    _stream_token = stream_sink.set_emitter(callback.emit_external)
    # Same context hand-off for the entity sink: the tools record into it from
    # the agent's worker thread, and the drive loop reads it after the run.
    _entity_token = entity_sink_mod.set_sink(entities_touched)
    task = asyncio.create_task(_run_agent_async())
    artifact_sink.reset_recorder(_artifact_token)
    stream_sink.reset_emitter(_stream_token)
    entity_sink_mod.reset_sink(_entity_token)

    yield {"type": "meta", "run_id": run_id, "session_id": session_id}

    drive = StreamDriveResult()
    try:
        async for event in drive_streaming_run(
            task=task, queue=queue, callback=callback, run_id=run_id,
            full_prompt=full_prompt, started_ts=message_started, result=drive,
            entity_sink=entities_touched,
        ):
            yield event

        finished = utc_iso()
        duration_ms = drive.duration_ms
        usage = drive.usage
        process_payload = drive.process_payload
        final_response = drive.response
        final_ok = drive.ok
        final_error = drive.error

        if drive.stopped:
            _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
            update_run(run_id, {
                "status": "stopped",
                "finished_at": finished,
                "exit_code": 1,
                "error": "stopped by user",
                "process": process_payload,
            })
            yield {
                "type": "done",
                "ok": False,
                "response": "Stopped by user",
                "error": "stopped by user",
                "run_id": run_id,
                "session_id": session_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
            return

        summary_line = (
            f"[message_summary] id={msg_id} "
            f"inbound_tokens={callback.prompt_tokens} "
            f"outbound_tokens={callback.completion_tokens} "
            f"total_tokens={callback.total_tokens} "
            f"tool_calls={callback.tool_calls} "
            f"duration_ms={duration_ms}"
        )

        if final_ok:
            _append_log(log_lines, final_response, log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : completed"])
            auto_journal(request.agent_id, get_pool_id(request.agent_id, request.workspace), request.message, final_response, run_id)
            # Resolve the structured response to its transport payload. An inline
            # view is persisted here and replaced with a lightweight view_ref, so
            # both the stored run record and the client carry the reference (never
            # the full spec). Non-view responses pass through as to_payload().
            structured_payload = None
            if drive.response_obj is not None:
                from views.publish import publish_structured_response
                structured_payload = publish_structured_response(
                    drive.response_obj, workspace=request.workspace,
                    run_id=run_id, task_id=None,
                )
                if isinstance(process_payload.get("response"), dict):
                    process_payload["response"]["structured"] = structured_payload
            update_run(run_id, {
                "status": "completed",
                "finished_at": finished,
                "exit_code": 0,
                "process": process_payload,
            })
            done_event = {
                "type": "done",
                "ok": True,
                "response": final_response,
                "run_id": run_id,
                "session_id": session_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
            # Structured response (buttons / Telegram keyboard / view_ref / …)
            # travels next to the plain text; surfaces that don't understand it
            # ignore it.
            if structured_payload is not None:
                done_event["response_obj"] = structured_payload
            # Links to the service entities the run touched. The web chat renders
            # them under the reply; text-only surfaces (Telegram) append them to
            # the text via ``common.entity_links.append_entity_links``.
            if drive.entities:
                done_event["entities"] = drive.entities
            yield done_event
        else:
            err = final_error or "unknown error"
            _append_log(log_lines, f"(error: {err})", log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : failed"])
            update_run(run_id, {
                "status": "failed",
                "finished_at": finished,
                "exit_code": 1,
                "error": err,
                "process": process_payload,
            })
            done_event = {
                "type": "done",
                "ok": False,
                "response": f"Error: {err}",
                "error": err,
                "run_id": run_id,
                "session_id": session_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
            # A conversation that outgrew the model is not a generic failure:
            # the user can only get out of it by clearing the chat or moving to
            # a bigger model, so say which case this is rather than handing the
            # UI a provider string to render in red.
            code = error_code(err)
            if code:
                done_event["error_code"] = code
            yield done_event
    except asyncio.CancelledError:
        callback.cancelled = True
        finished = utc_iso()
        _write_log(log_file, log_lines + ["(cancelled)", "", f"Finished: {finished}", "Status  : stopped"])
        update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "cancelled"})
        raise
    except Exception as e:
        finished = utc_iso()
        _write_log(log_file, log_lines + [f"(stream error: {e})", "", f"Finished: {finished}", "Status  : failed"])
        update_run(run_id, {"status": "failed", "finished_at": finished, "exit_code": 1, "error": str(e)})
        yield {"type": "done", "ok": False, "response": f"Error: {e}", "error": str(e), "run_id": run_id}


async def _run_chat_flow_pipeline(request: ChatRequest):
    """
    Drive a multi-agent flow conversation in-process and yield SSE events.

    The DAG walk (ordering, branch pruning, FlowState, entity dispatch) runs
    through the shared ``flow.engine``; this adapter supplies the chat-specific
    edges (prompt building with history/attachments, per-node run records +
    streaming via ``drive_streaming_run``, flow-log writes) and translates the
    engine's neutral events into the per-node SSE shapes the frontend expects.

    Yielded events extend the single-agent shape with per-node markers:
    - {"type": "flow_meta", "flow_id", "flow_name", "session_id", "nodes": [{node_id, agent_id, label}]}
    - {"type": "node_start", "node_id", "agent_id", "agent_label", "run_id"}
    - token / thinking / tool_start / tool_end / tool_error / usage / error events
      (each carries node_id / agent_id so the frontend can route them to the right bubble)
    - {"type": "node_done", "node_id", "run_id", "ok", "response", "error", "usage", "tool_calls", "duration_ms", "entities"}
    - {"type": "done", "ok", "flow_id", "session_id", "run_id" (last node), "responses": [{node_id, response}]}
    """
    # Local imports keep the surface narrow and avoid circulars at module load.
    from flow import run_store
    from flow.engine import run_flow_engine
    from flow.validate import FlowValidationError
    from chat.flow_driver import build_chat_driver

    if not request.flow_id:
        raise HTTPException(status_code=400, detail="flow_id is required for flow chat")

    materialize_attachments(request)
    resolve_references(request)
    flow = load_flow_definition(request.flow_id)

    # Resolve workspace and publish it to agent tools via the context var.
    workspace_abs = resolve_workspace_abs(request)
    apply_workspace_ctx(request, workspace_abs)

    # One session per conversation, same as agent chat.
    conv_id = request.conversation_id or str(uuid4())
    flow_name = flow.get("name") or request.flow_id
    # History records are titled by the conversation's first message (truncated),
    # matching agent chat (see chat/runs.py). The flow name is only a fallback for
    # an empty message. The first turn's title sticks for the whole conversation
    # (the /runs grouper keeps the earliest non-empty title).
    msg_title = (request.message or "").strip().replace("\n", " ")
    run_title = msg_title[:60] + ("…" if len(msg_title) > 60 else "")
    session_title = request.conversation_title or run_title or f"Flow: {flow_name}"
    try:
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=session_title,
            workspace=request.workspace,
            agent_id=f"flow:{request.flow_id}",
        )
    except Exception:
        session_id = None

    # Shared history / attached-context blocks — same builders as
    # build_chat_context. Only root nodes (no predecessor output) receive the
    # history + latest user message; downstream nodes work from predecessor
    # output (handled by the engine's build_agent_input, which the chat builder
    # below wraps).
    history_lines = build_history_lines(request.history)
    context_lines = context_block_lines(request)
    user_message = request.message
    # Project scope preamble (same text as single-agent chat); prepended to root
    # node prompts so flow agents also know they operate inside the project.
    proj_note = project_scope_note(request)

    # All turns of one flow chat conversation collapse into a single History
    # record (run_group=conv_id, kind="chat" vs kind="task" from runtime/flow_run.py).
    # The conversation id is also the per-run log filename
    # (flow_logs/<flow_id>/<conv_id>.json). Tagging is shared via run_store.
    def _log_flow(payload: dict) -> None:
        run_store.log_flow_event(request.flow_id, conv_id, payload, kind="chat")

    overall_started = time.perf_counter()
    responses: list[dict] = []
    flow_done = False  # set once flow_finish is emitted, so the CancelledError
    # handler doesn't double-log a flow_stopped after a clean finish.

    # The chat-surface driver supplies prompt building, streaming agent execution,
    # per-node run records, and flow-log writes (see chat.flow_driver). ``state``
    # holds the per-node bookkeeping (node_meta / last_run_id) the translation
    # loop below reads while turning neutral engine events into SSE shapes.
    driver, state = build_chat_driver(
        request=request, flow=flow, conv_id=conv_id, session_id=session_id,
        flow_name=flow_name, session_title=session_title, workspace_abs=workspace_abs,
        history_lines=history_lines, context_lines=context_lines,
        user_message=user_message, log_flow=_log_flow, project_note=proj_note,
    )
    node_meta = state.node_meta

    # ── Drive the shared engine, translating neutral events → SSE shapes ──────
    try:
        engine = run_flow_engine(flow, flow_id=request.flow_id, shared_context=user_message, driver=driver)
        async for ev in engine:
            kind = ev.get("type")
            if kind == "flow_start":
                # NB: the flow_start flow-log event is written by the driver's
                # _on_flow_start hook (it carries the initial state snapshot), so
                # we only translate to the SSE flow_meta shape here.
                yield {
                    "type": "flow_meta",
                    "flow_id": request.flow_id,
                    "flow_name": flow_name,
                    "session_id": session_id,
                    "nodes": [
                        {"node_id": n["node_id"], "agent_id": n["entity_id"], "agent_label": n["label"]}
                        for n in ev.get("nodes", [])
                    ],
                    # Initial shared-state snapshot so the editor's Runtime State
                    # block can show seed values before any node finishes.
                    "state": ev.get("state"),
                }
            elif kind == "node_skip":
                yield {"type": "node_skip", "node_id": ev["node_id"],
                       "session_id": session_id, "reason": ev.get("reason")}
            elif kind == "node_start":
                meta = node_meta.get(ev["node_id"], {})
                run_id = meta.get("run_id")
                yield {
                    "type": "node_start", "node_id": ev["node_id"],
                    "agent_id": ev.get("entity_id"), "agent_label": ev.get("label"),
                    "run_id": run_id, "session_id": session_id,
                }
                # Legacy "meta" anchor for older clients (agent nodes only).
                if run_id:
                    yield {"type": "meta", "run_id": run_id, "session_id": session_id,
                           "node_id": ev["node_id"]}
            elif kind == "node_event":
                inner = ev.get("event") or {}
                # Tag the raw callback event with its node so the UI routes it.
                meta = node_meta.get(ev["node_id"], {})
                yield {**inner, "node_id": ev["node_id"], "agent_id": meta.get("agent_id")}
            elif kind == "node_done":
                meta = node_meta.get(ev["node_id"], {})
                resp = ("Stopped by user" if ev.get("stopped")
                        else ev.get("output") if ev.get("ok")
                        else f"Error: {ev.get('error') or 'unknown error'}")
                if ev.get("ok") and not ev.get("stopped"):
                    responses.append({
                        "node_id": ev["node_id"], "agent_id": ev.get("entity_id"),
                        "agent_label": ev.get("label"), "response": ev.get("output", ""),
                    })
                elif not ev.get("stopped"):
                    responses.append({
                        "node_id": ev["node_id"], "agent_id": ev.get("entity_id"),
                        "agent_label": ev.get("label"), "response": resp,
                        "error": ev.get("error"),
                    })
                yield {
                    "type": "node_done", "node_id": ev["node_id"],
                    "agent_id": ev.get("entity_id"), "agent_label": ev.get("label"),
                    "run_id": ev.get("run_id"), "ok": ev.get("ok"),
                    "response": resp, "error": ev.get("error"),
                    "usage": meta.get("usage", {}),
                    "tool_calls": meta.get("tool_calls", 0),
                    "duration_ms": ev.get("duration_ms", 0),
                    # Links to the service entities this node touched.
                    "entities": meta.get("entities") or [],
                    # Shared-state snapshot after this node's writes, for the
                    # editor's live Runtime State block.
                    "state": ev.get("state"),
                }
            elif kind == "flow_finish":
                total_duration_ms = int((time.perf_counter() - overall_started) * 1000)
                _log_flow({
                    "timestamp": utc_iso(),
                    "type": "flow_stopped" if ev.get("any_failure") else "flow_finish",
                    "content": f"Flow '{flow_name}' " + ("stopped" if ev.get("any_failure") else "finished"),
                    "status": "stopped" if ev.get("any_failure") else "completed",
                })
                flow_done = True
                yield {
                    "type": "done", "ok": ev.get("ok"),
                    "flow_id": request.flow_id, "flow_name": flow_name,
                    "session_id": session_id, "run_id": state.last_run_id,
                    "responses": responses, "duration_ms": total_duration_ms,
                }
    except asyncio.CancelledError:
        # The SSE client disconnected / pressed Stop mid-run, so the engine never
        # reached flow_finish. Finalize the History record as stopped here (the
        # per-node agent_stopped event is written by the chat driver), then
        # re-raise so the cancellation propagates normally.
        if not flow_done:
            _log_flow({
                "timestamp": utc_iso(), "type": "flow_stopped",
                "content": f"Flow '{flow_name}' stopped", "status": "stopped",
            })
        raise
    except FlowValidationError as e:
        yield {
            "type": "done", "ok": False, "flow_id": request.flow_id,
            "flow_name": flow_name, "session_id": session_id,
            "error": "; ".join(e.errors), "responses": responses,
        }


async def _run_chat_team_pipeline(request: ChatRequest):
    """
    Hand a chat message to a team and stream the conversation it produces.

    A team run is agents talking to each other, so what streams here is the
    board — every message, tagged with who said it and who it was for — rather
    than one agent's tokens. The run itself is the same ``teams.runner.run_team``
    the Teams page and a task-attached run use; only the delivery differs, which
    is why a team reached from chat behaves identically to one started anywhere
    else.

    The runner is synchronous and long-running, so it goes on a worker thread
    and posts each board entry into an asyncio queue as it lands.

    Yielded event shapes:
    - {"type": "team_meta", "team_id", "team_name", "mode", "session_id", "members": [...]}
    - {"type": "team_message", "sender", "recipients", "kind", "round", "content", "run_id", ...}
    - {"type": "done", "ok", "response", "team_run_id", "session_id", "duration_ms"}
    """
    from teams import store as team_store
    from teams.runner import run_team, stop_run as stop_team_run

    if not request.team_id:
        raise HTTPException(status_code=400, detail="team_id is required for team chat")
    team = team_store.get_team(request.team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{request.team_id}' not found")
    if not team.members:
        raise HTTPException(status_code=400, detail="Team has no members")

    materialize_attachments(request)
    resolve_references(request)
    workspace_abs = resolve_workspace_abs(request)
    apply_workspace_ctx(request, workspace_abs)

    conv_id = request.conversation_id or str(uuid4())
    msg_title = (request.message or "").strip().replace("\n", " ")
    run_title = msg_title[:60] + ("…" if len(msg_title) > 60 else "")
    try:
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=request.conversation_title or run_title or f"Team: {team.name}",
            workspace=request.workspace,
            agent_id=f"team:{request.team_id}",
        )
    except Exception:
        session_id = None

    # The goal carries the conversation, not just the last line: a team asked to
    # "make that shorter" needs to know what "that" was.
    parts = list(build_history_lines(request.history))
    goal_parts = (["## Conversation so far", *parts, ""] if parts else [])
    goal_parts += ["## The request to work on", request.message]
    goal_parts += context_block_lines(request)
    goal = "\n".join(goal_parts)

    yield {
        "type": "team_meta",
        "team_id": team.team_id, "team_name": team.name, "mode": team.mode,
        "session_id": session_id, "conversation_id": conv_id,
        "members": [
            {"agent_id": m.agent_id, "name": m.display_name(), "role": m.role,
             "manifest": m.manifest}
            for m in team.members
        ],
        "leader": team.leader_display_name() if team.leader_agent_id else None,
    }

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    started = time.perf_counter()

    def _on_message(msg) -> None:
        # Called on the runner's worker thread (and on its member threads), so
        # the queue must be fed through the loop rather than touched directly.
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "team_message", **msg.to_dict()})

    task = asyncio.create_task(asyncio.to_thread(
        run_team, request.team_id, goal,
        workspace=request.workspace, conversation_id=conv_id,
        on_message=_on_message,
    ))

    # Drain the board until the run finishes, then flush whatever landed between
    # the last drain and the runner returning.
    team_run_id = ""
    while not task.done() or not queue.empty():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            # The client disconnected or pressed Stop. Cancelling the task does
            # not reach into the worker thread, so stop the run itself — which
            # interrupts the member turns in flight — rather than leaving a team
            # talking to nobody at full cost.
            if team_run_id:
                stop_team_run(team_run_id)
            task.cancel()
            raise
        team_run_id = event.get("team_run_id") or team_run_id
        yield event

    duration_ms = int((time.perf_counter() - started) * 1000)
    try:
        run = task.result()
    except Exception as e:  # noqa: BLE001
        yield {
            "type": "done", "ok": False, "response": f"Error: {e}", "error": str(e),
            "team_id": request.team_id, "session_id": session_id,
            "duration_ms": duration_ms,
        }
        return

    yield {
        "type": "done",
        "ok": run.status == "completed",
        "response": run.result or "(the team produced no answer)",
        "error": run.error,
        "team_id": request.team_id, "team_run_id": run.team_run_id,
        "session_id": session_id, "rounds": run.rounds_done,
        "stop_reason": run.stop_reason, "total_cost": run.total_cost,
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
# Every consumer — the web routes, the Telegram adapter, an instance delivery,
# the CLI — goes through these, so a turn started anywhere is visible to anyone
# with that conversation open. The wrapper only passes events along on their way
# out (see chat.broadcast); the generators above are unchanged by it.

async def run_chat_pipeline(request: ChatRequest):
    """One agent, streamed to its caller and to the conversation's channel."""
    async for event in broadcast_turn(request, _run_chat_pipeline(request)):
        yield event


async def run_chat_flow_pipeline(request: ChatRequest):
    """A flow's DAG, same treatment: one bubble per node, seen by every viewer."""
    async for event in broadcast_turn(request, _run_chat_flow_pipeline(request)):
        yield event


async def run_chat_team_pipeline(request: ChatRequest):
    """A team's conversation, streamed as it is said."""
    async for event in broadcast_turn(request, _run_chat_team_pipeline(request)):
        yield event
