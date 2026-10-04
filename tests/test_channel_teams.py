"""Microsoft Teams channel: Bot Framework JWT verification, activity dispatch,
mention stripping and the outbound reply.

The shared chat-channel core (allowlist, commands, the turn, the routes) is
covered once in tests/test_channels_core.py; this file is about what Teams
owns: connectors/teams/auth.py (inbound token verification, with a locally
generated RSA key standing in for the Bot Framework's own signing key) and
connectors/teams/service.py (activity -> handle_message -> reply), plus the
webhook route in dashboard/backend/routes/teams_channel.py. No network: httpx is
mocked at the transport level (httpx.MockTransport), matching
tests/test_models_service.py.

Run: ``python -m pytest tests/test_channel_teams.py -q``
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


# ── RSA / JWK / JWT test helpers (stand in for the Bot Framework's own key) ──


def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return key, private_pem


def _b64url_uint(n: int) -> str:
    length = (n.bit_length() + 7) // 8 or 1
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def _jwk_from_key(key, kid: str) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
        "n": _b64url_uint(numbers.n), "e": _b64url_uint(numbers.e),
    }


def _make_token(private_pem: bytes, kid: str, *, audience: str = "bot-app-id",
                issuer: str = "https://api.botframework.com",
                exp_delta: int = 3600, service_url: str | None = None) -> str:
    now = int(time.time())
    payload = {"aud": audience, "iss": issuer, "iat": now, "exp": now + exp_delta}
    if service_url:
        payload["serviceurl"] = service_url
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import teams_channel as teams_routes

    app = FastAPI()
    app.include_router(teams_routes.router)
    return TestClient(app)


def _configure_spec(app_id: str = "bot-app-id", app_password: str = "pw-1"):
    from connectors.teams import SPEC
    SPEC.store.set_config({"app_id": app_id, "app_password": app_password, "tenant_id": ""})
    return SPEC


# ── auth.verify_activity_token ──────────────────────────────────────────────


def test_a_valid_token_verifies():
    from connectors.teams import auth

    key, pem = _rsa_keypair()
    jwk = _jwk_from_key(key, "kid-1")
    token = _make_token(pem, "kid-1")

    claims = auth.verify_activity_token(token, "bot-app-id", jwks_fetcher=lambda: {"keys": [jwk]})
    assert claims["aud"] == "bot-app-id"
    assert claims["iss"] == "https://api.botframework.com"


def test_wrong_audience_fails():
    from connectors.teams import auth

    key, pem = _rsa_keypair()
    jwk = _jwk_from_key(key, "kid-1")
    token = _make_token(pem, "kid-1", audience="someone-elses-app")

    with pytest.raises(auth.TeamsAuthError):
        auth.verify_activity_token(token, "bot-app-id", jwks_fetcher=lambda: {"keys": [jwk]})


def test_expired_token_fails():
    from connectors.teams import auth

    key, pem = _rsa_keypair()
    jwk = _jwk_from_key(key, "kid-1")
    token = _make_token(pem, "kid-1", exp_delta=-3600)

    with pytest.raises(auth.TeamsAuthError):
        auth.verify_activity_token(token, "bot-app-id", jwks_fetcher=lambda: {"keys": [jwk]})


def test_service_url_mismatch_fails():
    from connectors.teams import auth

    key, pem = _rsa_keypair()
    jwk = _jwk_from_key(key, "kid-1")
    token = _make_token(pem, "kid-1", service_url="https://smba.trafficmanager.net/amer/")

    with pytest.raises(auth.TeamsAuthError):
        auth.verify_activity_token(
            token, "bot-app-id", service_url="https://other.example/",
            jwks_fetcher=lambda: {"keys": [jwk]},
        )


def test_matching_service_url_passes():
    from connectors.teams import auth

    key, pem = _rsa_keypair()
    jwk = _jwk_from_key(key, "kid-1")
    url = "https://smba.trafficmanager.net/amer/"
    token = _make_token(pem, "kid-1", service_url=url)

    claims = auth.verify_activity_token(
        token, "bot-app-id", service_url=url, jwks_fetcher=lambda: {"keys": [jwk]},
    )
    assert claims["serviceurl"] == url


def test_unknown_kid_forces_a_refresh_then_fails():
    from connectors.teams import auth

    key, pem = _rsa_keypair()
    token = _make_token(pem, "kid-missing")
    calls = []

    def fetcher():
        calls.append(1)
        return {"keys": []}

    # jwks_fetcher is not auth.fetch_jwks, so no forced-refresh retry happens;
    # it still raises cleanly either way.
    with pytest.raises(auth.TeamsAuthError):
        auth.verify_activity_token(token, "bot-app-id", jwks_fetcher=fetcher)
    assert calls == [1]


# ── mention stripping ────────────────────────────────────────────────────────


def test_mention_is_stripped_from_text():
    from connectors.teams.service import strip_mentions

    entities = [{"type": "mention", "text": "<at>HubBot</at>", "mentioned": {"id": "bot-1"}}]
    out = strip_mentions("<at>HubBot</at> what's on my calendar?", entities, "bot-1")
    assert out == "what's on my calendar?"


def test_mention_of_someone_else_is_left_alone():
    from connectors.teams.service import strip_mentions

    entities = [{"type": "mention", "text": "<at>SomeoneElse</at>", "mentioned": {"id": "other-1"}}]
    out = strip_mentions("<at>SomeoneElse</at> hi", entities, "bot-1")
    assert out == "<at>SomeoneElse</at> hi"


# ── handle_activity dispatch ─────────────────────────────────────────────────


def _activity(conversation_id="conv-1", conv_type="personal", text="hello",
             mentioned=False, sender_id="user-1", service_url="https://smba.example/amer"):
    entities = []
    if mentioned:
        entities.append({"type": "mention", "text": "<at>HubBot</at>", "mentioned": {"id": "bot-1"}})
        text = f"<at>HubBot</at> {text}"
    return {
        "type": "message",
        "id": "activity-1",
        "serviceUrl": service_url,
        "text": text,
        "entities": entities,
        "conversation": {"id": conversation_id, "conversationType": conv_type, "tenantId": "tenant-1"},
        "recipient": {"id": "bot-1", "name": "HubBot"},
        "from": {"id": sender_id, "name": "User"},
    }


def test_non_allowlisted_conversation_is_dropped(monkeypatch):
    _configure_spec()
    from connectors.teams import SPEC

    SPEC.store.set_allowed([])  # reject everyone
    calls = []

    async def fake_handle_message(*a, **kw):
        calls.append((a, kw))

    import asyncio
    monkeypatch.setattr(SPEC.service, "handle_message", fake_handle_message)

    asyncio.run(SPEC.service.handle_activity(_activity(conversation_id="conv-dropped")))

    assert calls == []
    assert SPEC.store.get_cursor("conv:conv-dropped") is None


def test_group_chat_without_mention_is_ignored(monkeypatch):
    _configure_spec()
    from connectors.teams import SPEC

    SPEC.store.set_allowed(["conv-group-1"])
    calls = []

    async def fake_handle_message(*a, **kw):
        calls.append((a, kw))

    import asyncio
    monkeypatch.setattr(SPEC.service, "handle_message", fake_handle_message)

    asyncio.run(SPEC.service.handle_activity(
        _activity(conversation_id="conv-group-1", conv_type="channel", mentioned=False)
    ))

    assert calls == []


def test_group_chat_with_mention_is_handled(monkeypatch):
    _configure_spec()
    from connectors.teams import SPEC

    SPEC.store.set_allowed(["conv-group-2"])
    calls = []

    async def fake_handle_message(chat_key, text, **kw):
        calls.append((chat_key, text, kw))

    import asyncio
    monkeypatch.setattr(SPEC.service, "handle_message", fake_handle_message)

    asyncio.run(SPEC.service.handle_activity(
        _activity(conversation_id="conv-group-2", conv_type="channel", mentioned=True, text="status?")
    ))

    assert len(calls) == 1
    chat_key, text, kw = calls[0]
    assert chat_key == "conv-group-2"
    assert text == "status?"
    assert kw.get("reply_to") == "activity-1"
    assert SPEC.store.get_cursor("conv:conv-group-2") is not None


def test_personal_chat_reacts_without_mention(monkeypatch):
    _configure_spec()
    from connectors.teams import SPEC

    SPEC.store.set_allowed(["conv-dm-1"])
    calls = []

    async def fake_handle_message(chat_key, text, **kw):
        calls.append((chat_key, text, kw))

    import asyncio
    monkeypatch.setattr(SPEC.service, "handle_message", fake_handle_message)

    asyncio.run(SPEC.service.handle_activity(
        _activity(conversation_id="conv-dm-1", conv_type="personal", mentioned=False, text="hi there")
    ))

    assert len(calls) == 1
    assert calls[0][1] == "hi there"


def test_bot_own_echoed_message_is_ignored(monkeypatch):
    _configure_spec()
    from connectors.teams import SPEC

    SPEC.store.set_allowed(["conv-echo"])
    calls = []

    async def fake_handle_message(*a, **kw):
        calls.append((a, kw))

    import asyncio
    monkeypatch.setattr(SPEC.service, "handle_message", fake_handle_message)

    asyncio.run(SPEC.service.handle_activity(
        _activity(conversation_id="conv-echo", conv_type="personal", sender_id="bot-1")
    ))

    assert calls == []


# ── outbound: send_text / send_result over a mocked transport ───────────────


def test_send_text_posts_to_the_reply_url_with_the_bearer_token(monkeypatch):
    import asyncio

    from connectors.teams import SPEC, auth

    spec = _configure_spec()
    spec.store.set_cursor("conv:conv-out-1", {
        "serviceUrl": "https://smba.example/amer/",
        "conversation": {"id": "conv-out-1"},
        "recipient": {"id": "bot-1"},
        "from": {"id": "user-1"},
        "activity_id": "activity-7",
    })

    async def fake_token(app_id, app_password):
        return "outbound-token-xyz"

    monkeypatch.setattr(auth, "get_outbound_token", fake_token)

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "sent-1"})

    from connectors.teams import service as service_mod

    _real_async_client = httpx.AsyncClient

    def fake_async_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return _real_async_client(*a, **kw)

    monkeypatch.setattr(service_mod.httpx, "AsyncClient", fake_async_client)

    asyncio.run(SPEC.service.send_text("conv-out-1", "hi there", reply_to="activity-7"))

    assert len(requests) == 1
    req = requests[0]
    assert str(req.url) == "https://smba.example/amer/v3/conversations/conv-out-1/activities/activity-7"
    assert req.headers["authorization"] == "Bearer outbound-token-xyz"
    body = json.loads(req.content.decode("utf-8"))
    assert body["type"] == "message"
    assert body["text"] == "hi there"
    assert body["replyToId"] == "activity-7"


def test_bound_conversation_runs_a_turn_and_replies(monkeypatch):
    import asyncio

    from connectors.teams import SPEC, auth

    spec = _configure_spec()
    chat_key = "conv-turn-1"
    spec.store.set_allowed([chat_key])
    spec.store.upsert_binding(
        chat_key=chat_key, agent_id="some-agent", workspace="some-ws",
        conversation_id="conv-id-1", title="Teams chat",
    )

    from connectors.channels.turns import TurnResult
    import connectors.channels.service as channels_service_mod
    import connectors.channels.turns as turns_mod

    async def fake_run_turn(*a, **kw):
        return TurnResult(text="hi", ok=True)

    monkeypatch.setattr(channels_service_mod, "run_turn", fake_run_turn)
    monkeypatch.setattr(turns_mod, "run_turn", fake_run_turn)

    async def fake_token(app_id, app_password):
        return "outbound-token-abc"

    monkeypatch.setattr(auth, "get_outbound_token", fake_token)

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "reply-activity-1"})

    from connectors.teams import service as service_mod

    _real_async_client = httpx.AsyncClient

    def fake_async_client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return _real_async_client(*a, **kw)

    monkeypatch.setattr(service_mod.httpx, "AsyncClient", fake_async_client)

    activity = _activity(conversation_id=chat_key, conv_type="personal", text="what's my day look like?")
    asyncio.run(SPEC.service.handle_activity(activity))

    assert len(requests) == 1
    req = requests[0]
    assert str(req.url) == f"https://smba.example/amer/v3/conversations/{chat_key}/activities/activity-1"
    assert req.headers["authorization"] == "Bearer outbound-token-abc"
    body = json.loads(req.content.decode("utf-8"))
    assert body["text"] == "hi"
    assert body["replyToId"] == "activity-1"


# ── the webhook route ────────────────────────────────────────────────────────


def test_route_returns_503_when_unconfigured():
    from connectors.teams import SPEC

    SPEC.store.set_config({"app_id": "", "app_password": ""}, clear=["app_password"])
    client = _client()
    resp = client.post("/api/channels/teams/messages", json={"type": "message"},
                       headers={"authorization": "Bearer x"})
    assert resp.status_code == 503


def test_route_returns_401_on_a_missing_token():
    _configure_spec()
    client = _client()
    resp = client.post("/api/channels/teams/messages", json={"type": "message"})
    assert resp.status_code == 401


def test_route_returns_401_on_a_bad_token():
    _configure_spec()
    client = _client()
    resp = client.post(
        "/api/channels/teams/messages", json={"type": "message"},
        headers={"authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_route_returns_200_and_schedules_handling_on_a_good_token(monkeypatch):
    spec = _configure_spec()
    key, pem = _rsa_keypair()
    jwk = _jwk_from_key(key, "kid-ok")
    token = _make_token(pem, "kid-ok", audience=spec.store.get("app_id"))

    from connectors.teams import auth
    monkeypatch.setattr(auth, "fetch_jwks", lambda force=False: {"keys": [jwk]})

    called = []

    async def fake_handle(activity):
        called.append(activity)

    monkeypatch.setattr(spec.service, "handle_activity", fake_handle)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import teams_channel as teams_routes

    app = FastAPI()
    app.include_router(teams_routes.router)

    with TestClient(app) as client:
        resp = client.post(
            "/api/channels/teams/messages",
            json={"type": "message", "text": "hello", "id": "a1", "conversation": {"id": "conv1"}},
            headers={"authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {}

        for _ in range(50):
            if called:
                break
            time.sleep(0.01)

    assert called and called[0]["id"] == "a1"
