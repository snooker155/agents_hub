"""
The exit criterion, judged by an agent.

The loop's stopping condition is prose ("the draft reads as publishable and
every claim carries a source"), so nothing but a model can decide whether it
holds. This module asks one — normally the agent on the flow's terminal node,
because that is the participant that has just seen the finished work — and
turns its answer into a :class:`~loops.models.Verdict`.

The evaluating agent does **not** need any of this in its own system prompt.
Evaluation is a role assigned for a single call: the prompt below supplies the
criterion, the work, the score history and the output contract, and the agent's
own instructions stay untouched for the passes where it does its normal job.

Two habits of self-reviewing models are corrected here rather than hoped away:

* they praise their own first draft — so ``min_iterations`` on the loop can
  overrule an immediate "stop", and the prompt tells the judge to grade the work
  against the criterion, not against its own effort;
* they return a number that contradicts their words — so ``verdict`` is binding
  and ``score`` is advisory (see :class:`~loops.models.Verdict`).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from loops.models import Verdict

#: Output contract. Kept small and demanded as bare JSON: every extra field is
#: another thing a mid-sized model gets wrong under a long context.
_CONTRACT = """Reply with a single JSON object and nothing else:

{"score": <0-100>, "verdict": "continue" | "stop", "reason": "<one or two sentences>", "feedback": "<what the next attempt must change; empty when you chose stop>"}"""

_EVAL_TEMPLATE = """You are reviewing work produced by an agent workflow, and this
review is the only thing standing between it and being shipped. For this one
message you are a reviewer — set aside whatever else you were doing.

## The exit criterion this work must meet
{criterion}

## The original request
{goal}

## Attempt {iteration} of at most {max_iterations} — the work to judge
{output}
{history_block}{state_block}
## How to judge
- Grade the work against the criterion above, not against how much effort it took.
- "score" is 0-100: how fully the criterion is met right now.
- Choose "stop" only when the criterion is genuinely met and another attempt
  would not meaningfully improve it. Choose "continue" otherwise.
- When you choose "continue", "feedback" must be concrete and actionable — the
  next attempt receives it verbatim and nothing else of yours. Name what is
  wrong and what to do about it; do not restate the criterion.
- Do not rewrite the work here. Judge it.

{contract}"""

_HISTORY_TEMPLATE = """
## Previous attempts
{lines}
The score is expected to move. If it has stalled across attempts, say so in
"reason" — a loop that cannot improve should stop rather than spend.
"""


def _clip(text: str, limit: int = 24000) -> str:
    """Keep an evaluation prompt bounded. Judging is a whole-work read, so the
    middle is dropped rather than the end: the closing section is usually the
    most revealing part of a draft."""
    text = text or ""
    if len(text) <= limit:
        return text
    head, tail = limit * 2 // 3, limit // 3
    return (
        text[:head]
        + f"\n\n[... {len(text) - limit} characters omitted from the middle ...]\n\n"
        + text[-tail:]
    )


def build_evaluation_prompt(
    *,
    criterion: str,
    goal: str,
    output: str,
    iteration: int,
    max_iterations: int,
    history: Optional[List[Dict[str, Any]]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Assemble the single evaluation message. Pure — no I/O, so it is testable
    and the exact text is inspectable in the iteration record."""
    history_block = ""
    if history:
        lines = "\n".join(
            f"- attempt {h.get('iteration')}: score "
            f"{'—' if h.get('score') is None else h.get('score')} · "
            f"{(h.get('reason') or '').strip()[:200]}"
            for h in history
        )
        history_block = _HISTORY_TEMPLATE.format(lines=lines)

    state_block = ""
    if state:
        try:
            rendered = json.dumps(state, indent=2, ensure_ascii=False, default=str)
        except Exception:
            rendered = str(state)
        state_block = f"\n## Shared state after this attempt\n{_clip(rendered, 4000)}\n"

    return _EVAL_TEMPLATE.format(
        criterion=(criterion or "").strip() or
                  "(none stated — judge whether the original request is fully satisfied)",
        goal=_clip((goal or "").strip() or "(no request recorded)", 4000),
        iteration=iteration,
        max_iterations=max_iterations,
        output=_clip(output) or "(the attempt produced no output at all)",
        history_block=history_block,
        state_block=state_block,
        contract=_CONTRACT,
    )


def parse_verdict(text: str) -> Verdict:
    """Pull ``{score, verdict, reason, feedback}`` out of a model reply.

    Forgiving about the wrapper (prose, code fences) and strict about the
    content, the same way :func:`playground.runner.parse_decision` is. An
    unparseable answer is *not* treated as "stop": a judge that could not make
    itself understood has not approved anything, so the loop continues and the
    raw text is carried into the next attempt's feedback.
    """
    raw = (text or "").strip()
    verdict = Verdict(raw=raw)
    if not raw:
        verdict.error = "evaluator returned nothing"
        verdict.feedback = ""
        return verdict

    candidate = raw
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        candidate = fence.group(1).strip()

    parsed: Any = None
    try:
        parsed = json.loads(candidate)
    except Exception:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
            except Exception:
                parsed = None

    if not isinstance(parsed, dict):
        verdict.error = "no JSON object in the evaluator's reply"
        # The prose still carries the judgement even when the envelope failed;
        # hand it to the next attempt rather than discarding a paid-for opinion.
        verdict.feedback = raw[:4000]
        verdict.reason = "evaluator did not return the required JSON object"
        return verdict

    raw_score = parsed.get("score")
    if isinstance(raw_score, str):
        m = re.search(r"-?\d+(?:\.\d+)?", raw_score)
        raw_score = m.group(0) if m else None
    try:
        verdict.score = None if raw_score is None else max(0.0, min(100.0, float(raw_score)))
    except (TypeError, ValueError):
        verdict.score = None

    decision = str(parsed.get("verdict") or "").strip().lower()
    # Models reach for these words instead of the two we asked for.
    if decision in ("stop", "done", "accept", "accepted", "pass", "complete", "finished"):
        verdict.verdict = "stop"
    else:
        verdict.verdict = "continue"
    verdict.reason = str(parsed.get("reason") or "").strip()
    verdict.feedback = str(parsed.get("feedback") or "").strip()
    return verdict


# ── Choosing the judge ───────────────────────────────────────────────────────

def resolve_final_agent(flow: Dict[str, Any]) -> Optional[str]:
    """The agent on the flow's terminal node — the last agent node in execution
    order. Non-agent tail nodes (a transform, a condition) are skipped backwards
    because they have no opinion to give."""
    from flow.validate import execution_order

    try:
        order = execution_order(flow)
    except Exception:
        order = [n.get("id") for n in flow.get("nodes", []) if isinstance(n, dict)]
    by_id = {n.get("id"): n for n in flow.get("nodes", []) or [] if isinstance(n, dict)}
    for node_id in reversed(order):
        node = by_id.get(node_id) or {}
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        agent_id = node.get("agent_id") or data.get("agent_id")
        category = node.get("category") or data.get("category") or ("agent" if agent_id else "")
        if agent_id and category == "agent":
            return str(agent_id)
    return None


def resolve_evaluator(loop: Any, flow: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Return ``(mode, agent_id)`` for this loop's judge.

    ``final_agent`` degrades to ``model`` when the flow's tail is not an agent
    node — a plain model call still judges, where a missing agent could not.
    """
    mode = getattr(loop, "evaluator_mode", "final_agent")
    if mode == "agent":
        return ("agent", getattr(loop, "evaluator_agent_id", None) or None) if \
            getattr(loop, "evaluator_agent_id", None) else ("model", None)
    if mode == "model":
        return "model", None
    final_agent = resolve_final_agent(flow)
    return ("final_agent", final_agent) if final_agent else ("model", None)


# ── Running the judge ────────────────────────────────────────────────────────

def _model_cost(provider: str, model: str, inbound: int, outbound: int) -> float:
    try:
        from common.pricing import load_price_map, run_cost_usd
        return round(run_cost_usd(
            {"provider": provider, "model": model,
             "process": {"token_usage": {"inbound_tokens": inbound,
                                         "outbound_tokens": outbound}}},
            load_price_map(),
        ), 6)
    except Exception:
        return 0.0


def evaluate(
    loop: Any,
    flow: Dict[str, Any],
    *,
    goal: str,
    output: str,
    iteration: int,
    history: Optional[List[Dict[str, Any]]] = None,
    state: Optional[Dict[str, Any]] = None,
    workspace: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Tuple[Verdict, float]:
    """Judge one iteration. Returns ``(verdict, cost_usd)``. Never raises: a
    failed evaluation is a "continue" with the error recorded, because a broken
    judge must not silently end a loop as if the work were accepted."""
    mode, agent_id = resolve_evaluator(loop, flow)
    prompt = build_evaluation_prompt(
        criterion=getattr(loop, "exit_criterion", "") or "",
        goal=goal, output=output, iteration=iteration,
        max_iterations=getattr(loop, "max_iterations", 0) or 0,
        history=history, state=state,
    )

    if mode in ("final_agent", "agent") and agent_id:
        verdict, cost = _evaluate_with_agent(
            agent_id, prompt, workspace=workspace, run_id=run_id,
        )
        verdict.agent = agent_id
        return verdict, cost

    verdict, cost = _evaluate_with_model(
        prompt,
        provider=getattr(loop, "evaluator_provider", None),
        model=getattr(loop, "evaluator_model", None),
    )
    verdict.agent = f"model:{getattr(loop, 'evaluator_model', None) or 'default'}"
    return verdict, cost


def _evaluate_with_agent(
    agent_id: str, prompt: str, *, workspace: Optional[str], run_id: Optional[str],
) -> Tuple[Verdict, float]:
    """Run the judgement through a real agent, so it can use its tools (read the
    file it just wrote, run the tests) before scoring."""
    try:
        from agents.agent_factory import create_agent
        from agents.agent_invoke import invoke_agent

        agent = create_agent(agent_id, workspace=workspace)
        inv = invoke_agent(agent, prompt, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        v = Verdict(verdict="continue", error=f"{type(e).__name__}: {e}")
        v.reason = "the evaluator could not be run"
        return v, 0.0

    result = inv.result
    text = str(getattr(result, "agent_output", "") or "")
    verdict = parse_verdict(text)
    if not getattr(result, "ok", False) and not text:
        verdict.error = str(getattr(result, "error", "") or "evaluator run failed")
        verdict.verdict = "continue"

    usage = (inv.process or {}).get("token_usage") or {}
    cost = _model_cost(
        getattr(agent, "provider", "") or "", getattr(agent, "model", "") or "",
        int(usage.get("inbound_tokens") or 0), int(usage.get("outbound_tokens") or 0),
    )
    return verdict, cost


def _evaluate_with_model(
    prompt: str, *, provider: Optional[str], model: Optional[str],
) -> Tuple[Verdict, float]:
    """Judge with a bare model call — no tools, no run record, no side effects."""
    try:
        from agents.agent_utils import build_chat_model
        llm = build_chat_model(provider=provider, model=model, temperature=0.0)
        reply = llm.invoke([
            ("system", "You are a strict reviewer. You reply with one JSON object and nothing else."),
            ("human", prompt),
        ])
    except Exception as e:  # noqa: BLE001
        v = Verdict(verdict="continue", error=f"{type(e).__name__}: {e}")
        v.reason = "the evaluator model could not be reached"
        return v, 0.0

    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        content = "".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    verdict = parse_verdict(str(content))
    usage = getattr(reply, "usage_metadata", None) or {}
    cost = _model_cost(
        provider or "", model or "",
        int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
    )
    return verdict, cost


__all__ = [
    "build_evaluation_prompt", "parse_verdict", "evaluate",
    "resolve_evaluator", "resolve_final_agent",
]
