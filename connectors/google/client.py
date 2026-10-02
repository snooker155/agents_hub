"""
GoogleClient — a thin ``httpx`` wrapper over the Drive, Docs, Sheets and
Calendar REST APIs, spending the token ``connectors/google/auth.py`` hands
out and mapping Google's error shapes to :class:`GoogleError` the same way
``connectors/git/providers.py`` maps GitHub/GitLab errors.

``tools/google_workspace.py`` is the only caller; this module knows nothing
about Drive files, spreadsheets or events, just how to make an authenticated
request and turn a bad response into a message the tool's JSON envelope can
carry back to the agent.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from . import auth
from .auth import GoogleError

_TIMEOUT = 30.0


class GoogleClient:
    """One client per call: a bearer token and no connection pool to keep
    warm, so there is nothing stale to hold across a long-lived agent
    process. Takes the token explicitly (rather than always calling
    :func:`connectors.google.auth.get_access_token` itself) so a caller that
    already has one on hand, or a test that monkeypatches
    ``connectors.google.auth.get_access_token``, can control it. The call
    below goes through the ``auth`` module object rather than a direct
    `from .auth import get_access_token`, so that monkeypatch is seen here
    too, instead of being shadowed by an import-time binding."""

    def __init__(self, token: Optional[str] = None):
        self.token = token if token is not None else auth.get_access_token()

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if extra:
            headers.update(extra)
        return headers

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise GoogleError("Google token expired or was revoked. Reconnect it on the Connectors page.")
        if resp.status_code == 403:
            raise GoogleError("Google denied access: a missing scope, or the item is not shared with this account.")
        if resp.status_code == 404:
            raise GoogleError("Google could not find that file, sheet, document or calendar.")
        if resp.status_code == 429:
            raise GoogleError("Google rate limited this request. Try again shortly.")
        if resp.status_code >= 400:
            detail = ""
            try:
                data = resp.json()
                err = data.get("error")
                detail = str((err or {}).get("message") or "") if isinstance(err, dict) else str(err or "")
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise GoogleError(f"Google API error {resp.status_code}{suffix}")
        return resp

    def get(self, url: str, *, params: Optional[dict[str, Any]] = None,
            headers: Optional[dict[str, str]] = None) -> httpx.Response:
        try:
            resp = httpx.get(url, headers=self._headers(headers), params=params, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google API request failed: {exc.__class__.__name__}") from exc
        return self._check(resp)

    def post(self, url: str, *, json: Optional[Any] = None, data: Optional[Any] = None,
             params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None) -> httpx.Response:
        try:
            resp = httpx.post(url, headers=self._headers(headers), params=params, json=json, data=data,
                              timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google API request failed: {exc.__class__.__name__}") from exc
        return self._check(resp)

    def put(self, url: str, *, json: Optional[Any] = None, data: Optional[Any] = None,
           params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None) -> httpx.Response:
        try:
            resp = httpx.put(url, headers=self._headers(headers), params=params, json=json, data=data,
                             timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google API request failed: {exc.__class__.__name__}") from exc
        return self._check(resp)

    def patch(self, url: str, *, json: Optional[Any] = None,
             params: Optional[dict[str, Any]] = None, headers: Optional[dict[str, str]] = None) -> httpx.Response:
        try:
            resp = httpx.patch(url, headers=self._headers(headers), params=params, json=json, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Google API request failed: {exc.__class__.__name__}") from exc
        return self._check(resp)


__all__ = ["GoogleClient"]
