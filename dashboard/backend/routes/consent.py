"""
Routes: the consent portal (connectors/consent/, docs/consent.md).

Two routers.

``public_router``, outside ``/api`` and so open without a hub login
(common/auth.py ``is_open_path``), for the end user's browser:

- GET  /consent/callback          where Google and Microsoft send the end
                                  user back (the redirect URI both app
                                  registrations list)
- GET  /consent/{token}           what one request asks for
- POST /consent/{token}/start     Continue: off to the provider
- POST /consent/{token}/decline   No, thanks

Each is throttled per client address, answers with ``no-store``, no referrer
(the token is in the path) and a policy that allows no script and no framing.
A page only ever shows the request its signed token names; a bad, expired or
used token gets the same kind of page whatever the reason behind it.

``router``, under ``/api/consent`` and behind the usual guard, for the
operator: the access catalog with the redirect URI to register, an agent's
settings (which providers it acts on as the end user, and with which access),
and the live grants of a workspace with a Revoke button (workspace owner or
admin, as for the workspace's secrets).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from common import audit, identity
from common.auth import WS_OWNER
from common.rate_limit import SlidingWindow
from connectors.consent import access, catalog, flow, page, store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/consent", tags=["consent"])
public_router = APIRouter(tags=["consent"])

#: Page loads and callbacks per client address per minute; starts are rarer.
PAGE_PER_MINUTE = 30
START_PER_MINUTE = 10
_window = SlidingWindow()

_PROVIDER_ORIGINS = "https://accounts.google.com https://login.microsoftonline.com"
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        f"form-action 'self' {_PROVIDER_ORIGINS}; frame-ancestors 'none'; base-uri 'none'"),
}


def reset_limits() -> None:
    """Forget the throttles (tests)."""
    _window.reset()


# ── helpers ──────────────────────────────────────────────────────────────────

def _base(request: Request) -> str:
    """Where the end user's browser reaches the hub: AGENTS_HUB_PUBLIC_URL,
    else the request as a reverse proxy forwarded it. Start and callback
    derive it the same way, so the redirect URI matches."""
    from common.hub_urls import public_base
    configured = public_base()
    if configured:
        return configured
    headers = request.headers
    proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip() or request.url.scheme
    host = ((headers.get("x-forwarded-host") or "").split(",")[0].strip()
            or headers.get("host") or request.url.netloc)
    return f"{proto}://{host}".rstrip("/")


def _lang(request: Request) -> str:
    return page.pick_language(request.headers.get("accept-language"))


def _html(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(body, status_code=status, headers=dict(_HEADERS))


def _throttled(request: Request, kind: str, limit: int) -> Optional[HTMLResponse]:
    ok, retry = _window.check(f"{kind}:{identity.client_ip(request) or 'unknown'}", limit, 60.0)
    if ok:
        return None
    response = _html(page.render_outcome("busy", lang=_lang(request)), 429)
    response.headers["Retry-After"] = str(retry)
    return response


def _agent_name(agent_id: str) -> str:
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
        return str(getattr(spec, "name", "") or agent_id) if spec else agent_id
    except Exception:  # noqa: BLE001 - the id is a fine name when the registry cannot answer
        return agent_id


_STATUS_FOR = {"invalid": 404, "expired": 410, "used": 410, "granted": 200, "denied": 200,
               "revoked": 410, "failed": 400}


def _outcome(request: Request, code: str, row: Optional[Dict[str, Any]] = None) -> HTMLResponse:
    row = row or {}
    return _html(page.render_outcome(code, lang=_lang(request),
                                     agent_name=_agent_name(row["agent_id"]) if row.get("agent_id") else "",
                                     provider=row.get("provider") or "",
                                     account_email=row.get("account_email") or ""),
                 _STATUS_FOR.get(code, 200))


# ── the end user's pages ─────────────────────────────────────────────────────
# The callback is declared first: /consent/{token} would otherwise take it.

@public_router.get(flow.CALLBACK_PATH, response_class=HTMLResponse)
async def consent_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    busy = _throttled(request, "page", PAGE_PER_MINUTE)
    if busy is not None:
        return busy
    import asyncio
    try:
        row = await asyncio.to_thread(flow.complete, state=state, code=code, error=error,
                                      base=_base(request))
    except flow.ConsentFlowError as exc:
        return _outcome(request, exc.code)
    return _outcome(request, "done", row)


@public_router.get("/consent/{token}", response_class=HTMLResponse)
async def consent_page(token: str, request: Request):
    busy = _throttled(request, "page", PAGE_PER_MINUTE)
    if busy is not None:
        return busy
    try:
        row = flow.open_request(token)
    except flow.ConsentFlowError as exc:
        return _outcome(request, exc.code)
    return _html(page.render_request(row, lang=_lang(request),
                                     agent_name=_agent_name(row["agent_id"]), token=token))


@public_router.post("/consent/{token}/start")
async def consent_start(token: str, request: Request):
    busy = _throttled(request, "start", START_PER_MINUTE)
    if busy is not None:
        return busy
    try:
        row = flow.open_request(token)
        url = flow.begin(row, base=_base(request))
    except flow.ConsentFlowError as exc:
        return _outcome(request, exc.code)
    except Exception:  # noqa: BLE001 - a provider not configured any more ends on the failure page
        log.warning("consent: could not start the provider round trip", exc_info=True)
        return _outcome(request, "failed")
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store",
                                                           "Referrer-Policy": "no-referrer"})


@public_router.post("/consent/{token}/decline", response_class=HTMLResponse)
async def consent_decline(token: str, request: Request):
    busy = _throttled(request, "page", PAGE_PER_MINUTE)
    if busy is not None:
        return busy
    try:
        row = flow.open_request(token)
    except flow.ConsentFlowError as exc:
        return _outcome(request, exc.code)
    flow.decline(row)
    return _outcome(request, "denied", row)


# ── the operator's side ──────────────────────────────────────────────────────

class ConsentSettingsUpdate(BaseModel):
    providers: List[str] = []
    scopes: Dict[str, List[str]] = {}


def _catalog(request: Request) -> Dict[str, Any]:
    return {
        "providers": [{
            "id": p,
            "label": catalog.PROVIDER_LABELS[p],
            "access": catalog.keys_for(p),
            "default": list(catalog.DEFAULT_KEYS[p]),
            "ready": flow.provider_ready(p),
        } for p in catalog.PROVIDERS],
        "redirect_uri": flow.redirect_uri(_base(request)),
    }


@router.get("/catalog")
async def consent_catalog(request: Request):
    return _catalog(request)


@router.get("/agents/{agent_id}")
async def get_agent_settings(agent_id: str, request: Request):
    return {"agent_id": agent_id, **store.get_settings(agent_id), "catalog": _catalog(request)}


@router.put("/agents/{agent_id}")
async def update_agent_settings(agent_id: str, data: ConsentSettingsUpdate, request: Request):
    from agents import registry
    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if getattr(spec, "system", False):
        raise HTTPException(status_code=403, detail="A system agent's account access cannot be edited")
    principal = identity.request_principal(request)
    try:
        saved = store.save_settings(agent_id, data.providers, data.scopes,
                                    by=getattr(principal, "id", None))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The tools come and go with the providers: rebuild this agent's cached builds.
    from agents import agent_cache
    agent_cache.invalidate(agent_id)
    audit.record("agent.consent_settings", principal=principal, object_type="agent",
                 object_id=agent_id, workspace=getattr(spec, "owner_workspace", None),
                 ip=identity.client_ip(request),
                 details={"providers": saved["providers"], "scopes": saved["scopes"]})
    return {"agent_id": agent_id, **saved, "catalog": _catalog(request)}


def _public_grant(row: Dict[str, Any]) -> Dict[str, Any]:
    principal = row["principal"]
    kind = principal.split(":", 1)[0] if ":" in principal else ""
    return {
        "request_id": row["request_id"], "agent_id": row["agent_id"],
        "agent_name": _agent_name(row["agent_id"]), "provider": row["provider"],
        "access": row["scopes"], "principal": principal, "principal_kind": kind,
        "account_email": row.get("account_email") or "", "granted_at": row.get("completed_at"),
    }


# Grants name end users and their accounts: like the workspace's secrets, only
# its owner (or an admin) reads and revokes them.

@router.get("/grants")
async def list_grants(workspace: str, request: Request, agent_id: Optional[str] = None):
    identity.require_role(identity.request_principal(request), workspace=workspace, role=WS_OWNER)
    return {"grants": [_public_grant(r) for r in store.list_grants(workspace, agent_id)]}


@router.post("/grants/{request_id}/revoke")
async def revoke_grant(request_id: str, workspace: str, request: Request):
    import asyncio
    identity.require_role(identity.request_principal(request), workspace=workspace, role=WS_OWNER)
    row = store.get_request(request_id)
    # The workspace in the query is what the guard checked the caller's role
    # against, so it must be the grant's own.
    if row is None or row["workspace"] != workspace or row["status"] != "granted":
        raise HTTPException(status_code=404, detail="No such grant in this workspace")
    principal = identity.request_principal(request)
    done = await asyncio.to_thread(
        access.revoke, row["workspace"], row["agent_id"], row["principal"], row["provider"],
        by=f"operator:{getattr(principal, 'id', '') or 'local'}", principal_obj=principal)
    return {"ok": True, "revoked": bool(done)}


__all__ = ["PAGE_PER_MINUTE", "START_PER_MINUTE", "public_router", "reset_limits", "router"]
