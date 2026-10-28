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
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from loops.models import Verdict, utc_iso

log = logging.getLogger(__name__)

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
        except (TypeError, ValueError):
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
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(candidate[start:end + 1])
            except ValueError:
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
    except (KeyError, TypeError, AttributeError, ValueError):
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
    except Exception:  # noqa: BLE001 - an unpriced attempt costs zero rather than failing the verdict
        log.debug("pricing the evaluator call failed", exc_info=True)
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
    record: Optional[Dict[str, Any]] = None,
) -> Tuple[Verdict, float]:
    """Judge one iteration. Returns ``(verdict, cost_usd)``. Never raises: a
    failed evaluation is a "continue" with the error recorded, because a broken
    judge must not silently end a loop as if the work were accepted.

    ``record``, with ``run_id``, makes an agent judge's run a run record of its
    own (:func:`_open_judge_run`): what it fields are laid on the record.

    A loop with a ``rubric`` is graded per criterion instead
    (:func:`evaluate_with_rubric`); without one the evaluator path below is
    what it always was."""
    if str(getattr(loop, "rubric", "") or "").strip():
        return evaluate_with_rubric(loop, goal=goal, output=output, workspace=workspace)
    mode, agent_id = resolve_evaluator(loop, flow)
    prompt = build_evaluation_prompt(
        criterion=getattr(loop, "exit_criterion", "") or "",
        goal=goal, output=output, iteration=iteration,
        max_iterations=getattr(loop, "max_iterations", 0) or 0,
        history=history, state=state,
    )

    if mode in ("final_agent", "agent") and agent_id:
        verdict, cost = _evaluate_with_agent(
            agent_id, prompt, workspace=workspace, run_id=run_id, record=record,
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
    record: Optional[Dict[str, Any]] = None,
) -> Tuple[Verdict, float]:
    """Run the judgement through a real agent, so it can use its tools (read the
    file it just wrote, run the tests) before scoring.

    With ``run_id`` and ``record`` the judge's run is a run record like a
    node's: on the Messages list, with its log and steps, its cost in the
    pass's run group and the accounting."""
    from agents.agent_factory import create_agent
    from agents.agent_invoke import invoke_agent

    try:
        agent = create_agent(agent_id, workspace=workspace)
    except Exception as e:  # noqa: BLE001
        v = Verdict(verdict="continue", error=f"{type(e).__name__}: {e}")
        v.reason = "the evaluator could not be run"
        return v, 0.0

    judge = _open_judge_run(agent, agent_id, prompt, run_id, record) if run_id and record is not None else None
    try:
        inv = invoke_agent(agent, prompt, run_id=run_id,
                           extra_callbacks=_judge_callbacks(judge, run_id))
    except Exception as e:  # noqa: BLE001
        if judge:
            _close_judge_run(judge, None, None, error=f"{type(e).__name__}: {e}")
        v = Verdict(verdict="continue", error=f"{type(e).__name__}: {e}")
        v.reason = "the evaluator could not be run"
        return v, 0.0

    result = inv.result
    if judge:
        _close_judge_run(judge, result, inv)
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


def _judge_callbacks(judge: Optional[Dict[str, Any]], run_id: Optional[str]) -> List[Any]:
    """The recorded judge's watcher, and its stop from the Messages list."""
    if not judge or not run_id:
        return []
    from agents.callbacks import RunStopCallback
    return [judge["watcher"], RunStopCallback(run_id)]


def _open_judge_run(agent: Any, agent_id: str, prompt: str, run_id: str,
                    record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Open the judge's run record and its log, watched by the chat callback
    as a flow node is (flow/task_driver.py). ``record`` carries the fields
    that place it: the pass's ``flow_id`` and ``flow_run_id``, the node
    ``judge``, the workspace, task and session, a title. None when the record
    could not be opened: the judge still runs, unrecorded as before."""
    try:
        from agents.callbacks import ChatStreamCallback, write_log
        from flow.node_log import node_log_header
        from managers.run_manager import open_run, run_log_path

        log_path = run_log_path(run_id)
        open_run(
            run_id, agent_id, pid=os.getpid(), session_type="task", channel="loop",
            log_file=str(log_path), link_to_session=False, input=prompt,
            # It runs on the loop's thread, in whatever process runs the loop
            # (the API server's own, often): a stop marks the record and the
            # run's stop callback ends it, nothing signals the pid.
            in_process=True,
            provider=getattr(agent, "provider", "") or "", model=getattr(agent, "model", "") or "",
            **{k: v for k, v in record.items() if v not in (None, "")},
        )
        started = utc_iso()
        msg_id = uuid.uuid4().hex[:8]
        log_lines = node_log_header(meta=[
            f"=== Loop judge run  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {agent_id}",
            f"Flow run  : {record.get('flow_run_id') or '—'}",
        ], started=started, msg_id=msg_id, agent_id=agent_id, user_message=prompt)
        write_log(log_path, log_lines)
        watcher = ChatStreamCallback(None, None, log_lines, log_path, run_id=run_id)
        watcher.bind_model(getattr(agent, "provider", "") or "", getattr(agent, "model", "") or "")
        return {"run_id": run_id, "watcher": watcher, "log_lines": log_lines, "log_path": log_path,
                "msg_id": msg_id, "prompt": prompt}
    except Exception:  # noqa: BLE001 - the record is bookkeeping; the judge runs without it
        log.debug("loop judge: could not open run %s", run_id, exc_info=True)
        return None


def _close_judge_run(judge: Dict[str, Any], result: Any, inv: Any, *, error: str = "") -> None:
    """Close the judge's run record with its log ending and process record."""
    try:
        from agents.callbacks import append_log, write_log
        from chat.streaming import build_process_payload
        from flow.node_log import node_log_ending, node_summary_line
        from managers.run_manager import close_run, close_run_from_result

        watcher, log_lines, log_path = judge["watcher"], judge["log_lines"], judge["log_path"]
        duration_ms = int(getattr(inv, "duration_ms", 0) or 0)
        if error or result is None:
            append_log(log_lines, f"(error: {error or 'no result'})", log_path)
            write_log(log_path, log_lines + node_log_ending("failed", utc_iso()))
            close_run(judge["run_id"], status="failed", exit_code=1, error=error or "no result")
            return
        output = str(getattr(result, "agent_output", "") or "")
        if output:
            append_log(log_lines, output, log_path)
        append_log(log_lines, node_summary_line(watcher, judge["msg_id"], duration_ms), log_path)
        status = "completed" if getattr(result, "ok", False) else "failed"
        write_log(log_path, log_lines + node_log_ending(status, utc_iso()))
        usage = (getattr(inv, "process", None) or {}).get("token_usage") or {}
        process = dict(getattr(inv, "process", None) or {})
        process.update(build_process_payload(
            watcher, full_prompt=judge["prompt"], final_response=output,
            response_obj=getattr(result, "response", None),
            usage={**usage, **watcher.context_usage()}, duration_ms=duration_ms,
        ))
        close_run_from_result(judge["run_id"], result, process=process)
    except Exception:  # noqa: BLE001 - bookkeeping never changes the verdict
        log.debug("loop judge: could not close run %s", judge.get("run_id"), exc_info=True)


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


# ── Grading against a rubric ─────────────────────────────────────────────────

def rubric_grader(loop: Any, workspace: Optional[str]) -> Dict[str, str]:
    """The rubric grader of a loop: its ``grader``, else the evaluator model
    it names, else the outcome grader default (tasks.outcome.resolve_grader)."""
    from tasks.outcome import resolve_grader

    grader = getattr(loop, "grader", None)
    if not grader and getattr(loop, "evaluator_model", None):
        grader = {"provider": getattr(loop, "evaluator_provider", None) or "",
                  "model": loop.evaluator_model}
    # The runner passes the workspace's folder; the model lookup wants its name.
    name = getattr(loop, "workspace", None)
    if not name and workspace:
        try:
            from common.workspace_context import workspace_name_from_path
            name = workspace_name_from_path(workspace) or workspace
        except Exception:  # noqa: BLE001 - an unresolvable path means no workspace opinion on the model
            name = None
    return resolve_grader(grader, name)


def _rubric_feedback(criteria: List[Dict[str, Any]], overall: str) -> str:
    """What the next pass is told: every unmet criterion with its feedback."""
    lines = []
    for c in criteria:
        if c.get("passed"):
            continue
        fb = str(c.get("feedback") or "").strip()
        lines.append(f"- {c.get('name')}" + (f": {fb}" if fb else ": not met"))
    if overall.strip():
        lines.append(f"Overall: {overall.strip()}")
    return "\n".join(lines)


def evaluate_with_rubric(
    loop: Any, *, goal: str, output: str, workspace: Optional[str] = None,
) -> Tuple[Verdict, float]:
    """Grade one pass against the loop's rubric and express it as a Verdict.

    One grader for loops and task outcomes (evals.graders.grade_rubric): a
    fresh model call per pass, blind to the flow's own conversation. Passed
    means ``stop``; the mean criterion score becomes the 0-100 score, so the
    trajectory chart and ``target_score`` read it like any other judge's; the
    unmet criteria with their feedback become the next pass's feedback. The
    whole grading is kept in ``raw`` as JSON so the per-criterion rows survive
    on the iteration record. A grader that failed is a ``continue`` with the
    error recorded, never an acceptance.
    """
    from types import SimpleNamespace

    from evals.graders import grade_rubric

    grader = rubric_grader(loop, workspace)
    # The loop's target score is the rubric's pass threshold, so the verdict
    # and the runner's own target check (loops.runner._check_convergence)
    # agree on when a pass is good enough.
    threshold = None
    if getattr(loop, "target_score", None) is not None:
        threshold = max(0.0, min(float(loop.target_score) / 100.0, 1.0))
    criterion = str(getattr(loop, "exit_criterion", "") or "").strip()
    context = (goal or "").strip()
    if criterion:
        context = f"{context}\n\nExit criterion: {criterion}".strip()

    try:
        result = grade_rubric(
            output or "",
            SimpleNamespace(input=context, rubric=loop.rubric, expected=None),
            {"rubric": loop.rubric, "grader": grader if grader.get("model") else None,
             "threshold": threshold, "context": context},
            None,
        )
    except Exception as e:  # noqa: BLE001 - the grader contract is never raise; guard it anyway so a loop keeps going
        v = Verdict(verdict="continue", error=f"{type(e).__name__}: {e}")
        v.reason = "the rubric grader could not be run"
        return v, 0.0

    extra = result.extra or {}
    criteria = list(extra.get("criteria") or [])
    overall = str(extra.get("feedback") or "")
    model = (extra.get("grader") or grader or {}).get("model") or "default"
    verdict = Verdict(
        score=round(float(result.score) * 100.0, 2),
        verdict="stop" if result.passed else "continue",
        reason=str(result.detail or ""),
        feedback="" if result.passed else _rubric_feedback(criteria, overall),
        raw=json.dumps({
            "kind": "rubric", "passed": bool(result.passed), "score": result.score,
            "criteria": criteria, "feedback": overall, "grader": extra.get("grader"),
            "threshold": threshold, "tokens": extra.get("tokens"),
            "raw": str(extra.get("raw") or "")[:2000],
        }, ensure_ascii=False, default=str),
        agent=f"rubric:{model}",
        error=str(extra.get("error") or ""),
    )
    if verdict.error:
        verdict.verdict = "continue"
        verdict.score = None
        verdict.feedback = (
            "The grader could not assess this pass. Make sure the result states "
            "plainly how each criterion of the rubric is met."
        )
    return verdict, float(extra.get("cost_usd") or 0.0)


__all__ = [
    "build_evaluation_prompt", "parse_verdict", "evaluate", "evaluate_with_rubric",
    "rubric_grader", "resolve_evaluator", "resolve_final_agent",
]
