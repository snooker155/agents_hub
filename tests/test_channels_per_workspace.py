"""Chat channel bots per workspace (docs/connectors.md "Connectors per workspace").

The default workspace's bot serves every workspace, exactly as before. A bot
a workspace defines for itself is a separate bot: its own loop and lease
role, its own config, allowlist, cursor and bindings in that workspace's
document, and it serves that workspace only. Two workspaces, ``team-a`` with
a bot of its own and ``team-b`` without, a fake transport so nothing
connects; then the same for Telegram and the Teams inbound webhook.

Run: ``python -m pytest tests/test_channels_per_workspace.py -q``
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.channels.registry import ChannelSpec, ConfigField  # noqa: E402
from connectors.channels.service import ChannelService  # noqa: E402
from connectors.channels.store import ChannelStore, in_workspace  # noqa: E402
from connectors.channels.turns import TurnResult  # noqa: E402

A, B = "team-a", "team-b"


class FakeService(ChannelService):
    name = "fake"
    required_fields = ("token",)

    def __init__(self, store):
        super().__init__(store)
        self.sent: list[tuple[str, str]] = []
        self.connected = 0

    async def _connect(self):
        self.connected += 1
        self._status["identity"] = f"@bot-{self.store.get('token')}"

    async def _run(self):
        await self._stop_event.wait()

    async def send_text(self, chat_key, text, **kwargs):
        self.sent.append((chat_key, text))


@pytest.fixture
def workspaces():
    from workspace.storage import create_workspace_folder
    for ws in (A, B):
        create_workspace_folder(ws)
    return A, B


@pytest.fixture
def supervisor(monkeypatch):
    import common.singletons as singletons
    sup = singletons.SingletonSupervisor([])
    monkeypatch.setattr(singletons, "supervisor", sup)
    return sup


@pytest.fixture
def channel(monkeypatch, workspaces, supervisor):
    from connectors.channels import registry
    store = ChannelStore("fake", secret_fields=("token",), defaults={"token": ""})
    spec = ChannelSpec(name="fake", store=store, service=FakeService(store),
                       fields=[ConfigField("token", secret=True, required=True)],
                       inbound_url="/api/channels/fake/events")
    monkeypatch.setattr(registry, "_LOADED", True)
    monkeypatch.setattr(registry, "_CHANNELS", {"fake": spec})
    default = store.for_workspace("default")
    default.set_config({"token": "d"})
    default.set_enabled(True)
    team_a = store.for_workspace(A)
    team_a.set_config({"token": "a"})
    team_a.set_enabled(True)
    return spec


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import channels as channels_routes

    app = FastAPI()
    app.include_router(channels_routes.router)
    return TestClient(app)


# ── storage: two bots, two documents ─────────────────────────────────────────

def test_each_bot_has_its_own_config_and_allowlist(channel):
    store = channel.store
    store.for_workspace("default").set_allowed(["D1"])
    store.for_workspace(A).set_allowed(["A1"])
    assert store.defines(A) and not store.defines(B)
    assert in_workspace(A, store.get, "token") == "a"
    assert in_workspace(B, store.get, "token") == "d"  # inherits the default's
    assert in_workspace(A, store.get_allowed) == ["A1"]
    assert in_workspace(B, store.get_allowed) == ["D1"]

    from connectors.channels import registry
    own = registry.service_for("fake", A)
    assert own is not channel.service and own.workspace == A and own.store.get("token") == "a"
    assert registry.service_for("fake", B) is None
    assert registry.effective_service("fake", B) is channel.service
    assert registry.effective_service("fake", A) is own
    # The default's service is pinned to the default document, whatever runs it.
    assert in_workspace(A, channel.service.store.get, "token") == "d"


# ── resync: create, restart, stop, drop ──────────────────────────────────────

def test_resync_creates_restarts_and_stops_the_right_instance(channel, supervisor):
    from connectors.channels import registry

    async def run():
        own = await registry.resync("fake", A)
        assert own is registry.service_for("fake", A) and own is not channel.service
        assert own.is_running() and own.connected == 1
        assert own.status["identity"] == "@bot-a"
        assert supervisor.has("channel_fake@team-a")
        assert not channel.service.is_running()  # the default's is untouched

        channel.store.for_workspace(A).set_config({"token": "a2"})
        assert await registry.resync("fake", A) is own
        assert own.is_running() and own.connected == 2 and own.status["identity"] == "@bot-a2"

        channel.store.for_workspace(A).set_enabled(False)
        await registry.resync("fake", A)
        assert not own.is_running()

        channel.store.for_workspace(A).set_enabled(True)
        await registry.resync("fake", A)
        assert own.is_running()
        channel.store.remove_workspace(A)
        assert await registry.resync("fake", A) is None
        assert not own.is_running()
        assert registry.service_for("fake", A, create=False) is None
        assert not supervisor.has("channel_fake@team-a")

        assert await registry.resync("fake", "default") is channel.service
        assert channel.service.is_running()
        await channel.service.stop()

    asyncio.run(run())


def test_startup_registers_every_bot_and_discovery_follows_the_stores(channel, supervisor):
    from connectors.channels import registry

    roles = registry.register_all(supervisor)
    assert set(roles) == {"channel_fake", "channel_fake@team-a"}

    async def run():
        channel.store.for_workspace(B).set_config({"token": "b"})
        await registry.discover()
        assert supervisor.has("channel_fake@team-b")
        channel.store.remove_workspace(A)
        await registry.discover()
        assert not supervisor.has("channel_fake@team-a")
        assert set(channel.instances) == {B}

    asyncio.run(run())


# ── a workspace's bot serves that workspace only ─────────────────────────────

def test_a_workspace_bot_cannot_be_pointed_elsewhere(channel, monkeypatch):
    from connectors.channels import registry
    own = registry.service_for("fake", A)
    own.store.set_allowed(["A1"])

    asyncio.run(own.handle_message("A1", "/workspace team-b"))
    assert "serves workspace `team-a` only" in own.sent[-1][1]
    asyncio.run(own.handle_message("A1", "/workspaces"))
    assert own.sent[-1][1] == "This bot serves workspace `team-a` only."
    assert own.store.get_binding("A1") is None

    c = _client()
    r = c.post("/api/channels/fake/bindings?workspace=team-a",
               json={"chat_key": "A1", "workspace": B, "agent_id": "x"})
    assert r.status_code == 400 and "team-a" in r.json()["detail"]
    r = c.post("/api/channels/fake/bindings?workspace=team-a", json={"chat_key": "A2", "agent_id": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"] == A
    assert own.store.get_binding("A2")["workspace"] == A
    assert own.store.is_allowed("A2")
    assert channel.service.store.get_binding("A2") is None  # not the default's bot

    # The default's bot still binds a chat to any workspace.
    r = c.post("/api/channels/fake/bindings", json={"chat_key": "D1", "workspace": B, "agent_id": "x"})
    assert r.status_code == 200 and r.json()["workspace"] == B


def test_a_turn_from_the_workspace_bot_runs_in_that_workspace(channel, monkeypatch):
    from connectors.channels import registry
    import connectors.channels.service as service_mod
    own = registry.service_for("fake", A)
    own.store.set_allowed(["A1"])
    # A binding naming another workspace (written behind the routes' back).
    own.store.upsert_binding(chat_key="A1", agent_id="x", workspace=B)
    channel.service.store.set_allowed(["D1"])
    channel.service.store.upsert_binding(chat_key="D1", agent_id="x", workspace=B)
    seen = []

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen.append((store.workspace, binding["workspace"]))
        return TurnResult(text="ok", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)
    asyncio.run(own.handle_message("A1", "hello"))
    asyncio.run(channel.service.handle_message("D1", "hello"))
    assert seen == [(A, A), ("default", B)]


def test_run_turn_forces_the_workspace_of_a_workspace_bot(channel, monkeypatch):
    import chat
    import chat.runs
    from connectors.channels.turns import run_turn
    requests = []

    async def fake_pipeline(request):
        requests.append(request.workspace)
        yield {"type": "done", "ok": True, "response": "hi"}

    monkeypatch.setattr(chat, "run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(chat.runs, "build_conversation_history", lambda conv_id: [])
    own_store = channel.store.for_workspace(A)
    own_store.upsert_binding(chat_key="A1", agent_id="x", workspace=B, conversation_id="c1")
    result = asyncio.run(run_turn(own_store, "A1", own_store.get_binding("A1"), "hello"))
    assert result.ok and requests == [A]

    default_store = channel.store.for_workspace("default")
    default_store.upsert_binding(chat_key="D1", agent_id="x", workspace=B, conversation_id="c2")
    asyncio.run(run_turn(default_store, "D1", default_store.get_binding("D1"), "hello"))
    assert requests == [A, B]


# ── outbound: the bot serving the workspace ──────────────────────────────────

def test_channel_send_uses_the_bot_of_the_runs_workspace(channel, monkeypatch):
    from connectors.channels import registry
    from tools.channel_send import channel_send
    own = registry.service_for("fake", A)
    own.store.upsert_binding(chat_key="A1", agent_id="x", workspace=A)
    channel.service.store.upsert_binding(chat_key="D1", agent_id="x", workspace=B)
    current = {"ws": A}
    monkeypatch.setattr("common.workspace_context.resolve_active_workspace", lambda *a, **k: current["ws"])

    def send(**args):
        return json.loads(channel_send.invoke({"channel": "fake", "text": "hey", **args}))

    assert send(chat_key="A1")["sent"] == 1
    assert own.sent == [("A1", "hey")] and channel.service.sent == []
    # The default bot's chat is not on team-a's bot.
    out = send(chat_key="D1")
    assert out["ok"] is False and "not bound" in out["error"]
    assert send()["sent"] == 1 and own.sent[-1] == ("A1", "hey")

    current["ws"] = B
    assert send(chat_key="D1")["sent"] == 1
    assert channel.service.sent == [("D1", "hey")]
    out = send(chat_key="A1")  # team-a's chat is on another bot
    assert out["ok"] is False
    assert send()["sent"] == 1 and len(own.sent) == 2


def test_notifications_go_through_the_workspace_bot(channel):
    from connectors.channels import notify, registry
    own = registry.service_for("fake", A)
    own.store.upsert_binding(chat_key="A1", agent_id="x", workspace=A)
    channel.service.store.upsert_binding(chat_key="DA", agent_id="x", workspace=A)
    channel.service.store.upsert_binding(chat_key="DB", agent_id="x", workspace=B)
    assert notify.notify_workspace("fake", A, "T") == 1
    assert own.sent == [("A1", "T")]
    assert notify.notify_workspace("fake", B, "T") == 1
    assert channel.service.sent == [("DB", "T")]


# ── routes with ?workspace= ──────────────────────────────────────────────────

def test_routes_take_a_workspace_and_delete_stops_the_loop(channel, supervisor):
    from connectors.channels import registry
    channel.store.remove_workspace(A)
    with _client() as c:
        got = c.get("/api/channels/fake/config?workspace=team-a").json()
        assert got["source"] == "default" and got["config"] == {"has_token": True}
        assert got["inbound_url"] == "/api/channels/fake/events"

        r = c.put("/api/channels/fake/config?workspace=team-a",
                  json={"config": {"token": "a"}, "enabled": True, "allowed": ["A1"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "here" and body["allowed"] == ["A1"] and body["running"] is True
        assert body["identity"] == "@bot-a"
        assert body["inbound_url"] == "/api/channels/fake/events?workspace=team-a"
        own = registry.service_for("fake", A, create=False)
        assert own is not None and own.is_running() and supervisor.has("channel_fake@team-a")

        default = c.get("/api/channels/fake/config").json()
        assert default["source"] == "here" and default["allowed"] == []
        assert default["defined_in"] == ["default", A]
        assert c.get("/api/channels/fake/config?workspace=team-b").json()["source"] == "default"
        assert c.get("/api/channels/fake/config?workspace=nowhere").status_code == 404

        assert c.post("/api/channels/fake/test?workspace=team-a").json()["identity"] == "@bot-a"
        assert c.post("/api/channels/fake/test?workspace=team-b").json()["identity"] == "@bot-d"
        st = c.get("/api/channels/fake/status?workspace=team-a").json()
        assert st["running"] is True and st["source"] == "here"

        assert c.post("/api/channels/fake/send?workspace=team-a",
                      json={"chat_key": "A1", "text": "ping"}).status_code == 200
        assert own.sent[-1] == ("A1", "ping")

        # team-b uses the default's bot, limited to the chats bound to team-b.
        channel.service.store.upsert_binding(chat_key="DB", agent_id="x", workspace=B)
        channel.service.store.upsert_binding(chat_key="DX", agent_id="x", workspace="elsewhere")
        listed = c.get("/api/channels/fake/bindings?workspace=team-b").json()
        assert [b["chat_key"] for b in listed] == ["DB"]
        assert c.delete("/api/channels/fake/bindings/DX?workspace=team-b").status_code == 404
        assert c.post("/api/channels/fake/send?workspace=team-b",
                      json={"chat_key": "DX", "text": "x"}).status_code == 404
        r = c.post("/api/channels/fake/bindings?workspace=team-b", json={"chat_key": "DX", "agent_id": "x"})
        assert r.status_code == 409

        r = c.delete("/api/channels/fake/config?workspace=team-a")
        assert r.status_code == 200 and r.json()["source"] == "default"
        assert not own.is_running()
        assert registry.service_for("fake", A, create=False) is None
        assert not supervisor.has("channel_fake@team-a")
        assert not channel.store.defines(A)
        assert c.delete("/api/channels/fake/config").status_code == 400


# ── Teams inbound webhook per workspace ──────────────────────────────────────

def test_teams_inbound_route_reaches_the_workspace_bot(workspaces, supervisor, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from connectors.channels import registry
    from connectors.teams import SPEC, auth
    from routes import teams_channel

    monkeypatch.setattr(SPEC, "instances", {})
    SPEC.store.for_workspace(A).set_config({"app_id": "app-a", "app_password": "pw-a"})
    checked, handled = [], []
    monkeypatch.setattr(auth, "verify_activity_token",
                        lambda token, app_id, service_url=None: checked.append(app_id))

    async def fake_handle(activity, service=None):
        handled.append(service.workspace)

    monkeypatch.setattr(teams_channel, "_handle_activity_safely", fake_handle)
    app = FastAPI()
    app.include_router(teams_channel.router)
    headers = {"Authorization": "Bearer t"}
    with TestClient(app) as c:
        r = c.post("/api/channels/teams/messages?workspace=team-a", json={"type": "message"},
                   headers=headers)
        assert r.status_code == 200, r.text
        assert c.post("/api/channels/teams/messages?workspace=team-b", json={},
                      headers=headers).status_code == 404
        deadline = time.time() + 2
        while not handled and time.time() < deadline:
            time.sleep(0.01)
    assert checked == ["app-a"] and handled == [A]
    assert registry.get("teams").inbound_url_for(A) == "/api/channels/teams/messages?workspace=team-a"


# ── mail with a Google sign in: the Google connector of the bot's workspace ──

def test_mail_bots_sign_in_with_their_workspaces_google(workspaces, supervisor, monkeypatch):
    from connectors import google
    from connectors.channels import registry
    from connectors.google import auth as google_auth
    from connectors.mail import SPEC
    from connectors.mail.service import _from_address

    monkeypatch.setattr(SPEC, "instances", {})
    google.STORE.for_workspace("default").set_config({"account_email": "d@example.com"})
    google.STORE.for_workspace(A).set_config({"account_email": "a@example.com"})
    logins = []

    def fake_gmail_login(workspace=None):
        address = google.store_for(workspace).get("account_email")
        logins.append((workspace, address))
        return address, f"token-{address}"

    monkeypatch.setattr(google_auth, "gmail_login", fake_gmail_login)
    cfg = {"auth_mode": "google"}
    for ws in ("default", A, B):  # team-b defines a mail bot but no Google of its own
        SPEC.store.for_workspace(ws).set_config(cfg)

    default_bot = SPEC.service
    own_a = registry.service_for("mail", A)
    own_b = registry.service_for("mail", B)
    assert own_a.workspace == A and own_b.workspace == B

    assert _from_address(cfg, default_bot.workspace) == "d@example.com"
    assert _from_address(cfg, own_a.workspace) == "a@example.com"
    assert _from_address(cfg, own_b.workspace) == "d@example.com"

    def users(svc):
        with svc.scope():  # as notify, the routes and the loop run it
            return svc._open_imap(cfg).config.user, svc._open_smtp(cfg).config.from_address

    assert users(own_a) == ("a@example.com", "a@example.com")
    assert users(own_b) == ("d@example.com", "d@example.com")
    assert users(default_bot) == ("d@example.com", "d@example.com")
    # A workspace's bot names its workspace; the default's runs as the default.
    assert [w for w, _ in logins] == [A, A, B, B, None, None]
    # Even from inside a run of team-a, the default's bot keeps the default's Google.
    assert in_workspace(A, users, default_bot) == ("d@example.com", "d@example.com")


# ── Telegram ─────────────────────────────────────────────────────────────────

class _FakeApi:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return {}

    async def send_chat_action(self, chat_id, action="typing"):
        return None


@pytest.fixture
def telegram(monkeypatch, workspaces, supervisor):
    from connectors.telegram import bots, telegram_runner as tr, telegram_store

    monkeypatch.setattr(bots, "_INSTANCES", {})

    async def fake_get_me(self):
        return {"username": f"bot_{self._base.rsplit('bot', 1)[-1]}"}

    async def fake_poll(self, token):
        await self._stop_event.wait()

    monkeypatch.setattr(tr.TelegramAPI, "get_me", fake_get_me)
    monkeypatch.setattr(tr.TelegramService, "_poll_loop", fake_poll)
    default = telegram_store.for_workspace("default")
    default.set_token("tok-d")
    default.set_enabled(True)
    own = telegram_store.for_workspace(A)
    own.set_token("tok-a")
    own.set_enabled(True)
    own.set_allowed_chat_ids([11])
    return default, own


def test_telegram_bots_have_their_own_documents_and_loops(telegram, supervisor):
    from connectors.telegram import bots, telegram_store
    default, own = telegram
    assert default.get_allowed_chat_ids() == [] and own.get_allowed_chat_ids() == [11]
    assert in_workspace(A, telegram_store.get_token) == "tok-a"
    assert in_workspace(B, telegram_store.get_token) == "tok-d"
    assert telegram_store.defined_workspaces() == [A]

    async def run():
        svc = await bots.resync(A)
        assert svc.workspace == A and svc.is_running() and svc.lease_role == "telegram@team-a"
        assert svc.status["bot_username"] == "bot_tok-a"
        assert supervisor.has("telegram@team-a")
        assert bots.effective_service(B) is bots.service_for("default")
        own.set_enabled(False)
        await bots.resync(A)
        assert not svc.is_running()
        own.set_enabled(True)
        await bots.resync(A)
        assert await bots.drop(A) is True
        assert not svc.is_running() and not supervisor.has("telegram@team-a")

    asyncio.run(run())


def test_telegram_workspace_bot_keeps_its_chats_in_its_workspace(telegram, monkeypatch):
    import chat
    import chat.runs
    from connectors.telegram import telegram_runner as tr
    default, own = telegram
    api = _FakeApi()
    message = {"chat": {"id": 11, "username": "ann"}}
    assert asyncio.run(tr._handle_command(api, message, "/workspace team-b", store=own))
    assert "serves workspace `team-a` only" in api.sent[-1][1]
    assert own.get_binding(11) is None

    requests = []

    async def fake_pipeline(request):
        requests.append(request.workspace)
        yield {"type": "done", "ok": True, "response": "hi"}

    monkeypatch.setattr(chat, "run_chat_pipeline", fake_pipeline)
    monkeypatch.setattr(chat.runs, "build_conversation_history", lambda conv_id: [])
    own.upsert_binding(chat_id=11, agent_id="x", workspace=B, conversation_id="c1")
    asyncio.run(tr._run_agent_for_telegram(api, 11, own.get_binding(11), "hello", [], store=own))
    default.upsert_binding(chat_id=22, agent_id="x", workspace=B, conversation_id="c2")
    asyncio.run(tr._run_agent_for_telegram(api, 22, default.get_binding(22), "hello", [], store=default))
    assert requests == [A, B]


def test_telegram_send_and_notify_use_the_workspace_bot(telegram, monkeypatch):
    import connectors.telegram.notify as tg_notify
    from tools.channel_send import channel_send
    default, own = telegram
    own.upsert_binding(chat_id=11, agent_id="x", workspace=A)
    default.upsert_binding(chat_id=22, agent_id="x", workspace=B)
    sent = []
    monkeypatch.setattr(tg_notify, "_send_text", lambda token, cid, text: sent.append((token, cid)) or True)
    current = {"ws": A}
    monkeypatch.setattr("common.workspace_context.resolve_active_workspace", lambda *a, **k: current["ws"])

    def send(**args):
        return json.loads(channel_send.invoke({"channel": "telegram", "text": "hey", **args}))

    assert send(chat_key="11")["sent"] == 1
    assert send(chat_key="22")["ok"] is False
    assert send()["sent"] == 1
    current["ws"] = B
    assert send(chat_key="22")["sent"] == 1
    assert send()["sent"] == 1
    assert sent == [("tok-a", 11), ("tok-a", 11), ("tok-d", 22), ("tok-d", 22)]


def test_telegram_routes_take_a_workspace(telegram, supervisor):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from connectors.telegram import bots, telegram_store
    from routes import telegram as telegram_routes

    app = FastAPI()
    app.include_router(telegram_routes.router)
    telegram_store.remove_workspace(A)
    with TestClient(app) as c:
        assert c.get("/api/telegram/config?workspace=team-a").json()["source"] == "default"
        r = c.put("/api/telegram/config?workspace=team-a",
                  json={"bot_token": "tok-a", "enabled": True, "allowed_chat_ids": [11]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "here" and body["allowed_chat_ids"] == [11] and body["running"] is True
        assert body["bot_username"] == "bot_tok-a"
        svc = bots.service_for(A, create=False)
        assert svc is not None and svc.is_running()
        default = c.get("/api/telegram/config").json()
        assert default["allowed_chat_ids"] == [] and default["defined_in"] == ["default", A]

        r = c.post("/api/telegram/bindings?workspace=team-a", json={"chat_id": 11, "workspace": B})
        assert r.status_code == 400
        r = c.post("/api/telegram/bindings?workspace=team-a", json={"chat_id": 11})
        assert r.status_code == 200 and r.json()["workspace"] == A
        assert telegram_store.for_workspace(A).get_binding(11)["workspace"] == A
        assert telegram_store.for_workspace("default").get_binding(11) is None
        assert [b["chat_id"] for b in c.get("/api/telegram/bindings?workspace=team-a").json()] == [11]

        r = c.delete("/api/telegram/config?workspace=team-a")
        assert r.status_code == 200 and r.json()["source"] == "default"
        assert not svc.is_running() and not supervisor.has("telegram@team-a")
        assert not telegram_store.defines(A)
