"""Telegram chat allowlist and the removal of self-service workspace binding.

Before this, any Telegram user who found the bot could talk to it, and could
bind their own chat to a workspace with `/workspace <name>` — no operator
involved at all. Two independent changes close that:

- `telegram_store.allowed_chat_ids` gates every inbound update
  (`TelegramService._chat_allowed`, checked in `_dispatch_update` before either
  a message or a callback_query is handled). Empty list = reject everyone once
  a token is configured.
- A binding's `workspace` field can only be set through
  POST /api/telegram/bindings (the dashboard). The inbound `/workspace`
  command is now read-only: it never calls `telegram_store.upsert_binding`,
  and instead tells the user to ask the operator.

The suite has no pytest-asyncio; async bodies are driven with asyncio.run,
matching the pattern already used in tests/test_chat_broadcast.py.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path


BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


class _FakeApi:
    """Stand-in for TelegramAPI: records what would have been sent."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return {}

    async def send_chat_action(self, chat_id, action="typing"):
        return None


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ── the allowlist itself ─────────────────────────────────────────────────────


def test_empty_allowlist_rejects_every_chat():
    from connectors.telegram import telegram_store

    telegram_store.set_allowed_chat_ids([])
    assert telegram_store.is_chat_allowed(123) is False


def test_an_allowlisted_chat_id_is_allowed():
    from connectors.telegram import telegram_store

    telegram_store.set_allowed_chat_ids([111, 222])
    assert telegram_store.is_chat_allowed(111) is True
    assert telegram_store.is_chat_allowed(333) is False


def test_config_route_round_trips_the_allowlist():
    from routes import telegram as telegram_routes
    from connectors.telegram import telegram_store

    telegram_store.set_allowed_chat_ids([])
    client = _client(telegram_routes.router)

    got = client.get("/api/telegram/config").json()
    assert got["allowed_chat_ids"] == []

    put = client.put("/api/telegram/config", json={"allowed_chat_ids": [42, 43]})
    assert put.status_code == 200
    assert sorted(put.json()["allowed_chat_ids"]) == [42, 43]
    assert telegram_store.get_allowed_chat_ids() == [42, 43]


# ── the poller drops updates from chats not on the allowlist ───────────────


async def _async_allowed_chat_is_dispatched():
    from connectors.telegram import telegram_runner as tr
    from connectors.telegram import telegram_store

    telegram_store.set_allowed_chat_ids([900])
    service = tr.TelegramService()
    calls = []

    async def fake_handle_messages(api, chat_id, messages):
        calls.append(chat_id)

    service._handle_messages = fake_handle_messages
    update = {"message": {"chat": {"id": 900}, "text": "hello"}}

    await service._dispatch_update(_FakeApi(), update)
    return calls


def test_an_allowlisted_chat_is_processed():
    calls = asyncio.run(_async_allowed_chat_is_dispatched())
    assert calls == [900]


def test_an_unlisted_chat_is_dropped_and_logged_once(caplog):
    from connectors.telegram import telegram_runner as tr
    from connectors.telegram import telegram_store

    async def run():
        telegram_store.set_allowed_chat_ids([])  # reject everyone
        service = tr.TelegramService()
        calls = []

        async def fake_handle_messages(api, chat_id, messages):
            calls.append(chat_id)

        service._handle_messages = fake_handle_messages
        update = {"message": {"chat": {"id": 777}, "text": "hi there"}}

        with caplog.at_level(logging.INFO, logger="telegram"):
            await service._dispatch_update(_FakeApi(), update)
            await service._dispatch_update(_FakeApi(), update)  # a second message, same chat

        return calls

    calls = asyncio.run(run())
    assert calls == []  # never reached the handler
    drop_lines = [r for r in caplog.records if "not on the allowlist" in r.message]
    assert len(drop_lines) == 1  # logged once per chat id, not once per message


def test_a_callback_query_from_an_unlisted_chat_is_also_dropped():
    from connectors.telegram import telegram_runner as tr
    from connectors.telegram import telegram_store

    async def run():
        telegram_store.set_allowed_chat_ids([1])
        service = tr.TelegramService()
        calls = []

        async def fake_handle_callback(api, callback_query):
            calls.append(callback_query)

        service._handle_callback_query = fake_handle_callback
        update = {"callback_query": {"id": "cq1", "data": "x",
                                      "message": {"chat": {"id": 999}}}}
        await service._dispatch_update(_FakeApi(), update)
        return calls

    assert asyncio.run(run()) == []


# ── binding creation: dashboard only, never an inbound command ─────────────


def test_workspace_command_no_longer_creates_a_binding():
    from connectors.telegram import telegram_runner as tr
    from connectors.telegram import telegram_store

    async def run():
        api = _FakeApi()
        message = {"chat": {"id": 5001, "username": "bob"}}
        handled = await tr._handle_command(api, message, "/workspace default")
        return api, handled

    api, handled = asyncio.run(run())
    assert handled is True
    assert telegram_store.get_binding(5001) is None
    assert any("operator" in text.lower() for _, text in api.sent)


def test_start_command_does_not_create_a_binding_either():
    from connectors.telegram import telegram_runner as tr
    from connectors.telegram import telegram_store

    async def run():
        api = _FakeApi()
        message = {"chat": {"id": 5002, "username": "carol"}}
        handled = await tr._handle_command(api, message, "/start")
        return api, handled

    api, handled = asyncio.run(run())
    assert handled is True
    assert telegram_store.get_binding(5002) is None
    assert any("operator" in text.lower() for _, text in api.sent)


def test_dashboard_route_is_the_only_way_to_create_a_binding():
    from routes import telegram as telegram_routes
    from connectors.telegram import telegram_store
    from workspace.storage import create_workspace_folder

    create_workspace_folder("tg-allowlist-ws")
    telegram_store.remove_binding(5003)

    client = _client(telegram_routes.router)
    resp = client.post("/api/telegram/bindings", json={
        "chat_id": 5003, "workspace": "tg-allowlist-ws",
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chat_id"] == 5003
    assert body["workspace"] == "tg-allowlist-ws"

    stored = telegram_store.get_binding(5003)
    assert stored is not None
    assert stored["workspace"] == "tg-allowlist-ws"


def test_dashboard_route_rejects_an_unknown_workspace():
    from routes import telegram as telegram_routes

    client = _client(telegram_routes.router)
    resp = client.post("/api/telegram/bindings", json={
        "chat_id": 5004, "workspace": "no-such-workspace-anywhere",
    })
    assert resp.status_code == 404
