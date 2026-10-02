"""Slack channel: Socket Mode dispatch, outbound rendering, the Events API webhook.

Mirrors ``tests/test_telegram_allowlist.py``'s shape (no pytest-asyncio; async
bodies driven with ``asyncio.run``) and reuses the monkeypatch pattern from
``tests/test_channels_core.py`` for ``connectors.channels.service.run_turn``
(the name is bound at import time into that module's namespace, so patching
``connectors.channels.turns.run_turn`` instead would not be seen by
``ChannelService.handle_message``).

No network: ``httpx.AsyncClient`` is replaced with an in-memory fake that
answers by URL suffix; ``websockets.connect`` is never exercised here since
the socket-mode loop itself has no branching logic worth a dedicated test
beyond what ``handle_event``/``handle_block_action`` already cover.

Run: ``python -m pytest tests/test_channel_slack.py -q``
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import connectors.channels.service as service_mod  # noqa: E402
from connectors.channels.store import ChannelStore  # noqa: E402
from connectors.channels.turns import TurnResult  # noqa: E402
from connectors.slack.service import SlackService, _split, _to_mrkdwn  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=self)  # noqa: BLE001


class _FakeAsyncClient:
    """Answers a POST/GET by matching the end of the URL against ``responses``."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **kw):
        self.calls.append(("POST", url, json, headers))
        return _FakeResponse(self._lookup(url))

    async def get(self, url, headers=None, **kw):
        self.calls.append(("GET", url, None, headers))
        return _FakeResponse(self._lookup(url))

    def _lookup(self, url):
        for suffix, body in self._responses.items():
            if url.endswith(suffix):
                return body
        raise AssertionError(f"unexpected call: {url}")


def _patch_httpx(monkeypatch, responses):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(responses))


def _make_service():
    store = ChannelStore("slack-test", secret_fields=("bot_token", "app_token", "signing_secret"),
                         defaults={"mode": "socket"})
    return SlackService(store), store


def _capture_sends(svc):
    sent = []

    async def fake(chat_key, text, **kw):
        sent.append((chat_key, text, kw))

    svc.send_text = fake
    return sent


# ── _connect ─────────────────────────────────────────────────────────────────

def test_connect_sets_identity(monkeypatch):
    svc, store = _make_service()
    store.set_config({"bot_token": "xoxb-1"})
    _patch_httpx(monkeypatch, {"auth.test": {"ok": True, "user": "hubbot", "user_id": "U1", "team": "Acme"}})

    asyncio.run(svc._connect())

    assert svc._status["identity"] == "@hubbot"
    assert svc._status["bot_user_id"] == "U1"
    assert svc._status["team"] == "Acme"


# ── dispatch: allowlist, unbound reply, bound turn, mention stripping ───────

def test_message_from_an_unallowlisted_chat_is_dropped():
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    sent = _capture_sends(svc)
    event = {"type": "message", "channel": "D1", "channel_type": "im", "text": "hello", "user": "U2", "ts": "1"}

    asyncio.run(svc.handle_event(event))

    assert sent == []


def test_allowlisted_unbound_dm_gets_the_unbound_reply():
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    store.set_allowed(["D1"])
    sent = _capture_sends(svc)
    event = {"type": "message", "channel": "D1", "channel_type": "im", "text": "hello", "user": "U2", "ts": "1"}

    asyncio.run(svc.handle_event(event))

    assert len(sent) == 1
    assert "isn't bound to a workspace" in sent[0][1]


def test_bound_dm_runs_a_turn_and_replies_in_thread(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    store.set_allowed(["D1"])
    store.upsert_binding(chat_key="D1", agent_id="a", workspace="demo")
    sent = _capture_sends(svc)

    seen = {}

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen.update(chat_key=chat_key, text=text, source=kw.get("source"))
        return TurnResult(text="hi there", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)
    event = {"type": "message", "channel": "D1", "channel_type": "im", "text": "hello", "user": "U2", "ts": "42.1"}

    asyncio.run(svc.handle_event(event))

    assert seen == {"chat_key": "D1", "text": "hello", "source": "slack"}
    assert sent[-1] == ("D1", "hi there", {"thread_ts": "42.1"})


def test_channel_message_needs_a_mention_and_the_mention_is_stripped(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    store.set_allowed(["C1"])
    store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    _capture_sends(svc)

    seen_texts = []

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen_texts.append(text)
        return TurnResult(text="ok", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)

    unmentioned = {"type": "message", "channel": "C1", "channel_type": "channel",
                   "text": "no mention here", "user": "U2", "ts": "1"}
    asyncio.run(svc.handle_event(unmentioned))
    assert seen_texts == []  # not mentioned in a channel: ignored

    mentioned = {"type": "message", "channel": "C1", "channel_type": "channel",
                 "text": "<@U1> hello there", "user": "U2", "ts": "2"}
    asyncio.run(svc.handle_event(mentioned))
    assert seen_texts == ["hello there"]


def test_bot_messages_are_ignored():
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    store.set_allowed(["D1"])
    sent = _capture_sends(svc)
    event = {"type": "message", "channel": "D1", "channel_type": "im", "text": "hi", "bot_id": "B1", "ts": "1"}

    asyncio.run(svc.handle_event(event))

    assert sent == []


def test_duplicate_events_are_deduped():
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    store.set_allowed(["D1"])
    sent = _capture_sends(svc)
    event = {"type": "message", "channel": "D1", "channel_type": "im", "text": "hi",
             "user": "U2", "ts": "1", "client_msg_id": "dup-1"}

    asyncio.run(svc.handle_event(event))
    asyncio.run(svc.handle_event(dict(event)))

    assert len(sent) == 1  # the unbound reply, only once


# ── block_actions: echo then run as the next message ────────────────────────

def test_block_action_echoes_then_runs_the_value(monkeypatch):
    svc, store = _make_service()
    svc._bot_user_id = "U1"
    store.set_allowed(["C1"])
    store.upsert_binding(chat_key="C1", agent_id="a", workspace="demo")
    sent = _capture_sends(svc)

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        return TurnResult(text="done", ok=True)

    monkeypatch.setattr(service_mod, "run_turn", fake_run_turn)
    payload = {
        "type": "block_actions",
        "actions": [{"value": "yes", "text": {"type": "plain_text", "text": "Yes"}}],
        "channel": {"id": "C1"},
        "message": {"ts": "5"},
    }

    asyncio.run(svc.handle_block_action(payload))

    assert sent[0] == ("C1", "➡️ Yes", {"thread_ts": "5"})
    assert sent[1] == ("C1", "done", {"thread_ts": "5"})


# ── markdown and splitting ───────────────────────────────────────────────────

def test_mrkdwn_conversion_keeps_code_fences():
    text = "**bold** and ```still **bold**```"
    assert _to_mrkdwn(text) == "*bold* and ```still **bold**```"


def test_split_respects_the_limit():
    text = "x" * 9000
    chunks = _split(text, 3900)
    assert len(chunks) > 1
    assert all(len(c) <= 3900 for c in chunks)
    assert "".join(chunks) == text


# ── send_result: native buttons ──────────────────────────────────────────────

def test_send_result_renders_buttons_as_block_kit(monkeypatch):
    svc, store = _make_service()
    store.set_config({"bot_token": "xoxb-1"})
    posted = {}

    async def fake_post_message(chat_key, text, *, blocks=None, thread_ts=None):
        posted.update(chat_key=chat_key, text=text, blocks=blocks, thread_ts=thread_ts)

    svc._post_message = fake_post_message
    result = TurnResult(text="pick one", ok=True, response_obj={
        "kind": "buttons", "text": "pick one",
        "buttons": [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],
    })

    asyncio.run(svc.send_result("C1", result, thread_ts="9"))

    assert posted["chat_key"] == "C1"
    assert posted["thread_ts"] == "9"
    actions = [b for b in posted["blocks"] if b["type"] == "actions"][0]
    assert [e["action_id"] for e in actions["elements"]] == ["hub_btn_0", "hub_btn_1"]
    assert [e["value"] for e in actions["elements"]] == ["yes", "no"]


# ── the Events API webhook route ─────────────────────────────────────────────

def _sign(body: bytes, ts: str, secret: str) -> str:
    base = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def _client(router):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def slack_webhook():
    from connectors.slack import SPEC
    SPEC.store.set_config({"signing_secret": "shhh"})
    SPEC.store.set_allowed([])
    return SPEC


def test_events_route_answers_url_verification_with_a_valid_signature(slack_webhook):
    from routes import slack as slack_routes

    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign(body, ts, "shhh")
    client = _client(slack_routes.router)

    resp = client.post("/api/channels/slack/events", content=body,
                       headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})

    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


def test_events_route_rejects_a_stale_timestamp(slack_webhook):
    from routes import slack as slack_routes

    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode("utf-8")
    ts = str(int(time.time()) - 400)
    sig = _sign(body, ts, "shhh")
    client = _client(slack_routes.router)

    resp = client.post("/api/channels/slack/events", content=body,
                       headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})

    assert resp.status_code == 403


def test_events_route_rejects_a_bad_signature(slack_webhook):
    from routes import slack as slack_routes

    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode("utf-8")
    ts = str(int(time.time()))
    client = _client(slack_routes.router)

    resp = client.post("/api/channels/slack/events", content=body,
                       headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": "v0=deadbeef"})

    assert resp.status_code == 403


def test_events_route_acks_an_event_callback_immediately(slack_webhook):
    from routes import slack as slack_routes

    body = json.dumps({"type": "event_callback", "event_id": "Ev1",
                       "event": {"type": "message", "channel": "C9", "channel_type": "im",
                                 "text": "hi", "ts": "1"}}).encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign(body, ts, "shhh")
    client = _client(slack_routes.router)

    resp = client.post("/api/channels/slack/events", content=body,
                       headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_handle_event_safely_swallows_errors(slack_webhook, monkeypatch):
    from routes import slack as slack_routes

    async def boom(event, *, event_id=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(slack_webhook.service, "handle_event", boom)

    asyncio.run(slack_routes._handle_event_safely({"type": "message"}, "id1"))  # must not raise


def test_interactions_route_verifies_signature_and_dispatches_block_actions(slack_webhook, monkeypatch):
    from routes import slack as slack_routes

    seen = {}

    async def fake_handle_block_action(payload):
        seen["payload"] = payload

    monkeypatch.setattr(slack_webhook.service, "handle_block_action", fake_handle_block_action)

    payload = {"type": "block_actions", "actions": [{"value": "yes"}], "channel": {"id": "C1"}}
    body = f"payload={quote(json.dumps(payload))}".encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign(body, ts, "shhh")
    client = _client(slack_routes.router)

    resp = client.post("/api/channels/slack/interactions", content=body,
                       headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
                                "Content-Type": "application/x-www-form-urlencoded"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_events_route_without_a_signing_secret_is_refused():
    from connectors.slack import SPEC
    SPEC.store.set_config({}, clear=["signing_secret"])
    from routes import slack as slack_routes

    client = _client(slack_routes.router)
    resp = client.post("/api/channels/slack/events", content=b"{}",
                       headers={"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=x"})

    assert resp.status_code == 403
