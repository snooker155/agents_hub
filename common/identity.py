"""
Identity, authentication and authorization: the stateful half.

:mod:`common.auth` holds the predicates, which are functions of their
arguments. This module holds everything that has to look something up: users,
password hashes, sessions, workspace membership, the service credential the
hub's own subprocesses carry, and the resolution of a live request into a
:class:`~common.auth.Principal`.

Three modes, chosen with ``AUTH_MODE`` (see docs/identity.md):

``single`` (the default)
    One operator on this machine or host. No login, no users, no roles, no
    owner checks: the API behaves exactly as it did before any of this
    existed, and every ownable record is owned by the constant
    ``LOCAL_OPERATOR_ID``. Nothing here touches the database in this mode.

``token``
    The shared ``AGENTS_HUB_API_TOKEN`` gates ``/api``. Still one operator.
    Reached either by setting ``AUTH_MODE=token`` or, for compatibility, by
    configuring the token and leaving ``AUTH_MODE`` alone.

``multi``
    Named users with passwords, sessions, a global role (``admin`` /
    ``member``) and per-workspace membership roles (``owner`` / ``editor`` /
    ``viewer``).

Why passwords are stored the way they are: PBKDF2-HMAC-SHA256 with a per-user
salt and a per-user iteration count, from the standard library. It is not the
strongest KDF available, but it is the strongest one that adds no dependency,
and the alternative to a dependency-free hash here is not argon2, it is people
running this with plaintext passwords or no login at all.

Why sessions are opaque rather than signed: a JWT cannot be revoked without
building the very table a JWT is meant to avoid. A random ``token_urlsafe``
string, stored as its SHA-256, gives logout, expiry and "log everyone out"
for free, and a stolen database yields no usable session.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from common import db
from common.auth import (
    LOCAL_OPERATOR_ID,
    LOCAL_PRINCIPAL,
    MULTI,
    Principal,
    ROLE_ADMIN,
    ROLE_MEMBER,
    SINGLE,
    TOKEN,
    TOKEN_PRINCIPAL,
    WORKSPACE_ROLES,
    WS_OWNER,
    authorize,
    effective_auth_mode,
    extract_bearer,
    is_open_path,
    required_workspace_role,
    workspace_from_request,
)

#: Cost of one password hash. Raise it, never lower it: an existing user's
#: stored iteration count is what verifies them, so old rows keep working and
#: are re-hashed the next time their password is set.
PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16
_SESSION_BYTES = 32

#: Environment variable that carries the service credential to subprocesses.
SERVICE_TOKEN_ENV = "AGENTS_HUB_SERVICE_TOKEN"

#: The service credential's principal: admin, because the relays it
#: authenticates (run state, stream notifications, session broker) write on
#: behalf of whoever started the run, across any workspace.
SERVICE_PRINCIPAL = Principal(id="service", username="service",
                              role=ROLE_ADMIN, kind="service")

#: Set by the middleware for the duration of one request, read by the stores
#: that stamp an owner onto a new record. A contextvar rather than a parameter
#: so ``create_task``/``save_chat``/``create_workspace_folder`` keep the
#: signatures every caller already uses, including the ones that run in an
#: agent subprocess where there is no request at all.
_current_user: ContextVar[Optional[str]] = ContextVar("agents_hub_current_user",
                                                      default=None)

#: Minted once per process when ``multi`` is in force and no API token is
#: configured. In memory only: it dies with the process that issued it, which
#: is exactly the lifetime the subprocesses it authenticates have.
_service_token: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


# ── mode ─────────────────────────────────────────────────────────────────────

def current_mode() -> str:
    """The mode in force right now, read live from the settings object.

    Read through here rather than off ``settings.auth_mode``: the tests (and
    the Settings page) mutate the settings object at runtime, and the
    token-implies-token-mode compatibility rule has to be applied every time.
    """
    from common.config import settings
    return effective_auth_mode(getattr(settings, "auth_mode", SINGLE),
                               getattr(settings, "api_token", "") or "")


def mode_features() -> Dict[str, bool]:
    """What the frontend should render, decided here rather than in the browser.

    The UI asks one question ("what mode are we in?") and gets back the answers
    it would otherwise have to derive, so a future fourth mode does not mean
    editing a chain of ``mode === 'multi'`` checks across the pages.
    """
    mode = current_mode()
    return {
        "login": mode == MULTI,
        "users": mode == MULTI,
        "members": mode == MULTI,
        "api_token": mode == TOKEN,
    }


# ── passwords ────────────────────────────────────────────────────────────────

def hash_password(password: str, *, salt: Optional[str] = None,
                  iterations: int = PBKDF2_ITERATIONS) -> Dict[str, Any]:
    """Derive a stored password record. Never stores the password itself."""
    if not password:
        raise ValueError("password must not be empty")
    salt = salt or secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), iterations)
    return {"password_hash": digest.hex(), "password_salt": salt,
            "password_iterations": iterations}


def verify_password(password: str, *, password_hash: str, password_salt: str,
                    password_iterations: int) -> bool:
    """Constant-time check of a password against a stored record."""
    if not password or not password_hash:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                    (password_salt or "").encode("utf-8"),
                                    int(password_iterations or PBKDF2_ITERATIONS))
    return hmac.compare_digest(candidate.hex(), password_hash)


# ── users ────────────────────────────────────────────────────────────────────

def _row_to_user(row) -> Dict[str, Any]:
    """A user as the API returns it: everything but the password record."""
    return {
        "id": row["user_id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"] or ROLE_MEMBER,
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def user_count() -> int:
    return int(db.get_conn().execute("SELECT COUNT(*) FROM users").fetchone()[0])


def bootstrap_required() -> bool:
    """True when ``multi`` is on and nobody can log in yet.

    This is what makes the first run possible at all: with no users there is no
    admin to create the first user, so ``POST /api/auth/bootstrap`` is open
    until exactly one account exists and closed forever after.
    """
    return current_mode() == MULTI and user_count() == 0


def list_users() -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM users ORDER BY username").fetchall()
    return [_row_to_user(r) for r in rows]


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE username = ?",
        ((username or "").strip().lower(),)).fetchone()
    return _row_to_user(row) if row else None


def create_user(username: str, password: str, *, role: str = ROLE_MEMBER,
                display_name: str = "") -> Dict[str, Any]:
    """Add a user. Raises ValueError on a bad or taken username."""
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("username is required")
    if len(username) > 64 or any(c.isspace() for c in username):
        raise ValueError("username must be one word of at most 64 characters")
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise ValueError(f"unknown role '{role}'")
    record = hash_password(password)
    now = _iso(_now())
    user_id = secrets.token_hex(8)
    with db.transaction() as conn:
        taken = conn.execute("SELECT 1 FROM users WHERE username = ?",
                             (username,)).fetchone()
        if taken:
            raise ValueError(f"username '{username}' is already taken")
        conn.execute(
            "INSERT INTO users (user_id, username, display_name, role, "
            "password_hash, password_salt, password_iterations, disabled, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (user_id, username, display_name.strip() or username, role,
             record["password_hash"], record["password_salt"],
             record["password_iterations"], now, now),
        )
    return get_user(user_id)  # type: ignore[return-value]


def update_user(user_id: str, *, role: Optional[str] = None,
                display_name: Optional[str] = None,
                disabled: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """Change a user's role, display name or enabled state.

    Refuses to remove the last admin: an installation with no admin has no way
    back short of editing the database by hand.
    """
    user = get_user(user_id)
    if user is None:
        return None
    if role is not None and role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise ValueError(f"unknown role '{role}'")
    demoting = (user["role"] == ROLE_ADMIN
                and ((role is not None and role != ROLE_ADMIN) or disabled is True))
    if demoting and _admin_count() <= 1:
        raise ValueError("this is the last admin: promote another user first")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET role = ?, display_name = ?, disabled = ?, "
            "updated_at = ? WHERE user_id = ?",
            (role if role is not None else user["role"],
             display_name.strip() if display_name is not None else user["display_name"],
             1 if (user["disabled"] if disabled is None else disabled) else 0,
             _iso(_now()), str(user_id)),
        )
    return get_user(user_id)


def set_password(user_id: str, password: str) -> bool:
    """Set a user's password and drop every session they held.

    A password change that leaves the old sessions alive is not a password
    change: the point of resetting one is usually that somebody else has it.
    """
    record = hash_password(password)
    with db.transaction() as conn:
        cursor = conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, "
            "password_iterations = ?, updated_at = ? WHERE user_id = ?",
            (record["password_hash"], record["password_salt"],
             record["password_iterations"], _iso(_now()), str(user_id)),
        )
        if not cursor.rowcount:
            return False
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (str(user_id),))
    return True


def delete_user(user_id: str) -> bool:
    """Remove a user, their sessions and their memberships."""
    user = get_user(user_id)
    if user is None:
        return False
    if user["role"] == ROLE_ADMIN and _admin_count() <= 1:
        raise ValueError("this is the last admin: promote another user first")
    with db.transaction() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (str(user_id),))
        conn.execute("DELETE FROM workspace_members WHERE user_id = ?", (str(user_id),))
        conn.execute("DELETE FROM users WHERE user_id = ?", (str(user_id),))
    return True


def _admin_count() -> int:
    return int(db.get_conn().execute(
        "SELECT COUNT(*) FROM users WHERE role = ? AND disabled = 0",
        (ROLE_ADMIN,)).fetchone()[0])


def create_first_admin(username: str, password: str,
                       display_name: str = "") -> Dict[str, Any]:
    """The one-shot bootstrap. Raises ValueError once any user exists."""
    if current_mode() != MULTI:
        raise ValueError("bootstrap is only available when AUTH_MODE=multi")
    if user_count() > 0:
        raise ValueError("this installation already has users")
    return create_user(username, password, role=ROLE_ADMIN,
                       display_name=display_name)


# ── sessions ─────────────────────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def login(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Check a password and open a session. Returns ``{token, expires_at, user}``.

    Returns None for a wrong username, a wrong password and a disabled account
    alike: which of the three it was is information the caller has not earned.
    """
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE username = ?",
        ((username or "").strip().lower(),)).fetchone()
    if row is None or row["disabled"]:
        # Still spend the hashing time, so a missing username and a wrong
        # password take the same wall clock to answer.
        hash_password(password or "x")
        return None
    if not verify_password(password, password_hash=row["password_hash"],
                           password_salt=row["password_salt"],
                           password_iterations=row["password_iterations"]):
        return None

    from common.config import settings
    hours = max(1, int(getattr(settings, "auth_session_hours", 24 * 14) or 1))
    token = secrets.token_urlsafe(_SESSION_BYTES)
    now = _now()
    expires = now + timedelta(hours=hours)
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auth_sessions "
            "(token_hash, user_id, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_token_hash(token), row["user_id"], _iso(now), _iso(expires), _iso(now)),
        )
    return {"token": token, "expires_at": _iso(expires),
            "user": _row_to_user(row)}


def logout(token: str) -> bool:
    """Close one session. Idempotent: an unknown token is already logged out."""
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?",
                              (_token_hash(token),))
    return bool(cursor.rowcount)


def user_for_session(token: str) -> Optional[Dict[str, Any]]:
    """The user a session token belongs to, or None when it is unusable.

    Expired rows are deleted on the way past rather than by a sweeper: a
    session is only ever looked at when it is presented, so that is the one
    moment its expiry is worth checking.
    """
    if not token:
        return None
    row = db.get_conn().execute(
        "SELECT s.expires_at AS expires_at, u.* FROM auth_sessions s "
        "JOIN users u ON u.user_id = s.user_id WHERE s.token_hash = ?",
        (_token_hash(token),)).fetchone()
    if row is None:
        return None
    try:
        expired = datetime.fromisoformat(row["expires_at"]) <= _now()
    except (TypeError, ValueError):
        expired = True
    if expired or row["disabled"]:
        with db.transaction() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?",
                         (_token_hash(token),))
        return None
    return _row_to_user(row)


# ── workspace membership ─────────────────────────────────────────────────────

def membership_role(workspace: str, user_id: str) -> Optional[str]:
    """This user's role in this workspace, or None when they are not a member."""
    row = db.get_conn().execute(
        "SELECT role FROM workspace_members WHERE workspace = ? AND user_id = ?",
        (str(workspace), str(user_id))).fetchone()
    return row["role"] if row else None


def list_members(workspace: str) -> List[Dict[str, Any]]:
    """Everyone with a role in this workspace, with their account details."""
    rows = db.get_conn().execute(
        "SELECT m.role AS ws_role, m.created_at AS joined_at, u.* "
        "FROM workspace_members m JOIN users u ON u.user_id = m.user_id "
        "WHERE m.workspace = ? ORDER BY u.username", (str(workspace),)).fetchall()
    return [{**_row_to_user(r), "workspace_role": r["ws_role"],
             "joined_at": r["joined_at"]} for r in rows]


def set_member(workspace: str, user_id: str, role: str) -> Dict[str, Any]:
    """Add a member or change their role."""
    if role not in WORKSPACE_ROLES:
        raise ValueError(f"unknown workspace role '{role}'")
    if get_user(user_id) is None:
        raise ValueError(f"no such user: {user_id}")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO workspace_members (workspace, user_id, role, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(workspace, user_id) DO UPDATE SET role = excluded.role",
            (str(workspace), str(user_id), role, _iso(_now())),
        )
    return {"workspace": str(workspace), "user_id": str(user_id), "role": role}


def remove_member(workspace: str, user_id: str) -> bool:
    """Drop a membership. Refuses to leave a workspace with no owner."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT role FROM workspace_members WHERE workspace = ? AND user_id = ?",
            (str(workspace), str(user_id))).fetchone()
        if row is None:
            return False
        if row["role"] == WS_OWNER:
            owners = conn.execute(
                "SELECT COUNT(*) FROM workspace_members WHERE workspace = ? AND role = ?",
                (str(workspace), WS_OWNER)).fetchone()[0]
            if int(owners) <= 1:
                raise ValueError("this is the workspace's last owner")
        conn.execute(
            "DELETE FROM workspace_members WHERE workspace = ? AND user_id = ?",
            (str(workspace), str(user_id)))
    return True


def workspaces_for_user(user_id: str) -> List[str]:
    rows = db.get_conn().execute(
        "SELECT workspace FROM workspace_members WHERE user_id = ?",
        (str(user_id),)).fetchall()
    return [r["workspace"] for r in rows]


def claim_workspace(workspace: str, user_id: Optional[str] = None) -> None:
    """Make the creator of a workspace its owner.

    A no-op outside ``multi`` and for the local operator: there is nobody to
    own anything when there is only one of you.
    """
    if current_mode() != MULTI:
        return
    user_id = user_id or current_user_id()
    if not user_id or user_id == LOCAL_OPERATOR_ID:
        return
    if get_user(user_id) is None:
        return
    set_member(workspace, user_id, WS_OWNER)


# ── the service credential ───────────────────────────────────────────────────

def service_token() -> str:
    """The credential the hub's own subprocess relays authenticate with.

    In ``token`` mode that is the configured API token, which is what those
    relays have always used (``common.auth.auth_headers``). In ``multi`` mode
    with no token configured there is nothing for a subprocess to present, since
    it has no session and no password, so one random token is minted per backend
    process, kept in memory, and exported to every subprocess through
    ``base_subprocess_env``. It never reaches disk and never outlives the
    process that issued it.
    """
    global _service_token
    from common.config import settings
    configured = (getattr(settings, "api_token", "") or "").strip()
    if configured:
        return configured
    if _service_token is None:
        # A subprocess re-entering this function must reuse the token it was
        # given, not mint a second one nobody will accept.
        inherited = os.environ.get(SERVICE_TOKEN_ENV, "").strip()
        _service_token = inherited or secrets.token_urlsafe(_SESSION_BYTES)
    return _service_token


def is_service_token(presented: str) -> bool:
    """Whether a presented credential is the service one (constant time)."""
    if not presented:
        return False
    return hmac.compare_digest(presented, service_token())


# ── the current user, for the stores ─────────────────────────────────────────

def current_user_id() -> str:
    """Who owns whatever is being created right now.

    ``LOCAL_OPERATOR_ID`` unless a request carrying a named user is in flight,
    which is what lets the task, chat and workspace stores stamp an owner
    without any of their callers passing one. Agent subprocesses and background
    jobs have no request, so they get the local operator too: correct in
    ``single`` and ``token`` mode, and in ``multi`` mode an honest "the system
    did this" rather than a user who did not.
    """
    return _current_user.get() or LOCAL_OPERATOR_ID


def set_current_user(user_id: Optional[str]):
    """Bind the current user for this context; returns the reset token."""
    return _current_user.set(user_id)


def reset_current_user(token) -> None:
    _current_user.reset(token)


# ── request resolution ───────────────────────────────────────────────────────

def presented_credential(request) -> Optional[str]:
    """The credential on a request, from any of the three places it may ride.

    The ``?token=`` query form exists for the browser's EventSource, which
    cannot set headers and is how the whole UI receives live updates.
    """
    return (extract_bearer(request.headers.get("authorization"))
            or request.headers.get("x-api-token")
            or request.query_params.get("token"))


def current_principal(request) -> Optional[Principal]:
    """Resolve a request to a principal, or None when it is unauthenticated.

    Mode by mode: ``single`` is always the local operator, ``token`` matches
    the shared token, and ``multi`` accepts a session token, the service
    credential, or nothing.
    """
    mode = current_mode()
    if mode == SINGLE:
        return LOCAL_PRINCIPAL

    presented = presented_credential(request)
    if mode == TOKEN:
        from common.config import settings
        configured = (settings.api_token or "").strip()
        if presented and hmac.compare_digest(presented, configured):
            return TOKEN_PRINCIPAL
        return None

    if not presented:
        return None
    if is_service_token(presented):
        return SERVICE_PRINCIPAL
    user = user_for_session(presented)
    if user is None:
        return None
    return Principal(id=user["id"], username=user["username"],
                     role=user["role"], kind="user")


def authorize_request(request) -> tuple[bool, Optional[Principal]]:
    """The whole middleware decision for one request: (allowed, principal).

    Resolves the principal, works out which workspace (if any) the request
    targets, looks up the membership that workspace needs, and hands all of it
    to the pure :func:`~common.auth.authorize`. The split is what keeps the
    matrix testable: everything interesting about the decision is in a
    function that takes values, not a request.
    """
    method, path = request.method, request.url.path
    mode = current_mode()
    if mode == SINGLE:
        return True, LOCAL_PRINCIPAL
    if is_open_path(method, path):
        return True, None

    principal = current_principal(request)
    workspace = None
    role = None
    if mode == MULTI and principal is not None and not principal.is_admin:
        workspace = workspace_from_request(
            path=path,
            query_workspace=request.query_params.get("workspace"),
            header_workspace=request.headers.get("x-workspace"),
        )
        if workspace:
            role = membership_role(workspace, principal.id)
    allowed = authorize(mode, principal=principal, method=method, path=path,
                        workspace=workspace, membership_role=role)
    return allowed, principal


def require_role(principal: Optional[Principal], *, admin: bool = False,
                 workspace: Optional[str] = None,
                 role: str = WS_OWNER) -> None:
    """Raise 403/401 unless the principal clears the bar. For use in routes.

    Outside ``multi`` there is nothing to check: one operator is every role.
    """
    from fastapi import HTTPException
    if current_mode() != MULTI:
        return
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if admin and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    if workspace and not principal.is_admin:
        from common.auth import role_satisfies
        if not role_satisfies(membership_role(workspace, principal.id), role):
            raise HTTPException(
                status_code=403,
                detail=f"'{role}' access to workspace '{workspace}' is required")


def request_principal(request) -> Optional[Principal]:
    """The principal the middleware put on the request, for a route to read."""
    principal = getattr(request.state, "principal", None)
    if principal is None and current_mode() == SINGLE:
        return LOCAL_PRINCIPAL
    return principal


def principal_dict(principal: Optional[Principal]) -> Optional[Dict[str, Any]]:
    """A principal as JSON, for ``GET /api/auth/me``."""
    return asdict(principal) if principal is not None else None


__all__ = [
    "PBKDF2_ITERATIONS", "SERVICE_PRINCIPAL", "SERVICE_TOKEN_ENV",
    "authorize_request", "bootstrap_required", "claim_workspace",
    "create_first_admin", "create_user", "current_mode", "current_principal",
    "current_user_id", "delete_user", "get_user", "get_user_by_username",
    "hash_password", "is_service_token", "list_members", "list_users", "login",
    "logout", "membership_role", "mode_features", "presented_credential",
    "principal_dict",
    "remove_member", "request_principal", "require_role", "reset_current_user",
    "service_token", "set_current_user", "set_member", "set_password",
    "update_user", "user_count", "user_for_session", "verify_password",
    "workspaces_for_user", "required_workspace_role",
]
