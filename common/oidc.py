"""
OIDC single sign-on: Authorization Code with PKCE, against any provider that
publishes a discovery document (Keycloak, Microsoft Entra ID, Google).

Why it is built this way:

- **Code + PKCE, confidential or public.** The browser never sees a token
  from the provider: it carries a one-time code back to the hub, and the hub
  exchanges it server to server. PKCE is on even for a confidential client,
  because it is what makes a stolen code useless and costs nothing.
- **One signed cookie for the round trip.** ``state``, ``nonce`` and the PKCE
  verifier have to survive the trip to the provider and back. They go into a
  single short-lived cookie (HttpOnly, SameSite=Lax, ten minutes) signed with
  an HMAC, rather than into a server-side table: nothing to clean up, and any
  replica that shares the signing key can finish a flow another one started.
  The key is ``AGENTS_HUB_SECRET_KEY`` when set, else the OIDC client secret,
  else the process's service credential (``identity.service_token()``), in
  that order, so a multi-replica deployment has something shared to sign with
  and a single process always has something.
- **Only the id token is trusted, and only once verified**: signature against
  the provider's JWKS (refetched once when a key id is unknown, which is how
  key rotation shows up), ``iss``, ``aud``, ``exp`` and the ``nonce`` from the
  cookie, with a small leeway for clock skew.
- **The HTTP calls are small module functions** (:func:`_http_get_json`,
  :func:`_http_post_form`) using the standard library, so tests replace them
  with an in-process fake provider and the hub needs no HTTP client library
  for this. ``authlib`` is imported lazily, inside :func:`verify_id_token`, so
  a hub with OIDC off starts without it installed.

The session the hub opens afterwards is an ordinary opaque session
(``identity.open_session(kind="oidc")``), shorter than a password one: the
provider's own session makes signing in again silent, so asking often costs
the user nothing and bounds how long a deprovisioned account keeps access.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

#: Name of the round-trip cookie, scoped to the two OIDC routes.
STATE_COOKIE = "ah_oidc"
STATE_COOKIE_PATH = "/api/auth/oidc"
STATE_TTL_SECONDS = 600

#: How long a discovery document and a key set are reused before refetching.
DISCOVERY_TTL_SECONDS = 3600
#: Tolerated clock difference between the hub and the provider.
CLOCK_SKEW_SECONDS = 120
_HTTP_TIMEOUT = 10

_ALLOWED_ALGS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
                 "ES256", "ES384", "ES512"]

_discovery_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_jwks_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


class OidcError(Exception):
    """A failed sign-in. ``code`` is what the login screen is told."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


# ── configuration ────────────────────────────────────────────────────────────

def _settings():
    from common.config import settings
    return settings


def issuer() -> str:
    return (getattr(_settings(), "auth_oidc_issuer", "") or "").strip().rstrip("/")


def client_id() -> str:
    return (getattr(_settings(), "auth_oidc_client_id", "") or "").strip()


def client_secret() -> str:
    return (getattr(_settings(), "auth_oidc_client_secret", "") or "").strip()


def scopes() -> str:
    raw = (getattr(_settings(), "auth_oidc_scopes", "") or "openid profile email").split()
    if "openid" not in raw:
        raw.insert(0, "openid")
    return " ".join(raw)


def groups_claim() -> str:
    return (getattr(_settings(), "auth_oidc_groups_claim", "") or "").strip()


def enabled() -> bool:
    """OIDC is offered: ``multi`` mode with an issuer and a client id."""
    from common import identity
    from common.auth import MULTI
    return identity.current_mode() == MULTI and bool(issuer()) and bool(client_id())


def reset_caches() -> None:
    """Forget cached discovery documents and key sets (tests, key rotation)."""
    _discovery_cache.clear()
    _jwks_cache.clear()


# ── HTTP (monkeypatched in tests) ────────────────────────────────────────────

def _http_get_json(url: str) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _http_post_form(url: str, data: Dict[str, str],
                    headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    all_headers = {"Accept": "application/json",
                   "Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    request = urllib.request.Request(url, data=body, headers=all_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The provider explains a refused exchange in the body; keep it.
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"error": f"http_{exc.code}"}


# ── discovery and keys ───────────────────────────────────────────────────────

def discovery(force: bool = False) -> Dict[str, Any]:
    """The provider's ``.well-known/openid-configuration``, cached."""
    iss = issuer()
    cached = _discovery_cache.get(iss)
    if cached and not force and time.time() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    doc = _http_get_json(f"{iss}/.well-known/openid-configuration")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise OidcError("provider_unreachable", f"discovery document lacks {key}")
    _discovery_cache[iss] = (time.time(), doc)
    return doc


def jwks(force: bool = False) -> Dict[str, Any]:
    """The provider's signing keys, cached; ``force`` refetches."""
    uri = discovery()["jwks_uri"]
    cached = _jwks_cache.get(uri)
    if cached and not force and time.time() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    keys = _http_get_json(uri)
    _jwks_cache[uri] = (time.time(), keys)
    return keys


# ── the public URL and the redirect ──────────────────────────────────────────

def public_url(request) -> str:
    """Where the browser reaches this hub: ``AUTH_PUBLIC_URL``, else derived
    from the request, honouring ``X-Forwarded-Proto`` and ``X-Forwarded-Host``
    from a reverse proxy."""
    configured = (getattr(_settings(), "auth_public_url", "") or "").strip().rstrip("/")
    if configured:
        return configured
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
    proto = proto or request.url.scheme
    host = host or headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def redirect_uri(request) -> str:
    return f"{public_url(request)}/api/auth/oidc/callback"


def cookie_secure(request) -> bool:
    """Whether the round-trip cookie is marked Secure (``AUTH_COOKIE_SECURE``)."""
    raw = str(getattr(_settings(), "auth_cookie_secure", "auto") or "auto").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    return public_url(request).startswith("https://")


def safe_next(value: Optional[str]) -> str:
    """A post-login destination: a path on this hub, never another site.

    Raises ValueError for anything with a scheme, a host (``//evil``, and the
    ``/\\evil`` spelling browsers read the same way) or no leading slash.
    """
    value = (value or "").strip()
    if not value:
        return "/"
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme or parsed.netloc or not value.startswith("/")
            or value.startswith("//") or value.startswith("/\\") or "\\" in value[:2]
            or any(ord(c) < 32 for c in value)):
        raise ValueError("next must be a relative path on this hub")
    return value


# ── the signed round-trip cookie ─────────────────────────────────────────────

def _signing_key() -> bytes:
    from common import identity
    settings = _settings()
    material = ((getattr(settings, "secret_key", "") or "").strip()
                or client_secret()
                or identity.service_token())
    return hashlib.sha256(b"agents-hub-oidc-state:" + material.encode("utf-8")).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(payload: Dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def read_state(value: Optional[str]) -> Dict[str, Any]:
    """The cookie's payload, or OidcError("bad_state") when it is missing,
    forged or expired."""
    if not value or "." not in value:
        raise OidcError("bad_state", "no sign-in in progress")
    body, mac = value.rsplit(".", 1)
    expected = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
    try:
        presented = _unb64(mac)
    except Exception:
        raise OidcError("bad_state", "malformed state cookie")
    if not hmac.compare_digest(expected, presented):
        raise OidcError("bad_state", "state cookie signature mismatch")
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
    except Exception:
        raise OidcError("bad_state", "malformed state cookie")
    if float(payload.get("exp", 0)) < time.time():
        raise OidcError("bad_state", "the sign-in took too long")
    return payload


def _pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ── the flow ─────────────────────────────────────────────────────────────────

def begin(request, next_path: str) -> Tuple[str, str]:
    """Start a sign-in: returns (the provider URL to send the browser to, the
    signed cookie value to set)."""
    doc = discovery()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    cookie = sign_state({"state": state, "nonce": nonce, "verifier": verifier,
                         "next": next_path, "exp": int(time.time()) + STATE_TTL_SECONDS})
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri(request),
        "scope": scopes(),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    endpoint = doc["authorization_endpoint"]
    sep = "&" if "?" in endpoint else "?"
    return f"{endpoint}{sep}{urllib.parse.urlencode(params)}", cookie


def exchange_code(request, code: str, verifier: str) -> Dict[str, Any]:
    """Trade the code for tokens at the provider's token endpoint."""
    doc = discovery()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(request),
        "client_id": client_id(),
        "code_verifier": verifier,
    }
    headers: Dict[str, str] = {}
    secret = client_secret()
    if secret:
        methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        if "client_secret_post" in methods or "client_secret_basic" not in methods:
            data["client_secret"] = secret
        else:
            pair = (f"{urllib.parse.quote(client_id(), safe='')}:"
                    f"{urllib.parse.quote(secret, safe='')}")
            headers["Authorization"] = "Basic " + base64.b64encode(
                pair.encode("utf-8")).decode("ascii")
    try:
        tokens = _http_post_form(doc["token_endpoint"], data, headers)
    except Exception as exc:
        raise OidcError("exchange_failed", f"token endpoint unreachable: {exc}")
    if not isinstance(tokens, dict) or tokens.get("error") or not tokens.get("id_token"):
        detail = (tokens or {}).get("error_description") or (tokens or {}).get("error")
        raise OidcError("exchange_failed", str(detail or "no id_token in the response"))
    return tokens


def verify_id_token(id_token: str, nonce: str) -> Dict[str, Any]:
    """Verify signature, issuer, audience, expiry and nonce; returns the claims."""
    from authlib.jose import JsonWebKey, JsonWebToken
    from authlib.jose.errors import JoseError

    jwt = JsonWebToken(_ALLOWED_ALGS)
    expected_iss = discovery().get("issuer") or issuer()
    options = {
        "iss": {"essential": True, "value": expected_iss},
        "aud": {"essential": True, "value": client_id()},
        "exp": {"essential": True},
        "sub": {"essential": True},
        "nonce": {"essential": True, "value": nonce},
    }

    def _decode(keys: Dict[str, Any]):
        return jwt.decode(id_token, JsonWebKey.import_key_set(keys), claims_options=options)

    try:
        try:
            claims = _decode(jwks())
        except (ValueError, JoseError):
            # Decoding checks only the signature (claims are validated below),
            # so a failure here is most often a key id this cached set does
            # not know yet, which is what a rotation looks like: refetch once.
            claims = _decode(jwks(force=True))
        claims.validate(now=int(time.time()), leeway=CLOCK_SKEW_SECONDS)
    except (ValueError, JoseError) as exc:
        raise OidcError("verification_failed", str(exc))
    data = dict(claims)
    aud = data.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and data.get("azp") not in (None, client_id()):
        raise OidcError("verification_failed", "id token issued to another party")
    return data


# ── reading the claims ───────────────────────────────────────────────────────

_MISSING = object()


def claim_at(claims: Dict[str, Any], path: str) -> Any:
    """A claim by dotted path (``realm_access.roles``); ``_MISSING`` when any
    step is absent. A claim whose own name contains dots wins when present."""
    if path in claims:
        return claims[path]
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def groups_overage(claims: Dict[str, Any]) -> bool:
    """Whether the provider left the groups out because there were too many.

    Microsoft Entra ID caps the groups claim (about 200 groups in an id
    token). Past that it sends no ``groups`` claim at all but a pointer to
    Microsoft Graph instead: ``_claim_names`` maps the claim name to a source
    listed under ``_claim_sources``. The hub does not follow the pointer (it
    would need Graph permissions of its own); it reports the case so the
    administrator can restrict the claim to the groups assigned to the
    application, which is the Entra setting that keeps it under the cap
    (docs/sso.md).
    """
    names = claims.get("_claim_names")
    if not isinstance(names, dict):
        return False
    leaf = (groups_claim() or "groups").split(".")[-1]
    return leaf in names or "groups" in names


def groups_from_claims(claims: Dict[str, Any]) -> Optional[List[str]]:
    """Group names from the configured claim, or None when the claim is absent
    (which means "the provider says nothing", not "no groups")."""
    path = groups_claim()
    if not path:
        return None
    value = claim_at(claims, path)
    if value is _MISSING or value is None:
        return None
    if isinstance(value, str):
        value = [v for v in value.replace(",", " ").split() if v]
    if not isinstance(value, (list, tuple)):
        return None
    names = []
    for item in value:
        name = str(item).strip()
        # Keycloak with "full group path" on sends /parent/child: keep the
        # leaf only when it is a bare top-level path, so /devs matches devs.
        if name.startswith("/") and name.count("/") == 1:
            name = name[1:]
        if name:
            names.append(name)
    return names


def identity_from_claims(claims: Dict[str, Any]) -> Dict[str, str]:
    """username, email and display name as the hub stores them."""
    email = str(claims.get("email") or "").strip().lower()
    username = str(claims.get("preferred_username") or "").strip()
    if not username and email:
        username = email.split("@", 1)[0]
    if "@" in username and not email:
        email = username.lower()
    username = "".join(c for c in username if not c.isspace()).lower()
    name = str(claims.get("name") or "").strip()
    if not name:
        name = " ".join(str(claims.get(k) or "").strip()
                        for k in ("given_name", "family_name")).strip()
    return {"username": username, "email": email, "display_name": name or username}


def sync_groups(user_id: str, names: List[str]) -> List[str]:
    """Make the provider's word on groups true for this account.

    ``groups.set_user_groups`` replaces *every* membership, which would also
    wipe what an administrator granted by hand in a group the provider has
    never heard of. So the rule is: a group the provider names becomes the
    provider's (a group first created by hand under the same name is adopted,
    its ``source`` flipped to ``oidc``), and from then on its membership
    follows the claim; a manual or SCIM group the provider never named keeps
    its members. Returns the names the user is in afterwards.
    """
    from common import db, groups
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    for name in wanted:
        group = groups.get_group_by_name(name)
        if group is not None and group["source"] == groups.SOURCE_MANUAL:
            with db.transaction() as conn:
                conn.execute("UPDATE groups SET source = ? WHERE group_id = ?",
                             ("oidc", group["id"]))
    kept = [g["name"] for g in groups.groups_of_user(user_id)
            if g["source"] != "oidc" and g["name"] not in wanted]
    return groups.set_user_groups(user_id, sorted(wanted) + kept, source="oidc")


def complete(request, *, code: str, state: str, cookie_value: Optional[str]) -> Dict[str, Any]:
    """Finish a sign-in up to verified claims.

    Returns ``{"claims", "next", "issuer", "subject", "username", "email",
    "display_name", "groups"}`` (``groups`` None when the claim was absent).
    Raises :class:`OidcError` for anything the login screen should report.
    """
    saved = read_state(cookie_value)
    if not state or not hmac.compare_digest(str(saved.get("state", "")), str(state)):
        raise OidcError("bad_state", "state does not match this browser's sign-in")
    if not code:
        raise OidcError("bad_state", "no code in the callback")
    tokens = exchange_code(request, code, str(saved.get("verifier", "")))
    claims = verify_id_token(tokens["id_token"], str(saved.get("nonce", "")))
    who = identity_from_claims(claims)
    return {
        "claims": claims,
        "next": saved.get("next") or "/",
        "issuer": str(claims.get("iss") or issuer()),
        "subject": str(claims["sub"]),
        "groups": groups_from_claims(claims),
        **who,
    }


__all__ = [
    "CLOCK_SKEW_SECONDS", "OidcError", "STATE_COOKIE", "STATE_COOKIE_PATH",
    "STATE_TTL_SECONDS", "begin", "claim_at", "client_id", "complete", "cookie_secure",
    "discovery", "enabled", "exchange_code", "groups_from_claims", "groups_overage", "identity_from_claims",
    "issuer", "jwks", "public_url", "read_state", "redirect_uri", "reset_caches",
    "safe_next", "sign_state", "sync_groups", "verify_id_token",
]
