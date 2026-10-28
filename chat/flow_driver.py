"""
Chat-surface driver for the shared flow engine.

Builds the :class:`flow.engine.FlowEngineDriver` used by
``chat.pipelines.run_chat_flow_pipeline``: per-node chat run records, streaming
agent execution via ``drive_streaming_run`` + ``ChatStreamCallback``, and
``kind="chat"`` flow-log writes.

The engine owns the DAG walk; everything here is the chat-specific I/O it
delegates to. ``build_chat_driver`` captures the per-request context in closures
and returns ``(driver, state)`` — ``state`` exposes the per-node bookkeeping
(``node_meta``, ``last_run_id``) the pipeline's neutral→SSE translation reads.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from agents.callbacks import (
    ChatStreamCallback,
    append_log as _append_log,
    write_log as _write_log,
)
from agents.agent_factory import create_agent
from managers.run_manager import (
    run_log_path,
    new_unique_run_id,
    open_run as register_run,
    update_run as _update_run,
)
from common import artifact_sink, citation_sink as citation_sink_mod, entity_sink as entity_sink_mod

from chat.models import ChatRequest
from chat.runs import utc_iso
from chat.streaming import StreamDriveResult, drive_streaming_run

from flow.branching import node_route
from flow.engine import SKIP_REASON_TEXT, FlowEngineDriver, build_agent_input
from flow.node_log import (
    node_files as _node_files, node_log_ending, node_log_header, node_step_stats as _node_step_stats,
    node_summary_line,
)
from flow.state import RunContext

log = logging.getLogger(__name__)


@dataclass
class ChatFlowState:
    """Per-node bookkeeping shared between the chat driver and the pipeline's
    neutral→SSE translation. ``node_meta`` maps node_id → {run_id, label,
    agent_id, usage, tool_calls, ...}; ``last_run_id`` is the most recent node's
    run id (used as the final ``done`` event's run_id)."""
    node_meta: Dict[str, dict] = field(default_factory=dict)
    last_run_id: Optional[str] = None
    #: node_id → the conversation messages that node runs with. Only a root node
    #: has one: downstream nodes answer their predecessor, not the user.
    node_history: Dict[str, list] = field(default_factory=dict)
    #: node_id → the branch a switch or condition chose (see node_route).
    node_route: Dict[str, dict] = field(default_factory=dict)
    #: The provider, model, key and base URL of the last agent node that ran,
    #: for the turn's summary (chat/flow_summary.py).
    last_model: Dict[str, Any] = field(default_factory=dict)


def build_chat_driver(
    *,
    request: ChatRequest,
    flow: Dict[str, Any],
    conv_id: str,
    session_id: Optional[str],
    flow_name: str,
    session_title: str,
    workspace_abs: Optional[str],
    history_messages: list,
    context_lines: list,
    user_message: str,
    log_flow: Callable[[dict], None],
    project_note: Optional[str] = None,
) -> tuple[FlowEngineDriver, ChatFlowState]:
    """Build the chat-surface :class:`FlowEngineDriver` + its :class:`ChatFlowState`.

    ``log_flow`` is the conversation's ``kind="chat"`` flow-log writer (the
    pipeline owns it because it also logs flow_start/finish). The returned
    ``state`` is mutated as nodes run; the pipeline reads it while translating
    engine events into SSE shapes.
    """
    state = ChatFlowState()
    node_meta = state.node_meta

    def _chat_prompt(shared, node, node_id, predecessors, node_outputs, flow_state, node_task):
        """Root nodes get the latest user message + the attached context blocks
        (entities and files), with the conversation itself carried alongside as
        messages; downstream nodes get the engine's predecessor/state block."""
        has_pred = any(pid in node_outputs for pid in predecessors.get(node_id, []))
        # Project scope preamble leads every node prompt (root and downstream) so
        # each agent knows its file/task tools are confined to the project.
        parts = [project_note, ""] if project_note else []
        if has_pred:
            base = build_agent_input(shared, node, node_id, predecessors, node_outputs, flow_state, node_task)
            parts.append(base)
        else:
            # A root node answers the user, so it gets the conversation — as
            # messages, next to this turn's text, not folded into it.
            state.node_history[node_id] = list(history_messages)
            parts += [user_message]
            # Still surface any declared input-state slice for root nodes.
            state_block = build_agent_input("", node, node_id, predecessors, {}, flow_state, "")
            if state_block.strip():
                parts.append(state_block)
            if node_task:
                parts += ["", "## Your specific task for this step:", node_task]
        if context_lines:
            parts.extend(context_lines)
        return "\n".join(parts)

    def _open_node_run(node, node_id, label, prompt):
        """Open a node's chat run record + log scaffold. Called from on_node_start
        (sync) so the run_id is in node_meta before the engine yields node_start —
        the SSE node_start/meta events must carry it to anchor token routing."""
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        yaml_agent_id = node.get("agent_id") or data.get("agent_id") or label
        node_domain = node.get("domain") or data.get("domain") or "general"

        run_id = new_unique_run_id()
        state.last_run_id = run_id
        log_file = run_log_path(run_id)
        started = utc_iso()
        run_title = f"{label}: {user_message[:50]}" + ("…" if len(user_message) > 50 else "")
        node_msg_id = str(uuid4())[:8]
        # Log format mirrors the standard agent chat log so the chat-log parser in
        # routes/messages.py (_extract_chat_message_runs) picks up tool calls,
        # thinking entries, and token usage for the message-insights UI.
        log_lines = node_log_header(meta=[
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {yaml_agent_id}",
            f"Workspace : {request.workspace or '—'}",
            f"Conv ID   : {conv_id}",
            f"Session ID: {session_id or '—'}",
            f"Flow      : {flow_name} ({request.flow_id})",
            f"Node      : {node_id} / {label}",
            f"Title     : {run_title}",
        ], started=started, msg_id=node_msg_id, agent_id=yaml_agent_id, user_message=user_message)
        _write_log(log_file, log_lines)
        register_run(
            run_id, yaml_agent_id, task_id=conv_id, session_id=session_id,
            session_type="chat", message_origin=request.source or "chat", channel="chat_flow",
            workspace=request.workspace,
            title=run_title, log_file=str(log_file), flow_id=request.flow_id,
            flow_node_id=node_id, flow_node_label=label,
            **({"auto_approve": True} if getattr(request, "auto_approve", False) else {}),
        )
        try:
            _update_run(run_id, {"input": prompt})
        except Exception:  # noqa: BLE001 - recording the input is best effort
            log.debug("run input update failed", exc_info=True)
        log_flow({
            "timestamp": utc_iso(), "type": "agent_start", "node_id": node_id,
            "agent_id": yaml_agent_id, "agent_name": label, "tag": node_domain,
            "content": f"Running {label}", "status": "running", "input": prompt,
            "run_id": run_id,
        })
        # A message sent to a node of this turn that finished without taking
        # it goes to the node starting now (common/steering.py), so a person
        # steering a flow is heard by whichever agent runs next.
        try:
            from common import steering
            steering.retarget_pending(
                [m.get("run_id") for m in node_meta.values() if m.get("run_id")], run_id)
        except Exception:  # noqa: BLE001 - an unmoved message is settled with the turn instead
            logging.getLogger(__name__).debug("steering: could not move messages to %s", run_id,
                                              exc_info=True)
        node_meta[node_id] = {
            "run_id": run_id, "label": label, "agent_id": yaml_agent_id,
            "domain": node_domain, "log_file": log_file, "log_lines": log_lines,
            "msg_id": node_msg_id, "usage": {}, "tool_calls": 0,
        }

    async def _run_agent_node(node, node_id, label, prompt, outcome, flow_state):
        """Stream one agent node: run via arun + the chat callback, drain events,
        and finalize logs/run-record/flow-log. The run record was opened in
        on_node_start (_open_node_run); this reads it from node_meta. Yields the
        node-tagged callback events; fills ``outcome`` for the engine.

        ``flow_state`` (the engine's instance) is accepted for contract symmetry;
        chat agent output keys are written by the engine from ``outcome.output``."""
        meta = node_meta[node_id]
        run_id = meta["run_id"]
        log_file = meta["log_file"]
        log_lines = meta["log_lines"]
        node_msg_id = meta["msg_id"]
        yaml_agent_id = meta["agent_id"]
        node_domain = meta["domain"]

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        callback = ChatStreamCallback(loop, queue, log_lines, log_file,
                                      session_id=session_id, run_id=run_id)
        node_started_ts = time.perf_counter()

        async def _run_node_agent():
            # create_agent is heavyweight and fully synchronous (YAML load, model
            # resolution, memory/skills/workspace-instruction injection with file
            # I/O, tool construction, LLM-client init). Running it inline would
            # block the single event loop for the whole build — once per node — so
            # every concurrent request (the SSE channel, the editor's logs/runs
            # polling) stalls and shows as "pending" while a flow chat runs. Build
            # it in a worker thread to keep the loop responsive.
            agent = await asyncio.to_thread(
                create_agent, yaml_agent_id, workspace=workspace_abs, streaming=True,
                flow_step=True,
            )
            callback.bind_model(agent.provider or "", agent.model or "")
            # The turn's summary (chat/flow_summary.py) is written by the
            # model of the last agent node that ran.
            state.last_model = {
                "provider": agent.provider, "model": agent.model,
                "api_key": getattr(agent, "api_key", None),
                "base_url": getattr(agent, "base_url", None),
            }
            # The node's run id reaches its loop so a message sent to this
            # node while it works is read before its next step.
            from chat.pipelines import _run_id_kwargs
            return await agent.arun(prompt, history=state.node_history.get(node_id),
                                    callbacks=[callback], **_run_id_kwargs(agent, run_id))

        # Per-node artifact recorder so each node's file changes are attributed to
        # its own run. create_task copies the context, so set→create→reset here.
        _node_artifact_token = artifact_sink.set_recorder(callback.record_artifact)
        # Per-node entity sink for the same reason: the tasks, views, flows and
        # files this node's tools touched are linked from that node's reply.
        node_entities = entity_sink_mod.EntitySink()
        _node_entity_token = entity_sink_mod.set_sink(node_entities)
        # And a per-node citation sink: the passages this node's memory search
        # showed its model, numbered for that node's own [n] references.
        node_citations = citation_sink_mod.CitationSink()
        _node_citation_token = citation_sink_mod.set_sink(node_citations)
        task = asyncio.create_task(_run_node_agent())
        artifact_sink.reset_recorder(_node_artifact_token)
        entity_sink_mod.reset_sink(_node_entity_token)
        citation_sink_mod.reset_sink(_node_citation_token)

        drive = StreamDriveResult()
        try:
            async for event in drive_streaming_run(
                task=task, queue=queue, callback=callback, run_id=run_id,
                full_prompt=prompt, started_ts=node_started_ts, result=drive,
                entity_sink=node_entities,
                citation_sink=node_citations,
            ):
                yield event
        except asyncio.CancelledError:
            callback.cancelled = True
            try:
                task.cancel()
            except RuntimeError:
                log.debug("task cancel failed", exc_info=True)
            finished = utc_iso()
            _write_log(log_file, log_lines + ["(cancelled)", "", f"Finished: {finished}", "Status  : stopped"])
            _update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "cancelled"})
            # Emit the node's terminal flow-log event so the History record and the
            # canvas node status finalize as "stopped" instead of being stuck on the
            # earlier agent_start. The drive.stopped path below does this for a
            # polled stop; a client abort lands here and must do it too.
            log_flow({
                "timestamp": finished, "type": "agent_stopped", "node_id": node_id,
                "agent_id": yaml_agent_id, "agent_name": label, "tag": node_domain,
                "content": f"{label} stopped by user", "status": "stopped",
                "input": prompt, "output": "Stopped by user",
                "run_id": run_id, "stats": _node_step_stats(callback, log_lines),
                "files": _node_files(callback),
            })
            raise

        finished = utc_iso()
        usage = drive.usage
        meta["usage"] = usage
        meta["tool_calls"] = callback.tool_calls
        # Links to the service entities this node touched; the pipeline attaches
        # them to the node's done event so its bubble can show them.
        meta["entities"] = drive.entities
        # The sources the node's reply cites as [n] (common/citation_sink.py);
        # already on the node's run record as ``process.citations``.
        meta["citations"] = drive.citations
        summary_line = node_summary_line(callback, node_msg_id, drive.duration_ms)

        if drive.stopped:
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + ["(stopped by user)", *node_log_ending("stopped", finished)])
            _update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1,
                                 "error": "stopped by user", "process": drive.process_payload})
            log_flow({
                "timestamp": finished, "type": "agent_stopped", "node_id": node_id,
                "agent_id": yaml_agent_id, "agent_name": label, "tag": node_domain,
                "content": f"{label} stopped by user", "status": "stopped",
                "input": prompt, "output": "Stopped by user",
                "run_id": run_id, "stats": _node_step_stats(callback, log_lines),
                "files": _node_files(callback),
            })
            outcome.ok = False
            outcome.stopped = True
            outcome.output = "Stopped by user"
            outcome.error = "stopped by user"
            outcome.run_id = run_id
            outcome.duration_ms = drive.duration_ms
            return

        if drive.ok:
            _append_log(log_lines, drive.response, log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + node_log_ending("completed", finished))
            _update_run(run_id, {"status": "completed", "finished_at": finished,
                                 "exit_code": 0, "process": drive.process_payload})
            log_flow({
                "timestamp": finished, "type": "agent_finish", "node_id": node_id,
                "agent_id": yaml_agent_id, "agent_name": label, "tag": node_domain,
                "content": f"Completed {label}", "status": "completed",
                "input": prompt, "output": drive.response,
                "run_id": run_id, "stats": _node_step_stats(callback, log_lines),
                "files": _node_files(callback),
            })
            outcome.ok = True
            outcome.output = drive.response
            outcome.text = f"Completed {label}"
            outcome.run_id = run_id
            outcome.duration_ms = drive.duration_ms
        else:
            err = drive.error or "unknown error"
            _append_log(log_lines, f"(error: {err})", log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + node_log_ending("failed", finished))
            _update_run(run_id, {"status": "failed", "finished_at": finished, "exit_code": 1,
                                 "error": err, "process": drive.process_payload})
            log_flow({
                "timestamp": finished, "type": "agent_error", "node_id": node_id,
                "agent_id": yaml_agent_id, "agent_name": label, "tag": node_domain,
                "content": f"{label} failed: {err}", "status": "failed",
                "input": prompt, "output": err,
                "run_id": run_id, "stats": _node_step_stats(callback, log_lines),
                "files": _node_files(callback),
            })
            outcome.ok = False
            outcome.output = f"Error: {err}"
            outcome.error = err
            outcome.run_id = run_id
            outcome.duration_ms = drive.duration_ms

    def _make_run_context(node_id: str) -> RunContext:
        return RunContext(
            flow_id=request.flow_id, run_id=conv_id, task_id=conv_id,
            session_id=session_id or "", workspace=workspace_abs or "", node_id=node_id,
        )

    def _on_node_start(ev: dict) -> None:
        # Open the run record for agent nodes here (sync, before the engine yields
        # node_start) so the run_id is in node_meta when we translate node_start.
        if ev.get("is_agent"):
            # A map node's item (flow/branching.py) is not in the flow's node
            # list; the engine hands its synthesized node along.
            node = ev.get("node") or next(
                (n for n in flow.get("nodes", []) if n.get("id") == ev["node_id"]), {})
            _open_node_run(node, ev["node_id"], ev.get("label"), ev.get("prompt") or "")

    def _on_flow_start(ev: dict) -> None:
        log_flow({
            "timestamp": utc_iso(), "type": "flow_start",
            "content": f"Starting flow: {flow_name}", "status": "running",
            "title": session_title, "session_id": session_id, "conversation_id": conv_id,
            "state": ev.get("state"),
            # A turn that started at a node or ran again from one (chat/pipelines.py).
            **({"turn": ev["turn"]} if ev.get("turn") else {}),
            # The turn's own id, which a summary written for it later names.
            **({"turn_id": ev["turn_id"]} if ev.get("turn_id") else {}),
        })

    def _on_node_skip(ev: dict) -> None:
        # Logged as the task surface logs it (flow/task_driver.py), so a saved
        # chat record knows which nodes a branch passed over and which a run
        # again from a node took from the turn before (reason already_done).
        reason = ev.get("reason") or ""
        log_flow({
            "timestamp": utc_iso(), "type": "node_skip", "node_id": ev["node_id"],
            "content": f"Skipping node '{ev['node_id']}': "
                       + SKIP_REASON_TEXT.get(reason, reason or "not on the selected branch"),
            "reason": reason, "status": "skipped",
        })

    def _on_node_done(ev: dict) -> None:
        # A node with no agent (a switch, a transform) logs its end here, as
        # the task surface does (flow/task_driver.py), so a saved chat record
        # shows what it did: its bubble, its log row, its status on the graph.
        if not ev.get("is_agent") and not ev.get("stopped"):
            route = node_route(ev, flow)
            if route:
                state.node_route[ev["node_id"]] = route
            label = ev.get("label") or ev["node_id"]
            log_flow({
                "timestamp": utc_iso(), "type": "agent_finish" if ev.get("ok") else "agent_error",
                "node_id": ev["node_id"], "agent_id": ev.get("entity_id"),
                "agent_name": label, "tag": ev.get("category"), "entity": True,
                "content": f"Completed {label}" if ev.get("ok") else f"{label} failed: {ev.get('error')}",
                "status": "completed" if ev.get("ok") else "failed",
                "output": (ev.get("output") or ev.get("text") or "") if ev.get("ok") else (ev.get("error") or ""),
                **({"route": route} if route else {}),
            })
        # The chat path logs agent_finish/error inside _run_agent_node, before the
        # engine applies output keys, so the state snapshot isn't available there.
        # Emit it now as a lightweight event the dashboard folds into runtime state.
        snapshot = ev.get("state")
        if snapshot is None:
            return
        log_flow({
            "timestamp": utc_iso(), "type": "flow_state",
            "node_id": ev.get("node_id"), "content": "",
            "status": "running", "state": snapshot,
        })

    driver = FlowEngineDriver(
        build_agent_prompt=_chat_prompt,
        run_agent_node=_run_agent_node,
        make_run_context=_make_run_context,
        should_stop=lambda: False,   # chat polls run status per-node inside drive_streaming_run
        on_node_start=_on_node_start,
        on_flow_start=_on_flow_start,
        on_node_done=_on_node_done,
        on_node_skip=_on_node_skip,
        # No stop_agent_node override: unlike the task surface's synchronous,
        # worker-thread invocation (flow.task_driver, which needs an explicit
        # stop_event + in-loop guard to actually reach it), this node runs on
        # the event loop the whole way (agent.arun as an asyncio.Task). When
        # the engine's timeout fires, asyncio.wait_for cancels the coroutine
        # that is awaiting this generator, which throws CancelledError in here
        # at the current `async for event in drive_streaming_run(...)` — the
        # `except asyncio.CancelledError` block below cancels the underlying
        # agent task and closes the run record as stopped. The default no-op
        # stop_agent_node is correct as-is: there is nothing left for it to do.
    )
    return driver, state
