"""
Chat execution pipelines.

Two async generators that drive a chat message to completion and yield SSE-style
event dicts:

- :func:`run_chat_pipeline` — a single YAML agent, followed in the same turn by
  the agent it hands the conversation to, if it does (chat/handoff.py).
- :func:`run_chat_flow_pipeline` — a multi-agent flow, driven through the shared
  ``flow.engine`` and translated back into the per-node SSE event shapes the
  frontend expects.

Both are consumed by the ``/api/chat/stream`` SSE endpoint and directly by the
Telegram adapter, so they form the single execution path for chat.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
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
from common import artifact_sink, citation_sink as citation_sink_mod, entity_sink as entity_sink_mod, stream_sink

from .compaction import compact_for_turn, compaction_event
from .context import (
    build_chat_context,
    build_history_lines,
    build_history_messages,
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
    turn_overrides,
    validate_chat_request,
    load_flow_definition,
    create_chat_run,
)
from . import handoff as handoff_mod
from .broadcast import broadcast_turn
from .streaming import StreamDriveResult, drive_streaming_run

log = logging.getLogger(__name__)


def _run_id_kwargs(agent, run_id: str) -> dict:
    """``{"run_id": run_id}`` for an agent whose ``arun`` takes it (every hub
    agent does, through ``**kwargs``); nothing for one with a narrower
    signature, which is then called exactly as before."""
    import inspect
    try:
        params = inspect.signature(agent.arun).parameters
    except (TypeError, ValueError):
        return {}
    if "run_id" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return {"run_id": run_id}
    return {}


def _settle_steering(run_id: str) -> list:
    """The steering messages this turn ended without taking, marked expired
    and returned as ``[{msg_id, body}]`` for the ``done`` event: a chat keeps
    queue semantics, so the client sends them as its next turn rather than
    losing them (common/steering.py)."""
    try:
        from common import steering
        return [{"msg_id": m["msg_id"], "body": m["body"]} for m in steering.mark_expired(run_id)]
    except Exception:  # noqa: BLE001 - a steering lookup failing must not break closing the turn
        return []


def _settle_turn(run_ids: list) -> dict:
    """``{delivered, undelivered}`` for a turn made of several runs (a flow's
    nodes, a team), only the non-empty lists; see common.steering.settle_turn."""
    try:
        from common import steering
        settled = steering.settle_turn([r for r in run_ids if r])
    except Exception:  # noqa: BLE001 - a steering lookup failing must not break closing the turn
        return {}
    return {k: v for k, v in settled.items() if v}


@dataclass
class _AgentStep:
    """One agent's run inside a chat turn.

    A turn is usually one step. When the agent hands the conversation over
    (tools/handoff.py), the agent it names runs as the next step of the same
    turn, with its own run record, and so on up to the turn's handoff limit.
    """
    #: The turn's request with ``agent_id`` set to this step's agent.
    request: ChatRequest
    #: The human message this run sends: the user's message with its context
    #: blocks, behind a handoff note for a receiving run.
    prompt: str
    #: The conversation before this turn, as chat messages, before compaction.
    history: list
    workspace_abs: Optional[str]
    ws_name: Optional[str]
    #: What ``create_chat_run`` returned: (run_id, msg_id, log_file, log_lines, session_id).
    run: tuple
    #: Agents that held the turn so far, this one last.
    chain: list = field(default_factory=list)
    #: The handoff that started this step, None for the first agent.
    received: Optional[handoff_mod.HandoffIntent] = None


@dataclass
class _StepOutcome:
    """What one step left for the turn: the ``done`` event it would end the
    turn with, and the handoff it recorded when it handed the conversation on."""
    done: dict = field(default_factory=dict)
    intent: Optional[handoff_mod.HandoffIntent] = None
    response: str = ""
    usage: dict = field(default_factory=dict)
    tool_calls: int = 0
    duration_ms: int = 0


def _with_view_note(request: ChatRequest, prompt: str) -> str:
    """Visualization Studio binding: expose the active view to the mutation
    tools and put a compact scene-context note in front of the prompt, so the
    agent knows what it edits. Set before the agent task is created so it
    propagates into the run context."""
    if not request.view_id:
        return prompt
    from common.agent_context import current_view_id
    from views.studio import scene_context_note
    current_view_id.set(request.view_id)
    note = scene_context_note(request.view_id)
    return f"{note}\n\n---\n\n{prompt}" if note else prompt


async def _run_agent_step(step: _AgentStep, outcome: _StepOutcome):
    """Run one agent of a chat turn and yield its events, except the last.

    Builds the agent, compacts its history, executes it with streaming
    callbacks and closes its run record: the same lifecycle every chat turn had
    before a turn could hold more than one agent. The ``done`` event this run
    would end the turn with goes on *outcome* instead of the stream, because
    only the turn knows whether this run was its last one; a handoff the agent
    recorded goes there too.
    """
    request = step.request
    full_prompt = step.prompt
    workspace_abs = step.workspace_abs
    run_id, msg_id, log_file, log_lines, session_id = step.run

    # Service entities this run's tools create, change or read — tasks, views,
    # flows, scheduled jobs, files. The reply carries a link to each (see
    # chat.streaming). The Studio-bound view is ignored: the user is already
    # looking at it, so re-linking it every turn is noise.
    entities_touched = entity_sink_mod.EntitySink(
        ignore=[("view", request.view_id)] if request.view_id else None)
    # Where the handoff tool learns about the turn (who held it, how many
    # handoffs it made) and records the agent's decision (chat/handoff.py).
    handoff_sink = handoff_mod.HandoffSink(
        agent_id=request.agent_id or "", chain=list(step.chain),
        depth=len(step.chain) - 1, max_depth=handoff_mod.max_depth(),
        workspace=step.ws_name,
    )

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    callback = ChatStreamCallback(loop, queue, log_lines, log_file,
                                  session_id=session_id, run_id=run_id)
    message_started = time.perf_counter()

    async def _run_agent_async():
        overrides = {**agent_overrides(request.agent_id), **turn_overrides()}
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
        # A summary handed on with the conversation is written by this agent's
        # own model (chat.handoff.summarize_for_handoff).
        handoff_sink.summarizer = agent
        history = handoff_mod.history_for(agent, step.history, step.received)
        # A receiving run with a narrowed history must not get the whole
        # conversation back through the session's stored summary.
        compaction_session = (session_id if step.received is None
                              or handoff_mod.uses_session_summary(step.received.history_filter)
                              else None)

        async def _compact(force: bool):
            # Summarising is an LLM call of its own; keep it off the event loop
            # the way the agent build already is.
            compaction = await asyncio.to_thread(
                compact_for_turn,
                agent=agent, history=history,
                system_prompt=getattr(agent, "system_prompt", "") or "",
                session_id=compaction_session, force=force,
            )
            if compaction.folded:
                # The UI has nothing for this event yet; it is emitted now so the
                # turn that folded a conversation is visible when it does.
                callback.emit_external(compaction_event(compaction))
            handoff_sink.set_conversation(compaction)
            return compaction

        compaction = await _compact(False)
        # The run id reaches the agent loop so a message the user sends while
        # this turn works (POST /api/runs/{id}/steer) is taken before the
        # model's next step (agents/loop_ext/steering.py).
        run_kwargs = _run_id_kwargs(agent, run_id)
        result = await agent.arun(full_prompt, history=compaction.messages,
                                  callbacks=[callback], **run_kwargs)

        # The provider is the last word on what fits: when it says the turn was
        # too long anyway, fold the history and run the turn once more rather
        # than handing the user an error they can only answer by clearing the chat.
        if not result.ok and error_code(result.error or ""):
            retry = await _compact(True)
            if retry.folded:
                result = await agent.arun(full_prompt, history=retry.messages,
                                          callbacks=[callback], **run_kwargs)
        return result

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
    # Passages a retrieval tool shows the model, numbered for [n] citations.
    citations = citation_sink_mod.CitationSink()
    _citation_token = citation_sink_mod.set_sink(citations)
    _handoff_token = handoff_mod.set_sink(handoff_sink)
    # A view the agent creates becomes the default target of its view tools
    # (tools/views.py), for this run only.
    from common.agent_context import current_view_binding
    _view_binding_token = current_view_binding.set({})
    task = asyncio.create_task(_run_agent_async())
    current_view_binding.reset(_view_binding_token)
    artifact_sink.reset_recorder(_artifact_token)
    stream_sink.reset_emitter(_stream_token)
    entity_sink_mod.reset_sink(_entity_token)
    citation_sink_mod.reset_sink(_citation_token)
    handoff_mod.reset_sink(_handoff_token)

    yield {"type": "meta", "run_id": run_id, "session_id": session_id,
           "agent_id": request.agent_id}

    drive = StreamDriveResult()
    try:
        async for event in drive_streaming_run(
            task=task, queue=queue, callback=callback, run_id=run_id,
            full_prompt=full_prompt, started_ts=message_started, result=drive,
            entity_sink=entities_touched,
            citation_sink=citations,
        ):
            yield event

        # Settled before the run's status changes, so nothing reading the
        # run as finished can expire these first: the client gets them back
        # and sends them as its next turn.
        undelivered = _settle_steering(run_id)
        loop_extra = {"loop": drive.loop} if drive.loop else {}

        finished = utc_iso()
        duration_ms = drive.duration_ms
        usage = drive.usage
        process_payload = drive.process_payload
        final_response = drive.response
        final_ok = drive.ok
        final_error = drive.error
        outcome.usage = usage
        outcome.tool_calls = callback.tool_calls
        outcome.duration_ms = duration_ms

        if drive.stopped:
            _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
            update_run(run_id, {
                "status": "stopped",
                "finished_at": finished,
                "exit_code": 1,
                "error": "stopped by user",
                "process": process_payload,
                **loop_extra,
            })
            done_event = {
                "type": "done",
                "ok": False,
                "response": "Stopped by user",
                "error": "stopped by user",
                "run_id": run_id,
                "session_id": session_id,
                "agent_id": request.agent_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
            if undelivered:
                done_event["undelivered"] = undelivered
            outcome.done = done_event
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
            intent = handoff_sink.intent
            if intent is not None:
                # The run is complete: its answer is what it told the user
                # before giving the conversation away. The turn goes on with
                # the agent it named (see _run_chat_pipeline).
                _append_log(log_lines, f"[handoff] to={intent.to_agent_id} "
                                       f"history={intent.history_filter} reason={intent.reason}", log_file)
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
                **loop_extra,
            })
            done_event = {
                "type": "done",
                "ok": True,
                "response": final_response,
                "run_id": run_id,
                "session_id": session_id,
                "agent_id": request.agent_id,
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
            # Sources the reply cites as [n]; the chat renders them under it.
            if drive.citations:
                done_event["citations"] = drive.citations
            if undelivered:
                done_event["undelivered"] = undelivered
            outcome.done = done_event
            outcome.intent = intent
            outcome.response = final_response
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
                **loop_extra,
            })
            done_event = {
                "type": "done",
                "ok": False,
                "response": f"Error: {err}",
                "error": err,
                "run_id": run_id,
                "session_id": session_id,
                "agent_id": request.agent_id,
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
            if drive.budget:
                done_event["error_code"] = "budget"
                done_event["budget"] = drive.budget
            if undelivered:
                done_event["undelivered"] = undelivered
            outcome.done = done_event
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
        outcome.done = {"type": "done", "ok": False, "response": f"Error: {e}", "error": str(e),
                        "run_id": run_id, "agent_id": request.agent_id}


def _open_receiving_step(step: _AgentStep, intent: handoff_mod.HandoffIntent,
                         replies: list) -> _AgentStep:
    """The next step of a turn whose agent handed the conversation over.

    Same user message, attachments, references, workspace and conversation;
    the receiving agent's own run record (``parent_run_id`` is the handing
    run), the handoff note in front of the message, and the history as the
    handoff's filter shapes it (applied once the agent is built).
    """
    receiving, prompt, run = handoff_mod.open_receiving_run(
        step.request, intent, handing_run_id=step.run[0])
    note = handoff_mod.handoff_note(intent, replies=replies)
    return _AgentStep(
        request=receiving,
        prompt=handoff_mod.receiving_prompt(note, _with_view_note(receiving, prompt)),
        history=step.history,
        workspace_abs=step.workspace_abs,
        ws_name=step.ws_name,
        run=run,
        chain=[*step.chain, intent.to_agent_id],
        received=intent,
    )


def _close_unstarted(step: _AgentStep, *, status: str, error: str) -> dict:
    """Close a receiving run that never started (a stop, a budget cap) and
    return the ``done`` event that ends the turn on it."""
    run_id, _msg_id, log_file, log_lines, session_id = step.run
    finished = utc_iso()
    _write_log(log_file, log_lines + [f"({error})", "", f"Finished: {finished}", f"Status  : {status}"])
    update_run(run_id, {"status": status, "finished_at": finished, "exit_code": 1, "error": error})
    return {
        "type": "done", "ok": False,
        "response": "Stopped by user" if status == "stopped" else f"Error: {error}",
        "error": error, "run_id": run_id, "session_id": session_id,
        "agent_id": step.request.agent_id,
    }


async def _run_chat_pipeline(request: ChatRequest):
    """
    Drive the full chat run lifecycle and yield events as dicts.

    Validates the request, materializes attachments, builds the prompt,
    records a run, executes the agent with streaming callbacks, and emits
    the same events that the SSE endpoint forwards to the browser. The
    Telegram adapter consumes the dict stream directly to assemble its
    own reply, so both surfaces share one execution path.

    A turn may hold several agents: when the agent hands the conversation over
    (tools/handoff.py), the agent it names answers in the same turn, as a run
    of its own, and may hand over again up to the turn's limit
    (``AGENTS_HUB_HANDOFF_MAX_DEPTH``), never back to an agent that already held
    it. The stream stays one turn: one final ``done``.

    Yielded event shapes:
    - {"type": "meta", "run_id", "session_id", "agent_id"} at the start of every run
    - callback events: token / thinking / tool_start / tool_end / tool_error / usage / error
    - {"type": "compaction", "folded", "summary_chars", ...} when the conversation
      was folded into a summary before the turn ran
    - {"type": "steer_delivered", "msg_id", "after_step", "run_id"} when a message the
      user sent while the turn worked reached the model (agents/loop_ext/steering.py)
    - {"type": "handoff", "from_agent_id", "from_agent_name", "to_agent_id",
      "to_agent_name", "reason", "history_filter", "run_id", "next_run_id",
      "from_response", "usage", "tool_calls", "duration_ms"} between the handing
      run and the receiving run's ``meta``
    - {"type": "done", "ok", "response", "error", "run_id", "session_id", "agent_id",
      "usage", "tool_calls", "duration_ms", "entities", "undelivered"} once, at the end;
      ``agent_id`` is the agent that answered, and after a handoff ``handoff`` is
      the last handoff event's fields and ``handoffs`` all of them. ``undelivered``
      lists the steering messages the turn ended without taking, which the client
      sends as its next turn
    """
    validate_chat_request(request)
    materialize_attachments(request)
    resolve_references(request)
    full_prompt, workspace_abs = build_chat_context(request)
    # Prior turns travel as messages, not as text in front of this one. Built
    # uncapped: the flat HISTORY_CHAR_BUDGET cut used to run here, before the
    # agent (and therefore its model) was even known, and by the time
    # compact_for_turn saw the history it had already been truncated, so a
    # large-window model's conversation was thrown away rather than folded
    # into a summary. compact_for_turn below now does that bounding itself,
    # against the running agent's real budget, so the fold gets a chance.
    history_messages = build_history_messages(request.history, budget_chars=float("inf"))
    first_run = create_chat_run(request)

    ws_name = apply_workspace_ctx(request, workspace_abs)
    full_prompt = _with_view_note(request, full_prompt)

    step = _AgentStep(
        request=request, prompt=full_prompt, history=history_messages,
        workspace_abs=workspace_abs, ws_name=ws_name, run=first_run,
        chain=[request.agent_id],
    )
    handoffs: list = []
    replies: list = []
    undelivered: list = []
    # A receiving run opened but not started yet: a disconnect in that gap
    # must still close it.
    unstarted: Optional[_AgentStep] = None
    try:
        while True:
            outcome = _StepOutcome()
            async for event in _run_agent_step(step, outcome):
                yield event
            undelivered.extend(outcome.done.pop("undelivered", None) or [])

            intent = outcome.intent
            refused = handoff_mod.refusal_by_turn(step.chain, intent) if intent is not None else None
            if refused:
                log.warning("chat turn: handoff from %s not carried out: %s", step.request.agent_id, refused)
            if intent is None or refused:
                done = outcome.done
                if undelivered:
                    done["undelivered"] = undelivered
                if handoffs:
                    done["handoff"] = handoff_mod.done_fields(handoffs[-1])
                    done["handoffs"] = [handoff_mod.done_fields(h) for h in handoffs]
                yield done
                return

            # The conversation changes hands. The receiving run is opened first
            # so the handoff event can name it, then the turn goes on with it.
            handing_run_id = step.run[0]
            replies.append({"agent_name": intent.from_agent_name, "text": outcome.response})
            next_step = _open_receiving_step(step, intent, replies)
            unstarted = next_step
            next_run_id = next_step.run[0]
            update_run(handing_run_id, {"handoff": handoff_mod.handing_record(intent, next_run_id)})
            event = handoff_mod.handoff_event(
                intent, run_id=handing_run_id, next_run_id=next_run_id,
                from_response=outcome.response, session_id=step.run[4],
                usage=outcome.usage, tool_calls=outcome.tool_calls,
                duration_ms=outcome.duration_ms,
            )
            handoffs.append(event)
            yield event

            # Each run of the turn spends on its own: the workspace's hard cap
            # is checked before the receiving agent starts, as for any run.
            from common.budget import BudgetExceededError, check_budget
            closing = None
            try:
                check_budget(ws_name)
            except BudgetExceededError as e:
                closing = _close_unstarted(next_step, status="failed", error=str(e))
            if closing is None:
                from managers.run_manager import get_run_by_id
                if (get_run_by_id(next_run_id) or {}).get("status") in ("stop", "stopped"):
                    closing = _close_unstarted(next_step, status="stopped", error="stopped by user")
            if closing is not None:
                unstarted = None
                if undelivered:
                    closing["undelivered"] = undelivered
                closing["handoff"] = handoff_mod.done_fields(handoffs[-1])
                closing["handoffs"] = [handoff_mod.done_fields(h) for h in handoffs]
                yield closing
                return
            unstarted = None
            step = next_step
    except asyncio.CancelledError:
        if unstarted is not None:
            _close_unstarted(unstarted, status="stopped", error="cancelled")
        raise


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
    # below wraps). The history goes to a root node as messages, the same way
    # single-agent chat sends it.
    history_messages = build_history_messages(request.history)
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
        history_messages=history_messages, context_lines=context_lines,
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
                    "citations": meta.get("citations") or [],
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
                done_event = {
                    "type": "done", "ok": ev.get("ok"),
                    "flow_id": request.flow_id, "flow_name": flow_name,
                    "session_id": session_id, "run_id": state.last_run_id,
                    "responses": responses, "duration_ms": total_duration_ms,
                }
                done_event.update(_settle_turn([m.get("run_id") for m in node_meta.values()]))
                yield done_event
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
        **_settle_turn([run.team_run_id]),
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
# Every consumer — the web routes, the Telegram adapter, an instance delivery,
# the CLI — goes through these, so a turn started anywhere is visible to anyone
# with that conversation open. The wrapper only passes events along on their way
# out (see chat.broadcast); the generators above are unchanged by it.

#
# Where the turn runs (chat/routing.py, docs/services.md): with chat execution
# on ``instances`` (the default) the three public pipelines hand the turn to a
# service replica and relay its events; ``execute_locally`` is the turn in
# this process, what the replica itself runs (chat/turns.py), and what every
# process runs with chat execution on ``inprocess``.

def _local_pipeline(request: ChatRequest, kind: str):
    if kind == "team":
        return _run_chat_team_pipeline(request)
    if kind == "flow":
        return _run_chat_flow_pipeline(request)
    return _run_chat_pipeline(request)


def kind_of(request: ChatRequest) -> str:
    """Which pipeline a request is for: ``team``, ``flow`` or ``agent``."""
    if request.team_id:
        return "team"
    if request.flow_id:
        return "flow"
    return "agent"


async def execute_locally(request: ChatRequest, kind: Optional[str] = None):
    """The turn in this process, whatever chat execution says, published to
    the conversation's channel like any other."""
    kind = kind or kind_of(request)
    async for event in broadcast_turn(request, _local_pipeline(request, kind)):
        yield event


async def _run(request: ChatRequest, kind: str):
    from chat import routing
    if routing.enabled():
        async for event in routing.relay(request, kind):
            yield event
        return
    async for event in execute_locally(request, kind):
        yield event


async def run_chat_pipeline(request: ChatRequest):
    """One agent, streamed to its caller and to the conversation's channel."""
    async for event in _run(request, "agent"):
        yield event


async def run_chat_flow_pipeline(request: ChatRequest):
    """A flow's DAG, same treatment: one bubble per node, seen by every viewer."""
    async for event in _run(request, "flow"):
        yield event


async def run_chat_team_pipeline(request: ChatRequest):
    """A team's conversation, streamed as it is said."""
    async for event in _run(request, "team"):
        yield event
