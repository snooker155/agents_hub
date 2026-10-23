"""The planner chat keeps its history (routes/projects/planner.py): it lives in
the entity chat store, "New chat" files the thread under sessions, and a chat
saved in the project graph store before the move is carried over on read."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from routes import projects as project_routes
    from routes import workspaces, entity_chats
    from projects.storage import ProjectStore
    monkeypatch.setattr(project_routes, "_store", ProjectStore(path=tmp_path / "projects.json"))
    app = FastAPI()
    for r in (workspaces.router, project_routes.router, entity_chats.router):
        app.include_router(r)
    c = TestClient(app)
    assert c.post("/api/workspaces", json={"name": "plan-ws"}).status_code == 200
    return c


def _project(client):
    resp = client.post("/api/projects", json={"name": "plan-proj", "workspace": "plan-ws"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_an_old_chat_moves_over_and_new_chat_files_it_in_history(client):
    from routes.projects._common import _graph_store
    pid = _project(client)
    _graph_store.append_message(pid, "tasks", "user", "Generate tasks")
    _graph_store.append_message(pid, "tasks", "assistant", "Made 4 tasks.")

    chat = client.get(f"/api/projects/{pid}/tasks/chat").json()
    assert [m["content"] for m in chat["messages"]] == ["Generate tasks", "Made 4 tasks."]
    assert chat["chat_ref"] == {"kind": "projtasks", "id": pid}
    assert _graph_store.get_messages(pid, "tasks") == []

    assert client.delete(f"/api/projects/{pid}/tasks/chat").status_code == 200
    assert client.get(f"/api/projects/{pid}/tasks/chat").json()["messages"] == []
    history = client.get("/api/entity-chats/sessions", params={"kind": "projtasks", "entity_id": pid}).json()
    past = [s for s in history["sessions"] if not s.get("active")]
    assert len(past) == 1 and past[0]["messages"] == 2

    restored = client.post("/api/entity-chats/sessions/activate",
                           json={"kind": "projtasks", "entity_id": pid, "session_id": past[0]["id"]})
    assert restored.status_code == 200, restored.text
    assert len(client.get(f"/api/projects/{pid}/tasks/chat").json()["messages"]) == 2


def test_architect_chat_moves_over_keeps_its_epoch_and_new_chat_files_it(client):
    from routes.projects._common import _graph_store
    pid = _project(client)
    _graph_store.save(pid, "architecture", [{"id": "a"}], [], source="manual")
    _graph_store.append_message(pid, "architecture", "user", "Draw it")
    _graph_store.append_message(pid, "architecture", "assistant", "Drawn.")
    _graph_store.append_trace(pid, "architecture", [{"k": "user", "text": "Draw it"}])
    with _graph_store.docs.transaction():
        doc = _graph_store.get(pid, "architecture")
        doc["session_epoch"] = 2
        _graph_store.docs.put(f"{pid}:architecture", doc)

    chat = client.get(f"/api/projects/{pid}/graph/messages", params={"view": "architecture"}).json()
    assert chat["chat_ref"] == {"kind": "project_graph", "id": f"{pid}:architecture"}
    assert [m["content"] for m in chat["messages"]] == ["Draw it", "Drawn."]
    assert chat["trace"] == [{"k": "user", "text": "Draw it"}]
    from common.entity_chat_store import entity_chat_store
    assert entity_chat_store().get_session_epoch("project_graph", f"{pid}:architecture") == 2
    doc = _graph_store.get(pid, "architecture")
    assert not doc.get("messages") and doc["nodes"] == [{"id": "a"}]

    assert client.delete(f"/api/projects/{pid}/graph/messages", params={"view": "architecture"}).status_code == 200
    assert client.get(f"/api/projects/{pid}/graph/messages", params={"view": "architecture"}).json()["messages"] == []
    assert _graph_store.get(pid, "architecture")["nodes"] == [{"id": "a"}]
    history = client.get("/api/entity-chats/sessions",
                         params={"kind": "project_graph", "entity_id": f"{pid}:architecture"}).json()
    assert [s["messages"] for s in history["sessions"] if not s["active"]] == [2]


def test_activate_is_refused_while_an_architect_turn_runs(client):
    import asyncio
    from routes.projects import graph as g
    pid = _project(client)
    eid = f"{pid}:process"

    async def go():
        task = asyncio.ensure_future(asyncio.sleep(5))
        g._ACTIVE_GRAPH_RUNS[eid] = task
        try:
            from chat.entity_chat import entity_run_active
            assert entity_run_active("project_graph", eid)
        finally:
            task.cancel()
            g._ACTIVE_GRAPH_RUNS.pop(eid, None)
        assert not entity_run_active("project_graph", eid)

    asyncio.run(go())
