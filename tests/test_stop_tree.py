"""
Recursive stop: managers.runs.groups.stop_tree and the stop_group it now
powers.

A run group can nest another (a team a flow node ran) and both can own leaf
agent runs directly. Before stop_tree, stopping the outer group left the
inner one and its own leaves running. This is the fix: walk parent_run_id
down from the group asked to stop, and leaf_children at every stop along the
way.
"""
from __future__ import annotations

from managers import run_manager as rm
from managers.runs import groups as run_groups


def _a_flow_run(flow_run_id="fr-top", flow_id="flow-a", workspace="ws", task_id="t1"):
    from flow import run_store as flow_run_store
    flow_run_store.open_flow_run(flow_run_id, flow_id, workspace=workspace,
                                 task_id=task_id, title="nightly build")
    flow_run_store.mark_running(flow_run_id, pid=0)
    return flow_run_id


def _a_nested_team_run(team_run_id="team-nested", parent_run_id="fr-top", workspace="ws"):
    from teams import store as team_store
    from teams.models import TeamRun
    run = TeamRun(team_run_id=team_run_id, team_id="team-a", workspace=workspace,
                  goal="ship it", status="running", parent_run_id=parent_run_id)
    team_store.save_run(run)
    return team_run_id


def _a_leaf_run(run_id: str, *, parent_run_id: str, task_id: str = "t1"):
    """An in-process agent run (a team turn, a node run) directly owned by a
    group: the shape managers.runs.lifecycle._stop_run_record stops by
    marking the record rather than signalling a pid, same as the run group
    tests in tests/test_run_groups.py use."""
    rm.upsert_run({
        "run_id": run_id, "agent_id": "writer", "status": "running",
        "pid": 0, "in_process": True, "parent_run_id": parent_run_id,
        "task_id": task_id,
    })
    return run_id


def _tree():
    """fr-top (flow) -> team-nested (team, parent_run_id=fr-top) -> both own
    one leaf agent run directly (parent_run_id on the runs table)."""
    _a_flow_run()
    _a_leaf_run("leaf-flow-1", parent_run_id="fr-top")
    _a_nested_team_run()
    _a_leaf_run("leaf-team-1", parent_run_id="team-nested")


def test_stop_tree_stops_the_group_its_nested_team_and_every_leaf_run():
    from flow import run_store as flow_run_store
    from teams import store as team_store

    _tree()
    counts = run_groups.stop_tree("flow", "fr-top")

    assert counts["groups"] == 2       # the flow itself, and the nested team
    assert counts["entity_runs"] == 1  # the nested team run, found by parent_run_id
    assert counts["leaf_runs"] == 2    # both leaf agent runs

    assert flow_run_store.get_flow_run("fr-top")["status"] == "stopped"
    assert team_store.get_run("team-nested").status == "stopping"
    assert rm.get_run_by_id("leaf-flow-1")["status"] == "stop"
    assert rm.get_run_by_id("leaf-team-1")["status"] == "stop"


def test_stop_group_reaches_the_whole_tree_too():
    """stop_group is the public single-group verb; it must recurse exactly
    the way calling stop_tree directly does, because it now calls stop_tree."""
    from teams import store as team_store

    _tree()
    assert run_groups.stop_group("flow", "fr-top") is True
    assert team_store.get_run("team-nested").status == "stopping"
    assert rm.get_run_by_id("leaf-team-1")["status"] == "stop"


def test_a_group_with_nothing_nested_only_counts_itself():
    _a_flow_run("fr-lonely")
    counts = run_groups.stop_tree("flow", "fr-lonely")
    assert counts == {"groups": 1, "entity_runs": 0, "leaf_runs": 0}


def test_an_already_finished_group_stops_nothing():
    from flow import run_store as flow_run_store

    _a_flow_run("fr-done")
    flow_run_store.close_flow_run("fr-done", status="completed")
    counts = run_groups.stop_tree("flow", "fr-done")
    assert counts == {"groups": 0, "entity_runs": 0, "leaf_runs": 0}


def test_stop_tree_on_a_container_is_just_its_own_stop(no_launch):
    """A container has no entry in entity_runs (its "runs" are the task
    tree), so stop_tree is only its own adapter stop, which already
    recurses over the container's subtasks itself."""
    from uuid import uuid4

    from tasks import service as ts
    parent = ts.create_task(title="Ship the feature", workspace="ws")
    ts.add_subtask(parent.id, title="Write the code")

    counts = run_groups.stop_tree("container", str(parent.id))
    assert counts["groups"] == 1
    assert counts["entity_runs"] == 0
    assert counts["leaf_runs"] == 0
    assert ts.get_task(parent.id).status == ts.TaskStatus.stopped
    # Sanity: not a real run id, just confirming an unrelated uuid never
    # matches anything and the walk still terminates cleanly.
    assert run_groups.stop_tree("container", str(uuid4()))["groups"] == 0
