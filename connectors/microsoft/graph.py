"""
A small client for Microsoft Graph: token acquisition plus GET/POST.

Used by ``tools/microsoft_graph.py`` (the Outlook calendar tools) and this
package's own ``test`` callback (``connectors/microsoft/__init__.py``).
Token acquisition goes through ``msal``'s ``ConfidentialClientApplication``
when it is importable, with the app object cached per (tenant, client_id) so
repeat calls do not re-authenticate; a plain ``httpx`` client-credentials
POST is the fallback, so the connector still works in an environment without
msal installed. ``acquire_token`` is a free module function rather than a
method, and ``GraphClient._request`` is the one place that leaves the
process, so a test can monkeypatch either cheaply instead of touching the
network (see tests/test_microsoft_graph.py).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

log = logging.getLogger("connectors.microsoft.graph")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

_app_cache: dict[tuple[str, str], Any] = {}


class GraphError(Exception):
    """A UI-safe error from a Microsoft Graph call."""


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

    key = (tenant_id, client_id)
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


__all__ = ["GraphClient", "GraphError", "acquire_token"]
