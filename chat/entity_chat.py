"""
Entity build chats — one conversation that edits one service entity.

The project graph has had this for a while: a chat pinned to the thing it
builds, where the agent's tool calls land on the object in front of you rather
than in a reply you then have to apply. This module is that mechanism, made
reusable, so a scenario and a loop get the same surface without a third copy of
the plumbing.

What a caller supplies is an :class:`EntityChatSpec` (which agent, which kind,
how to title the session) and a **prompt builder** — a function that renders the
entity's current state plus the conversation so far into one turn's prompt. The
prompt is the only genuinely per-entity part; everything below it (the run
record, the chat log scaffold, streaming, persistence, cancellation) is the same
for every kind.

Three properties are deliberate and worth keeping if this is ever changed:

* **The run is detached from the SSE connection.** Leaving the page must not
  abort the agent or skip persistence, so the worker runs as its own task and
  the response generator only relays what it puts on a queue.
* **Every turn is a run record.** The turn shows up in Messages with its own log
  and process trace, the same as any other chat — an entity chat is not a
  side-channel that escapes the ledger.
* **Stopping cancels the agent, not the request.** Because the worker is
  detached, closing the browser cannot stop it; :func:`cancel_entity_runs` is
  the only thing that can.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from chat.errors import error_event
from chat import remote_agent

log = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

#: Tool-call / reasoning channel artifacts some local models (gpt-oss "harmony"
#: format) leak into their final text. When present the reply is unusable, so
#: the caller falls back to a summary of what actually changed.
_REPLY_GARBAGE_MARKERS = (
    "to=functions.", "<|constrain|>", "<|channel|>", "<|call|>",
    "<|start|>", "<|end|>", "commentary<|", "assistantfinal",
)

_STREAM_DONE = object()



#: Strong refs to detached workers — asyncio only keeps weak ones, so without
#: this a long build could be garbage-collected mid-run.
_BG_RUNS: set = set()

#: In-flight agent tasks per entity, so a Stop button can cancel one.
_ACTIVE_RUNS: Dict[str, asyncio.Task] = {}


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_agent_reply(text: str) -> str:
    t = (text or "").strip()
    if not t or any(m in t for m in _REPLY_GARBAGE_MARKERS):
        return ""
    return t


# ── run registry ──────────────────────────────────────────────────────────────

def _run_key(kind: str, entity_id: str) -> str:
    return f"{kind}:{entity_id}"


def register_entity_run(kind: str, entity_id: str, task: asyncio.Task) -> None:
    key = _run_key(kind, entity_id)
    _ACTIVE_RUNS[key] = task
    task.add_done_callback(
        lambda _t, k=key: (_ACTIVE_RUNS.pop(k, None)
                           if _ACTIVE_RUNS.get(k) is _t else None)
    )


def entity_run_active(kind: str, entity_id: str) -> bool:
    """Is a turn in flight for this entity? Switching threads mid-run would file
    the running turn under whichever thread happened to be live when it ended,
    so the session picker asks first."""
    task = _ACTIVE_RUNS.get(_run_key(kind, entity_id))
    return bool(task and not task.done())


def cancel_entity_runs(kind: str, entity_id: str) -> int:
    """Cancel the in-flight build run for one entity. Returns how many stopped."""
    key = _run_key(kind, entity_id)
    task = _ACTIVE_RUNS.get(key)
    if task and not task.done():
        task.cancel()
        return 1
    return 0


def spawn_detached(coro) -> asyncio.Task:
    """Run *coro* as a task that survives client disconnects."""
    task = asyncio.create_task(coro)
    _BG_RUNS.add(task)
    task.add_done_callback(_BG_RUNS.discard)
    return task


class RecordingQueue(asyncio.Queue):
    """A queue that also keeps everything put into it, and may answer back.

    Two jobs, both because ``_put`` is the single choke point for ``put`` and
    ``put_nowait`` alike:

    * **Recording** lets the detached worker persist the full display trace
      after the run, even when the client disconnected mid-run and the relay
      stopped reading.
    * **The tap** turns an event into more events. A caller passes a function
      that sees each event and may return extra ones to enqueue behind it —
      what the entity chats use to notice, at every tool boundary, that the
      thing under edit changed, and to say so while the turn is still running
      rather than in one lump at the end.

    The tap is called on the event loop thread (``_emit`` marshals onto it), so
    it must be quick and must not block. Extras are appended directly, one
    level deep: a tap is never asked about its own output.
    """

    def __init__(self, *a, tap: Optional[Callable[[dict], Any]] = None, **k):
        super().__init__(*a, **k)
        self.recorded: list = []
        self._tap = tap
        self._in_tap = False

    def _record(self, item) -> None:
        self.recorded.append(item)

    def _put(self, item):
        super()._put(item)
        self._record(item)
        if self._tap is None or self._in_tap or not isinstance(item, dict):
            return
        self._in_tap = True
        try:
            extras = self._tap(item) or ()
        except Exception:  # noqa: BLE001 — a failing tap must not break the turn
            extras = ()
        finally:
            self._in_tap = False
        for extra in extras:
            super()._put(extra)
            self._record(extra)


async def relay_queue(queue: asyncio.Queue):
    """Yield SSE frames from *queue* until the done sentinel.

    Tolerates client disconnect: if this generator is cancelled we stop relaying
    but leave the producing worker running.
    """
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_DONE:
                break
            yield sse(item)
    except (asyncio.CancelledError, GeneratorExit):
        return


async def guarded(run_turn, queue: asyncio.Queue):
    """Drive a detached worker, always closing the relay with the sentinel.

    The sentinel terminates the SSE relay even if the worker raised; if the
    client already left it simply sits unread on the queue.
    """
    try:
        await run_turn(queue)
    except Exception as exc:  # noqa: BLE001 — last resort so the stream closes
        try:
            await queue.put({"type": "error", "source": "server", "error": str(exc)})
        except Exception:  # noqa: BLE001 - the stream closes even if the error event cannot be queued
            log.debug("error event could not be queued", exc_info=True)
    finally:
        try:
            queue.put_nowait(_STREAM_DONE)
        except asyncio.QueueFull:
            log.debug("stream sentinel dropped, queue full", exc_info=True)


def settle_tool_step(items: List[Dict[str, Any]], ev: dict) -> bool:
    """Close the latest tool step of a stored trace on its ``tool_end`` or
    ``tool_error``: ``done``, or ``error`` with what went wrong. True when the
    event was a tool ending and a step took it, so no item of its own is added.
    """
    t = ev.get("type")
    if t not in ("tool_end", "tool_error"):
        return False
    # The step of the same tool: a call the trace leaves out (the project
    # graph's own tools) must not settle the step before it.
    tool = ev.get("tool")
    step = next((it for it in reversed(items) if it.get("k") == "tool"), None)
    if step is None or (tool and step.get("tool") != tool):
        return False
    if t == "tool_error" or ev.get("status") == "error":
        step["status"] = "error"
        if t == "tool_error" and ev.get("error"):
            step["error"] = str(ev["error"])
    elif step.get("status") == "running":
        step["status"] = "done"
    return True


def trace_item_for(ev: dict) -> Optional[Dict[str, Any]]:
    """Map one streamed event to a chat feed item, or None to drop it.

    Kept in sync with the onEvent switch in the EntityChat component so a
    reloaded session renders the same as the live run did.
    """
    t = ev.get("type")
    if t == "think":
        content = ev.get("content")
        return {"k": "thinking", "text": content} if content else None
    if t in ("thinking", "native_reasoning"):
        txt = ev.get("message") or ev.get("content") or ""
        # "[llm_start] …" and friends are log markers, not the model's words.
        return {"k": "thinking", "text": txt} if txt and not txt.startswith("[") else None
    if t == "tool_start":
        # The input rides along so a reloaded step reads like the live one did
        # ("modify_world_tool · wld_3f2a"), not as a bare tool name.
        return {"k": "tool", "tool": str(ev.get("tool") or ""),
                "input": ev.get("input"), "status": "done"}
    if t == "tool_error":
        return {"k": "tool", "tool": str(ev.get("tool") or ""), "status": "error",
                "error": str(ev.get("error") or "")}
    if t == "entity_changed":
        return {"k": "entity", "action": ev.get("action") or "updated",
                "kind": ev.get("kind") or "", "label": ev.get("label") or ""}
    if t == "stopped":
        return {"k": "tool", "tool": "stopped by you", "status": "done"}
    if t == "message":
        content = ev.get("content")
        return {"k": "assistant", "text": content} if content else None
    if t == "error":
        return {"k": "error", "text": ev.get("error") or "error"}
    return None


# ── the turn ──────────────────────────────────────────────────────────────────

@dataclass
class EntityChatSpec:
    """Everything about one entity kind's chat that is not the prompt."""

    #: Entity kind — the store key prefix and the conversation id prefix.
    kind: str
    #: The builder agent that runs the turn.
    agent_id: str
    #: Human title for the session, shown in Messages.
    title: str
    #: Workspace the agent runs in (its tools resolve the active one from it).
    workspace: Optional[str] = None
    #: Where the agent's filesystem tools are rooted, when it has any.
    workspace_path: Optional[str] = None
    #: The workspace the conversation's session is filed under, when it is not
    #: the one each turn runs in: the assistant keeps one thread in the
    #: person's home workspace while its turns run where they choose.
    session_workspace: Optional[str] = None
    #: Building a whole entity can mean dozens of tool calls in one run, so the
    #: ceilings are generous by default — a build cut off half-way is worse than
    #: a slow one. There is no repetition ceiling at all: writing twenty files or
    #: adding twenty nodes is one tool called twenty times in a row, which is the
    #: work these agents exist to do, not a runaway loop.
    max_iterations: int = 120
    #: 0 is ``UNLIMITED_TOOL_REPEATS`` (agents.callbacks.guards) — spelled as a
    #: literal so this module keeps its lazy agent imports.
    max_tool_repeats: int = 0
    #: Extra build parameters for ``create_agent``. Used where something the
    #: user is looking at, rather than the agent's configuration, decides how it
    #: is built — the Memory page pins ``memory_pool`` to the open pool so a
    #: question about it is answered from it. They reach the agent cache key, so
    #: two different values are two cached agents rather than one stale one.
    agent_overrides: Dict[str, Any] = field(default_factory=dict)
    #: Kept beside the user's message in the transcript and in its trace item:
    #: the assistant marks a spoken turn (``{"voice": True}``), so the text
    #: version of the thread on the Chat page says which lines were said.
    user_meta: Dict[str, Any] = field(default_factory=dict)
    #: Whether the thread is the substance rather than context around an entity.
    #: Off, the prompt sees the last turns only (``transcript_block``). On, the
    #: older turns are folded into the session's summary after each reply and
    #: the prompt gets that summary plus every turn since: the assistant's thread
    #: is the person's short-term memory, so turn 7 must not simply vanish.
    fold_history: bool = False


async def run_entity_chat_turn(
    queue: asyncio.Queue,
    spec: EntityChatSpec,
    entity_id: str,
    user_message: str,
    build_prompt: Callable[[List[dict]], str],
    *,
    summarize: Optional[Callable[[], str]] = None,
) -> None:
    """Run one turn of an entity build chat, streaming events onto *queue*.

    ``build_prompt`` receives the stored transcript (oldest first, the user's new
    message already appended) and returns the prompt for this turn — the one
    genuinely per-entity part. ``summarize`` is consulted only when the agent
    produced no usable reply, to say what it changed instead of going silent.
    """
    from agents.callbacks import (
        ChatStreamCallback, append_log as _append_log, write_log as _write_log,
    )
    from common.bootstrap import ensure_system_agent
    from common.entity_chat_store import entity_chat_store
    from managers.run_manager import (
        new_unique_run_id as _new_run_id, open_run as _open_run,
        run_log_path as _run_log_path, update_run as _update_run,
    )

    store = entity_chat_store()

    async def emit(payload: Dict[str, Any]) -> None:
        await queue.put(payload)

    # Record the user turn first, then build the prompt from the live entity +
    # the transcript that now includes it.
    store.append_message(spec.kind, entity_id, "user", user_message, meta=spec.user_meta)
    history = store.get_messages(spec.kind, entity_id)

    # Open a run record so the turn appears in Messages, grouped per
    # entity + session epoch. The epoch lets "Clear chat" start a fresh thread
    # (epoch 0 keeps the un-suffixed id, so an existing thread is not split).
    run_id = _new_run_id()
    log_file = _run_log_path(run_id)
    epoch = store.get_session_epoch(spec.kind, entity_id)
    conv_id = f"{spec.kind}chat:{entity_id}" + (f":{epoch}" if epoch else "")
    session_id = None
    try:
        from common.session_service import get_or_create_chat_session
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=spec.title + (f" #{epoch + 1}" if epoch else ""),
            workspace=spec.session_workspace or spec.workspace,
            agent_id=spec.agent_id,
        )
    except Exception:  # noqa: BLE001 - a chat without a stored session still answers
        log.debug("chat session create failed", exc_info=True)
        session_id = None
    prompt = build_prompt(folded_history(history, session_id) if spec.fold_history else history)
    _open_run(run_id, spec.agent_id, task_id=conv_id, session_id=session_id,
              session_type="chat", message_origin=f"{spec.kind}-chat", channel="chat",
              workspace=spec.workspace, title=user_message[:60], log_file=str(log_file))
    # The run id before any token, so a client can refer to the turn while it
    # streams (the assistant reads its first sentences aloud: chat/voice.py).
    await emit({"type": "run", "run_id": run_id})

    # The same chat-log scaffold every chat run writes. The Messages insights
    # view parses chat runs from these headers, so a run without them shows no
    # process trace (mirrors chat.runs.create_chat_run + run_chat_pipeline).
    msg_id = run_id[:8]
    started = _iso()
    log_lines = [
        f"=== Chat message  run_id={run_id} ===",
        f"Started   : {started}",
        f"Agent     : {spec.agent_id}",
        f"Workspace : {spec.workspace or '—'}",
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
    message_started = time.perf_counter()

    reply, status, err = "", "completed", None
    callback = None
    # The agent part on a runner replica (chat/remote_agent.py): what it
    # reported, when the turn ran there rather than in this process.
    remote = None

    def _stopped_reply() -> str:
        return (summarize() if summarize else "") or "Stopped."

    if not ensure_system_agent(spec.agent_id):
        status, err = "failed", f"{spec.agent_id} is not available"
        await emit(error_event("registry", err))
    elif remote_agent.enabled():
        from common.workspace_context import _project_ctx
        task = asyncio.create_task(remote_agent.stream_agent_turn(
            queue, agent_id=spec.agent_id, run_id=run_id, prompt=prompt,
            workspace=spec.workspace, workspace_path=spec.workspace_path,
            project_id=_project_ctx.get(), session_id=session_id,
            log_file=str(log_file), log_lines=log_lines,
            build={"max_tool_repeats": spec.max_tool_repeats,
                   "max_iterations": spec.max_iterations,
                   **dict(spec.agent_overrides or {})},
            user_message=user_message))
        register_entity_run(spec.kind, entity_id, task)
        try:
            remote = await task
        except asyncio.CancelledError:
            status, reply = "stopped", _stopped_reply()
            await emit({"type": "stopped"})
        except Exception as e:  # noqa: BLE001
            status, err = "failed", str(e)
            await emit(error_event("agent", err))
        if remote is not None:
            _update_run(run_id, {"provider": remote.provider, "model": remote.model,
                                 **remote.run_fields()})
            if remote.status == "stopped":
                status, reply = "stopped", _stopped_reply()
                await emit({"type": "stopped"})
            elif remote.ok:
                reply = clean_agent_reply(remote.agent_output)
            else:
                status = "failed"
                err = remote.error or "agent returned no output"
                await emit(error_event("agent", err))
    else:
        try:
            from agents.agent_factory import create_agent

            loop = asyncio.get_running_loop()
            callback = ChatStreamCallback(loop, queue, log_lines, log_file,
                                          session_id=session_id)
            # The secret scope is entered before the run task is created:
            # a task copies the context it is created in, so the tools the
            # agent calls inside ``arun`` see the same scope a subprocess run
            # would get through its environment (docs/secrets.md).
            from common import secrets as _secrets
            from common.identity import current_user_id
            with _secrets.activate(spec.workspace or "", spec.agent_id, current_user_id()):
                # The workspace's model choice (the header's pick) applies
                # through the path, so a chat with only a name gets its folder.
                from common.workspace_context import workspace_operating_path
                agent = await asyncio.to_thread(
                    create_agent, spec.agent_id,
                    workspace=workspace_operating_path(spec.workspace, spec.workspace_path),
                    streaming=True,
                    max_tool_repeats=spec.max_tool_repeats,
                    max_iterations=spec.max_iterations,
                    **dict(spec.agent_overrides or {}),
                )
                _update_run(run_id, {"provider": agent.provider or "",
                                     "model": agent.model or ""})
                callback.bind_model(agent.provider or "", agent.model or "")
                await emit({"type": "agent", "agent_id": spec.agent_id,
                            "provider": agent.provider or "", "model": agent.model or ""})

                # The stream emitter on the context the task copies, as the
                # main chat does (chat/pipelines.py): a delegated agent's
                # events and an approval or connection card reach this stream
                # (common/tool_approvals.py chat_context).
                from common import stream_sink
                _stream_token = stream_sink.set_emitter(getattr(callback, "emit_external", None))
                try:
                    task = asyncio.create_task(agent.arun(prompt, callbacks=[callback]))
                finally:
                    stream_sink.reset_emitter(_stream_token)
            register_entity_run(spec.kind, entity_id, task)

            # The callback pushes token/tool events straight onto `queue` and the
            # relay drains it concurrently, so here we only await the agent.
            try:
                res = await task
            except asyncio.CancelledError:
                # Stop button: keep whatever the agent already wrote and close
                # the run cleanly (the worker itself is not cancelled).
                status = "stopped"
                reply = _stopped_reply()
                await emit({"type": "stopped"})
                res = None
            if res is not None:
                from chat.streaming import budget_pause
                capped = budget_pause(res)
                if capped is not None:
                    status = "failed"
                    err = str(capped.get("reason") or "The turn reached its money cap")
                    await emit(error_event("agent", err))
                elif getattr(res, "ok", False):
                    reply = clean_agent_reply(str(res.agent_output))
                else:
                    status = "failed"
                    err = getattr(res, "error", None) or "agent returned no output"
                    await emit(error_event("agent", err))
        except Exception as e:  # noqa: BLE001
            from agents.callbacks.chat_stream import describe_llm_error
            status, err = "failed", describe_llm_error(
                e, getattr(callback, "bound_provider", ""), getattr(callback, "bound_model", ""))
            await emit(error_event("agent", err))

    # Always produce a result: the agent's words if it has any, otherwise what
    # it changed — a run that only renamed something should not read as silent.
    if not reply:
        reply = (summarize() if summarize else "") or (
            f"Couldn't complete the request: {err}" if err
            else "I didn't change anything — could you say what you'd like me to do?"
        )

    usage = {
        "inbound_tokens": getattr(callback, "prompt_tokens", 0) if callback else 0,
        "outbound_tokens": getattr(callback, "completion_tokens", 0) if callback else 0,
        "total_tokens": getattr(callback, "total_tokens", 0) if callback else 0,
        "cached_tokens": getattr(callback, "cached_prompt_tokens", 0) if callback else 0,
        "context_window": getattr(callback, "context_window", 0),
        "context_used": getattr(callback, "max_prompt_tokens", 0),
    }
    process_payload = {
        "llm_input_context": {
            "system_prompt": ((getattr(callback, "_last_prompt_struct", {}) or {})
                              .get("system_prompt", "") if callback else ""),
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
        process_payload.setdefault("llm_input_context", {})
        process_payload["llm_input_context"] = {
            **process_payload["llm_input_context"], "user_message": user_message, "response": reply}

    finished = _iso()
    duration_ms = int((time.perf_counter() - message_started) * 1000)
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

    store.append_message(spec.kind, entity_id, "assistant", reply)
    thread = list(history) + [{"role": "assistant", "content": reply}]
    if spec.fold_history and session_id and fold_due(session_id, thread):
        # After the reply, off the turn's path: a spoken answer must not wait
        # for a summary, and the next turn reads whatever this one stored.
        provider = (remote.provider if remote is not None
                    else getattr(callback, "bound_provider", "")) or ""
        model = (remote.model if remote is not None
                 else getattr(callback, "bound_model", "")) or ""
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(fold_session_history, session_id, thread,
                                               provider, model),
                         name=f"fold-{session_id[:8]}", daemon=True).start()
    await emit({"type": "message", "role": "assistant", "content": reply, "run_id": run_id})
    # The turn's context fill travels with the terminal event so the panel can
    # show how much room is left before the next turn, not after it fails.
    await emit({"type": "done", "run_id": run_id, "usage": usage})

    # Persist this turn's display trace from the recording queue, so it is
    # captured even when the client disconnected mid-run.
    turn_items: List[Dict[str, Any]] = [{"k": "user", "text": user_message, **(spec.user_meta or {})}]
    for ev in list(getattr(queue, "recorded", [])):
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "tool_end" and ev.get("memory"):
            # Where a remember or forget call wrote, on the step it closes.
            step = next((it for it in reversed(turn_items) if it.get("k") == "tool"), None)
            if step is not None:
                step["memory"] = ev["memory"]
        if settle_tool_step(turn_items, ev):
            continue
        item = trace_item_for(ev)
        if item:
            turn_items.append(item)
    try:
        store.append_trace(spec.kind, entity_id, turn_items)
    except Exception:  # noqa: BLE001 - a trace that cannot be saved must not fail the answered turn
        log.debug("trace append failed", exc_info=True)


def transcript_block(history: List[dict], limit: Optional[int] = 12) -> str:
    """The recent conversation, rendered for a prompt.

    Bounded on purpose: the entity's own state is re-rendered in full every
    turn, so old turns are context, not the source of truth, and an unbounded
    transcript is the thing that makes turn 30 cost ten times turn 3. A thread
    that folds its history (``folded_history``) is already bounded and passes
    ``limit=None``; its summary entry comes first.
    """
    recent = [m for m in history if m.get("role") in ("user", "assistant")]
    if limit:
        recent = recent[-limit:]
    summaries = [m for m in history if m.get("role") == "summary" and m.get("content")]
    if not recent and not summaries:
        return ""
    lines = [f"(Earlier in this conversation, summarised: {m['content']})" for m in summaries]
    for m in recent:
        who = "User" if m.get("role") == "user" else "You"
        lines.append(f"{who}: {m.get('content') or ''}")
    return "\n".join(lines)


# ── a thread that is the person's short-term memory ──────────────────────────
# The assistant's thread folds instead of forgetting: after a reply, the turns
# past FOLD_BUDGET_CHARS are summarised into the session (the same summary
# record the main chat's compaction keeps), and the next prompt is that summary
# plus every turn since it. The budget is far below the model's window on
# purpose: the whole tail is re-sent on every turn, voice included.

#: Verbatim conversation a turn may carry before the next reply folds it.
FOLD_BUDGET_CHARS = 24_000
#: What a fold leaves verbatim, so one is not due again on the next turn.
FOLD_TARGET_CHARS = 10_000
#: Ceiling on the verbatim tail when no fold has caught up yet (a long thread
#: from before folding, a summary call that failed): the newest turns win.
FOLD_HARD_TAIL_CHARS = 48_000


def _thread_talk(history: List[dict]) -> List[dict]:
    return [m for m in history if m.get("role") in ("user", "assistant")]


def _as_messages(talk: List[dict]) -> list:
    from langchain_core.messages import AIMessage, HumanMessage
    return [HumanMessage(content=str(m.get("content") or "")) if m.get("role") == "user"
            else AIMessage(content=str(m.get("content") or "")) for m in talk]


def _stored_fold(session_id: Optional[str], talk: List[dict]) -> tuple:
    """The session's summary and where, in *talk*, the turns after it start."""
    from chat.compaction import realign_covers_until
    from common.session_service import get_session_summary
    try:
        stored = get_session_summary(session_id) if session_id else {}
    except Exception:  # noqa: BLE001 - no summary reads as nothing folded yet
        stored = {}
    text = str(stored.get("text") or "")
    if not text:
        return "", 0
    start = realign_covers_until(_as_messages(talk), stored.get("covers_until") or 0,
                                 str(stored.get("anchor") or ""))
    return text, start


def folded_history(history: List[dict], session_id: Optional[str]) -> List[dict]:
    """The transcript a folding thread's prompt is built from.

    *history* ends with the person's new message. Returns the stored summary as
    a ``summary`` entry, then every turn after it (the newest ones up to
    ``FOLD_HARD_TAIL_CHARS``), then the new message.
    """
    talk, latest = _thread_talk(history[:-1]), history[-1:]
    text, start = _stored_fold(session_id, talk)
    tail, used = [], 0
    for m in reversed(talk[start:]):
        cost = len(str(m.get("content") or ""))
        if tail and used + cost > FOLD_HARD_TAIL_CHARS:
            break
        tail.insert(0, m)
        used += cost
    return ([{"role": "summary", "content": text}] if text else []) + tail + latest


def fold_due(session_id: Optional[str], history: List[dict]) -> bool:
    """Whether the turns after the session's summary have passed the budget."""
    talk = _thread_talk(history)
    _text, start = _stored_fold(session_id, talk)
    return sum(len(str(m.get("content") or "")) for m in talk[start:]) > FOLD_BUDGET_CHARS


def fold_session_history(session_id: str, history: List[dict],
                         provider: str = "", model: str = "") -> None:
    """Fold a thread's older turns into its session summary once the turns
    after the summary pass ``FOLD_BUDGET_CHARS``. Written by the model the turn
    ran on; a lossy heuristic stands in when that call fails. Never raises."""
    try:
        from chat.compaction import SUMMARY_MAX_TOKENS, compact_history
        from common.session_service import set_session_summary
        if not fold_due(session_id, history):
            return
        talk = _thread_talk(history)
        text, start = _stored_fold(session_id, talk)
        messages = _as_messages(talk)
        llm = None
        try:
            from agents.agent_utils import build_chat_model
            llm = build_chat_model(provider=provider or None, model=model or None,
                                   temperature=0.0, max_tokens=SUMMARY_MAX_TOKENS,
                                   streaming=False)
        except Exception:  # noqa: BLE001 - the heuristic summary stands in
            log.debug("fold: no summarizer model for %s/%s", provider, model, exc_info=True)
        result = compact_history(messages, budget_chars=FOLD_BUDGET_CHARS, summary=text,
                                 covers_until=start, llm=llm, provider=provider,
                                 target_chars=FOLD_TARGET_CHARS)
        if result.changed:
            set_session_summary(session_id, result.summary, result.covers_until,
                                anchor=result.anchor)
    except Exception:  # noqa: BLE001 - a fold that fails leaves the thread as it was
        log.warning("fold of session %s failed", session_id, exc_info=True)


__all__ = [
    "EntityChatSpec",
    "RecordingQueue",
    "SSE_HEADERS",
    "cancel_entity_runs",
    "entity_run_active",
    "fold_due",
    "fold_session_history",
    "folded_history",
    "clean_agent_reply",
    "guarded",
    "relay_queue",
    "run_entity_chat_turn",
    "spawn_detached",
    "sse",
    "settle_tool_step",
    "trace_item_for",
    "transcript_block",
]
