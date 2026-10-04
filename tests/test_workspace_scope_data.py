"""
An agent and every tool it calls read and change only the workspace its run
belongs to (common/workspace_scope.py): records named by id, listings and side
effects of the data tools (views, workspace files, the filesystem, projects and
their deployments and repos, database connections, chat channels, memory pools).

Two workspaces, ``acme`` (the run's) and ``globex``, each with its own record:
the other workspace's record is answered like a missing one, the run's own
works, and a service wide agent still reaches both.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

HOME, OTHER = "acme", "globex"


@pytest.fixture
def run_as(monkeypatch):
    """``run_as(agent_id)``: the context of a run of ``agent_id`` in ``acme``."""
    from agents.agent_loop import LoopState, reset_state, set_state
    tokens = []
    monkeypatch.setenv("AGENT_WORKSPACE", HOME)

    def _enter(agent_id: str = "swe_agent"):
        tokens.append(set_state(LoopState(run_id="run-scope", agent_id=agent_id, workspace=HOME)))

    yield _enter
    for token in reversed(tokens):
        reset_state(token)


def _call(tool, args=None):
    return json.loads(tool.invoke(args or {}))


# ── views ────────────────────────────────────────────────────────────────────

@pytest.fixture
def views():
    from views.store import create_view
    own = create_view("markdown", "Own", {"markdown": "# own"}, workspace=HOME)
    other = create_view("markdown", "Other", {"markdown": "# other"}, workspace=OTHER)
    return own.view_id, other.view_id


def test_view_tools_see_only_this_workspaces_views(run_as, views):
    from tools.views import view_apply_ops, view_get, view_snapshot, view_revert
    from views.store import get_view
    own, other = views
    run_as()

    assert _call(view_get, {"view_id": own})["ok"] is True
    out = _call(view_get, {"view_id": other})
    assert out["ok"] is False and "not found" in out["error"]

    ops = json.dumps([{"op": "update", "path": "title", "value": "Hijacked"}])
    assert _call(view_apply_ops, {"ops": ops, "view_id": other})["ok"] is False
    assert get_view(other)["title"] == "Other"
    assert _call(view_snapshot, {"name": "cp", "view_id": other})["ok"] is False
    assert _call(view_revert, {"seq": 0, "view_id": other})["ok"] is False

    assert _call(view_apply_ops, {"ops": ops, "view_id": own})["ok"] is True
    assert get_view(own)["title"] == "Hijacked"


def test_a_service_wide_agent_reaches_every_view(run_as, views):
    from tools.views import view_get
    _own, other = views
    run_as("service_agent")
    assert _call(view_get, {"view_id": other})["ok"] is True


def test_view_tools_without_a_workspace_keep_reaching_every_view(monkeypatch, views):
    from tools.views import view_get
    monkeypatch.setattr("common.workspace_scope.current_workspace", lambda: None)
    assert _call(view_get, {"view_id": views[1]})["ok"] is True


# ── workspace files ──────────────────────────────────────────────────────────

def test_workspace_file_of_another_workspace_is_not_found(run_as):
    from files import service
    from tools.workspace_files import list_workspace_files, read_workspace_file
    own = service.create_file(HOME, "own.md", b"own text", source="upload")
    other = service.create_file(OTHER, "other.md", b"secret text", source="upload")
    run_as()

    assert _call(read_workspace_file, {"file_id": own["file_id"]})["text"] == "own text"
    out = _call(read_workspace_file, {"file_id": other["file_id"]})
    assert out["ok"] is False and out["code"] == "not_found"
    listed = {f["file_id"] for f in _call(list_workspace_files)["files"]}
    assert own["file_id"] in listed and other["file_id"] not in listed

    run_as("service_agent")
    assert _call(read_workspace_file, {"file_id": other["file_id"]})["text"] == "secret text"


# ── the filesystem tools built without an operating path ────────────────────

def test_filesystem_tools_without_a_path_root_at_the_runs_workspace(run_as):
    from common.paths import WORKSPACES_ROOT
    from tools.filesystem_langchain import create_filesystem_tools
    for name, text in ((HOME, "own"), (OTHER, "other")):
        (WORKSPACES_ROOT / name).mkdir(parents=True, exist_ok=True)
        (WORKSPACES_ROOT / name / "note.txt").write_text(text)
    tools = {t.name: t for t in create_filesystem_tools(workspace=None)}
    run_as()

    assert _call(tools["read_file"], {"path": "note.txt"})["content"] == "own"
    out = _call(tools["read_file"], {"path": f"../{OTHER}/note.txt"})
    assert out["ok"] is False and "escapes" in out["error"]
    out = _call(tools["write_file"], {"path": str(WORKSPACES_ROOT / OTHER / "x.txt"), "content": "x"})
    assert out["ok"] is False and not (WORKSPACES_ROOT / OTHER / "x.txt").exists()

    run_as("service_agent")
    assert _call(tools["read_file"], {"path": f"{OTHER}/note.txt"})["content"] == "other"


# ── projects, deployments, repos, trackers ──────────────────────────────────

@pytest.fixture
def projects():
    from common.paths import PROJECTS_FILE, WORKSPACES_ROOT
    from projects.models import Project
    from projects.storage import ProjectStore
    store = ProjectStore(path=PROJECTS_FILE)
    out = {}
    for ws in (HOME, OTHER):
        (WORKSPACES_ROOT / ws).mkdir(parents=True, exist_ok=True)
        p = Project(name=f"site-{ws}", workspace=ws)
        store.add(p)
        out[ws] = p
    return out


def test_project_tools_see_only_this_workspaces_projects(run_as, projects):
    from tools.project_management import (
        delete_project_tool, get_project_tool, list_projects_tool, modify_project_tool,
    )
    own, other = projects[HOME], projects[OTHER]
    run_as()

    assert _call(get_project_tool, {"project_id": own.id})["ok"] is True
    for tool, args in ((get_project_tool, {}), (modify_project_tool, {"description": "x"}),
                       (delete_project_tool, {})):
        out = _call(tool, {"project_id": other.id, **args})
        assert out["ok"] is False and out["code"] == "not_found", tool.name
    from tools.project_management import _store
    assert _store().get(other.id) is not None and _store().get(other.id).description != "x"

    listed = {p["project_id"] for p in _call(list_projects_tool)["projects"]}
    assert listed == {own.id}
    out = _call(list_projects_tool, {"workspace": OTHER})
    assert out["ok"] is False

    run_as("service_agent")
    assert _call(get_project_tool, {"project_id": other.id})["ok"] is True
    assert {p["project_id"] for p in _call(list_projects_tool, {"workspace": OTHER})["projects"]} == {other.id}


def test_create_project_in_another_workspace_is_refused(run_as, projects):
    from tools.project_management import create_project_tool, _store
    run_as()
    out = _call(create_project_tool, {"name": "sneaky", "workspace": OTHER})
    assert out["ok"] is False
    assert not [p for p in _store().list() if p.name == "sneaky"]


def test_deploy_and_publish_tools_do_not_find_another_workspaces_project(run_as, projects, monkeypatch):
    from tools import project_deploy, git_publish, trackers
    other = projects[OTHER]
    called = []
    monkeypatch.setattr(git_publish, "run_git_publish", lambda *a, **k: called.append(a) or {"ok": True})
    run_as()

    for ref in (other.id, other.name):
        out = _call(project_deploy.project_deployment_status, {"project": ref})
        assert out["ok"] is False and out["code"] == "not_found"
        out = _call(project_deploy.stop_project_deployment, {"project": ref})
        assert out["ok"] is False and out["code"] == "not_found"
        out = _call(git_publish.git_publish, {"project": ref, "title": "t"})
        assert out["ok"] is False and out["code"] == "not_found"
        out = _call(trackers.tracker_sync, {"project": ref})
        assert out["ok"] is False and "No project" in out["error"]
    assert called == []

    # The run's own project is found by the same tools, by id and by name.
    assert project_deploy._resolve_project(projects[HOME].name).id == projects[HOME].id
    assert _call(git_publish.git_publish, {"project": projects[HOME].id, "title": "t"})["ok"] is True
    assert called == [(projects[HOME].id,)]

    run_as("service_agent")
    assert project_deploy._resolve_project(other.id).id == other.id
    assert trackers._resolve_project(other.name).id == other.id


def test_a_same_named_project_elsewhere_does_not_make_a_name_ambiguous(run_as, projects):
    from common.paths import PROJECTS_FILE
    from projects.models import Project
    from projects.storage import ProjectStore
    ProjectStore(path=PROJECTS_FILE).add(Project(name=f"site-{HOME}", workspace=OTHER))
    from tools.project_deploy import _resolve_project
    run_as()
    assert _resolve_project(f"site-{HOME}").id == projects[HOME].id


def test_project_graph_of_another_workspaces_project_is_not_found(run_as, projects):
    from common.workspace_context import _project_ctx
    from tools.project_graph import get_project_graph
    run_as()
    token = _project_ctx.set(projects[OTHER].id)
    try:
        out = _call(get_project_graph, {"view": "architecture"})
    finally:
        _project_ctx.reset(token)
    assert out["ok"] is False and "not found" in out["error"]


# ── database connections ─────────────────────────────────────────────────────

def test_database_connection_of_another_workspace_is_not_found(run_as, tmp_path):
    import sqlite3
    from connectors.databases import store
    from tools.databases import db_list_connections, db_query
    paths = {}
    for ws in (HOME, OTHER):
        paths[ws] = tmp_path / f"{ws}.db"
        with sqlite3.connect(paths[ws]) as conn:
            conn.execute("CREATE TABLE t (v TEXT)")
            conn.execute("INSERT INTO t VALUES (?)", (ws,))
    own = store.create_connection(workspace=HOME, name="db", kind="sqlite", dsn=str(paths[HOME]))
    other = store.create_connection(workspace=OTHER, name="db2", kind="sqlite", dsn=str(paths[OTHER]))
    run_as()

    assert _call(db_query, {"connection": own["id"], "sql": "SELECT v FROM t"})["rows"] == [[HOME]]
    for ref in (other["id"], "db2"):
        out = _call(db_query, {"connection": ref, "sql": "SELECT v FROM t"})
        assert out["ok"] is False and "no database connection" in out["error"]
    assert [c["id"] for c in _call(db_list_connections)["connections"]] == [own["id"]]

    run_as("service_agent")
    assert _call(db_query, {"connection": other["id"], "sql": "SELECT v FROM t"})["rows"] == [[OTHER]]


# ── chat channels ────────────────────────────────────────────────────────────

@pytest.fixture
def channel(monkeypatch):
    from connectors.channels.registry import ChannelSpec, ConfigField
    from connectors.channels.service import ChannelService
    from connectors.channels.store import ChannelStore
    from connectors.channels import registry

    class FakeService(ChannelService):
        name = "fake"
        required_fields = ("token",)

        def __init__(self, store):
            super().__init__(store)
            self.sent = []

        async def _connect(self):
            pass

        async def _run(self):
            await self._stop_event.wait()

        async def send_text(self, chat_key, text, **kwargs):
            self.sent.append((chat_key, text))

    store = ChannelStore("fake", secret_fields=("token",), defaults={"token": ""})
    spec = ChannelSpec(name="fake", store=store, service=FakeService(store),
                       fields=[ConfigField("token", secret=True, required=True)])
    monkeypatch.setattr(registry, "_LOADED", True)
    monkeypatch.setattr(registry, "_CHANNELS", {"fake": spec})
    store.set_config({"token": "t"})
    store.set_enabled(True)
    store.upsert_binding(chat_key="C-own", agent_id="a", workspace=HOME)
    store.upsert_binding(chat_key="C-other", agent_id="a", workspace=OTHER)
    store.upsert_binding(chat_key="C-shared", agent_id="a")
    return spec


def test_channel_send_to_a_chat_bound_to_another_workspace_is_refused(run_as, channel):
    from tools.channel_send import channel_send
    run_as()
    out = _call(channel_send, {"channel": "fake", "chat_key": "C-other", "text": "leak"})
    assert out["ok"] is False and "not bound" in out["error"]
    assert channel.service.sent == []
    assert _call(channel_send, {"channel": "fake", "chat_key": "C-own", "text": "hi"})["sent"] == 1
    assert _call(channel_send, {"channel": "fake", "chat_key": "C-shared", "text": "hi"})["sent"] == 1
    # The workspace broadcast reaches the run's chats and the shared one only.
    assert _call(channel_send, {"channel": "fake", "text": "all"})["sent"] == 2
    assert "C-other" not in {k for k, _ in channel.service.sent}

    run_as("service_agent")
    assert _call(channel_send, {"channel": "fake", "chat_key": "C-other", "text": "ops"})["sent"] == 1


def test_telegram_send_to_a_chat_of_another_workspace_is_refused(run_as, monkeypatch):
    from connectors.telegram import telegram_store
    import connectors.telegram.notify as tg_notify
    from tools.channel_send import channel_send
    sent = []
    monkeypatch.setattr(tg_notify, "_send_text", lambda token, cid, text: sent.append(cid) or True)
    telegram_store.set_token("tok")
    telegram_store.upsert_binding(chat_id=111, agent_id="a", workspace=HOME)
    telegram_store.upsert_binding(chat_id=222, agent_id="a", workspace=OTHER)
    run_as()

    for key in ("222", "333"):  # another workspace's chat, a chat nobody bound
        out = _call(channel_send, {"channel": "telegram", "chat_key": key, "text": "leak"})
        assert out["ok"] is False and "not bound" in out["error"]
    assert _call(channel_send, {"channel": "telegram", "chat_key": "111", "text": "hi"})["sent"] == 1
    assert sent == [111]

    run_as("service_agent")
    assert _call(channel_send, {"channel": "telegram", "chat_key": "222", "text": "ops"})["sent"] == 1


# ── memory pools named by id ─────────────────────────────────────────────────

@pytest.fixture
def pools(monkeypatch):
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    store = MemoryStore()
    bound = SharedMemory(name="bound", workspace=HOME, notes=[{"title": "n", "content": "bound"}])
    unbound = SharedMemory(name="unbound", workspace=HOME, notes=[{"title": "n", "content": "unbound"}])
    other = SharedMemory(name="other", workspace=OTHER, notes=[{"title": "n", "content": "other"}])
    for m in (bound, unbound, other):
        store.add(m)
    spec = SimpleNamespace(id="swe_agent", owner_workspace=HOME, memory_type="shared",
                           memory_data=str(bound.id))
    monkeypatch.setattr("agents.registry.get_agent",
                        lambda agent_id: spec if agent_id == "swe_agent" else None)
    monkeypatch.setattr("memory.personal.enabled", lambda spec, workspace=None: False)
    return str(bound.id), str(unbound.id), str(other.id)


def test_memory_tools_reach_only_the_pools_the_run_is_bound_to(run_as, pools):
    from memory.tool import read_memory_tool, write_memory_tool, search_memory_tool
    from memory.store import MemoryStore
    bound, unbound, other = pools
    run_as()

    assert _call(read_memory_tool, {"memory_id": bound, "note_title": "n"})["content"] == "bound"
    for pid in (unbound, other):
        out = _call(read_memory_tool, {"memory_id": pid, "note_title": "n"})
        assert out["ok"] is False and "not found" in out["error"]
        out = _call(write_memory_tool, {"memory_id": pid, "note_title": "n", "note_content": "x"})
        assert out["ok"] is False
        out = _call(search_memory_tool, {"memory_id": pid, "query": "other"})
        assert out["ok"] is False
    assert MemoryStore().get(other).notes[0]["content"] == "other"

    run_as("service_agent")
    assert _call(read_memory_tool, {"memory_id": other, "note_title": "n"})["content"] == "other"


def test_memory_tools_without_a_workspace_keep_reaching_every_pool(monkeypatch, pools):
    from memory.tool import read_memory_tool
    monkeypatch.setattr("common.workspace_scope.current_workspace", lambda: None)
    assert _call(read_memory_tool, {"memory_id": pools[2], "note_title": "n"})["content"] == "other"
