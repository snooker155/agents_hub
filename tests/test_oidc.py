"""Single sign-on against an in-process fake provider.

The provider is three dictionaries and an RSA key: a discovery document, a
JWKS, and a token endpoint that signs whatever id token the test queued for
the next exchange. ``common/oidc.py`` keeps its HTTP calls in two small
functions precisely so they can be swapped for these; everything else (the
signed state cookie, PKCE, verification, account linking, group mappings,
the session) runs for real.
"""
from __future__ import annotations

import base64
import hashlib
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

ISSUER = "https://idp.example.test/realms/corp"
CLIENT_ID = "agents-hub"
PASSWORD = "hunter2-but-longer"


# ── the fake provider ────────────────────────────────────────────────────────

class FakeProvider:
    def __init__(self):
        from authlib.jose import JsonWebKey
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        self.key = JsonWebKey.import_key(pem, {"kty": "RSA", "kid": "k1", "use": "sig",
                                               "alg": "RS256"})
        self.kid = "k1"
        self.jwks = {"keys": [self.key.as_dict(is_private=False)]}
        self.discovery = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
        }
        self.claims: dict = {}
        self.exchanges: list = []
        self.issued_codes: dict = {}

    def sign(self, claims: dict) -> str:
        from authlib.jose import jwt
        return jwt.encode({"alg": "RS256", "kid": self.kid}, claims, self.key).decode("ascii")

    # the two HTTP functions of common/oidc.py
    def get_json(self, url: str):
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return self.discovery
        if url == self.discovery["jwks_uri"]:
            return self.jwks
        raise AssertionError(f"unexpected GET {url}")

    def post_form(self, url, data, headers=None):
        assert url == self.discovery["token_endpoint"]
        self.exchanges.append(dict(data))
        pending = self.issued_codes.pop(data.get("code"), None)
        if pending is None:
            return {"error": "invalid_grant"}
        # PKCE: the verifier must hash to the challenge sent at /authorize.
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        if digest != pending["challenge"]:
            return {"error": "invalid_grant", "error_description": "PKCE mismatch"}
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                  "nonce": pending["nonce"], **self.claims}
        return {"access_token": "at", "token_type": "Bearer", "id_token": self.sign(claims)}

    def authorize(self, location: str) -> dict:
        """What the provider does at /authorize: remember the request, issue a code."""
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
        code = f"code-{len(self.issued_codes)}-{time.time_ns()}"
        self.issued_codes[code] = {"nonce": query["nonce"],
                                   "challenge": query["code_challenge"]}
        return {"code": code, "state": query["state"]}


@pytest.fixture
def provider(monkeypatch):
    from common import oidc
    from common.config import settings
    fake = FakeProvider()
    oidc.reset_caches()
    monkeypatch.setattr(oidc, "_http_get_json", fake.get_json)
    monkeypatch.setattr(oidc, "_http_post_form", fake.post_form)
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    monkeypatch.setattr(settings, "auth_oidc_issuer", ISSUER, raising=False)
    monkeypatch.setattr(settings, "auth_oidc_client_id", CLIENT_ID, raising=False)
    monkeypatch.setattr(settings, "auth_oidc_client_secret", "shh", raising=False)
    monkeypatch.setattr(settings, "auth_oidc_groups_claim", "groups", raising=False)
    monkeypatch.setattr(settings, "auth_oidc_link_by_email", True, raising=False)
    monkeypatch.setattr(settings, "auth_public_url", "http://testserver", raising=False)
    monkeypatch.setattr(settings, "auth_cookie_secure", "auto", raising=False)
    yield fake
    oidc.reset_caches()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app, follow_redirects=False)


def _fragment(location: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).fragment))


def _sign_in(client, provider, claims: dict, next_path: str = "/") -> dict:
    provider.claims = claims
    start = client.get("/api/auth/oidc/start", params={"next": next_path})
    assert start.status_code == 302, start.text
    back = provider.authorize(start.headers["location"])
    done = client.get("/api/auth/oidc/callback", params=back)
    assert done.status_code == 302, done.text
    assert done.headers["location"].startswith("http://testserver/login/oidc#")
    return _fragment(done.headers["location"])


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap_admin(client) -> dict:
    response = client.post("/api/auth/bootstrap",
                           json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


# ── start ────────────────────────────────────────────────────────────────────

def test_start_sets_the_cookie_and_redirects_with_pkce(provider, client):
    response = client.get("/api/auth/oidc/start", params={"next": "/tasks"})
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(provider.discovery["authorization_endpoint"] + "?")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == "http://testserver/api/auth/oidc/callback"
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) >= 43
    assert query["state"] and query["nonce"]
    assert "openid" in query["scope"].split()
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("ah_oidc=")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Path=/api/auth/oidc" in cookie
    # http public URL, auto: not Secure (the browser would drop it otherwise).
    assert "Secure" not in cookie


def test_cookie_is_secure_behind_https(provider, client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_public_url", "https://hub.example.test", raising=False)
    response = client.get("/api/auth/oidc/start")
    assert "Secure" in response.headers["set-cookie"]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(
        response.headers["location"]).query))
    assert query["redirect_uri"] == "https://hub.example.test/api/auth/oidc/callback"


def test_public_url_honours_forwarded_headers(provider, client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_public_url", "", raising=False)
    response = client.get("/api/auth/oidc/start", headers={
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "hub.corp.example"})
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(
        response.headers["location"]).query))
    assert query["redirect_uri"] == "https://hub.corp.example/api/auth/oidc/callback"
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("bad", [
    "https://evil.example/", "//evil.example/x", "/\\evil.example", "javascript:alert(1)",
    "relative/path",
])
def test_next_guard_rejects_absolute_urls(provider, client, bad):
    assert client.get("/api/auth/oidc/start", params={"next": bad}).status_code == 400


def test_start_is_404_without_oidc_or_outside_multi(provider, client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    assert client.get("/api/auth/oidc/start").status_code == 404
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "auth_oidc_issuer", "", raising=False)
    assert client.get("/api/auth/oidc/start").status_code == 404
    assert client.get("/api/auth/oidc/callback").status_code == 404


def test_mode_probe_offers_the_button(provider, client):
    body = client.get("/api/auth/mode").json()
    assert body["features"]["oidc"] is True
    assert body["oidc"]["start_url"] == "/api/auth/oidc/start"


# ── callback ─────────────────────────────────────────────────────────────────

def test_callback_with_a_bad_state_is_refused(provider, client):
    start = client.get("/api/auth/oidc/start")
    back = provider.authorize(start.headers["location"])
    back["state"] = "not-the-state"
    response = client.get("/api/auth/oidc/callback", params=back)
    assert response.status_code == 302
    assert _fragment(response.headers["location"]) == {"error": "bad_state"}
    assert provider.exchanges == []  # never reached the token endpoint


def test_callback_without_the_cookie_is_refused(provider, client):
    start = client.get("/api/auth/oidc/start")
    back = provider.authorize(start.headers["location"])
    client.cookies.clear()
    response = client.get("/api/auth/oidc/callback", params=back)
    assert _fragment(response.headers["location"])["error"] == "bad_state"


def test_a_forged_cookie_is_refused(provider, client):
    from common import oidc
    start = client.get("/api/auth/oidc/start")
    back = provider.authorize(start.headers["location"])
    forged = oidc.sign_state({"state": back["state"]}).split(".")[0] + ".AAAA"
    client.cookies.set(oidc.STATE_COOKIE, forged, path=oidc.STATE_COOKIE_PATH)
    response = client.get("/api/auth/oidc/callback", params=back)
    assert _fragment(response.headers["location"])["error"] == "bad_state"


def test_provider_error_lands_on_the_login_screen_and_is_audited(provider, client):
    response = client.get("/api/auth/oidc/callback",
                          params={"error": "access_denied", "error_description": "no"})
    assert _fragment(response.headers["location"]) == {"error": "provider_error"}
    from common import db
    row = db.get_conn().execute(
        "SELECT result, details FROM audit_log WHERE action = 'auth.login' "
        "ORDER BY id DESC").fetchone()
    assert row["result"] == "denied" and "provider_error" in row["details"]


def test_a_wrong_nonce_or_audience_fails_verification(provider, client):
    provider.claims = {"sub": "s-1", "preferred_username": "eve"}
    start = client.get("/api/auth/oidc/start")
    back = provider.authorize(start.headers["location"])
    provider.issued_codes[back["code"]]["nonce"] = "another-nonce"
    response = client.get("/api/auth/oidc/callback", params=back)
    assert _fragment(response.headers["location"])["error"] == "verification_failed"

    provider.claims = {"sub": "s-1", "preferred_username": "eve", "aud": "someone-else"}
    start = client.get("/api/auth/oidc/start")
    back = provider.authorize(start.headers["location"])
    response = client.get("/api/auth/oidc/callback", params=back)
    assert _fragment(response.headers["location"])["error"] == "verification_failed"


def test_good_callback_creates_the_user_links_groups_and_applies_mappings(provider, client):
    from common import groups
    _bootstrap_admin(client)
    groups.add_mapping("hub-admins", target="role", role="admin")
    groups.add_mapping("devs", target="workspace", role="editor", workspace="w1")

    fragment = _sign_in(client, provider, {
        "sub": "alice-sub", "preferred_username": "alice", "email": "Alice@Example.com",
        "name": "Alice Liddell", "groups": ["hub-admins", "devs"],
    }, next_path="/tasks?x=1")
    assert set(fragment) == {"token", "expires_at", "next"}
    assert fragment["next"] == "/tasks?x=1"
    # The token rides in the fragment only.
    me = client.get("/api/auth/me", headers=_bearer(fragment["token"]))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["username"] == "alice"
    assert body["role"] == "admin"
    assert body["via"] == "oidc"
    assert body["source"] == "oidc"
    assert body["email"] == "alice@example.com"
    assert body["display_name"] == "Alice Liddell"
    assert body["has_password"] is False
    assert sorted(body["groups"]) == ["devs", "hub-admins"]
    assert "w1" in body["workspaces"]

    from common import identity
    user = identity.get_user(body["id"])
    assert user["source"] == "oidc" and user["external_issuer"] == ISSUER
    assert identity.membership_role("w1", user["id"]) == "editor"
    # The OIDC session is the shorter one.
    from datetime import datetime, timezone
    left = datetime.fromisoformat(fragment["expires_at"]) - datetime.now(timezone.utc)
    assert left.total_seconds() <= 8 * 3600 + 5

    from common import db
    row = db.get_conn().execute(
        "SELECT actor_name, result, details FROM audit_log WHERE action = 'auth.login' "
        "AND result = 'ok' ORDER BY id DESC").fetchone()
    assert row["actor_name"] == "alice" and '"oidc"' in row["details"]


def test_second_login_of_the_same_sub_reuses_the_account(provider, client):
    claims = {"sub": "bob-sub", "preferred_username": "bob", "email": "bob@example.com",
              "groups": ["devs"]}
    first = _sign_in(client, provider, claims)
    second = _sign_in(client, provider, {**claims, "name": "Bob Renamed",
                                         "preferred_username": "robert"})
    a = client.get("/api/auth/me", headers=_bearer(first["token"])).json()
    b = client.get("/api/auth/me", headers=_bearer(second["token"])).json()
    assert a["id"] == b["id"]
    assert b["display_name"] == "Bob Renamed"
    from common import identity
    assert len([u for u in identity.list_users() if u["source"] == "oidc"]) == 1


def test_groups_follow_the_claim_and_a_missing_claim_changes_nothing(provider, client):
    from common import groups, identity
    admin = _bootstrap_admin(client)
    root = client.get("/api/auth/me", headers=admin).json()
    groups.add_mapping("hub-admins", target="role", role="admin")
    base = {"sub": "c-sub", "preferred_username": "carol"}
    token = _sign_in(client, provider, {**base, "groups": ["hub-admins"]})["token"]
    user_id = client.get("/api/auth/me", headers=_bearer(token)).json()["id"]
    assert identity.get_user(user_id)["role"] == "admin"

    # A manual group the provider never named survives every login.
    manual = groups.ensure_group("local-only")
    groups.add_group_member(manual["id"], user_id)

    _sign_in(client, provider, base)  # no groups claim at all
    assert sorted(groups.group_names_of_user(user_id)) == ["hub-admins", "local-only"]
    assert identity.get_user(user_id)["role"] == "admin"

    _sign_in(client, provider, {**base, "groups": []})  # present and empty
    assert groups.group_names_of_user(user_id) == ["local-only"]
    assert identity.get_user(user_id)["role"] == "member"
    assert identity.get_user(root["id"])["role"] == "admin"


def test_dotted_groups_claim_path(provider, client, monkeypatch):
    from common import groups
    from common.config import settings
    monkeypatch.setattr(settings, "auth_oidc_groups_claim", "realm_access.roles",
                        raising=False)
    token = _sign_in(client, provider, {
        "sub": "d-sub", "preferred_username": "dave",
        "realm_access": {"roles": ["devs", "/ops"]}})["token"]
    me = client.get("/api/auth/me", headers=_bearer(token)).json()
    assert sorted(groups.group_names_of_user(me["id"])) == ["devs", "ops"]


def test_username_falls_back_to_the_email_local_part(provider, client):
    token = _sign_in(client, provider, {"sub": "e-sub", "email": "erin.w@example.com"})["token"]
    me = client.get("/api/auth/me", headers=_bearer(token)).json()
    assert me["username"] == "erin.w"


def test_link_by_email_attaches_to_an_existing_local_account(provider, client):
    admin = _bootstrap_admin(client)
    created = client.post("/api/auth/users", json={
        "username": "frank", "password": PASSWORD, "email": "frank@example.com"},
        headers=admin)
    assert created.status_code == 200, created.text
    token = _sign_in(client, provider, {
        "sub": "frank-sub", "preferred_username": "f.smith",
        "email": "FRANK@example.com"})["token"]
    me = client.get("/api/auth/me", headers=_bearer(token)).json()
    assert me["id"] == created.json()["id"]
    assert me["username"] == "frank"
    assert me["has_password"] is True  # the local password still works


def test_link_by_email_off_creates_a_separate_account(provider, client, monkeypatch):
    from common.config import settings
    admin = _bootstrap_admin(client)
    created = client.post("/api/auth/users", json={
        "username": "gina", "password": PASSWORD, "email": "gina@example.com"},
        headers=admin).json()
    monkeypatch.setattr(settings, "auth_oidc_link_by_email", False, raising=False)
    token = _sign_in(client, provider, {"sub": "g-sub", "preferred_username": "gina",
                                        "email": "gina@example.com"})["token"]
    me = client.get("/api/auth/me", headers=_bearer(token)).json()
    assert me["id"] != created["id"]
    assert me["username"] == "gina-2"


def test_a_disabled_account_is_refused(provider, client):
    from common import identity
    admin = _bootstrap_admin(client)
    token = _sign_in(client, provider, {"sub": "h-sub", "preferred_username": "hank"})["token"]
    user_id = client.get("/api/auth/me", headers=_bearer(token)).json()["id"]
    assert client.patch(f"/api/auth/users/{user_id}", json={"disabled": True},
                        headers=admin).status_code == 200
    fragment = _sign_in(client, provider, {"sub": "h-sub", "preferred_username": "hank"})
    assert fragment == {"error": "disabled"}
    assert identity.get_user(user_id)["disabled"] is True


def test_a_code_is_exchanged_with_the_verifier_and_the_secret(provider, client):
    _sign_in(client, provider, {"sub": "i-sub", "preferred_username": "ivy"})
    exchange = provider.exchanges[-1]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["client_secret"] == "shh"
    assert exchange["redirect_uri"] == "http://testserver/api/auth/oidc/callback"
    assert len(exchange["code_verifier"]) >= 43


def test_key_rotation_refetches_the_jwks(provider, client):
    from common import oidc
    _sign_in(client, provider, {"sub": "j-sub", "preferred_username": "jo"})
    # Rotate: a new key with a new id; the cached set does not know it.
    from authlib.jose import JsonWebKey
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    provider.key = JsonWebKey.import_key(pem, {"kty": "RSA", "kid": "k2", "use": "sig"})
    provider.kid = "k2"
    provider.jwks = {"keys": [provider.key.as_dict(is_private=False)]}
    fragment = _sign_in(client, provider, {"sub": "j-sub", "preferred_username": "jo"})
    assert "token" in fragment
    assert oidc.jwks()["keys"][0]["kid"] == "k2"


def test_claim_helpers():
    from common import oidc
    assert oidc.claim_at({"a": {"b": [1]}}, "a.b") == [1]
    assert oidc.claim_at({"a.b": 2}, "a.b") == 2
    assert oidc.claim_at({}, "a.b") is oidc._MISSING
    who = oidc.identity_from_claims({"given_name": "Ann", "family_name": "Lee",
                                     "email": "Ann@X.io"})
    assert who == {"username": "ann", "email": "ann@x.io", "display_name": "Ann Lee"}
    assert oidc.safe_next("/a/b?c=d") == "/a/b?c=d"
    assert oidc.safe_next("") == "/"


# ── Entra ID groups overage ──────────────────────────────────────────────────

def test_groups_overage_is_detected_and_reported(provider, client):
    """Entra ID drops the groups claim past its cap and sends a Graph pointer
    instead. The hub follows nothing: the person's groups stay as they were,
    and the login row says why."""
    from common import db, groups, identity, oidc
    _bootstrap_admin(client)
    groups.add_mapping("devs", target="workspace", role="editor", workspace="w1")
    first = _sign_in(client, provider, {"sub": "carol-sub", "preferred_username": "carol",
                                        "groups": ["devs"]})
    user = identity.get_user_by_username("carol")
    assert identity.membership_role("w1", user["id"]) == "editor"

    overage_claims = {
        "sub": "carol-sub", "preferred_username": "carol",
        "_claim_names": {"groups": "src1"},
        "_claim_sources": {"src1": {"endpoint": "https://graph.microsoft.com/v1.0/users/x/getMemberObjects"}},
    }
    assert oidc.groups_overage(overage_claims) is True
    assert oidc.groups_from_claims(overage_claims) is None
    second = _sign_in(client, provider, overage_claims)
    assert second["token"] and first["token"] != second["token"]
    # Nothing changed: the membership the earlier claim granted is still there.
    assert identity.membership_role("w1", user["id"]) == "editor"
    row = db.get_conn().execute(
        "SELECT details FROM audit_log WHERE action = 'auth.login' AND result = 'ok' "
        "ORDER BY id DESC").fetchone()
    assert '"groups_overage": true' in row["details"]
