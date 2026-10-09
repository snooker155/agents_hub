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
- ``GET    /api/auth/spend-limits``         limits and month spend per person (admin)
- ``PUT    /api/auth/spend-limits/default`` the hub default limit (admin)
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

from common import audit, identity
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
    # Optional: an account without one can only sign in through single
    # sign-on until an administrator sets a password.
    password: Optional[str] = None
    role: str = "member"
    display_name: str = ""
    email: str = ""


class UserPatch(BaseModel):
    role: Optional[str] = None
    display_name: Optional[str] = None
    disabled: Optional[bool] = None
    email: Optional[str] = None
    # Own monthly spend limit (common/user_budget.py). Sent as null it clears
    # the person's own value, so the hub default applies; 0 is unlimited.
    spend_limit_usd: Optional[float] = None


class DefaultSpendLimit(BaseModel):
    limit_usd: float = Field(ge=0)


class PasswordReset(BaseModel):
    password: str


class MemberPut(BaseModel):
    user_id: str
    role: str = Field(default="viewer", description="viewer | editor | owner")


# ── helpers ──────────────────────────────────────────────────────────────────

def _principal(request: Request):
    return identity.request_principal(request)


def _ensure_personal_workspace(user_id: str) -> None:
    """A person signing in has a workspace of their own (common/personal_workspace.py)."""
    import logging
    from common import personal_workspace
    try:
        personal_workspace.ensure_personal_workspace(user_id)
    except Exception:  # noqa: BLE001 - logged; the sign-in goes on without it
        logging.getLogger(__name__).warning(
            "personal workspace for %s could not be ensured", user_id, exc_info=True)


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
    features = identity.mode_features()
    body = {
        "mode": mode,
        "bootstrap_required": identity.bootstrap_required(),
        "features": features,
    }
    if features.get("oidc"):
        from common.config import settings
        body["oidc"] = {
            "provider_name": (getattr(settings, "auth_oidc_provider_name", "") or "").strip()
            or "single sign-on",
            "start_url": "/api/auth/oidc/start",
        }
    return body


@router.post("/api/auth/bootstrap")
async def bootstrap(request: Request, payload: BootstrapRequest):
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
    session = identity.open_session(
        user["id"], kind=identity.SESSION_BOOTSTRAP, ip=identity.client_ip(request),
        user_agent=request.headers.get("user-agent"))
    _ensure_personal_workspace(user["id"])
    audit.record("auth.bootstrap", actor={"actor_id": user["id"], "actor_kind": "user",
                                          "actor_name": user["username"]},
                 object_type="user", object_id=user["id"], ip=identity.client_ip(request))
    return {"user": user, **(session or {})}


@router.post("/api/auth/login")
async def login(request: Request, payload: LoginRequest):
    """Exchange a username and password for a session token.

    429 once the account or the address has failed too often in the window
    (``AUTH_LOGIN_MAX_ATTEMPTS`` per ``AUTH_LOGIN_WINDOW_MINUTES``).
    """
    _require_multi()
    ip = identity.client_ip(request)
    try:
        session = identity.login(payload.username, payload.password, ip=ip,
                                 user_agent=request.headers.get("user-agent"))
    except identity.LoginThrottled:
        audit.record("auth.login", actor={"actor_id": None, "actor_kind": "anonymous",
                                          "actor_name": payload.username},
                     result="throttled", ip=ip)
        raise HTTPException(status_code=429,
                            detail="Too many failed sign-ins. Try again later.")
    if session is None:
        audit.record("auth.login", actor={"actor_id": None, "actor_kind": "anonymous",
                                          "actor_name": payload.username},
                     result="denied", ip=ip)
        raise HTTPException(status_code=401, detail="Wrong username or password")
    user = session["user"]
    _ensure_personal_workspace(user["id"])
    audit.record("auth.login", actor={"actor_id": user["id"], "actor_kind": "user",
                                      "actor_name": user["username"]},
                 object_type="session", object_id=session.get("session_id"), ip=ip,
                 details={"kind": session.get("kind")})
    return session


# ── the caller ───────────────────────────────────────────────────────────────

@router.post("/api/auth/logout")
async def logout(request: Request):
    """Close the session this request carries."""
    if identity.current_mode() != MULTI:
        return {"ok": True}
    presented = identity.presented_credential(request)
    principal = _principal(request)
    closed = identity.logout(presented or "")
    if closed:
        audit.record("auth.logout", principal=principal, object_type="session",
                     object_id=getattr(principal, "credential_id", None) or None,
                     ip=identity.client_ip(request))
    return {"ok": closed}


@router.post("/api/auth/ticket")
async def auth_ticket(request: Request):
    """A one-time, minute-long ticket for a connection that cannot set a
    header: the dashboard's EventSource on ``/api/stream``, the browser
    frame WebSocket, a navigation such as the GitHub connect link.

    Presented as ``?ticket=`` it stands for whoever minted it (a user, with
    an API key's workspace scope kept; the shared token; the service
    credential), so the long-lived credential never has to ride a URL. In
    ``multi`` mode it is the only query credential accepted. See
    docs/identity.md, "Tickets for streams".
    """
    from common import preview_tickets
    principal = _principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    ttl = preview_tickets.AUTH_TTL_SECONDS
    return {"ticket": preview_tickets.mint_auth(principal, ttl_seconds=ttl),
            "expires_in": ttl}


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
    assert payload is not None  # principal is not None just above
    if identity.current_mode() == MULTI and principal.kind == "user":
        from common import personal_workspace
        payload["personal_workspace"] = personal_workspace.ensure_for_principal(principal)
        payload["workspaces"] = identity.workspaces_for_user(principal.id)
        payload["workspace_roles"] = identity.workspace_roles_for_user(principal.id)
        user = identity.get_user(principal.id) or {}
        payload["display_name"] = user.get("display_name") or principal.username
        payload["email"] = user.get("email") or ""
        payload["source"] = user.get("source") or "local"
        payload["has_password"] = bool(user.get("has_password"))
        from common import groups
        payload["groups"] = groups.group_names_of_user(principal.id)
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
        user = identity.create_user(payload.username, payload.password or None,
                                    role=payload.role,
                                    display_name=payload.display_name,
                                    email=payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("user.create", principal=_principal(request), object_type="user",
                 object_id=user["id"], ip=identity.client_ip(request),
                 details={"username": user["username"], "role": user["role"]})
    return user


@router.patch("/api/auth/users/{user_id}")
async def patch_user(request: Request, user_id: str, payload: UserPatch):
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    try:
        user = identity.update_user(user_id, role=payload.role,
                                    display_name=payload.display_name,
                                    disabled=payload.disabled, email=payload.email)
        if user is not None and "spend_limit_usd" in payload.model_fields_set:
            user = identity.set_spend_limit(user_id, payload.spend_limit_usd)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "spend_limit_usd" in payload.model_fields_set:
        changes["spend_limit_usd"] = payload.spend_limit_usd
    audit.record("user.role" if "role" in changes else "user.update",
                 principal=_principal(request), object_type="user", object_id=user_id,
                 ip=identity.client_ip(request), details=changes)
    return user


# ── spend limits per person (admin) ──────────────────────────────────────────

@router.get("/api/auth/spend-limits")
async def get_spend_limits(request: Request):
    """The hub default limit and, per person, their limit, its source and
    this month's spend (common/user_budget.py)."""
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    from common import user_budget
    return {
        "period": user_budget.PERIOD,
        "default_limit_usd": user_budget.default_limit(),
        "users": {u["id"]: user_budget.user_budget_status(u["id"]) for u in identity.list_users()},
    }


@router.put("/api/auth/spend-limits/default")
async def put_default_spend_limit(request: Request, payload: DefaultSpendLimit):
    """Set the monthly limit of every person without one of their own.
    Written to ``.env`` for the next start and applied to this process now."""
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    import os
    from common import user_budget
    from common.config import settings
    from routes.settings import _write_env_key
    value = round(float(payload.limit_usd), 4)
    _write_env_key(user_budget.DEFAULT_LIMIT_ENV, str(value))
    os.environ[user_budget.DEFAULT_LIMIT_ENV] = str(value)
    settings.user_spend_limit_usd = value
    audit.record("user.spend_limit_default", principal=_principal(request),
                 object_type="setting", object_id=user_budget.DEFAULT_LIMIT_ENV,
                 ip=identity.client_ip(request), details={"limit_usd": value})
    return {"default_limit_usd": user_budget.default_limit()}


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
    audit.record("user.delete", principal=_principal(request), object_type="user",
                 object_id=user_id, ip=identity.client_ip(request))
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
    audit.record("user.password", principal=_principal(request), object_type="user",
                 object_id=user_id, ip=identity.client_ip(request))
    return {"ok": True}


# ── workspace membership (owner or admin) ────────────────────────────────────

@router.get("/api/workspaces/{name}/members")
async def get_members(request: Request, name: str) -> List[dict]:
    _require_multi()
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    return identity.list_members(name)


def _refuse_personal(name: str) -> None:
    """A personal workspace has exactly one member, its person, as owner
    (common/personal_workspace.py): nobody is added, nobody removed."""
    from common import personal_workspace
    if personal_workspace.is_personal(name):
        raise HTTPException(status_code=403,
                            detail="A personal workspace cannot be shared or change its owner")


@router.put("/api/workspaces/{name}/members")
async def put_member(request: Request, name: str, payload: MemberPut):
    """Add a member or change their role."""
    _require_multi()
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    _refuse_personal(name)
    if payload.role not in WORKSPACE_ROLES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown role '{payload.role}'")
    try:
        member = identity.set_member(name, payload.user_id, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("workspace.member", principal=_principal(request), object_type="user",
                 object_id=payload.user_id, workspace=name, ip=identity.client_ip(request),
                 details={"role": payload.role})
    return member


@router.delete("/api/workspaces/{name}/members/{user_id}")
async def delete_member(request: Request, name: str, user_id: str):
    """Drop a membership. A workspace may not lose its last owner."""
    _require_multi()
    identity.require_role(_principal(request), workspace=name, role=WS_OWNER)
    _refuse_personal(name)
    try:
        removed = identity.remove_member(name, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail="Not a member")
    audit.record("workspace.member", principal=_principal(request), object_type="user",
                 object_id=user_id, workspace=name, ip=identity.client_ip(request),
                 details={"role": None})
    return {"deleted": True}
