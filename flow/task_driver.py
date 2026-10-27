"""
Task-surface driver for the shared flow engine.

Builds the :class:`flow.engine.FlowEngineDriver` used by ``runtime/flow_run.py``'s
subprocess flow runs: per-node run records, node log files, ``SessionPublishCallback``
HTTP streaming, task execution-log entries, ``kind="task"`` flow-log events, and
the between-node stop check against the flow-run record.

The engine owns the DAG walk; everything here is the task-specific I/O it
delegates to. ``build_task_driver`` captures the per-run context (argparse args,
ids, the loaded flow) in closures and returns a configured driver.
"""
from __future__ import annotations

import logging
import argparse
import asyncio
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from langchain_core.callbacks import BaseCallbackHandler

from agents.callbacks import (
    ChatStreamCallback,
    SessionPublishCallback as _SessionPublishCallback,
    append_log,
    write_log,
)
from agents.agent_lifecycle import run_agent_lifecycle
from managers.run_manager import open_run, close_run, update_run, run_log_path
from flow import run_store
from flow.engine import (
    SKIP_REASON_TEXT,
    _node_value,
    build_agent_input as _build_agent_input,
    FlowEngineDriver,
)
from flow.state import RunContext, StateMutationError
from flow.dispatch import DispatchResult
from flow.node_log import node_files, node_log_header, node_log_ending, node_step_stats, node_summary_line

log = logging.getLogger(__name__)


class _NodeStopped(RuntimeError):
    """Raised by :class:`_StopFlagGuard` once a node's stop flag is set."""


class _StopFlagGuard(BaseCallbackHandler):
    """Abort a synchronous agent run the next time it starts an LLM call or a
    tool call, once ``stop_event`` has been set.

    This is the actual stop for a node that hit its ``timeout_seconds``: the
    invocation runs synchronously on a worker thread (``asyncio.to_thread``,
    see ``_run_agent_node_async``), which ``asyncio.wait_for``'s cancellation
    at the engine level cannot reach — cancelling the coroutine that is
    *awaiting* the thread does nothing to the thread already inside a
    blocking call. ``_stop_agent_node`` sets the flag from the engine's task;
    this guard is the thing actually watching for it from inside the run, so
    the model stops observably at its next step instead of running to
    completion unobserved while the engine has already moved on and closed
    its run record.

    It cannot interrupt a call already in flight — the HTTP request to the
    model, or a tool already executing — only the boundary before the next
    one starts. ``raise_error = True`` tells LangChain to propagate the
    exception out of the executor instead of swallowing it, exactly like the
    other run guards in ``agents/callbacks/guards.py``.
    """

    def __init__(self, stop_event: threading.Event) -> None:
        super().__init__()
        self.raise_error = True
        self._stop_event = stop_event

    def _check(self) -> None:
        if self._stop_event.is_set():
            raise _NodeStopped("node stopped: it exceeded its timeout_seconds")

    def on_llm_start(self, *args: Any, **kwargs: Any) -> None:
        self._check()

    def on_tool_start(self, *args: Any, **kwargs: Any) -> None:
        self._check()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_flow(flow_id, run_id, payload: Dict[str, Any]) -> None:
    """Append a ``kind="task"`` flow-log event. Each flow run writes to its own
    file (flow_logs/<flow_id>/<run_id>.json), tagged with run_group=run_id so the
    dashboard History tab groups events by run. Shared tagging lives in run_store."""
    run_store.log_flow_event(flow_id, run_id, payload, kind="task")


def _agent_name(agent_id: str) -> str:
    """The agent's name, for a node without a label of its own."""
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:  # noqa: BLE001 - the id names it well enough
        return agent_id
    return (getattr(spec, "name", "") or "").strip() or agent_id


def _resolve_agent_id(node: Dict[str, Any]) -> Optional[str]:
    """The agent a node runs, taken from its explicit ``agent_id``.

    Nodes must carry a real agent id; there is no label translation. A
    renamed agent's old id (agents.registry.LEGACY_AGENT_IDS) runs the agent
    under its current id.
    """
    raw = _node_value(node, "agent_id")
    if not raw:
        return None
    from agents.registry import resolve_agent_id
    return resolve_agent_id(raw) or None


def _close_node_run(
    task_id: str,
    run_id: str,
    agent_id: str,
    *,
    status: str,
    exit_code: int,
    error: str = "",
    output: Optional[str] = None,
    process: Any = None,
) -> None:
    """Close a node's run record (status/exit_code/output/process/error).

    The task's run history is derived from the session (message_ids → runs), so
    the run record is the single source of truth — there is no separate sidecar.
    """
    close_kw: Dict[str, Any] = {"status": status, "exit_code": exit_code}
    if error:
        close_kw["error"] = error
    if output is not None:
        close_kw["output"] = output
    if process is not None:
        close_kw["process"] = process
    close_run(run_id, **close_kw)


def _run_agent_node(
    node: Dict[str, Any],
    node_id: str,
    prompt: str,
    *,
    args: argparse.Namespace,
    flow_state: Any,
    agent_overrides: Dict[str, Any],
    port: int,
    outcome: Any = None,
    stream_channels: Optional[list] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    flow_name: str = "",
    approval_link: Optional[Dict[str, Any]] = None,
) -> Any:
    """Execute an agent node in-process: open a run record, run the LLM agent
    with callbacks against the prebuilt ``prompt``, write its output to flow
    state, and close the record. Returns the same DispatchResult shape the
    dispatcher produced, so the caller handles agent and entity nodes uniformly.
    The prompt is built by the engine driver (flow.engine.build_agent_input).

    Runs synchronously on a worker thread (see ``_run_agent_node_async``), so
    when ``outcome`` is given, a ``_StopFlagGuard`` watching its
    ``stop_event`` rides along as an extra callback: it is how
    ``_stop_agent_node`` actually reaches into this call from the engine's
    side once the node's ``timeout_seconds`` has passed.

    A tool call that needs a person waits in this node
    (common/tool_approvals.py ``background_scope``): the card goes out on the
    run's channels, a notification lands in the bell, and the wait ends with
    an answer, the node's timeout, ``should_stop`` (the flow's or the loop's
    stop) or the background approval timeout. ``approval_link`` is what the
    notification links to besides the flow run (a loop's ids).
    """
    from uuid import uuid4

    agent_id = _resolve_agent_id(node)
    if not agent_id:
        return DispatchResult(ok=False, error="node has no agent assigned")
    label = _node_value(node, "label") or _agent_name(agent_id)

    run_id = str(uuid4())
    # Publish the run id on the outcome as soon as it exists: a node that hits
    # its timeout is stopped from the engine's thread, and closing its run
    # record is the only stop this surface can offer (the LLM call itself runs
    # in a worker thread that cannot be interrupted).
    if outcome is not None:
        outcome.run_id = run_id
    log_path = run_log_path(run_id)

    # One instance per node of this flow run — re-executions of the same node
    # (retry, loop-back) land on the same copy rather than multiplying rows.
    instance_id = None
    try:
        from instances import registry as instance_registry
        instance_id = instance_registry.ensure_instance(
            agent_id,
            instance_id=instance_registry.deterministic_id(args.run_id, node_id),
            kind="flow_node",
            session_id=args.session_id,
            task_id=args.task_id,
            label=f"{agent_id} · {label}",
            state="active",
            pid=os.getpid(),
        )["instance_id"]
    except Exception:  # noqa: BLE001 - a node without an instance record still runs
        log.debug("flow node instance create failed", exc_info=True)

    open_run(
        run_id, agent_id, pid=os.getpid(),
        task_id=args.task_id, session_id=args.session_id, session_type="task",
        channel="flow",
        log_file=str(log_path), flow_id=args.flow_id, flow_run_id=args.run_id,
        flow_node_id=node_id, flow_node_label=label, link_to_session=True,
        instance_id=instance_id,
    )

    try:
        update_run(run_id, {"input": prompt})
    except Exception:  # noqa: BLE001 - recording the input is best effort
        log.debug("run input update failed", exc_info=True)

    # The node is watched by the chat callback, as a flow chat's node is
    # (chat/flow_driver.py): the same log, the same record of its tool calls,
    # thoughts, changed files, touched records and cited sources. Its events
    # stream on the run's channels (common/run_stream.py) in the flow chat's
    # vocabulary, tagged with the node, so every page that shows the run
    # follows it live; the session publisher below feeds the session page.
    started = _utc_now_iso()
    msg_id = str(uuid4())[:8]
    log_lines = node_log_header(meta=[
        f"=== Flow node run  run_id={run_id} ===",
        f"Started   : {started}",
        f"Agent     : {agent_id}",
        f"Flow run  : {args.run_id} ({args.flow_id})",
        f"Session ID: {args.session_id or '—'}",
        f"Node      : {node_id} / {label}",
    ], started=started, msg_id=msg_id, agent_id=agent_id, user_message=prompt)
    write_log(log_path, log_lines)
    from common.run_stream import ChannelRelay
    relay = ChannelRelay(stream_channels or [], {
        "node_id": node_id, "agent_id": agent_id, "agent_label": label,
        "flow_run_id": args.run_id,
    })
    callback = ChatStreamCallback(None, None, log_lines, log_path,
                                  session_id=args.session_id, run_id=run_id, publish=relay)
    pub_cb = _SessionPublishCallback(args.session_id, run_id, agent_id, port)
    pub_cb._post({"type": "meta", "run_id": run_id, "session_id": args.session_id,
                  "agent_id": agent_id, "continuation": True})

    from common import artifact_sink, citation_sink as citation_sink_mod, entity_sink as entity_sink_mod
    node_entities = entity_sink_mod.EntitySink()
    node_citations = citation_sink_mod.CitationSink()

    def _record(inv: Any, response_text: str, response_obj: Any = None) -> Dict[str, Any]:
        """The node run's process record, as the chat builds one, and its
        links kept at the top of the run record where the run page reads them."""
        from chat.streaming import build_process_payload
        entities: list = []
        if node_entities:
            try:
                from common.entity_links import entity_payloads
                entities = entity_payloads(node_entities.records())
            except Exception:  # noqa: BLE001 - links to touched records are a nicety
                log.debug("node entity payloads failed", exc_info=True)
        citations = node_citations.payloads()
        links = {k: v for k, v in (("entities", entities), ("citations", citations)) if v}
        if links:
            try:
                update_run(run_id, links)
            except Exception:  # noqa: BLE001 - the process record still carries them
                log.debug("could not store the links of run %s", run_id, exc_info=True)
        usage = {
            "inbound_tokens": callback.prompt_tokens,
            "outbound_tokens": callback.completion_tokens,
            "total_tokens": callback.total_tokens,
            "cached_tokens": callback.cached_prompt_tokens,
            **callback.context_usage(),
        }
        return build_process_payload(
            callback, full_prompt=prompt, final_response=response_text, response_obj=response_obj,
            entities=entities, citations=citations, usage=usage,
            duration_ms=int(getattr(inv, "duration_ms", 0) or 0),
        )

    def _end_log(status: str, duration_ms: int, *, output: str = "", error: str = "") -> None:
        if error:
            append_log(log_lines, f"(error: {error})", log_path)
        elif output:
            append_log(log_lines, output, log_path)
        append_log(log_lines, node_summary_line(callback, msg_id, duration_ms), log_path)
        write_log(log_path, log_lines + node_log_ending(status, _utc_now_iso()))

    def _extras() -> Dict[str, Any]:
        # What the node's line in the flow log says about its steps and files.
        return {"stats": node_step_stats(callback, log_lines), "files": node_files(callback)}

    def _on_build_error(error_msg: str) -> Any:
        # create_agent never started running, so there is no duration/process.
        _end_log("failed", 0, error=error_msg)
        _close_node_run(args.task_id, run_id, agent_id, status="failed", exit_code=1, error=error_msg)
        return DispatchResult(ok=False, error=error_msg, run_id=run_id, duration_ms=0)

    def _on_failure(error_msg: str, inv: Any) -> Any:
        pub_cb.publish_done(inv.result, inv)
        _end_log("failed", inv.duration_ms, error=error_msg)
        _close_node_run(args.task_id, run_id, agent_id, status="failed", exit_code=1,
                        error=error_msg, process=_record(inv, ""))
        return DispatchResult(ok=False, error=error_msg, run_id=run_id, duration_ms=inv.duration_ms,
                              extras=_extras())

    def _on_success(result: Any, inv: Any) -> Any:
        output = result.agent_output or ""
        # Write to declared output state keys (mutability enforced).
        out_keys = _node_value(node, "output") or _node_value(node, "outputs") or []
        written: Dict[str, Any] = {}
        if out_keys:
            try:
                written = flow_state.apply(list(out_keys), output)
            except StateMutationError as sme:
                return _on_failure(str(sme), inv)
        # Close the node's live block only once the state writes succeeded — a
        # mutation error routes to _on_failure, which publishes its own `done`.
        pub_cb.publish_done(result, inv)
        _end_log("completed", inv.duration_ms, output=output)
        _close_node_run(args.task_id, run_id, agent_id, status="completed", exit_code=0,
                        output=output, process=_record(inv, output, getattr(result, "response", None)))
        return DispatchResult(
            ok=True, text=f"Completed {label}", output=output,
            written=written, run_id=run_id, duration_ms=inv.duration_ms, extras=_extras(),
        )

    # Watches outcome.stop_event, so a node the engine has already timed out
    # actually stops here at its next LLM/tool call instead of running to
    # completion unobserved on this worker thread — see _StopFlagGuard and
    # _stop_agent_node below.
    extra_callbacks = [callback, pub_cb]
    if outcome is not None:
        extra_callbacks.append(_StopFlagGuard(outcome.stop_event))

    # The node's own sinks, as the flow chat sets them per node, so each
    # node's changes, records and sources are its own.
    artifact_token = artifact_sink.set_recorder(callback.record_artifact)
    entity_token = entity_sink_mod.set_sink(node_entities)
    citation_token = citation_sink_mod.set_sink(node_citations)
    def _stop_wanted() -> bool:
        if outcome is not None and outcome.stop_event.is_set():
            return True
        return bool(should_stop and should_stop())

    from common import tool_approvals
    try:
        with tool_approvals.background_scope(
                kind=tool_approvals.KIND_FLOW, group_run_id=args.run_id, entity_id=args.flow_id,
                entity_name=flow_name, seat_id=node_id, seat_label=label,
                emitter=relay, should_stop=_stop_wanted, link=approval_link):
            return run_agent_lifecycle(
                agent_id, args.workspace, prompt,
                overrides=agent_overrides,
                extra_callbacks=extra_callbacks, catch_invoke_exceptions=True,
                after_build=lambda agent: callback.bind_model(getattr(agent, "provider", "") or "",
                                                              getattr(agent, "model", "") or ""),
                on_build_error=_on_build_error, on_success=_on_success, on_failure=_on_failure,
            )
    finally:
        relay.flush()
        artifact_sink.reset_recorder(artifact_token)
        entity_sink_mod.reset_sink(entity_token)
        citation_sink_mod.reset_sink(citation_token)


def build_task_driver(
    *,
    args: argparse.Namespace,
    flow: Dict[str, Any],
    flow_id: str,
    run_id: str,
    task_id: str,
    session_id: str,
    task_title: str,
    node_run_ids: Dict[str, str],
    port: int,
    agent_overrides: Optional[Dict[str, Any]] = None,
    stream_channels: Optional[list] = None,
    approval_link: Optional[Dict[str, Any]] = None,
) -> FlowEngineDriver:
    """Build the task-surface :class:`FlowEngineDriver`.

    Captures the per-run context in closures: agent nodes run through the sync
    ``_run_agent_node`` (off the event loop via ``asyncio.to_thread``), node
    outcomes are mirrored into flow-log events + the ``node_run_ids`` map, and
    the stop check reads the live flow-run record. The checkpoint and heartbeat
    hooks write to the same record, which is what lets a killed run be resumed
    instead of failed.

    The run streams live on ``run:<run_id>`` and on ``stream_channels`` (a
    loop's own channel for its pass): the flow chat's events, ``flow_meta``,
    ``node_start``, the agent's steps tagged with their node, ``node_skip``
    and ``node_done``, so a page shows a background run as it shows its own.

    A tool call that waits for a person in a node ends its wait when the
    driver's ``should_stop`` says so, read at the time: a loop replaces that
    hook after this returns, and its stop counts too.
    """
    from common import run_stream
    channels = [run_stream.run_channel(run_id), *(stream_channels or [])]
    # The driver this returns, for a node's stop check (see above).
    driver_box: list = []

    def _stream(event: Dict[str, Any]) -> None:
        run_stream.publish(channels, {**event, "flow_id": flow_id, "flow_run_id": run_id})
    # Every agent node is built as a flow step (agents/agent_factory.py):
    # it answers the next node, not a person.
    agent_overrides = {**(agent_overrides or {}), "flow_step": True}

    # What each agent node was given, for its line in the run log (the
    # History and a loop's pass log show it above the node's steps).
    node_inputs: Dict[str, str] = {}
    # Each agent node's step counts and changed files, for the same line.
    node_extras: Dict[str, Dict[str, Any]] = {}

    async def _run_agent_node_async(node, node_id, label, prompt, outcome, flow_state):
        node_inputs[node_id] = prompt
        # _run_agent_node is synchronous (sync invoke_agent); run it off the loop.
        # It yields no streaming events — agent output reaches the UI via the
        # SessionPublishCallback HTTP posts inside _run_agent_node itself.
        # flow_state is the engine's instance, so output-key writes land where
        # successor nodes read them.
        dr = await asyncio.to_thread(
            _run_agent_node, node, node_id, prompt,
            args=args, flow_state=flow_state, agent_overrides=agent_overrides,
            port=port, outcome=outcome, stream_channels=channels,
            should_stop=lambda: bool(driver_box) and driver_box[0].should_stop(),
            flow_name=str(flow.get("name") or ""), approval_link=approval_link,
        )
        if dr.extras:
            node_extras[node_id] = dr.extras
        outcome.ok = dr.ok
        outcome.output = dr.output
        outcome.text = dr.text
        outcome.written = dr.written or {}
        outcome.goto = dr.goto
        outcome.error = dr.error
        outcome.run_id = dr.run_id
        outcome.duration_ms = dr.duration_ms
        return
        yield  # make this an async generator (never reached)

    def _stop_agent_node(node: Dict[str, Any], node_id: str, outcome: Any) -> None:
        """Stop a node that ran past its ``timeout_seconds``.

        The invocation runs synchronously on a worker thread
        (``asyncio.to_thread``, see ``_run_agent_node_async``), which nothing
        here can forcibly kill or interrupt mid-call: a blocking HTTP request
        to the model, or a tool already executing, runs to its own end
        regardless. Two things happen instead:

        1. The node's run record is closed as failed immediately, so the
           engine (and everything reading the record — the task's execution
           log, the dashboard) sees the node as done right now rather than
           waiting on a thread that may never return in a useful time.
        2. ``outcome.stop_event`` is set. ``_StopFlagGuard`` (installed as a
           callback on the same invocation, see ``_run_agent_node``) checks it
           at the start of the model's *next* LLM call or tool call and aborts
           the run there, from inside the still-running thread. This is the
           actual stop: the model does not keep going indefinitely, it stops
           at most one step after the timeout fires. It is not immediate — a
           call already in flight still has to finish first — so this narrows
           "abandoned, runs to completion unobserved" down to "stops at its
           next step," not all the way to "stops now."

        Either way the thread's own eventual return (whether it stopped there
        or, rarer, finished normally just after) tries to close the same run
        record again; that second close is a no-op against the one already
        written here.
        """
        rid = getattr(outcome, "run_id", "") or ""
        stop_event = getattr(outcome, "stop_event", None)
        if stop_event is not None:
            stop_event.set()
        if not rid:
            return
        try:
            close_run(rid, status="failed", exit_code=1,
                      error="node exceeded its timeout_seconds and was abandoned")
        except Exception as e:  # noqa: BLE001
            print(f"[node_timeout] could not close run {rid[:8]}: {e}")

    def _on_checkpoint(cp: Dict[str, Any]) -> None:
        """Persist the resume point on the flow-run record after every node.

        Arbitrary keys survive on a flow-run record, so the checkpoint rides on
        the record the watchdog and the resume endpoint already read, rather
        than in a file of its own that could disagree with it.
        """
        try:
            run_store.update_flow_run(run_id, {
                "checkpoint": cp, "heartbeat_at": _utc_now_iso(),
            })
        except Exception as e:  # noqa: BLE001 - a checkpoint never fails a run
            print(f"[checkpoint] could not write checkpoint for {run_id[:8]}: {e}")

    def _on_heartbeat() -> None:
        """Prove the run is alive while one node takes a long time. The watchdog
        treats a stale heartbeat, not a missing pid, as death."""
        try:
            run_store.update_flow_run(run_id, {"heartbeat_at": _utc_now_iso()})
        except Exception:  # noqa: BLE001 - a missed heartbeat must not fail the run
            log.debug("heartbeat update failed", exc_info=True)

    def _make_run_context(node_id: str) -> RunContext:
        return RunContext(
            flow_id=args.flow_id, run_id=args.run_id, task_id=args.task_id,
            session_id=args.session_id, workspace=args.workspace, node_id=node_id,
        )

    def _should_stop() -> bool:
        _fr = run_store.get_flow_run(run_id)
        return bool(_fr and _fr.get("status") in ("stop", "stopping", "stopped"))

    def _on_node_start(ev: dict) -> None:
        _stream({
            "type": "node_start", "node_id": ev["node_id"],
            "agent_id": ev.get("entity_id"), "agent_label": ev.get("label"),
            "session_id": session_id,
            **({"input": ev.get("prompt") or ""} if ev.get("is_agent") else {}),
        })
        if ev.get("is_agent"):
            log_flow(flow_id, run_id, {
                "timestamp": _utc_now_iso(), "type": "agent_start",
                "node_id": ev["node_id"], "agent_name": ev.get("label"),
                "tag": ev.get("category"), "content": f"Running {ev.get('label')}",
                "status": "running",
            })

    #: Why a node was not executed, in the words the history tab shows.
    def _on_node_skip(ev: dict) -> None:
        reason = ev.get("reason") or ""
        print(f"[node_skip] node={ev['node_id']} reason={reason}")
        _stream({"type": "node_skip", "node_id": ev["node_id"], "reason": reason,
                 "session_id": session_id})
        log_flow(flow_id, run_id, {
            "timestamp": _utc_now_iso(), "type": "node_skip", "node_id": ev["node_id"],
            "content": f"Skipping node '{ev['node_id']}': "
                       + SKIP_REASON_TEXT.get(reason, reason or "not on the selected branch"),
            "reason": reason, "status": "skipped",
        })

    def _on_node_done(ev: dict) -> None:
        node_id = ev["node_id"]
        if ev.get("run_id"):
            node_run_ids[node_id] = ev["run_id"]
        is_agent = ev.get("is_agent")
        # The branch a switch or condition chose, with the steps' names, as
        # the flow chat words it (chat/flow_driver.py): the page says which
        # step it went to by name, not by id.
        route = None
        if not is_agent and ev.get("ok") and ev.get("goto") is not None:
            from flow.branching import node_route
            route = node_route(ev, flow)
        # The flow chat's node_done (chat/pipelines.py), so a page folds it alike.
        _stream({
            "type": "node_done", "node_id": node_id,
            "agent_id": ev.get("entity_id"), "agent_label": ev.get("label"),
            "run_id": ev.get("run_id"), "ok": ev.get("ok"),
            "response": ("Stopped by user" if ev.get("stopped")
                         else (ev.get("output") or ev.get("text") or "") if ev.get("ok")
                         else f"Error: {ev.get('error') or 'unknown error'}"),
            "error": ev.get("error"), "duration_ms": ev.get("duration_ms", 0),
            "state": ev.get("state"), "session_id": session_id,
            **({} if is_agent else {"is_agent": False, "summary": ev.get("text") or ""}),
            **({"route": route} if route else {}),
        })
        if ev.get("ok"):
            if ev.get("goto") is not None:
                print(f"[branch] node={node_id} selected={ev['goto']}")
            print(f"[node_done] node={node_id} label={ev.get('label')} duration_ms={ev.get('duration_ms')}")
            log_flow(flow_id, run_id, {
                "timestamp": _utc_now_iso(), "type": "agent_finish",
                "node_id": node_id, "agent_id": ev.get("entity_id"),
                "agent_name": ev.get("label"), "tag": ev.get("category"),
                "content": f"Completed {ev.get('label')}"
                           + ("" if is_agent else
                              f": wrote {', '.join(ev.get('written') or []) or '(no state keys)'}")
                           + (f" → branch {ev['goto']}" if ev.get("goto") else ""),
                "status": "completed",
                "output": ev.get("output") or ev.get("text"),
                "state": ev.get("state"),
                **({"input": node_inputs.pop(node_id)} if node_id in node_inputs else {}),
                **node_extras.pop(node_id, {}),
                **({"run_id": ev["run_id"]} if ev.get("run_id") else {}),
                **({"route": route} if route else {}),
            })
        else:
            print(f"[node_error] node={node_id} label={ev.get('label')} error={ev.get('error')}")
            log_flow(flow_id, run_id, {
                "timestamp": _utc_now_iso(), "type": "agent_error",
                "node_id": node_id, "agent_id": ev.get("entity_id"),
                "agent_name": ev.get("label"), "tag": ev.get("category"),
                "content": f"{ev.get('label')} failed: {ev.get('error')}",
                "status": "failed", "output": ev.get("error"),
                **({"input": node_inputs.pop(node_id)} if node_id in node_inputs else {}),
                **node_extras.pop(node_id, {}),
                **({"run_id": ev["run_id"]} if ev.get("run_id") else {}),
            })

    def _on_flow_start(ev: dict) -> None:
        _stream({
            "type": "flow_meta", "flow_name": flow.get("name") or flow_id,
            "session_id": session_id, "state": ev.get("state"),
            "nodes": [{"node_id": n.get("node_id"), "agent_id": n.get("entity_id"),
                       "agent_label": n.get("label")} for n in ev.get("nodes") or []],
        })
        log_flow(flow_id, run_id, {
            "timestamp": _utc_now_iso(), "type": "flow_start",
            "content": f"Starting flow: {flow.get('name', flow_id)}", "status": "running",
            "title": task_title or f"Flow: {flow.get('name', flow_id)}",
            "task_id": task_id, "session_id": session_id,
            "state": ev.get("state"),
        })

    driver = FlowEngineDriver(
        build_agent_prompt=_build_agent_input,
        run_agent_node=_run_agent_node_async,
        make_run_context=_make_run_context,
        should_stop=_should_stop,
        on_flow_start=_on_flow_start,
        on_node_start=_on_node_start,
        on_node_done=_on_node_done,
        on_node_skip=_on_node_skip,
        on_checkpoint=_on_checkpoint,
        on_heartbeat=_on_heartbeat,
        stop_agent_node=_stop_agent_node,
        # The task surface owns a task and a flow-run record, so it can park a
        # run in awaiting_input and come back to it. That is what makes a
        # human_interrupt node work here and not in flow chat.
        supports_interrupt=True,
    )
    driver_box.append(driver)
    return driver
