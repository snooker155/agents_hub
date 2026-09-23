"""
The caller's own account: sessions and personal API keys.

Built this way because the accounts API (``routes/auth.py``) is an
administrator's view of everybody; this one is a signed-in person's view of
themselves. The two never overlap on a route, but they share the same
building blocks (``common.identity``, ``common.api_keys``, ``common.audit``),
so a session list here and a session list an admin might one day want look
the same shape.

Every route needs ``AUTH_MODE=multi`` (404 outside it, the same "these
endpoints do not exist" reasoning ``routes/auth.py`` uses) and a signed-in
*user* principal: 401 with no credential at all, 403 for a credential that is
not a person (the service credential, a shared API token) since there is no
account behind it to show sessions or keys for.

A password change is the one route that revokes the very session making the
request (``identity.set_password`` drops every session of that user). The
frontend is told this explicitly (``relogin: true``) rather than left to
discover it from the next 401, so it can send the person straight to the
login screen instead of showing a confusing failure on whatever they click
next.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from common import api_keys, audit, identity
from common.auth import MULTI

router = APIRouter(tags=["account"])


# ── request models ──────────────────────────────────────────────────────────

class PasswordChange(BaseModel):
    current_password: str
    password: str


class KeyCreate(BaseModel):
    name: str = ""
    # None: the owner's full reach. A list, even a single-item one, narrows
    # the key; an empty list is treated as None by common.api_keys.create_key.
    workspaces: Optional[List[str]] = None
    expires_in_days: Optional[int] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _principal(request: Request):
    return identity.request_principal(request)


def _require_account(request: Request):
    """404 outside ``multi``, 401 with no credential, 403 for a credential
    that is not a person's (service, token): those have no account settings
    of their own to show. Returns the caller's principal."""
    if identity.current_mode() != MULTI:
        raise HTTPException(status_code=404,
                            detail="Account settings need AUTH_MODE=multi")
    principal = _principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if principal.kind != "user":
        raise HTTPException(status_code=403,
                            detail="This credential has no account of its own")
    return principal


def _require_multi() -> None:
    if identity.current_mode() != MULTI:
        raise HTTPException(status_code=404,
                            detail="Account settings need AUTH_MODE=multi")


def _verify_current_password(user_id: str, current_password: str) -> bool:
    """Check a plaintext password against the stored record.

    No public helper returns the hash fields (``identity.get_user`` strips
    them on purpose, see its docstring), so this reads the row directly the
    same way ``identity.login`` does.
    """
    from common import db
    row = db.get_conn().execute(
        "SELECT password_hash, password_salt, password_iterations "
        "FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    if row is None or not row["password_hash"]:
        return False
    return identity.verify_password(
        current_password, password_hash=row["password_hash"],
        password_salt=row["password_salt"],
        password_iterations=row["password_iterations"])


# ── sessions ─────────────────────────────────────────────────────────────────

@router.get("/api/auth/sessions")
async def get_sessions(request: Request) -> List[dict]:
    principal = _require_account(request)
    sessions = identity.list_sessions(principal.id)
    # Only a session (or an SSO session) has a "this one" to mark; an API key
    # never reads this route as itself, since it authenticates a user, not a
    # session, and carries no session id.
    current_id = principal.credential_id if principal.via in ("session", "oidc") else None
    for session in sessions:
        session["current"] = bool(current_id) and session["id"] == current_id
    return sessions


@router.delete("/api/auth/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    principal = _require_account(request)
    removed = identity.revoke_session(principal.id, session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such session")
    audit.record("session.revoke", principal=principal, object_type="session",
                 object_id=session_id, ip=identity.client_ip(request))
    return {"deleted": True}


@router.post("/api/auth/sessions/revoke-others")
async def revoke_other_sessions(request: Request):
    principal = _require_account(request)
    keep_id = principal.credential_id if principal.via in ("session", "oidc") else None
    count = identity.revoke_other_sessions(principal.id, keep_id)
    audit.record("session.revoke_others", principal=principal, object_type="session",
                 ip=identity.client_ip(request), details={"count": count})
    return {"revoked": count}


# ── password ─────────────────────────────────────────────────────────────────

@router.post("/api/auth/password")
async def change_password(request: Request, payload: PasswordChange):
    """Change the caller's own password. Drops every session of theirs,
    the one making this request included: see ``identity.set_password``.
    The frontend is told so it can send them back to the login screen
    instead of letting the next click surface a bare 401."""
    principal = _require_account(request)
    if not identity.local_passwords_enabled():
        raise HTTPException(status_code=403,
                            detail="Local passwords are turned off for this hub")
    user = identity.get_user(principal.id)
    if not user or not user.get("has_password"):
        raise HTTPException(status_code=403,
                            detail="This account has no password to change")
    if not payload.current_password or not _verify_current_password(
            principal.id, payload.current_password):
        raise HTTPException(status_code=403, detail="Current password is wrong")
    if not payload.password:
        raise HTTPException(status_code=400, detail="A new password is required")
    identity.set_password(principal.id, payload.password)
    audit.record("auth.password", principal=principal, object_type="user",
                 object_id=principal.id, ip=identity.client_ip(request))
    return {"ok": True, "relogin": True}


# ── my API keys ──────────────────────────────────────────────────────────────

@router.get("/api/auth/keys")
async def get_my_keys(request: Request) -> List[dict]:
    principal = _require_account(request)
    return api_keys.list_keys(principal.id)


@router.post("/api/auth/keys")
async def post_my_key(request: Request, payload: KeyCreate):
    """Cut a personal key. A non-admin may only scope it to workspaces they
    are themselves a member of, so a key never reaches further than its
    owner already does; leaving ``workspaces`` off is allowed for anyone,
    since an unscoped key still only reaches what its owner reaches."""
    principal = _require_account(request)
    if payload.workspaces is not None and not principal.is_admin:
        allowed = set(identity.workspaces_for_user(principal.id))
        requested = {str(w).strip() for w in payload.workspaces if str(w).strip()}
        outside = sorted(requested - allowed)
        if outside:
            raise HTTPException(
                status_code=403,
                detail=f"you are not a member of: {', '.join(outside)}")
    try:
        key, record = api_keys.create_key(
            principal.id, name=payload.name, workspaces=payload.workspaces,
            expires_in_days=payload.expires_in_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("key.create", principal=principal, object_type="api_key",
                 object_id=record["id"], ip=identity.client_ip(request),
                 details={"name": record["name"], "workspaces": record["workspaces"],
                          "expires_at": record["expires_at"]})
    # The key is returned exactly once: the record never carries it again.
    return {**record, "key": key}


@router.delete("/api/auth/keys/{key_id}")
async def delete_my_key(request: Request, key_id: str):
    principal = _require_account(request)
    removed = api_keys.revoke_key(key_id, user_id=principal.id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such key")
    audit.record("key.revoke", principal=principal, object_type="api_key",
                 object_id=key_id, ip=identity.client_ip(request))
    return {"deleted": True}


# ── an administrator, on someone else's keys ─────────────────────────────────

@router.get("/api/auth/users/{user_id}/keys")
async def get_user_keys(request: Request, user_id: str) -> List[dict]:
    _require_multi()
    identity.require_role(_principal(request), admin=True)
    return api_keys.list_keys(user_id)


@router.delete("/api/auth/users/{user_id}/keys/{key_id}")
async def delete_user_key(request: Request, user_id: str, key_id: str):
    _require_multi()
    principal = _principal(request)
    identity.require_role(principal, admin=True)
    removed = api_keys.revoke_key(key_id, user_id=user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such key")
    audit.record("key.revoke", principal=principal, object_type="api_key",
                 object_id=key_id, ip=identity.client_ip(request),
                 details={"owner": user_id})
    return {"deleted": True}
