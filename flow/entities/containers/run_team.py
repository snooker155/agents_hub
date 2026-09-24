"""Container entity: run a team as a nested run of the flow run.

The node launches the team through ``teams.launcher.start_team_run`` with the
flow run as ``parent_run_id``, waits for it to finish and writes the team's
answer (the final synthesis the runner records as the run's result) into its
output key. See flow/entities/containers/_nested.py for launching, waiting,
reattaching on resume and the depth limit.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.entities.containers import _nested
from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="run_team",
    name="Run Team",
    category="container",
    group="Nested runs",
    entrypoint="flow.entities.containers.run_team:run",
    description=(
        "Run a team against a goal as a nested run of this flow and wait for "
        "its answer. The answer lands in the output key."
    ),
    inputs=[],
    outputs=["result"],
    config_schema={
        "team_id": {
            "type": "str", "default": "", "source": "teams",
            "description": "The team to run.",
        },
        "goal": {
            "type": "str", "default": "",
            "description": "The goal for the team. {state_key} is filled from flow state.",
        },
    },
    icon="users",
)


def _result(rec: Dict[str, Any]) -> str:
    result = str(rec.get("result") or "").strip()
    if result:
        return result
    # No synthesis recorded: fall back to the team's last message.
    try:
        from teams import store
        messages = [m for m in store.list_messages(str(rec.get("run_id")))
                    if m.kind in ("result", "message") and (m.content or "").strip()]
        if messages:
            return str(messages[-1].content)
    except Exception:  # noqa: BLE001 - an empty answer is still an answer
        pass
    return ""


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    cfg = config or {}
    team_id = _nested.config_value(cfg, "team_id")
    goal = _nested.fill(str(cfg.get("goal") or "").strip(), state)
    workspace = _nested.workspace_name(ctx)
    parent_id = _nested.parent_run_id(ctx)

    def _launch() -> str:
        from teams.launcher import start_team_run
        run_rec = start_team_run(team_id, goal, workspace=workspace, parent_run_id=parent_id)
        return str(run_rec.team_run_id)

    def _resume(child_id: str) -> Any:
        from teams.launcher import resume_team_run
        return resume_team_run(child_id)

    def _stop(child_id: str) -> Any:
        from teams.launcher import stop_team_run
        return stop_team_run(child_id)

    from teams.launcher import TeamResumeError

    child = _nested.ChildKind(
        kind="team", launch=_launch, resume=_resume, stop=_stop, result=_result,
        resume_errors=(TeamResumeError,),
    )
    return _nested.run_nested(child, ctx, cfg)
