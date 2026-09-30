"""
Jobs a runner replica executes for the backend (docs/services.md).

Beside chat turns (chat/turns.py) a runner takes ``job`` messages
(instances/inbox.py KIND_JOB): work the backend used to do in its own process
because it is one agent invocation wrapped in something else, an eval case,
a replay, a task decomposition, a project graph, a playground world or
scenario, the agent part of an entity chat. The backend writes the job
(services/jobs.py), this module runs it here and closes the message with a
result document the backend reads back.

Two jobs:

- ``invoke``: build an agent and run one prompt through it, optionally as a
  run record of its own; the result carries the output, the error, the token
  usage, the tool steps and the run id.
- ``entity_turn``: the streaming agent part of an entity chat
  (chat/entity_chat.py) or the project planner: the same agent build and
  ``arun`` with the chat stream callback, every event posted to the backend
  on ``entity:<run_id>`` (chat/remote_agent.py relays them into the SSE
  response) and an ``entity_result`` event at the end.

Both run under the scopes the backend's process would have had: the acting
user and key, the workspace and project the tools resolve from, the agent's
secrets, and the runner service's money cap.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

STEP_OUTPUT_CHARS = 20_000
OUTPUT_CHARS = 200_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: Any, limit: int) -> str:
    txt = str(text or "")
    return txt if len(txt) <= limit else txt[:limit] + "\n...[truncated]"


def _steps(result: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for step in getattr(result, "steps", None) or []:
        try:
            out.append({"name": str(getattr(step, "name", "") or ""),
                        "output": _clip(getattr(step, "output", ""), STEP_OUTPUT_CHARS)})
        except Exception:  # noqa: BLE001 - one unreadable step is left out
            continue
    return out


@contextlib.contextmanager
def _scopes(payload: Dict[str, Any], args: Dict[str, Any], instance: Dict[str, Any]):
    """Everything the backend's own context would have given the agent."""
    from common import api_keys, identity
    from common.run_budget import turn_cap
    from common.workspace_context import _project_ctx, _workspace_ctx, normalize_project_id

    ws_name = str(args.get("workspace_name") or instance.get("workspace") or "default")
    tokens: List[Any] = []
    if payload.get("user_id"):
        tokens.append(("user", identity.set_current_user(str(payload["user_id"]))))
    if payload.get("key_id"):
        tokens.append(("key", api_keys.set_current_key_id(str(payload["key_id"]))))
    tokens.append(("ws", _workspace_ctx.set(ws_name)))
    tokens.append(("project", _project_ctx.set(normalize_project_id(args.get("project_id")))))
    try:
        from common import secrets as _secrets
        secret_scope = _secrets.activate(ws_name, str(args.get("agent_id") or ""),
                                         payload.get("user_id") or instance.get("started_by"))
    except Exception:  # noqa: BLE001 - no secret store: run without one
        secret_scope = contextlib.nullcontext()
    try:
        with turn_cap(payload.get("budget_usd"), ws_name), secret_scope:
            yield
    finally:
        for which, token in reversed(tokens):
            try:
                if which == "user":
                    identity.reset_current_user(token)
                elif which == "key":
                    api_keys.reset_current_key_id(token)
                elif which == "ws":
                    _workspace_ctx.reset(token)
                else:
                    _project_ctx.reset(token)
            except Exception:  # noqa: BLE001 - a reset that cannot apply changes nothing
                log.debug("could not reset the %s context", which, exc_info=True)


# ── invoke ───────────────────────────────────────────────────────────────────

def _invoke(instance_id: str, instance: Dict[str, Any], args: Dict[str, Any],
            msg_id: str) -> Dict[str, Any]:
    from agents.agent_factory import create_agent
    from agents.agent_invoke import invoke_agent
    from agents.callbacks import RunStopCallback
    from chat.streaming import budget_pause
    from managers import run_manager as rm

    agent_id = str(args.get("agent_id") or "")
    if not agent_id:
        raise ValueError("invoke: agent_id is required")
    prompt = str(args.get("prompt") or "")
    build = dict(args.get("build") or {})
    overrides = dict(args.get("overrides") or {})
    agent = create_agent(agent_id, args.get("workspace"), **overrides, **build)
    provider = getattr(agent, "provider", "") or ""
    model = getattr(agent, "model", "") or ""

    run = args.get("run")
    run_id = str((run or {}).get("run_id") or args.get("run_id") or "") or None
    if run is not None:
        run_id = run_id or rm.new_unique_run_id()
        extra = dict(run.get("extra") or {})
        rm.open_run(
            run_id, agent_id,
            workspace=run.get("workspace"), title=run.get("title"),
            channel=run.get("channel"), execution_mode=run.get("execution_mode"),
            session_type=run.get("session_type"), message_origin=run.get("message_origin"),
            provider=provider, model=model, input=prompt,
            link_to_session=bool(run.get("link_to_session", False)),
            instance_id=instance_id, carrier_run=True,
            **({"service_id": instance.get("service_id")} if instance.get("service_id") else {}),
            **extra,
        )
        rm.seed_run_input_context(run_id, getattr(agent, "system_prompt", "") or "", prompt)

    history = args.get("history") or None
    if history:
        from chat.context import build_history_messages
        from chat.models import ChatHistoryMessage
        history = build_history_messages([ChatHistoryMessage(**h) for h in history])
    callbacks = [RunStopCallback(run_id)] if run_id else []
    invocation = invoke_agent(agent, prompt, run_id=run_id, extra_callbacks=callbacks,
                              history=history)
    result = invocation.result
    capped = budget_pause(result)
    ok = bool(getattr(result, "ok", False)) and capped is None
    error = None
    error_code = None
    if capped is not None:
        error = str(capped.get("reason") or "The run reached its money cap")
        error_code = "budget"
    elif not ok:
        error = str(getattr(result, "error", "") or "agent error")
    status = "completed" if ok else ("stopped" if callbacks and callbacks[0].cancelled else "failed")
    if run is not None:
        if capped is not None:
            rm.close_run(run_id, status="failed", exit_code=1, error=error, process=invocation.process)
        else:
            rm.close_run_from_result(run_id, result, process=invocation.process)
    output = _clip(getattr(result, "agent_output", "") or "", OUTPUT_CHARS) if ok else ""

    log_file = args.get("log_file")
    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write((output + "\n") if ok else f"error: {error}\n")
        except OSError:
            log.debug("job %s: could not write %s", msg_id, log_file, exc_info=True)

    process = invocation.process or {}
    return {
        "run_id": run_id, "ok": ok, "status": status, "output": output, "error": error,
        "error_code": error_code,
        "duration_ms": int(invocation.duration_ms or 0),
        "process": {"token_usage": process.get("token_usage") or {},
                    "duration_ms": process.get("duration_ms")},
        "provider": provider, "model": model,
        "steps": _steps(result),
        "pending_approval": getattr(result, "pending_approval", None) if capped is None else None,
    }


# ── entity turn ──────────────────────────────────────────────────────────────

def _entity_turn(instance_id: str, instance: Dict[str, Any], args: Dict[str, Any],
                 msg_id: str) -> Dict[str, Any]:
    from agents.agent_factory import create_agent
    from agents.callbacks import (
        ChatStreamCallback, RunStopCallback, append_log as _append_log, write_log as _write_log,
    )
    from chat.streaming import budget_pause
    from chat.turns import EventForwarder

    agent_id = str(args.get("agent_id") or "")
    run_id = str(args.get("run_id") or "")
    if not agent_id or not run_id:
        raise ValueError("entity_turn: agent_id and run_id are required")
    prompt = str(args.get("prompt") or "")
    log_file = Path(str(args.get("log_file") or "")) if args.get("log_file") else None
    log_lines: List[str] = list(args.get("log_lines") or [])
    build = dict(args.get("build") or {})
    build.setdefault("streaming", True)
    channel = f"entity:{run_id}"
    forwarder = EventForwarder(instance_id, [channel])
    started = time.perf_counter()
    state: Dict[str, Any] = {"callback": None, "stop": None}

    async def _drive():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        callback = ChatStreamCallback(loop, queue, log_lines, log_file,
                                      session_id=args.get("session_id"), run_id=run_id)
        state["callback"] = callback
        from common.workspace_context import workspace_operating_path
        agent = await asyncio.to_thread(
            create_agent, agent_id,
            workspace=workspace_operating_path(args.get("workspace_name"), args.get("workspace_path")),
            **build)
        provider, model = getattr(agent, "provider", "") or "", getattr(agent, "model", "") or ""
        callback.bind_model(provider, model)
        forwarder.post({"type": "agent", "agent_id": agent_id, "provider": provider,
                        "model": model, "run_id": run_id})
        stop_cb = RunStopCallback(run_id)
        state["stop"] = stop_cb
        task = asyncio.create_task(agent.arun(prompt, callbacks=[callback, stop_cb], run_id=run_id))

        async def _pump() -> None:
            while True:
                event = await queue.get()
                if event is None:
                    return
                if isinstance(event, dict):
                    forwarder.post(dict(event))

        pump = asyncio.create_task(_pump())
        try:
            result = await task
        finally:
            await queue.put(None)
            await pump
        return result, provider, model

    result = None
    provider = model = ""
    error: Optional[str] = None
    try:
        result, provider, model = asyncio.run(_drive())
    except Exception as exc:  # noqa: BLE001 - reported as the turn's failure
        error = str(exc) or exc.__class__.__name__
    callback = state["callback"]
    stopped = bool(state["stop"] and state["stop"].cancelled)

    reply = ""
    error_code = None
    status = "completed"
    if stopped:
        status = "stopped"
    elif result is not None:
        capped = budget_pause(result)
        if capped is not None:
            status, error, error_code = "failed", str(capped.get("reason") or "money cap"), "budget"
        elif getattr(result, "ok", False):
            reply = str(getattr(result, "agent_output", "") or "")
        else:
            status = "failed"
            error = str(getattr(result, "error", None) or "agent returned no output")
    elif error is None:
        status, error = "failed", "agent returned no output"
    elif not stopped:
        status = "failed"

    usage = {
        "inbound_tokens": getattr(callback, "prompt_tokens", 0) if callback else 0,
        "outbound_tokens": getattr(callback, "completion_tokens", 0) if callback else 0,
        "total_tokens": getattr(callback, "total_tokens", 0) if callback else 0,
        "cached_tokens": getattr(callback, "cached_prompt_tokens", 0) if callback else 0,
        "context_window": getattr(callback, "context_window", 0) if callback else 0,
        "context_used": getattr(callback, "max_prompt_tokens", 0) if callback else 0,
    }
    process_payload = {
        "llm_input_context": {
            "system_prompt": ((getattr(callback, "_last_prompt_struct", {}) or {})
                              .get("system_prompt", "") if callback else ""),
            "user_message": str(args.get("user_message") or prompt),
            "response": reply,
            "llm_invocations": getattr(callback, "llm_invocations", []) if callback else [],
        },
        "tool_calls": getattr(callback, "tool_history", []) if callback else [],
        "thinking": getattr(callback, "thinking_history", []) if callback else [],
        "llm_invoke_responses": getattr(callback, "llm_invoke_responses", []) if callback else [],
        "artifacts": getattr(callback, "artifact_history", []) if callback else [],
        "token_usage": usage,
    }
    duration_ms = int((time.perf_counter() - started) * 1000)
    if log_file is not None:
        # The runner owns the log from here: the backend wrote the scaffold,
        # the callback appended the stream, this is the closing block.
        summary_line = (
            f"[message_summary] id={run_id[:8]} "
            f"inbound_tokens={usage['inbound_tokens']} outbound_tokens={usage['outbound_tokens']} "
            f"total_tokens={usage['total_tokens']} "
            f"tool_calls={getattr(callback, 'tool_calls', 0) if callback else 0} "
            f"duration_ms={duration_ms}")
        try:
            _append_log(log_lines, reply or (f"(error: {error})" if error else "(stopped)"), log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + ["", f"Finished: {_now_iso()}", f"Status  : {status}"])
        except Exception:  # noqa: BLE001 - the log is an extra
            log.debug("entity turn %s: log write failed", run_id, exc_info=True)

    outcome = {
        "run_id": run_id, "ok": status == "completed", "status": status, "output": reply,
        "error": error, "error_code": error_code, "usage": usage, "process": process_payload,
        "provider": provider, "model": model, "duration_ms": duration_ms,
        "tool_calls": getattr(callback, "tool_calls", 0) if callback else 0,
        "steps": _steps(result),
        "instance_id": instance_id, "service_id": instance.get("service_id"),
    }
    forwarder.post({"type": "entity_result", **outcome})
    return outcome


JOBS: Dict[str, Callable[[str, Dict[str, Any], Dict[str, Any], str], Dict[str, Any]]] = {
    "invoke": _invoke,
    "entity_turn": _entity_turn,
}


def execute_job(instance_id: str, workspace_abs: Optional[str], message: Dict[str, Any]) -> Optional[str]:
    """Run the job a mailbox message carries and close the message with its
    result. Returns the run id the job recorded, if any."""
    from instances import inbox, registry as instance_registry, store as instance_store

    msg_id = str(message.get("msg_id") or "")
    payload = inbox.payload_of(message)
    job = str(payload.get("job") or "")
    args = dict(payload.get("args") or {})
    handler = JOBS.get(job)
    if handler is None:
        inbox.finish(msg_id, error=f"unknown job: {job!r}")
        return None
    instance = instance_store.get(instance_id) or {}
    if not args.get("workspace") and workspace_abs and job == "invoke":
        args["workspace"] = workspace_abs
    log.info("job %s (%s) starting on %s", msg_id[:12], job, instance_id)
    instance_registry.mark_active(instance_id, None, f"{job}: {args.get('agent_id') or ''}"[:200])
    run_id: Optional[str] = None
    try:
        with _scopes(payload, args, instance):
            result = handler(instance_id, instance, args, msg_id)
        run_id = str(result.get("run_id") or "") or None
        inbox.finish(msg_id, result=result)
    except Exception as exc:  # noqa: BLE001 - the failure is the job's result
        log.warning("job %s (%s) failed: %s", msg_id[:12], job, exc)
        inbox.finish(msg_id, error=str(exc) or exc.__class__.__name__)
    finally:
        # A job that opened no run record leaves the instance active otherwise.
        try:
            from instances import registry
            if not run_id:
                registry.mark_standby(instance_id, "idle — waiting for work")
        except Exception:  # noqa: BLE001 - the state label is cosmetic; the next claim fixes it
            log.debug("could not mark %s standby", instance_id, exc_info=True)
    return run_id


__all__ = ["execute_job", "JOBS"]
