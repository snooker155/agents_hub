"""
Task outcomes: a rubric, an independent grader, and another attempt when the
work falls short.

A task may say what "done" means (``Task.outcome``): a markdown rubric, how
many attempts the agent gets, which model grades, and an optional pass
threshold. After every completed agent run on the task, the finalizer hands
the run here (:func:`on_run_completed`). A fresh model call grades the result
against the rubric one criterion at a time (``evals.graders.grade_rubric``,
the same grader a loop's rubric and an eval set use); the grading is kept on
the task (``Task.outcome_evaluations``), and then:

* passed: the finalizer carries on as for any finished run (resolved, or kept
  in progress for the orchestrator in continuous mode);
* not passed, attempts left: the same agent is started again on the task, and
  its instruction carries the grader's per-criterion feedback
  (:func:`instruction_sections`, read by ``tasks.context.build_task_instruction``);
* not passed at the limit, or the grader failing twice in a row: the task is
  blocked for a person with a reason naming what is unmet.

The grader is independent on purpose: it sees the request, the rubric, the
result and a compact summary of the tool calls, never the agent's own
conversation, so it cannot be argued into agreement by the reasoning that
produced the work.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import UUID

log = logging.getLogger(__name__)

#: Attempts a task gets when its outcome does not say.
DEFAULT_MAX_ITERATIONS = 3
#: The most attempts an outcome may ask for. Every attempt is a whole agent
#: run plus a grading call, so the ceiling is enforced, not suggested.
MAX_ITERATIONS_LIMIT = 10
#: Grader failures in a row after which the task goes to a person instead of
#: spending another agent run on a grade nobody can read.
GRADER_ERROR_LIMIT = 2
#: "provider/model" of the grader when the outcome names none.
GRADER_MODEL_ENV = "AGENTS_HUB_OUTCOME_GRADER_MODEL"
#: A rubric longer than this many criteria is graded on the first ones only.
MAX_CRITERIA = 30
#: Gradings kept per task, newest last.
MAX_EVALUATIONS = 100

#: Agents whose runs are not the work itself: routing, decomposition, review.
NON_GRADED_AGENTS = frozenset({"orchestrator", "code_reviewer", "decomposer"})

#: Marks the note an outcome relaunch appends to the run's description, so a
#: third attempt replaces the second attempt's note instead of stacking.
_ATTEMPT_NOTE_PREFIX = "[Outcome attempt "

_SEP = "=" * 60


class OutcomeError(ValueError):
    """An outcome definition or a grading request that cannot be accepted."""


# ── The rubric ───────────────────────────────────────────────────────────────

_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_CHECKBOX = re.compile(r"^\[[ xX]\]\s*")
_BOLD_LEAD = re.compile(r"^(\*\*|__)(.+?)\1\s*[:.]?\s*(.*)$")
_COLON_LEAD = re.compile(r"^([^:]{1,60}):\s+(.+)$")


def _clean_inline(text: str) -> str:
    """Drop the markdown emphasis and code marks a criterion name carries."""
    text = re.sub(r"(\*\*|__|`)", "", text or "")
    return re.sub(r"\s+", " ", text).strip().rstrip(":").strip()


def _short_name(text: str, limit: int = 60) -> str:
    text = _clean_inline(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(",.;") + "…"


def _name_and_description(text: str) -> Tuple[str, str]:
    """Split one bullet into a short name and the full criterion text.

    ``**Tests**: all pass`` and ``Tests: all pass`` name the criterion
    explicitly; anything else is named by its own opening words.
    """
    text = _CHECKBOX.sub("", (text or "").strip())
    m = _BOLD_LEAD.match(text)
    if m and m.group(2).strip():
        name = _clean_inline(m.group(2))
        desc = _clean_inline(m.group(3)) or name
        return _short_name(name), desc
    m = _COLON_LEAD.match(text)
    if m:
        return _short_name(m.group(1)), _clean_inline(m.group(2))
    return _short_name(text), _clean_inline(text)


def _dedupe(criteria: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: Dict[str, int] = {}
    out = []
    for c in criteria[:MAX_CRITERIA]:
        key = c["name"].lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            c = {**c, "name": f"{c['name']} ({seen[key]})"}
        out.append(c)
    return out


def parse_rubric(markdown: str) -> List[Dict[str, str]]:
    """The criteria of a markdown rubric, as ``[{"name", "description"}]``.

    Top-level bullet items (``-``, ``*``, ``+``, ``1.``, checkboxes) are the
    criteria, with nested items and indented lines folded into their parent's
    description. A rubric without bullets is read by its headings, each with
    its section text as the description. A rubric with neither is one
    criterion, ``Overall``, whose description is the whole text. An empty
    rubric has no criteria.
    """
    text = (markdown or "").strip()
    if not text:
        return []

    bullets: List[Dict[str, Any]] = []
    headings: List[Dict[str, Any]] = []
    top_indent: Optional[int] = None
    current: Optional[Dict[str, Any]] = None
    section: Optional[Dict[str, Any]] = None
    in_fence = False
    for raw in text.splitlines():
        line = raw.expandtabs(4)
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            if current is not None:
                current["more"].append(stripped)
            if section is not None:
                section["body"].append(stripped)
            continue
        heading = _HEADING.match(line)
        if heading:
            current = None
            section = {"title": _clean_inline(heading.group(2)), "body": []}
            headings.append(section)
            continue
        if section is not None and stripped:
            section["body"].append(stripped)
        bullet = _BULLET.match(line)
        if bullet:
            indent = len(bullet.group(1))
            if current is None or top_indent is None or indent <= top_indent:
                top_indent = indent if top_indent is None else min(top_indent, indent)
                current = {"text": bullet.group(2), "more": []}
                bullets.append(current)
            else:
                current["more"].append(_CHECKBOX.sub("", bullet.group(2).strip()))
            continue
        if not stripped:
            continue
        if current is not None and line[:1] in (" ", "\t"):
            current["more"].append(stripped)
        else:
            current = None

    if bullets:
        criteria = []
        for b in bullets:
            name, desc = _name_and_description(b["text"])
            if not name:
                continue
            if b["more"]:
                desc = f"{desc} ({'; '.join(_clean_inline(m) for m in b['more'] if m)})"
            criteria.append({"name": name, "description": desc})
        if criteria:
            return _dedupe(criteria)

    named = [h for h in headings if h["title"]]
    if named:
        return _dedupe([
            {"name": _short_name(h["title"]),
             "description": _clean_inline(" ".join(h["body"]))[:1000] or h["title"]}
            for h in named
        ])

    return [{"name": "Overall", "description": text[:2000]}]


# ── The definition ───────────────────────────────────────────────────────────

def parse_grader_ref(value: Any, field: str = "grader") -> Optional[Dict[str, str]]:
    """``"provider/model"`` or ``{provider, model}`` as a dict; None when empty."""
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        provider, sep, model = value.strip().partition("/")
        if not sep or not provider.strip() or not model.strip():
            raise OutcomeError(f"{field} must be 'provider/model'")
        return {"provider": provider.strip(), "model": model.strip()}
    if isinstance(value, dict):
        provider = str(value.get("provider") or "").strip()
        model = str(value.get("model") or "").strip()
        if not provider and not model:
            return None
        if not provider or not model:
            raise OutcomeError(f"{field} needs both a provider and a model")
        return {"provider": provider, "model": model}
    raise OutcomeError(f"{field} must be 'provider/model' or an object with provider and model")


def normalize_outcome(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an outcome definition and return it in its stored shape.

    ``rubric`` must not be empty, ``max_iterations`` is 1 to
    ``MAX_ITERATIONS_LIMIT`` (default ``DEFAULT_MAX_ITERATIONS``),
    ``threshold`` is None or 0 to 1, ``grader`` is None, ``"provider/model"``
    or ``{provider, model}``. Raises :class:`OutcomeError`.
    """
    if not isinstance(data, dict):
        raise OutcomeError("outcome must be an object")
    rubric = str(data.get("rubric") or "").strip()
    if not rubric:
        raise OutcomeError("rubric must not be empty")

    raw_max = data.get("max_iterations")
    try:
        max_iterations = DEFAULT_MAX_ITERATIONS if raw_max in (None, "") else int(raw_max)
    except (TypeError, ValueError):
        raise OutcomeError("max_iterations must be a whole number")
    if not 1 <= max_iterations <= MAX_ITERATIONS_LIMIT:
        raise OutcomeError(f"max_iterations must be between 1 and {MAX_ITERATIONS_LIMIT}")

    raw_threshold = data.get("threshold")
    threshold: Optional[float] = None
    if raw_threshold not in (None, ""):
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            raise OutcomeError("threshold must be a number between 0 and 1")
        if not 0.0 <= threshold <= 1.0:
            raise OutcomeError("threshold must be between 0 and 1")

    return {
        "rubric": rubric,
        "max_iterations": max_iterations,
        "grader": parse_grader_ref(data.get("grader")),
        "threshold": threshold,
    }


def _max_iterations(outcome: Dict[str, Any]) -> int:
    try:
        value = int(outcome.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_ITERATIONS
    return max(1, min(value, MAX_ITERATIONS_LIMIT))


def _workspace_model(workspace: Optional[str]) -> Dict[str, str]:
    """The model the workspace points at: its override, else its default.

    ``global`` (or nothing set) means no workspace opinion, and the grader
    falls through to the global default ``build_chat_model`` resolves.
    """
    empty = {"provider": "", "model": ""}
    if not workspace:
        return empty
    try:
        from workspace import get_workspace_default_model_config, get_workspace_metadata
        meta = get_workspace_metadata(workspace) or {}
        override = meta.get("model_override") or {}
        provider = str(override.get("provider") or "").strip()
        if provider == "global":
            return empty
        if provider and provider != "workspace_default" and override.get("model"):
            return {"provider": provider, "model": str(override["model"]).strip()}
        default = get_workspace_default_model_config(meta) or {}
        if default.get("model"):
            return {"provider": str(default.get("provider") or ""), "model": str(default["model"])}
    except Exception:  # noqa: BLE001 - an unreadable workspace falls back to the global default model
        log.debug("workspace model lookup failed for %s", workspace, exc_info=True)
    return empty


def resolve_grader(grader: Any, workspace: Optional[str]) -> Dict[str, str]:
    """Which model grades: the one named, else ``AGENTS_HUB_OUTCOME_GRADER_MODEL``,
    else the workspace's model, else the global default (both parts empty).
    """
    from evals.graders import split_model_ref

    if grader:
        ref = split_model_ref(grader)
        if ref["model"]:
            return ref
    env = (os.environ.get(GRADER_MODEL_ENV) or "").strip()
    if env:
        ref = split_model_ref(env)
        if ref["model"]:
            return ref
    return _workspace_model(workspace)


# ── Grading ──────────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_context(task: Any) -> str:
    title = str(getattr(task, "title", "") or "").strip()
    desc = str(getattr(task, "description", "") or "").strip()
    return "\n\n".join(p for p in (title, desc) if p)


def grade_outcome(task: Any, output: str, *, run_id: Optional[str] = None,
                  trigger: str = "run") -> Dict[str, Any]:
    """Grade ``output`` against the task's outcome and return the evaluation.

    Nothing is written here; :func:`record_evaluation` keeps it on the task.
    ``trigger`` is ``run`` for the grading after a finished run (it counts
    against ``max_iterations``) or ``manual`` for "Grade now" (it does not).
    """
    from evals.graders import _load_run_payload, grade_rubric

    outcome = dict(getattr(task, "outcome", None) or {})
    rubric = str(outcome.get("rubric") or "").strip()
    if not rubric:
        raise OutcomeError("the task has no outcome rubric")
    grader = resolve_grader(outcome.get("grader"), getattr(task, "workspace", None))
    threshold = outcome.get("threshold")
    context = _task_context(task)
    payload = _load_run_payload(run_id) if run_id else None
    result = grade_rubric(
        output or "",
        SimpleNamespace(input=context, rubric=rubric, expected=None),
        {"rubric": rubric, "criteria": parse_rubric(rubric),
         "grader": grader if grader.get("model") else None,
         "threshold": threshold, "context": context},
        payload,
    )
    extra = result.extra or {}
    evaluation: Dict[str, Any] = {
        "iteration": len(getattr(task, "outcome_evaluations", None) or []) + 1,
        "run_id": run_id,
        "trigger": trigger,
        "passed": bool(result.passed),
        "score": round(float(result.score), 4),
        "criteria": list(extra.get("criteria") or []),
        "feedback": str(extra.get("feedback") or result.detail or ""),
        "grader": dict(extra.get("grader") or grader),
        "threshold": threshold,
        "cost_usd": float(extra.get("cost_usd") or 0.0),
        "tokens": dict(extra.get("tokens") or {}),
        "graded_at": _utc_now_iso(),
    }
    if extra.get("error"):
        evaluation["error"] = str(extra["error"])
        evaluation["passed"] = False
    _count_on_run(run_id, evaluation)
    return evaluation


def _count_on_run(run_id: Optional[str], evaluation: Dict[str, Any]) -> None:
    """The grading is a model call made for the run it graded: its tokens go
    onto that run's ``loop.aux_calls`` (common/aux_usage.py), so the Costs page
    and the task's money cap (which prices the task's runs) include it."""
    tokens = evaluation.get("tokens") or {}
    if not run_id or not (tokens.get("input") or tokens.get("output")):
        return
    grader = evaluation.get("grader") or {}
    from common import aux_usage
    aux_usage.record_on_run(str(run_id), aux_usage.entry(
        "outcome_grader", provider=str(grader.get("provider") or ""), model=str(grader.get("model") or ""),
        tokens={"input_tokens": int(tokens.get("input") or 0),
                "output_tokens": int(tokens.get("output") or 0), "cached_tokens": 0}))


def record_evaluation(task_id: Any, evaluation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Append ``evaluation`` to the task's gradings and return the new list."""
    from tasks import service as _ts

    tid = task_id if isinstance(task_id, UUID) else UUID(str(task_id))
    task = _ts.get_task(tid)
    evaluations = list(getattr(task, "outcome_evaluations", None) or []) if task else []
    evaluations.append(evaluation)
    evaluations = evaluations[-MAX_EVALUATIONS:]
    _ts.update_task(tid, outcome_evaluations=evaluations)
    return evaluations


def _unmet(evaluation: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in evaluation.get("criteria") or [] if not c.get("passed")]


def _summary(evaluation: Dict[str, Any]) -> str:
    """One line for the activity log."""
    it = evaluation.get("iteration")
    if evaluation.get("error"):
        return f"Outcome check {it}: the grader failed ({evaluation['error']})"
    score = float(evaluation.get("score") or 0.0)
    if evaluation.get("passed"):
        return f"Outcome check {it}: met (score {score:.2f})"
    names = ", ".join(str(c.get("name")) for c in _unmet(evaluation)) or "overall"
    return f"Outcome check {it}: not met (score {score:.2f}); unmet: {names}"


def _run_output(run: Dict[str, Any], task_id: UUID) -> str:
    """What the run produced: its recorded output, else its persisted result."""
    text = str((run or {}).get("output") or "").strip()
    if text:
        return text
    try:
        from tasks import service as _ts
        run_id = str((run or {}).get("run_id") or "")
        for entry in reversed(_ts.get_task_results(task_id) or []):
            if run_id and str(entry.get("run_id") or "") == run_id:
                return str(entry.get("result") or "")
    except Exception:  # noqa: BLE001 - a missing result file grades as an empty answer
        log.debug("task result lookup failed for %s", task_id, exc_info=True)
    return ""


def _consecutive_errors(evaluations: List[Dict[str, Any]]) -> int:
    n = 0
    for e in reversed(evaluations):
        if not e.get("error"):
            break
        n += 1
    return n


def _attempts(evaluations: List[Dict[str, Any]]) -> int:
    return sum(1 for e in evaluations if (e.get("trigger") or "run") == "run")


def _block(tid: UUID, task: Any, reason: str, entry_type: str, run_id: str = "") -> None:
    """Hand the task to a person: block it, release the agent, say so."""
    from tasks import service as _ts

    _ts.block_task(tid, reason=reason)
    _ts.clear_agent(tid)
    try:
        _ts.append_task_activity_log(tid, entry_type, reason, run_id=run_id)
    except Exception:  # noqa: BLE001 - an activity-log write is best-effort, the block itself is done
        log.debug("append_task_activity_log failed for %s", tid, exc_info=True)
    try:
        from plans import service as _plan_service
        _plan_service.create_notification(
            title="A task did not meet its outcome",
            body=reason,
            severity="warning",
            source={"origin": "agent", "task_id": str(tid)},
            workspace=str(getattr(task, "workspace", "") or "") or None,
            channels=["dashboard"],
        )
    except Exception:  # noqa: BLE001 - best-effort notification, must not undo blocking the task
        log.debug("create_notification failed for %s", tid, exc_info=True)


def _limit_reason(evaluation: Dict[str, Any], attempts: int) -> str:
    unmet = _unmet(evaluation)
    lines = [
        f"Outcome not met after {attempts} attempt(s). It needs your input instead of "
        "another automatic attempt."
    ]
    if unmet:
        lines.append("Unmet criteria:")
        for c in unmet:
            fb = str(c.get("feedback") or "").strip()
            lines.append(f"- {c.get('name')}" + (f": {fb[:300]}" if fb else ""))
    elif evaluation.get("feedback"):
        lines.append(str(evaluation["feedback"])[:600])
    return "\n".join(lines)


def _attempt_description(params: Dict[str, Any], attempt: int, max_iterations: int) -> str:
    base = str(params.get("description") or "")
    marker = base.find(_ATTEMPT_NOTE_PREFIX)
    if marker != -1:
        base = base[:marker]
    note = (
        f"{_ATTEMPT_NOTE_PREFIX}{attempt}/{max_iterations}] The previous attempt did not "
        "meet the definition of done. The grader's feedback is in the OUTCOME REVIEW "
        "section of this instruction: address every unmet criterion before you finish."
    )
    return f"{base.strip()}\n\n{note}".strip()


def _relaunch(tid: UUID, task: Any, agent_id: str, attempt: int, max_iterations: int) -> str:
    """Start the same agent on the task again. Returns the new run id.

    Launched the way the retry policy relaunches a failed run
    (managers.runs.task_finalize._maybe_retry_failed_run): straight through
    ``agent_launcher.start_run`` and ``assign_agent``, since the agent was
    already a valid choice for this task once. Run-bound session
    continuations are moved onto the new run so the orchestrator follows up
    when the last attempt finishes, not after this one.
    """
    from agents import agent_launcher
    from tasks import service as _ts

    params: Dict[str, Any] = {}
    if str(getattr(task, "assigned_agent_type", "") or "") == agent_id:
        params = dict(getattr(task, "assigned_agent_params", None) or {})
    params["description"] = _attempt_description(params, attempt, max_iterations)

    new_run_id, _session = agent_launcher.start_run(str(tid), agent_id, params)
    _ts.assign_agent(tid, agent_id, params, run_id=new_run_id)
    _ts.update_task(tid, status=_ts.TaskStatus.in_progress)
    try:
        from common.session_service import rebind_continuations_to_run
        rebind_continuations_to_run(str(tid), new_run_id)
    except Exception:  # noqa: BLE001 - a continuation left on the old run still fires, just earlier
        log.debug("rebind_continuations_to_run failed for %s", tid, exc_info=True)
    return new_run_id


def on_run_completed(task_id: Any, run: Dict[str, Any]) -> bool:
    """Grade a finished agent run against the task's outcome and act on it.

    Called by the finalizer for a completed ordinary agent run. Returns True
    when this function decided what happens to the task (another attempt was
    started, or the task was blocked), so the finalizer skips its normal
    transition; False when the outcome passed or does not apply, so the
    finalizer resolves the task as usual.
    """
    from tasks import service as _ts

    tid = task_id if isinstance(task_id, UUID) else UUID(str(task_id))
    task = _ts.get_task(tid)
    outcome = getattr(task, "outcome", None) if task else None
    if not outcome or not str(outcome.get("rubric") or "").strip():
        return False
    agent_id = str((run or {}).get("agent_id") or "")
    if not agent_id or agent_id in NON_GRADED_AGENTS:
        return False
    run_id = str((run or {}).get("run_id") or "")

    evaluation = grade_outcome(task, _run_output(run, tid), run_id=run_id or None, trigger="run")
    evaluations = record_evaluation(tid, evaluation)
    try:
        _ts.append_task_activity_log(
            tid, "outcome_graded", _summary(evaluation), run_id=run_id, agent_id=agent_id,
            passed=bool(evaluation.get("passed")), score=evaluation.get("score"),
        )
    except Exception:  # noqa: BLE001 - an activity-log write is best-effort
        log.debug("append_task_activity_log failed for %s", tid, exc_info=True)

    return _act_on(tid, task, outcome, evaluation, evaluations, run_id=run_id,
                   relaunch=lambda attempt, limit: _relaunch(tid, task, agent_id, attempt, limit),
                   actor=agent_id)


def _act_on(tid: UUID, task: Any, outcome: Dict[str, Any], evaluation: Dict[str, Any],
            evaluations: List[Dict[str, Any]], *, run_id: str,
            relaunch: Callable[[int, int], str], actor: str) -> bool:
    """What a recorded grading means for the task, the same for an agent run
    and for a flow, team, loop or scenario: pass (False, the finalizer goes
    on as usual), block after repeated grader errors or at the attempt limit,
    or start the next attempt through ``relaunch(attempt, max_iterations)``."""
    from tasks import service as _ts

    if evaluation.get("passed"):
        return False

    errors = _consecutive_errors(evaluations)
    if errors >= GRADER_ERROR_LIMIT:
        _block(tid, task, (
            f"The outcome grader failed {errors} times in a row "
            f"(last error: {evaluation.get('error')}). Check the grader model or the "
            "rubric, then reassign the task."
        ), "outcome_grader_failed", run_id)
        return True

    max_iterations = _max_iterations(outcome)
    attempts = _attempts(evaluations)
    if attempts >= max_iterations:
        _block(tid, task, _limit_reason(evaluation, attempts), "outcome_limit", run_id)
        return True

    try:
        new_run_id = relaunch(attempts + 1, max_iterations)
    except Exception as e:  # noqa: BLE001 - a launch that cannot start (budget, capacity) blocks rather than resolving unmet work
        log.warning("outcome relaunch failed for task %s", tid, exc_info=True)
        _block(tid, task, (
            "The outcome was not met and another attempt could not be started "
            f"({type(e).__name__}: {e})."
        ), "outcome_relaunch_failed", run_id)
        return True
    try:
        _ts.append_task_activity_log(
            tid, "outcome_retry",
            f"Outcome not met: attempt {attempts + 1}/{max_iterations} started with the grader's feedback",
            run_id=str(new_run_id or ""), agent_id=actor,
        )
    except Exception:  # noqa: BLE001 - an activity-log write is best-effort
        log.debug("append_task_activity_log failed for %s", tid, exc_info=True)
    return True


def _relaunch_executor(tid: UUID, task: Any, executor: Any, attempt: int, max_iterations: int) -> str:
    """Start the task's flow, team, loop or scenario again for the next attempt.

    Relaunched the way the retry policy relaunches a failed executor run
    (``tasks.assign.assign_executor_to_task``). A flow builds its input with
    ``tasks.context.build_task_instruction``, which already carries the
    definition of done and the latest review; a team or a loop starts from a
    goal, so the review goes into the goal itself.
    """
    from tasks import service as _ts
    from tasks.assign import assign_executor_to_task
    from tasks.serialize import task_to_dict

    params: Dict[str, Any] = dict(getattr(task, "assigned_agent_params", None) or {})
    params["description"] = _attempt_description(params, attempt, max_iterations)
    if executor.kind in ("team", "loop"):
        # The goal they start from, with this attempt's note and the review
        # (the rubric and the grader's feedback) where a flow or an agent
        # reads them in its instruction.
        fresh = _ts.get_task(tid) or task
        goal = str(params.get("goal") or getattr(fresh, "description", "") or getattr(fresh, "title", "") or "")
        params["goal"] = _attempt_description({"description": goal}, attempt, max_iterations) \
            + "\n\n" + instruction_sections(fresh)
    result = assign_executor_to_task(tid, executor, params, task_to_dict=task_to_dict)
    return str((result or {}).get("run_id") or "")


def on_executor_completed(task_id: Any) -> bool:
    """Grade what a flow, team, loop or scenario produced for the task.

    The executor's counterpart of :func:`on_run_completed`, called by the
    generic finalizer when such a run completes. The graded result is the
    task's latest persisted result (every executor persists one before it
    finalizes). Returns True when this decided what happens to the task.
    """
    from tasks import service as _ts

    tid = task_id if isinstance(task_id, UUID) else UUID(str(task_id))
    task = _ts.get_task(tid)
    outcome = getattr(task, "outcome", None) if task else None
    if not outcome or not str(outcome.get("rubric") or "").strip():
        return False
    executor = getattr(task, "executor", None)
    if executor is None or executor.kind == "agent":
        return False

    run_id, output = "", ""
    try:
        results = _ts.get_task_results(tid) or []
        if results:
            run_id = str(results[-1].get("run_id") or "")
            output = str(results[-1].get("result") or "")
    except Exception:  # noqa: BLE001 - no stored result grades as an empty answer
        log.debug("task results lookup failed for %s", tid, exc_info=True)

    # An executor's run id names a flow, team, loop or scenario run, not a
    # row of ``runs``, so the grading is not attached to a run record.
    evaluation = grade_outcome(task, output, run_id=None, trigger="run")
    evaluation["run_id"] = run_id or None
    evaluations = record_evaluation(tid, evaluation)
    actor = f"{executor.kind}:{executor.id}"
    try:
        _ts.append_task_activity_log(
            tid, "outcome_graded", _summary(evaluation), run_id=run_id, agent_id=actor,
            passed=bool(evaluation.get("passed")), score=evaluation.get("score"),
        )
    except Exception:  # noqa: BLE001 - an activity-log write is best-effort
        log.debug("append_task_activity_log failed for %s", tid, exc_info=True)
    return _act_on(tid, task, outcome, evaluation, evaluations, run_id=run_id,
                   relaunch=lambda attempt, limit: _relaunch_executor(tid, task, executor, attempt, limit),
                   actor=actor)


# ── Grading on request ───────────────────────────────────────────────────────

def _latest_gradable_run(tid: UUID) -> Tuple[Optional[str], str]:
    """``(run_id, output)`` of the task's latest completed work run.

    Falls back to the latest persisted result of a work agent when no run
    record is found (a run recorded before the task had a session, say).
    """
    from tasks import service as _ts

    try:
        runs = [
            r for r in (_ts.get_task_runs(tid) or [])
            if str(r.get("status") or "") == "completed"
            and str(r.get("agent_id") or "") not in NON_GRADED_AGENTS
        ]
    except Exception:  # noqa: BLE001 - no run list means falling back to the stored results
        log.debug("get_task_runs failed for %s", tid, exc_info=True)
        runs = []
    runs.sort(key=lambda r: str(r.get("finished_at") or r.get("started_at") or ""))
    if runs:
        run = runs[-1]
        return str(run.get("run_id") or "") or None, _run_output(run, tid)
    for entry in reversed(_ts.get_task_results(tid) or []):
        if str(entry.get("agent_id") or "") in NON_GRADED_AGENTS:
            continue
        return (str(entry.get("run_id") or "") or None), str(entry.get("result") or "")
    return None, ""


def grade_now(task_id: Any) -> Dict[str, Any]:
    """Grade the task's latest completed run now and keep the grading.

    Nothing is relaunched and the task's status does not change: this is the
    operator asking "would this pass?", for instance after editing the
    rubric. The grading is recorded with ``trigger: manual`` and does not
    count against ``max_iterations``.
    """
    from tasks import service as _ts

    tid = task_id if isinstance(task_id, UUID) else UUID(str(task_id))
    task = _ts.get_task(tid)
    if not task:
        raise OutcomeError("task not found")
    if not getattr(task, "outcome", None):
        raise OutcomeError("the task has no outcome to grade against")
    run_id, output = _latest_gradable_run(tid)
    if not run_id and not output.strip():
        raise OutcomeError("the task has no completed run to grade")
    evaluation = grade_outcome(task, output, run_id=run_id, trigger="manual")
    record_evaluation(tid, evaluation)
    try:
        _ts.append_task_activity_log(tid, "outcome_graded", _summary(evaluation) + " (graded on request)",
                                     run_id=run_id or "", passed=bool(evaluation.get("passed")),
                                     score=evaluation.get("score"))
    except Exception:  # noqa: BLE001 - an activity-log write is best-effort
        log.debug("append_task_activity_log failed for %s", tid, exc_info=True)
    return evaluation


# ── What the next attempt reads ──────────────────────────────────────────────

def instruction_sections(task: Any) -> str:
    """The outcome blocks of a run's instruction, or "" when there is none.

    The rubric as the definition of done, and, when the latest grading did
    not pass, the grader's per-criterion feedback on that attempt: an agent
    that is not told why it is running again produces the same thing again.
    """
    outcome = getattr(task, "outcome", None) or {}
    rubric = str(outcome.get("rubric") or "").strip()
    if not rubric:
        return ""
    parts = [
        f"{_SEP}\nDEFINITION OF DONE (an independent grader checks your result against this)\n"
        f"{_SEP}\n{rubric}\n{_SEP}"
    ]
    evaluations = list(getattr(task, "outcome_evaluations", None) or [])
    latest = evaluations[-1] if evaluations else None
    if latest and not latest.get("passed"):
        attempts = _attempts(evaluations)
        header = (
            f"OUTCOME REVIEW OF YOUR PREVIOUS ATTEMPT (attempt {attempts} of at most "
            f"{_max_iterations(outcome)}: not met, score {float(latest.get('score') or 0.0):.2f})"
        )
        if latest.get("error"):
            body = (
                "The grader could not assess the previous attempt. Make sure your final "
                "answer states plainly how each criterion above is met."
            )
        else:
            lines = []
            for c in latest.get("criteria") or []:
                state = "met" if c.get("passed") else "NOT MET"
                fb = str(c.get("feedback") or "").strip()
                lines.append(
                    f"- {c.get('name')}: {state} ({float(c.get('score') or 0.0):.2f})"
                    + (f". {fb}" if fb and not c.get("passed") else "")
                )
            if latest.get("feedback"):
                lines.append(f"Overall: {str(latest['feedback']).strip()}")
            body = "\n".join(lines) or str(latest.get("feedback") or "")
        parts.append(
            f"{_SEP}\n{header}\n{_SEP}\n{body}\n{_SEP}\n"
            "Fix every unmet criterion above before you finish."
        )
    return "\n\n".join(parts)


__all__ = [
    "DEFAULT_MAX_ITERATIONS", "MAX_ITERATIONS_LIMIT", "GRADER_ERROR_LIMIT",
    "GRADER_MODEL_ENV", "NON_GRADED_AGENTS", "OutcomeError",
    "parse_rubric", "parse_grader_ref", "normalize_outcome", "resolve_grader", "grade_outcome",
    "record_evaluation", "on_run_completed", "grade_now", "instruction_sections",
]
