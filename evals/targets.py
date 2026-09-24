"""
Eval targets other than a single agent: what running a case means for a
flow, a team, a loop and a scenario, and what the graders score.

Each adapter has the shape ``fn(case, cfg, evalset, eval_run_id, workspace,
*, prompt, work_dir=None, attempt=1) -> Outcome`` and is registered in
``evals.runner.TARGET_RUNNERS``. ``prompt`` is the case input with the task
block of an artifact already prepended; ``work_dir`` is the isolated
directory a case with an artifact runs in.

What ``output`` means, per kind:

- **flow**: the output of the last node that produced one (the node that
  finished last), else the engine's combined output;
- **team**: the team's result, the synthesis or the last substantive board
  message (``TeamRun.result``);
- **loop**: the accepted result of the loop (``LoopRun.result``);
- **scenario**: a compact text rendering of the scores and the final state.

Overrides, per kind:

- **flow**: provider and model go to every agent node through the task
  driver's ``agent_overrides``; ``work_dir`` is the nodes' working directory.
  ``settings`` is not read;
- **team** and **loop**: provider and model are ignored (members and nodes run
  on their own agents' models, and ``run_team`` / ``run_loop`` take no
  override), and so are ``max_rounds`` / ``max_iterations`` (both runners read
  the stored team or loop). ``work_dir`` is not their working directory: its
  path is named in the task block of the prompt instead;
- **scenario**: provider and model become the frozen config's
  ``default_provider`` / ``default_model`` and every role's own override, so
  the model under test answers for every role; ``settings.max_ticks`` caps the
  ticks, ``settings.trigger_agent`` names the role the case input is sent to
  (default: the first role).

Eval channel: leaf runs of a container are opened by the container's own
code with its own channel (``flow``, ``team``...). Once the container
finishes, every leaf run is retagged ``channel="eval"`` so cost and budget
aggregation skip it as they skip an agent eval run. While the container is
still running its leaves count as production spend, and a notification rule
evaluated on their finish sees their original channel.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from evals.models import EVAL_CHANNEL

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    """What one target run produced, in the shape the runner records."""
    ok: bool = False
    error: Optional[str] = None
    output: str = ""
    trajectory: List[Dict[str, Any]] = field(default_factory=list)
    run_id: Optional[str] = None
    duration_ms: int = 0
    inbound_tokens: int = 0
    outbound_tokens: int = 0
    cost: float = 0.0


# ── Shared helpers ───────────────────────────────────────────────────────────

def _group_children(kind: str, run_id: str) -> List[str]:
    try:
        from managers.runs.groups import get_group
        group = get_group(kind, run_id)
        return list(group.children) if group else []
    except Exception:  # noqa: BLE001 - a trajectory is best effort
        log.debug("eval: no run group for %s %s", kind, run_id, exc_info=True)
        return []


def leaf_run_ids(kind: str, run_id: str) -> List[str]:
    """The agent runs a container went through, in order. A loop's children
    are flow runs, so each is expanded into its own node runs."""
    if kind == "loop":
        out: List[str] = []
        for frid in _group_children("loop", run_id):
            out.extend(_group_children("flow", frid))
        return out
    return _group_children(kind, run_id)


def build_trajectory(kind: str, run_id: str) -> List[Dict[str, Any]]:
    """``[{"run_id", "kind", "summary"}]`` for a container run: a loop's
    iterations (kind ``flow``) each followed by its node runs, else the leaf
    runs (kind ``run``) directly."""
    try:
        from managers.run_manager import get_runs_by_ids
    except Exception:  # noqa: BLE001
        get_runs_by_ids = None  # type: ignore[assignment]

    def leaves(ids: List[str]) -> List[Dict[str, Any]]:
        records = {}
        if get_runs_by_ids and ids:
            try:
                records = get_runs_by_ids(ids) or {}
            except Exception:  # noqa: BLE001
                records = {}
        out = []
        for rid in ids:
            rec = records.get(rid) or {}
            bits = [str(rec.get("agent_id") or ""), str(rec.get("status") or "")]
            out.append({"run_id": rid, "kind": "run",
                        "summary": ": ".join(b for b in bits if b)})
        return out

    if kind == "loop":
        steps: List[Dict[str, Any]] = []
        for i, frid in enumerate(_group_children("loop", run_id), 1):
            steps.append({"run_id": frid, "kind": "flow", "summary": f"iteration {i}"})
            steps.extend(leaves(_group_children("flow", frid)))
        return steps
    return leaves(_group_children(kind, run_id))


def retag_eval(run_ids: List[str]) -> None:
    """Mark leaf runs as evaluation runs, so cost aggregation skips them."""
    if not run_ids:
        return
    try:
        from managers.run_manager import update_run
    except Exception:  # noqa: BLE001
        return
    for rid in run_ids:
        try:
            update_run(rid, {"channel": EVAL_CHANNEL})
        except Exception:  # noqa: BLE001 - a retag never fails the cell
            log.debug("eval: could not retag run %s", rid, exc_info=True)


def token_totals(run_ids: List[str]) -> tuple:
    """(inbound, outbound) tokens summed over leaf run records."""
    if not run_ids:
        return 0, 0
    try:
        from managers.run_manager import get_runs_by_ids
        records = get_runs_by_ids(run_ids) or {}
    except Exception:  # noqa: BLE001
        return 0, 0
    inbound = sum(int(r.get("prompt_tokens") or 0) for r in records.values())
    outbound = sum(int(r.get("completion_tokens") or 0) for r in records.values())
    return inbound, outbound


def _finish(outcome: Outcome, kind: str, run_id: Optional[str], started: float) -> Outcome:
    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    if run_id:
        outcome.run_id = run_id
        leaves = leaf_run_ids(kind, run_id)
        outcome.trajectory = build_trajectory(kind, run_id)
        outcome.inbound_tokens, outcome.outbound_tokens = token_totals(leaves)
        retag_eval(leaves)
    return outcome


# ── Flow ─────────────────────────────────────────────────────────────────────

def run_flow_target(case, cfg, evalset, eval_run_id: str, workspace: Optional[str], *,
                    prompt: str, work_dir: Optional[str] = None, attempt: int = 1) -> Outcome:
    """Drive a flow once in process through the task style driver."""
    import argparse
    import asyncio
    from uuid import uuid4

    from flow import run_store
    from flow import store as flow_store
    from flow.engine import run_flow_engine
    from flow.task_driver import build_task_driver

    started = time.monotonic()
    flow_id = cfg.target_id
    flow = flow_store.get_flow(flow_id)
    if not flow:
        return Outcome(ok=False, error=f"flow not found: {flow_id!r}")

    ws_path = work_dir
    if not ws_path:
        try:
            from workspace import create_workspace_folder
            ws_path = str(create_workspace_folder(workspace) if workspace else create_workspace_folder())
        except Exception:  # noqa: BLE001
            ws_path = os.getcwd()

    overrides: Dict[str, Any] = {}
    if cfg.provider:
        overrides["provider"] = cfg.provider
    if cfg.model:
        overrides["model"] = cfg.model

    flow_run_id = str(uuid4())
    title = f"Eval {evalset.name or evalset.eval_set_id}: {case.case_id}"
    run_store.open_flow_run(flow_run_id, flow_id, workspace=workspace, title=title,
                            pid=os.getpid(), status="running")
    args = argparse.Namespace(flow_id=flow_id, workspace=ws_path, task_id="",
                              run_id=flow_run_id, session_id="", desc=prompt, seed="")
    node_run_ids: Dict[str, str] = {}
    driver = build_task_driver(
        args=args, flow=flow, flow_id=flow_id, run_id=flow_run_id, task_id="",
        session_id="", task_title=title, node_run_ids=node_run_ids,
        port=int(os.environ.get("DASHBOARD_PORT", "8000")), agent_overrides=overrides,
    )

    async def _drive() -> Dict[str, Any]:
        final: Dict[str, Any] = {}
        async for ev in run_flow_engine(flow, flow_id=flow_id, shared_context=prompt, driver=driver):
            if ev.get("type") == "flow_finish":
                final = ev
        return final

    try:
        final = asyncio.run(_drive())
    except Exception as e:  # noqa: BLE001 - recorded on the cell
        run_store.close_flow_run(flow_run_id, status="failed", exit_code=1, error=str(e))
        return _finish(Outcome(ok=False, error=f"{type(e).__name__}: {e}"),
                       "flow", flow_run_id, started)

    stopped, failed = bool(final.get("stopped")), bool(final.get("any_failure"))
    run_store.close_flow_run(
        flow_run_id, status="stopped" if stopped else ("failed" if failed else "completed"),
        exit_code=1 if (stopped or failed) else 0)
    outputs = [str(v) for v in (final.get("node_outputs") or {}).values() if v]
    output = outputs[-1] if outputs else str(final.get("combined_output") or "")
    ok = bool(final) and not stopped and not failed
    error = None if ok else ("flow stopped" if stopped else
                             f"flow failed at {', '.join(final.get('failed_nodes') or []) or 'a node'}")
    outcome = _finish(Outcome(ok=ok, error=error, output=output if ok else ""),
                      "flow", flow_run_id, started)
    try:
        from managers.runs.groups import runs_cost
        outcome.cost = round(runs_cost(leaf_run_ids("flow", flow_run_id)), 6)
    except Exception:  # noqa: BLE001
        outcome.cost = 0.0
    return outcome


# ── Team ─────────────────────────────────────────────────────────────────────

def run_team_target(case, cfg, evalset, eval_run_id: str, workspace: Optional[str], *,
                    prompt: str, work_dir: Optional[str] = None, attempt: int = 1) -> Outcome:
    """Run a team in process on the case input as its goal.

    A conversation id is passed so the run lives in its own chat session
    rather than minting a task on the board for every cell.
    """
    from teams.runner import run_team

    started = time.monotonic()
    run = run_team(cfg.target_id, prompt, workspace=workspace,
                   conversation_id=f"eval:{eval_run_id}:{case.case_id}:{attempt}")
    ok = run.status == "completed"
    outcome = Outcome(ok=ok, error=None if ok else (run.error or f"team run {run.status}"),
                      output=str(run.result or "") if ok else "",
                      cost=float(run.total_cost or 0.0))
    return _finish(outcome, "team", run.team_run_id, started)


# ── Loop ─────────────────────────────────────────────────────────────────────

def run_loop_target(case, cfg, evalset, eval_run_id: str, workspace: Optional[str], *,
                    prompt: str, work_dir: Optional[str] = None, attempt: int = 1) -> Outcome:
    """Run a loop in process to convergence (or its ceiling) on the case input."""
    from loops.runner import run_loop

    started = time.monotonic()
    run = run_loop(cfg.target_id, goal=prompt, workspace=workspace, own_process=True)
    ok = run.status == "completed"
    outcome = Outcome(ok=ok, error=None if ok else (run.error or f"loop run {run.status}"),
                      output=str(run.result or "") if ok else "",
                      cost=float(run.total_cost or 0.0))
    return _finish(outcome, "loop", run.loop_run_id, started)


# ── Scenario ─────────────────────────────────────────────────────────────────

def render_scenario(scores: Dict[str, Any], final_state: Dict[str, Any],
                    limit: int = 8000) -> str:
    """Scores and final state as compact text, for the graders."""
    parts = ["Scores:"]
    for key, value in sorted((scores or {}).items()):
        parts.append(f"  {key}: {json.dumps(value, ensure_ascii=False, default=str)}")
    parts.append("Final state:")
    parts.append(json.dumps(final_state or {}, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), default=str))
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def run_scenario_target(case, cfg, evalset, eval_run_id: str, workspace: Optional[str], *,
                        prompt: str, work_dir: Optional[str] = None, attempt: int = 1) -> Outcome:
    """Run a scenario in process with the case input sent as an external
    trigger to one role before the first tick."""
    from playground import control
    from playground import store as sim_store
    from playground.models import Scenario, SimRun
    from playground.runner import run_simulation, trigger_agent

    started = time.monotonic()
    scenario = sim_store.get_scenario(cfg.target_id)
    if not scenario:
        return Outcome(ok=False, error=f"scenario not found: {cfg.target_id!r}")
    from playground.runner import roles_from_team

    config = scenario.to_dict()
    settings = dict(cfg.settings or {})
    if settings.get("max_ticks"):
        config["max_ticks"] = int(settings["max_ticks"])
    # A scenario cast by a team (Scenario.team_id, no roles of its own) gets
    # its roster here, before the recipient of the case input is chosen and
    # before the model override is applied: the runner would fill it in
    # anyway, but too late for either of those, and the case would then be
    # delivered to nobody.
    if not config.get("roles") and config.get("team_id"):
        config["roles"] = [r.to_dict() for r in roles_from_team(Scenario.from_dict(config))]
    if cfg.model or cfg.provider:
        config["default_provider"] = cfg.provider or config.get("default_provider")
        config["default_model"] = cfg.model or config.get("default_model")
        if cfg.model:
            for role in config.get("roles") or []:
                role["provider"] = cfg.provider or role.get("provider")
                role["model"] = cfg.model
    frozen = Scenario.from_dict(config)
    names = [r.display_name() for r in frozen.roles]
    wanted = str(settings.get("trigger_agent") or "").strip()
    recipient = wanted if wanted in names else (names[0] if names else "")

    ws = workspace or frozen.workspace
    run = SimRun(scenario_id=cfg.target_id, workspace=ws, environment=frozen.environment,
                 activation=frozen.activation, config=frozen.to_dict(),
                 pid=os.getpid(), host=socket.gethostname())
    heartbeat: List[Any] = []

    def _on_start(r: Any) -> None:
        from runtime.entity_heartbeat import EntityHeartbeat
        hb = EntityHeartbeat(r.sim_run_id, on_stop=lambda: control.request_stop(r.sim_run_id))
        hb.start()
        heartbeat.append(hb)
        if recipient and prompt.strip():
            trigger_agent(r.sim_run_id, recipient, prompt, sender="(eval)")

    try:
        run = run_simulation(cfg.target_id, workspace=ws, run=run, on_start=_on_start)
    finally:
        for hb in heartbeat:
            hb.stop()
    ok = run.status == "completed"
    outcome = Outcome(ok=ok, error=None if ok else (run.error or f"scenario run {run.status}"),
                      output=render_scenario(run.scores, run.final_state) if ok else "",
                      cost=float(run.total_cost or 0.0))
    return _finish(outcome, "scenario", run.sim_run_id, started)


__all__ = [
    "Outcome", "run_flow_target", "run_team_target", "run_loop_target",
    "run_scenario_target", "render_scenario", "build_trajectory", "leaf_run_ids",
    "retag_eval", "token_totals",
]
