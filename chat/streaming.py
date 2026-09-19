"""
Shared streaming drive loop for chat agent runs.

``drive_streaming_run`` runs one streaming agent task while forwarding its
callback events onto the SSE stream, polling for a user stop, and resolving the
final result into a :class:`StreamDriveResult`. Used by both the single-agent
pipeline and each flow node, so the queue-draining / stop-polling logic lives in
one place; each caller owns its own finalization (logs, run records, journaling).
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

from managers.run_manager import get_run_by_id as get_run


@dataclass
class StreamDriveResult:
    """Outcome of :func:`drive_streaming_run`, consumed by per-pipeline finalizers.

    Holds everything the single-agent and per-flow-node finalizers need that the
    shared drive loop already computed: the resolved response text, ok/error
    state, whether the run was stopped, and the usage/process payloads built from
    the callback's accumulated stats.
    """
    ok: bool = False
    response: str = ""
    error: str | None = None
    stopped: bool = False
    duration_ms: int = 0
    usage: dict = field(default_factory=dict)
    process_payload: dict = field(default_factory=dict)
    # Structured response (AgentResponse) when the agent emitted a <<<ui>>> block;
    # ``response`` above still holds the plain-text fallback. None for plain replies.
    response_obj: object | None = None
    # Service entities the run touched (tasks, views, flows, files, …), resolved
    # to link payloads — see ``common.entity_links``. The chat UI renders them as
    # links under the reply; text-only surfaces append them to the text.
    entities: list = field(default_factory=list)


async def drive_streaming_run(
    *,
    task: asyncio.Task,
    queue: asyncio.Queue,
    callback,
    run_id: str,
    full_prompt: str,
    started_ts: float,
    result: StreamDriveResult,
    enrich: Callable[[dict], dict] | None = None,
    entity_sink=None,
):
    """Drive one streaming agent run: forward queued events and resolve the result.

    Shared by ``run_chat_pipeline`` (single agent) and the flow chat pipeline
    (per flow node). Yields each callback event (optionally passed through
    ``enrich`` to tag it with node/agent ids), polls the run record so a user
    "stop" cancels the task, then awaits the agent and populates ``result`` with
    the resolved text, usage, and process payload.

    The caller owns finalization (log writing, run-status update, journaling,
    flow-event emission) because those payloads differ between the two pipelines.

    ``entity_sink`` is the caller's :class:`common.entity_sink.EntitySink` for
    this run; the entities its tools touched are resolved into link payloads on
    ``result.entities`` so each surface can point the user at them.
    """
    token_parts: list[str] = []
    final_response = ""
    final_ok = False
    final_error: str | None = None

    last_stop_poll = 0.0
    while not task.done():
        # Poll for a user stop on every pass (throttled), not only when the
        # queue goes quiet — a steadily streaming model otherwise starves the
        # check. Setting callback.cancelled makes the agent's own callbacks
        # raise inside its execution thread (sync tool/LLM calls that
        # task.cancel() can't reach); task.cancel() handles the await points.
        now = time.perf_counter()
        if now - last_stop_poll >= 0.2:
            last_stop_poll = now
            current_status = (get_run(run_id) or {}).get("status")
            if current_status in ("stop", "stopped"):
                callback.cancelled = True
                task.cancel()
                break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.2)
            if event.get("type") == "token":
                token_parts.append(event.get("token") or "")
            yield enrich(event) if enrich else event
        except asyncio.TimeoutError:
            continue

    while not queue.empty():
        event = await queue.get()
        if event.get("type") == "token":
            token_parts.append(event.get("token") or "")
        yield enrich(event) if enrich else event

    response_obj = None
    try:
        agent_result = await task
        if agent_result.ok:
            final_response = str(agent_result.agent_output)
            response_obj = getattr(agent_result, "response", None)
            final_ok = True
        else:
            final_error = agent_result.error or "Agent returned no output"
    except asyncio.CancelledError:
        pass
    except Exception as e:
        final_error = str(e)

    if not final_response and response_obj is not None:
        # Structured-only reply (e.g. just a buttons block): the answer IS the UI.
        # Use the response's clean fallback text, never the raw token stream —
        # that still holds the <<<ui>>> block and any intermediate tool-call
        # chatter from earlier LLM turns, which would otherwise show in the bubble.
        final_response = getattr(response_obj, "fallback_text", "") or ""
    elif not final_response and token_parts:
        # No structured response and no AgentFinish text: fall back to the streamed
        # tokens, but strip any UI block so raw markers never leak into the bubble.
        from agents.agent_response import parse_agent_response
        final_response, _ = parse_agent_response("".join(token_parts))
    if not final_response and response_obj is None:
        final_response = "(no textual output)"

    # Service entities the run's tools touched — a task it created, a view it
    # edited, a file it wrote. Each has a page in the dashboard that the reply
    # text alone gives no way to reach, so they are resolved into link payloads
    # and travel with the message.
    if final_ok and entity_sink:
        from common.entity_links import entity_payloads
        try:
            result.entities = entity_payloads(entity_sink.records())
        except Exception:
            result.entities = []

    usage = {
        "inbound_tokens": callback.prompt_tokens,
        "outbound_tokens": callback.completion_tokens,
        "total_tokens": callback.total_tokens,
        # Subset of inbound served from the provider's prompt cache; priced at
        # the model's cached rate rather than its input rate.
        "cached_tokens": getattr(callback, "cached_prompt_tokens", 0),
        # How full the model's context was at this turn's largest call, so a
        # surface that missed the streamed usage events (a reload, Telegram)
        # still knows where the conversation stands.
        **callback.context_usage(),
    }
    duration_ms = int((time.perf_counter() - started_ts) * 1000)

    result.ok = final_ok
    result.response = final_response
    result.response_obj = response_obj
    result.error = final_error
    result.stopped = (get_run(run_id) or {}).get("status") in ("stop", "stopped") or callback.cancelled
    result.duration_ms = duration_ms
    result.usage = usage
    last_struct = getattr(callback, "_last_prompt_struct", None) or {}
    # The prompt the agent ran on folds prior session turns into one string
    # (build_chat_context). Recover them as dedicated history blocks so the
    # stored input context shows the previous user/assistant turns and a clean
    # latest user message — never the agent's own intra-run loop output ("middle
    # response tokens"), which lives only in the live bubble and thinking trace.
    from chat.context import split_embedded_history
    prompt_text = last_struct.get("user_message") or full_prompt
    history, user_message = split_embedded_history(prompt_text)
    structured_response = None
    if response_obj is not None:
        try:
            structured_response = response_obj.to_payload() if hasattr(response_obj, "to_payload") else None
        except Exception:
            structured_response = None
    # Canonical structured payload (see common.run_payloads): the input context
    # is stored as blocks, the response as {text, structured}, and the trace as
    # reasoning — identical shape to subprocess/node/docker runs.
    result.process_payload = {
        "input_context": {
            "system_prompt": last_struct.get("system_prompt", ""),
            "history": history,
            "user_message": user_message,
        },
        "response": {"text": final_response, "structured": structured_response},
        "tool_calls": callback.tool_history,
        "reasoning": callback.thinking_history,
        "llm_invocations": getattr(callback, "llm_invocations", []),
        "llm_raw_responses": callback.llm_invoke_responses,
        "artifacts": callback.artifact_history,
        "entities": result.entities,
        "token_usage": usage,
        "duration_ms": duration_ms,
    }
