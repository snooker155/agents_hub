"""
Demo workspace: seeding, removal, the /api/demo routes, and the fixture
export script. See common/demo_workspace.py and docs/demo.md.

``export_demo_fixtures`` is loaded by file path rather than as
``scripts.export_demo_fixtures``: some environments already have an unrelated
``scripts`` distribution installed, and a real package anywhere on
``sys.path`` wins over this repository's ``scripts/`` directory as a
namespace package (PEP 420), so importing it by name is not reliable.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = str(REPO_ROOT / "dashboard" / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _load_export_script():
    spec = importlib.util.spec_from_file_location(
        "_demo_export_fixtures_script",
        REPO_ROOT / "scripts" / "export_demo_fixtures.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    """Keep generated definition markdown out of the real agents/definitions,
    the same way tests/test_agent_versions.py isolates it: ensure_demo_workspace
    writes each demo agent's instructions.md through agents.prompt_assembly,
    which otherwise targets the repository's own agents/definitions/."""
    from agents import prompt_assembly

    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    return defs


@pytest.fixture(autouse=True)
def _clean_demo_workspace_folder():
    """Remove any leftover ``demo`` workspace folder before each test.

    ``fresh_db`` (conftest.py) gives every test a new, empty database, but the
    workspaces *root* is a real directory under the one ``AGENTS_HUB_ROOT`` the
    whole test session shares — it is not recreated per test. A workspace folder
    another test in this file left on disk would otherwise make
    ``ensure_demo_workspace`` see the demo as already present before this test
    ever seeds it.
    """
    from workspace.storage import delete_workspace_folder

    delete_workspace_folder("demo")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from dashboard.backend.main import app
    return TestClient(app)


# ── seeding ──────────────────────────────────────────────────────────────────

def test_ensure_demo_workspace_seeds_everything():
    from agents.registry import get_agent
    from common import chat_store
    from common.demo_workspace import (
        DEMO_AGENT_IDS,
        DEMO_FLOW_ID,
        DEMO_SCENARIO_ID,
        DEMO_TEAM_ID,
        ensure_demo_workspace,
    )
    from flow.store import get_flow
    from managers.run_manager import query_runs
    from playground.store import get_scenario
    from tasks.service import list_tasks
    from teams.store import get_team
    from views.store import list_views
    from workspace.storage import get_workspace_folder

    assert ensure_demo_workspace() is True
    assert get_workspace_folder("demo") is not None

    for agent_id in DEMO_AGENT_IDS:
        spec = get_agent(agent_id)
        assert spec is not None, agent_id
        assert spec.owner_workspace == "demo"
        assert spec.tools  # every demo agent carries at least one tool

    project_dir = get_workspace_folder("demo") / "Demo_site"
    for rel in ("README.md", "src/app.py", "docs/notes.md", "data/sales.csv"):
        assert (project_dir / rel).is_file(), rel

    assert get_flow(DEMO_FLOW_ID) is not None
    assert get_team(DEMO_TEAM_ID) is not None
    assert get_scenario(DEMO_SCENARIO_ID) is not None

    views = list_views(workspace="demo")
    assert len(views) == 4
    assert {"chart", "table", "markdown", "document"} == {v["kind"] for v in views}

    demo_tasks = [t for t in list_tasks() if t.workspace == "demo"]
    assert len(demo_tasks) == 6
    statuses = {t.status.value for t in demo_tasks}
    assert {"todo", "in_progress", "blocked", "done"} <= statuses
    done_results = [t for t in demo_tasks if t.status.value == "done"]
    assert len(done_results) == 3

    chats = chat_store.list_chats(workspace="demo", limit=10)["items"]
    assert len(chats) == 6
    # Two of the chats run over more than one turn: one run per turn, every
    # turn of a chat on the same session.
    transcripts = {c["id"]: chat_store.get_chat(c["id"]) for c in chats}
    assert len(transcripts["demo_chat_3"]["messages"]) == 4
    assert len(transcripts["demo_chat_5"]["messages"]) == 4
    assert transcripts["demo_chat_4"]["messages"][1]["response_obj"]["view_kind"] == "table"

    runs = query_runs(workspace="demo", limit=20)["items"]
    assert len(runs) == 11  # 8 chat turns + 3 task runs
    assert all(r["status"] == "completed" for r in runs)
    by_id = {r["run_id"]: r for r in runs}
    assert {"demo_chat_3_run", "demo_chat_3_run_2", "demo_task_run_categories"} <= set(by_id)
    # The task runs are tied to the tasks they closed.
    done_ids = {str(t.id) for t in done_results}
    assert by_id["demo_task_run_categories"]["task_id"] in done_ids
    assert by_id["demo_task_run_rename"]["task_id"] in done_ids

    # One agent and the flow are published, so the marketplace has a card.
    assert get_agent("demo_writer").shared is True
    assert get_flow(DEMO_FLOW_ID).get("shared") is True

    from instances import store as istore
    instances = istore.list_instances(limit=10, workspace="demo")["items"]
    assert {i["kind"] for i in instances} == {"resident", "task"}
    assert {i["state"] for i in instances} == {"finished", "stopped"}
    writer = next(i for i in instances if i["label"] == "Demo Writer #1")
    assert writer["runs_count"] == 4  # the rename run + three writer chat turns

    from services import store as sstore
    services = sstore.list_services(workspace="demo")
    assert len(services) == 1
    assert services[0]["status"] == "paused"
    replica = next(i for i in instances if i.get("service_id") == services[0]["service_id"])
    assert replica["state"] == "stopped"
    assert {e["kind"] for e in sstore.events(services[0]["service_id"])} >= {"created", "paused"}

    from files import service as files_service
    files = files_service.list_files("demo", limit=50)
    assert {f["name"] for f in files} == {
        "README.md", "app.py", "notes.md", "sales.csv",
        "supplier-questions.md", "august-report.md",
    }
    uploaded = next(f for f in files if f["name"] == "supplier-questions.md")
    assert uploaded["source"] == "upload"
    assert [u["ref_id"] for u in files_service.list_uses(uploaded["file_id"], "chat")] == ["demo_chat_6"]


def test_ensure_demo_workspace_is_idempotent():
    from common.demo_workspace import ensure_demo_workspace
    from tasks.service import list_tasks

    assert ensure_demo_workspace() is True
    assert ensure_demo_workspace() is False  # already present: no-op

    demo_tasks = [t for t in list_tasks() if t.workspace == "demo"]
    assert len(demo_tasks) == 6  # not duplicated by the second call


def test_capability_guard_accepts_the_demo_agents():
    from agents.registry import get_agent
    from common.demo_workspace import DEMO_AGENT_IDS, ensure_demo_workspace
    from tools.capabilities import check_combination

    ensure_demo_workspace()
    for agent_id in DEMO_AGENT_IDS:
        spec = get_agent(agent_id)
        violation = check_combination(spec.tools)
        assert violation is None, (
            f"{agent_id}: {violation.message if violation else ''}"
        )


# ── removal ──────────────────────────────────────────────────────────────────

def test_remove_demo_workspace_removes_everything_and_only_that():
    from agents.registry import get_agent
    from common.demo_workspace import ensure_demo_workspace, remove_demo_workspace
    from tasks.models import CreatedBy
    from tasks.service import create_task, get_task
    from workspace.storage import get_workspace_folder

    ensure_demo_workspace()

    # An unrelated task in another workspace must survive the removal: only
    # workspace == "demo" (or a fixed demo_ id) may be touched.
    unrelated = create_task(
        "Unrelated task", "Not part of the demo.",
        created_by=CreatedBy.user, workspace="default",
    )

    counts = remove_demo_workspace()
    assert counts["agents"] == 4
    assert counts["project"] == 1
    assert counts["flow"] == 1
    assert counts["team"] == 1
    assert counts["scenario"] == 1
    assert counts["views"] == 4
    assert counts["tasks"] == 6
    assert counts["chats"] == 6
    assert counts["runs"] == 11
    assert counts["instances"] == 3
    assert counts["services"] == 1
    assert counts["files"] == 6
    assert counts["workspace"] == 1

    assert get_workspace_folder("demo") is None
    for agent_id in ("demo_writer", "demo_analyst", "demo_reviewer"):
        assert get_agent(agent_id) is None

    assert get_task(unrelated.id) is not None


def test_remove_demo_workspace_without_seeding_first_is_a_harmless_no_op():
    from common.demo_workspace import remove_demo_workspace

    counts = remove_demo_workspace()
    assert all(v == 0 for v in counts.values())


# ── routes ───────────────────────────────────────────────────────────────────

def test_demo_status_route(client):
    resp = client.get("/api/demo")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "enabled": body["enabled"],  # the DEMO_WORKSPACE setting, whatever it is
        "present": False,
        "workspace": "demo",
        "counts": {"agents": 0, "views": 0, "tasks": 0, "chats": 0},
    }


def test_demo_toggle_route_seeds_and_removes(client):
    resp = client.post("/api/demo", json={"present": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["counts"]["agents"] == 4
    assert body["counts"]["tasks"] == 6
    assert body["counts"]["chats"] == 6

    assert client.get("/api/demo").json()["present"] is True

    resp = client.post("/api/demo", json={"present": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is False
    assert body["counts"]["agents"] == 0


# ── install.sh ───────────────────────────────────────────────────────────────

def test_install_sh_has_the_with_demo_flag():
    text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--with-demo" in text
    assert "DEMO_WORKSPACE=1" in text


# ── fixture export ───────────────────────────────────────────────────────────

def test_export_script_produces_both_files(tmp_path):
    export_demo_fixtures = _load_export_script()

    out_dir = tmp_path / "fixtures"
    summary = export_demo_fixtures.build_fixtures(out_dir)

    fixtures_path = out_dir / "fixtures.json"
    streams_path = out_dir / "streams.json"
    assert fixtures_path.is_file()
    assert streams_path.is_file()

    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    assert fixtures["workspace"] == "demo"
    assert "generated_at" in fixtures
    responses = fixtures["responses"]
    assert "GET /api/health" in responses
    assert "GET /api/auth/mode" in responses
    assert "GET /api/workspaces" in responses
    assert "GET /api/agents" in responses
    assert "GET /api/agents?workspace=demo" in responses
    assert "GET /api/playground/environments" in responses
    assert "GET /api/projects/demo_project_site/tasks" in responses
    assert "GET /api/instances?workspace=demo" in responses
    assert "GET /api/services?workspace=demo" in responses
    assert "GET /api/files?limit=500&workspace=demo" in responses
    assert "GET /api/marketplace/agents?workspace=demo" in responses
    assert summary["request_keys"] == len(responses)
    assert summary["request_keys"] > 15
    assert summary["instances"] == 3
    assert summary["services"] == 1
    assert summary["files"] == 6

    # The published demo hides the playground's quest designer from every
    # agent list, and lists the one published demo agent on the marketplace.
    for key in ("GET /api/agents", "GET /api/agents?workspace=demo",
                "GET /api/marketplace/agents?workspace=demo"):
        assert all(a["id"] != "plot-manager" for a in responses[key]), key
    assert [a["id"] for a in responses["GET /api/marketplace/agents?workspace=demo"]] == ["demo_writer"]
    assert any(a["id"] == "demo_support" for a in responses["GET /api/agents?workspace=demo"])

    # Flat {run_id: [frames]}, no wrapper: the frontend's resolver
    # (src/demo/resolver.js) reads this object's own keys as the set of
    # recorded run ids, so a metadata key here would read as a bogus one.
    streams = json.loads(streams_path.read_text(encoding="utf-8"))
    assert len(streams) == summary["stream_runs"] == 11
    for frames in streams.values():
        events = [f["event"] for f in frames]
        assert events[0] == "tool_start"
        assert events[-1] == "done"
