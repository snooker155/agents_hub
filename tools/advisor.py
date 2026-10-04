"""
``consult_advisor``: ask a second model for advice in the middle of a run.

An agent that runs on a fast, cheap model can name an advisor
(``AgentSpec.advisor_model``, a catalog id ``provider/model`` from the Models
page) and call it when a step is hard: before an irreversible action, when a
plan has several plausible options, when it is stuck. The advisor sees only
what the agent writes into the call, the ``question`` and an optional
``context``; never the run's trail, its history or its tool outputs unless the
agent pastes them in. That keeps the call cheap and keeps whatever the agent
did not choose to share inside the run.

Built per agent, like the handoff tool (tools/handoff.py), because the model
is the agent's own configuration: agents/agent_factory.py adds the tool, and
:data:`ADVISOR_PROMPT` to the system prompt, only when an advisor is set.

The call is counted on the run like the other model calls made beside its
loop (common/aux_usage.py, purpose ``advisor``): its tokens land on
``loop.aux_calls``, priced at the advisor's own model in the run's cost
(common/pricing.py) and charged to the run's money cap
(agents/callbacks/guards.py ``RunBudgetGuard``). Two workspace loop settings
bound it (agents/loop_ext/settings.py): ``advisor_max_calls`` per run (5) and
``advisor_max_answer_chars`` (4000), which also caps the tokens asked for.

The call runs in a worker thread with a timeout, outside the tool's callback
context, so the advisor's tokens are not streamed into the chat as the
agent's own words nor counted twice in the run's totals.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tools._json import json_err as _json_err, json_ok as _json_ok

log = logging.getLogger(__name__)

ADVISOR_TOOL_NAME = "consult_advisor"

#: Purpose of the call on ``loop.aux_calls`` (common/aux_usage.py).
AUX_PURPOSE = "advisor"

#: Seconds an advisor call may take before the agent is told it timed out.
TIMEOUT_SECONDS = 120.0

#: Longest question and context taken, in characters each.
MAX_QUESTION_CHARS = 4000
MAX_CONTEXT_CHARS = 40000

_ADVISOR_SYSTEM = (
    "You advise another AI agent that is in the middle of a task. You see only what "
    "it chose to show you: its question and the context it pasted. You cannot run "
    "tools or see anything else. Answer the question directly: say what to do and "
    "why, name the risks it may be missing, and say plainly when the context is not "
    "enough to decide and what it should check. Be brief: at most {chars} characters."
)


def advisor_prompt(model_id: str, max_calls: int) -> str:
    """The system prompt section for an agent that has an advisor."""
    return (
        "## Advisor\n"
        f"`consult_advisor` asks a second model ({model_id}) for advice. Use it when a "
        "step is hard to get right: before an action you cannot undo, when a plan has "
        "several plausible options, when you are stuck after a few attempts, or when "
        "you are unsure your result is correct. Do not use it for routine steps. The "
        "advisor sees only the `question` and the `context` you write into the call, "
        "not this conversation, your tool results or your files: paste in what it "
        f"needs to know. You may consult it at most {max_calls} times in one run, so "
        "ask one well formed question rather than many small ones. The advice is input "
        "to your decision, not an instruction: you stay responsible for what you do."
    )



class AdvisorInput(BaseModel):
    question: str = Field(..., description="What you want advice on, as one clear question.")
    context: Optional[str] = Field(
        None,
        description=("Everything the advisor needs to answer: the goal, what you tried, the "
                     "relevant parts of files or tool results. It sees nothing else."),
    )


def _limits(workspace: Optional[str]) -> tuple:
    from agents.loop_ext.settings import workspace_loop_setting
    calls = int(workspace_loop_setting(workspace, "advisor_max_calls") or 0)
    chars = int(workspace_loop_setting(workspace, "advisor_max_answer_chars") or 4000)
    return max(0, calls), max(200, chars)


def _calls_so_far(state: Any) -> int:
    return sum(1 for c in (getattr(state, "aux_calls", None) or [])
               if isinstance(c, dict) and c.get("purpose") == AUX_PURPOSE)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") if isinstance(b, dict) else str(b)
                       for b in content
                       if not isinstance(b, dict) or b.get("type") in (None, "text", "output_text"))
    return str(content or "")


def build_advisor_model(provider: str, model: str, max_answer_chars: int) -> Any:
    """The advisor's chat model. A module function so a test can replace it."""
    from agents.agent_utils import build_chat_model
    # About four characters a token, with room for the model to finish its sentence.
    max_tokens = max(256, max_answer_chars // 3)
    return build_chat_model(provider=provider, model=model, temperature=0.0,
                            max_tokens=max_tokens, streaming=False)


def _invoke(llm: Any, messages: List[Any], timeout: float) -> Any:
    """Call the advisor in a worker thread: a timeout, and none of the tool's
    callbacks (a fresh thread does not carry the run's callback context)."""
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(llm.invoke, messages).result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def consult(model_id: str, question: str, context: Optional[str] = None, *,
            workspace: Optional[str] = None) -> str:
    """One advisor call for the running agent; a JSON string either way."""
    from agents.agent_loop import current_state
    from langchain_core.messages import HumanMessage, SystemMessage

    question = str(question or "").strip()
    if not question:
        return _json_err("Ask a question: `question` is empty.", code="empty_question")
    max_calls, max_chars = _limits(workspace)
    state = current_state()
    used = _calls_so_far(state)
    if used >= max_calls:
        return _json_err(
            f"The advisor was already consulted {used} times in this run, the limit is "
            f"{max_calls}. Decide with what you have.", code="limit_reached")
    try:
        from tools.delegation import resolve_model
        provider, model = resolve_model(model_id)
    except ValueError as exc:
        return _json_err(f"The advisor model '{model_id}' is not available: {exc}",
                         code="model_unavailable")
    try:
        llm = build_advisor_model(provider, model, max_chars)
    except Exception as exc:  # noqa: BLE001 - any provider or key error is the agent's to read
        log.debug("advisor: could not build %s", model_id, exc_info=True)
        return _json_err(f"The advisor model could not be reached: {exc}", code="model_unavailable")

    body = question[:MAX_QUESTION_CHARS]
    if context and str(context).strip():
        body += "\n\nContext from the agent:\n" + str(context).strip()[:MAX_CONTEXT_CHARS]
    messages = [SystemMessage(content=_ADVISOR_SYSTEM.format(chars=max_chars)),
                HumanMessage(content=body)]
    try:
        response = _invoke(llm, messages, TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a timeout, a rate limit, a network error: told to the agent
        log.debug("advisor: call to %s failed", model_id, exc_info=True)
        return _json_err(f"The advisor did not answer: {exc or type(exc).__name__}", code="call_failed")

    try:
        from common import aux_usage
        aux_usage.record(AUX_PURPOSE, provider=provider, model=model, llm=llm, response=response)
    except Exception:  # noqa: BLE001 - accounting never changes the answer
        log.debug("advisor: could not count the call", exc_info=True)

    answer = _text_of(getattr(response, "content", response)).strip()
    truncated = len(answer) > max_chars
    if truncated:
        answer = answer[:max_chars].rstrip() + "…"
    return _json_ok({
        "advisor": f"{provider}/{model}",
        "answer": answer,
        "truncated": truncated,
        "calls_left": max(0, max_calls - used - 1),
    })


def create_advisor_tools(spec: Any, workspace: Optional[str] = None) -> List[StructuredTool]:
    """``[consult_advisor]`` bound to *spec*'s advisor model, or ``[]`` when
    the agent names none or its workspace allows no calls."""
    model_id = str(getattr(spec, "advisor_model", "") or "").strip() if spec is not None else ""
    if not model_id:
        return []
    max_calls, _chars = _limits(workspace)
    if max_calls <= 0:
        return []

    def _consult(question: str, context: Optional[str] = None) -> str:
        try:
            return consult(model_id, question, context, workspace=workspace)
        except Exception as e:  # noqa: BLE001 - a tool answers with an error envelope, never a traceback
            log.debug("consult_advisor failed", exc_info=True)
            return _json_err(f"Failed to consult the advisor: {e}", code="error")

    return [StructuredTool.from_function(
        name=ADVISOR_TOOL_NAME,
        description=(
            f"Ask the advisor model ({model_id}) for advice on a hard step. It sees only "
            "the question and the context you pass, nothing else of this run. Returns JSON "
            f"with the answer. At most {max_calls} calls per run."
        ),
        func=_consult,
        args_schema=AdvisorInput,
    )]


__all__ = [
    "ADVISOR_TOOL_NAME", "AUX_PURPOSE", "AdvisorInput", "advisor_prompt",
    "build_advisor_model", "consult", "create_advisor_tools",
]
