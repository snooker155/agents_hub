"""Container entity: run a loop as a nested run of the flow run.

The node launches the loop through ``loops.launcher.start_loop_run`` with the
flow run as ``parent_run_id`` and the named state keys as its seed, waits for
it to finish and writes the accepted output (the loop run's result) into its
output key. See flow/entities/containers/_nested.py for the shared mechanics.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.entities.containers import _nested
from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="run_loop",
    name="Run Loop",
    category="container",
    group="Nested runs",
    entrypoint="flow.entities.containers.run_loop:run",
    description=(
        "Run a loop until it converges, as a nested run of this flow, and wait "
        "for the accepted output. The output lands in the output key."
    ),
    inputs=[],
    outputs=["result"],
    config_schema={
        "loop_id": {
            "type": "str", "default": "", "source": "loops",
            "description": "The loop to run.",
        },
        "goal": {
            "type": "str", "default": "",
            "description": "The goal for the loop. {state_key} is filled from flow state.",
        },
        "seed_keys": {
            "type": "list", "default": [],
            "description": "State keys copied into the loop's seed state.",
        },
    },
    icon="repeat",
)


def _result(rec: Dict[str, Any]) -> str:
    return str(rec.get("result") or "")


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    cfg = config or {}
    loop_id = _nested.config_value(cfg, "loop_id")
    goal = _nested.fill(str(cfg.get("goal") or "").strip(), state)
    seed = _nested.seed_from(state, _nested.as_keys(cfg.get("seed_keys")))
    workspace = _nested.workspace_name(ctx)
    parent_id = _nested.parent_run_id(ctx)

    def _launch() -> str:
        from loops.launcher import start_loop_run
        run_rec = start_loop_run(loop_id, goal, workspace=workspace, seed=seed,
                                 parent_run_id=parent_id)
        return str(run_rec.loop_run_id)

    def _resume(child_id: str) -> Any:
        from loops.launcher import resume_loop_run
        return resume_loop_run(child_id)

    def _stop(child_id: str) -> Any:
        from loops.launcher import stop_loop_run
        return stop_loop_run(child_id)

    from loops.launcher import LoopResumeError

    child = _nested.ChildKind(
        kind="loop", launch=_launch, resume=_resume, stop=_stop, result=_result,
        resume_errors=(LoopResumeError,),
    )
    return _nested.run_nested(child, ctx, cfg)
