"""Record kinds: evals (see common/record_kinds/__init__.py).

Eval sets, eval runs and prompt suggestions (routes/evals.py), and the
playground's worlds, scenarios and simulation runs (routes/playground.py).
Each resolver reads the workspace the way the route's own check does, so the
middleware and the route agree: a record that names none is the default
workspace's.
"""
from __future__ import annotations

from typing import Optional

from common.record_access import Record
from common.record_kinds import in_workspace

ROUTES: dict = {
    ("/api/evals/", "eval_set_id"): "eval_set",
    # The first run of a comparison; the route checks the second itself.
    ("/api/evals/runs/", "run_a_id"): "eval_run",
    # An agent run, flow, team, loop or scenario run, whichever store holds it.
    ("/api/evals/for-run/", "run_id"): "eval_for_run",
    ("/api/eval-runs/", "eval_run_id"): "eval_run",
    ("/api/prompt-suggestions/", "suggestion_id"): "prompt_suggestion",
    ("/api/playground/runs/", "sim_run_id"): "sim_run",
    ("/api/playground/scenarios/", "scenario_id"): "scenario",
    ("/api/playground/worlds/", "world_id"): "world",
}

#: Calls that only read, though they are POSTs: the cost projections.
READ_ROUTES = {
    "/api/evals/{eval_set_id}/estimate",
    "/api/playground/scenarios/{scenario_id}/estimate",
}

EXEMPT: dict = {}


def _eval_set(eval_set_id: str) -> Optional[Record]:
    from evals import store
    try:
        evalset = store.get_eval_set(eval_set_id)
    except Exception:  # noqa: BLE001 - an unreadable set is the route's 4xx to give
        return None
    return in_workspace(evalset.workspace) if evalset else None


def _eval_run(eval_run_id: str) -> Optional[Record]:
    """A run's own workspace, else its set's (an older run recorded none), as
    routes/evals.py ``_run_workspace`` reads it."""
    from evals import store
    try:
        run = store.get_eval_run(eval_run_id)
        if run is None:
            return None
        workspace = run.workspace
        if not workspace:
            evalset = store.get_eval_set(run.eval_set_id)
            workspace = evalset.workspace if evalset else None
    except Exception:  # noqa: BLE001 - an unreadable run is the route's 4xx to give
        return None
    return in_workspace(workspace)


def _prompt_suggestion(suggestion_id: str) -> Optional[Record]:
    from evals import store
    try:
        suggestion = store.get_prompt_suggestion(suggestion_id)
    except Exception:  # noqa: BLE001 - an unreadable suggestion is the route's 4xx to give
        return None
    return in_workspace(suggestion.workspace) if suggestion else None


def _eval_for_run(run_id: str) -> Optional[Record]:
    """The run a case would be made from: an agent run (the runs table) or an
    entity run (flow, team, loop, scenario), as ``evals.runner._run_target_info``
    finds it. Named apart from the runs module's kinds."""
    try:
        from managers import run_manager
        run = run_manager.get_run_by_id(run_id)
        if run is not None:
            return in_workspace(run.get("workspace"))
        from common import entity_runs
        rec = entity_runs.get(run_id)
    except Exception:  # noqa: BLE001 - an unreadable run is the route's 4xx to give
        return None
    return in_workspace(rec.get("workspace")) if rec else None


def _sim_run(sim_run_id: str) -> Optional[Record]:
    """A simulation run's own workspace, else its scenario's (routes/playground.py
    ``_sim_run_workspace``)."""
    from playground import store
    try:
        run = store.get_sim_run(sim_run_id)
        if run is None:
            return None
        workspace = run.workspace
        if not workspace:
            scenario = store.get_scenario(run.scenario_id)
            workspace = scenario.workspace if scenario else None
    except Exception:  # noqa: BLE001 - an unreadable run is the route's 4xx to give
        return None
    return in_workspace(workspace)


def _scenario(scenario_id: str) -> Optional[Record]:
    from playground import store
    try:
        scenario = store.get_scenario(scenario_id)
    except Exception:  # noqa: BLE001 - an unreadable scenario is the route's 4xx to give
        return None
    return in_workspace(scenario.workspace) if scenario else None


def _world(world_id: str) -> Optional[Record]:
    from playground import store
    try:
        world = store.get_world(world_id)
    except Exception:  # noqa: BLE001 - an unreadable world is the route's 4xx to give
        return None
    return in_workspace(world.workspace) if world else None


RESOLVERS: dict = {
    "eval_set": _eval_set,
    "eval_run": _eval_run,
    "eval_for_run": _eval_for_run,
    "prompt_suggestion": _prompt_suggestion,
    "sim_run": _sim_run,
    "scenario": _scenario,
    "world": _world,
}
