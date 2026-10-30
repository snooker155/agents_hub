"""
Flow, loop, team and scenario runs with no database (stage 4 of the remote
state work). The launcher prepares each run on the hub as usual; the process
it would spawn runs here the way its container does, reaching the hub only
through its run token (tests/remote_harness.py).

``REMOTE_AUDIT=<file>`` runs the same scenarios in audit mode (see
tests/test_remote_task_run.py).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from common import run_tokens
from remote_harness import audit_report, child_env, hub_server, misses, run_child

AUDIT = os.environ.get("REMOTE_AUDIT")


@pytest.fixture
def acme(tmp_path, reopen_db):
    (tmp_path / "hubroot").mkdir()
    reopen_db(tmp_path / "hubroot" / "agents_hub.db")
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace.storage import create_workspace_folder, update_workspace_metadata
    seed_registry_from_bootstrap()
    folder = create_workspace_folder("acme")
    update_workspace_metadata("acme", {"model_default": {"provider": "openai", "model": "gpt-4o-mini"}})
    return folder


@pytest.fixture
def captured(monkeypatch):
    """The specs the launchers would spawn, instead of spawning them."""
    specs: List[Dict[str, Any]] = []
    import runtime.entity_launch as entity_launch

    def _capture(spec: Dict[str, Any]) -> None:
        specs.append(dict(spec))

    monkeypatch.setattr(entity_launch, "launch_prepared", _capture)
    return specs


def _run_spec(tmp_path: Path, spec: Dict[str, Any], *, script: Optional[List[Any]] = None) -> str:
    """Run the process of one prepared launch as its container would."""
    from runtime.entity_launch import ENTRYPOINTS
    audit = tmp_path / "audit.txt" if AUDIT else None
    run_id = str(spec["run_id"])
    with hub_server(script) as hub:
        token = run_tokens.mint(run_id=run_id, session_id=spec.get("session_id"), workspace="acme")
        extra = {"AGENTS_HUB_ENTITY_RUN_ID": run_id, "AGENTS_HUB_ENTITY_RUN_KIND": str(spec["kind"])}
        if spec.get("session_id"):
            extra["AGENT_SESSION_ID"] = str(spec["session_id"])
        env = child_env(hub["url"], token, workspace="acme", root=tmp_path / "child", audit=audit,
                        extra=extra)
        out = run_child([f"runtime.{ENTRYPOINTS[str(spec['kind'])]}",
                         *[str(a) for a in spec.get("cli_args") or []]], env, timeout=180)
    text = out.stdout + out.stderr
    if audit is not None:
        with open(AUDIT, "a", encoding="utf-8") as fh:
            fh.write(f"######## {spec['kind']} {run_id}\n")
            for head, paths in sorted(audit_report(audit).items()):
                fh.write(f"## {head}  ({len(paths)})\n")
                for line in paths[:3]:
                    fh.write(f"   {line}\n")
    assert not misses(text), "\n".join(misses(text)) + "\n" + text[-4000:]
    assert out.returncode == 0, text[-4000:]
    return text


def test_a_flow_run_needs_no_database(acme, tmp_path, captured):
    from flow import store as flow_store
    from flow.launcher import start_flow_run
    flow_store.save_flow({
        "id": "f-remote", "name": "Two steps", "description": "", "workspace": "acme",
        "nodes": [{"id": n, "type": "flowNode", "position": {"x": 0, "y": 0},
                   "data": {"label": n, "agent_id": "swe_agent"}} for n in ("a", "b")],
        "edges": [{"id": "e", "source": "a", "target": "b"}],
    })
    from tasks.service import create_task
    task = create_task("flow work", "do two steps", workspace="acme")
    run_id, _session = start_flow_run(str(task.id), "f-remote", {"workspace": "acme"})
    (spec,) = captured
    _run_spec(tmp_path, spec)
    from flow.run_store import get_flow_run
    from common import entity_runs
    record = entity_runs.get(run_id) or {}
    flow_run = get_flow_run(run_id) or {}
    assert (record.get("status") or flow_run.get("status")) == "completed", (record, flow_run)


def _two_step_flow(flow_id: str = "f-remote") -> None:
    from flow import store as flow_store
    flow_store.save_flow({
        "id": flow_id, "name": "Two steps", "description": "", "workspace": "acme",
        "nodes": [{"id": n, "type": "flowNode", "position": {"x": 0, "y": 0},
                   "data": {"label": n, "agent_id": "swe_agent"}} for n in ("a", "b")],
        "edges": [{"id": "e", "source": "a", "target": "b"}],
    })


def test_a_loop_run_needs_no_database(acme, tmp_path, captured):
    from loops import store
    from loops.launcher import start_loop_run
    from loops.models import Loop
    _two_step_flow()
    loop = store.save_loop(Loop(name="Polish", flow_id="f-remote", exit_criterion="reads well",
                                workspace="acme", max_iterations=1))
    run = start_loop_run(loop.loop_id, "write it", workspace="acme")
    (spec,) = captured
    _run_spec(tmp_path, spec)
    done = store.get_run(getattr(run, "loop_run_id", None) or getattr(run, "team_run_id", None))
    assert str(getattr(done.status, "value", done.status)) in ("completed", "failed", "stopped"), done


def test_a_team_run_needs_no_database(acme, tmp_path, captured):
    from teams import store
    from teams.launcher import start_team_run
    from teams.models import Team, TeamMember
    team = store.save_team(Team(name="Crew", mode="parallel", workspace="acme",
                                members=[TeamMember(agent_id="swe_agent", name="Sam",
                                                    manifest="writes code")]))
    run = start_team_run(team.team_id, "say hello", workspace="acme")
    (spec,) = captured
    _run_spec(tmp_path, spec)
    done = store.get_run(getattr(run, "loop_run_id", None) or getattr(run, "team_run_id", None))
    assert str(getattr(done.status, "value", done.status)) == "completed", done


def test_a_scenario_run_needs_no_database(acme, tmp_path, captured):
    from playground import store
    from playground.launcher import start_scenario_run
    from playground.models import Role, Scenario
    scenario = store.save_scenario(Scenario(name="s", environment="market", workspace="acme",
                                            roles=[Role(agent_id="swe_agent", name="Alice")],
                                            max_ticks=1))
    run = start_scenario_run(scenario.scenario_id, workspace="acme")
    (spec,) = captured
    _run_spec(tmp_path, spec)
    done = store.get_sim_run(run.sim_run_id)
    assert str(getattr(done.status, "value", done.status)) in ("completed", "finished"), done
