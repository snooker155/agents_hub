"""Discord channel: the Gateway dispatch, outbound rendering, REST helpers.

Same shape as ``tests/test_channel_slack.py``: no pytest-asyncio (async
bodies driven with ``asyncio.run``), ``connectors.channels.service.run_turn``
is the name to monkeypatch (bound into that module's namespace at import
time), and ``httpx.AsyncClient`` is replaced with an in-memory fake so no
request leaves the process. The gateway websocket loop itself is not
exercised here (``_run_gateway``'s branching is simple op-code dispatch);
what matters for correctness is ``handle_discord_message`` and
``handle_interaction``, the IDENTIFY payload, and outbound rendering.

Run: ``python -m pytest tests/test_channel_discord.py -q``
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import connectors.channels.service as service_mod  # noqa: E402
from connectors.channels.store import ChannelStore  # noqa: E402
from connectors.channels.turns import TurnResult  # noqa: E402
from connectors.discord.service import DiscordService, _INTENTS, _split  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.content = b"{}" if body is not None else b""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=self)  # noqa: BLE001


class _FakeAsyncClient:
    def __init__(self, responses, *, sequence=None):
        self._responses = responses
        self._sequence = sequence or {}  # suffix -> list of (status, body), consumed in order
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, headers=None, json=None, **kw):
        self.calls.append((method, url, json, headers))
        for suffix, steps in self._sequence.items():
            if url.endswith(suffix) and steps:
                status, body = steps.pop(0)
                return _FakeResponse(body, status)
        return _FakeResponse(self._lookup(url))

    async def get(self, url, headers=None, **kw):
        self.calls.append(("GET", url, None, headers))
        return _FakeResponse(self._lookup(url))

    def _lookup(self, url):
        for suffix, body in self._responses.items():
            if url.endswith(suffix):
                return body
        raise AssertionError(f"unexpected call: {url}")


def _patch_httpx(monkeypatch, responses=None, sequence=None):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses or {}, sequence=sequence))


def _make_service():
    store = ChannelStore("discord-test", secret_fields=("bot_token",))
    return DiscordService(store), store


def _capture_sends(svc):
    sent = []

    async def fake(chat_key, text, **kw):
        sent.append((chat_key, text, kw))

    svc.send_text = fake
    return sent


# ── _connect ─────────────────────────────────────────────────────────────────

def test_connect_sets_identity(monkeypatch):
    svc, store = _make_service()
    store.set_config({"bot_token": "tok"})
    _patch_httpx(monkeypatch, {"/users/@me": {"id": "B1", "username": "hubbot", "discriminator": "0"}})

    asyncio.run(svc._connect())

    assert svc._status["identity"] == "@hubbot"
    assert svc._status["bot_user_id"] == "B1"
    assert svc._bot_user_id == "B1"


def test_connect_identity_with_a_legacy_discriminator(monkeypatch):
    svc, store = _make_service()
    store.set_config({"bot_token": "tok"})
    _patch_httpx(monkeypatch, {"/users/@me": {"id": "B1", "username": "hubbot", "discriminator": "4242"}})

    asyncio.run(svc._connect())

    assert svc._status["identity"] == "hubbot#4242"


# ── dispatch: allowlist, unbound reply, bound turn, mention stripping ───────

def test_message_from_an_unallowlisted_chat_is_dropped():
    svc, store = _make_service()
    svc._bot_user_id = "B1"
    sent = _capture_sends(svc)
    message = {"channel_id": "D1", "guild_id": None, "content": "hi", "id": "m1",
              "author": {"id": "U2", "bot": False}}

    asyncio.run(svc.handle_discord_message(message))

    assert sent == []


def test_allowlisted_unbound_dm_gets_the_unbound_reply():
    svc, store = _make_service()
    svc._bot_user_id = "B1"
    store.set_allowed(["D1"])
    sent = _capture_sends(svc)
    message = {"channel_id": "D1", "guild_id": None, "content": "hi", "id": "m1",
              "author": {"id": "U2", "bot": False}}

    asyncio.run(svc.handle_discord_message(message))

    assert len(sent) == 1
    assert "isn't bound to a workspace" in sent[0][1]


def test_bound_dm_runs_a_turn_and_replies_to_the_message_id(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "B1"
    store.set_allowed(["D1"])
    store.upsert_binding(chat_key="D1", agent_id="a", workspace="demo")
    sent = _capture_sends(svc)

    seen = {}

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen.update(chat_key=chat_key, text=text, source=kw.get("source"))
        return TurnResult(text="hi there", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)
    message = {"channel_id": "D1", "guild_id": None, "content": "hello", "id": "m42",
              "author": {"id": "U2", "bot": False}}

    asyncio.run(svc.handle_discord_message(message))

    assert seen == {"chat_key": "D1", "text": "hello", "source": "discord"}
    assert sent[-1] == ("D1", "hi there", {"reply_to": "m42"})


def test_guild_message_needs_a_mention_and_the_mention_is_stripped(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "B1"
    store.set_allowed(["C1"])
    store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    _capture_sends(svc)

    seen_texts = []

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen_texts.append(text)
        return TurnResult(text="ok", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)

    unmentioned = {"channel_id": "C1", "guild_id": "G1", "content": "no mention here", "id": "m1",
                  "author": {"id": "U2", "bot": False}, "mentions": []}
    asyncio.run(svc.handle_discord_message(unmentioned))
    assert seen_texts == []

    mentioned = {"channel_id": "C1", "guild_id": "G1", "content": "<@B1> hello there", "id": "m2",
                "author": {"id": "U2", "bot": False}, "mentions": [{"id": "B1"}]}
    asyncio.run(svc.handle_discord_message(mentioned))
    assert seen_texts == ["hello there"]


def test_guild_reply_to_the_bot_counts_as_mentioned(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "B1"
    store.set_allowed(["C1"])
    store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    _capture_sends(svc)
    seen_texts = []

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen_texts.append(text)
        return TurnResult(text="ok", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)
    message = {"channel_id": "C1", "guild_id": "G1", "content": "yes please", "id": "m3",
              "author": {"id": "U2", "bot": False}, "mentions": [],
              "referenced_message": {"author": {"id": "B1"}}}

    asyncio.run(svc.handle_discord_message(message))

    assert seen_texts == ["yes please"]


# ── interactions: acknowledge then run the value as the next message ───────

def test_interaction_acknowledges_then_echoes_and_runs(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "B1"
    store.set_config({"bot_token": "tok"})
    store.set_allowed(["C1"])
    store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    sent = _capture_sends(svc)

    callback_calls = []

    async def fake_request(token, method, path, *, json_body=None, retry=True):
        callback_calls.append((method, path, json_body))
        return {}

    monkeypatch.setattr("connectors.discord.service._discord_request", fake_request)

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        return TurnResult(text="done", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)

    interaction = {
        "type": 3, "id": "i1", "token": "itok", "channel_id": "C1",
        "data": {"custom_id": "hub_btn:yes"},
        "message": {"id": "m9", "components": [{"components": [
            {"custom_id": "hub_btn:yes", "label": "Yes"},
        ]}]},
    }

    asyncio.run(svc.handle_interaction(interaction))

    assert callback_calls == [("POST", "/interactions/i1/itok/callback", {"type": 6})]
    assert sent[0] == ("C1", "➡️ Yes", {})
    assert sent[1] == ("C1", "done", {"reply_to": "m9"})


def test_interaction_ignores_non_component_types():
    svc, store = _make_service()
    sent = _capture_sends(svc)

    asyncio.run(svc.handle_interaction({"type": 2}))  # APPLICATION_COMMAND, not a component

    assert sent == []


# ── IDENTIFY payload ─────────────────────────────────────────────────────────

def test_identify_payload_has_the_right_intents():
    svc, _ = _make_service()
    payload = svc._identify_payload("tok")

    assert payload["op"] == 2
    assert payload["d"]["token"] == "tok"
    assert payload["d"]["intents"] == _INTENTS
    # GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
    assert _INTENTS == (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


def test_resume_payload_carries_session_and_sequence():
    svc, _ = _make_service()
    svc._session_id = "sess-1"
    svc._seq = 7

    payload = svc._resume_payload("tok")

    assert payload == {"op": 6, "d": {"token": "tok", "session_id": "sess-1", "seq": 7}}


# ── outbound ─────────────────────────────────────────────────────────────────

def test_send_text_splits_and_attaches_the_reply_reference(monkeypatch):
    svc, store = _make_service()
    store.set_config({"bot_token": "tok"})
    calls = []

    async def fake_request(token, method, path, *, json_body=None, retry=True):
        calls.append((method, path, json_body))
        return {}

    monkeypatch.setattr("connectors.discord.service._discord_request", fake_request)

    text = "y" * 2500
    asyncio.run(svc.send_text("C1", text, reply_to="m1"))

    assert len(calls) == 2  # split across the 1900-char limit
    assert calls[0][2]["message_reference"] == {"message_id": "m1"}
    assert "message_reference" not in calls[1][2]
    assert calls[0][2]["content"] + calls[1][2]["content"] == text


def test_send_result_renders_buttons_as_components(monkeypatch):
    svc, store = _make_service()
    store.set_config({"bot_token": "tok"})
    calls = []

    async def fake_request(token, method, path, *, json_body=None, retry=True):
        calls.append((method, path, json_body))
        return {}

    monkeypatch.setattr("connectors.discord.service._discord_request", fake_request)
    result = TurnResult(text="pick one", ok=True, response_obj={
        "kind": "buttons", "text": "pick one",
        "buttons": [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],
    })

    asyncio.run(svc.send_result("C1", result, reply_to="m2"))

    body = calls[0][2]
    row = body["components"][0]
    assert [c["custom_id"] for c in row["components"]] == ["hub_btn:yes", "hub_btn:no"]
    assert body["message_reference"] == {"message_id": "m2"}


def test_discord_request_sleeps_once_on_429(monkeypatch):
    from connectors.discord.service import _discord_request

    _patch_httpx(monkeypatch, sequence={
        "/channels/C1/messages": [(429, {"retry_after": 0}), (200, {"id": "ok"})],
    })

    result = asyncio.run(_discord_request("tok", "POST", "/channels/C1/messages", json_body={"content": "hi"}))

    assert result == {"id": "ok"}


# ── splitting ────────────────────────────────────────────────────────────────

def test_split_respects_the_limit():
    text = "z" * 5000
    chunks = _split(text, 1900)
    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)
    assert "".join(chunks) == text
