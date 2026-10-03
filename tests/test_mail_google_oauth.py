"""
Gmail through the connected Google account (connectors/mail/oauth.py): the
Gmail scope on the consent screen, the stored grant, the XOAUTH2 exchange on
IMAP and SMTP, the mail channel's ``auth_mode: google`` and the IMAP
watcher's ``use_google``. No network: the token refresh, the IMAP and SMTP
connections are all fakes.

Run: python -m pytest tests/test_mail_google_oauth.py -q
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.channels.store import ChannelStore  # noqa: E402
from connectors.google import CREDENTIALS, STORE  # noqa: E402
from connectors.google import auth as google_auth  # noqa: E402
from connectors.mail import imap as mail_imap  # noqa: E402
from connectors.mail import oauth  # noqa: E402
from connectors.mail import smtp as mail_smtp  # noqa: E402
from connectors.mail.service import MailService, _from_address  # noqa: E402
from watchers import kinds  # noqa: E402

GMAIL = google_auth.GMAIL_SCOPE
ADDRESS = "anna@gmail.com"


def _clear():
    STORE.set_config({}, clear=(
        "service_account_json", "client_id", "client_secret", "refresh_token", "account_email",
        "granted_scopes",
    ))


@pytest.fixture(autouse=True)
def _reset():
    _clear()
    google_auth.reset_cache()
    yield
    _clear()
    google_auth.reset_cache()


def _connect(scopes: str = f"https://www.googleapis.com/auth/drive {GMAIL}", **extra):
    STORE.set_config({"client_id": "cid", "client_secret": "cs", "refresh_token": "rt",
                      "account_email": ADDRESS, "granted_scopes": scopes, **extra})


def _client(router):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── the consent screen and the stored grant ─────────────────────────────────

def test_gmail_scope_is_asked_for_only_with_the_gmail_flag():
    from routes import google as google_routes

    STORE.set_config({"client_id": "cid", "client_secret": "cs"})
    client = _client(google_routes.router)
    plain = parse_qs(urlparse(client.get("/api/google/oauth/start", follow_redirects=False)
                              .headers["location"]).query)
    gmail = parse_qs(urlparse(client.get("/api/google/oauth/start", params={"gmail": "1"},
                                         follow_redirects=False).headers["location"]).query)
    assert GMAIL not in plain["scope"][0].split()
    assert GMAIL in gmail["scope"][0].split()
    # Both ask who the account is, and keep what was granted before.
    for qs in (plain, gmail):
        assert "https://www.googleapis.com/auth/userinfo.email" in qs["scope"][0].split()
        assert qs["include_granted_scopes"] == ["true"]


def test_callback_stores_the_granted_scopes_and_disconnect_clears_them(monkeypatch):
    from routes import google as google_routes

    STORE.set_config({"client_id": "cid", "client_secret": "cs"})
    state = google_auth.new_state()
    monkeypatch.setattr(google_routes, "exchange_code", lambda code, uri: {
        "access_token": "at", "refresh_token": "rt", "scope": f"openid {GMAIL}"})
    monkeypatch.setattr(google_routes, "fetch_userinfo", lambda token: {"email": ADDRESS})
    monkeypatch.setattr(google_routes, "notify_change", lambda *a, **k: None)
    client = _client(google_routes.router)
    client.get("/api/google/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)

    assert google_auth.has_gmail()
    assert CREDENTIALS.extra()["gmail"] is True
    assert client.get("/api/google/gmail/status").json() == {
        "connected": True, "gmail": True, "account_email": ADDRESS}

    client.post("/api/google/oauth/disconnect")
    assert not google_auth.has_gmail()
    assert STORE.get("granted_scopes") == ""


# ── gmail_login ─────────────────────────────────────────────────────────────

def test_gmail_login_explains_what_is_missing(monkeypatch):
    with pytest.raises(google_auth.GoogleError, match="not connected"):
        google_auth.gmail_login()
    _connect(scopes="https://www.googleapis.com/auth/drive")
    with pytest.raises(google_auth.GoogleError, match="no Gmail access"):
        google_auth.gmail_login()


def test_gmail_login_uses_the_oauth_token_even_beside_a_service_account(monkeypatch):
    _connect(service_account_json='{"type": "service_account"}')
    calls = []
    monkeypatch.setattr(google_auth, "_refresh_oauth_token",
                        lambda: (calls.append(1), ("oauth-token", 9e12))[1])
    monkeypatch.setattr(google_auth, "_service_account_token", lambda: pytest.fail("not the service account"))
    assert google_auth.gmail_login() == (ADDRESS, "oauth-token")
    assert google_auth.gmail_login() == (ADDRESS, "oauth-token")
    assert calls == [1]  # cached


# ── the SASL exchange ───────────────────────────────────────────────────────

class _ImapConn:
    def __init__(self):
        self.mechanism = None
        self.answers = []
        self.logged_in = False
        self.selected = None

    def authenticate(self, mechanism, respond):
        self.mechanism = mechanism
        self.answers = [respond(b""), respond(b'{"status":"400"}')]
        return "OK", [b"done"]

    def login(self, *a):
        self.logged_in = True

    def select(self, folder, readonly=False):
        self.selected = folder
        return "OK", [b"1"]


class _SmtpConn:
    def __init__(self):
        self.calls = []

    def starttls(self):
        self.calls.append("starttls")

    def ehlo_or_helo_if_needed(self):
        self.calls.append("ehlo")

    def auth(self, mechanism, respond, initial_response_ok=True):
        self.calls.append(("auth", mechanism, initial_response_ok, respond(), respond(b"challenge")))

    def login(self, *a):
        self.calls.append("login")


def test_imap_and_smtp_send_the_xoauth2_string_once():
    expected = f"user={ADDRESS}\x01auth=Bearer tok\x01\x01"
    conn = _ImapConn()
    oauth.imap_authenticate(conn, ADDRESS, "tok")
    assert conn.mechanism == "XOAUTH2"
    assert conn.answers == [expected.encode(), b""]

    sconn = _SmtpConn()
    oauth.smtp_authenticate(sconn, ADDRESS, "tok")
    assert sconn.calls == ["ehlo", ("auth", "XOAUTH2", True, expected, "")]


# ── the mail channel ────────────────────────────────────────────────────────

def _login():
    return ADDRESS, "tok"


def test_channel_google_mode_fills_gmail_and_signs_in_without_a_password():
    cfg = {"auth_mode": "google"}
    icfg = mail_imap.config_from_dict(cfg, google_login=_login)
    assert (icfg.host, icfg.port, icfg.ssl, icfg.user, icfg.oauth_token) == ("imap.gmail.com", 993, True, ADDRESS, "tok")
    conn = _ImapConn()
    mail_imap.ImapClient(icfg, connection_factory=lambda c: conn).connect()
    assert conn.mechanism == "XOAUTH2" and not conn.logged_in and conn.selected == "INBOX"

    scfg = mail_smtp.config_from_dict(cfg, google_login=_login)
    assert (scfg.host, scfg.port, scfg.security, scfg.from_address) == ("smtp.gmail.com", 587, "starttls", ADDRESS)
    sconn = _SmtpConn()
    mail_smtp.SmtpClient(scfg, connection_factory=lambda c: sconn).connect()
    assert sconn.calls[0] == "starttls" and sconn.calls[2][1] == "XOAUTH2" and "login" not in sconn.calls


def test_channel_google_mode_refuses_another_mailbox():
    with pytest.raises(oauth.MailOAuthError, match=ADDRESS):
        mail_imap.config_from_dict({"auth_mode": "google", "imap_user": "other@gmail.com"}, google_login=_login)
    # The same address in another case is the same mailbox.
    assert mail_imap.config_from_dict({"auth_mode": "google", "imap_user": "Anna@Gmail.com"},
                                      google_login=_login).user == ADDRESS


def test_channel_google_mode_surfaces_a_missing_grant():
    _connect(scopes="https://www.googleapis.com/auth/drive")
    with pytest.raises(oauth.MailOAuthError, match="Connect with Gmail"):
        mail_smtp.config_from_dict({"auth_mode": "google"})


def test_password_mode_is_unchanged():
    icfg = mail_imap.config_from_dict({"imap_host": "imap.x", "imap_user": "u", "imap_password": "p"})
    assert icfg.oauth_token is None
    conn = _ImapConn()
    mail_imap.ImapClient(icfg, connection_factory=lambda c: conn).connect()
    assert conn.logged_in and conn.mechanism is None


def test_service_needs_only_auth_mode_under_google_and_answers_from_the_account(monkeypatch):
    store = ChannelStore("mail-google-test", secret_fields=("imap_password", "smtp_password"))
    store.set_config({"auth_mode": "password", "imap_host": "", "from_address": ""})
    svc = MailService(store)
    assert "imap_password" in svc.required_fields
    assert not store.is_configured(*svc.required_fields)

    store.set_config({"auth_mode": "google"})
    assert svc.required_fields == ("auth_mode",)
    assert store.is_configured(*svc.required_fields)

    _connect()
    assert _from_address(store.get_config()) == ADDRESS
    import connectors.mail.service as service_mod
    sent = []
    fake = SimpleNamespace(connect=lambda: None, quit=lambda: None, logout=lambda: None,
                           send=lambda m: sent.append(m))

    class _Ctx:
        def __enter__(self):
            return fake

        def __exit__(self, *e):
            return None
    monkeypatch.setattr(service_mod, "open_smtp", lambda cfg: _Ctx())
    svc.send_text_sync("bob@example.com", "hello")
    assert sent[0]["From"] == ADDRESS
    # Mail from the account itself is never answered.
    raw = f"From: {ADDRESS}\r\nSubject: loop\r\nMessage-ID: <l@x>\r\n\r\nself".encode()
    assert asyncio.run(svc._handle_raw_message(raw)) is None


# ── the IMAP watcher ────────────────────────────────────────────────────────

class _WatchImap:
    def __init__(self):
        self.logged_out = False

    def select(self, folder, readonly=True):
        return "OK", [b"0"]

    def uid(self, command, *args):
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


def test_watcher_use_google_needs_no_password_and_defaults_to_gmail():
    cfg = kinds.validate_config("imap", {"use_google": True, "password_secret": "IGNORED"})
    assert cfg["host"] == "imap.gmail.com" and cfg["port"] == 993 and cfg["ssl"] is True
    assert cfg["password_secret"] == "" and cfg["username"] == ""
    with pytest.raises(ValueError, match="password_secret"):
        kinds.validate_config("imap", {"host": "h", "username": "u"})


def test_watcher_probe_signs_in_with_the_google_token():
    seen = {}
    w = SimpleNamespace(config=kinds.validate_config("imap", {"use_google": True}), state={})
    box = _WatchImap()

    def connect(cfg, credential):
        seen["cfg"], seen["cred"] = cfg, credential
        return box

    result = kinds.probe_imap(w, lambda n: pytest.fail("no secret is read"), connect=connect, google_login=_login)
    assert isinstance(seen["cred"], kinds.GoogleToken)
    assert (seen["cred"].address, seen["cred"].token) == (ADDRESS, "tok")
    assert seen["cfg"]["username"] == ADDRESS
    assert result.summary == "no new messages" and box.logged_out


def test_watcher_probe_reports_a_missing_grant_and_a_wrong_mailbox():
    w = SimpleNamespace(config=kinds.validate_config("imap", {"use_google": True}), state={})
    with pytest.raises(kinds.ProbeError, match="not connected"):
        kinds.probe_imap(w, lambda n: None, connect=lambda c, p: _WatchImap())
    w2 = SimpleNamespace(config=kinds.validate_config("imap", {"use_google": True, "username": "x@gmail.com"}), state={})
    with pytest.raises(kinds.ProbeError, match=ADDRESS):
        kinds.probe_imap(w2, lambda n: None, connect=lambda c, p: _WatchImap(), google_login=_login)


def test_watcher_imap_connect_uses_xoauth2_for_a_google_token(monkeypatch):
    conn = _ImapConn()
    monkeypatch.setattr(kinds.imaplib, "IMAP4_SSL", lambda host, port, timeout=None: conn)
    kinds._imap_connect({"host": "imap.gmail.com", "port": 993, "ssl": True}, kinds.GoogleToken(ADDRESS, "tok"))
    assert conn.mechanism == "XOAUTH2" and not conn.logged_in


def test_only_an_administrator_may_create_a_google_watcher(monkeypatch):
    from routes import watchers as routes

    calls = []
    monkeypatch.setattr(routes.identity, "require_role", lambda principal, **kw: calls.append(kw))
    routes._require_google_admin(None, {"use_google": False, "host": "h"})
    routes._require_google_admin(None, None)
    assert calls == []
    routes._require_google_admin(None, {"use_google": True})
    routes._require_google_admin(None, {"use_google": "true"})
    assert calls == [{"admin": True}, {"admin": True}]


def test_watcher_kinds_carry_the_google_status():
    from routes import watchers as routes

    _connect()
    body = asyncio.run(routes.list_kinds())
    assert body["google"] == {"connected": True, "gmail": True, "account_email": ADDRESS}
    imap = next(k for k in body["kinds"] if k["kind"] == "imap")
    assert imap["fields"][0]["name"] == "use_google"
