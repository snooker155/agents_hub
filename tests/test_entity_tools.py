"""
The builder tools for teams, loops and projects.

These are the tools that let an agent create and maintain the service entities
the dashboard pages own, so what is verified here is the same thing those pages
enforce: a write that would produce an unrunnable entity is refused, and refused
*before* anything is stored. A tool that saved first and complained afterwards
would leave the user with a broken team and a polite error.

Every tool answers in JSON, so the assertions read the parsed answer rather than
the prose — that contract is what the agent is steering by.
"""
import json

import pytest

from agents.registry import AgentSpec
from tools.loop_management import (
    create_loop_tool, delete_loop_tool, get_loop_tool, list_loops_tool,
    modify_loop_tool, validate_loop_tool,
)
from tools.project_management import (
    create_project_tool, delete_project_tool, get_project_tool,
    list_projects_tool, modify_project_tool,
)
from tools.team_management import (
    create_team_tool, delete_team_tool, get_team_tool, modify_team_tool,
)


def call(tool, **kwargs):
    """Invoke a tool and parse its JSON answer."""
    return json.loads(tool.invoke(kwargs))


@pytest.fixture
def cast(monkeypatch):
    """Two registered agents to build entities out of, and no others.

    The tools validate against the live registry, so a test that did not pin it
    would pass or fail on whatever the developer's install happens to hold.
    """
    specs = [
        AgentSpec(id="alpha", name="Alpha", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor",
                  description="does the first half"),
        AgentSpec(id="beta", name="Beta", type="langchain",
                  entrypoint="agents.agent_factory:build_agent_executor",
                  description="does the second half"),
    ]
    # The tools import the registry lazily inside each call, so patching the
    # source module is what they will actually see.
    import agents.registry as registry
    monkeypatch.setattr(registry, "list_agents", lambda: list(specs))
    monkeypatch.setattr(registry, "get_agent",
                        lambda aid: next((s for s in specs if s.id == aid), None))
    return specs


@pytest.fixture
def no_workspace(monkeypatch):
    """Run outside any workspace, so nothing is filtered by membership."""
    import common.workspace_context as wc
    monkeypatch.setattr(wc, "resolve_active_workspace", lambda preferred=None: None)
    for module in ("tools.team_management",
                   "tools.loop_management", "tools.project_management"):
        mod = __import__(module, fromlist=["resolve_active_workspace"])
        monkeypatch.setattr(mod, "resolve_active_workspace", lambda preferred=None: None)


# ── Teams ─────────────────────────────────────────────────────────────────────

def _members():
    return [
        {"agent_id": "alpha", "name": "Lead", "role": "team lead",
         "manifest": "splits the work and signs it off"},
        {"agent_id": "beta", "name": "Rev", "role": "reviewer",
         "manifest": "reviews diffs for security regressions"},
    ]


def test_a_team_is_created_with_its_roster_and_charter(cast, no_workspace):
    out = call(create_team_tool, name="Platform", members=_members(),
               charter="keep the platform shippable", leader_agent_id="alpha")
    assert out["ok"], out
    assert [m["name"] for m in out["team"]["members"]] == ["Lead", "Rev"]
    assert call(get_team_tool, team_id=out["team_id"])["team"]["mode"] == "centralized"


def test_centralized_mode_needs_a_leader_on_the_roster(cast, no_workspace):
    missing = call(create_team_tool, name="Headless", members=_members())
    assert missing["ok"] is False
    assert any("leader" in e for e in missing["errors"])

    outsider = call(create_team_tool, name="Outsider", members=_members(),
                    leader_agent_id="gamma")
    assert outsider["ok"] is False
    assert any("not on the roster" in e for e in outsider["errors"])


def test_a_member_without_a_manifest_is_a_warning_not_a_refusal(cast, no_workspace):
    out = call(create_team_tool, name="Quiet", mode="parallel",
               members=[{"agent_id": "alpha", "name": "Solo"}],
               charter="think out loud")
    assert out["ok"], out
    assert any("manifest" in w for w in out["warnings"])


def test_members_can_be_added_and_dropped_by_name(cast, no_workspace):
    created = call(create_team_tool, name="Platform", members=_members(),
                   leader_agent_id="alpha", charter="ship it")
    tid = created["team_id"]
    dropped = call(modify_team_tool, team_id=tid, remove_members=["Rev"])
    assert [m["name"] for m in dropped["team"]["members"]] == ["Lead"]
    added = call(modify_team_tool, team_id=tid,
                 add_members=[{"agent_id": "beta", "name": "Doc",
                               "manifest": "writes the release notes"}])
    assert [m["name"] for m in added["team"]["members"]] == ["Lead", "Doc"]


def test_a_round_trip_through_modify_keeps_the_roster_intact(cast, no_workspace):
    """``to_dict`` emits display-only fields that ``from_dict`` does not read;
    a modify that fed them back would corrupt the record it was re-saving."""
    created = call(create_team_tool, name="Platform", members=_members(),
                   leader_agent_id="alpha", charter="ship it")
    out = call(modify_team_tool, team_id=created["team_id"], description="now with a description")
    assert out["ok"], out
    assert [m["name"] for m in out["team"]["members"]] == ["Lead", "Rev"]
    assert out["team"]["leader_agent_id"] == "alpha"
    assert out["team"]["charter"] == "ship it"


def test_deleting_a_team_reports_it(cast, no_workspace):
    created = call(create_team_tool, name="Platform", members=_members(),
                   leader_agent_id="alpha", charter="ship it")
    assert call(delete_team_tool, team_id=created["team_id"])["ok"]
    assert call(get_team_tool, team_id=created["team_id"])["code"] == "not_found"


# ── Loops ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def one_flow(monkeypatch):
    """A single stored flow for loops to wrap."""
    flow = {"id": "flow-1", "name": "Write and review", "workspace": None,
            "nodes": [{"id": "n1", "data": {"agent_id": "alpha"}}], "edges": []}
    import flow.store as flow_store
    monkeypatch.setattr(flow_store, "get_flow",
                        lambda fid: flow if fid == flow["id"] else None)
    monkeypatch.setattr(flow_store, "list_flows", lambda: [flow])
    return flow


def test_a_loop_wraps_an_existing_flow(cast, one_flow, no_workspace):
    out = call(create_loop_tool, name="Polish", flow_id="flow-1",
               exit_criterion="every claim carries a source",
               convergence={"max_iterations": 4, "cost_ceiling": 1.0})
    assert out["ok"], out
    assert out["loop"]["convergence"]["max_iterations"] == 4
    assert call(list_loops_tool)["count"] == 1


def test_a_loop_over_a_missing_flow_is_refused(cast, one_flow, no_workspace):
    out = call(create_loop_tool, name="Nowhere", flow_id="flow-404",
               exit_criterion="good enough")
    assert out["ok"] is False
    assert any("does not exist" in e for e in out["errors"])


def test_an_empty_exit_criterion_is_refused(cast, one_flow, no_workspace):
    """A loop that cannot be judged runs to its ceiling every time, so the
    criterion is required rather than defaulted to something agreeable."""
    out = call(validate_loop_tool, flow_id="flow-1", exit_criterion="")
    assert out["valid"] is False
    assert any("exit_criterion" in e for e in out["errors"])


def test_the_iteration_ceiling_is_enforced_not_suggested(cast, one_flow, no_workspace):
    out = call(create_loop_tool, name="Forever", flow_id="flow-1",
               exit_criterion="perfection", convergence={"max_iterations": 500})
    assert out["ok"] is False
    assert any("may not exceed" in e for e in out["errors"])


def test_an_agent_evaluator_must_name_a_registered_agent(cast, one_flow, no_workspace):
    out = call(validate_loop_tool, flow_id="flow-1", exit_criterion="readable",
               evaluator_mode="agent", evaluator_agent_id="nobody")
    assert out["valid"] is False
    assert any("not registered" in e for e in out["errors"])


def test_convergence_settings_merge(cast, one_flow, no_workspace):
    created = call(create_loop_tool, name="Polish", flow_id="flow-1",
                   exit_criterion="sourced",
                   convergence={"max_iterations": 4, "target_score": 70})
    out = call(modify_loop_tool, loop_id=created["loop_id"],
               convergence={"max_iterations": 6})
    assert out["loop"]["convergence"]["max_iterations"] == 6
    assert out["loop"]["convergence"]["target_score"] == 70


def test_deleting_a_loop_leaves_its_flow_alone(cast, one_flow, no_workspace):
    created = call(create_loop_tool, name="Polish", flow_id="flow-1",
                   exit_criterion="sourced")
    assert call(delete_loop_tool, loop_id=created["loop_id"])["ok"]
    assert call(get_loop_tool, loop_id=created["loop_id"])["code"] == "not_found"
    import flow.store as flow_store
    assert flow_store.get_flow("flow-1") is not None


# ── Projects ──────────────────────────────────────────────────────────────────

@pytest.fixture
def workspace(monkeypatch, tmp_path):
    """A workspace that exists on disk, so projects can own a folder in it.

    The project store is a JSON file under the suite-wide state root, not the
    per-test database, so it is pointed at ``tmp_path`` here — otherwise every
    project these tests create would still be there for the next one.
    """
    import tools.project_management as pm
    from projects.storage import ProjectStore

    folder = tmp_path / "ws"
    folder.mkdir()
    store = ProjectStore(path=tmp_path / "projects.json")
    monkeypatch.setattr(pm, "_store", lambda: store)
    monkeypatch.setattr(pm, "resolve_active_workspace", lambda preferred=None: "ws")

    import workspace as ws_module
    monkeypatch.setattr(ws_module, "get_workspace_folder",
                        lambda name: folder if name == "ws" else None)
    monkeypatch.setattr(ws_module, "resolve_project_root",
                        lambda ws, sub: (folder / sub).resolve())
    return folder


def test_a_project_is_created_in_its_workspace(workspace):
    out = call(create_project_tool, name="Pricing Study", type="research",
               description="how the market prices us")
    assert out["ok"], out
    assert out["project"]["workspace"] == "ws"
    # The enum is reported as the value the API speaks, not "ProjectType.research".
    assert out["project"]["type"] == "research"
    assert out["project"]["status"] == "active"


def test_a_second_project_with_the_same_name_is_refused(workspace):
    call(create_project_tool, name="Pricing Study")
    out = call(create_project_tool, name="pricing study")
    assert out["ok"] is False
    assert out["code"] == "conflict"


def test_an_unknown_project_type_names_the_ones_that_exist(workspace):
    out = call(create_project_tool, name="Odd", type="interpretive-dance")
    assert out["ok"] is False
    assert "general" in out["error"]


def test_archiving_is_an_ordinary_modify(workspace):
    created = call(create_project_tool, name="Migration")
    out = call(modify_project_tool, project_id=created["project_id"], status="archived")
    assert out["project"]["status"] == "archived"


def test_a_modify_with_nothing_in_it_is_refused(workspace):
    created = call(create_project_tool, name="Migration")
    out = call(modify_project_tool, project_id=created["project_id"])
    assert out["ok"] is False


def test_a_project_with_tasks_attached_cannot_be_deleted(workspace, monkeypatch):
    """Deleting would orphan the work. Archiving is the reversible answer, and
    the refusal says so."""
    created = call(create_project_tool, name="Migration")

    class _Task:
        project_id = created["project_id"]

    import tasks.service as tasks_service
    monkeypatch.setattr(tasks_service, "list_tasks", lambda *a, **k: [_Task()])

    out = call(delete_project_tool, project_id=created["project_id"])
    assert out["ok"] is False
    assert out["tasks_count"] == 1
    assert "archived" in out["error"]

    monkeypatch.setattr(tasks_service, "list_tasks", lambda *a, **k: [])
    assert call(delete_project_tool, project_id=created["project_id"])["ok"]


def test_listing_reports_how_much_work_hangs_off_each_project(workspace, monkeypatch):
    created = call(create_project_tool, name="Migration")

    class _Task:
        project_id = created["project_id"]

    import tasks.service as tasks_service
    monkeypatch.setattr(tasks_service, "list_tasks", lambda *a, **k: [_Task(), _Task()])

    listed = call(list_projects_tool)
    assert listed["projects"][0]["tasks_count"] == 2
    assert call(get_project_tool, project_id=created["project_id"])["project"]["tasks_count"] == 2
