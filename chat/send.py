"""
One blocking chat turn, as a function.

This is the non-streaming single-agent path: build the prompt, open a run,
invoke the agent, record the outcome. It lives here rather than in the route so
that everything able to start a chat turn — the dashboard's
``POST /api/chat/message`` and the terminal client — runs the *same*
orchestration, including the timeout handling and the run bookkeeping that the
Sessions and Messages pages read back.

Errors are raised as :class:`ChatSendError`, which carries the HTTP status the
route used to raise directly, so the route stays a mapping layer and callers
that are not HTTP (the CLI) can render the message however they like.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from agents.callbacks import write_log as _write_log
from agents.agent_factory import create_agent
from managers.run_manager import update_run, get_run_by_id as get_run

from chat.models import ChatRequest
from chat.compaction import compact_for_turn
from chat.context import (
    apply_workspace_ctx,
    build_chat_context,
    build_history_messages,
)
from chat.errors import error_code
from chat.attachments import materialize_attachments
from chat.references import resolve_references
from chat.runs import (
    utc_iso,
    get_pool_id,
    auto_journal,
    agent_overrides,
    validate_chat_request,
    create_chat_run,
)


class ChatSendError(Exception):
    """A chat turn that could not be completed.

    ``status`` mirrors the HTTP status the API reports for this failure, so the
    route can re-raise it unchanged while other callers just read ``detail``.
    """

    def __init__(self, detail: str, status: int = 500):
        super().__init__(detail)
        self.detail = detail
        self.status = status


async def send_chat_message(request: ChatRequest) -> Dict[str, Any]:
    """Run one turn for a single agent and return ``{response, ok, run_id}``.

    Flows and teams are not handled here: they fan out to several agents and
    only make sense over the streaming transport.
    """
    if request.flow_id or request.team_id:
        raise ChatSendError(
            "Flow and team chat require the streaming endpoint (/api/chat/stream)",
            status=400,
        )
    validate_chat_request(request)
    materialize_attachments(request)
    resolve_references(request)
    full_prompt, workspace_abs = build_chat_context(request)
    # Uncapped: compact_for_turn below bounds this against the agent's real
    # model budget, so a flat cut here cannot throw away history compaction
    # would otherwise have folded into a summary. See chat/pipelines.py for
    # the same fix on the streaming path.
    history_messages = build_history_messages(request.history, budget_chars=float("inf"))
    run_id, _, log_file, log_lines, session_id = create_chat_run(request)

    # Propagate workspace to agent tools (e.g. list_tasks) via a context var
    # that is thread-safe and copied into asyncio.to_thread's execution context.
    ws_name = apply_workspace_ctx(request, workspace_abs)

    # The launcher, evals and loops all refuse to start a run once a workspace's
    # hard budget cap is met (common.budget.check_budget); chat was the one
    # surface that could still spend past it turn after turn. Gate here, before
    # the agent is built or invoked, using the same resolved workspace name the
    # run record and journal already use.
    from common.budget import check_budget, BudgetExceededError
    try:
        check_budget(ws_name)
    except BudgetExceededError as e:
        finished = utc_iso()
        _write_log(log_file, log_lines + [f"(budget: {e})", "", f"Finished: {finished}"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                            "exit_code": 1, "error": str(e)})
        raise ChatSendError(str(e), status=402)

    def _run_agent():
        # Timed call + stats go through the shared invocation core (same path as
        # agent_run.py / flow.dispatch); this function keeps its own orchestration
        # (run record, logs, task finalization). catch_exceptions=False so the
        # error-type handling below (timeout / missing YAML / generic) still works.
        # A RunStopCallback is attached so that on a wall-clock timeout we can
        # flip the run status to "stop" and have the in-flight thread abort at its
        # next LLM/tool boundary — asyncio.wait_for cancels the awaiting coroutine
        # but cannot reach into the worker thread by itself.
        from agents.agent_invoke import invoke_agent
        from agents.callbacks import RunStopCallback
        overrides = agent_overrides(request.agent_id)
        agent = create_agent(request.agent_id, workspace=workspace_abs, **overrides)
        # Persist the model/provider actually used for this run so the message
        # record reflects what ran, not the current default at view time.
        update_run(run_id, {"provider": agent.provider or "", "model": agent.model or ""})

        def _invoke(history):
            return invoke_agent(
                agent, full_prompt, history=history, run_id=run_id,
                catch_exceptions=False, extra_callbacks=[RunStopCallback(run_id)],
            ).result

        compaction = compact_for_turn(
            agent=agent, history=history_messages,
            system_prompt=getattr(agent, "system_prompt", "") or "", session_id=session_id,
        )
        if compaction.folded:
            # Appended rather than written past: every later write rebuilds the
            # file from this list, so a line that is not in it is lost.
            log_lines.append(
                f"[compaction] folded={compaction.folded} "
                f"summary_chars={len(compaction.summary)}")
            _write_log(log_file, log_lines)
        result = _invoke(compaction.messages)

        # The provider is the last word on what fits: when it says the turn was
        # too long anyway, fold the history and try the turn once more rather
        # than handing the user an error they can only answer by clearing the chat.
        if not getattr(result, "ok", False) and error_code(getattr(result, "error", "") or ""):
            compaction = compact_for_turn(
                agent=agent, history=history_messages,
                system_prompt=getattr(agent, "system_prompt", "") or "", session_id=session_id,
                force=True,
            )
            if compaction.folded:
                result = _invoke(compaction.messages)
        return result

    from common.config import settings as _cfg
    chat_timeout = max(_cfg.chat_request_timeout, _cfg.llm_request_timeout + 60)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_agent),
            timeout=chat_timeout,
        )
    except asyncio.TimeoutError:
        finished = utc_iso()
        mins = chat_timeout // 60
        _write_log(log_file, log_lines + [f"(timed out after {mins} minutes)", "", f"Finished: {finished}"])
        # Signal the still-running worker thread to abort at its next boundary,
        # then record the terminal state. The thread will not resurrect the
        # record: this call's success/fail update path never runs after the raise.
        update_run(run_id, {"status": "stop"})
        update_run(run_id, {"status": "failed", "finished_at": finished,
                            "exit_code": 1, "error": f"timed out after {mins} minutes"})
        raise ChatSendError(f"Agent timed out after {mins} minutes", status=504)
    except FileNotFoundError:
        finished = utc_iso()
        _write_log(log_file, log_lines + ["(no YAML definition)", "", f"Finished: {finished}"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                            "exit_code": 1, "error": "no YAML definition"})
        raise ChatSendError(
            f"Agent '{request.agent_id}' has no YAML definition and cannot run in chat mode",
            status=400,
        )
    except Exception as e:
        finished = utc_iso()
        _write_log(log_file, log_lines + [f"(error: {e})", "", f"Finished: {finished}"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                            "exit_code": 1, "error": str(e)})
        raise ChatSendError(str(e), status=500)

    finished = utc_iso()

    current = get_run(run_id) or {}
    if current.get("status") == "stop":
        _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
        update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "stopped by user"})
        return {"response": "Stopped by user", "ok": False, "run_id": run_id}

    if result.ok:
        response_text = str(result.agent_output)
        _write_log(log_file, log_lines + [response_text, "", f"Finished: {finished}", "Status  : completed"])
        update_run(run_id, {"status": "completed", "finished_at": finished, "exit_code": 0})
        auto_journal(request.agent_id, get_pool_id(request.agent_id, request.workspace), request.message, response_text, run_id)
        return {"response": response_text, "ok": True, "run_id": run_id}

    error_text = result.error or "Agent returned no output"
    _write_log(log_file, log_lines + [f"(error: {error_text})", "", f"Finished: {finished}", "Status  : failed"])
    update_run(run_id, {"status": "failed", "finished_at": finished,
                        "exit_code": 1, "error": error_text})
    return {"response": f"Error: {error_text}", "ok": False, "run_id": run_id}


def send_chat_message_sync(request: ChatRequest) -> Dict[str, Any]:
    """Blocking wrapper for callers with no event loop of their own (the CLI)."""
    return asyncio.run(send_chat_message(request))
