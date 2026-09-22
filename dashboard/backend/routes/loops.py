"""
Loops API — iterative flows with an agent-judged exit criterion.

``GET|POST /api/loops``                      list / create loops
``GET|PUT|DELETE /api/loops/{loop_id}``
``POST /api/loops/{loop_id}/estimate``       upper-bound call count for a full run
``POST /api/loops/{loop_id}/run``            start a loop run (background thread)
``GET  /api/loops/runs``                     recent runs
``GET  /api/loops/runs/{run_id}``            one run + its iterations
``GET  /api/loops/runs/{run_id}/iterations`` the iteration log (``?since=`` to poll)
``POST /api/loops/runs/{run_id}/stop``       ask a running loop to stop

A loop run is N passes of a whole flow — many minutes — so it starts on a
background thread and returns its id immediately. The UI follows the
``loop:<loop_run_id>`` channel, or polls the iteration log; both read the same
rows, so watching live and reviewing afterwards look identical.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loops import store
from loops.models import EVALUATOR_MODES, Loop
from loops.runner import LoopResumeError, estimate_cost, resume_loop_run, run_loop
from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router

router = APIRouter(prefix="/api/loops", tags=["loops"])


class LoopIn(BaseModel):
    name: str = ""
    description: str = ""
    workspace: Optional[str] = None
    flow_id: str = ""
    exit_criterion: str = ""
    max_iterations: int = 5
    min_iterations: int = 1
    target_score: Optional[float] = 80.0
    patience: int = 2
    cost_ceiling: Optional[float] = None
    max_wall_seconds: float = 3600.0
    evaluator_mode: str = "final_agent"
    evaluator_agent_id: Optional[str] = None
    evaluator_provider: Optional[str] = None
    evaluator_model: Optional[str] = None


class RunIn(BaseModel):
    goal: str = ""
    workspace: Optional[str] = None
    task_id: Optional[str] = None
    seed: Dict[str, Any] = {}


def _loop_from_in(data: LoopIn, existing: Optional[Loop] = None) -> Loop:
    payload = data.model_dump()
    payload["name"] = (data.name or "").strip()
    if existing:
        payload["loop_id"] = existing.loop_id
        payload["created_at"] = existing.created_at
    return Loop.from_dict(payload)


def _load_flow(flow_id: str):
    """The loop's flow, or None when it is missing *or unreadable*.

    A flow whose nodes reference a deleted agent raises rather than returning
    None, and a broken flow must not turn every loop listing into a 500 — the
    loop is still a real record the operator needs to see and fix.
    """
    from flow import store as flow_store
    from flow.store import FlowParseError
    try:
        return flow_store.get_flow(flow_id)
    except FlowParseError:
        return None


def _validate(data: LoopIn) -> None:
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not data.flow_id:
        raise HTTPException(status_code=400, detail="a loop must reference a flow")
    if data.evaluator_mode not in EVALUATOR_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"evaluator_mode must be one of {', '.join(EVALUATOR_MODES)}",
        )
    if data.evaluator_mode == "agent" and not data.evaluator_agent_id:
        raise HTTPException(
            status_code=400,
            detail="evaluator_mode 'agent' needs evaluator_agent_id",
        )
    if data.min_iterations > data.max_iterations:
        raise HTTPException(
            status_code=400, detail="min_iterations cannot exceed max_iterations",
        )
    from flow import store as flow_store
    from flow.store import FlowParseError
    try:
        flow = flow_store.get_flow(data.flow_id)
    except FlowParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not flow:
        raise HTTPException(status_code=400, detail=f"Flow not found: {data.flow_id}")


def _enrich(loop: Loop) -> Dict[str, Any]:
    """The loop plus what the list needs to be readable without a second call:
    the flow's name and who will actually be judging."""
    from loops.evaluator import resolve_evaluator

    out = loop.to_dict()
    flow = _load_flow(loop.flow_id)
    out["flow_name"] = (flow or {}).get("name") or loop.flow_id
    out["flow_exists"] = flow is not None
    mode, agent_id = resolve_evaluator(loop, flow or {})
    out["resolved_evaluator"] = {"mode": mode, "agent_id": agent_id}
    return out


# ── Definitions ──────────────────────────────────────────────────────────────

@router.get("")
async def list_loops(workspace: Optional[str] = None):
    return {"loops": [_enrich(l) for l in store.list_loops(workspace)]}


@router.post("")
async def create_loop(data: LoopIn):
    _validate(data)
    return _enrich(store.save_loop(_loop_from_in(data)))


@router.get("/runs")
async def list_runs(loop_id: Optional[str] = None, limit: int = 50):
    """Recent loop runs. Declared before ``/{loop_id}`` so "runs" is not
    swallowed as a loop id."""
    return {"runs": [r.to_dict() for r in store.list_runs(loop_id, limit)]}


@router.get("/runs/{loop_run_id}")
async def get_run(loop_run_id: str):
    run = store.get_run(loop_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Loop run not found")
    loop = store.get_loop(run.loop_id)
    return {
        **run.to_dict(),
        "loop": _enrich(loop) if loop else None,
        "iterations": store.list_iterations(loop_run_id),
    }


@router.get("/runs/{loop_run_id}/iterations")
async def get_iterations(loop_run_id: str, since: int = 0):
    """The iteration log — the artifact of record. ``since`` returns only later
    iterations so a live page can poll cheaply."""
    run = store.get_run(loop_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Loop run not found")
    return {
        "loop_run_id": loop_run_id,
        "status": run.status,
        "iterations_done": run.iterations_done,
        "best_score": run.best_score,
        "final_score": run.final_score,
        "stop_reason": run.stop_reason,
        "total_cost": run.total_cost,
        "error": run.error,
        "iterations": store.list_iterations(loop_run_id, since),
    }


@router.post("/runs/{loop_run_id}/resume")
async def resume_run(loop_run_id: str):
    """Continue a loop run from the iteration after its last completed one.

    A loop runs inside the backend process, so a restart ends it mid-run. Every
    finished iteration is a whole flow's worth of work, and the run's stored
    position is what makes picking it up cheaper than starting over. Runs on a
    background thread, like the initial start, and returns the run record.
    """
    run = store.get_run(loop_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Loop run not found")

    failure: Dict[str, Any] = {}
    ready = threading.Event()

    def _worker():
        try:
            resume_loop_run(loop_run_id, on_iteration=lambda _it: ready.set())
        except Exception as e:  # noqa: BLE001
            failure["error"] = f"{type(e).__name__}: {e}"
        finally:
            ready.set()

    # Refuse before starting the thread when the run is plainly not resumable,
    # so the caller gets the reason instead of a silently dead background task.
    try:
        _precheck_resumable(run)
    except LoopResumeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    threading.Thread(target=_worker, name=f"loop-resume-{loop_run_id}", daemon=True).start()
    ready.wait(timeout=1.0)
    if failure:
        raise HTTPException(status_code=400, detail=failure["error"])
    return (store.get_run(loop_run_id) or run).to_dict()


def _precheck_resumable(run) -> None:
    """Raise :class:`LoopResumeError` when this run cannot be resumed at all."""
    if run.status in ("completed", "stopped"):
        raise LoopResumeError(f"Loop run already finished ({run.status})")
    if not (run.position or {}).get("iterations_done"):
        raise LoopResumeError("Loop run has no position to resume from")


@router.post("/runs/{loop_run_id}/stop")
async def stop_run(loop_run_id: str):
    """Ask a running loop to stop. It is checked between nodes and between
    iterations, so the flow is never left half-applied."""
    if not store.request_stop(loop_run_id):
        raise HTTPException(status_code=400, detail="Run is not running")
    return {"ok": True}


@router.get("/{loop_id}")
async def get_loop(loop_id: str):
    loop = store.get_loop(loop_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Loop not found")
    return _enrich(loop)


@router.put("/{loop_id}")
async def update_loop(loop_id: str, data: LoopIn):
    existing = store.get_loop(loop_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Loop not found")
    _validate(data)
    return _enrich(store.save_loop(_loop_from_in(data, existing)))


@router.delete("/{loop_id}")
async def delete_loop(loop_id: str):
    if not store.delete_loop(loop_id):
        raise HTTPException(status_code=404, detail="Loop not found")
    # The build chat is keyed by loop id and would otherwise outlive the loop.
    try:
        from common.entity_chat_store import entity_chat_store
        entity_chat_store().delete(LOOP_CHAT_KIND, loop_id)
    except Exception:
        pass
    return {"ok": True}


@router.post("/{loop_id}/estimate")
async def estimate(loop_id: str):
    loop = store.get_loop(loop_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Loop not found")
    return estimate_cost(loop)


@router.post("/{loop_id}/run")
async def start_run(loop_id: str, data: RunIn):
    """Start a loop run on a background thread and return its record.

    The client needs a run id to follow, and only ``run_loop`` mints one, so we
    wait for the row to appear rather than duplicating the id logic here. The
    loop keeps running regardless of when this returns.
    """
    loop = store.get_loop(loop_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Loop not found")

    if not _load_flow(loop.flow_id):
        raise HTTPException(
            status_code=400,
            detail=f"Loop references a flow that is missing or unreadable: {loop.flow_id}",
        )

    ready = threading.Event()
    failure: Dict[str, Any] = {}

    def _worker():
        try:
            run_loop(
                loop_id, goal=data.goal, workspace=data.workspace or loop.workspace,
                task_id=data.task_id, seed=dict(data.seed or {}),
                on_iteration=lambda _it: ready.set(),
            )
        except Exception as e:  # noqa: BLE001
            failure["error"] = f"{type(e).__name__}: {e}"
        finally:
            ready.set()

    threading.Thread(target=_worker, name=f"loop-{loop_id}", daemon=True).start()

    for _ in range(60):
        runs = store.list_runs(loop_id, limit=1)
        if runs:
            return runs[0].to_dict()
        if failure:
            raise HTTPException(status_code=400, detail=failure["error"])
        ready.wait(timeout=0.05)

    if failure:
        raise HTTPException(status_code=400, detail=failure["error"])
    return {"loop_id": loop_id, "status": "starting"}


# ── Loop build chat ───────────────────────────────────────────────────────────
#
# A loop is three decisions — which flow, what "good enough" means, and when to
# give up — and two of them are prose. That is a conversation, not a form, so
# the loop gets a chat pinned to it: the Loop Creator edits this loop in place
# with its tools while the panel beside it updates.
#
# The plumbing (run record, streaming, cancellation, transcript) is shared with
# every other entity chat and lives in ``chat.entity_chat``; only the prompt is
# specific to a loop.

LOOP_AGENT_ID = "loop_creator"
LOOP_CHAT_KIND = "loop"


def _flow_catalog(workspace: Optional[str]) -> List[Dict[str, Any]]:
    """The flows this loop could wrap, as the prompt needs them.

    Each entry carries the node sequence, because the terminal node's agent is
    the default evaluator — "which flow" and "who judges it" are one decision.
    """
    from flow import store as flow_store

    out: List[Dict[str, Any]] = []
    try:
        flows = flow_store.list_flows()
    except Exception:
        return out
    for f in flows or []:
        if not isinstance(f, dict):
            continue
        # ``list_flows`` is workspace-blind; a loop can only wrap a flow from
        # its own workspace, and a global flow (no workspace) fits anywhere.
        flow_ws = f.get("workspace")
        if workspace and flow_ws and flow_ws != workspace:
            continue
        nodes = []
        for n in f.get("nodes") or []:
            data = n.get("data") or {} if isinstance(n, dict) else {}
            nodes.append(data.get("agent_id") or data.get("label") or n.get("id"))
        out.append({
            "flow_id": f.get("id"), "name": f.get("name"),
            "description": f.get("description") or "", "nodes": nodes,
        })
    return out


def _agent_catalog(workspace: Optional[str]) -> List[Dict[str, str]]:
    """The agents available as a dedicated evaluator."""
    from agents import registry
    from common.workspace_context import filter_agents_for_workspace

    specs = filter_agents_for_workspace(registry.list_agents(), workspace)
    return [
        {"id": s.id, "name": getattr(s, "name", "") or s.id,
         "description": (getattr(s, "description", "") or "")[:240]}
        for s in specs if s.id != LOOP_AGENT_ID
    ]


def _loop_state(loop: Loop) -> Dict[str, Any]:
    from tools.loop_management import _simplify
    return _simplify(loop)


def _loop_chat_prompt(loop: Loop, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the live loop, what it can be built from, the talk."""
    from chat.entity_chat import transcript_block

    state = _loop_state(loop)
    parts = [
        "You are editing ONE iteration loop in this platform. The user is looking "
        "at its page: every change you make with your tools appears in the panel "
        "beside this chat.",
        "",
        f"Loop under edit: {state['name']} (loop_id: {state['loop_id']})",
        f"Workspace: {state.get('workspace') or '—'}",
        "",
        "=== Current definition ===",
        json.dumps(state, ensure_ascii=False, indent=2),
        "",
        "=== Flows available to wrap ===",
        json.dumps(_flow_catalog(state.get("workspace")), ensure_ascii=False, indent=2),
        "",
        "=== Agents available as a dedicated evaluator ===",
        json.dumps(_agent_catalog(state.get("workspace")), ensure_ascii=False, indent=2),
        "",
        "Rules for this conversation:",
        f"- Apply every change to loop_id '{state['loop_id']}' with modify_loop_tool. "
        "Never create a second loop unless the user explicitly asks for a new one.",
        "- `convergence` merges into what is stored, so change one ceiling without "
        "restating the rest.",
        "- Only wrap flows listed above; you cannot create a flow from here. If the "
        "loop needs a flow that does not exist, say which one and stop.",
        "- When the user only asks a question, answer it without changing anything.",
        "- Finish with one short paragraph: what you changed, and under what "
        "conditions the loop will now stop.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _load_loop_chat(request):
    from types import SimpleNamespace

    loop_id = request.path_params["loop_id"]
    loop = store.get_loop(loop_id)
    if not loop:
        raise HTTPException(status_code=404, detail="Loop not found")
    return SimpleNamespace(entity_id=loop_id, workspace=loop.workspace, loop=loop)


def _load_loop_send(request, body):
    ctx = _load_loop_chat(request)
    ctx.before = _loop_state(ctx.loop)
    return ctx


def _loop_summarize(ctx):
    def _summarize() -> str:
        after_loop = store.get_loop(ctx.entity_id)
        if not after_loop:
            return "The loop is gone."
        after = _loop_state(after_loop)
        before = ctx.before
        if after == before:
            return ""
        bits = []
        if after["name"] != before["name"]:
            bits.append(f"renamed it to '{after['name']}'")
        if after["flow_id"] != before["flow_id"]:
            bits.append("pointed it at a different flow")
        if after["exit_criterion"] != before["exit_criterion"]:
            bits.append("rewrote the exit criterion")
        if after["convergence"] != before["convergence"]:
            bits.append("retuned when it stops")
        if after["evaluator"] != before["evaluator"]:
            bits.append("changed who judges each pass")
        return ("Done — " + ", ".join(bits) + ".") if bits else "Done — the loop was updated."
    return _summarize


def _loop_context_setup(ctx):
    # The builder's tools resolve the workspace from this ContextVar, so the
    # edit lands in the loop's own workspace, not the UI's current one.
    if ctx.loop.workspace:
        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ctx.loop.workspace)


async def _loop_post_turn(queue, ctx):
    after = store.get_loop(ctx.entity_id)
    if after:
        await queue.put({"type": "loop", "loop": _enrich(after)})


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=LOOP_CHAT_KIND,
    path="/{loop_id}/chat",
    load=_load_loop_chat,
    load_for_send=_load_loop_send,
    prompt=lambda ctx, history, msg: _loop_chat_prompt(
        store.get_loop(ctx.entity_id) or ctx.loop, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=LOOP_CHAT_KIND, agent_id=LOOP_AGENT_ID,
        title=f"{ctx.loop.name} · loop", workspace=ctx.loop.workspace,
    ),
    summarize=_loop_summarize,
    context_setup=_loop_context_setup,
    post_turn=_loop_post_turn,
)))
