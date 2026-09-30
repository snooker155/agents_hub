"""
The GitHub App over REST (connectors/git/github_app.py, docs/github-app.md).

Three audiences, three sets of routes:

- **The administrator** sees whether the app is configured, its install link
  and its installations, and refreshes that list (``/api/git/github-app``).
  In ``multi`` mode that is an administrator only: an installation reaches an
  organisation's repositories, which is not a workspace member's business.
- **A workspace owner** binds an installation to their workspace
  (``PUT /api/workspaces/{name}/github-installation``). ``github-installation``
  is not an owner-scoped segment in common/auth.py, so the middleware only
  asks for ``editor`` on a write; the owner check is made here. A non-admin
  owner may bind an unbound installation or one already bound to their
  workspace, never take one away from another workspace.
- **Any signed-in person** connects their own GitHub account
  (``/api/auth/github``), so agents with ``github_identity: user`` push and
  open pull requests as them.

Why two routes live under ``/api/external``: GitHub sends the browser back
with no hub credential on it (the callback after authorising, the setup URL
after installing), and ``/api/external`` is the prefix common/auth.py already
leaves open. Neither trusts the query alone: the callback accepts a code only
with the signed state cookie the connect route set in the same browser, and
the cookie names the user, so no session is needed on the way back. The setup
route only triggers a sync, which asks GitHub itself what is installed.

The state cookie is signed with common/oidc.py's HMAC (``sign_state``), with
a ``purpose`` field so an OIDC sign-in cookie can never pass for this one.
"""
from __future__ import annotations

import asyncio
import hmac
import secrets as pysecrets
import time
import urllib.parse
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from common import audit, identity
from common.auth import WS_OWNER
from connectors.git import github_app

router = APIRouter(tags=["github-app"])

#: The round-trip cookie of the connect flow, sent back only to the callback.
STATE_COOKIE = "ah_github"
STATE_COOKIE_PATH = "/api/external/github"
STATE_TTL_SECONDS = 600
_PURPOSE = "github-connect"

CALLBACK_PATH = "/api/external/github/callback"
SETUP_PATH = "/api/external/github-app/setup"


class InstallationBinding(BaseModel):
    installation_id: Optional[int] = None


def _principal(request: Request):
    return identity.request_principal(request)


def _public_url(request: Request) -> str:
    from common import oidc
    return oidc.public_url(request)


def _redirect_uri(request: Request) -> str:
    return f"{_public_url(request)}{CALLBACK_PATH}"


def _http_error(exc: github_app.GitHubAppError) -> HTTPException:
    code = {"not_found": 404, "api_error": 502, "exchange_failed": 502}.get(exc.code, 400)
    return HTTPException(status_code=code, detail=str(exc))


def _overview() -> dict:
    return {
        "configured": github_app.configured(),
        "app_id": github_app.app_id(),
        "slug": github_app.slug(),
        "install_url": github_app.install_url(),
        "web_url": github_app.web_url(),
        "api_url": github_app.api_url(),
        "installations": github_app.installations(),
        "env": ["GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_APP_CLIENT_ID",
                "GITHUB_APP_CLIENT_SECRET", "GITHUB_APP_PRIVATE_KEY"],
    }


# ── the administrator's view ────────────────────────────────────────────────

@router.get("/api/git/github-app")
async def get_github_app(request: Request) -> dict:
    identity.require_role(_principal(request), admin=True)
    return _overview()


@router.post("/api/git/github-app/sync")
async def sync_github_app(request: Request) -> dict:
    principal = _principal(request)
    identity.require_role(principal, admin=True)
    try:
        await asyncio.to_thread(github_app.list_installations)
    except github_app.GitHubAppError as exc:
        raise _http_error(exc)
    audit.record("github.install.sync", principal=principal, object_type="github_app",
                 ip=identity.client_ip(request),
                 details={"installations": len(github_app.installations())})
    return _overview()


@router.get(SETUP_PATH)
async def github_app_setup(request: Request):
    """GitHub's "Setup URL": the browser lands here after an installation.

    Public (see the module docstring); it only syncs and redirects, and a
    sync that fails still lands the person on the Git page, which shows the
    state as it is.
    """
    if github_app.configured():
        try:
            await asyncio.to_thread(github_app.list_installations)
        except github_app.GitHubAppError:
            pass
    return RedirectResponse(f"{_public_url(request)}/connectors?tab=git", status_code=302)


@router.put("/api/workspaces/{name}/github-installation")
async def put_workspace_installation(request: Request, name: str,
                                     payload: InstallationBinding) -> dict:
    principal = _principal(request)
    identity.require_role(principal, workspace=name, role=WS_OWNER)
    from workspace import get_workspace_folder
    if not get_workspace_folder(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' does not exist")
    ip = identity.client_ip(request)
    if payload.installation_id is None:
        current = github_app.installation_for_workspace(name)
        if current is not None:
            github_app.unbind(current)
            audit.record("github.install.unbind", principal=principal,
                         object_type="github_installation", object_id=str(current),
                         workspace=name, ip=ip)
        return {"workspace": name, "installation_id": None}
    found = github_app.get_installation(payload.installation_id)
    if found is None:
        raise HTTPException(status_code=404,
                            detail=f"No installation {payload.installation_id}: sync first")
    elsewhere = found["workspace"] and found["workspace"] != name
    if elsewhere and principal is not None and not principal.is_admin \
            and identity.current_mode() == "multi":
        raise HTTPException(status_code=403,
                            detail="this installation is bound to another workspace; "
                                   "an administrator must unbind it first")
    try:
        row = github_app.bind_installation(payload.installation_id, name)
    except github_app.GitHubAppError as exc:
        raise _http_error(exc)
    audit.record("github.install.bind", principal=principal, object_type="github_installation",
                 object_id=str(payload.installation_id), workspace=name, ip=ip,
                 details={"account": row.get("account_login", ""),
                          "previous_workspace": found["workspace"] or ""})
    return {"workspace": name, "installation_id": payload.installation_id, "installation": row}


# ── a person's own GitHub account ────────────────────────────────────────────

def _require_person(request: Request):
    """A principal that is somebody: a user in ``multi``, the operator in
    ``single`` or ``token`` mode. The service credential has no account."""
    principal = _principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if principal.kind == "service":
        raise HTTPException(status_code=403, detail="This credential has no account of its own")
    return principal


@router.get("/api/auth/github")
async def get_github_status(request: Request) -> dict:
    principal = _require_person(request)
    return github_app.status_for(principal.id)


@router.get("/api/auth/github/connect")
async def github_connect(request: Request):
    principal = _require_person(request)
    if not github_app.configured():
        raise HTTPException(status_code=400, detail="The GitHub App is not configured")
    from common import secrets as secret_store
    if not secret_store.key_configured():
        raise HTTPException(status_code=400,
                            detail="Connecting a GitHub account needs AGENTS_HUB_SECRET_KEY: "
                                   + secret_store.NO_KEY_MESSAGE)
    from common import oidc
    state = pysecrets.token_urlsafe(24)
    cookie = oidc.sign_state({"purpose": _PURPOSE, "uid": principal.id,
                              "state": state, "exp": int(time.time()) + STATE_TTL_SECONDS})
    response = RedirectResponse(github_app.connect_url(state, _redirect_uri(request)),
                                status_code=302)
    response.set_cookie(STATE_COOKIE, cookie, max_age=STATE_TTL_SECONDS, path=STATE_COOKIE_PATH,
                        httponly=True, samesite="lax", secure=oidc.cookie_secure(request))
    return response


def _back_to_account(request: Request, outcome: str, reason: str = "") -> RedirectResponse:
    fragment = {"github": outcome}
    if reason:
        fragment["reason"] = reason
    response = RedirectResponse(
        f"{_public_url(request)}/account#{urllib.parse.urlencode(fragment)}", status_code=302)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return response


@router.get(CALLBACK_PATH)
async def github_callback(request: Request):
    """GitHub sends the browser back here after the person authorised the app.

    With "Request user authorization during installation" on, an
    installation lands here too (with ``installation_id``): the list is
    synced, and when the same browser started a connect flow, the person is
    connected as well.
    """
    params = request.query_params
    installation_id = params.get("installation_id")
    if installation_id and github_app.configured():
        try:
            await asyncio.to_thread(github_app.list_installations)
        except github_app.GitHubAppError:
            pass

    from common import oidc
    saved = None
    try:
        saved = oidc.read_state(request.cookies.get(STATE_COOKIE))
        if saved.get("purpose") != _PURPOSE:
            saved = None
    except oidc.OidcError:
        saved = None
    state_ok = bool(saved) and bool(params.get("state")) and hmac.compare_digest(
        str(saved.get("state", "")), str(params.get("state")))
    ip = identity.client_ip(request)

    if not state_ok:
        if installation_id:
            response = RedirectResponse(f"{_public_url(request)}/connectors?tab=git",
                                        status_code=302)
            response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
            return response
        return _back_to_account(request, "error", "bad_state")

    user_id = str(saved.get("uid") or "")
    actor = {"actor_id": user_id, "actor_kind": "user", "actor_name": None}
    if params.get("error"):
        audit.record("github.connect", actor=actor, object_type="github_account",
                     object_id=user_id, ip=ip, result="error",
                     details={"error": params.get("error")})
        return _back_to_account(request, "error", str(params.get("error"))[:64])
    try:
        result = await asyncio.to_thread(github_app.exchange_code, user_id,
                                         params.get("code") or "", _redirect_uri(request))
    except github_app.GitHubAppError as exc:
        audit.record("github.connect", actor=actor, object_type="github_account",
                     object_id=user_id, ip=ip, result="error", details={"error": exc.code})
        return _back_to_account(request, "error", exc.code)
    audit.record("github.connect", actor=actor, object_type="github_account",
                 object_id=user_id, ip=ip, details={"login": result.get("login", "")})
    return _back_to_account(request, "connected")


@router.delete("/api/auth/github")
async def github_disconnect(request: Request) -> dict:
    principal = _require_person(request)
    removed = await asyncio.to_thread(github_app.disconnect, principal.id)
    if not removed:
        raise HTTPException(status_code=404, detail="No GitHub account is connected")
    audit.record("github.disconnect", principal=principal, object_type="github_account",
                 object_id=principal.id, ip=identity.client_ip(request))
    return {"deleted": True}
