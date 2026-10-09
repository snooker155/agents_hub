"""Project and container preview through the hub: short-lived tickets under
/api/preview and the proxied pages under /preview/<ticket>/. Filled in by
feature 7a; see docs/containers.md and docs/projects.md.

Why a ticket and not a direct iframe: an iframe navigation cannot carry the
``Authorization: Bearer`` header the rest of the API uses, and the proxied
page must not share an origin with the dashboard in a way that lets it read
the hub's own tokens out of ``localStorage``. So the dashboard mints a
short-lived ticket with an authenticated call
(``POST /api/preview/tickets``), the iframe loads ``/preview/<ticket>/<path>``
(outside ``/api``, an open path: the ticket in the path is the credential,
see ``common/preview_tickets.py``), and the iframe runs with
``sandbox="allow-scripts allow-forms allow-popups allow-modals"`` without
``allow-same-origin``, so the previewed page executes in an opaque origin and
cannot touch the dashboard's storage.

``_validate_and_resolve`` and the pinned transport used to actually reach the
target are reused from ``projects.proxy_service`` rather than duplicated:
that module already has the full SSRF guard (every resolved address must be
public, the local-machine exception for a project backend on the operator's
own host, connection pinning against DNS rebinding, and the container host
rewrite) and there is no reason for a second copy of it here.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, Literal, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from common import access, identity
from common.auth import LOCAL_OPERATOR_ID, MULTI
from common.paths import PROJECTS_FILE
from common import preview_tickets
from projects.proxy_service import _PinnedTransport, _validate_and_resolve  # noqa: PLC2701 - reuse by design, see module docstring
from projects.storage import ProjectStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/preview", tags=["preview"])
# The proxied pages: outside /api on purpose, a ticket in the path is the credential.
public_router = APIRouter(prefix="/preview", tags=["preview"])

# A second store instance is fine: it is a thin wrapper over the shared
# ``projects`` document collection in the database (projects/storage.py), not
# in-process state, so this reads exactly what routes/projects.py's own
# ``_store`` would.
_projects = ProjectStore(path=PROJECTS_FILE)


# ── minting ──────────────────────────────────────────────────────────────────

class PreviewTicketRequest(BaseModel):
    kind: Literal["container", "project", "deployment"]
    name: Optional[str] = None
    project_id: Optional[str] = None
    deployment_id: Optional[str] = None
    service: Optional[str] = None


def _container_target(name: str) -> Optional[Tuple[str, Optional[str]]]:
    """``(http_url, workspace)`` for a *running* container with an http_url,
    or ``None`` when the name is unknown, stopped, or has nothing exposed.

    Mirrors the enrichment ``GET /api/containers`` already does (merging the
    resident instance's ``http_url``/``workspace`` onto the container's own
    running/stopped state), rather than importing that route: the two only
    share a shape, not code, so a container test can monkeypatch the manager
    functions directly.
    """
    from instances import carrier
    from managers import container_manager
    instances_by_container = {
        i.get("container_name"): i
        for i in carrier.list_resident()
        if i.get("container_name")
    }
    for c in container_manager.list_containers():
        if c.get("name") != name:
            continue
        instance = instances_by_container.get(name) or {}
        state = str(c.get("state") or c.get("status") or "").lower()
        if state != "running":
            return None
        http_url = instance.get("http_url") or c.get("http_url")
        if not http_url:
            return None
        return http_url, instance.get("workspace")
    return None


def _project_target(project_id: str) -> Optional[Tuple[str, Optional[str]]]:
    """``(frontend_url, workspace)`` for a project with a preview URL or
    port, or ``None`` when the project is unknown or has neither."""
    project = _projects.get(project_id)
    if project is None:
        return None
    frontend = project.frontend
    if frontend.url:
        url = frontend.url
    elif frontend.port:
        url = f"http://localhost:{frontend.port}"
    else:
        return None
    return url, project.workspace


def _deployment_target(target_id: str) -> Optional[Tuple[str, Optional[str]]]:
    """``(service_url, workspace)`` for a running service of a project
    deployment (deployments/service.py). ``target_id`` is the deployment id,
    optionally ``<id>/<service>``; without a service the primary one."""
    from deployments import service as deployments, store as dstore
    dep_id, _, service_name = target_id.partition("/")
    dep = dstore.get(dep_id)
    if dep is None:
        return None
    url = deployments.target_url(dep, service_name or None)
    if not url:
        return None
    return url, dep.workspace


def _resolve_target(kind: str, target_id: str) -> Optional[Tuple[str, Optional[str]]]:
    if kind == "container":
        return _container_target(target_id)
    if kind == "project":
        return _project_target(target_id)
    if kind == "deployment":
        return _deployment_target(target_id)
    return None


@router.post("/tickets")
async def create_preview_ticket(payload: PreviewTicketRequest, request: Request):
    """Mint a ticket for a running container or a project's frontend.

    Authorization beyond the standard guard: in ``multi`` mode, the caller
    must be able to see the target's workspace (``common.access``, the same
    record-level visibility check a get/delete route uses once it has loaded
    a record and knows its workspace); an admin, the local operator, the
    shared token and the service credential always pass. 404 when the
    container is not running or exposes nothing, or the project has no
    preview URL or port configured.
    """
    principal = identity.request_principal(request)

    if payload.kind == "container":
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required for a container preview")
        resolved = _container_target(name)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"container '{name}' is not running or exposes no http_url")
        target_id = name
    elif payload.kind == "deployment":
        dep_id = (payload.deployment_id or "").strip()
        if not dep_id:
            raise HTTPException(status_code=400,
                               detail="deployment_id is required for a deployment preview")
        target_id = f"{dep_id}/{payload.service.strip()}" if payload.service else dep_id
        resolved = _deployment_target(target_id)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail="the deployment is not running or has no such service")
    else:
        project_id = (payload.project_id or "").strip()
        if not project_id:
            raise HTTPException(status_code=400,
                               detail="project_id is required for a project preview")
        resolved = _project_target(project_id)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"project '{project_id}' has no preview url or port configured")
        target_id = project_id

    _url, workspace = resolved
    access.require_visible(principal, workspace)
    return _minted(payload.kind, target_id, principal)


def _minted(kind: str, target_id: str, principal) -> dict:
    principal_id = principal.id if principal else LOCAL_OPERATOR_ID
    ttl = preview_tickets.DEFAULT_TTL_SECONDS
    ticket = preview_tickets.mint({"kind": kind, "id": target_id},
                                  principal_id=principal_id, ttl_seconds=ttl)
    return {"url": f"/preview/{ticket}/", "expires_in": ttl,
            "expires_at": time.time() + ttl}


class PreviewRenewRequest(BaseModel):
    ticket: Optional[str] = None
    kind: Optional[Literal["container", "project", "deployment"]] = None
    name: Optional[str] = None
    project_id: Optional[str] = None
    deployment_id: Optional[str] = None
    service: Optional[str] = None


@router.post("/tickets/renew")
async def renew_preview_ticket(payload: PreviewRenewRequest, request: Request):
    """A fresh ticket for a preview that is still open.

    The body is the current ticket (``{ticket}``) or, when that one has
    already lapsed, the same target body ``POST /tickets`` takes. A ticket
    renews only for the user it was minted for (an administrator may renew
    anyone's), and the target is re-resolved and its workspace re-checked,
    so a renewal never outlives the caller's access. The dashboard calls
    this every few minutes while a preview is on screen and swaps the
    iframe only when the old ticket is about to run out.
    """
    principal = identity.request_principal(request)
    current = preview_tickets.verify(payload.ticket) if payload.ticket else None
    if current is not None:
        if (identity.current_mode() == MULTI and principal is not None
                and not principal.is_admin and current.get("user") != principal.id):
            raise HTTPException(status_code=403, detail="This preview ticket is not yours")
        kind, target_id = current["kind"], current["id"]
    elif payload.kind:
        kind = payload.kind
        if kind == "container":
            target_id = (payload.name or "").strip()
        elif kind == "deployment":
            target_id = (payload.deployment_id or "").strip()
            if target_id and payload.service:
                target_id = f"{target_id}/{payload.service.strip()}"
        else:
            target_id = (payload.project_id or "").strip()
        if not target_id:
            raise HTTPException(status_code=400, detail="name or project_id is required")
    else:
        raise HTTPException(status_code=403, detail="The preview ticket has expired")
    resolved = _resolve_target(kind, target_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="The preview target is no longer available")
    access.require_visible(principal, resolved[1])
    return _minted(kind, target_id, principal)


# ── the proxy itself ─────────────────────────────────────────────────────────

_EXPIRED_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Preview link expired</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:48px;color:#555">
<p>This preview link has expired or the target is no longer available.</p>
<p>Reopen it from the hub.</p>
<script>try { parent.postMessage({source: "agents-hub-preview", type: "expired"}, "*"); } catch (e) {}</script>
</body></html>
"""

#: Request headers safe to forward to the previewed target. Cookie is
#: deliberately not in this list: the dashboard's own session cookie (if any)
#: must never reach a proxied third party, and the ticket in the path is
#: already the credential the target needs.
_FORWARD_REQUEST_HEADERS = ("accept", "content-type", "accept-language", "user-agent")

#: Response headers stripped unconditionally: framing/hop-by-hop headers that
#: do not apply to a re-served response, and content-encoding because httpx
#: already transparently decodes the body before we ever see it, so
#: forwarding the original encoding header alongside decoded bytes would
#: break the browser's own decoding.
_HOP_BY_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length",
                       "content-encoding"}
#: Stripped because they are either the dashboard's business, not the
#: proxied target's (framing the page), or must never leave this process
#: (a cookie set for the upstream's own origin has no business in the
#: browser's cookie jar for ours).
_UPSTREAM_DROP_HEADERS = {"set-cookie", "content-security-policy", "x-frame-options"}
_ADDED_RESPONSE_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "frame-ancestors 'self'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_SCRIPT_RE = re.compile(rb"(<script\b[^>]*>.*?</script>)", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(rb'\b(href|src|action)="/(?!/)', re.IGNORECASE)
_HEAD_RE = re.compile(rb"<head[^>]*>", re.IGNORECASE)
_BASE_RE = re.compile(rb"<base\b", re.IGNORECASE)


def _client_factory(pinned_ip: Optional[str]) -> httpx.AsyncClient:
    """Build the client used to reach the proxied target.

    A separate function, not an inline ``httpx.AsyncClient(...)``, so a test
    can monkeypatch it to build a client over ``httpx.MockTransport`` instead
    of opening a real connection. ``follow_redirects=False``: a redirect is
    returned to the browser instead (with its Location rewritten, see
    ``_rewrite_location``), the same "never let the library follow it
    blindly" reasoning ``projects.proxy_service.proxy_api_request`` documents.
    """
    transport = _PinnedTransport(pinned_ip) if pinned_ip else None
    return httpx.AsyncClient(timeout=30.0, transport=transport, follow_redirects=False)


def _forward_request_headers(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in _FORWARD_REQUEST_HEADERS:
        value = headers.get(name)
        if value:
            out[name] = value
    return out


def _upstream_url(base: str, path: str, query: str) -> str:
    root = base if base.endswith("/") else base + "/"
    url = urljoin(root, path.lstrip("/"))
    if query:
        url = f"{url}?{query}"
    return url


def _rewrite_location(location: str, *, requested_url: str, base: str, ticket: str = "",
                      prefix: Optional[str] = None) -> str:
    """Keep a same-origin redirect under the proxy prefix (``/preview/<ticket>/``,
    or ``/apps/<slug>/`` for a published deployment); a Location naming
    another origin is passed through unchanged, and the browser simply leaves
    the frame (the sandbox has no allow-same-origin either way)."""
    prefix = prefix if prefix is not None else f"/preview/{ticket}/"
    absolute = urljoin(requested_url, location)
    loc = urlsplit(absolute)
    origin = urlsplit(base)
    if (loc.scheme, loc.hostname, loc.port) != (origin.scheme, origin.hostname, origin.port):
        return location
    rest = loc.path.lstrip("/")
    out = f"{prefix}{rest}"
    if loc.query:
        out = f"{out}?{loc.query}"
    return out


def _response_headers(upstream_headers, *, ticket: str = "", base: str,
                      requested_url: str, prefix: Optional[str] = None) -> Dict[str, str]:
    prefix = prefix if prefix is not None else f"/preview/{ticket}/"
    out: Dict[str, str] = {}
    for key, value in upstream_headers.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP_HEADERS or lower in _UPSTREAM_DROP_HEADERS:
            continue
        if lower == "location":
            value = _rewrite_location(value, requested_url=requested_url, base=base, prefix=prefix)
        out[key] = value
    out.update(_ADDED_RESPONSE_HEADERS)
    return out


def _rewrite_html_prefix(html: bytes, prefix: str) -> bytes:
    """Inject ``<base href="<prefix>">`` when the document has none, and
    rewrite ``href="/``, ``src="/`` and ``action="/`` to the prefix so
    absolute-path URLs still resolve under the proxy. A plain regex, not a
    parser, and never applied inside a ``<script>`` block: known limitation,
    an absolute-path URL built at runtime by inline or external JavaScript is
    not caught (see docs/containers.md, "Preview through the hub"; a deployed
    app can read ``AGENTS_HUB_PUBLIC_PATH`` and build its URLs under it).
    """
    attr_prefix = f'="{prefix}'.encode("ascii")
    parts = _SCRIPT_RE.split(html)
    rewritten = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            rewritten.append(part)  # a captured <script>...</script> block: untouched
        else:
            rewritten.append(_ATTR_RE.sub(lambda m: m.group(1) + attr_prefix, part))
    out = b"".join(rewritten)
    if not _BASE_RE.search(out):
        base_tag = f'<base href="{prefix}">'.encode("ascii")
        if _HEAD_RE.search(out):
            out = _HEAD_RE.sub(lambda m: m.group(0) + base_tag, out, count=1)
        else:
            out = base_tag + out
    return out


def _rewrite_html(html: bytes, ticket: str) -> bytes:
    return _rewrite_html_prefix(html, f"/preview/{ticket}/")


async def proxy_to(raw_base: str, path: str, request: Request, *, prefix: str,
                   extra_headers: Optional[Dict[str, str]] = None) -> Response:
    """Forward ``request`` to ``raw_base``/``path`` and re-serve the answer
    under ``prefix``: the one proxy both ``/preview/<ticket>/`` and
    ``/apps/<slug>/`` (routes/project_deployments.py) are made of."""
    try:
        base, _host, pinned_ip = _validate_and_resolve(raw_base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    upstream_url = _upstream_url(base, path, request.url.query)
    body = await request.body()
    fwd_headers = _forward_request_headers(request.headers)

    client = _client_factory(pinned_ip)
    try:
        upstream_request = client.build_request(
            request.method, upstream_url, headers=fwd_headers, content=body or None,
        )
        resp = await client.send(upstream_request, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Cannot reach the previewed target")
    except httpx.TimeoutException:
        await client.aclose()
        raise HTTPException(status_code=504, detail="The previewed target timed out")

    content_type = resp.headers.get("content-type", "")
    is_html = content_type.split(";")[0].strip().lower() == "text/html"
    headers = _response_headers(resp.headers, base=base, requested_url=upstream_url, prefix=prefix)
    if extra_headers:
        headers.update(extra_headers)

    if is_html:
        content = await resp.aread()
        await resp.aclose()
        await client.aclose()
        content = _rewrite_html_prefix(content, prefix)
        return Response(content=content, status_code=resp.status_code, headers=headers,
                        media_type="text/html")

    async def _stream():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_stream(), status_code=resp.status_code, headers=headers,
                             media_type=content_type or None)


@public_router.api_route("/{ticket}", methods=["GET", "HEAD"], include_in_schema=False)
async def preview_root_redirect(ticket: str):
    """``/preview/<ticket>`` (no trailing slash) redirects to the form every
    relative URL on the proxied page is written against."""
    return RedirectResponse(url=f"/preview/{ticket}/", status_code=307)


@public_router.api_route(
    "/{ticket}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def preview_proxy(ticket: str, path: str, request: Request):
    target = preview_tickets.verify(ticket)
    if target is None:
        return HTMLResponse(_EXPIRED_HTML, status_code=403)

    resolved = _resolve_target(target["kind"], target["id"])
    if resolved is None:
        # The target existed at mint time and is gone now (stopped container,
        # reconfigured project): reads to the browser exactly like an expired
        # ticket, which is the accurate story either way ("this link no
        # longer works, go back to the hub").
        return HTMLResponse(_EXPIRED_HTML, status_code=403)
    raw_base, _workspace = resolved

    # Past half its life, the ticket this request came in on is due for a
    # successor: hand one back for a client that can read response headers
    # (the dashboard's iframe cannot, it renews through /tickets/renew).
    extra: Dict[str, str] = {}
    fresh = preview_tickets.renew(ticket)
    if fresh:
        extra["X-Preview-Ticket"] = fresh
    return await proxy_to(raw_base, path, request, prefix=f"/preview/{ticket}/", extra_headers=extra)


__all__ = ["router", "public_router", "proxy_to"]
