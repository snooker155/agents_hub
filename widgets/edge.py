"""
The widget's edge: ``/widget.js`` and CORS for the public widget paths.

Registered in dashboard/backend/main.py as the outermost middleware, so it
sees a request before the hub's own CORS middleware and after it on the way
out. Two jobs:

``/widget.js``
    The one address a site owner's snippet names. It is rewritten to the
    route that serves the script (``/api/widgets/public/widget.js``), which
    also answers under ``/api``, so the dashboard's preview works behind the
    development server's ``/api`` proxy too.

CORS on ``/api/widgets/public/{widget_id}/...``
    Visitors' browsers call these from the embedding site, cross origin. The
    hub's global CORS setup (``ALLOW_ORIGINS``) is for the dashboard and must
    stay as narrow as it is, so it is not widened: these paths get their own
    answer, computed per widget from its allowed origins
    (:func:`widgets.service.cors_origin`), never with credentials. A
    preflight is answered here and never reaches the global middleware. On
    an actual request, whatever CORS headers the global middleware added
    (it allows credentials for the dashboard's origins) are replaced by the
    widget's own. The route checks the origin again and refuses it; this
    layer only decides what a browser is allowed to read.
"""
from __future__ import annotations

import logging
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, Response

log = logging.getLogger(__name__)

PUBLIC_PREFIX = "/api/widgets/public"
SCRIPT_PATH = "/widget.js"
SCRIPT_ROUTE = PUBLIC_PREFIX + "/widget.js"

ALLOWED_METHODS = "GET, POST, DELETE, OPTIONS"
ALLOWED_HEADERS = "Content-Type, X-Widget-Key, X-Visitor-Token, X-Widget-Preview"
EXPOSED_HEADERS = "Retry-After"
PREFLIGHT_MAX_AGE = "600"


def _widget_id(path: str) -> str:
    rest = path[len(PUBLIC_PREFIX) + 1:]
    return rest.split("/", 1)[0]


def _allowed_origin(widget_id: str, origin: str, headers: Headers, scheme: str) -> Any:
    from . import service, store
    try:
        widget = store.get_widget(widget_id)
    except Exception:  # noqa: BLE001 - an unreadable widget gets no CORS headers, the route answers the error
        log.warning("widget edge: could not load %s", widget_id, exc_info=True)
        return None
    return service.cors_origin(widget, origin, headers, scheme)


class WidgetEdgeMiddleware:
    """Pure ASGI, so a streamed turn passes through untouched."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path == SCRIPT_PATH:
            scope = dict(scope, path=SCRIPT_ROUTE, raw_path=SCRIPT_ROUTE.encode("ascii"))
            await self.app(scope, receive, send)
            return
        if not path.startswith(PUBLIC_PREFIX + "/"):
            await self.app(scope, receive, send)
            return
        widget_id = _widget_id(path)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if not widget_id or widget_id == "widget.js" or not origin:
            # The script itself is loaded by a <script> tag (no CORS), and a
            # request with no Origin has nothing to answer CORS for.
            await self.app(scope, receive, send)
            return
        scheme = scope.get("scheme") or "http"
        allowed = _allowed_origin(widget_id, origin, headers, scheme)

        if scope.get("method") == "OPTIONS" and headers.get("access-control-request-method"):
            if not allowed:
                response: Response = PlainTextResponse("This origin may not embed the widget",
                                                       status_code=403, headers={"Vary": "Origin"})
            else:
                response = Response(status_code=204, headers={
                    "Access-Control-Allow-Origin": allowed,
                    "Access-Control-Allow-Methods": ALLOWED_METHODS,
                    "Access-Control-Allow-Headers": ALLOWED_HEADERS,
                    "Access-Control-Max-Age": PREFLIGHT_MAX_AGE,
                    "Vary": "Origin",
                })
            await response(scope, receive, send)
            return

        async def send_with_cors(message) -> None:
            if message.get("type") == "http.response.start":
                out = MutableHeaders(scope=message)
                for name in {k.lower() for k in out.keys()}:
                    if name.startswith("access-control-"):
                        del out[name]
                if allowed:
                    out["Access-Control-Allow-Origin"] = allowed
                    out["Access-Control-Expose-Headers"] = EXPOSED_HEADERS
                out.append("Vary", "Origin")
            await send(message)

        await self.app(scope, receive, send_with_cors)


__all__ = ["PUBLIC_PREFIX", "SCRIPT_PATH", "SCRIPT_ROUTE", "WidgetEdgeMiddleware"]
