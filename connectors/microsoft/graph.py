"""
A small client for Microsoft Graph: token acquisition plus GET/POST.

Used by ``tools/microsoft_graph.py`` (the Outlook calendar tools) and this
package's own ``test`` callback (``connectors/microsoft/__init__.py``).
Token acquisition goes through ``msal``'s ``ConfidentialClientApplication``
when it is importable, with the app object cached per (tenant, client_id,
client secret digest) so repeat calls do not re-authenticate and two
workspaces that registered the same app with different secrets never share
one; a plain ``httpx`` client-credentials
POST is the fallback, so the connector still works in an environment without
msal installed. ``acquire_token`` is a free module function rather than a
method, and ``GraphClient._request`` is the one place that leaves the
process, so a test can monkeypatch either cheaply instead of touching the
network (see tests/test_microsoft_graph.py).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Optional

import httpx

log = logging.getLogger("connectors.microsoft.graph")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_AUTHORIZE_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"

_app_cache: dict[tuple[str, str, str], Any] = {}


class GraphError(Exception):
    """A UI-safe error from a Microsoft Graph call."""


class GraphGrantRevoked(GraphError):
    """An end user's refresh token the identity platform no longer accepts."""


def _error_detail(body: Any) -> str:
    if isinstance(body, dict):
        return str(((body.get("error") or {}).get("message")) or "")
    return ""


def _status_message(status_code: int, body: Any) -> str:
    detail = _error_detail(body)
    suffix = f": {detail}" if detail else ""
    if status_code == 401:
        return f"Microsoft Graph token is invalid or expired{suffix}"
    if status_code == 403:
        return f"Microsoft Graph denied this request, a permission is likely missing{suffix}"
    if status_code == 404:
        return f"Microsoft Graph resource not found{suffix}"
    return f"Microsoft Graph error {status_code}{suffix}"


def _acquire_token_msal(tenant_id: str, client_id: str, client_secret: str) -> str:
    import msal

    key = (tenant_id, client_id, hashlib.sha256(client_secret.encode("utf-8")).hexdigest())
    app = _app_cache.get(key)
    if app is None:
        app = msal.ConfidentialClientApplication(
            client_id, authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        _app_cache[key] = app
    result = app.acquire_token_for_client(scopes=_SCOPES)
    token = result.get("access_token") if isinstance(result, dict) else None
    if not token:
        err = (result or {}).get("error_description") or (result or {}).get("error") or "no access_token returned"
        raise GraphError(f"could not acquire a Microsoft Graph token: {err}")
    return token


def _acquire_token_httpx(tenant_id: str, client_id: str, client_secret: str) -> str:
    url = _TOKEN_URL.format(tenant=tenant_id)
    data = {
        "grant_type": "client_credentials", "client_id": client_id,
        "client_secret": client_secret, "scope": _SCOPES[0],
    }
    resp = httpx.post(url, data=data, timeout=15.0)
    if resp.status_code != 200:
        raise GraphError(f"could not acquire a Microsoft Graph token: {resp.status_code} {resp.text[:200]}")
    token = resp.json().get("access_token")
    if not token:
        raise GraphError("token response carried no access_token")
    return token


def acquire_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """A Graph access token for this client-credentials app. msal if present, else httpx."""
    try:
        import msal  # noqa: F401
    except ImportError:
        return _acquire_token_httpx(tenant_id, client_id, client_secret)
    return _acquire_token_msal(tenant_id, client_id, client_secret)


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - an empty or non-JSON body reads as {}
        return {}


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, *, timeout: float = 30.0):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout

    def _token(self) -> str:
        return acquire_token(self.tenant_id, self.client_id, self.client_secret)

    def _request(self, method: str, path: str, *, params: Optional[dict[str, Any]] = None,
                json: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{_GRAPH_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._token()}"}
        resp = httpx.request(method, url, params=params, json=json, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise GraphError(_status_message(resp.status_code, _safe_json(resp)))
        if not resp.content:
            return {}
        return _safe_json(resp)

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self._request("POST", path, json=json)


# ── delegated access: an end user's own account (the consent portal) ───────
# The client-credentials app above acts as itself on any mailbox the tenant
# allows. connectors/consent/ also lets a widget visitor or a channel user
# sign in to the same app registration and grant delegated permissions on
# their own account: the identity platform's v2 authorization code flow, with
# offline_access for a refresh token and PKCE. ``consent_tenant`` on the
# connector picks the authority (``common``, ``organizations``, ``consumers``
# or a tenant id); empty means the app's own tenant, so only its users can
# sign in, which is the safe default for a single-tenant registration.
#
# The app registration is the one of the workspace the end user's turn runs
# in (its own Microsoft connector, else the default's): every function below
# takes that ``workspace``, the running code's when None.

def _app_config(workspace: Optional[str] = None) -> dict[str, str]:
    from . import store_for

    store = store_for(workspace)
    return {
        "tenant_id": str(store.get("tenant_id") or "").strip(),
        "client_id": str(store.get("client_id") or "").strip(),
        "client_secret": str(store.get("client_secret") or "").strip(),
        "consent_tenant": str(store.get("consent_tenant") or "").strip(),
    }


def _authority(cfg: dict[str, str]) -> str:
    return cfg["consent_tenant"] or cfg["tenant_id"] or "common"


def consent_ready(workspace: Optional[str] = None) -> bool:
    """Whether an app registration exists to ask end users with."""
    cfg = _app_config(workspace)
    return bool(cfg["client_id"] and cfg["client_secret"])


def build_consent_url(redirect_uri: str, state: str, scopes: list[str], *,
                      code_challenge: Optional[str] = None,
                      workspace: Optional[str] = None) -> str:
    from urllib.parse import urlencode

    cfg = _app_config(workspace)
    if not cfg["client_id"]:
        raise GraphError("No Microsoft app registration configured on the Connectors page")
    params = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": " ".join(scopes),
        "state": state,
        "prompt": "select_account",
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{_AUTHORIZE_URL.format(tenant=_authority(cfg))}?{urlencode(params)}"


def _token_request(data: dict[str, str], workspace: Optional[str] = None) -> dict[str, Any]:
    cfg = _app_config(workspace)
    if not (cfg["client_id"] and cfg["client_secret"]):
        raise GraphError("No Microsoft app registration configured on the Connectors page")
    body = {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], **data}
    try:
        resp = httpx.post(_TOKEN_URL.format(tenant=_authority(cfg)), data=body, timeout=30.0)
    except httpx.HTTPError as exc:
        raise GraphError(f"Microsoft token request failed: {exc.__class__.__name__}") from exc
    payload = _safe_json(resp)
    if resp.status_code >= 400:
        if str(payload.get("error") or "") == "invalid_grant":
            raise GraphGrantRevoked("Microsoft no longer accepts this account's access. Ask for access again.")
        raise GraphError(f"Microsoft token request failed ({resp.status_code})")
    if not payload.get("access_token"):
        raise GraphError("Microsoft token response carried no access token")
    return payload


def exchange_delegated_code(code: str, redirect_uri: str, scopes: list[str], *,
                            code_verifier: Optional[str] = None,
                            workspace: Optional[str] = None) -> dict[str, Any]:
    """The authorization code for tokens (``access_token``, ``refresh_token``,
    ``expires_in``, ``scope``)."""
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "scope": " ".join(scopes)}
    if code_verifier:
        data["code_verifier"] = code_verifier
    return _token_request(data, workspace)


def refresh_delegated(refresh_token: str, scopes: list[str], *,
                      workspace: Optional[str] = None) -> dict[str, Any]:
    """A fresh access token for an end user. The platform may rotate the
    refresh token: the caller stores ``refresh_token`` when one comes back."""
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token,
                           "scope": " ".join(scopes)}, workspace)


def fetch_me(access_token: str) -> dict[str, Any]:
    """The signed-in user's profile (``mail``, ``userPrincipalName``)."""
    try:
        resp = httpx.get(f"{_GRAPH_BASE}/me", headers={"Authorization": f"Bearer {access_token}"},
                         params={"$select": "mail,userPrincipalName,displayName"}, timeout=30.0)
    except httpx.HTTPError as exc:
        raise GraphError(f"Microsoft Graph request failed: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        raise GraphError(_status_message(resp.status_code, _safe_json(resp)))
    return _safe_json(resp)


class DelegatedGraphClient(GraphClient):
    """A Graph client acting as one end user, on their own mailbox only.

    ``token`` is a callable so the token is fetched (and a refusal raised) at
    the first call, inside the tool's own ``except GraphError``. The tools
    address a mailbox as ``/users/<mailbox>/...``; acting as the end user,
    ``me``, the account's own address and nothing else is accepted, rewritten
    to ``/me/...``, so an agent cannot reach another mailbox through an end
    user's grant even where their organization would share it.
    """

    def __init__(self, token: Callable[[], str], account_email: str = "", *,
                 timeout: float = 30.0):
        super().__init__("", "", "", timeout=timeout)
        self._token_fn = token
        self.account_email = (account_email or "").strip().lower()

    def _token(self) -> str:
        return self._token_fn()

    def _rewrite(self, path: str) -> str:
        if path.startswith("http") or not path.startswith("/users/"):
            return path
        rest = path[len("/users/"):]
        mailbox, sep, tail = rest.partition("/")
        if mailbox.lower() in ("me", self.account_email) and mailbox:
            return "/me" + (sep + tail if sep else "")
        raise GraphError("Acting for the person in this conversation, only their own mailbox "
                         "can be used. Leave the user out of the call.")

    def _request(self, method: str, path: str, *, params: Optional[dict[str, Any]] = None,
                 json: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return super()._request(method, self._rewrite(path), params=params, json=json)


__all__ = ["DelegatedGraphClient", "GraphClient", "GraphError", "GraphGrantRevoked",
           "acquire_token", "build_consent_url", "consent_ready", "exchange_delegated_code",
           "fetch_me", "refresh_delegated"]
