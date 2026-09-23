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
    ``viewer``). Stage 3 adds, on top of the same tables: accounts linked to
    an external identity (single sign-on, ``common/oidc.py``, or SCIM
    provisioning), groups and the rules that turn them into roles
    (``common/groups.py``), personal API keys that act as their owner
    (``common/api_keys.py``), session metadata and revocation, a login
    throttle, and the audit trail (``common/audit.py``).

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
from typing import Any, Dict, List, Optional, Tuple

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

#: Where an account came from.
SOURCE_LOCAL = "local"
SOURCE_OIDC = "oidc"
SOURCE_SCIM = "scim"

#: How a session was opened.
SESSION_PASSWORD = "password"
SESSION_OIDC = "oidc"
SESSION_BOOTSTRAP = "bootstrap"


class LoginThrottled(Exception):
    """Too many failed logins for this account or address in the window."""

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
    from common.config import settings
    oidc = bool((getattr(settings, "auth_oidc_issuer", "") or "").strip())
    return {
        "login": mode == MULTI,
        "users": mode == MULTI,
        "members": mode == MULTI,
        "api_token": mode == TOKEN,
        "oidc": mode == MULTI and oidc,
        # The password form is shown when passwords are on, or when there is
        # no other way in.
        "local_passwords": mode == MULTI and (local_passwords_enabled() or not oidc),
        "api_keys": mode == MULTI,
        "groups": mode == MULTI,
        "audit": mode != SINGLE,
        "scim": mode == MULTI and bool((getattr(settings, "auth_scim_token", "") or "").strip()),
    }


def local_passwords_enabled() -> bool:
    from common.config import settings
    return bool(getattr(settings, "auth_local_passwords", True))


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
    keys = row.keys() if hasattr(row, "keys") else ()

    def col(name, default=None):
        return row[name] if name in keys else default

    return {
        "id": row["user_id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": row["role"] or ROLE_MEMBER,
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "email": col("email") or "",
        "source": col("source") or SOURCE_LOCAL,
        "external_issuer": col("external_issuer"),
        "external_subject": col("external_subject"),
        "external_id": col("external_id"),
        "role_source": col("role_source") or "manual",
        # Whether a password login is possible at all: an account provisioned
        # by SSO or SCIM has no password until an admin sets one.
        "has_password": bool(col("password_hash")),
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


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    email = (email or "").strip().lower()
    if not email:
        return None
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_external(issuer: str, subject: str) -> Optional[Dict[str, Any]]:
    """The account linked to an external identity (``iss`` + ``sub``)."""
    if not issuer or not subject:
        return None
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE external_issuer = ? AND external_subject = ?",
        (str(issuer), str(subject))).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_external_id(external_id: str) -> Optional[Dict[str, Any]]:
    """The account a SCIM ``externalId`` names."""
    if not external_id:
        return None
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE external_id = ?", (str(external_id),)).fetchone()
    return _row_to_user(row) if row else None


def _validate_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("username is required")
    if len(username) > 128 or any(c.isspace() for c in username):
        raise ValueError("username must be one word of at most 128 characters")
    return username


def create_user(username: str, password: Optional[str], *, role: str = ROLE_MEMBER,
                display_name: str = "", email: str = "", source: str = SOURCE_LOCAL,
                external_issuer: Optional[str] = None,
                external_subject: Optional[str] = None,
                external_id: Optional[str] = None) -> Dict[str, Any]:
    """Add a user. Raises ValueError on a bad or taken username.

    ``password`` may be None for an account that arrives from single sign-on
    or SCIM: it then has no password at all (``has_password`` is false) until
    an administrator sets one, and can only sign in through its provider.
    """
    username = _validate_username(username)
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise ValueError(f"unknown role '{role}'")
    if password:
        record = hash_password(password)
    else:
        record = {"password_hash": "", "password_salt": "", "password_iterations": 0}
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
            "created_at, updated_at, email, source, external_issuer, external_subject, "
            "external_id, role_source) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, (display_name or "").strip() or username, role,
             record["password_hash"], record["password_salt"],
             record["password_iterations"], now, now, (email or "").strip().lower(),
             source or SOURCE_LOCAL, external_issuer, external_subject, external_id,
             "manual"),
        )
    return get_user(user_id)  # type: ignore[return-value]


def update_user(user_id: str, *, role: Optional[str] = None,
                display_name: Optional[str] = None,
                disabled: Optional[bool] = None,
                email: Optional[str] = None,
                username: Optional[str] = None,
                role_source: Optional[str] = None,
                external_issuer: Optional[str] = None,
                external_subject: Optional[str] = None,
                external_id: Optional[str] = None,
                source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Change a user's role, display name, enabled state or identity fields.

    Refuses to remove the last admin: an installation with no admin has no way
    back short of editing the database by hand. Disabling an account drops
    its sessions and revokes its API keys, so a deprovisioned person is out
    at once, not at their next expiry.
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
    new_username = _validate_username(username) if username is not None else user["username"]
    now_disabled = user["disabled"] if disabled is None else bool(disabled)
    with db.transaction() as conn:
        if new_username != user["username"]:
            taken = conn.execute("SELECT 1 FROM users WHERE username = ? AND user_id <> ?",
                                 (new_username, str(user_id))).fetchone()
            if taken:
                raise ValueError(f"username '{new_username}' is already taken")
        conn.execute(
            "UPDATE users SET role = ?, display_name = ?, disabled = ?, "
            "updated_at = ?, email = ?, username = ?, role_source = ?, "
            "external_issuer = ?, external_subject = ?, external_id = ?, source = ? "
            "WHERE user_id = ?",
            (role if role is not None else user["role"],
             display_name.strip() if display_name is not None else user["display_name"],
             1 if now_disabled else 0,
             _iso(_now()),
             email.strip().lower() if email is not None else user["email"],
             new_username,
             role_source if role_source is not None else (
                 "manual" if role is not None else user["role_source"]),
             external_issuer if external_issuer is not None else user["external_issuer"],
             external_subject if external_subject is not None else user["external_subject"],
             external_id if external_id is not None else user["external_id"],
             source if source is not None else user["source"],
             str(user_id)),
        )
        if now_disabled and not user["disabled"]:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (str(user_id),))
    if now_disabled and not user["disabled"]:
        from common import api_keys
        api_keys.revoke_all(user_id)
    return get_user(user_id)


def upsert_external_user(issuer: str, subject: str, *, username: str = "",
                         email: str = "", display_name: str = "",
                         source: str = SOURCE_OIDC) -> Dict[str, Any]:
    """The account an external identity maps to, created or linked on the way.

    Lookup order: the ``iss``+``sub`` link; then, when
    ``AUTH_OIDC_LINK_BY_EMAIL`` is on, an existing account with the same
    email or username (a person who had a local account before SSO arrived
    keeps it, with its memberships); else a new account with no password.
    A disabled account is returned disabled: linking never re-enables.
    """
    from common.config import settings
    user = get_user_by_external(issuer, subject)
    if user is None and bool(getattr(settings, "auth_oidc_link_by_email", True)):
        user = get_user_by_email(email) if email else None
        if user is None and username:
            user = get_user_by_username(username)
        if user is not None and not user.get("external_issuer"):
            user = update_user(user["id"], external_issuer=issuer, external_subject=subject)
        elif user is not None:
            # Linked to a different external identity already: a different
            # person with the same email at another provider. Not ours.
            user = None
    if user is None:
        base = _validate_username(username or email or f"{source}-{subject}")
        candidate = base
        n = 1
        while get_user_by_username(candidate) is not None:
            n += 1
            candidate = f"{base}-{n}"
        return create_user(candidate, None, display_name=display_name or candidate,
                           email=email, source=source, external_issuer=issuer,
                           external_subject=subject)
    changes: Dict[str, Any] = {}
    if email and email.strip().lower() != (user.get("email") or ""):
        changes["email"] = email
    if display_name and display_name != user["display_name"]:
        changes["display_name"] = display_name
    if changes:
        user = update_user(user["id"], **changes) or user
    return user


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
        conn.execute("DELETE FROM group_members WHERE user_id = ?", (str(user_id),))
        conn.execute("DELETE FROM api_keys WHERE user_id = ?", (str(user_id),))
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


def open_session(user_id: str, *, kind: str = SESSION_PASSWORD, ip: Optional[str] = None,
                 user_agent: Optional[str] = None,
                 hours: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Open a session for a known, enabled user.

    Returns ``{token, expires_at, session_id, user}`` or None for an unknown
    or disabled account. The credential check happens before this: a
    verified password (:func:`login`), a verified id token
    (``common/oidc.py``), or the bootstrap.
    """
    row = db.get_conn().execute("SELECT * FROM users WHERE user_id = ?",
                                (str(user_id),)).fetchone()
    if row is None or row["disabled"]:
        return None
    from common.config import settings
    if hours is None:
        if kind == SESSION_OIDC:
            hours = int(getattr(settings, "auth_oidc_session_hours", 8) or 8)
        else:
            hours = int(getattr(settings, "auth_session_hours", 24 * 14) or 1)
    hours = max(1, int(hours))
    token = secrets.token_urlsafe(_SESSION_BYTES)
    session_id = secrets.token_hex(8)
    now = _now()
    expires = now + timedelta(hours=hours)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at, "
            "last_seen_at, session_id, kind, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_token_hash(token), row["user_id"], _iso(now), _iso(expires), _iso(now),
             session_id, kind, (ip or "")[:64] or None, (user_agent or "")[:256] or None),
        )
    return {"token": token, "expires_at": _iso(expires), "session_id": session_id,
            "kind": kind, "user": _row_to_user(row)}


def _login_window() -> Tuple[int, int]:
    from common.config import settings
    attempts = int(getattr(settings, "auth_login_max_attempts", 10) or 0)
    minutes = int(getattr(settings, "auth_login_window_minutes", 15) or 15)
    return max(0, attempts), max(1, minutes)


def login_throttled(username: str, ip: Optional[str] = None) -> bool:
    """Whether this account or address has failed too often lately."""
    limit, minutes = _login_window()
    if limit <= 0:
        return False
    since = _iso(_now() - timedelta(minutes=minutes))
    conn = db.get_conn()
    name = (username or "").strip().lower()
    by_name = int(conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = ? AND at >= ?",
        (name, since)).fetchone()[0])
    if by_name >= limit:
        return True
    if ip:
        by_ip = int(conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND at >= ?",
            (ip, since)).fetchone()[0])
        # An address gets more room than an account: an office NAT is many
        # people, and a wrong password by one must not lock out the rest.
        if by_ip >= limit * 5:
            return True
    return False


def _record_failed_login(username: str, ip: Optional[str]) -> None:
    _, minutes = _login_window()
    now = _now()
    with db.transaction() as conn:
        conn.execute("INSERT INTO login_attempts (username, ip, at) VALUES (?, ?, ?)",
                     ((username or "").strip().lower(), ip, _iso(now)))
        # Keep the table small: nothing older than the window matters.
        conn.execute("DELETE FROM login_attempts WHERE at < ?",
                     (_iso(now - timedelta(minutes=minutes * 2)),))


def _clear_failed_logins(username: str) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM login_attempts WHERE username = ?",
                     ((username or "").strip().lower(),))


def login(username: str, password: str, *, ip: Optional[str] = None,
          user_agent: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Check a password and open a session. Returns ``{token, expires_at, user}``.

    Returns None for a wrong username, a wrong password and a disabled account
    alike: which of the three it was is information the caller has not earned.
    Raises :class:`LoginThrottled` once the account (or the address) has
    failed too often in the window. With ``AUTH_LOCAL_PASSWORDS`` off only
    administrators may still sign in this way: the emergency door for when
    the identity provider is down.
    """
    if login_throttled(username, ip):
        raise LoginThrottled()
    row = db.get_conn().execute(
        "SELECT * FROM users WHERE username = ?",
        ((username or "").strip().lower(),)).fetchone()
    if row is None or row["disabled"] or not row["password_hash"]:
        # Still spend the hashing time, so a missing username and a wrong
        # password take the same wall clock to answer.
        hash_password(password or "x")
        _record_failed_login(username, ip)
        return None
    if not verify_password(password, password_hash=row["password_hash"],
                           password_salt=row["password_salt"],
                           password_iterations=row["password_iterations"]):
        _record_failed_login(username, ip)
        return None
    if not local_passwords_enabled() and (row["role"] or ROLE_MEMBER) != ROLE_ADMIN:
        return None
    _clear_failed_logins(username)
    return open_session(row["user_id"], kind=SESSION_PASSWORD, ip=ip, user_agent=user_agent)


def logout(token: str) -> bool:
    """Close one session. Idempotent: an unknown token is already logged out."""
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?",
                              (_token_hash(token),))
    return bool(cursor.rowcount)


def _session_row(token: str):
    if not token:
        return None
    return db.get_conn().execute(
        "SELECT s.expires_at AS expires_at, s.session_id AS session_id, "
        "s.kind AS session_kind, s.last_seen_at AS last_seen_at, u.* "
        "FROM auth_sessions s JOIN users u ON u.user_id = s.user_id "
        "WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()


def session_for_token(token: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """``(user, session)`` for a presented session token, or None when it is
    unusable.

    Expired rows are deleted on the way past rather than by a sweeper: a
    session is only ever looked at when it is presented, so that is the one
    moment its expiry is worth checking. ``last_seen_at`` is refreshed at
    most once a minute.
    """
    row = _session_row(token)
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
    now = _now()
    try:
        seen = datetime.fromisoformat(row["last_seen_at"] or "")
        stale = (now - seen) >= timedelta(seconds=60)
    except (TypeError, ValueError):
        stale = True
    if stale:
        try:
            with db.transaction() as conn:
                conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE token_hash = ?",
                             (_iso(now), _token_hash(token)))
        except Exception:
            pass
    session = {"session_id": row["session_id"], "kind": row["session_kind"] or SESSION_PASSWORD,
               "expires_at": row["expires_at"]}
    return _row_to_user(row), session


def user_for_session(token: str) -> Optional[Dict[str, Any]]:
    """The user a session token belongs to, or None when it is unusable."""
    found = session_for_token(token)
    return found[0] if found else None


def list_sessions(user_id: str) -> List[Dict[str, Any]]:
    """Every live session of one user, newest first, without token hashes."""
    rows = db.get_conn().execute(
        "SELECT session_id, kind, ip, user_agent, created_at, expires_at, last_seen_at "
        "FROM auth_sessions WHERE user_id = ? ORDER BY created_at DESC",
        (str(user_id),)).fetchall()
    now = _now()
    out = []
    for r in rows:
        try:
            if datetime.fromisoformat(r["expires_at"]) <= now:
                continue
        except (TypeError, ValueError):
            continue
        out.append({"id": r["session_id"], "kind": r["kind"] or SESSION_PASSWORD,
                    "ip": r["ip"], "user_agent": r["user_agent"],
                    "created_at": r["created_at"], "expires_at": r["expires_at"],
                    "last_seen_at": r["last_seen_at"]})
    return out


def revoke_session(user_id: str, session_id: str) -> bool:
    """Close one of a user's sessions by its id."""
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM auth_sessions WHERE user_id = ? AND session_id = ?",
            (str(user_id), str(session_id)))
    return bool(cursor.rowcount)


def revoke_other_sessions(user_id: str, keep_session_id: Optional[str]) -> int:
    """Close every session of a user but the one they are using."""
    with db.transaction() as conn:
        if keep_session_id:
            cursor = conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = ? AND "
                "COALESCE(session_id, '') <> ?", (str(user_id), str(keep_session_id)))
        else:
            cursor = conn.execute("DELETE FROM auth_sessions WHERE user_id = ?",
                                  (str(user_id),))
    return int(cursor.rowcount or 0)


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
        "SELECT m.role AS ws_role, m.created_at AS joined_at, m.source AS ws_source, u.* "
        "FROM workspace_members m JOIN users u ON u.user_id = m.user_id "
        "WHERE m.workspace = ? ORDER BY u.username", (str(workspace),)).fetchall()
    return [{**_row_to_user(r), "workspace_role": r["ws_role"],
             "joined_at": r["joined_at"], "source": r["ws_source"] or "manual"} for r in rows]


def set_member(workspace: str, user_id: str, role: str, *,
               source: str = "manual") -> Dict[str, Any]:
    """Add a member or change their role.

    ``source`` says who granted it: ``manual`` (an owner or admin, through
    the API) or ``group`` (``common/groups.py`` on a login). A manual grant
    marks the row manual even when a group row existed, so the group's
    later disappearance does not take the membership away.
    """
    if role not in WORKSPACE_ROLES:
        raise ValueError(f"unknown workspace role '{role}'")
    if get_user(user_id) is None:
        raise ValueError(f"no such user: {user_id}")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO workspace_members (workspace, user_id, role, created_at, source) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(workspace, user_id) DO UPDATE SET "
            "role = excluded.role, source = excluded.source",
            (str(workspace), str(user_id), role, _iso(_now()), source or "manual"),
        )
    return {"workspace": str(workspace), "user_id": str(user_id), "role": role,
            "source": source or "manual"}


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


def workspace_roles_for_user(user_id: str) -> Dict[str, Dict[str, str]]:
    """``{workspace: {"role", "source"}}`` for one user, for the Account page."""
    rows = db.get_conn().execute(
        "SELECT workspace, role, source FROM workspace_members WHERE user_id = ? "
        "ORDER BY workspace", (str(user_id),)).fetchall()
    return {r["workspace"]: {"role": r["role"], "source": r["source"] or "manual"}
            for r in rows}


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
    from common import api_keys
    if api_keys.looks_like_key(presented):
        found = api_keys.resolve(presented)
        if found is None:
            return None
        user, key = found
        scope = key.get("workspaces")
        return Principal(id=user["id"], username=user["username"], role=user["role"],
                         kind="user", via="api_key",
                         scope=tuple(scope) if scope is not None else None,
                         credential_id=key["id"])
    found = session_for_token(presented)
    if found is None:
        return None
    user, session = found
    return Principal(id=user["id"], username=user["username"],
                     role=user["role"], kind="user",
                     via=("oidc" if session.get("kind") == SESSION_OIDC else "session"),
                     credential_id=session.get("session_id") or "")


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
    if mode == MULTI and principal is not None:
        # The workspace is resolved for everyone: a scoped API key is checked
        # against it before roles matter, an administrator's included. Only
        # the membership lookup is skipped for admins, who bypass it anyway.
        workspace = workspace_from_request(
            path=path,
            query_workspace=request.query_params.get("workspace"),
            header_workspace=request.headers.get("x-workspace"),
        )
        if workspace and not principal.is_admin:
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
    if workspace and not principal.reaches(workspace):
        raise HTTPException(
            status_code=403,
            detail=f"this API key does not reach workspace '{workspace}'")
    if workspace and not principal.is_admin:
        from common.auth import role_satisfies
        if not role_satisfies(membership_role(workspace, principal.id), role):
            raise HTTPException(
                status_code=403,
                detail=f"'{role}' access to workspace '{workspace}' is required")


def request_principal(request) -> Optional[Principal]:
    """The principal the middleware put on the request, for a route to read.

    A route called without a request at all (the CLI's direct backend calls
    route functions in-process) is the operator acting on their own state,
    so it gets the local principal in every mode.
    """
    if request is None:
        return LOCAL_PRINCIPAL
    state = getattr(request, "state", None)
    principal = getattr(state, "principal", None) if state is not None else None
    if principal is None and current_mode() == SINGLE:
        return LOCAL_PRINCIPAL
    return principal


def principal_dict(principal: Optional[Principal]) -> Optional[Dict[str, Any]]:
    """A principal as JSON, for ``GET /api/auth/me``."""
    if principal is None:
        return None
    data = asdict(principal)
    data["scope"] = list(principal.scope) if principal.scope is not None else None
    return data


def client_ip(request) -> Optional[str]:
    """The caller's address, honouring the first ``X-Forwarded-For`` hop when
    the hub sits behind a proxy."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return str(host)[:64] if host else None


__all__ = [
    "LoginThrottled", "PBKDF2_ITERATIONS", "SERVICE_PRINCIPAL", "SERVICE_TOKEN_ENV",
    "SESSION_BOOTSTRAP", "SESSION_OIDC", "SESSION_PASSWORD",
    "SOURCE_LOCAL", "SOURCE_OIDC", "SOURCE_SCIM",
    "authorize_request", "bootstrap_required", "claim_workspace", "client_ip",
    "create_first_admin", "create_user", "current_mode", "current_principal",
    "current_user_id", "delete_user", "get_user", "get_user_by_email",
    "get_user_by_external", "get_user_by_external_id", "get_user_by_username",
    "hash_password", "is_service_token", "list_members", "list_sessions", "list_users",
    "local_passwords_enabled", "login", "login_throttled",
    "logout", "membership_role", "mode_features", "open_session", "presented_credential",
    "principal_dict",
    "remove_member", "request_principal", "require_role", "reset_current_user",
    "revoke_other_sessions", "revoke_session",
    "service_token", "session_for_token", "set_current_user", "set_member", "set_password",
    "update_user", "upsert_external_user", "user_count", "user_for_session",
    "verify_password", "workspace_roles_for_user", "workspaces_for_user",
    "required_workspace_role",
]
