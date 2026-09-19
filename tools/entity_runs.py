"""
Launch, follow and stop the long-running entities: scenarios, teams, loops.

These are kept apart from the builder tools in ``tools.scenario_management`` and
its siblings for one reason: building is free and reversible, running is neither.
A scenario run is N agents x T ticks of model calls, a team run is members x
rounds, a loop run is a whole flow repeated — minutes of spend that nothing
automatic will take back. So they form their own grant group (``entity_runs``),
and an agent can be trusted to *design* a simulation without being trusted to
*start* one.

Three rules hold across all three kinds:

* **Launching is approval-gated.** Like ``run_flow_tool``, each tool refuses
  unless ``user_approved`` is True, and the refusal carries the cost estimate —
  so what the user is being asked to approve comes with its number attached
  rather than after the fact.
* **Launching returns immediately.** The run starts on a background thread and
  the tool answers with its id as soon as the store has a row. Holding the tool
  call open for the whole run would burn the agent's own turn on waiting.
* **Following is a separate call.** ``get_*_run_tool`` reports status, progress,
  spend and the result when there is one; ``stop_*_run_tool`` ends a run that is
  still going. Polling in a tight loop is not the intent — the run outlives the
  conversation turn, and the pages show it live.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from common.entity_sink import record_entity
from common.workspace_context import (
    normalize_workspace_name,
    resolve_active_workspace,
)


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request",
              extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


#: Statuses that mean "this run is still spending money".
LIVE_STATUSES = ("starting", "running", "stopping")


def _approval_required(kind: str, entity_id: str, list_tool: str, run_tool: str,
                       estimate: Optional[Dict[str, Any]]) -> str:
    """The refusal a caller gets when it has not been approved yet.

    It carries the estimate on purpose: "shall I run this?" and "this will cost
    about $1.40" are the same question, and splitting them into two turns is how
    a user ends up approving a number they never saw.
    """
    extra: Dict[str, Any] = {f"{kind}_id": entity_id}
    if estimate:
        extra["estimate"] = estimate
    return _json_err(
        f"This {kind} run is not approved. Show the user what it will cost, ask "
        f"them to confirm, and only then call {run_tool} again with "
        f"user_approved=True. If they have not chosen a {kind} yet, call "
        f"{list_tool} first.",
        code="approval_required",
        extra=extra,
    )


def _workspace_conflict(kind: str, entity_id: str, entity_ws: Optional[str],
                        active_ws: Optional[str]) -> Optional[str]:
    """Refuse to run something belonging to a different workspace."""
    if active_ws and entity_ws and entity_ws != active_ws:
        return _json_err(
            f"{kind.capitalize()} '{entity_id}' belongs to workspace "
            f"'{entity_ws}', not the active workspace '{active_ws}'.",
            code="forbidden",
        )
    return None


def _start_background_run(
    worker: Callable[[Callable[[], None]], None],
    poll: Callable[[], Optional[Dict[str, Any]]],
    *,
    thread_name: str,
    timeout: float = 10.0,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Start a long run on a daemon thread, return its record once it exists.

    The runners mint their own ids, so the record is read back from the store
    rather than guessed here — ``poll`` is what knows where to look. Everything
    before the row is written is setup (building the world, validating the
    roster), so this normally returns in milliseconds; the run itself keeps
    going regardless of when we return, including past this timeout.

    Returns ``(record, error)`` — the record when the run is under way, the
    error when it failed during setup, and neither when it is still starting.
    """
    ready = threading.Event()
    failure: Dict[str, str] = {}

    def _run() -> None:
        try:
            worker(ready.set)
        except Exception as e:  # noqa: BLE001 — reported to the caller, not raised
            failure["error"] = f"{type(e).__name__}: {e}"
        finally:
            ready.set()

    threading.Thread(target=_run, name=thread_name, daemon=True).start()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = poll()
        if record is not None:
            return record, None
        if failure:
            return None, failure["error"]
        ready.wait(timeout=0.05)
    return None, failure.get("error")


def _started(kind: str, name: str, record: Dict[str, Any], run_key: str,
             follow_tool: str) -> Dict[str, Any]:
    """The common half of a "it is running now" answer."""
    return {
        "message": (
            f"{kind.capitalize()} '{name}' started. It runs in the background — "
            f"tell the user it is under way and give them the id; do not poll for "
            f"completion. Use {follow_tool} when they ask how it went."
        ),
        run_key: record.get(run_key),
        "status": record.get("status"),
    }


# ── Scenarios ─────────────────────────────────────────────────────────────────

class RunScenarioInput(BaseModel):
    scenario_id: str = Field(..., min_length=1,
                             description="ID of the scenario to run (from list_scenarios_tool)")
    user_approved: bool = Field(
        False,
        description=(
            "Must be True. Set only after you have shown the user the cost "
            "estimate and they have explicitly approved starting the simulation. "
            "Never set True on the user's behalf."
        ),
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to run in; defaults to the scenario's own"
    )


@tool("run_scenario_tool", args_schema=RunScenarioInput)
def run_scenario_tool(scenario_id: str, user_approved: bool = False,
                      workspace: Optional[str] = None) -> str:
    """Start a playground simulation, AFTER the user has approved the spend.

    A simulation is every role acting for up to max_ticks ticks — minutes of
    model calls that no one can take back. So this refuses unless
    `user_approved` is True, and the refusal hands you the cost estimate to show
    the user first. Returns the sim_run_id immediately; the run continues in the
    background. Use get_scenario_run_tool to report on it later.
    """
    try:
        from playground import store
        from playground.runner import estimate_cost, run_simulation

        scenario = store.get_scenario(scenario_id)
        if not scenario:
            return _json_err("Scenario not found", code="not_found",
                             extra={"scenario_id": scenario_id})
        if not scenario.roles:
            return _json_err(
                "This scenario has no roles — a simulation needs participants. "
                "Add a cast with modify_scenario_tool first.",
                code="invalid_scenario", extra={"scenario_id": scenario_id},
            )

        try:
            estimate = estimate_cost(scenario)
        except Exception:  # noqa: BLE001 — an estimate is help, not a precondition
            estimate = None

        if not user_approved:
            return _approval_required("scenario", scenario_id,
                                      "list_scenarios_tool", "run_scenario_tool", estimate)

        active_ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        conflict = _workspace_conflict("scenario", scenario_id, scenario.workspace, active_ws)
        if conflict:
            return conflict

        run_ws = scenario.workspace or active_ws
        started: Dict[str, Any] = {}

        def _worker(mark_ready: Callable[[], None]) -> None:
            def _on_start(run) -> None:
                started["run"] = run.to_dict()
                mark_ready()

            run_simulation(scenario_id, workspace=run_ws, on_start=_on_start)

        record, error = _start_background_run(
            _worker, lambda: started.get("run"), thread_name=f"sim-{scenario_id}")

        if record is None:
            if error:
                return _json_err(f"Failed to start the simulation: {error}",
                                 code="start_failed", extra={"scenario_id": scenario_id})
            return _json_ok({
                "message": (f"Scenario '{scenario.name}' is starting. Check back with "
                            "get_scenario_run_tool."),
                "scenario_id": scenario_id, "status": "starting",
            })

        record_entity("scenario", scenario_id, "viewed", scenario.name)
        payload = _started("scenario", scenario.name, record, "sim_run_id", "get_scenario_run_tool")
        payload["scenario_id"] = scenario_id
        if estimate:
            payload["estimate"] = estimate
        return _json_ok(payload)
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to run scenario: {e}")


class GetScenarioRunInput(BaseModel):
    sim_run_id: Optional[str] = Field(
        None, description="The run to report on. Omit to take the scenario's most recent run."
    )
    scenario_id: Optional[str] = Field(
        None, description="Report on this scenario's latest run, when no sim_run_id is given"
    )


@tool("get_scenario_run_tool", args_schema=GetScenarioRunInput)
def get_scenario_run_tool(sim_run_id: Optional[str] = None,
                          scenario_id: Optional[str] = None) -> str:
    """How a simulation is going, or how it went: status, ticks, spend, scores.

    Pass the sim_run_id, or a scenario_id to get its most recent run. Returns
    the run's own summary — not the tick log, which is the artifact of record
    and belongs on the scenario page rather than in a chat reply.
    """
    try:
        from playground import store

        if sim_run_id:
            run = store.get_sim_run(sim_run_id)
        elif scenario_id:
            runs = store.list_sim_runs(scenario_id, limit=1)
            run = runs[0] if runs else None
        else:
            return _json_err("Provide either sim_run_id or scenario_id", code="invalid")

        if not run:
            return _json_err("No simulation run found", code="not_found",
                             extra={"sim_run_id": sim_run_id, "scenario_id": scenario_id})

        d = run.to_dict()
        return _json_ok({
            "sim_run_id": d["sim_run_id"], "scenario_id": d["scenario_id"],
            "status": d["status"], "live": d["status"] in LIVE_STATUSES,
            "ticks_done": d["ticks_done"], "total_cost": d["total_cost"],
            # The status says it ended; the stop reason says whether it ran out
            # of ticks, went quiet, or hit a ceiling — which is the difference
            # between "it finished" and "it was cut off".
            "stop_reason": d["stop_reason"], "scores": d["scores"],
            "error": d["error"],
            "started_at": d["started_at"], "finished_at": d["finished_at"],
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to get the simulation run: {e}")


class StopScenarioRunInput(BaseModel):
    sim_run_id: str = Field(..., min_length=1, description="The simulation run to stop")


@tool("stop_scenario_run_tool", args_schema=StopScenarioRunInput)
def stop_scenario_run_tool(sim_run_id: str) -> str:
    """Stop a running simulation now.

    Not a between-ticks flag: the decisions in flight are interrupted, so the
    spend ends with this call rather than at the end of the tick already under
    way. The world is kept as of the last tick that completed.
    """
    try:
        from playground.runner import stop_simulation

        if not stop_simulation(sim_run_id):
            return _json_err("That simulation is not running", code="not_running",
                             extra={"sim_run_id": sim_run_id})
        return _json_ok({"message": "Simulation stopped.", "sim_run_id": sim_run_id})
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to stop the simulation: {e}")


# ── Teams ─────────────────────────────────────────────────────────────────────

class RunTeamInput(BaseModel):
    team_id: str = Field(..., min_length=1,
                         description="ID of the team to run (from list_teams_tool)")
    goal: str = Field(
        "",
        description=(
            "The request the team works on — what you want back at the end. "
            "Falls back to the team's description when empty."
        ),
    )
    user_approved: bool = Field(
        False,
        description=(
            "Must be True. Set only after you have shown the user the cost "
            "estimate and they have explicitly approved starting the run."
        ),
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to run in; defaults to the team's own"
    )


@tool("run_team_tool", args_schema=RunTeamInput)
def run_team_tool(team_id: str, goal: str = "", user_approved: bool = False,
                  workspace: Optional[str] = None) -> str:
    """Start a team run against one goal, AFTER the user has approved the spend.

    Members x rounds is the cost floor, so this refuses unless `user_approved`
    is True and the refusal hands you the estimate to show the user first.
    Returns the team_run_id immediately; the run continues in the background.
    Use get_team_run_tool to report on it later.
    """
    try:
        from teams import store
        from teams.runner import estimate_cost, run_team

        team = store.get_team(team_id)
        if not team:
            return _json_err("Team not found", code="not_found", extra={"team_id": team_id})
        if not team.members:
            return _json_err(
                "This team has no members. Add some with modify_team_tool first.",
                code="invalid_team", extra={"team_id": team_id},
            )

        request = (goal or "").strip() or (team.description or "").strip()
        if not request:
            return _json_err(
                "A team run needs a goal — the request the team is to work on. "
                "Ask the user what they want it to produce.",
                code="invalid", extra={"team_id": team_id},
            )

        try:
            estimate = estimate_cost(team)
        except Exception:  # noqa: BLE001
            estimate = None

        if not user_approved:
            return _approval_required("team", team_id, "list_teams_tool",
                                      "run_team_tool", estimate)

        active_ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        conflict = _workspace_conflict("team", team_id, team.workspace, active_ws)
        if conflict:
            return conflict

        run_ws = team.workspace or active_ws

        def _worker(mark_ready: Callable[[], None]) -> None:
            run_team(team_id, request, workspace=run_ws,
                     on_message=lambda _m: mark_ready())

        def _poll() -> Optional[Dict[str, Any]]:
            runs = store.list_runs(team_id, limit=1)
            return runs[0].to_dict() if runs else None

        record, error = _start_background_run(
            _worker, _poll, thread_name=f"team-{team_id}")

        if record is None:
            if error:
                return _json_err(f"Failed to start the team run: {error}",
                                 code="start_failed", extra={"team_id": team_id})
            return _json_ok({
                "message": (f"Team '{team.name}' is starting. Check back with "
                            "get_team_run_tool."),
                "team_id": team_id, "status": "starting",
            })

        record_entity("team", team_id, "viewed", team.name)
        payload = _started("team", team.name, record, "team_run_id", "get_team_run_tool")
        payload["team_id"] = team_id
        payload["goal"] = request
        if estimate:
            payload["estimate"] = estimate
        return _json_ok(payload)
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to run team: {e}")


class GetTeamRunInput(BaseModel):
    team_run_id: Optional[str] = Field(
        None, description="The run to report on. Omit to take the team's most recent run."
    )
    team_id: Optional[str] = Field(
        None, description="Report on this team's latest run, when no team_run_id is given"
    )


@tool("get_team_run_tool", args_schema=GetTeamRunInput)
def get_team_run_tool(team_run_id: Optional[str] = None,
                      team_id: Optional[str] = None) -> str:
    """How a team run is going, or how it went: status, rounds, spend, and the result.

    Pass the team_run_id, or a team_id to get its most recent run. The `result`
    is the team's synthesized answer when the run finished with one — that is
    what the user asked the team for, so report it rather than the transcript.
    """
    try:
        from teams import store

        if team_run_id:
            run = store.get_run(team_run_id)
        elif team_id:
            runs = store.list_runs(team_id, limit=1)
            run = runs[0] if runs else None
        else:
            return _json_err("Provide either team_run_id or team_id", code="invalid")

        if not run:
            return _json_err("No team run found", code="not_found",
                             extra={"team_run_id": team_run_id, "team_id": team_id})

        d = run.to_dict()
        return _json_ok({
            "team_run_id": d["team_run_id"], "team_id": d["team_id"],
            "status": d["status"], "live": d["status"] in LIVE_STATUSES,
            "mode": d["mode"], "goal": d["goal"],
            "rounds_done": d["rounds_done"], "total_cost": d["total_cost"],
            "result": d["result"], "stop_reason": d["stop_reason"],
            "error": d["error"],
            "started_at": d["started_at"], "finished_at": d["finished_at"],
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to get the team run: {e}")


class StopTeamRunInput(BaseModel):
    team_run_id: str = Field(..., min_length=1, description="The team run to stop")


@tool("stop_team_run_tool", args_schema=StopTeamRunInput)
def stop_team_run_tool(team_run_id: str) -> str:
    """Stop a running team now.

    The member turns in flight are interrupted, so the spend ends with this call
    rather than at the end of the round already under way. Everything the team
    said up to that point is kept.
    """
    try:
        from teams.runner import stop_run

        if not stop_run(team_run_id):
            return _json_err("That team run is not running", code="not_running",
                             extra={"team_run_id": team_run_id})
        return _json_ok({"message": "Team run stopped.", "team_run_id": team_run_id})
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to stop the team run: {e}")


# ── Loops ─────────────────────────────────────────────────────────────────────

class RunLoopInput(BaseModel):
    loop_id: str = Field(..., min_length=1,
                         description="ID of the loop to run (from list_loops_tool)")
    goal: str = Field(
        "",
        description=(
            "What this run should produce — the request the wrapped flow works "
            "on. Falls back to the loop's description when empty."
        ),
    )
    user_approved: bool = Field(
        False,
        description=(
            "Must be True. Set only after you have shown the user the cost "
            "estimate and they have explicitly approved starting the run."
        ),
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to run in; defaults to the loop's own"
    )


@tool("run_loop_tool", args_schema=RunLoopInput)
def run_loop_tool(loop_id: str, goal: str = "", user_approved: bool = False,
                  workspace: Optional[str] = None) -> str:
    """Start a loop run, AFTER the user has approved the spend.

    A loop run is the whole flow repeated up to max_iterations times, plus an
    evaluation after each pass — the most expensive thing in this system. So it
    refuses unless `user_approved` is True, and the refusal hands you the
    ceiling to show the user first. Returns the loop_run_id immediately; the run
    continues in the background. Use get_loop_run_tool to report on it later.
    """
    try:
        from loops import store
        from loops.runner import estimate_cost, run_loop

        loop = store.get_loop(loop_id)
        if not loop:
            return _json_err("Loop not found", code="not_found", extra={"loop_id": loop_id})

        # A loop whose flow has been deleted or broken cannot run at all, and
        # the runner would only discover that after opening a run record.
        from flow import store as flow_store
        try:
            flow = flow_store.get_flow(loop.flow_id)
        except Exception:  # noqa: BLE001 — a broken flow reads the same as a missing one here
            flow = None
        if flow is None:
            return _json_err(
                f"This loop wraps a flow that is missing or unreadable: "
                f"'{loop.flow_id}'. Fix the flow, or point the loop at another "
                "one with modify_loop_tool.",
                code="invalid_loop", extra={"loop_id": loop_id, "flow_id": loop.flow_id},
            )

        request = (goal or "").strip() or (loop.description or "").strip()

        try:
            estimate = estimate_cost(loop)
        except Exception:  # noqa: BLE001
            estimate = None

        if not user_approved:
            return _approval_required("loop", loop_id, "list_loops_tool",
                                      "run_loop_tool", estimate)

        active_ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        conflict = _workspace_conflict("loop", loop_id, loop.workspace, active_ws)
        if conflict:
            return conflict

        run_ws = loop.workspace or active_ws

        def _worker(mark_ready: Callable[[], None]) -> None:
            run_loop(loop_id, goal=request, workspace=run_ws,
                     on_iteration=lambda _i: mark_ready())

        def _poll() -> Optional[Dict[str, Any]]:
            runs = store.list_runs(loop_id, limit=1)
            return runs[0].to_dict() if runs else None

        record, error = _start_background_run(
            _worker, _poll, thread_name=f"loop-{loop_id}")

        if record is None:
            if error:
                return _json_err(f"Failed to start the loop run: {error}",
                                 code="start_failed", extra={"loop_id": loop_id})
            return _json_ok({
                "message": (f"Loop '{loop.name}' is starting. Check back with "
                            "get_loop_run_tool."),
                "loop_id": loop_id, "status": "starting",
            })

        record_entity("loop", loop_id, "viewed", loop.name)
        payload = _started("loop", loop.name, record, "loop_run_id", "get_loop_run_tool")
        payload["loop_id"] = loop_id
        if request:
            payload["goal"] = request
        if estimate:
            payload["estimate"] = estimate
        return _json_ok(payload)
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to run loop: {e}")


class GetLoopRunInput(BaseModel):
    loop_run_id: Optional[str] = Field(
        None, description="The run to report on. Omit to take the loop's most recent run."
    )
    loop_id: Optional[str] = Field(
        None, description="Report on this loop's latest run, when no loop_run_id is given"
    )


@tool("get_loop_run_tool", args_schema=GetLoopRunInput)
def get_loop_run_tool(loop_run_id: Optional[str] = None,
                      loop_id: Optional[str] = None) -> str:
    """How a loop run is going, or how it went: status, scores, spend, the result.

    Pass the loop_run_id, or a loop_id to get its most recent run. The score
    trajectory is included because it is what a loop is *for*: 40 → 65 → 78 says
    something the final answer alone does not.
    """
    try:
        from loops import store

        if loop_run_id:
            run = store.get_run(loop_run_id)
        elif loop_id:
            runs = store.list_runs(loop_id, limit=1)
            run = runs[0] if runs else None
        else:
            return _json_err("Provide either loop_run_id or loop_id", code="invalid")

        if not run:
            return _json_err("No loop run found", code="not_found",
                             extra={"loop_run_id": loop_run_id, "loop_id": loop_id})

        d = run.to_dict()
        trajectory = [
            {"iteration": it.get("iteration"), "score": it.get("score"),
             "verdict": it.get("verdict"), "reason": it.get("reason")}
            for it in store.list_iterations(d["loop_run_id"])
        ]
        return _json_ok({
            "loop_run_id": d["loop_run_id"], "loop_id": d["loop_id"],
            "status": d["status"], "live": d["status"] in LIVE_STATUSES,
            "goal": d["goal"], "iterations_done": d["iterations_done"],
            "best_score": d["best_score"], "final_score": d["final_score"],
            "stop_reason": d["stop_reason"], "total_cost": d["total_cost"],
            "result": d["result"], "error": d["error"],
            "trajectory": trajectory,
            "started_at": d["started_at"], "finished_at": d["finished_at"],
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to get the loop run: {e}")


class StopLoopRunInput(BaseModel):
    loop_run_id: str = Field(..., min_length=1, description="The loop run to stop")


@tool("stop_loop_run_tool", args_schema=StopLoopRunInput)
def stop_loop_run_tool(loop_run_id: str) -> str:
    """Ask a running loop to stop.

    Checked between nodes and between iterations, so the flow is never left
    half-applied — the pass in flight finishes its current step and the loop
    closes with whatever it had produced by then.
    """
    try:
        from loops import store

        if not store.request_stop(loop_run_id):
            return _json_err("That loop run is not running", code="not_running",
                             extra={"loop_run_id": loop_run_id})
        return _json_ok({"message": "Loop run stopping.", "loop_run_id": loop_run_id})
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to stop the loop run: {e}")


ENTITY_RUN_TOOLS = [
    run_scenario_tool,
    get_scenario_run_tool,
    stop_scenario_run_tool,
    run_team_tool,
    get_team_run_tool,
    stop_team_run_tool,
    run_loop_tool,
    get_loop_run_tool,
    stop_loop_run_tool,
]

__all__ = [
    "run_scenario_tool", "get_scenario_run_tool", "stop_scenario_run_tool",
    "run_team_tool", "get_team_run_tool", "stop_team_run_tool",
    "run_loop_tool", "get_loop_run_tool", "stop_loop_run_tool",
    "ENTITY_RUN_TOOLS", "LIVE_STATUSES",
]
