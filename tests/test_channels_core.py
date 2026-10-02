"""The shared chat-channel core (connectors/channels) and its routes.

Slack, Discord, Teams and mail each supply only a transport; the store, the
text commands, the allowlist gate, the pipeline bridge and the routes are
written once. This file checks that once, with a fake channel, so each
transport's own tests can stay about the transport.

Run: ``python -m pytest tests/test_channels_core.py -q``
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.channels.registry import ChannelSpec, ConfigField  # noqa: E402
from connectors.channels.service import ChannelService  # noqa: E402
from connectors.channels.store import ChannelStore  # noqa: E402
from connectors.channels.turns import TurnResult  # noqa: E402


class FakeService(ChannelService):
    name = "fake"
    required_fields = ("token",)

    def __init__(self, store):
        super().__init__(store)
        self.sent: list[tuple[str, str, dict]] = []
        self.connected = 0

    async def _connect(self):
        self.connected += 1
        self._status["identity"] = "@fakebot"

    async def _run(self):
        await self._stop_event.wait()

    async def send_text(self, chat_key, text, **kwargs):
        self.sent.append((chat_key, text, kwargs))


@pytest.fixture
def channel(monkeypatch):
    store = ChannelStore("fake", secret_fields=("token",), defaults={"token": "", "mode": "a"})
    svc = FakeService(store)
    spec = ChannelSpec(name="fake", store=store, service=svc,
                       fields=[ConfigField("token", secret=True, required=True),
                               ConfigField("mode", kind="select", options=["a", "b"])])
    from connectors.channels import registry
    monkeypatch.setattr(registry, "_LOADED", True)
    monkeypatch.setattr(registry, "_CHANNELS", {"fake": spec})
    return spec


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import channels as channels_routes

    app = FastAPI()
    app.include_router(channels_routes.router)
    return TestClient(app)


# ── store ────────────────────────────────────────────────────────────────────

def test_store_masks_secrets_and_keeps_them_on_empty_write(channel):
    store = channel.store
    store.set_config({"token": "xoxb-1", "mode": "b"})
    pub = store.public_config()
    assert pub == {"has_token": True, "mode": "b"}
    assert "xoxb-1" not in str(pub)
    # An empty secret in a later save means "keep".
    store.set_config({"token": "", "mode": "a"})
    assert store.get("token") == "xoxb-1"
    assert store.get("mode") == "a"
    store.set_config({}, clear=["token"])
    assert store.get("token") == ""
    assert store.is_configured("token") is False


def test_store_bindings_and_allowlist(channel):
    store = channel.store
    assert store.is_allowed("C1") is False  # empty list rejects everyone
    store.set_allowed(["C1", " C2 "])
    assert store.is_allowed("C2")
    b = store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo", title="#gen")
    assert b["chat_key"] == "C1" and b["agent_id"] == "a"
    store.upsert_binding(chat_key="C1", agent_id="", flow_id="f1")
    b = store.get_binding("C1")
    assert b["flow_id"] == "f1" and b["agent_id"] == "" and b["workspace"] == "demo"
    assert store.chat_keys_for_workspace("demo") == ["C1"]
    assert store.chat_keys_for_workspace("other") == []
    store.set_cursor("last", 42)
    assert store.get_cursor("last") == 42
    assert store.remove_binding("C1") is True
    assert store.remove_binding("C1") is False


# ── dispatch ─────────────────────────────────────────────────────────────────

def test_unlisted_chat_is_dropped(channel):
    svc = channel.service
    asyncio.run(svc.handle_message("C9", "hello"))
    assert svc.sent == []


def test_help_command_and_unbound_reply(channel):
    svc = channel.service
    channel.store.set_allowed(["C1"])
    asyncio.run(svc.handle_message("C1", "!help"))
    assert "Commands:" in svc.sent[-1][1]
    asyncio.run(svc.handle_message("C1", "what is up", thread="t1"))
    key, text, kwargs = svc.sent[-1]
    assert "isn't bound to a workspace" in text
    assert kwargs == {"thread": "t1"}


def test_workspace_command_is_read_only(channel):
    svc = channel.service
    channel.store.set_allowed(["C1"])
    asyncio.run(svc.handle_message("C1", "/workspace demo"))
    assert "set by the operator" in svc.sent[-1][1]
    assert channel.store.get_binding("C1") is None


def test_bound_chat_runs_a_turn_and_replies_with_kwargs(channel, monkeypatch):
    svc = channel.service
    channel.store.set_allowed(["C1"])
    channel.store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    seen = {}

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen.update(chat_key=chat_key, text=text, source=kw.get("source"))
        return TurnResult(text="hi there", ok=True)

    import connectors.channels.service as service_mod
    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)
    result = asyncio.run(svc.handle_message("C1", "hello", thread="t1"))
    assert result.text == "hi there"
    assert seen == {"chat_key": "C1", "text": "hello", "source": "fake"}
    assert svc.sent[-1] == ("C1", "hi there", {"thread": "t1"})


def test_turn_result_reply_falls_back_to_error():
    assert TurnResult(text="", ok=False, error="boom").reply == "Error: boom"
    assert TurnResult(text="x", ok=False, is_flow=True).reply == "x"
    assert TurnResult().reply == "(no response)"


# ── routes ───────────────────────────────────────────────────────────────────

def test_config_route_round_trip(channel):
    c = _client()
    assert c.get("/api/channels").json()[0]["name"] == "fake"
    r = c.put("/api/channels/fake/config", json={"config": {"token": "t1"}, "allowed": ["C1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"] == {"has_token": True, "mode": "a"}
    assert body["allowed"] == ["C1"] and body["configured"] is True
    r = c.put("/api/channels/fake/config", json={"config": {"nope": 1}})
    assert r.status_code == 400
    assert c.get("/api/channels/nothing/config").status_code == 404


def test_binding_route_requires_a_workspace_and_allowlists_the_chat(channel, monkeypatch):
    c = _client()
    r = c.post("/api/channels/fake/bindings", json={"chat_key": "C1", "workspace": "missing"})
    assert r.status_code == 404
    from routes import channels as channels_routes
    monkeypatch.setattr(channels_routes, "get_workspace_folder", lambda ws: Path("/tmp"))
    r = c.post("/api/channels/fake/bindings",
               json={"chat_key": "C1", "workspace": "demo", "agent_id": "a", "flow_id": "f"})
    assert r.status_code == 400
    r = c.post("/api/channels/fake/bindings", json={"chat_key": "C1", "workspace": "demo", "agent_id": "a"})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"] == "demo"
    assert channel.store.is_allowed("C1")
    assert len(c.get("/api/channels/fake/bindings").json()) == 1
    assert c.delete("/api/channels/fake/bindings/C1").status_code == 200
    assert c.delete("/api/channels/fake/bindings/C1").status_code == 404


def test_test_and_send_routes(channel):
    c = _client()
    assert c.post("/api/channels/fake/test").json() == {"ok": False, "error": "not configured"}
    channel.store.set_config({"token": "t"})
    assert c.post("/api/channels/fake/test").json() == {"ok": True, "identity": "@fakebot"}
    r = c.post("/api/channels/fake/send", json={"chat_key": "C1", "text": "ping"})
    assert r.status_code == 200
    assert channel.service.sent[-1][:2] == ("C1", "ping")


def test_outbound_notification_reaches_bound_chats(channel):
    from connectors.channels import notify
    channel.store.set_config({"token": "t"})
    channel.store.set_enabled(True)
    channel.store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    channel.store.upsert_binding(chat_key="C2", agent_id="a", workspace="other")
    assert notify.notify_workspace("fake", "demo", "Title", "Body") == 1
    assert channel.service.sent[-1][:2] == ("C1", "Title\n\nBody")
    channel.store.set_enabled(False)
    assert notify.notify_workspace("fake", "demo", "Title") == 0


def test_channel_send_tool(channel, monkeypatch):
    from tools.channel_send import channel_send
    import json
    channel.store.set_config({"token": "t"})
    channel.store.set_enabled(True)
    channel.store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    monkeypatch.setattr("common.workspace_context.resolve_active_workspace", lambda *a, **k: "demo")
    out = json.loads(channel_send.invoke({"channel": "fake", "text": "hey"}))
    assert out == {"ok": True, "sent": 1, "workspace": "demo"}
    out = json.loads(channel_send.invoke({"channel": "fake", "chat_key": "C7", "text": "hey"}))
    assert out["ok"] is False and "not bound" in out["error"]
    out = json.loads(channel_send.invoke({"channel": "zzz", "text": "hey"}))
    assert out["ok"] is False


def test_proactive_channel_kinds_are_untrusted():
    from proactive.events import CHANNEL_TRIGGER_KINDS, UNTRUSTED_TRIGGER_KINDS
    from proactive.profile import TRIGGER_KINDS, NOTIFY_CHANNELS
    from notify.store import CHANNELS
    for kind in CHANNEL_TRIGGER_KINDS:
        assert kind in UNTRUSTED_TRIGGER_KINDS
        assert kind in TRIGGER_KINDS
    for ch in ("discord", "teams", "mail"):
        assert ch in NOTIFY_CHANNELS and ch in CHANNELS
