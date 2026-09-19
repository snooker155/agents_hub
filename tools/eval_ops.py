"""
Eval tools — building a dataset, grading it, and reading the result.

The Evals surface answers one question: did that prompt change actually help, or
does it only feel better on the two examples you tried. These tools let an agent
do the parts that are tedious by hand — writing cases, picking graders, reading
a score matrix back — while the expensive part stays behind an approval gate.

Three groups:

* **Building** is free. Creating a set, adding cases and choosing graders costs
  nothing and is fully reversible, so those tools just work.
* **Running** is not. A sweep is cases x configs model calls, plus a second call
  per cell when an LLM judge is in the graders. ``run_eval_tool`` therefore
  refuses until ``user_approved`` is True, and the refusal carries the projected
  cost — the same contract the scenario, team and loop run tools use.
* **Reading** returns the score matrix and what each cell produced.

On grader choice, which is where most of the value is: reach for a
deterministic grader whenever the question allows one. An LLM judge on a
question a regex could answer adds cost and noise, and its verdict is itself a
model's opinion.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from common.workspace_context import resolve_active_workspace


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2, default=str)


def _json_err(message: str, *, code: str = "bad_request",
              extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2, default=str)


def _summary(evalset) -> Dict[str, Any]:
    """An eval set without the full case list — enough to choose between sets."""
    d = evalset.to_dict()
    return {
        "eval_set_id": d.get("eval_set_id"),
        "name": d.get("name"),
        "description": d.get("description"),
        "workspace": d.get("workspace"),
        "agent_id": d.get("agent_id"),
        "cases": len(d.get("cases") or []),
        "graders": [g.get("kind") for g in (d.get("graders") or [])],
    }


# ── building ─────────────────────────────────────────────────────────────────

class ListEvalsInput(BaseModel):
    workspace: Optional[str] = Field(None, description="Filter to one workspace")


@tool("list_evals_tool", args_schema=ListEvalsInput)
def list_evals_tool(workspace: Optional[str] = None) -> str:
    """List the eval sets, with how many cases and which graders each carries.

    Start here. Naming an existing set is almost always better than building a
    second one that measures the same thing.
    """
    try:
        from evals import store
        ws = workspace or resolve_active_workspace()
        return _json_ok({"eval_sets": [_summary(e) for e in store.list_eval_sets(ws)]})
    except Exception as e:
        return _json_err(f"Failed to list eval sets: {e}", code="internal")


class GetEvalInput(BaseModel):
    eval_set_id: str = Field(..., description="Eval set id, from list_evals_tool")


@tool("get_eval_tool", args_schema=GetEvalInput)
def get_eval_tool(eval_set_id: str) -> str:
    """Return one eval set in full: every case and every grader."""
    try:
        from evals import store
        evalset = store.get_eval_set(eval_set_id)
        if not evalset:
            return _json_err(f"Eval set '{eval_set_id}' not found", code="not_found")
        return _json_ok({"eval_set": evalset.to_dict()})
    except Exception as e:
        return _json_err(f"Failed to read the eval set: {e}", code="internal")


@tool("list_graders_tool", args_schema=BaseModel)
def list_graders_tool() -> str:
    """List the graders available, and which of them cost money to run.

    Pick the cheapest grader that can actually answer the question. `exact`,
    `substring`, `regex`, `json_valid`, `json_schema` and `assertions` are
    deterministic and free; `llm_judge` is neither, and its verdict is a model's
    opinion rather than a measurement.
    """
    try:
        from evals.graders import COSTED_GRADERS, GRADERS
        return _json_ok({
            "graders": [
                {"kind": kind, "costs_money": kind in COSTED_GRADERS}
                for kind in sorted(GRADERS)
            ],
            "note": "Prefer a deterministic grader when the question allows one.",
        })
    except Exception as e:
        return _json_err(f"Failed to list graders: {e}", code="internal")


class CreateEvalInput(BaseModel):
    name: str = Field(..., min_length=1, description="What this set measures")
    description: str = Field("", description="What a passing score would mean")
    agent_id: Optional[str] = Field(None, description="Default agent under test")
    workspace: Optional[str] = Field(None, description="Workspace to create it in")
    graders: Optional[List[Dict[str, Any]]] = Field(
        None, description='Grader specs, e.g. [{"kind": "substring"}, {"kind": "json_valid"}]')


@tool("create_eval_tool", args_schema=CreateEvalInput)
def create_eval_tool(name: str, description: str = "", agent_id: Optional[str] = None,
                     workspace: Optional[str] = None,
                     graders: Optional[List[Dict[str, Any]]] = None) -> str:
    """Create an eval set: a named dataset plus the graders that score it.

    Creating one costs nothing and runs nothing. Add cases with add_eval_case_tool,
    then run it once the user has approved the cost.
    """
    try:
        from evals import store
        from evals.models import EvalSet, GraderSpec

        evalset = EvalSet(
            name=name.strip(),
            description=description or "",
            workspace=workspace or resolve_active_workspace(),
            agent_id=agent_id or None,
            graders=[GraderSpec.from_dict(g) for g in (graders or [])],
        )
        saved = store.save_eval_set(evalset)
        return _json_ok({"eval_set": _summary(saved),
                         "next": "Add cases with add_eval_case_tool."})
    except Exception as e:
        return _json_err(f"Failed to create the eval set: {e}", code="internal")


class ModifyEvalInput(BaseModel):
    eval_set_id: str = Field(..., description="Eval set to change")
    name: Optional[str] = None
    description: Optional[str] = None
    agent_id: Optional[str] = Field(None, description="Default agent under test")
    graders: Optional[List[Dict[str, Any]]] = Field(
        None, description="Replaces the grader list when given")


@tool("modify_eval_tool", args_schema=ModifyEvalInput)
def modify_eval_tool(eval_set_id: str, name: Optional[str] = None,
                     description: Optional[str] = None, agent_id: Optional[str] = None,
                     graders: Optional[List[Dict[str, Any]]] = None) -> str:
    """Change an eval set's name, description, default agent or graders.

    Changing the graders changes what past runs mean, so scores from before the
    change are not comparable with scores after it. Say that when you do it.
    """
    try:
        from evals import store
        from evals.models import GraderSpec

        evalset = store.get_eval_set(eval_set_id)
        if not evalset:
            return _json_err(f"Eval set '{eval_set_id}' not found", code="not_found")
        if name is not None:
            evalset.name = name.strip()
        if description is not None:
            evalset.description = description
        if agent_id is not None:
            evalset.agent_id = agent_id or None
        if graders is not None:
            evalset.graders = [GraderSpec.from_dict(g) for g in graders]
        return _json_ok({"eval_set": _summary(store.save_eval_set(evalset))})
    except Exception as e:
        return _json_err(f"Failed to modify the eval set: {e}", code="internal")


class AddCaseInput(BaseModel):
    eval_set_id: str = Field(..., description="Eval set to add to")
    input: str = Field(..., min_length=1, description="The message sent to the agent")
    expected: Optional[str] = Field(None, description="Reference answer, for deterministic graders")
    rubric: Optional[str] = Field(None, description="Instruction for an LLM judge")
    source_run_id: Optional[str] = Field(None, description="The real run this case came from")


@tool("add_eval_case_tool", args_schema=AddCaseInput)
def add_eval_case_tool(eval_set_id: str, input: str, expected: Optional[str] = None,
                       rubric: Optional[str] = None,
                       source_run_id: Optional[str] = None) -> str:
    """Add one case to an eval set.

    A case needs whatever its graders read: `expected` for the match graders, a
    `rubric` for a judge. A case with neither can only be scored by a grader that
    needs no reference, like json_valid — which is a real choice, not an
    oversight, but say which one you are making.
    """
    try:
        from evals import store
        from evals.models import Case

        case = Case(input=input, expected=expected, rubric=rubric,
                    source_run_id=source_run_id)
        evalset = store.add_case(eval_set_id, case)
        if not evalset:
            return _json_err(f"Eval set '{eval_set_id}' not found", code="not_found")
        return _json_ok({"case_id": case.case_id, "eval_set": _summary(evalset)})
    except Exception as e:
        return _json_err(f"Failed to add the case: {e}", code="internal")


class RemoveCaseInput(BaseModel):
    eval_set_id: str = Field(..., description="Eval set to remove from")
    case_id: str = Field(..., description="Case to remove")


@tool("remove_eval_case_tool", args_schema=RemoveCaseInput)
def remove_eval_case_tool(eval_set_id: str, case_id: str) -> str:
    """Remove one case from an eval set. Past results for it are left alone."""
    try:
        from evals import store
        evalset = store.remove_case(eval_set_id, case_id)
        if not evalset:
            return _json_err(f"Eval set '{eval_set_id}' not found", code="not_found")
        return _json_ok({"eval_set": _summary(evalset)})
    except Exception as e:
        return _json_err(f"Failed to remove the case: {e}", code="internal")


# ── running, approval-gated ──────────────────────────────────────────────────

def _configs(raw: Optional[List[Dict[str, Any]]], evalset):
    from evals.models import RunConfig

    configs = [RunConfig.from_dict(c) for c in (raw or [])]
    if not configs and evalset.agent_id:
        configs = [RunConfig(agent_id=evalset.agent_id, label="baseline")]
    return configs


class EstimateEvalInput(BaseModel):
    eval_set_id: str = Field(..., description="Eval set to price")
    configs: Optional[List[Dict[str, Any]]] = Field(
        None, description='Columns to sweep, e.g. [{"agent_id": "x", "model": "gpt-4o"}]')


@tool("estimate_eval_tool", args_schema=EstimateEvalInput)
def estimate_eval_tool(eval_set_id: str,
                       configs: Optional[List[Dict[str, Any]]] = None) -> str:
    """Project what a sweep will cost before running it.

    Show this to the user before asking them to approve a run. The number is an
    order-of-magnitude estimate, not a quote: cases times configs, plus a judge
    call per cell when the graders include one.
    """
    try:
        from evals import store
        from evals.runner import project_cost

        evalset = store.get_eval_set(eval_set_id)
        if not evalset:
            return _json_err(f"Eval set '{eval_set_id}' not found", code="not_found")
        resolved = _configs(configs, evalset)
        if not resolved:
            return _json_err(
                "No configs given and the set has no default agent_id. Say which "
                "agent should be measured.", code="invalid")
        return _json_ok({"estimate": project_cost(evalset, resolved)})
    except Exception as e:
        return _json_err(f"Failed to estimate the sweep: {e}", code="internal")


class RunEvalInput(BaseModel):
    eval_set_id: str = Field(..., description="Eval set to run")
    configs: Optional[List[Dict[str, Any]]] = Field(
        None, description="Columns to sweep; omit for the set's default agent")
    cost_ceiling: Optional[float] = Field(
        None, gt=0, description="Stop the sweep when accumulated spend crosses this (USD)")
    user_approved: bool = Field(
        False, description="Set only after the user has approved the projected cost")


@tool("run_eval_tool", args_schema=RunEvalInput)
def run_eval_tool(eval_set_id: str, configs: Optional[List[Dict[str, Any]]] = None,
                  cost_ceiling: Optional[float] = None,
                  user_approved: bool = False) -> str:
    """Run an eval set and return the score matrix. Refuses until approved.

    A sweep is every case against every config, so the call count is their
    product — plus one judge call per cell when the graders include an LLM judge.
    The refusal carries the projection so the user approves with the number in
    front of them.

    Unlike a scenario or a loop, this runs to completion before answering: an
    eval is a measurement, and half of one is not useful. Set `cost_ceiling` on
    anything large; the workspace budget is re-checked per cell as well, so a
    sweep cannot walk past a hard cap one call at a time.
    """
    try:
        from evals import store
        from evals.runner import project_cost, run_eval

        evalset = store.get_eval_set(eval_set_id)
        if not evalset:
            return _json_err(f"Eval set '{eval_set_id}' not found", code="not_found")
        if not evalset.cases:
            return _json_err(
                f"Eval set '{evalset.name}' has no cases — there is nothing to "
                "measure. Add cases first.", code="invalid")
        resolved = _configs(configs, evalset)
        if not resolved:
            return _json_err(
                "No configs given and the set has no default agent_id. Say which "
                "agent should be measured.", code="invalid")

        if not user_approved:
            return _json_err(
                "Running this eval needs the user's approval: it spends real "
                "money. Show them the estimate below, and only call again with "
                "user_approved=True once they have agreed.",
                code="approval_required",
                extra={"estimate": project_cost(evalset, resolved),
                       "cases": len(evalset.cases),
                       "configs": [c.resolved_label() for c in resolved]},
            )

        run = run_eval(eval_set_id, resolved,
                       workspace=evalset.workspace, cost_ceiling=cost_ceiling)
        return _json_ok({"eval_run": run.to_dict(),
                         "matrix": store.build_matrix(run.eval_run_id)})
    except ValueError as e:
        return _json_err(str(e), code="invalid")
    except Exception as e:
        return _json_err(f"The eval run failed: {e}", code="internal")


# ── reading results ──────────────────────────────────────────────────────────

class ListRunsInput(BaseModel):
    eval_set_id: Optional[str] = Field(None, description="Filter to one eval set")
    limit: int = Field(20, ge=1, le=100)


@tool("list_eval_runs_tool", args_schema=ListRunsInput)
def list_eval_runs_tool(eval_set_id: Optional[str] = None, limit: int = 20) -> str:
    """List past eval runs, newest first, with their status and aggregate score.

    This is how "did the change help" gets answered: compare a run from before
    against a run from after, on the same set.
    """
    try:
        from evals import store
        runs = store.list_eval_runs(eval_set_id, limit)
        return _json_ok({"eval_runs": [r.to_dict() for r in runs]})
    except Exception as e:
        return _json_err(f"Failed to list eval runs: {e}", code="internal")


class GetRunInput(BaseModel):
    eval_run_id: str = Field(..., description="Eval run id, from list_eval_runs_tool")


@tool("get_eval_run_tool", args_schema=GetRunInput)
def get_eval_run_tool(eval_run_id: str) -> str:
    """Return one eval run with its score matrix and the cases behind it.

    A score with no input is not evidence: read the cells that failed and quote
    what the agent actually produced, rather than reporting the average and
    stopping.
    """
    try:
        from evals import store
        run = store.get_eval_run(eval_run_id)
        if not run:
            return _json_err(f"Eval run '{eval_run_id}' not found", code="not_found")
        evalset = store.get_eval_set(run.eval_set_id)
        return _json_ok({
            "eval_run": run.to_dict(),
            "eval_set_name": evalset.name if evalset else "",
            "cases": [c.to_dict() for c in (evalset.cases if evalset else [])],
            "matrix": store.build_matrix(eval_run_id),
        })
    except Exception as e:
        return _json_err(f"Failed to read the eval run: {e}", code="internal")


EVAL_TOOLS = [
    list_evals_tool, get_eval_tool, list_graders_tool,
    create_eval_tool, modify_eval_tool,
    add_eval_case_tool, remove_eval_case_tool,
    estimate_eval_tool, run_eval_tool,
    list_eval_runs_tool, get_eval_run_tool,
]

__all__ = [
    "list_evals_tool", "get_eval_tool", "list_graders_tool", "create_eval_tool",
    "modify_eval_tool", "add_eval_case_tool", "remove_eval_case_tool",
    "estimate_eval_tool", "run_eval_tool", "list_eval_runs_tool",
    "get_eval_run_tool", "EVAL_TOOLS",
]
