"""
Identity API: the auth mode, sessions, users and workspace membership.

- ``GET    /api/auth/mode``                 what posture this hub is in (public)
- ``POST   /api/auth/bootstrap``            create the first admin, once (public)
- ``POST   /api/auth/login``                open a session (public)
- ``POST   /api/auth/logout``               close this session
- ``GET    /api/auth/me``                   who the caller is
- ``GET    /api/auth/users``                every account (admin)
- ``POST   /api/auth/users``                add one (admin)
- ``PATCH  /api/auth/users/{id}``           role / display name / disabled (admin)
- ``DELETE /api/auth/users/{id}``           remove one (admin)
- ``POST   /api/auth/users/{id}/password``  reset a password (admin)
- ``GET|PUT|DELETE /api/workspaces/{name}/members``  membership (owner or admin)

The three public routes are the ones ``common.auth.PUBLIC_AUTH_ROUTES`` exempts
from the guard: the frontend has to be able to ask what to render, offer a
login form, and complete a first run, all before anybody is authenticated.

The membership routes live here rather than in ``routes/workspaces.py`` on
purpose: they are identity, not workspace content, and keeping them together
means the file that owns the rules also owns every endpoint that changes them.
They keep the ``/api/workspaces`` prefix because that is what the guard's
workspace resolution reads, so they are protected by the same path rule as the
rest of a workspace's owner-scoped settings.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common import identity
from common.auth import MULTI, WORKSPACE_ROLES, WS_OWNER

router = APIRouter(tags=["auth"])


# ── request/response models ──────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class BootstrapRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "member"
    display_name: str = ""


class UserPatch(BaseModel):
    role: Optional[str] = None
    display_name: Optional[str] = None
    disabled: Optional[bool] = None


class PasswordReset(BaseModel):
    password: str


class MemberPut(BaseModel):
    user_id: str
    role: str = Field(default="viewer", description="viewer | editor | owner")


# ── helpers ──────────────────────────────────────────────────────────────────

def _principal(request: Request):
    return identity.request_principal(request)


def _require_multi() -> None:
    """Refuse identity management outside ``multi``.

    404 rather than 403: in the other two modes there are no users, so these
    endpoints do not exist rather than being closed to the caller.
    """
    if identity.current_mode() != MULTI:
        raise HTTPException(status_code=404,
                            detail="User management needs AUTH_MODE=multi")


# ── public ───────────────────────────────────────────────────────────────────

@router.get("/api/auth/mode")
async def get_mode():
    """The posture this hub is in, answered before anyone is authenticated.

    The frontend renders from this alone: in ``single`` there is no login page,
    no users page, no members card and no user chip, and it learns that here
    rather than by trying an endpoint and reading the failure.
    """
    mode = identity.current_mode()
    return {
        "mode": mode,
        "bootstrap_required": identity.bootstrap_required(),
        "features": identity.mode_features(),
    }


@router.post("/api/auth/bootstrap")
async def bootstrap(payload: BootstrapRequest):
    """Create the first administrator. Available exactly once.

    With no users there is no admin to create a user, so this route is open
    until one account exists and refuses forever after.
    """
    _require_multi()
    try:
        user = identity.create_first_admin(payload.username, payload.password,
                                           payload.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session = identity.login(payload.username, payload.password)
    return {"user": user, **(session or {})}


@router.post("/api/auth/login")
async def login(payload: LoginRequest):
    """Exchange a username and password for a session token."""
    _require_multi()
    session = identity.login(payload.username, payload.password)
    if session is None:
        raise HTTPException(status_code=401, detail="Wrong username or password")
    return session


# ── the caller ───────────────────────────────────────────────────────────────

@router.post("/api/auth/logout")
async def logout(request: Request):
    """Close the session this request carries."""
    if identity.current_mode() != MULTI:
        return {"ok": True}
    presented = identity.presented_credential(request)
    return {"ok": identity.logout(presented or "")}


@router.get("/api/auth/me")
async def me(request: Request):
    """Who the caller is, plus the workspaces they can reach.

    Answers in every mode: in ``single`` it is the constant local operator, so
    a component can read it without first branching on the mode.
    """
    principal = _principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = identity.principal_dict(principal)
    if identity.current_mode() == MULTI and principal.kind == "user":
        payload["workspaces"] = identity.workspaces_for_user(principal.id)
    return payload


# ── users (admin) ────────────────────────────────────────────────────────────

@router.get("/api/auth/users")
async def get_users(request: Request) -> List[dict]:
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    return identity.list_users()


@router.post("/api/auth/users")
async def post_user(request: Request, payload: UserCreate):
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    try:
        return identity.create_user(payload.username, payload.password,
                                    role=payload.role,
                                    display_name=payload.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/api/auth/users/{user_id}")
async def patch_user(request: Request, user_id: str, payload: UserPatch):
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    try:
        user = identity.update_user(user_id, role=payload.role,
                                    display_name=payload.display_name,
                                    disabled=payload.disabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    return user


@router.delete("/api/auth/users/{user_id}")
async def remove_user(request: Request, user_id: str):
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    try:
        removed = identity.delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail="No such user")
    return {"deleted": True}


@router.post("/api/auth/users/{user_id}/password")
async def reset_password(request: Request, user_id: str, payload: PasswordReset):
    """Set someone's password. Their open sessions are dropped with it."""
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    if not payload.password:
        raise HTTPException(status_code=400, detail="A password is required")
    if not identity.set_password(user_id, payload.password):
        raise HTTPException(status_code=404, detail="No such user")
    return {"ok": True}


# ── workspace membership (owner or admin) ────────────────────────────────────

@router.get("/api/workspaces/{name}/members")
async def get_members(request: Request, name: str) -> List[dict]:
    _require_multi()
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    return identity.list_members(name)


@router.put("/api/workspaces/{name}/members")
async def put_member(request: Request, name: str, payload: MemberPut):
    """Add a member or change their role."""
    _require_multi()
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    if payload.role not in WORKSPACE_ROLES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown role '{payload.role}'")
    try:
        return identity.set_member(name, payload.user_id, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/workspaces/{name}/members/{user_id}")
async def delete_member(request: Request, name: str, user_id: str):
    """Drop a membership. A workspace may not lose its last owner."""
    _require_multi()
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    try:
        removed = identity.remove_member(name, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail="Not a member")
    return {"deleted": True}
