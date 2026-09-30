"""The system loop's tools (tools/system_ops.py), the capability guard's system
workspace rule, and the scheduled job kind that starts the loop.

The tools run against a real clone of a throwaway repository (standing in for
PROJECT_ROOT) under the per process state root. No model is called; the loop
launcher is replaced where a job fires.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from common import system_workspace as sw


def _call(tool, **kwargs) -> dict:
    return json.loads(tool.invoke(kwargs))


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


@pytest.fixture
def copy(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "app.py").write_text("def answer():\n    return 41\n")
    (origin / "tests").mkdir()
    (origin / "tests" / "test_app.py").write_text(
        "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n")
    (origin / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    (origin / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "first")
    monkeypatch.setattr(sw, "PROJECT_ROOT", origin)
    result = sw.ensure_clone()
    assert result["error"] is None, result
    yield sw.repo_dir()
    import shutil
    shutil.rmtree(sw.repo_dir(), ignore_errors=True)


@pytest.fixture
def task():
    from tasks import service as tasks_service
    return tasks_service.create_task(title="[system] Fix the answer", description="code: yes",
                                     workspace="system")


# ── commit ───────────────────────────────────────────────────────────────────

def test_system_commit_creates_the_branch_and_returns_to_main(copy, task):
    from tools.system_ops import system_commit
    (copy / "app.py").write_text("def answer():\n    return 42\n")
    out = _call(system_commit, task_id=str(task.id), message="Return the right answer")
    assert out["ok"] is True, out
    assert out["branch"].startswith("system/") and out["branch"].endswith("-fix-the-answer")
    assert out["files"] == ["app.py"] and out["sha"]
    # The copy is back on its default branch, which never got the commit.
    assert _git(copy, "branch", "--show-current") == "main"
    assert "return 41" in _git(copy, "show", "main:app.py")
    assert "return 42" in _git(copy, "show", f"{out['branch']}:app.py")
    assert f"System-Task: {task.id}" in _git(copy, "log", "-1", "--format=%B", out["branch"])

    # A second fix for the same task reuses the branch; the same edit made
    # again on main merges cleanly, and the new file comes along.
    (copy / "app.py").write_text("def answer():\n    return 42\n")
    (copy / "NOTES.md").write_text("why\n")
    again = _call(system_commit, task_id=str(task.id), message="Notes")
    assert again["ok"] is True, again
    assert again["branch"] == out["branch"] and again["files"] == ["NOTES.md"]
    assert _git(copy, "branch", "--show-current") == "main"


def test_a_conflicting_second_fix_is_refused_and_the_work_kept(copy, task):
    from tools.system_ops import system_commit
    (copy / "app.py").write_text("def answer():\n    return 42\n")
    first = _call(system_commit, task_id=str(task.id), message="fix")
    (copy / "app.py").write_text("def answer():\n    return 6 * 7\n")
    out = _call(system_commit, task_id=str(task.id), message="another way")
    assert out["ok"] is False and "conflict" in out["error"]
    assert _git(copy, "branch", "--show-current") == "main"
    assert "6 * 7" in (copy / "app.py").read_text()
    assert _git(copy, "stash", "list") == ""
    assert "return 42" in _git(copy, "show", f"{first['branch']}:app.py")


def test_commit_refuses_the_default_branch(copy):
    (copy / "app.py").write_text("changed\n")
    with pytest.raises(sw.SystemCopyError, match="never on the copy's default branch"):
        sw.commit_on_branch("main", "sneaky")
    with pytest.raises(sw.SystemCopyError):
        sw.commit_on_branch("feature/x", "not a system branch")
    assert _git(copy, "log", "--oneline", "main").count("\n") == 0  # still one commit


def test_commit_never_stages_secrets(copy, task):
    from tools.system_ops import system_commit
    (copy / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    (copy / "app.py").write_text("def answer():\n    return 42\n")
    out = _call(system_commit, task_id=str(task.id), message="fix")
    assert out["ok"] is True
    assert ".env" not in out["files"]


def test_commit_on_an_unknown_task(copy):
    from tools.system_ops import system_commit
    out = _call(system_commit, task_id="not-a-uuid", message="x")
    assert out["ok"] is False and out["code"] == "not_found"


def test_tools_refuse_without_a_copy(tmp_path, monkeypatch, task):
    from tools.system_ops import system_commit, system_run_tests
    assert not sw.repo_dir().exists()
    assert _call(system_run_tests, paths=[])["code"] == "refused"
    assert _call(system_commit, task_id=str(task.id), message="x")["code"] == "refused"


# ── patch ────────────────────────────────────────────────────────────────────

def test_system_attach_patch_writes_the_marker_and_the_diff(copy, task, monkeypatch):
    from tasks import service as tasks_service
    from tools.system_ops import system_attach_patch, system_commit
    monkeypatch.setattr(sw, "_in_container", lambda: False)
    (copy / "app.py").write_text("def answer():\n    return 42\n")
    commit = _call(system_commit, task_id=str(task.id), message="fix")
    tests = {"exit_code": 0, "passed": 2, "failed": 0, "duration_s": 0.4, "runner": "local",
             "paths": ["tests/test_app.py"]}
    out = _call(system_attach_patch, task_id=str(task.id), branch=commit["branch"], tests=tests)
    assert out["ok"] is True, out

    text = tasks_service.get_task_result(task.id)
    first, rest = text.split("\n", 1)
    assert first.startswith("<!-- system-patch ") and first.endswith(" -->")
    marker = json.loads(first[len("<!-- system-patch "):-len(" -->")])
    assert marker["branch"] == commit["branch"]
    assert marker["repo_dir"] == str(copy)
    assert commit["sha"].startswith(marker["commit"])
    assert marker["fetch_command"] == f"git fetch {copy} {commit['branch']}:{commit['branch']}"
    assert "## System patch" in rest
    assert "```bash\n" + marker["fetch_command"] in rest
    assert "2 passed" in rest
    assert "```diff" in rest and "-    return 41" in rest and "+    return 42" in rest


def test_attach_patch_caps_a_large_diff(copy, task):
    (copy / "big.txt").write_text("x" * 200 + "\n" + ("line of text\n" * 8000))
    commit = sw.commit_on_branch(sw.branch_name(task.title), "big", task_id=str(task.id))
    info = sw.diff_against_default(commit["branch"])
    info["commit"] = info["commit"][:12]
    patch = sw.patch_markdown(info, "not run")
    assert patch["truncated"] is True
    assert "was cut at 60 KB" in patch["markdown"]
    assert len(patch["markdown"].encode()) < 70 * 1024


def test_attach_patch_refuses_a_non_system_branch(copy, task):
    from tools.system_ops import system_attach_patch
    out = _call(system_attach_patch, task_id=str(task.id), branch="main")
    assert out["ok"] is False and out["code"] == "refused"


# ── tests ────────────────────────────────────────────────────────────────────

def test_system_run_tests_in_subprocess_mode(copy, monkeypatch):
    import runtime.entity_launch as el
    from tools.system_ops import system_run_tests
    monkeypatch.setattr(el, "execution_mode_for", lambda ws: "local")
    out = _call(system_run_tests, paths=["tests/test_ok.py"], timeout=120)
    assert out["ok"] is True, out
    t = out["tests"]
    assert t["runner"] == "local" and t["exit_code"] == 0
    assert t["passed"] == 1 and t["failed"] == 0
    assert "1 passed" in t["tail"]

    failing = _call(system_run_tests, paths=["tests/test_app.py"], timeout=120)["tests"]
    assert failing["exit_code"] != 0 and failing["failed"] == 1


def test_run_tests_refuses_paths_outside_the_copy_and_options(copy):
    for bad in ("../../etc/passwd", "-p", "/etc"):
        with pytest.raises(sw.SystemCopyError):
            sw.run_tests([bad])


def test_run_tests_environment_holds_no_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")
    monkeypatch.setenv("AGENTS_HUB_DATABASE_URL", "postgresql://prod")
    env = sw._test_env("/tmp/x")
    assert "OPENAI_API_KEY" not in env and "AGENTS_HUB_DATABASE_URL" not in env
    assert env["AGENTS_HUB_ROOT"] == "/tmp/x"


def test_prune_tool_needs_approval(copy):
    from tools.system_ops import system_prune_branches
    out = _call(system_prune_branches, older_than_days=14)
    assert out["ok"] is False and out["code"] == "approval_required"
    out = _call(system_prune_branches, older_than_days=14, user_approved=True)
    assert out["ok"] is True and out["deleted"] == []


# ── the capability guard ─────────────────────────────────────────────────────

def _seed_agents():
    raw = json.loads((Path(__file__).resolve().parents[1] / "bootstrap" / "agents.json")
                     .read_text(encoding="utf-8"))
    return {a["id"]: a for a in raw["agents"]}


def test_seeded_loop_agents_hold_no_forbidden_tool():
    from tools.capabilities import (
        CAN_EXFILTRATE, CAPABILITY_GRANTS, SYSTEM_WORKSPACE_FORBIDDEN_TOOLS,
        check_system_workspace_tools, grants_of,
    )
    exfiltrating = {t for t, g in CAPABILITY_GRANTS.items() if CAN_EXFILTRATE in g}
    assert {"git_publish", "fetch_url", "notify_user", "view_serve"} <= exfiltrating
    seeds = _seed_agents()
    for agent_id in sw.SYSTEM_LOOP_AGENTS:
        tools = seeds[agent_id]["tools"]
        assert not set(tools) & SYSTEM_WORKSPACE_FORBIDDEN_TOOLS, agent_id
        assert not set(tools) & exfiltrating, agent_id
        assert all(CAN_EXFILTRATE not in grants_of(t) for t in tools), agent_id
        assert check_system_workspace_tools(tools) is None
        assert seeds[agent_id]["system"] is True


def test_seeded_loop_agents_pass_the_whole_guard():
    from agents.capability_guard import enforce_agent_tools, enforce_built_tools
    seeds = _seed_agents()
    for agent_id in sw.SYSTEM_LOOP_AGENTS:
        enforce_agent_tools(agent_id, seeds[agent_id]["tools"])
        enforce_built_tools(agent_id, seeds[agent_id]["tools"])


def test_every_exfiltrating_grant_is_forbidden():
    from tools.capabilities import (
        CAN_EXFILTRATE, CAPABILITY_GRANTS, SYSTEM_WORKSPACE_FORBIDDEN_TOOLS,
        system_workspace_offenders,
    )
    for tool_id, grants in CAPABILITY_GRANTS.items():
        if CAN_EXFILTRATE in grants:
            assert system_workspace_offenders([tool_id]) == [tool_id]
    assert {"git_publish", "git_push", "run_shell"} <= SYSTEM_WORKSPACE_FORBIDDEN_TOOLS


def _spec(agent_id, tools, **extra):
    from agents.registry import _validate_agent_dict
    return _validate_agent_dict({
        "id": agent_id, "name": agent_id, "type": "langchain",
        "entrypoint": "agents.agent_factory:build_agent_executor", "tools": tools, **extra})


def test_add_agent_refuses_git_publish_on_a_loop_agent():
    from agents.capability_guard import CapabilityViolation
    from agents.registry import add_agent
    with pytest.raises(CapabilityViolation) as exc:
        add_agent(_spec("system_engineer", ["read_file", "git_publish"]))
    assert exc.value.violation.rule_id == "system_workspace_no_push"
    assert "git_publish" in exc.value.violation.sources["can_exfiltrate"]


def test_add_agent_refuses_a_push_on_an_agent_owned_by_the_system_workspace():
    from agents.capability_guard import CapabilityViolation
    from agents.registry import add_agent, get_agent
    add_agent(_spec("my_system_helper", ["read_file"], owner_workspace="system"))
    with pytest.raises(CapabilityViolation):
        add_agent(_spec("my_system_helper", ["read_file", "git_publish"], owner_workspace="system"))
    assert get_agent("my_system_helper").tools == ["read_file"]
    # The same tool on an agent of another workspace is the ordinary guard's call.
    add_agent(_spec("elsewhere_helper", ["read_file", "git_publish"], owner_workspace="default"))


def test_the_rule_ignores_override_and_guard_mode(monkeypatch):
    from agents.capability_guard import (
        CapabilityViolation, enforce_agent_tools, enforce_built_tools,
    )
    from common.config import settings
    monkeypatch.setattr(settings, "capability_guard", "off", raising=False)
    with pytest.raises(CapabilityViolation):
        enforce_agent_tools("system_doctor", ["run_diagnostics", "notify_user"], override=True)
    with pytest.raises(CapabilityViolation):
        enforce_built_tools("system_doctor", ["run_diagnostics", "run_shell"], override=True)
    # Delegation is a way out too.
    with pytest.raises(CapabilityViolation):
        enforce_built_tools("system_engineer", ["read_file", "run_agent_tool"])


def test_system_ops_tools_are_in_the_catalog_and_classified():
    from tools.capabilities import REVIEWED_NO_GRANT, CAPABILITY_GRANTS
    from tools.registry import get_tool_by_id
    from tools.system_ops import SYSTEM_OPS_TOOLS
    for t in SYSTEM_OPS_TOOLS:
        spec = get_tool_by_id(t.name)
        assert spec is not None and spec.category == "system_ops"
        assert t.name in REVIEWED_NO_GRANT or t.name in CAPABILITY_GRANTS
        assert not spec.can_exfiltrate


# ── the scheduled job kind ───────────────────────────────────────────────────

def _loop_job():
    from datetime import datetime, timezone
    from plans.models import JobKind, Recurrence, ScheduledJob
    from plans.service import plan_store
    job = ScheduledJob(kind=JobKind.loop, title="System maintenance loop", loop_id=sw.LOOP_ID,
                       run_at=datetime.now(timezone.utc), recurrence=Recurrence.cron,
                       cron="0 */6 * * *", workspace="system")
    return plan_store.add(job)


def test_loop_job_fires_through_the_launcher(monkeypatch):
    from common.bootstrap import seed_registry_from_bootstrap
    from loops.models import LoopRun
    import loops.launcher as launcher
    from plans.service import fire_job, get_job

    seed_registry_from_bootstrap()
    sw.ensure_system_workspace()
    calls = []

    def fake_start(loop_id, goal="", *, workspace=None, task_id=None, seed=None, parent_run_id=None):
        calls.append((loop_id, workspace))
        return LoopRun(loop_id=loop_id, workspace=workspace, task_id="task-1", status="pending")
    monkeypatch.setattr(launcher, "start_loop_run", fake_start)

    job = _loop_job()
    result = fire_job(job)
    assert result["ok"] is True, result
    assert calls == [(sw.LOOP_ID, "system")]
    assert result["loop_run_id"] and result["task_id"] == "task-1"
    stored = get_job(job.id)
    assert stored.fire_count == 1 and stored.created_task_ids == ["task-1"]
    assert stored.last_error is None


def test_loop_job_skips_while_a_run_is_active(monkeypatch):
    from common.bootstrap import seed_registry_from_bootstrap
    from loops import store as loop_store
    from loops.models import LoopRun
    import loops.launcher as launcher
    from plans.service import fire_job, get_job

    seed_registry_from_bootstrap()
    sw.ensure_system_workspace()
    loop_store.save_run(LoopRun(loop_id=sw.LOOP_ID, workspace="system", status="running"))
    monkeypatch.setattr(launcher, "start_loop_run",
                        lambda *a, **k: pytest.fail("must not launch while a run is active"))
    job = _loop_job()
    result = fire_job(job)
    assert result["ok"] is False and "still" in result["error"]
    assert "still" in (get_job(job.id).last_error or "")
