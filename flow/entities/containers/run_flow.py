"""Container entity: run another flow as a nested run of this flow run.

The node creates a task of its own for the child (a flow closes its task when
it finishes, which must not be the parent's), launches the flow through
``flow.launcher.start_flow_run`` with this run as ``parent_run_id`` and a seed
built from ``params`` and the named state keys, waits for it to finish and
writes the last node's output (or, when that is empty, the child's final state)
into its output key. See flow/entities/containers/_nested.py for the rest.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.entities.containers import _nested
from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="run_flow",
    name="Run Flow",
    category="container",
    group="Nested runs",
    entrypoint="flow.entities.containers.run_flow:run",
    description=(
        "Run another flow as a nested run of this one and wait for it. Its "
        "last output lands in the output key."
    ),
    inputs=[],
    outputs=["result"],
    config_schema={
        "flow_id": {
            "type": "str", "default": "", "source": "flows",
            "description": "The flow to run. It may not lead back to this flow.",
        },
        "goal": {
            "type": "str", "default": "",
            "description": "Extra context for the child flow. {state_key} is filled from flow state.",
        },
        "params": {
            "type": "dict", "default": {},
            "description": "Fixed values for the child's initial state.",
        },
        "seed_keys": {
            "type": "list", "default": [],
            "description": "State keys copied into the child's initial state.",
        },
    },
    icon="workflow",
)


def _result(rec: Dict[str, Any]) -> Any:
    """The last output of a node that finished, else the final state."""
    checkpoint = rec.get("checkpoint") or {}
    for done in reversed(checkpoint.get("done") or []):
        if (done or {}).get("ok", True) and str(done.get("output") or "").strip():
            return done.get("output")
    return dict(checkpoint.get("state") or {})


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    cfg = config or {}
    flow_id = _nested.config_value(cfg, "flow_id")
    if flow_id == str(getattr(ctx, "flow_id", "") or ""):
        raise _nested.NestedRunError(f"run_flow node may not run its own flow '{flow_id}'")
    goal = _nested.fill(str(cfg.get("goal") or "").strip(), state)
    seed = _nested.seed_from(state, _nested.as_keys(cfg.get("seed_keys")), cfg.get("params"))
    workspace = _nested.workspace_name(ctx)
    parent_id = _nested.parent_run_id(ctx)

    def _launch() -> str:
        from flow import store as flow_store
        from flow.launcher import start_flow_run
        from tasks import service as _ts

        flow = flow_store.get_flow(flow_id)
        if not flow:
            raise _nested.NestedRunError(f"Flow not found: {flow_id}")
        name = flow.get("name") or flow_id
        task = _ts.create_task(
            title=f"Flow: {name}",
            description=goal or flow.get("description", "") or "",
            workspace=workspace, status=_ts.TaskStatus.in_progress,
        )
        params: Dict[str, Any] = {"flow_id": flow_id, "description": goal}
        if workspace:
            params["workspace"] = workspace
        if seed:
            params["seed"] = seed
        run_id, _session = start_flow_run(str(task.id), flow_id, params, parent_run_id=parent_id)
        try:
            _ts.assign_executor(task.id, {"kind": "flow", "id": flow_id}, params, run_id=run_id)
        except Exception:  # noqa: BLE001 - the run is already going; the task page just shows no executor
            pass
        return run_id

    def _resume(child_id: str) -> Any:
        from flow.launcher import resume_flow_run
        return resume_flow_run(child_id)

    def _stop(child_id: str) -> Any:
        # The run group stop reaches the child's own nested runs and node runs
        # too; the plain status request is the fallback.
        try:
            from managers.runs.groups import stop_tree
            return stop_tree("flow", child_id)
        except Exception:  # noqa: BLE001
            from flow import run_store
            return run_store.RUNS.request_stop(child_id)

    from flow.launcher import FlowResumeError

    child = _nested.ChildKind(
        kind="flow", launch=_launch, resume=_resume, stop=_stop, result=_result,
        resume_errors=(FlowResumeError,),
    )
    return _nested.run_nested(child, ctx, cfg)
