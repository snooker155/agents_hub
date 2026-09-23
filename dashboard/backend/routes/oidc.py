"""
Single sign-on routes: the redirect to the provider and the way back.

- ``GET /api/auth/oidc/start?next=/path``   302 to the provider (public)
- ``GET /api/auth/oidc/callback?code&state`` finish, 302 to ``/login/oidc`` (public)

Both are browser navigations, not XHR calls, so both answer with redirects
rather than JSON, and every failure lands on the dashboard's ``/login/oidc``
page with ``#error=<code>`` instead of a bare error body the person cannot
act on. The session token travels back in the URL *fragment*, never the query
string: a fragment is not sent to any server, so it stays out of access logs,
proxies and the ``Referer`` of whatever the page loads next.

Everything protocol-shaped lives in ``common/oidc.py``; this file only turns
its result into an account, a session and an audit row.
"""
from __future__ import annotations

import urllib.parse
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from common import audit, identity
import logging

from common import oidc

log = logging.getLogger(__name__)

router = APIRouter(tags=["oidc"])

_CALLBACK_PAGE = "/login/oidc"


def _landing(request: Request, **fragment: str) -> RedirectResponse:
    """A redirect to the dashboard's callback page with this fragment, and
    the round-trip cookie cleared."""
    base = oidc.public_url(request)
    target = f"{base}{_CALLBACK_PAGE}#{urllib.parse.urlencode(fragment)}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(oidc.STATE_COOKIE, path=oidc.STATE_COOKIE_PATH,
                           secure=oidc.cookie_secure(request), httponly=True,
                           samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return response


def _denied(request: Request, code: str, message: str = "",
            username: Optional[str] = None, user_id: Optional[str] = None) -> RedirectResponse:
    audit.record("auth.login",
                 actor={"actor_id": user_id, "actor_kind": "anonymous" if not user_id else "user",
                        "actor_name": username},
                 result="denied", ip=identity.client_ip(request),
                 details={"kind": "oidc", "error": code, "message": message[:300]})
    return _landing(request, error=code)


@router.get("/api/auth/oidc/start")
async def oidc_start(request: Request, next_path: str = Query("/", alias="next")):
    """Send the browser to the provider, with state, nonce and PKCE set up."""
    if not oidc.enabled():
        raise HTTPException(status_code=404, detail="Single sign-on is not configured")
    try:
        next_path = oidc.safe_next(next_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        location, cookie = oidc.begin(request, next_path)
    except Exception as exc:
        # Discovery failed: the provider is down or the issuer is wrong.
        return _denied(request, "provider_unreachable", str(exc))
    response = RedirectResponse(location, status_code=302)
    response.set_cookie(
        oidc.STATE_COOKIE, cookie, max_age=oidc.STATE_TTL_SECONDS,
        path=oidc.STATE_COOKIE_PATH, httponly=True, samesite="lax",
        secure=oidc.cookie_secure(request))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/auth/oidc/callback")
async def oidc_callback(request: Request, code: str = "", state: str = "",
                        error: str = "", error_description: str = ""):
    """Finish a sign-in: verify, link or create the account, open a session."""
    if not oidc.enabled():
        raise HTTPException(status_code=404, detail="Single sign-on is not configured")
    if error:
        # The provider refused (the person cancelled, consent was denied, the
        # client is misconfigured). Its code is not ours to forward verbatim.
        return _denied(request, "provider_error", f"{error}: {error_description}")
    try:
        result = oidc.complete(request, code=code, state=state,
                               cookie_value=request.cookies.get(oidc.STATE_COOKIE))
    except oidc.OidcError as exc:
        return _denied(request, exc.code, str(exc))
    except Exception as exc:  # the provider answered something unexpected
        return _denied(request, "verification_failed", str(exc))

    try:
        user = identity.upsert_external_user(
            result["issuer"], result["subject"], username=result["username"],
            email=result["email"], display_name=result["display_name"],
            source=identity.SOURCE_OIDC)
    except ValueError as exc:
        return _denied(request, "account_failed", str(exc), username=result["username"])
    if user.get("disabled"):
        return _denied(request, "disabled", "the account is disabled",
                       username=user["username"], user_id=user["id"])

    group_names = result["groups"]
    overage = group_names is None and oidc.groups_overage(result.get("claims") or {})
    if group_names is not None:
        # Present (even empty): the provider is the source of truth for the
        # groups it names. Absent: it said nothing, so nothing changes.
        oidc.sync_groups(user["id"], group_names)
    elif overage:
        log.warning("oidc: %s is in too many groups for the id token; groups left "
                    "unchanged. Restrict the claim to groups assigned to the "
                    "application (docs/sso.md).", user["username"])

    ip = identity.client_ip(request)
    session = identity.open_session(user["id"], kind=identity.SESSION_OIDC, ip=ip,
                                    user_agent=request.headers.get("user-agent"))
    if session is None:
        return _denied(request, "disabled", "no session could be opened",
                       username=user["username"], user_id=user["id"])
    audit.record("auth.login", actor={"actor_id": user["id"], "actor_kind": "user",
                                      "actor_name": user["username"]},
                 object_type="session", object_id=session.get("session_id"), ip=ip,
                 details={"kind": "oidc", "issuer": result["issuer"],
                          "groups": group_names,
                          **({"groups_overage": True} if overage else {})})
    return _landing(request, token=session["token"], expires_at=session["expires_at"],
                    next=result["next"])
