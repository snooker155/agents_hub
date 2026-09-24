"""The system workspace (common/system_workspace.py): seeding, the repository
copy, its branches and the fetch command.

The "real repository" here is a throwaway git repo passed in as PROJECT_ROOT,
so nothing touches this checkout. The copy lands under the per process
temporary state root tests/conftest.py sets up.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from common import system_workspace as sw


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    full_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", **(env or {})}
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True, env=full_env).stdout.strip()


@pytest.fixture(autouse=True)
def registry():
    """The shipped agents, so the seeded flow's nodes resolve."""
    from common.bootstrap import seed_registry_from_bootstrap
    seed_registry_from_bootstrap()


@pytest.fixture
def origin(tmp_path, monkeypatch):
    """A small repository standing in for PROJECT_ROOT, on branch main."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    monkeypatch.setattr(sw, "PROJECT_ROOT", repo)
    yield repo
    import shutil
    shutil.rmtree(sw.repo_dir(), ignore_errors=True)


# ── seeding ──────────────────────────────────────────────────────────────────

def test_ensure_system_workspace_is_idempotent():
    from flow import store as flow_store
    from loops import store as loop_store
    from plans.models import JobKind, JobStatus
    from plans.service import plan_store
    from projects.storage import ProjectStore
    from common.paths import PROJECTS_FILE
    from workspace import get_workspace_metadata

    assert sw.ensure_system_workspace() is True
    assert sw.ensure_system_workspace() is False

    meta = get_workspace_metadata("system")
    assert meta["orchestrator"]["enabled"] is False
    for agent in ("service_agent", "swe_agent", "code_reviewer", "system_doctor", "system_engineer"):
        assert agent in meta["allowed_agents"]
    assert meta["budget"]["hard_limit_usd"] > 0

    project = ProjectStore(path=PROJECTS_FILE).get(sw.PROJECT_ID)
    assert project.name == "Agents Hub (copy)" and project.repo.local_path == "repo"
    assert project.workspace == "system"

    flow = flow_store.get_flow(sw.FLOW_ID)
    assert [n["agent_id"] for n in flow["nodes"]] == ["system_doctor", "system_engineer"]
    assert flow["edges"][0]["source"] == "triage" and flow["edges"][0]["target"] == "patch"
    assert sum(1 for f in flow_store.list_flows() if f["id"] == sw.FLOW_ID) == 1

    loop = loop_store.get_loop(sw.LOOP_ID)
    assert loop.flow_id == sw.FLOW_ID and loop.workspace == "system"
    assert (loop.max_iterations, loop.min_iterations, loop.cost_ceiling) == (2, 1, 2.0)
    assert loop.evaluator_mode == "model" and "[system]" in loop.exit_criterion

    jobs = [j for j in plan_store.list() if j.id == sw.JOB_ID]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.kind == JobKind.loop and job.loop_id == sw.LOOP_ID
    assert job.status == JobStatus.paused and job.cron == "0 */6 * * *"
    assert job.workspace == "system"


def test_seeded_flow_passes_validation():
    from flow.validate import validate_flow, resolve_entities
    flow = sw.flow_definition()
    validate_flow(flow)
    resolved = resolve_entities(flow["nodes"])
    assert set(resolved) == {"triage", "patch"}


def test_seeding_never_clones(origin):
    sw.ensure_system_workspace()
    assert not sw.repo_dir().exists()


def test_schedule_flips_status_and_rewrites_cron():
    from plans.models import JobStatus
    from plans.service import get_job
    sw.ensure_system_workspace()

    loop = sw.set_schedule(True, 4)
    assert loop["scheduled"] is True and loop["every_hours"] == 4
    assert loop["recurrence"] == "0 */4 * * *" and loop["next_run_at"]
    assert get_job(sw.JOB_ID).status == JobStatus.scheduled

    loop = sw.set_schedule(False, 24)
    assert loop["scheduled"] is False and loop["recurrence"] == "0 0 * * *"
    assert loop["every_hours"] == 24 and loop["next_run_at"] is None
    assert get_job(sw.JOB_ID).status == JobStatus.paused


# ── the copy ─────────────────────────────────────────────────────────────────

def test_ensure_clone_clones_then_fast_forwards(origin):
    result = sw.ensure_clone()
    assert result["error"] is None, result
    assert result["exists"] and result["branch"] == "main" and result["synced_at"]
    repo = Path(result["repo_dir"])
    assert repo == sw.repo_dir() and (repo / "README.md").read_text() == "hello\n"
    # The copy cannot push, and carries its own identity.
    assert "no-push" in _git(repo, "remote", "get-url", "--push", "origin")

    # A system branch in the copy survives a sync untouched.
    _git(repo, "branch", "system/2026-01-01-keep")
    keep_sha = _git(repo, "rev-parse", "system/2026-01-01-keep")

    (origin / "NEW.md").write_text("new\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "second")
    result = sw.ensure_clone()
    assert result["error"] is None, result
    assert (repo / "NEW.md").exists()
    assert result["head"] == _git(origin, "rev-parse", "--short", "HEAD")
    assert _git(repo, "rev-parse", "system/2026-01-01-keep") == keep_sha


def test_ensure_clone_refuses_when_the_copy_would_be_project_root(origin, monkeypatch):
    monkeypatch.setattr(sw, "repo_dir", lambda: origin)
    result = sw.ensure_clone()
    assert result["error"] and "running instance" in result["error"]
    with pytest.raises(sw.SystemCopyError):
        sw.copy_dir()


def test_ensure_clone_refuses_outside_the_system_workspace(origin, tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "repo_dir", lambda: tmp_path / "elsewhere")
    result = sw.ensure_clone()
    assert result["error"] and "system copy lives at" in result["error"]
    assert not (tmp_path / "elsewhere").exists()


def test_ensure_clone_needs_a_git_repository(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(sw, "PROJECT_ROOT", plain)
    result = sw.ensure_clone()
    assert result["exists"] is False and "not a git repository" in result["error"]


def test_a_plain_folder_at_the_copy_path_is_refused(origin):
    """Without a .git of its own, git would walk up into whatever repository
    holds the state directory. copy_dir must refuse instead."""
    sw.repo_dir().mkdir(parents=True, exist_ok=True)
    with pytest.raises(sw.SystemCopyError):
        sw.copy_dir()
    assert sw.list_branches() == []


# ── branches ─────────────────────────────────────────────────────────────────

def test_list_and_prune_branches_by_age(origin):
    sw.ensure_clone()
    repo = sw.repo_dir()
    old_date = "2020-01-01T00:00:00+00:00"
    _git(repo, "checkout", "-q", "-b", "system/2020-01-01-old")
    (repo / "old.txt").write_text("old\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old fix\n\nSystem-Task: task-old",
         env={"GIT_COMMITTER_DATE": old_date, "GIT_AUTHOR_DATE": old_date})
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "system/2026-09-24-new")
    _git(repo, "branch", "feature/not-mine")

    rows = {b["name"]: b for b in sw.list_branches()}
    assert set(rows) == {"system/2020-01-01-old", "system/2026-09-24-new"}
    assert rows["system/2020-01-01-old"]["task_id"] == "task-old"
    assert rows["system/2020-01-01-old"]["age_days"] > 365
    assert rows["system/2026-09-24-new"]["task_id"] is None

    deleted = sw.prune_branches(14)
    assert deleted == ["system/2020-01-01-old"]
    assert {b["name"] for b in sw.list_branches()} == {"system/2026-09-24-new"}
    assert "feature/not-mine" in _git(repo, "branch", "--list", "feature/*")


def test_prune_never_deletes_the_checked_out_branch(origin):
    sw.ensure_clone()
    repo = sw.repo_dir()
    old_date = "2020-01-01T00:00:00+00:00"
    _git(repo, "checkout", "-q", "-b", "system/2020-01-01-current")
    (repo / "x.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "x",
         env={"GIT_COMMITTER_DATE": old_date, "GIT_AUTHOR_DATE": old_date})
    assert sw.prune_branches(1) == []


def test_branch_names():
    from datetime import datetime, timezone
    day = datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert sw.branch_name("[system] Fix: stale runs!", today=day) == "system/2026-09-24-fix-stale-runs"
    assert sw.branch_name("", today=day) == "system/2026-09-24-task"


def test_fetch_command(monkeypatch):
    monkeypatch.setattr(sw, "_in_container", lambda: False)
    cmd = sw.fetch_command("system/2026-09-24-x")
    assert cmd == f"git fetch {sw.repo_dir()} system/2026-09-24-x:system/2026-09-24-x"

    monkeypatch.setattr(sw, "_in_container", lambda: True)
    first, second = sw.fetch_command("system/2026-09-24-x").split("\n")
    assert first.startswith("git fetch ")
    assert second.startswith("# ") and "docker exec" in second and "bundle" in second


def test_status_shape(origin):
    sw.ensure_system_workspace()
    st = sw.status()
    assert st["enabled"] is True and st["workspace"] == "system"
    assert st["project_id"] == sw.PROJECT_ID
    assert st["clone"]["exists"] is False
    loop = st["loop"]
    assert loop["loop_id"] == sw.LOOP_ID and loop["flow_id"] == sw.FLOW_ID
    assert loop["job_id"] == str(sw.JOB_ID) and loop["scheduled"] is False
    assert loop["every_hours"] == 6 and loop["last_run"] is None
    assert st["branches"] == []


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.system import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_routes(client, origin):
    sw.ensure_system_workspace()
    assert client.get("/api/system").json()["workspace"] == "system"
    synced = client.post("/api/system/sync").json()
    assert synced["exists"] is True and synced["error"] is None
    loop = client.post("/api/system/schedule", json={"enabled": True, "every_hours": 12}).json()
    assert loop["scheduled"] is True and loop["every_hours"] == 12
    assert client.post("/api/system/schedule", json={"enabled": True, "every_hours": 0}).status_code == 422
    _git(sw.repo_dir(), "branch", "system/2026-09-24-y")
    branches = client.get("/api/system/branches").json()
    assert branches[0]["name"] == "system/2026-09-24-y" and "git fetch" in branches[0]["fetch_command"]
    assert client.post("/api/system/prune", json={"older_than_days": 30}).json()["deleted"] == []


def test_routes_404_when_turned_off(client, monkeypatch):
    monkeypatch.setattr(sw, "enabled", lambda: False)
    for method, path in (("get", "/api/system"), ("post", "/api/system/sync"),
                         ("get", "/api/system/branches")):
        resp = getattr(client, method)(path)
        assert resp.status_code == 404 and "SYSTEM_WORKSPACE" in resp.json()["detail"]
