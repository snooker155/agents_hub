"""The consent portal (connectors/consent/, routes/consent.py, docs/consent.md).

An end user of a widget or a channel grants an agent their own Google or
Microsoft account. Covered here: the signed link, a request's life from link
to grant (and to denied, expired, replayed), the callback with the provider's
HTTP mocked, the end user's token used in their own turn and the refusal when
there is none, revoke by the end user and by the operator, the routes, and
the principal bound by the widget, channel and replica turn paths.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

WS = "default"
AGENT = "helper"
PRINCIPAL = "widget:w1:vis_abc"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    from common.config import settings
    from common import secrets as secrets_mod
    monkeypatch.setattr(settings, "secret_key", secrets_mod.keygen(), raising=False)
    monkeypatch.setenv("AGENTS_HUB_PUBLIC_URL", "https://hub.example")
    from connectors.consent import access
    access.reset_cache()
    from routes import consent as consent_routes
    consent_routes.reset_limits()
    from connectors.google import STORE as GOOGLE
    GOOGLE.set_config({"client_id": "gid", "client_secret": "gsecret"})
    from connectors.microsoft import CREDENTIALS as MS
    MS.store.set_config({"tenant_id": "tid", "client_id": "mid", "client_secret": "msecret"})
    from connectors.google import auth
    auth.reset_cache()
    yield
    access.reset_cache()


@pytest.fixture
def agent():
    from agents.registry import AgentSpec, add_agent
    spec = AgentSpec(id=AGENT, name="Helper", type="langchain",
                     entrypoint="agents.standard_agent:StandardAgent")
    add_agent(spec)
    return spec


@pytest.fixture
def per_end_user(agent):
    from connectors.consent import store
    store.save_settings(AGENT, ["google", "microsoft"],
                        {"google": ["calendar"], "microsoft": ["calendar", "mail_read"]})
    return agent


class FakeHTTP:
    """Records provider calls and answers them from a small script."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.refresh_status = 200
        self.ms_refresh_token = None

    def _resp(self, method: str, url: str, status: int, body: Any) -> httpx.Response:
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    def post(self, url, data=None, **kw):
        self.calls.append({"method": "POST", "url": url, "data": dict(data or {})})
        data = dict(data or {})
        if url.startswith("https://oauth2.googleapis.com/token"):
            if data.get("grant_type") == "authorization_code":
                return self._resp("POST", url, 200, {
                    "access_token": "g-access-1", "refresh_token": "g-refresh", "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/calendar openid"})
            if self.refresh_status != 200:
                return self._resp("POST", url, self.refresh_status, {"error": "invalid_grant"})
            return self._resp("POST", url, 200, {"access_token": "g-access-2", "expires_in": 3600})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return self._resp("POST", url, 200, {})
        if "login.microsoftonline.com" in url:
            if data.get("grant_type") == "authorization_code":
                return self._resp("POST", url, 200, {
                    "access_token": "m-access-1", "refresh_token": "m-refresh", "expires_in": 3600})
            if self.refresh_status != 200:
                return self._resp("POST", url, 400, {"error": "invalid_grant"})
            body = {"access_token": "m-access-2", "expires_in": 3600}
            if self.ms_refresh_token:
                body["refresh_token"] = self.ms_refresh_token
            return self._resp("POST", url, 200, body)
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, headers=None, params=None, **kw):
        self.calls.append({"method": "GET", "url": url, "headers": dict(headers or {}), "data": {}})
        if url.startswith("https://www.googleapis.com/oauth2/v3/userinfo"):
            return self._resp("GET", url, 200, {"email": "visitor@gmail.com"})
        if url.startswith("https://graph.microsoft.com/v1.0/me"):
            return self._resp("GET", url, 200, {"mail": "visitor@contoso.com"})
        if url.startswith("https://www.googleapis.com/calendar/v3/"):
            return self._resp("GET", url, 200, {"items": []})
        raise AssertionError(f"unexpected GET {url}")

    def request(self, method, url, params=None, json=None, headers=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "data": {}})
        return self._resp(method, url, 200, {"value": []})


@pytest.fixture
def fake_http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(httpx, "post", fake.post)
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "request", fake.request)
    return fake


class _Turn:
    """Bind a widget or channel turn's scope the way the chat pipeline does."""

    def __init__(self, principal: Optional[str] = PRINCIPAL, agent_id: str = AGENT):
        self.principal = principal
        self.agent_id = agent_id

    def __enter__(self):
        from common import secrets
        self._scope = secrets.activate(WS, self.agent_id, "owner-1")
        self._scope.__enter__()
        self._token = secrets.set_end_user(self.principal)
        return self

    def __exit__(self, *exc):
        from common import secrets
        secrets.reset_end_user(self._token)
        self._scope.__exit__(*exc)


def _link_token(url: str) -> str:
    return urlsplit(url).path.rsplit("/", 1)[-1]


def _grant(provider: str = "google") -> Dict[str, Any]:
    """Walk a whole grant: the tool's link, Continue, the callback."""
    from connectors.consent import flow
    with _Turn():
        out = flow.request_access(provider, "to book the meeting")
    row = flow.open_request(_link_token(out["url"]))
    auth_url = flow.begin(row)
    state = parse_qs(urlsplit(auth_url).query)["state"][0]
    return flow.complete(state=state, code="the-code")


# ── the link ─────────────────────────────────────────────────────────────────

def test_link_token_binds_request_principal_and_expiry():
    from connectors.consent import links
    token = links.mint("req-1", PRINCIPAL, int(time.time()) + 60)
    assert links.verify(token) == {"request_id": "req-1", "principal": PRINCIPAL,
                                   "exp": pytest.approx(int(time.time()) + 60, abs=2)}
    body, _, mac = token.rpartition(".")
    assert links.verify(body + "." + mac[::-1]) is None
    assert links.verify(links.mint("req-1", PRINCIPAL, int(time.time()) - 1)) is None
    assert links.verify("") is None and links.verify("x" * 2000) is None


def test_a_widget_visitor_token_is_not_a_consent_link():
    from connectors.consent import links
    from widgets import visitor
    minted = visitor.mint_visitor("w1")
    assert links.verify(minted["visitor_token"]) is None


# ── the request ──────────────────────────────────────────────────────────────

def test_request_needs_an_end_user_and_an_enabled_provider(agent):
    from connectors.consent import flow, store
    with _Turn(principal=None):
        with pytest.raises(flow.ConsentFlowError) as exc:
            flow.request_access("google")
    assert exc.value.code == "no_end_user"
    with _Turn():
        with pytest.raises(flow.ConsentFlowError) as exc:
            flow.request_access("google")
        assert exc.value.code == "not_enabled"
        store.save_settings(AGENT, ["google"], {"google": ["calendar"]})
        with pytest.raises(flow.ConsentFlowError) as exc:
            flow.request_access("dropbox")
        assert exc.value.code == "bad_provider"


def test_request_is_refused_when_the_provider_has_no_app(per_end_user):
    from connectors.consent import flow
    from connectors.google import STORE
    STORE.set_config({}, clear=("client_id", "client_secret"))
    with _Turn():
        with pytest.raises(flow.ConsentFlowError) as exc:
            flow.request_access("google")
    assert exc.value.code == "not_configured"


def test_link_opens_once_and_names_only_its_request(per_end_user):
    from connectors.consent import flow, store
    with _Turn():
        out = flow.request_access("google", "to read your calendar")
    assert out["url"].startswith("https://hub.example/consent/")
    row = flow.open_request(_link_token(out["url"]))
    assert row["status"] == "pending" and row["scopes"] == ["calendar"]
    assert row["purpose"] == "to read your calendar"
    # A token for another request, or one whose principal does not match the
    # row, opens nothing.
    from connectors.consent import links
    forged = links.mint(row["request_id"], "widget:w1:someone_else", int(time.time()) + 60)
    with pytest.raises(flow.ConsentFlowError) as exc:
        flow.open_request(forged)
    assert exc.value.code == "invalid"
    store.finish(row["request_id"], "denied")
    with pytest.raises(flow.ConsentFlowError) as exc:
        flow.open_request(_link_token(out["url"]))
    assert exc.value.code == "denied"


def test_an_expired_request_is_closed(per_end_user):
    from connectors.consent import flow, store
    with _Turn():
        out = flow.request_access("google")
    row = flow.open_request(_link_token(out["url"]))
    store.update(row["request_id"], expires_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(flow.ConsentFlowError) as exc:
        flow.open_request(_link_token(out["url"]))
    assert exc.value.code == "expired"
    assert store.get_request(row["request_id"])["status"] == "expired"


def test_too_many_links_for_one_end_user(per_end_user, monkeypatch):
    from connectors.consent import flow
    monkeypatch.setattr(flow, "MAX_LINKS_PER_HOUR", 2)
    with _Turn():
        flow.request_access("google")
        flow.request_access("google")
        with pytest.raises(flow.ConsentFlowError) as exc:
            flow.request_access("google")
    assert exc.value.code == "too_many"


# ── the round trip ───────────────────────────────────────────────────────────

def test_google_grant_stores_a_personal_secret(per_end_user, fake_http):
    from common import secrets, audit
    from connectors.consent import access, catalog, flow
    with _Turn():
        out = flow.request_access("google", "to book the meeting")
    row = flow.open_request(_link_token(out["url"]))
    auth_url = flow.begin(row)
    query = parse_qs(urlsplit(auth_url).query)
    assert auth_url.startswith("https://accounts.google.com/")
    assert query["access_type"] == ["offline"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://hub.example/consent/callback"]
    assert "https://www.googleapis.com/auth/calendar" in query["scope"][0].split()
    state = query["state"][0]

    done = flow.complete(state=state, code="the-code")
    assert done["status"] == "granted" and done["account_email"] == "visitor@gmail.com"
    exchange = [c for c in fake_http.calls if c["data"].get("grant_type") == "authorization_code"][0]
    assert exchange["data"]["code_verifier"]
    # The grant is a secret at the end user's own scope, for this agent only.
    raw = secrets.get_secret(WS, "CONSENT_GOOGLE", agent_id=AGENT, user_id=PRINCIPAL)
    assert json.loads(raw)["refresh_token"] == "g-refresh"
    assert secrets.get_secret(WS, "CONSENT_GOOGLE", agent_id=AGENT) is None
    assert access.has_grant(WS, AGENT, PRINCIPAL, catalog.GOOGLE)
    # The state is single use: the same callback again finds nothing.
    with pytest.raises(flow.ConsentFlowError) as exc:
        flow.complete(state=state, code="the-code")
    assert exc.value.code == "invalid"
    actions = [e["action"] for e in audit.query(action="consent.grant")["items"]]
    assert actions == ["consent.grant"]
    # Nothing about the token ever reaches the audit row.
    assert "g-refresh" not in json.dumps(audit.query(action="consent.grant"))


def test_denied_and_forged_callbacks(per_end_user, fake_http):
    from connectors.consent import flow, store
    with _Turn():
        out = flow.request_access("google")
    row = flow.open_request(_link_token(out["url"]))
    state = parse_qs(urlsplit(flow.begin(row)).query)["state"][0]
    with pytest.raises(flow.ConsentFlowError) as exc:
        flow.complete(state="not-the-state", code="x")
    assert exc.value.code == "invalid"
    with pytest.raises(flow.ConsentFlowError) as exc:
        flow.complete(state=state, error="access_denied")
    assert exc.value.code == "denied"
    assert store.get_request(row["request_id"])["status"] == "denied"
    assert not [c for c in fake_http.calls if "token" in c["url"]]


def test_microsoft_grant_and_delegated_client(per_end_user, fake_http):
    from connectors.consent import flow
    with _Turn():
        out = flow.request_access("microsoft")
    row = flow.open_request(_link_token(out["url"]))
    auth_url = flow.begin(row)
    assert auth_url.startswith("https://login.microsoftonline.com/tid/oauth2/v2.0/authorize")
    scopes = parse_qs(urlsplit(auth_url).query)["scope"][0].split()
    assert {"Calendars.ReadWrite", "Mail.Read", "offline_access"} <= set(scopes)
    state = parse_qs(urlsplit(auth_url).query)["state"][0]
    done = flow.complete(state=state, code="c")
    assert done["account_email"] == "visitor@contoso.com"

    from tools.microsoft_graph import outlook_calendar_list
    with _Turn():
        result = json.loads(outlook_calendar_list.invoke({}))
        assert result["ok"] is True and result["user"] == "me"
        call = [c for c in fake_http.calls if c["method"] == "GET" and "calendarView" in c["url"]][-1]
        assert call["url"].startswith("https://graph.microsoft.com/v1.0/me/calendarView")
        assert call["headers"]["Authorization"] == "Bearer m-access-2"
        # Another mailbox is out of reach through an end user's grant.
        other = json.loads(outlook_calendar_list.invoke({"user": "boss@contoso.com"}))
        assert other["ok"] is False and "own mailbox" in other["error"]


def test_microsoft_rotated_refresh_token_is_kept(per_end_user, fake_http):
    from common import secrets
    from connectors.consent import access
    _grant("microsoft")
    fake_http.ms_refresh_token = "m-refresh-2"
    with _Turn():
        assert access.token_for_turn("microsoft") == "m-access-2"
    raw = secrets.get_secret(WS, "CONSENT_MICROSOFT", agent_id=AGENT, user_id=PRINCIPAL)
    assert json.loads(raw)["refresh_token"] == "m-refresh-2"


# ── the token in the end user's turns ────────────────────────────────────────

def test_the_end_users_token_replaces_the_hubs_in_their_turn(per_end_user, fake_http):
    from connectors.google import auth
    _grant("google")
    with _Turn():
        assert auth.get_access_token() == "g-access-2"
        refreshes = [c for c in fake_http.calls if c["data"].get("grant_type") == "refresh_token"]
        assert auth.get_access_token() == "g-access-2"
        again = [c for c in fake_http.calls if c["data"].get("grant_type") == "refresh_token"]
        assert len(again) == len(refreshes) == 1  # cached per principal
    # Another visitor of the same widget has no grant: refused, never the hub's.
    with _Turn(principal="widget:w1:vis_other"):
        with pytest.raises(auth.GoogleError) as exc:
            auth.get_access_token()
        assert "request_account_access" in str(exc.value)


def test_google_tool_refuses_without_consent_and_uses_it_with(per_end_user, fake_http):
    from tools.google_workspace import google_calendar_list
    with _Turn():
        refused = json.loads(google_calendar_list.invoke({}))
    assert refused["ok"] is False and refused["code"] == "consent_required"
    _grant("google")
    with _Turn():
        ok = json.loads(google_calendar_list.invoke({}))
    assert ok["ok"] is True
    call = [c for c in fake_http.calls if "calendar/v3" in c["url"]][-1]
    assert call["headers"]["Authorization"] == "Bearer g-access-2"


def test_outside_an_end_user_turn_the_hub_connection_applies(agent, monkeypatch):
    from connectors.consent import access
    from connectors.google import auth
    monkeypatch.setattr(auth, "_refresh_oauth_token", lambda: ("hub-token", time.time() + 600))
    from connectors.google import STORE
    STORE.set_config({"refresh_token": "hub-refresh"})
    # A dashboard turn (no end user), and an agent not set up for consent.
    with _Turn(principal=None):
        assert access.mode_for_turn("google") is None
        assert auth.get_access_token() == "hub-token"
    auth.reset_cache()
    with _Turn():
        assert access.mode_for_turn("google") is None
        assert auth.get_access_token() == "hub-token"


def test_a_dead_grant_is_dropped_and_the_agent_told(per_end_user, fake_http):
    from connectors.consent import access, store
    row = _grant("google")
    fake_http.refresh_status = 400
    with _Turn():
        with pytest.raises(access.ConsentError):
            access.token_for_turn("google")
    assert not access.has_grant(WS, AGENT, PRINCIPAL, "google")
    assert store.get_request(row["request_id"])["status"] == "revoked"


# ── revoke ───────────────────────────────────────────────────────────────────

def test_end_user_revokes_with_the_tool(per_end_user, fake_http):
    from common import audit
    from connectors.consent import access
    from connectors.consent.tools import revoke_account_access
    _grant("google")
    with _Turn():
        out = json.loads(revoke_account_access("google"))
        assert out["revoked"] is True
        again = json.loads(revoke_account_access("google"))
        assert again["revoked"] is False
    assert not access.has_grant(WS, AGENT, PRINCIPAL, "google")
    revoked = [c for c in fake_http.calls if c["url"].startswith("https://oauth2.googleapis.com/revoke")]
    assert revoked and revoked[0]["data"]["token"] == "g-refresh"
    rows = audit.query(action="consent.revoke")["items"]
    assert rows and rows[0]["actor_kind"] == "end_user"


def test_request_tool_answers(per_end_user, fake_http):
    from connectors.consent.tools import request_account_access
    with _Turn():
        out = json.loads(request_account_access("google", "to check your free time"))
    assert out["ok"] is True and out["url"].startswith("https://hub.example/consent/")
    with _Turn(principal=None):
        out = json.loads(request_account_access("google"))
    assert out["ok"] is False and out["code"] == "no_end_user"
    _grant("google")
    with _Turn():
        out = json.loads(request_account_access("google"))
    assert out["already_granted"] is True


def test_consent_tools_come_with_the_settings(agent, per_end_user):
    from connectors.consent.tools import consent_tools_for, TOOL_NAMES
    tools, prompt = consent_tools_for(agent)
    assert [t.name for t in tools] == list(TOOL_NAMES)
    assert "Google or Microsoft" in prompt
    from connectors.consent import store
    store.save_settings(AGENT, [], {})
    assert consent_tools_for(agent) == ([], "")


def test_capability_model_knows_the_tools():
    from tools.capabilities import grants_of, is_recognised_tool_id
    for name in ("request_account_access", "revoke_account_access"):
        assert is_recognised_tool_id(name) and grants_of(name) == frozenset()


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import consent as consent_routes
    app = FastAPI()
    app.include_router(consent_routes.router)
    app.include_router(consent_routes.public_router)
    return TestClient(app)


def test_public_page_start_callback_and_done(client, per_end_user, fake_http):
    from connectors.consent import flow
    with _Turn():
        out = flow.request_access("google", "to book <b>the</b> meeting")
    path = urlsplit(out["url"]).path
    page = client.get(path, headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"})
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "Helper" in page.text and "Google" in page.text and 'lang="ru"' in page.text
    assert "Видеть и изменять события" in page.text
    assert "&lt;b&gt;the&lt;/b&gt;" in page.text and "<b>the</b>" not in page.text

    started = client.post(path + "/start", follow_redirects=False)
    assert started.status_code == 303
    location = started.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    state = parse_qs(urlsplit(location).query)["state"][0]

    done = client.get(f"/consent/callback?state={state}&code=abc")
    assert done.status_code == 200 and "visitor@gmail.com" in done.text
    used = client.get(path)
    assert "already given" in used.text.lower()


def test_public_decline_bad_token_and_throttle(client, per_end_user, monkeypatch):
    from connectors.consent import flow
    with _Turn():
        out = flow.request_access("google")
    path = urlsplit(out["url"]).path
    declined = client.post(path + "/decline")
    assert declined.status_code == 200 and "No access given" in declined.text
    assert client.post(path + "/start", follow_redirects=False).status_code == 200
    bad = client.get("/consent/not-a-token")
    assert bad.status_code == 404 and "not valid" in bad.text
    assert client.get("/consent/callback?state=nope&code=x").status_code == 404
    from routes import consent as consent_routes
    monkeypatch.setattr(consent_routes, "PAGE_PER_MINUTE", 2)
    consent_routes.reset_limits()
    client.get("/consent/x")
    client.get("/consent/x")
    throttled = client.get("/consent/x")
    assert throttled.status_code == 429 and throttled.headers.get("retry-after")


def test_operator_settings_grants_and_revoke(client, agent, fake_http):
    from connectors.consent import access
    cat = client.get("/api/consent/catalog").json()
    assert client.get(f"/api/consent/agents/{AGENT}").json()["catalog"] == cat
    assert cat["redirect_uri"] == "https://hub.example/consent/callback"
    assert {p["id"] for p in cat["providers"]} == {"google", "microsoft"}
    bad = client.put(f"/api/consent/agents/{AGENT}", json={"providers": ["google"],
                                                           "scopes": {"google": ["teleport"]}})
    assert bad.status_code == 400
    saved = client.put(f"/api/consent/agents/{AGENT}", json={
        "providers": ["google"], "scopes": {"google": ["drive_read", "calendar"]}}).json()
    # Catalog order, whatever order the request listed them in.
    assert saved["providers"] == ["google"] and saved["scopes"]["google"] == ["calendar", "drive_read"]
    assert client.get(f"/api/consent/agents/{AGENT}").json()["providers"] == ["google"]

    row = _grant("google")
    grants = client.get("/api/consent/grants", params={"workspace": WS}).json()["grants"]
    assert len(grants) == 1 and grants[0]["principal"] == PRINCIPAL
    assert grants[0]["account_email"] == "visitor@gmail.com"
    assert "refresh" not in json.dumps(grants)
    wrong = client.post(f"/api/consent/grants/{row['request_id']}/revoke", params={"workspace": "other"})
    assert wrong.status_code == 404
    ok = client.post(f"/api/consent/grants/{row['request_id']}/revoke", params={"workspace": WS})
    assert ok.json()["revoked"] is True
    assert not access.has_grant(WS, AGENT, PRINCIPAL, "google")
    assert client.get("/api/consent/grants", params={"workspace": WS}).json()["grants"] == []


def test_the_public_page_needs_no_login(monkeypatch):
    from fastapi.testclient import TestClient
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from dashboard.backend.main import app
    response = TestClient(app).get("/consent/not-a-token")
    assert response.status_code == 404 and "text/html" in response.headers["content-type"]
    assert TestClient(app).get("/api/consent/catalog").status_code == 401


# ── the principal reaches the turn ───────────────────────────────────────────

def test_widget_relay_binds_the_visitor(monkeypatch):
    from chat import pipelines
    from common import secrets
    from widgets.relay import TurnRelay
    seen = {}

    async def fake(request):
        seen["end_user"] = secrets.current_end_user()
        yield {"type": "done", "ok": True, "response": "hi"}

    monkeypatch.setattr(pipelines, "run_chat_pipeline", fake)

    async def go():
        relay = TurnRelay(object(), end_user=secrets.widget_principal("w1", "vis_abc")).start()
        await relay.wait()

    asyncio.run(go())
    assert seen["end_user"] == "widget:w1:vis_abc"
    assert secrets.current_end_user() == ""


def test_channel_turn_binds_the_chat(monkeypatch):
    import chat
    from common import secrets
    from connectors.channels.store import ChannelStore
    from connectors.channels.turns import run_turn
    seen = {}

    async def fake(request):
        seen["end_user"] = secrets.current_end_user()
        yield {"type": "done", "ok": True, "response": "hi"}

    monkeypatch.setattr(chat, "run_chat_pipeline", fake)
    store = ChannelStore("slack")
    binding = {"agent_id": AGENT, "workspace": WS, "conversation_id": "conv-1"}
    result = asyncio.run(run_turn(store, "C123", binding, "hello"))
    assert result.ok and seen["end_user"] == "channel:slack:C123"
    assert secrets.current_end_user() == ""


def test_replica_payload_carries_the_end_user():
    from chat.models import ChatRequest
    from common import secrets
    from services.routing import turn_payload
    request = ChatRequest(agent_id=AGENT, message="hi")
    assert "end_user" not in turn_payload(request, "agent")
    with secrets.end_user("channel:telegram:42"):
        assert turn_payload(request, "agent")["end_user"] == "channel:telegram:42"


def test_grants_are_for_the_workspace_owner(client, monkeypatch):
    from common import identity
    from common.auth import MULTI, Principal
    monkeypatch.setattr(identity, "current_mode", lambda: MULTI)
    monkeypatch.setattr(identity, "request_principal", lambda request: Principal(id="u2", username="u2"))
    monkeypatch.setattr(identity, "membership_role", lambda workspace, user_id: "editor")
    assert client.get("/api/consent/grants", params={"workspace": WS}).status_code == 403
    assert client.post("/api/consent/grants/r1/revoke", params={"workspace": WS}).status_code == 403
    monkeypatch.setattr(identity, "membership_role", lambda workspace, user_id: "owner")
    assert client.get("/api/consent/grants", params={"workspace": WS}).status_code == 200
