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
    assert len(chats) == 2

    runs = query_runs(workspace="demo", limit=10)["items"]
    assert len(runs) == 3
    assert all(r["status"] == "completed" for r in runs)


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
    assert counts["agents"] == 3
    assert counts["project"] == 1
    assert counts["flow"] == 1
    assert counts["team"] == 1
    assert counts["scenario"] == 1
    assert counts["views"] == 4
    assert counts["tasks"] == 6
    assert counts["chats"] == 2
    assert counts["runs"] == 3
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
    assert body["counts"]["agents"] == 3
    assert body["counts"]["tasks"] == 6
    assert body["counts"]["chats"] == 2

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
    assert summary["request_keys"] == len(responses)
    assert summary["request_keys"] > 15

    # Flat {run_id: [frames]}, no wrapper: the frontend's resolver
    # (src/demo/resolver.js) reads this object's own keys as the set of
    # recorded run ids, so a metadata key here would read as a bogus one.
    streams = json.loads(streams_path.read_text(encoding="utf-8"))
    assert len(streams) == summary["stream_runs"] == 3
    for frames in streams.values():
        events = [f["event"] for f in frames]
        assert events[0] == "tool_start"
        assert events[-1] == "done"
