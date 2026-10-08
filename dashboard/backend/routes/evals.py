"""
Eval harness API — datasets, sweeps, score matrices.

``GET|POST /api/evals``                       list / create eval sets
``GET|PUT|DELETE /api/evals/{id}``            read / update / delete a set
``POST /api/evals/{id}/cases``                add a case (from a run or a task)
``DELETE /api/evals/{id}/cases/{case_id}``    remove a case
``POST /api/evals/{id}/estimate``             projected spend before a sweep
``POST /api/evals/{id}/run``                  run the sweep (blocking, billable)
``GET /api/evals/{id}/runs``                  history for a set
``GET /api/evals/runs/{a}/diff/{b}``          compare two runs cell by cell
``GET /api/eval-runs/{run_id}``               one run + its score matrix

Roles (multi mode), on the workspace of the set (a set without one is the
default workspace's): reading a set, its runs, a diff, an estimate or the
suggestions needs viewer; creating, changing or deleting a set or a case,
running, polling or cancelling a sweep, and suggesting, applying or
dismissing a prompt need editor. A run started into another workspace needs editor there too.

Sweeps are real, slow, billable LLM calls, so the run handler is a plain ``def``
— FastAPI puts it on a worker thread instead of blocking the event loop, the
same shape ``routes/replay.py`` uses.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import access, identity
from common.auth import WS_EDITOR, WS_VIEWER
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
    # When set, the case carries a snapshot of this task (evals.snapshot):
    # its description, context, documents and a capped slice of its project
    # files, and runs in an isolated directory holding those files.
    from_task_id: Optional[str] = None
    # A snapshot given directly, in the same shape snapshot_task returns.
    artifact: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = {}
    # Workspace files (files/service.py) the case runs with: copied into its
    # isolated directory and named in its input (evals.runner.prepare_work_dir).
    file_ids: List[str] = []


class TargetIn(BaseModel):
    kind: str = "agent"   # agent | flow | team | loop | scenario
    id: str = ""


class EvalSetIn(BaseModel):
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    # The default target (the baseline column). ``agent_id`` is the
    # compatibility alias: set alone it means an agent target.
    target: Optional[TargetIn] = None
    agent_id: Optional[str] = None
    cases: List[CaseIn] = []
    graders: List[GraderIn] = []
    # A finished sweep on an agent target that leaves failed cases builds a
    # prompt suggestion on its own (evals/prompt_suggest.py). Off by default.
    suggest_on_failure: bool = False


class ConfigIn(BaseModel):
    target: Optional[TargetIn] = None
    # Compatibility alias for an agent target.
    agent_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    label: str = ""
    # Runs each case this many times under this config (capped in RunConfig)
    # so sampling variance shows up as a spread instead of one lucky draw.
    repeats: int = 1
    # Per kind overrides: max_rounds, max_iterations, max_ticks,
    # trigger_agent (see evals/targets.py for which kind reads which).
    settings: Dict[str, Any] = {}


class RunEvalIn(BaseModel):
    configs: List[ConfigIn] = []
    workspace: Optional[str] = None
    # Stops the sweep once accumulated spend crosses this (USD).
    cost_ceiling: Optional[float] = None
    # "live" runs every cell now; "batch" sends agent cells and judge calls
    # through the provider batch APIs at half price (evals/batch.py).
    mode: str = "live"


def _artifact_from_in(c: CaseIn) -> Optional[Dict[str, Any]]:
    if c.from_task_id:
        from evals.snapshot import snapshot_task
        return snapshot_task(c.from_task_id)
    return dict(c.artifact) if c.artifact else None


def _case_file_ids(raw: List[str], workspace: Optional[str]) -> List[str]:
    """A case's workspace files, de-duplicated in order. ValueError (a 400 at
    the callers) for an id that is unknown, deleted, or of another workspace
    than the set's: a case must never copy another workspace's file into its
    run."""
    from files import service as files_service
    out: List[str] = []
    for fid in raw or []:
        fid = str(fid or "").strip()
        if not fid or fid in out:
            continue
        record = files_service.get_file(fid)
        if record is None:
            raise ValueError(f"Workspace file '{fid}' not found")
        if record["workspace"] != (workspace or "default"):
            raise ValueError(f"Workspace file '{fid}' belongs to another workspace than this eval set")
        out.append(fid)
    return out


def _case_from_in(c: CaseIn, workspace: Optional[str] = None) -> Case:
    artifact = _artifact_from_in(c)
    file_ids = _case_file_ids(c.file_ids, workspace)
    if c.from_run_id:
        case = case_from_run(c.from_run_id, expected=c.expected, rubric=c.rubric)
        if c.input.strip():
            case.input = c.input
        case.metadata.update(c.metadata or {})
        case.artifact = artifact
        case.file_ids = file_ids
        return case
    metadata = dict(c.metadata or {})
    if c.from_task_id:
        metadata.setdefault("task_id", c.from_task_id)
    return Case(
        input=c.input, expected=c.expected, rubric=c.rubric,
        metadata=metadata, artifact=artifact, file_ids=file_ids,
    )


def _target_of(target: Optional[TargetIn]) -> Optional[Dict[str, str]]:
    return target.model_dump() if target is not None and target.id else None


def _configs_from_in(items: List[ConfigIn]) -> List[RunConfig]:
    out = []
    for c in items:
        target = _target_of(c.target)
        if not target and not c.agent_id:
            raise ValueError("each config needs a target (or an agent_id)")
        out.append(RunConfig(
            agent_id=c.agent_id or "", target=target or {}, provider=c.provider,
            model=c.model, label=c.label, repeats=c.repeats, settings=dict(c.settings or {}),
        ))
    return out


# ── Roles ─────────────────────────────────────────────────────────────────────

def _require(request: Optional[Request], workspace: Optional[str], role: str) -> None:
    identity.require_role(identity.request_principal(request), workspace=workspace or "default", role=role)


def _set_or_404(eval_set_id: str, request: Optional[Request], role: str) -> EvalSet:
    evalset = store.get_eval_set(eval_set_id)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    _require(request, evalset.workspace, role)
    return evalset


def _run_or_404(eval_run_id: str, request: Optional[Request], role: str):
    run = store.get_eval_run(eval_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    workspace = run.workspace
    if not workspace:
        evalset = store.get_eval_set(run.eval_set_id)
        workspace = evalset.workspace if evalset else None
    _require(request, workspace, role)
    return run


def _suggestion_or_404(suggestion_id: str, request: Optional[Request]) -> None:
    suggestion = store.get_prompt_suggestion(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Prompt suggestion not found")
    _require(request, suggestion.workspace, WS_EDITOR)


# ── Eval sets ─────────────────────────────────────────────────────────────────

@router.get("/evals")
async def list_evals(workspace: Optional[str] = None, request: Request = None):
    if workspace:
        _require(request, workspace, WS_VIEWER)
    sets = [e.to_dict() for e in store.list_eval_sets(workspace)]
    principal = identity.request_principal(request)
    return {"eval_sets": [e for e in sets
                          if access.can_see_workspace(principal, e.get("workspace") or "default")]}


@router.post("/evals")
async def create_eval(data: EvalSetIn, request: Request = None):
    _require(request, data.workspace, WS_EDITOR)
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    try:
        cases = [_case_from_in(c, data.workspace) for c in data.cases]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        evalset = EvalSet(
            name=data.name.strip(),
            description=data.description,
            workspace=data.workspace,
            agent_id=data.agent_id,
            target=_target_of(data.target),
            cases=cases,
            graders=[GraderSpec(kind=g.kind, params=g.params, weight=g.weight) for g in data.graders],
            suggest_on_failure=data.suggest_on_failure,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
            "target": d.get("target"),
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
    # Reading the history is a viewer's; sending or clearing it acts on the
    # workspace's sets, an editor's.
    _require(request, ws, WS_VIEWER if request.method == "GET" else WS_EDITOR)
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
async def get_eval(eval_set_id: str, request: Request = None):
    return _set_or_404(eval_set_id, request, WS_VIEWER).to_dict()


@router.put("/evals/{eval_set_id}")
async def update_eval(eval_set_id: str, data: EvalSetIn, request: Request = None):
    evalset = _set_or_404(eval_set_id, request, WS_EDITOR)
    if data.name.strip():
        evalset.name = data.name.strip()
    evalset.description = data.description
    evalset.suggest_on_failure = data.suggest_on_failure
    try:
        if data.target is not None:
            evalset.set_target(_target_of(data.target))
        elif data.agent_id is not None:
            evalset.set_target(None, data.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data.graders:
        evalset.graders = [
            GraderSpec(kind=g.kind, params=g.params, weight=g.weight) for g in data.graders
        ]
    # Cases are only replaced when the caller sends some — a PUT that omits
    # them edits the set's metadata rather than silently emptying the dataset.
    if data.cases:
        try:
            evalset.cases = [_case_from_in(c, evalset.workspace) for c in data.cases]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return store.save_eval_set(evalset).to_dict()


@router.delete("/evals/{eval_set_id}")
async def delete_eval(eval_set_id: str, request: Request = None):
    _set_or_404(eval_set_id, request, WS_EDITOR)
    if not store.delete_eval_set(eval_set_id):
        raise HTTPException(status_code=404, detail="Eval set not found")
    return {"ok": True}


# ── Cases ─────────────────────────────────────────────────────────────────────

@router.post("/evals/{eval_set_id}/cases")
async def add_eval_case(eval_set_id: str, data: CaseIn, request: Request = None):
    """Add a case. With ``from_run_id`` this is the "save this run as an eval
    case" button, the cheapest way to seed a dataset from real traffic. With
    ``from_task_id`` the case carries a snapshot of that task (see
    evals/snapshot.py for what is copied and what never is)."""
    existing = _set_or_404(eval_set_id, request, WS_EDITOR)
    try:
        case = _case_from_in(data, existing.workspace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    evalset = store.add_case(eval_set_id, case)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    return {"eval_set": evalset.to_dict(), "case": case.to_dict()}


@router.delete("/evals/{eval_set_id}/cases/{case_id}")
async def delete_eval_case(eval_set_id: str, case_id: str, request: Request = None):
    _set_or_404(eval_set_id, request, WS_EDITOR)
    evalset = store.remove_case(eval_set_id, case_id)
    if not evalset:
        raise HTTPException(status_code=404, detail="Eval set not found")
    return evalset.to_dict()


@router.get("/evals/for-run/{run_id}")
async def eval_sets_for_run(run_id: str, request: Request = None):
    """What the "To eval case" dialog needs for one run: what kind of run it
    is (agent, flow, team, loop or scenario — whichever store the id belongs
    to), every eval set whose target fits it (so the dialog can offer them
    plus "new set" without the user picking a target by hand), and a preview
    of the case ``case_from_run`` would build, to seed the dialog's editable
    fields."""
    from evals.runner import _run_target_info, case_from_run

    info = _run_target_info(run_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Run not found")
    _require(request, info["workspace"], WS_VIEWER)
    from common.run_status import is_terminal
    fitting = [
        e.to_dict() for e in store.list_eval_sets(info["workspace"])
        if e.target_kind == info["target_kind"]
    ]
    preview, preview_error = None, None
    try:
        case = case_from_run(run_id)
        preview = {"input": case.input, "expected": case.expected, "metadata": case.metadata}
    except ValueError as e:
        preview_error = str(e)
    return {
        "run": {
            "run_id": run_id, "target_kind": info["target_kind"], "target_id": info["target_id"],
            "workspace": info["workspace"], "status": info["status"],
            "failed": info["status"] in ("failed", "error"),
            "finished": is_terminal(info["status"]),
        },
        "eval_sets": fitting,
        "preview": preview,
        "preview_error": preview_error,
    }


# ── Running ───────────────────────────────────────────────────────────────────

@router.post("/evals/{eval_set_id}/estimate")
async def estimate_eval(eval_set_id: str, data: RunEvalIn, request: Request = None):
    """Projected spend for a sweep — surfaced before it starts, not after."""
    evalset = _set_or_404(eval_set_id, request, WS_VIEWER)
    try:
        configs = _configs_from_in(data.configs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not configs:
        baseline = evalset.default_config()
        configs = [baseline] if baseline else []
    if not configs:
        raise HTTPException(status_code=400, detail="No configs and no default target on the set")
    return project_cost(evalset, configs, mode=data.mode)


@router.post("/evals/{eval_set_id}/run")
def start_eval_run(eval_set_id: str, data: Optional[RunEvalIn] = None, request: Request = None):
    data = data or RunEvalIn()
    evalset = _set_or_404(eval_set_id, request, WS_EDITOR)
    if data.workspace and data.workspace != (evalset.workspace or "default"):
        _require(request, data.workspace, WS_EDITOR)
    try:
        run = run_eval(
            eval_set_id,
            _configs_from_in(data.configs),
            workspace=data.workspace,
            cost_ceiling=data.cost_ceiling,
            mode=data.mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Eval run failed: {e}")
    out = {**run.to_dict(), "matrix": store.build_matrix(run.eval_run_id)}
    if run.mode == "batch":
        from evals.batch import progress
        out["batch"] = progress(run.eval_run_id)
    return out


@router.get("/evals/{eval_set_id}/runs")
async def list_eval_runs_for_set(eval_set_id: str, limit: int = 50, request: Request = None):
    _set_or_404(eval_set_id, request, WS_VIEWER)
    return {"eval_runs": [r.to_dict() for r in store.list_eval_runs(eval_set_id, limit)]}


@router.get("/evals/runs/{run_a_id}/diff/{run_b_id}")
async def diff_eval_runs(run_a_id: str, run_b_id: str, request: Request = None):
    """Compare two eval runs cell by cell: which cases got fixed, which regressed.

    Matches by (case id, config label) using each run's own recorded results,
    so it works across two runs of the same set even if their config lists
    differ, and regardless of whether either run used repeats.
    """
    _run_or_404(run_a_id, request, WS_VIEWER)
    _run_or_404(run_b_id, request, WS_VIEWER)
    try:
        return diff_runs(run_a_id, run_b_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/eval-runs/{eval_run_id}")
async def get_eval_run_details(eval_run_id: str, request: Request = None):
    run = _run_or_404(eval_run_id, request, WS_VIEWER)
    evalset = store.get_eval_set(run.eval_set_id)
    return {
        **run.to_dict(),
        # The matrix and the cases travel together: a score is meaningless
        # without the input that produced it.
        "cases": [c.to_dict() for c in (evalset.cases if evalset else [])],
        "eval_set_name": evalset.name if evalset else "",
        "matrix": store.build_matrix(eval_run_id),
        **({"batch": _batch_progress(eval_run_id)} if run.mode == "batch" else {}),
    }


def _batch_progress(eval_run_id: str) -> Dict[str, Any]:
    from evals.batch import progress
    return progress(eval_run_id)


@router.post("/eval-runs/{eval_run_id}/cancel")
def cancel_eval_run(eval_run_id: str, request: Request = None):
    """Cancel a batch run's open provider batches. Answers the provider already
    produced are still collected; the rest of the cells are recorded as stopped."""
    _run_or_404(eval_run_id, request, WS_EDITOR)
    from evals.batch import cancel_run
    try:
        run = cancel_run(eval_run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**run.to_dict(), "batch": _batch_progress(eval_run_id)}


@router.post("/eval-runs/{eval_run_id}/poll")
def poll_eval_run(eval_run_id: str, request: Request = None):
    """Check this run's provider batches now instead of at the next scheduler
    tick, and process any that ended."""
    from evals.batch import poll_pending
    _run_or_404(eval_run_id, request, WS_EDITOR)
    poll_pending(force=True, background=False)
    run = store.get_eval_run(eval_run_id)
    return {**run.to_dict(), "batch": _batch_progress(eval_run_id),
            "matrix": store.build_matrix(eval_run_id)}


# ── Prompt suggestions ────────────────────────────────────────────────────────
#
# A revised instructions.md proposed from an eval run's failed cases
# (evals/prompt_suggest.py). Suggesting is a model call (billable, recorded as
# its own run); applying and dismissing are free.

class ApplySuggestionIn(BaseModel):
    # Also starts the same eval set again once the new instructions.md is
    # written, so the before/after can be compared with the existing diff route.
    rerun: bool = False


@router.post("/eval-runs/{eval_run_id}/suggest-prompt")
def suggest_prompt(eval_run_id: str, request: Request = None):
    """Build a prompt suggestion from this eval run's failed cases. A plain
    ``def`` like ``start_eval_run``: it is one real model call, not free."""
    _run_or_404(eval_run_id, request, WS_EDITOR)
    from evals.prompt_suggest import SuggestionError, build_suggestion
    try:
        suggestion = build_suggestion(eval_run_id)
    except SuggestionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return suggestion.to_dict()


@router.get("/eval-runs/{eval_run_id}/suggestions")
async def list_suggestions(eval_run_id: str, request: Request = None):
    _run_or_404(eval_run_id, request, WS_VIEWER)
    return {"suggestions": [s.to_dict() for s in store.list_prompt_suggestions(eval_run_id)]}


@router.post("/prompt-suggestions/{suggestion_id}/apply")
def apply_suggestion_route(suggestion_id: str, data: Optional[ApplySuggestionIn] = None,
                           request: Request = None):
    """Write the suggestion's instructions.md (through the definition editor's
    own path, so it is snapshotted), and optionally re-run the eval set — a
    plain ``def`` since a rerun is a real sweep, not free."""
    _suggestion_or_404(suggestion_id, request)
    from evals.prompt_suggest import SuggestionError, apply_suggestion
    try:
        return apply_suggestion(suggestion_id, rerun=bool(data and data.rerun))
    except SuggestionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/prompt-suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion_route(suggestion_id: str, request: Request = None):
    _suggestion_or_404(suggestion_id, request)
    from evals.prompt_suggest import SuggestionError, dismiss_suggestion
    try:
        return dismiss_suggestion(suggestion_id).to_dict()
    except SuggestionError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
