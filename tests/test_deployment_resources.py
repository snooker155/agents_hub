"""A deployment's resources (docs/deployments.md, "Resources"): the project,
files, extra secret names and memory pools on a scheduled agent_task job are
validated when the job is saved, copied onto the task it creates, and reach
that task's runs only: the extra secrets through the run environment or the
in-process secret scope, the pools as a build override."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from plans import service as ps
from plans.models import JobKind, ScheduledJob
from plans.storage import FireStore, PlanStore

UTC = timezone.utc


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = PlanStore()
    monkeypatch.setattr(ps, "plan_store", s)
    monkeypatch.setattr(ps, "fire_store", FireStore())
    monkeypatch.setattr(ps, "create_notification", lambda **kw: SimpleNamespace(id="n"))
    return s


@pytest.fixture
def resource_stores(monkeypatch):
    """A project, a file and a pool that exist in workspace ``dev``."""
    monkeypatch.setattr("projects.storage.ProjectStore.get",
                        lambda self, pid: SimpleNamespace(id=pid, workspace="dev") if pid == "proj-1" else None)
    monkeypatch.setattr("files.service.get_files",
                        lambda ids: [{"id": i, "workspace": "dev"} for i in ids if i == "file_1"])
    monkeypatch.setattr("memory.store.MemoryStore.load",
                        lambda self: [SimpleNamespace(id="pool-1", workspace="dev"), SimpleNamespace(id="pool-g", workspace=None)])


def _job(**kw):
    defaults = dict(kind=JobKind.agent_task, title="t", run_at=datetime.now(UTC), workspace="dev")
    defaults.update(kw)
    return defaults


def test_resources_are_validated_and_stored(store, resource_stores):
    job = ps.create_job(**_job(project_id="proj-1", file_ids=["file_1", "file_1"], secrets=["JIRA_TOKEN", "JIRA_TOKEN"],
                               memory_pool_ids=["pool-1", "pool-g"]))
    assert job.project_id == "proj-1"
    assert job.file_ids == ["file_1"]
    assert job.secrets == ["JIRA_TOKEN"]
    assert job.memory_pool_ids == ["pool-1", "pool-g"]


@pytest.mark.parametrize("bad", [
    dict(project_id="proj-x"), dict(file_ids=["file_x"]), dict(memory_pool_ids=["pool-x"]), dict(secrets=["bad name"]),
])
def test_an_unknown_resource_is_refused(store, resource_stores, bad):
    with pytest.raises(ValueError):
        ps.create_job(**_job(**bad))


def test_resources_belong_to_agent_task_jobs_only(store, resource_stores):
    with pytest.raises(ValueError, match="agent task"):
        ps.create_job(**_job(kind=JobKind.flow, flow_id="f1", file_ids=["file_1"]))
    # An empty resource set on a flow job is fine.
    ps.create_job(**_job(kind=JobKind.flow, flow_id="f1", file_ids=[]))


def test_update_revalidates_the_merged_resources(store, resource_stores):
    job = ps.create_job(**_job(file_ids=["file_1"]))
    with pytest.raises(ValueError):
        ps.update_job(job.id, project_id="proj-x")
    updated = ps.update_job(job.id, project_id="proj-1")
    assert updated.project_id == "proj-1" and updated.file_ids == ["file_1"]


def test_extra_secrets_go_through_the_capability_guard(store, resource_stores, monkeypatch):
    from agents.registry import AgentSpec
    fetcher = AgentSpec(id="fetcher", name="f", description="", type="langchain",
                        entrypoint="agents.agent_launcher:run", tools=["fetch_url"])
    monkeypatch.setattr("agents.registry.get_agent", lambda aid: fetcher if aid == "fetcher" else None)
    # fetch_url ingests and exfiltrates; a secret adds the private read: the trifecta.
    with pytest.raises(ValueError, match="capability guard"):
        ps.create_job(**_job(agent_id="fetcher", secrets=["JIRA_TOKEN"]))


def test_firing_copies_the_resources_onto_the_task(store, resource_stores, monkeypatch):
    captured = {}

    def _fake_create_task(**kw):
        captured.update(kw)
        return SimpleNamespace(id="11111111-1111-1111-1111-111111111111")

    monkeypatch.setattr("tasks.service.create_task", _fake_create_task)
    monkeypatch.setattr("tasks.service.append_task_activity_log", lambda *a, **k: None)
    monkeypatch.setattr("tasks.service.update_task", lambda *a, **k: None)
    job = store.add(ScheduledJob(**_job(run_at=datetime.now(UTC) - timedelta(seconds=5), project_id="proj-1",
                                        file_ids=["file_1"], secrets=["JIRA_TOKEN"], memory_pool_ids=["pool-1"])))
    ps.fire_job(job)
    assert captured["project_id"] == "proj-1"
    assert captured["file_ids"] == ["file_1"]
    assert captured["secrets"] == ["JIRA_TOKEN"]
    assert captured["memory_pool_ids"] == ["pool-1"]


def test_task_keeps_the_resources():
    from tasks.service import create_task, get_task
    t = create_task("t", workspace="dev", secrets=["JIRA_TOKEN"], memory_pool_ids=["pool-1"])
    again = get_task(t.id)
    assert again.secrets == ["JIRA_TOKEN"] and again.memory_pool_ids == ["pool-1"]


def test_extra_names_widen_one_run_not_the_agent(monkeypatch):
    from common import secrets
    monkeypatch.setattr(secrets, "allowed_for_agent", lambda aid: ["OWN"])
    seen = {}

    def _resolve(workspace, agent_id, user_id, allowed):
        seen["allowed"] = list(allowed)
        return {n: f"v-{n}" for n in allowed}

    monkeypatch.setattr(secrets, "resolve_for_run", _resolve)
    env = secrets.env_for_run("dev", "a", None, extra_names=["JIRA_TOKEN", "OWN", " "])
    assert env == {"OWN": "v-OWN", "JIRA_TOKEN": "v-JIRA_TOKEN"}
    assert seen["allowed"] == ["OWN", "JIRA_TOKEN"]
    # Without extras the agent's own list is all there is.
    assert secrets.env_for_run("dev", "a", None) == {"OWN": "v-OWN"}
    # The in-process scope honours the same extras, and only inside the block.
    with secrets.activate("dev", "a", None, extra_names=["JIRA_TOKEN"]):
        assert secrets.get("JIRA_TOKEN") == "v-JIRA_TOKEN"
        assert secrets.get("OTHER") is None
        assert secrets.active_scope() == ("dev", "a", "")
    with secrets.activate("dev", "a", None):
        assert secrets.get("JIRA_TOKEN") is None


def test_memory_access_is_validated_and_defaults_to_read(store, resource_stores):
    job = ps.create_job(**_job(memory_pool_ids=["pool-1"]))
    assert job.memory_access == "read"
    job = ps.create_job(**_job(memory_pool_ids=["pool-1"], memory_access="WRITE"))
    assert job.memory_access == "write"
    with pytest.raises(ValueError, match="memory_access"):
        ps.create_job(**_job(memory_pool_ids=["pool-1"], memory_access="append"))


def test_firing_copies_the_access_mode_only_with_pools(store, resource_stores, monkeypatch):
    captured = []
    monkeypatch.setattr("tasks.service.create_task", lambda **kw: (captured.append(kw), SimpleNamespace(id="11111111-1111-1111-1111-111111111111"))[1])
    monkeypatch.setattr("tasks.service.append_task_activity_log", lambda *a, **k: None)
    monkeypatch.setattr("tasks.service.update_task", lambda *a, **k: None)
    past = datetime.now(UTC) - timedelta(seconds=5)
    ps.fire_job(store.add(ScheduledJob(**_job(run_at=past, memory_pool_ids=["pool-1"], memory_access="read"))))
    ps.fire_job(store.add(ScheduledJob(**_job(run_at=past))))
    assert captured[0]["memory_access"] == "read"
    # No pools of its own: the task keeps the agent's binding, writable as before.
    assert captured[1]["memory_access"] == "write"


def test_read_only_memory_drops_the_write_tools_at_build(monkeypatch):
    """The factory pops ``memory_access`` and, for "read", leaves the memory
    write tools out of the built set; checked on the tool names it hands the
    capability guard, the last stop before the agent gets its runtime."""
    from memory.binding import MEMORY_WRITE_TOOLS
    assert {"remember", "forget", "record_episode", "link", "memory_block_replace"} <= MEMORY_WRITE_TOOLS
    assert "recall" not in MEMORY_WRITE_TOOLS and "memory_block_read" not in MEMORY_WRITE_TOOLS
