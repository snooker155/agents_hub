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
from typing import Any, Dict, List, Optional, Tuple

from agents.callbacks import write_log as _write_log
from agents.agent_factory import create_agent
from managers.run_manager import update_run, get_run_by_id as get_run

from chat import handoff as handoff_mod
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
    request_overrides,
    validate_chat_request,
    create_chat_run,
)


class ChatSendError(Exception):
    """A chat turn that could not be completed.

    ``status`` mirrors the HTTP status the API reports for this failure, so the
    route can re-raise it unchanged while other callers just read ``detail``.
    """

    def __init__(self, detail: str, status: int = 500, refusal: Optional[Dict[str, Any]] = None):
        super().__init__(detail)
        self.detail = detail
        self.status = status
        # The structured form of a refusal the person can act on (chat/refusals.py).
        self.refusal = refusal


async def send_chat_message(request: ChatRequest) -> Dict[str, Any]:
    """Run one turn for a single agent and return ``{response, ok, run_id}``.

    Flows and teams are not handled here: they fan out to several agents and
    only make sense over the streaming transport.

    When the agent hands the conversation over (tools/handoff.py), the agent it
    names answers in the same call, as a run of its own, exactly as on the
    streaming path; the result is then the last agent's answer, plus
    ``agent_id`` (who answered, the target for the next turn), ``handoff``
    (the last handoff) and ``handoffs`` (all of them, each with the handing
    agent's own reply as ``from_response``).
    """
    if request.flow_id or request.team_id:
        raise ChatSendError(
            "Flow and team chat require the streaming endpoint (/api/chat/stream)",
            status=400,
        )
    from chat import routing
    if routing.enabled():
        # The turn runs on a service replica (docs/services.md): drain the
        # relayed stream and answer in this function's shape.
        return await _send_routed(request)
    validate_chat_request(request)
    materialize_attachments(request)
    resolve_references(request)
    full_prompt, workspace_abs = build_chat_context(request)
    # Uncapped: compact_for_turn below bounds this against the agent's real
    # model budget, so a flat cut here cannot throw away history compaction
    # would otherwise have folded into a summary. See chat/pipelines.py for
    # the same fix on the streaming path.
    history_messages = build_history_messages(request.history, budget_chars=float("inf"))
    run = create_chat_run(request)
    run_id, _, log_file, log_lines, _session_id = run

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
        from chat.refusals import refusal_of
        raise ChatSendError(str(e), status=402, refusal=refusal_of(e, agent_id=request.agent_id))

    step_request, prompt, chain = request, full_prompt, [request.agent_id]
    received: Optional[handoff_mod.HandoffIntent] = None
    handoffs: List[Dict[str, Any]] = []
    replies: List[Dict[str, str]] = []
    while True:
        result, intent = await _run_one(
            step_request, prompt=prompt, history=history_messages, workspace_abs=workspace_abs,
            ws_name=ws_name, run=run, chain=chain, received=received,
        )
        refused = handoff_mod.refusal_by_turn(chain, intent) if intent is not None else None
        if intent is None or refused:
            if handoffs:
                result.update(_handoff_fields(step_request.agent_id, handoffs))
            return result

        # The conversation changes hands: the receiving agent answers in this
        # same call, on a run of its own (see chat.handoff).
        replies.append({"agent_name": intent.from_agent_name, "text": result["response"]})
        receiving, base_prompt, next_run = handoff_mod.open_receiving_run(
            step_request, intent, handing_run_id=run[0])
        update_run(run[0], {"handoff": handoff_mod.handing_record(intent, next_run[0])})
        handoffs.append(handoff_mod.handoff_event(
            intent, run_id=run[0], next_run_id=next_run[0], from_response=result["response"]))
        step_request, run, received = receiving, next_run, intent
        chain = [*chain, intent.to_agent_id]
        prompt = handoff_mod.receiving_prompt(handoff_mod.handoff_note(intent, replies=replies), base_prompt)
        # Each run spends on its own; the cap is checked before this one starts.
        try:
            check_budget(ws_name)
        except BudgetExceededError as e:
            finished = utc_iso()
            _write_log(run[2], run[3] + [f"(budget: {e})", "", f"Finished: {finished}"])
            update_run(run[0], {"status": "failed", "finished_at": finished,
                                "exit_code": 1, "error": str(e)})
            from chat.refusals import refusal_of
            refusal = refusal_of(e, agent_id=step_request.agent_id)
            return {"response": f"Error: {e}", "ok": False, "run_id": run[0],
                    **({"refusal": refusal} if refusal else {}),
                    **_handoff_fields(step_request.agent_id, handoffs)}


def _handoff_fields(agent_id: Optional[str], handoffs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What a result carries after a handoff: who answered, the last handoff
    and every handoff of the turn, in the stream's ``done`` shape."""
    return {
        "agent_id": agent_id,
        "handoff": handoff_mod.done_fields(handoffs[-1]),
        "handoffs": [handoff_mod.done_fields(h) for h in handoffs],
    }


async def _run_one(request: ChatRequest, *, prompt: str, history: list,
                   workspace_abs: Optional[str], ws_name: Optional[str], run: tuple,
                   chain: List[str], received: Optional[handoff_mod.HandoffIntent],
                   ) -> Tuple[Dict[str, Any], Optional[handoff_mod.HandoffIntent]]:
    """One agent's run of a blocking turn: ``({response, ok, run_id}, handoff)``.

    ``handoff`` is the handoff the agent recorded when it gave the
    conversation away, None otherwise. Raises :class:`ChatSendError` for a
    timeout, a missing definition or a failure outside the agent's own result.
    """
    run_id, _, log_file, log_lines, session_id = run
    handoff_sink = handoff_mod.HandoffSink(
        agent_id=request.agent_id or "", chain=list(chain), depth=len(chain) - 1,
        max_depth=handoff_mod.max_depth(), workspace=ws_name,
    )

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
        # The record's model cascade, then this turn's version pin and
        # per-run overrides (chat/runs.py request_overrides), as the
        # streaming pipeline builds it.
        overrides = {**agent_overrides(request.agent_id), **request_overrides(request)}
        agent = create_agent(request.agent_id, workspace=workspace_abs, **overrides)
        # Persist the model/provider actually used for this run so the message
        # record reflects what ran, not the current default at view time.
        update_run(run_id, {"provider": agent.provider or "", "model": agent.model or ""})
        handoff_sink.summarizer = agent
        agent_history = handoff_mod.history_for(agent, history, received)
        # A receiving run with a narrowed history must not get the whole
        # conversation back through the session's stored summary.
        compaction_session = (session_id if received is None
                              or handoff_mod.uses_session_summary(received.history_filter)
                              else None)

        def _invoke(messages):
            return invoke_agent(
                agent, prompt, history=messages, run_id=run_id,
                catch_exceptions=False, extra_callbacks=[RunStopCallback(run_id)],
            ).result

        compaction = compact_for_turn(
            agent=agent, history=agent_history,
            system_prompt=getattr(agent, "system_prompt", "") or "", session_id=compaction_session,
        )
        if compaction.folded:
            # Appended rather than written past: every later write rebuilds the
            # file from this list, so a line that is not in it is lost.
            log_lines.append(
                f"[compaction] folded={compaction.folded} "
                f"summary_chars={len(compaction.summary)}")
            _write_log(log_file, log_lines)
        handoff_sink.set_conversation(compaction)
        result = _invoke(compaction.messages)

        # The provider is the last word on what fits: when it says the turn was
        # too long anyway, fold the history and try the turn once more rather
        # than handing the user an error they can only answer by clearing the chat.
        if not getattr(result, "ok", False) and error_code(getattr(result, "error", "") or ""):
            compaction = compact_for_turn(
                agent=agent, history=agent_history,
                system_prompt=getattr(agent, "system_prompt", "") or "",
                session_id=compaction_session, force=True,
            )
            if compaction.folded:
                handoff_sink.set_conversation(compaction)
                result = _invoke(compaction.messages)
        return result

    from common.config import settings as _cfg
    chat_timeout = max(_cfg.chat_request_timeout, _cfg.llm_request_timeout + 60)
    def _run_agent_scoped():
        # The chat turn runs in this process, so the agent's secrets cannot
        # arrive through the environment the way a subprocess run's do. The
        # active secret scope binds them for the thread instead: a tool asks
        # common.secrets.get(name) and receives only what this agent declares,
        # resolved for the person who sent the message (docs/secrets.md).
        from common import secrets as _secrets
        from common.identity import current_user_id
        with _secrets.activate(ws_name, request.agent_id, current_user_id()):
            return _run_agent()

    # The handoff tool reads the turn and records its decision on this sink
    # (chat/handoff.py); asyncio.to_thread copies the context it is set in.
    _handoff_token = handoff_mod.set_sink(handoff_sink)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_agent_scoped),
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
        from chat.refusals import refusal_of
        raise ChatSendError(str(e), status=500, refusal=refusal_of(e, agent_id=request.agent_id))
    finally:
        handoff_mod.reset_sink(_handoff_token)

    finished = utc_iso()

    current = get_run(run_id) or {}
    if current.get("status") == "stop":
        _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
        update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "stopped by user"})
        return {"response": "Stopped by user", "ok": False, "run_id": run_id}, None

    from chat.streaming import budget_pause
    capped = budget_pause(result)
    if capped is not None:
        error_text = str(capped.get("reason") or "The turn reached its money cap")
        _write_log(log_file, log_lines + [f"(budget: {error_text})", "", f"Finished: {finished}", "Status  : failed"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                            "exit_code": 1, "error": error_text})
        from chat.refusals import KIND_TURN, budget_refusal
        return {"response": f"Error: {error_text}", "ok": False, "run_id": run_id,
                "error_code": "budget",
                "budget": {"spent_usd": capped.get("spent_usd"), "limit_usd": capped.get("limit_usd")},
                "refusal": budget_refusal(KIND_TURN, spent_usd=capped.get("spent_usd"),
                                          limit_usd=capped.get("limit_usd"), message=error_text,
                                          agent_id=request.agent_id)}, None

    if result.ok:
        response_text = str(result.agent_output)
        intent = handoff_sink.intent
        handoff_line = ([f"[handoff] to={intent.to_agent_id} history={intent.history_filter} "
                         f"reason={intent.reason}"] if intent is not None else [])
        _write_log(log_file, log_lines + [response_text, *handoff_line, "", f"Finished: {finished}", "Status  : completed"])
        update_run(run_id, {"status": "completed", "finished_at": finished, "exit_code": 0})
        auto_journal(request.agent_id, get_pool_id(request.agent_id, request.workspace), request.message, response_text, run_id)
        return {"response": response_text, "ok": True, "run_id": run_id}, intent

    error_text = result.error or "Agent returned no output"
    _write_log(log_file, log_lines + [f"(error: {error_text})", "", f"Finished: {finished}", "Status  : failed"])
    update_run(run_id, {"status": "failed", "finished_at": finished,
                        "exit_code": 1, "error": error_text})
    return {"response": f"Error: {error_text}", "ok": False, "run_id": run_id}, None


async def _send_routed(request: ChatRequest) -> Dict[str, Any]:
    """The blocking turn when chat turns run on service replicas: the same
    events the streaming path relays, folded into ``{response, ok, run_id}``
    (plus the handoff fields when the conversation changed hands)."""
    from fastapi import HTTPException
    from chat.pipelines import run_chat_pipeline
    from common.config import settings as _cfg

    chat_timeout = max(_cfg.chat_request_timeout, _cfg.llm_request_timeout + 60)
    state: Dict[str, Any] = {"done": None, "handoffs": []}

    async def _drain() -> None:
        async for event in run_chat_pipeline(request):
            if not isinstance(event, dict):
                continue
            if event.get("type") == "handoff":
                state["handoffs"].append(event)
            elif event.get("type") == "done" and state["done"] is None:
                state["done"] = event

    try:
        await asyncio.wait_for(_drain(), timeout=chat_timeout)
    except asyncio.TimeoutError:
        raise ChatSendError(f"Agent timed out after {chat_timeout // 60} minutes", status=504)
    except HTTPException as e:
        raise ChatSendError(str(e.detail), status=int(e.status_code))
    done = state["done"]
    if done is None:
        raise ChatSendError("the turn ended without an answer", status=502)
    if not done.get("ok") and done.get("status"):
        raise ChatSendError(str(done.get("error") or "the agent could not run"),
                            status=int(done["status"]))
    result: Dict[str, Any] = {
        "response": str(done.get("response") or (f"Error: {done.get('error')}" if not done.get("ok") else "")),
        "ok": bool(done.get("ok")),
        "run_id": done.get("run_id"),
    }
    # Why a turn failed, when the reply alone does not say (a money cap, a
    # conversation that outgrew the model), the way the streaming done says it.
    for key in ("error_code", "budget", "refusal"):
        if done.get(key) is not None:
            result[key] = done[key]
    if done.get("handoffs"):
        result.update({"agent_id": done.get("agent_id"), "handoff": done.get("handoff"),
                       "handoffs": done.get("handoffs")})
    return result


def send_chat_message_sync(request: ChatRequest) -> Dict[str, Any]:
    """Blocking wrapper for callers with no event loop of their own (the CLI)."""
    return asyncio.run(send_chat_message(request))
