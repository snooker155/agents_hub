"""
Eval harness API — datasets, sweeps, score matrices.

``GET|POST /api/evals``                       list / create eval sets
``GET|PUT|DELETE /api/evals/{id}``            read / update / delete a set
``POST /api/evals/{id}/cases``                add a case (optionally from a run)
``DELETE /api/evals/{id}/cases/{case_id}``    remove a case
``POST /api/evals/{id}/estimate``             projected spend before a sweep
``POST /api/evals/{id}/run``                  run the sweep (blocking, billable)
``GET /api/evals/{id}/runs``                  history for a set
``GET /api/evals/runs/{a}/diff/{b}``          compare two runs cell by cell
``GET /api/eval-runs/{run_id}``               one run + its score matrix

Sweeps are real, slow, billable LLM calls, so the run handler is a plain ``def``
— FastAPI puts it on a worker thread instead of blocking the event loop, the
same shape ``routes/replay.py`` uses.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evals import store
from evals.models import Case, EvalSet, GraderSpec, RunConfig
from evals.runner import case_from_run, diff_runs, project_cost, run_eval
from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router

router = APIRouter(prefix="/api", tags=["evals"])


# ── Request models ────────────────────────────────────────────────────────────

class GraderIn(BaseModel):
    kind: str = "substring"
    params: Dict[str, Any] = {}
    weight: float = 1.0


class CaseIn(BaseModel):
    input: str = ""
    expected: Optional[str] = None
    rubric: Optional[str] = None
    # When set, the case is seeded from this run: its recorded user message
    # becomes the input and its output becomes `expected` unless overridden.
    from_run_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class EvalSetIn(BaseModel):
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    agent_id: Optional[str] = None
    cases: List[CaseIn] = []
    graders: List[GraderIn] = []


class ConfigIn(BaseModel):
    agent_id: str
    provider: Optional[str] = None
    model: Optional[str] = None
    label: str = ""
    # Runs each case this many times under this config (capped in RunConfig)
    # so sampling variance shows up as a spread instead of one lucky draw.
    repeats: int = 1


class RunEvalIn(BaseModel):
    configs: List[ConfigIn] = []
    workspace: Optional[str] = None
    # Stops the sweep once accumulated spend crosses this (USD).
    cost_ceiling: Optional[float] = None


def _case_from_in(c: CaseIn) -> Case:
    if c.from_run_id:
        case = case_from_run(c.from_run_id, expected=c.expected, rubric=c.rubric)
        if c.input.strip():
            case.input = c.input
        case.metadata.update(c.metadata or {})
        return case
    return Case(
        input=c.input, expected=c.expected, rubric=c.rubric,
        metadata=dict(c.metadata or {}),
    )


def _configs_from_in(items: List[ConfigIn]) -> List[RunConfig]:
    return [
        RunConfig(agent_id=c.agent_id, provider=c.provider, model=c.model,
                  label=c.label, repeats=c.repeats)
        for c in items
    ]


# ── Eval sets ─────────────────────────────────────────────────────────────────

@router.get("/evals")
async def list_evals(workspace: Optional[str] = None):
    return {"eval_sets": [e.to_dict() for e in store.list_eval_sets(workspace)]}


@router.post("/evals")
async def create_eval(data: EvalSetIn):
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    try:
        cases = [_case_from_in(c) for c in data.cases]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    evalset = EvalSet(
        name=data.name.strip(),
        description=data.description,
        workspace=data.workspace,
        agent_id=data.agent_id,
        cases=cases,
        graders=[GraderSpec(kind=g.kind, params=g.params, weight=g.weight) for g in data.graders],
    )
    return store.save_eval_set(evalset).to_dict()


# ── The Eval Agent's chat ────────────────────────────────────────────────────
#
# Workspace-level rather than per-set, because the first thing anyone wants is a
# set that does not exist yet: a chat pinned to a row could not build one.
#
# Declared before the ``/evals/{eval_set_id}`` routes below — "chat" is a
# literal path, and a catch-all declared first would read it as a set id.

EVAL_AGENT_ID = "eval_agent"
EVAL_CHAT_KIND = "evals"


def _eval_chat_id(workspace: Optional[str]) -> str:
    return (workspace or "default").strip() or "default"


def _eval_state(workspace: str) -> Dict[str, Any]:
    """The sets in this workspace and their most recent runs."""
    sets = []
    for e in store.list_eval_sets(workspace):
        d = e.to_dict()
        sets.append({
            "eval_set_id": d.get("eval_set_id"),
            "name": d.get("name"),
            "description": d.get("description"),
            "agent_id": d.get("agent_id"),
            "cases": len(d.get("cases") or []),
            "graders": [g.get("kind") for g in (d.get("graders") or [])],
        })
    runs = [r.to_dict() for r in store.list_eval_runs(None, 10)]
    return {"workspace": workspace, "eval_sets": sets, "recent_runs": runs}


def _eval_chat_prompt(workspace: str, history: List[dict], user_message: str) -> str:
    """One turn's prompt: what exists here, then the talk."""
    from chat.entity_chat import transcript_block

    state = _eval_state(workspace)
    parts = [
        "You are on the Evals page of this platform. The user is looking at the "
        "sets and runs in this workspace: everything you build or run appears "
        "there.",
        "",
        f"Workspace: {workspace}",
        "",
        "=== Eval sets here ===",
        json.dumps(state["eval_sets"], ensure_ascii=False, indent=2, default=str),
        "",
        "=== Recent runs ===",
        json.dumps(state["recent_runs"], ensure_ascii=False, indent=2, default=str),
        "",
        "Rules for this conversation:",
        f"- Create sets in workspace '{workspace}' unless the user names another.",
        "- Building a set costs nothing. Running one costs real money, so price "
        "it with estimate_eval_tool, show the number, and wait for a clear yes "
        "before calling run_eval_tool with user_approved=True.",
        "- Prefer a deterministic grader. An LLM judge on a question a regex "
        "could answer adds cost and noise, and its verdict is a model's opinion.",
        "- A suite where everything passes measured nothing. Write cases that "
        "can fail, and prefer cases seeded from real runs over invented ones.",
        "- When reporting a result, open the failing cells and quote what the "
        "agent produced. The average says something changed; the cells say what.",
        "- When the user only asks a question, answer it without building or "
        "running anything.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _load_eval_chat(request):
    from types import SimpleNamespace

    ws = _eval_chat_id(request.query_params.get("workspace"))
    return SimpleNamespace(entity_id=ws, workspace=ws)


def _load_eval_send(request, body):
    from common.bootstrap import ensure_system_agent

    if not ensure_system_agent(EVAL_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{EVAL_AGENT_ID}' agent is not registered")
    ctx = _load_eval_chat(request)
    ctx.before = _eval_state(ctx.workspace)
    return ctx


def _eval_summarize(ctx):
    def _summarize() -> str:
        after = _eval_state(ctx.workspace)
        before = ctx.before
        if after == before:
            return ""
        was = {e["eval_set_id"] for e in before["eval_sets"]}
        now = {e["eval_set_id"] for e in after["eval_sets"]}
        bits = []
        if now - was:
            bits.append(f"created {len(now - was)} eval set(s)")
        if len(after["recent_runs"]) != len(before["recent_runs"]):
            bits.append("ran a sweep")
        if not bits:
            bits.append("updated a set")
        return "Done — " + ", ".join(bits) + "."
    return _summarize


def _eval_context_setup(ctx):
    from common.workspace_context import _workspace_ctx

    # The eval tools resolve the workspace from this ContextVar, so a set
    # lands where the user is looking.
    _workspace_ctx.set(ctx.workspace)


async def _eval_post_turn(queue, ctx):
    await queue.put({"type": "evals", **_eval_state(ctx.workspace)})


# Declared before the ``/evals/{eval_set_id}`` routes below — "chat" is a
# literal path, and a catch-all declared first would read it as a set id.
router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=EVAL_CHAT_KIND,
    path="/evals/chat",
    load=_load_eval_chat,
    load_for_send=_load_eval_send,
    prompt=lambda ctx, history, msg: _eval_chat_prompt(ctx.workspace, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=EVAL_CHAT_KIND, agent_id=EVAL_AGENT_ID,
        title=f"{ctx.workspace} · evals", workspace=ctx.workspace,
        # A sweep runs to completion inside the turn, so the ceiling has to
        # allow for a long single tool call rather than many short ones.
        max_iterations=60,
    ),
    summarize=_eval_summarize,
    context_setup=_eval_context_setup,
    post_turn=_eval_post_turn,
)))


@router.get("/evals/{eval_set_id}")
async def get_eval(eval_set_id: str):
    evalset = store.get_eval_set(eval_set_id)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    return evalset.to_dict()


@router.put("/evals/{eval_set_id}")
async def update_eval(eval_set_id: str, data: EvalSetIn):
    evalset = store.get_eval_set(eval_set_id)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    if data.name.strip():
        evalset.name = data.name.strip()
    evalset.description = data.description
    if data.agent_id is not None:
        evalset.agent_id = data.agent_id
    if data.graders:
        evalset.graders = [
            GraderSpec(kind=g.kind, params=g.params, weight=g.weight) for g in data.graders
        ]
    # Cases are only replaced when the caller sends some — a PUT that omits
    # them edits the set's metadata rather than silently emptying the dataset.
    if data.cases:
        try:
            evalset.cases = [_case_from_in(c) for c in data.cases]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return store.save_eval_set(evalset).to_dict()


@router.delete("/evals/{eval_set_id}")
async def delete_eval(eval_set_id: str):
    if not store.delete_eval_set(eval_set_id):
        raise HTTPException(status_code=404, detail="Eval set not found")
    return {"ok": True}


# ── Cases ─────────────────────────────────────────────────────────────────────

@router.post("/evals/{eval_set_id}/cases")
async def add_eval_case(eval_set_id: str, data: CaseIn):
    """Add a case. With ``from_run_id`` this is the "save this run as an eval
    case" button — the cheapest way to seed a dataset from real traffic."""
    try:
        case = _case_from_in(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    evalset = store.add_case(eval_set_id, case)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    return {"eval_set": evalset.to_dict(), "case": case.to_dict()}


@router.delete("/evals/{eval_set_id}/cases/{case_id}")
async def delete_eval_case(eval_set_id: str, case_id: str):
    evalset = store.remove_case(eval_set_id, case_id)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    return evalset.to_dict()


# ── Running ───────────────────────────────────────────────────────────────────

@router.post("/evals/{eval_set_id}/estimate")
async def estimate_eval(eval_set_id: str, data: RunEvalIn):
    """Projected spend for a sweep — surfaced before it starts, not after."""
    evalset = store.get_eval_set(eval_set_id)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    configs = _configs_from_in(data.configs)
    if not configs and evalset.agent_id:
        configs = [RunConfig(agent_id=evalset.agent_id, label="baseline")]
    if not configs:
        raise HTTPException(status_code=400, detail="No configs and no default agent_id on the set")
    return project_cost(evalset, configs)


@router.post("/evals/{eval_set_id}/run")
def start_eval_run(eval_set_id: str, data: Optional[RunEvalIn] = None):
    data = data or RunEvalIn()
    try:
        run = run_eval(
            eval_set_id,
            _configs_from_in(data.configs),
            workspace=data.workspace,
            cost_ceiling=data.cost_ceiling,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Eval run failed: {e}")
    return {**run.to_dict(), "matrix": store.build_matrix(run.eval_run_id)}


@router.get("/evals/{eval_set_id}/runs")
async def list_eval_runs_for_set(eval_set_id: str, limit: int = 50):
    return {"eval_runs": [r.to_dict() for r in store.list_eval_runs(eval_set_id, limit)]}


@router.get("/evals/runs/{run_a_id}/diff/{run_b_id}")
async def diff_eval_runs(run_a_id: str, run_b_id: str):
    """Compare two eval runs cell by cell: which cases got fixed, which regressed.

    Matches by (case id, config label) using each run's own recorded results,
    so it works across two runs of the same set even if their config lists
    differ, and regardless of whether either run used repeats.
    """
    try:
        return diff_runs(run_a_id, run_b_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/eval-runs/{eval_run_id}")
async def get_eval_run_details(eval_run_id: str):
    run = store.get_eval_run(eval_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    evalset = store.get_eval_set(run.eval_set_id)
    return {
        **run.to_dict(),
        # The matrix and the cases travel together: a score is meaningless
        # without the input that produced it.
        "cases": [c.to_dict() for c in (evalset.cases if evalset else [])],
        "eval_set_name": evalset.name if evalset else "",
        "matrix": store.build_matrix(eval_run_id),
    }


@router.get("/eval-graders")
async def list_graders():
    """The grader catalog, for the eval editor's dropdown."""
    from evals.graders import COSTED_GRADERS, GRADERS
    return {
        "graders": [
            {
                "kind": kind,
                "description": (fn.__doc__ or "").strip().split("\n")[0],
                "costs_tokens": kind in COSTED_GRADERS,
            }
            for kind, fn in GRADERS.items()
        ]
    }
