"""AUTH_OIDC_INTERNAL_URL: server-to-server calls go internal, the browser and
the issuer check stay public. The HTTP layer is replaced, no network."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PUBLIC = "https://hub.example.test:8081"
INTERNAL = "http://keycloak:8080"
ISSUER = f"{PUBLIC}/realms/agents-hub"


def _doc(origin):
    base = f"{origin}/realms/agents-hub/protocol/openid-connect"
    return {"issuer": ISSUER, "authorization_endpoint": f"{base}/auth",
            "token_endpoint": f"{base}/token", "jwks_uri": f"{base}/certs",
            "end_session_endpoint": f"{base}/logout"}


@pytest.fixture
def calls(monkeypatch):
    from ee import oidc
    from common.config import settings
    monkeypatch.setattr(settings, "auth_oidc_issuer", ISSUER, raising=False)
    monkeypatch.setattr(settings, "auth_oidc_client_id", "agents-hub", raising=False)
    oidc.reset_caches()
    log = {"get": [], "post": []}

    def get(url):
        log["get"].append(url)
        if url.endswith("openid-configuration"):
            # A dynamic-backchannel Keycloak describes itself at the asked address.
            return _doc(INTERNAL if url.startswith(INTERNAL) else PUBLIC)
        return {"keys": []}

    def post(url, data, headers=None):
        log["post"].append(url)
        return {"id_token": "x"}

    monkeypatch.setattr(oidc, "_http_get_json", get)
    monkeypatch.setattr(oidc, "_http_post_form", post)
    yield log
    oidc.reset_caches()


def _set_internal(monkeypatch, value):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_oidc_internal_url", value, raising=False)


class _Req:
    pass


def test_empty_setting_leaves_urls_alone(calls, monkeypatch):
    from ee import oidc
    _set_internal(monkeypatch, "")
    doc = oidc.discovery()
    oidc.jwks()
    assert calls["get"][0] == f"{ISSUER}/.well-known/openid-configuration"
    assert calls["get"][1] == doc["jwks_uri"]
    assert doc["authorization_endpoint"].startswith(PUBLIC)


def test_internal_origin_used_for_server_calls(calls, monkeypatch):
    from ee import oidc
    _set_internal(monkeypatch, INTERNAL + "/")
    doc = oidc.discovery()
    oidc.jwks()
    assert calls["get"][0] == f"{INTERNAL}/realms/agents-hub/.well-known/openid-configuration"
    assert calls["get"][1].startswith(INTERNAL) and calls["get"][1].endswith("/certs")
    monkeypatch.setattr(oidc, "redirect_uri", lambda r: "https://hub/cb")
    oidc.exchange_code(_Req(), "c", "v")
    assert calls["post"] == [f"{INTERNAL}/realms/agents-hub/protocol/openid-connect/token"]
    # Browser-facing values are public even though discovery came from inside.
    assert doc["authorization_endpoint"].startswith(PUBLIC)
    assert doc["end_session_endpoint"].startswith(PUBLIC)
    assert doc["issuer"] == ISSUER


def test_authorization_redirect_stays_public(calls, monkeypatch):
    from ee import oidc
    _set_internal(monkeypatch, INTERNAL)
    monkeypatch.setattr(oidc, "redirect_uri", lambda r: "https://hub/cb")
    url, _cookie = oidc.begin(_Req(), "/")
    assert url.startswith(f"{PUBLIC}/realms/agents-hub/protocol/openid-connect/auth?")


def test_iss_is_checked_against_public_issuer(calls, monkeypatch):
    from ee import oidc
    from authlib.jose import JsonWebKey, JsonWebToken
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    _set_internal(monkeypatch, INTERNAL)
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    key = JsonWebKey.import_key(pem, {"kty": "RSA", "kid": "k1"})
    jwks = {"keys": [key.as_dict(is_private=False)]}
    monkeypatch.setattr(oidc, "jwks", lambda force=False: jwks)

    def token(iss):
        now = int(time.time())
        payload = {"iss": iss, "aud": "agents-hub", "sub": "u", "nonce": "n",
                   "exp": now + 300, "iat": now}
        return JsonWebToken(["RS256"]).encode({"alg": "RS256", "kid": "k1"}, payload, key).decode()

    assert oidc.verify_id_token(token(ISSUER), "n")["sub"] == "u"
    with pytest.raises(oidc.OidcError):
        oidc.verify_id_token(token(f"{INTERNAL}/realms/agents-hub"), "n")
