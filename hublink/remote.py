"""
Calls from this hub to the hub it is linked to.

A thin layer over ``requests`` so that the link, the mirror and the node
client speak to the remote through one place, with one timeout policy and one
error type. Tests replace :data:`transport` with a function that drives a
``TestClient`` of the app, so a "remote" hub in the suite is this same
application and no socket is opened.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterator, Optional

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20.0
#: The long poll a node holds open; the server answers earlier when work lands.
POLL_TIMEOUT_SECONDS = 40.0


class RemoteError(Exception):
    """The remote hub refused or could not be reached. ``status`` is the HTTP
    status when there was one, 0 when the connection failed."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class Response:
    """What :data:`transport` returns: a status, headers, and the body either
    whole or as an iterator of byte pieces (for a streamed response)."""

    def __init__(self, status: int, headers: Dict[str, str], *,
                 content: Optional[bytes] = None,
                 chunks: Optional[Iterator[bytes]] = None) -> None:
        self.status = status
        self.headers = headers
        self._content = content
        self._chunks = chunks

    @property
    def content(self) -> bytes:
        if self._content is None:
            self._content = b"".join(self._chunks or ())
            self._chunks = None
        return self._content

    def iter_chunks(self) -> Iterator[bytes]:
        if self._chunks is not None:
            chunks, self._chunks = self._chunks, None
            return chunks
        return iter([self._content or b""])

    def json(self) -> Any:
        import json
        if not self.content:
            return None
        return json.loads(self.content.decode("utf-8"))


def _requests_transport(method: str, url: str, *, headers: Dict[str, str],
                        body: Optional[bytes], timeout: float, stream: bool) -> Response:
    import requests
    try:
        resp = requests.request(method, url, headers=headers, data=body,
                                timeout=timeout, stream=stream)
    except requests.exceptions.RequestException as exc:
        raise RemoteError(f"cannot reach {url}: {exc}") from exc
    hdrs = {k: v for k, v in resp.headers.items()}
    if stream:
        return Response(resp.status_code, hdrs, chunks=resp.iter_content(chunk_size=4096))
    return Response(resp.status_code, hdrs, content=resp.content)


#: ``transport(method, url, headers=, body=, timeout=, stream=) -> Response``.
transport: Callable[..., Response] = _requests_transport


def send(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
         json_body: Any = None, body: Optional[bytes] = None,
         timeout: float = TIMEOUT_SECONDS, stream: bool = False) -> Response:
    hdrs = dict(headers or {})
    data = body
    if json_body is not None:
        import json
        data = json.dumps(json_body, ensure_ascii=False, default=str).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    return transport(method, url, headers=hdrs, body=data, timeout=timeout, stream=stream)


def _detail(resp: Response) -> str:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - a body that is not JSON is reported as text
        data = None
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("error") or data.get("message")
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        if detail:
            return str(detail)
    text = resp.content[:300].decode("utf-8", "replace") if resp.content else ""
    return text or f"HTTP {resp.status}"


class Remote:
    """One remote hub, one credential."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        out = {"Accept": "application/json"}
        if self.token:
            out["Authorization"] = f"Bearer {self.token}"
        out.update(extra or {})
        return out

    def call(self, method: str, path: str, *, json_body: Any = None,
             body: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
             timeout: float = TIMEOUT_SECONDS, ok: tuple = (200, 201, 204),
             stream: bool = False) -> Response:
        url = f"{self.url}{path}"
        resp = send(method, url, headers=self._headers(headers), json_body=json_body,
                    body=body, timeout=timeout, stream=stream)
        if resp.status not in ok:
            raise RemoteError(f"{method} {path}: {_detail(resp)}", status=resp.status)
        return resp

    def get_json(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw).json()

    def post_json(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.call("POST", path, json_body=body if body is not None else {}, **kw).json()

    def put_json(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.call("PUT", path, json_body=body if body is not None else {}, **kw).json()

    # ── what the link needs ──────────────────────────────────────────────────

    def whoami(self) -> Dict[str, Any]:
        data = self.get_json("/api/auth/me")
        return data if isinstance(data, dict) else {}


__all__ = ["POLL_TIMEOUT_SECONDS", "TIMEOUT_SECONDS", "Remote", "RemoteError", "Response",
           "send", "transport"]
