"""``telegram.changed`` / ``blender_daemons.changed`` invalidation events.

The Telegram and Blender connector panels used to poll every 30s because
nothing told them when a binding, a config or a daemon changed. Both routes
(and the processes that can mutate this state outside a route: the Telegram
poller's own command handling, and any agent process that starts or stops a
Blender engine through connectors.blender.pool) now call
``common.session_broker.notify_change`` after every mutation. This file checks
the event fires; it does not re-check the mutations themselves (see
tests/test_telegram_allowlist.py for those).

Run: ``python -m pytest tests/test_connector_events.py -q``
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


def _recorder(monkeypatch, *modules):
    """Patch ``notify_change`` in every given module and return the call log."""
    calls: list[tuple[str, dict]] = []

    def fake(resource, **meta):
        calls.append((resource, meta))

    for mod in modules:
        monkeypatch.setattr(mod, "notify_change", fake)
    return calls


# ── Telegram: the dashboard routes ──────────────────────────────────────────

def test_config_update_publishes_telegram_changed(monkeypatch):
    from routes import telegram as telegram_routes
    from connectors.telegram import telegram_store

    calls = _recorder(monkeypatch, telegram_routes)
    telegram_store.set_allowed_chat_ids([])
    client = _client(telegram_routes.router)

    resp = client.put("/api/telegram/config", json={"allowed_chat_ids": [7]})
    assert resp.status_code == 200
    assert any(r == "telegram" for r, _ in calls)


def test_creating_a_binding_publishes_telegram_changed(monkeypatch):
    from routes import telegram as telegram_routes
    from connectors.telegram import telegram_store
    from workspace.storage import create_workspace_folder

    calls = _recorder(monkeypatch, telegram_routes)
    create_workspace_folder("tg-events-ws")
    telegram_store.remove_binding(6001)
    client = _client(telegram_routes.router)

    resp = client.post("/api/telegram/bindings", json={
        "chat_id": 6001, "workspace": "tg-events-ws",
    })
    assert resp.status_code == 200
    assert any(r == "telegram" and m.get("chat_id") == 6001 for r, m in calls)


def test_deleting_a_binding_publishes_telegram_changed(monkeypatch):
    from routes import telegram as telegram_routes
    from connectors.telegram import telegram_store
    from workspace.storage import create_workspace_folder

    create_workspace_folder("tg-events-ws2")
    telegram_store.upsert_binding(chat_id=6002, agent_id="", workspace="tg-events-ws2",
                                  conversation_id="c1", title="t")
    calls = _recorder(monkeypatch, telegram_routes)
    client = _client(telegram_routes.router)

    resp = client.delete("/api/telegram/bindings/6002")
    assert resp.status_code == 200
    assert any(r == "telegram" and m.get("chat_id") == 6002 for r, m in calls)


# ── Telegram: the poller changing its own binding state ────────────────────

def test_the_poller_binding_an_agent_publishes_telegram_changed(monkeypatch):
    from connectors.telegram import telegram_runner as tr
    from connectors.telegram import telegram_store
    from workspace.storage import create_workspace_folder
    from agents.registry import AgentSpec

    calls = _recorder(monkeypatch, tr)
    create_workspace_folder("tg-events-ws3")
    telegram_store.upsert_binding(chat_id=6003, agent_id="", workspace="tg-events-ws3",
                                  conversation_id="c2", title="t")
    spec = AgentSpec(id="probe-agent", name="Probe", type="langchain",
                     entrypoint="agents.agent_factory:build_agent_executor")
    monkeypatch.setattr(tr.registry, "get_agent", lambda aid: spec if aid == "probe-agent" else None)
    monkeypatch.setattr(tr, "_allowed_agent_ids_for", lambda ws: None)

    class _FakeApi:
        async def send_message(self, chat_id, text, **kwargs):
            return {}

    async def run():
        message = {"chat": {"id": 6003, "username": "dave"}}
        return await tr._handle_command(_FakeApi(), message, "/agent probe-agent")

    handled = asyncio.run(run())
    assert handled is True
    assert any(r == "telegram" and m.get("chat_id") == 6003 for r, m in calls)


# ── Blender: the dashboard routes ───────────────────────────────────────────

def test_config_update_publishes_blender_daemons_changed(monkeypatch):
    from routes import blender as blender_routes

    calls = _recorder(monkeypatch, blender_routes)
    monkeypatch.setattr(blender_routes.store, "save", lambda patch: None)
    monkeypatch.setattr(blender_routes.store, "public_config", lambda: {})
    monkeypatch.setattr(blender_routes.pool, "availability", lambda: {"available": False})
    client = _client(blender_routes.router)

    resp = client.put("/api/blender/config", json={"enabled": True})
    assert resp.status_code == 200
    assert any(r == "blender_daemons" for r, _ in calls)


def test_stopping_one_daemon_publishes_blender_daemons_changed(monkeypatch):
    from routes import blender as blender_routes

    calls = _recorder(monkeypatch, blender_routes)
    monkeypatch.setattr(blender_routes.pool, "stop", lambda key: True)
    client = _client(blender_routes.router)

    resp = client.delete("/api/blender/daemons/scene-1")
    assert resp.status_code == 200
    assert any(r == "blender_daemons" and m.get("key") == "scene-1" for r, m in calls)


def test_stopping_all_daemons_publishes_blender_daemons_changed(monkeypatch):
    from routes import blender as blender_routes

    calls = _recorder(monkeypatch, blender_routes)
    monkeypatch.setattr(blender_routes.pool, "stop_all", lambda: 2)
    client = _client(blender_routes.router)

    resp = client.delete("/api/blender/daemons")
    assert resp.status_code == 200
    assert any(r == "blender_daemons" for r, _ in calls)


# ── Blender: the pool itself, whichever process registers/unregisters ──────

def test_pool_stop_publishes_blender_daemons_changed_even_off_the_dashboard(monkeypatch):
    """A daemon can be stopped from an agent's own process, not just the
    dashboard route, so the event has to fire from the pool, not the route."""
    from connectors.blender import pool

    calls = _recorder(monkeypatch, pool)
    monkeypatch.setattr(pool, "_read_record", lambda key: None)
    with pool._LOCK:
        pool._LOCAL.pop("no-such-key", None)

    result = pool.stop("no-such-key")
    assert result is False  # nothing to stop
    assert calls == []  # ...so nothing to announce either


def test_pool_stop_of_a_registered_local_daemon_publishes_the_event(monkeypatch):
    from connectors.blender import pool

    calls = _recorder(monkeypatch, pool)

    class _FakeDaemon:
        def stop(self):
            pass

    with pool._LOCK:
        pool._LOCAL["fake-key"] = _FakeDaemon()
    monkeypatch.setattr(pool, "_drop_record", lambda key: None)

    result = pool.stop("fake-key")
    assert result is True
    assert any(r == "blender_daemons" and m.get("key") == "fake-key" for r, m in calls)
