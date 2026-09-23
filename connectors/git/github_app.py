"""
The GitHub App: the hub issues and refreshes GitHub tokens itself.

Why a GitHub App rather than one personal token in Settings: a personal token
is one person's identity, long lived, and reaches everything that person can
reach. An app acts as itself (pull requests come from ``<app>[bot]``), only on
the repositories it was installed on, with tokens that live an hour. And the
same app can act *on behalf of* a person who connected their account, so a
team can choose per agent whether its pull requests are the bot's or the
launching user's (``AgentSpec.github_identity``, "app" or "user").

How the pieces fit:

- **Installation tokens.** The hub signs a ten minute JWT with the app's
  private key (:func:`app_jwt`) and trades it for an installation token
  (``POST /app/installations/{id}/access_tokens``). An installation is bound
  to at most one workspace (``github_installations.workspace``), and a run in
  that workspace whose agent declares ``GITHUB_TOKEN`` receives that token.
  The token is cached, encrypted with the secrets key, until five minutes
  before it expires; with no ``AGENTS_HUB_SECRET_KEY`` nothing is cached and a
  fresh token is issued each time, since a plain text token in the database
  is exactly what the secrets store exists to avoid.
- **User-to-server tokens.** The OAuth web flow (``/login/oauth/authorize``
  then ``/login/oauth/access_token``) stores an access token and, when the app
  has token expiry on, a refresh token. :func:`user_token` refreshes when the
  access token is within five minutes of expiring; GitHub rotates the refresh
  token on every refresh, so the new one always replaces the old. These need
  the secrets key: a user's token is never stored in plain text, and without
  a key the connect flow is refused.
- **The hand-out** is :func:`token_for_run`, which common/secrets.py asks only
  when an agent declares ``GITHUB_TOKEN`` and no explicit secret of that name
  exists for the run's scope: an operator's explicit token always wins.
  It never raises, because a GitHub outage must not crash a launch.

The HTTP calls go through one small function (:func:`_http`) with
``requests``, so tests replace it with an in-process fake GitHub.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import db

log = logging.getLogger(__name__)

#: A cached or stored token is renewed once it is this close to expiring, so
#: a run never starts with a token that dies a minute in.
RENEW_BEFORE = timedelta(minutes=5)

_HTTP_TIMEOUT = 15

IDENTITY_APP = "app"
IDENTITY_USER = "user"
IDENTITIES = (IDENTITY_APP, IDENTITY_USER)


class GitHubAppError(Exception):
    """A GitHub App operation failed. ``code`` is a short machine word the
    routes pass on (``not_configured``, ``no_key``, ``exchange_failed``,
    ``api_error``, ``not_found``); the message is safe to show."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


# ── configuration ────────────────────────────────────────────────────────────

def _settings():
    from common.config import settings
    return settings


def _setting(name: str) -> str:
    return str(getattr(_settings(), name, "") or "").strip()


def app_id() -> str:
    return _setting("github_app_id")


def slug() -> str:
    return _setting("github_app_slug")


def client_id() -> str:
    return _setting("github_app_client_id")


def client_secret() -> str:
    return _setting("github_app_client_secret")


def api_url() -> str:
    return (_setting("github_api_url") or "https://api.github.com").rstrip("/")


def web_url() -> str:
    return (_setting("github_url") or "https://github.com").rstrip("/")


def private_key() -> str:
    """The app's PEM private key: the setting itself, else the file it names.

    A key pasted into ``.env`` on one line usually arrives with literal
    ``\\n`` sequences, which are turned back into newlines.
    """
    raw = _setting("github_app_private_key")
    if raw:
        return raw.replace("\\n", "\n")
    path = _setting("github_app_private_key_file")
    if path:
        try:
            return Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.warning("github app: cannot read the private key file: %s", type(exc).__name__)
    return ""


def configured() -> bool:
    """App id, private key and client id are all set."""
    return bool(app_id() and client_id() and private_key())


def install_url() -> str:
    """Where an administrator installs the app, or "" without a slug."""
    return f"{web_url()}/apps/{urllib.parse.quote(slug())}/installations/new" if slug() else ""


def _require_configured() -> None:
    if not configured():
        raise GitHubAppError("not_configured", "the GitHub App is not configured: set "
                             "GITHUB_APP_ID, GITHUB_APP_CLIENT_ID and GITHUB_APP_PRIVATE_KEY")


# ── time and encryption helpers ──────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fresh(expires_at: Any) -> bool:
    """Whether a token with this expiry is still good for a while. An empty
    expiry means the token does not expire (user tokens with expiry off)."""
    if not str(expires_at or "").strip():
        return True
    parsed = _parse(expires_at)
    return parsed is not None and parsed - RENEW_BEFORE > _now()


def _fernet():
    from common import secrets
    return secrets._fernet()


def _seal(value: str) -> str:
    fernet = _fernet()
    if fernet is None:
        from common.secrets import NO_KEY_MESSAGE
        raise GitHubAppError("no_key", NO_KEY_MESSAGE)
    return fernet.encrypt(value.encode("utf-8")).decode("ascii")


def _unseal(value: Any) -> Optional[str]:
    fernet = _fernet()
    if fernet is None or not value:
        return None
    from cryptography.fernet import InvalidToken
    try:
        return fernet.decrypt(str(value).encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.warning("github app: a stored token does not decrypt with the configured key")
        return None


# ── HTTP (replaced in tests) ─────────────────────────────────────────────────

def _http(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
          json_body: Any = None, form: Optional[Dict[str, str]] = None,
          auth: Optional[Tuple[str, str]] = None) -> Tuple[int, Any]:
    """One request; returns (status, parsed JSON body or {})."""
    import requests
    response = requests.request(method, url, headers=headers or {}, json=json_body, data=form,
                                auth=auth, timeout=_HTTP_TIMEOUT)
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    return response.status_code, body


def _api_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _message(body: Any) -> str:
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error_description") or body.get("error") or "")
    return ""


# ── the app itself ───────────────────────────────────────────────────────────

def app_jwt(now: Optional[int] = None) -> str:
    """The app's own credential: an RS256 JWT, ``iat`` a minute in the past
    (GitHub's advice for clock drift) and ``exp`` nine minutes ahead, under the
    ten minute maximum GitHub accepts."""
    _require_configured()
    from authlib.jose import jwt
    issued = int(now if now is not None else time.time())
    claims = {"iat": issued - 60, "exp": issued + 9 * 60, "iss": app_id()}
    token = jwt.encode({"alg": "RS256", "typ": "JWT"}, claims, private_key().encode("utf-8"))
    return token.decode("ascii") if isinstance(token, bytes) else str(token)


_INSTALL_COLUMNS = ("installation_id", "account_login", "account_type", "target", "permissions",
                    "workspace", "created_at", "updated_at", "token_enc", "token_expires_at")


def _install_row(row) -> Dict[str, Any]:
    return {c: row[c] for c in _INSTALL_COLUMNS}


def _public_installation(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        permissions = json.loads(row.get("permissions") or "{}")
    except ValueError:
        permissions = {}
    return {
        "installation_id": int(row["installation_id"]),
        "account_login": row.get("account_login") or "",
        "account_type": row.get("account_type") or "",
        "target": row.get("target") or "",
        "permissions": permissions,
        "workspace": row.get("workspace") or "",
        "updated_at": row.get("updated_at") or "",
    }


def _get_installation(installation_id: int) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        f"SELECT {', '.join(_INSTALL_COLUMNS)} FROM github_installations "
        "WHERE installation_id = ?", (int(installation_id),)).fetchone()
    return _install_row(row) if row else None


def installations() -> List[Dict[str, Any]]:
    """The installations the hub knows about (last sync), never a token."""
    rows = db.get_conn().execute(
        f"SELECT {', '.join(_INSTALL_COLUMNS)} FROM github_installations "
        "ORDER BY account_login, installation_id").fetchall()
    return [_public_installation(_install_row(r)) for r in rows]


def get_installation(installation_id: int) -> Optional[Dict[str, Any]]:
    row = _get_installation(installation_id)
    return _public_installation(row) if row else None


def list_installations() -> List[Dict[str, Any]]:
    """Fetch every installation from GitHub and make the table match.

    Bindings survive a sync; an installation GitHub no longer lists (the app
    was uninstalled) is dropped along with its binding and cached token.
    """
    _require_configured()
    token = app_jwt()
    found: List[Dict[str, Any]] = []
    page = 1
    while True:
        status, body = _http("GET", f"{api_url()}/app/installations?per_page=100&page={page}",
                             headers=_api_headers(token))
        if status != 200 or not isinstance(body, list):
            raise GitHubAppError("api_error",
                                 f"GitHub refused to list installations (HTTP {status}) "
                                 f"{_message(body)}".strip())
        found.extend(item for item in body if isinstance(item, dict) and item.get("id"))
        if len(body) < 100:
            break
        page += 1
    now = _iso(_now())
    seen = []
    with db.transaction() as conn:
        for item in found:
            installation_id = int(item["id"])
            seen.append(installation_id)
            account = item.get("account") or {}
            values = (str(account.get("login") or ""), str(account.get("type") or ""),
                      str(item.get("repository_selection") or ""),
                      json.dumps(item.get("permissions") or {}, sort_keys=True))
            exists = conn.execute("SELECT 1 FROM github_installations WHERE installation_id = ?",
                                  (installation_id,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE github_installations SET account_login = ?, account_type = ?, "
                    "target = ?, permissions = ?, updated_at = ? WHERE installation_id = ?",
                    (*values, now, installation_id))
            else:
                conn.execute(
                    "INSERT INTO github_installations (installation_id, account_login, "
                    "account_type, target, permissions, workspace, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, '', ?, ?)",
                    (installation_id, *values, now, now))
        rows = conn.execute("SELECT installation_id FROM github_installations").fetchall()
        for row in rows:
            if int(row["installation_id"]) not in seen:
                conn.execute("DELETE FROM github_installations WHERE installation_id = ?",
                             (int(row["installation_id"]),))
    return installations()


def installation_token(installation_id: int) -> str:
    """A token for one installation, cached until five minutes before expiry.

    Raises :class:`GitHubAppError` when GitHub refuses; callers that must not
    fail (the hand-out) go through :func:`token_for_run`.
    """
    installation_id = int(installation_id)
    row = _get_installation(installation_id)
    if row and row.get("token_enc") and row.get("token_expires_at") \
            and _fresh(row["token_expires_at"]):
        cached = _unseal(row["token_enc"])
        if cached:
            return cached
    _require_configured()
    status, body = _http("POST", f"{api_url()}/app/installations/{installation_id}/access_tokens",
                         headers=_api_headers(app_jwt()))
    if status not in (200, 201) or not isinstance(body, dict) or not body.get("token"):
        raise GitHubAppError("api_error",
                             f"GitHub refused an installation token (HTTP {status}) "
                             f"{_message(body)}".strip())
    token = str(body["token"])
    expires_at = str(body.get("expires_at") or "")
    if _fernet() is not None:
        # Only with a key: without one there is no honest place to keep it.
        now = _iso(_now())
        with db.transaction() as conn:
            if row:
                conn.execute("UPDATE github_installations SET token_enc = ?, "
                             "token_expires_at = ?, updated_at = ? WHERE installation_id = ?",
                             (_seal(token), expires_at, now, installation_id))
            else:
                conn.execute(
                    "INSERT INTO github_installations (installation_id, workspace, created_at, "
                    "updated_at, token_enc, token_expires_at) VALUES (?, '', ?, ?, ?, ?)",
                    (installation_id, now, now, _seal(token), expires_at))
    return token


def installation_for_workspace(workspace: str) -> Optional[int]:
    if not workspace:
        return None
    row = db.get_conn().execute(
        "SELECT installation_id FROM github_installations WHERE workspace = ? "
        "ORDER BY installation_id LIMIT 1", (str(workspace),)).fetchone()
    return int(row["installation_id"]) if row else None


def bind_installation(installation_id: int, workspace: str) -> Dict[str, Any]:
    """Bind an installation to one workspace. A workspace holds one binding,
    so whatever was bound to it before is unbound in the same transaction."""
    installation_id = int(installation_id)
    workspace = str(workspace or "").strip()
    if not workspace:
        raise GitHubAppError("bad_request", "a workspace is required")
    if _get_installation(installation_id) is None:
        raise GitHubAppError("not_found", f"no installation {installation_id}: sync first")
    now = _iso(_now())
    with db.transaction() as conn:
        conn.execute("UPDATE github_installations SET workspace = '', updated_at = ? "
                     "WHERE workspace = ? AND installation_id <> ?",
                     (now, workspace, installation_id))
        conn.execute("UPDATE github_installations SET workspace = ?, updated_at = ? "
                     "WHERE installation_id = ?", (workspace, now, installation_id))
    return get_installation(installation_id) or {}


def unbind(installation_id: int) -> bool:
    row = _get_installation(int(installation_id))
    if row is None or not row.get("workspace"):
        return False
    with db.transaction() as conn:
        conn.execute("UPDATE github_installations SET workspace = '', updated_at = ? "
                     "WHERE installation_id = ?", (_iso(_now()), int(installation_id)))
    return True


# ── a person's own GitHub account ────────────────────────────────────────────

def connect_url(state: str, redirect_uri: str = "") -> str:
    """Where the browser goes to authorise the app for its user."""
    params = {"client_id": client_id(), "state": state}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    return f"{web_url()}/login/oauth/authorize?{urllib.parse.urlencode(params)}"


def _token_request(form: Dict[str, str]) -> Dict[str, Any]:
    """POST to the OAuth token endpoint (code exchange and refresh alike)."""
    status, body = _http("POST", f"{web_url()}/login/oauth/access_token",
                         headers={"Accept": "application/json"},
                         form={"client_id": client_id(), "client_secret": client_secret(), **form})
    if status != 200 or not isinstance(body, dict) or body.get("error") \
            or not body.get("access_token"):
        raise GitHubAppError("exchange_failed",
                             _message(body) or f"GitHub answered HTTP {status}")
    return body


def _expiry(seconds: Any) -> str:
    try:
        value = int(seconds or 0)
    except (TypeError, ValueError):
        value = 0
    return _iso(_now() + timedelta(seconds=value)) if value > 0 else ""


def _store_user_tokens(user_id: str, body: Dict[str, Any], login: Optional[str] = None,
                       *, keep_refresh: Optional[str] = None,
                       keep_refresh_expires: str = "") -> None:
    now = _iso(_now())
    access = _seal(str(body["access_token"]))
    refresh_plain = str(body.get("refresh_token") or "") or (keep_refresh or "")
    refresh = _seal(refresh_plain) if refresh_plain else ""
    refresh_expires = (_expiry(body.get("refresh_token_expires_in"))
                       if body.get("refresh_token") else keep_refresh_expires)
    values = (access, _expiry(body.get("expires_in")), refresh, refresh_expires,
              str(body.get("scope") or ""), now)
    with db.transaction() as conn:
        exists = conn.execute("SELECT login FROM github_user_tokens WHERE user_id = ?",
                              (str(user_id),)).fetchone()
        if exists:
            conn.execute(
                "UPDATE github_user_tokens SET access_enc = ?, access_expires_at = ?, "
                "refresh_enc = ?, refresh_expires_at = ?, scopes = ?, updated_at = ?, "
                "login = ? WHERE user_id = ?",
                (*values, login if login is not None else exists["login"], str(user_id)))
        else:
            conn.execute(
                "INSERT INTO github_user_tokens (access_enc, access_expires_at, refresh_enc, "
                "refresh_expires_at, scopes, updated_at, login, user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, login or "", str(user_id), now))


def exchange_code(user_id: str, code: str, redirect_uri: str = "") -> Dict[str, Any]:
    """Finish the connect flow: trade the code, learn the login, store both
    tokens encrypted. Returns :func:`status`."""
    _require_configured()
    if _fernet() is None:
        from common.secrets import NO_KEY_MESSAGE
        raise GitHubAppError("no_key", NO_KEY_MESSAGE)
    if not code:
        raise GitHubAppError("exchange_failed", "no code in the callback")
    form = {"code": str(code)}
    if redirect_uri:
        form["redirect_uri"] = redirect_uri
    body = _token_request(form)
    status, me = _http("GET", f"{api_url()}/user", headers=_api_headers(str(body["access_token"])))
    login = str(me.get("login") or "") if status == 200 and isinstance(me, dict) else ""
    _store_user_tokens(user_id, body, login)
    return status_for(user_id)


def _user_row(user_id: str):
    return db.get_conn().execute(
        "SELECT user_id, login, access_enc, access_expires_at, refresh_enc, refresh_expires_at, "
        "scopes, created_at, updated_at FROM github_user_tokens WHERE user_id = ?",
        (str(user_id),)).fetchone()


def user_token(user_id: str) -> Optional[str]:
    """The user's access token, refreshed when it is about to expire; None
    when they never connected, the refresh token has expired too, or the
    stored tokens no longer decrypt."""
    if not user_id:
        return None
    row = _user_row(user_id)
    if row is None:
        return None
    if _fresh(row["access_expires_at"]):
        return _unseal(row["access_enc"])
    refresh = _unseal(row["refresh_enc"])
    refresh_expiry = str(row["refresh_expires_at"] or "")
    if not refresh or (refresh_expiry and not _fresh(refresh_expiry)):
        return None
    try:
        body = _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    except GitHubAppError as exc:
        # Another process may have refreshed a moment ago, which used up the
        # refresh token we hold (GitHub rotates it). Read again before giving up.
        again = _user_row(user_id)
        if again is not None and _fresh(again["access_expires_at"]):
            return _unseal(again["access_enc"])
        log.warning("github app: refreshing the token of user %s failed: %s", user_id, exc)
        return None
    _store_user_tokens(user_id, body, keep_refresh=refresh,
                       keep_refresh_expires=str(row["refresh_expires_at"] or ""))
    return str(body["access_token"])


def disconnect(user_id: str) -> bool:
    """Forget the user's tokens, and tell GitHub to revoke the grant (best
    effort: a stored token is gone from the hub whatever GitHub answers)."""
    row = _user_row(user_id)
    if row is None:
        return False
    access = _unseal(row["access_enc"])
    if access and client_id() and client_secret():
        try:
            _http("DELETE", f"{api_url()}/applications/{client_id()}/grant",
                  headers={"Accept": "application/vnd.github+json"},
                  json_body={"access_token": access}, auth=(client_id(), client_secret()))
        except Exception as exc:
            log.warning("github app: revoking the grant failed: %s", type(exc).__name__)
    with db.transaction() as conn:
        conn.execute("DELETE FROM github_user_tokens WHERE user_id = ?", (str(user_id),))
    return True


def status_for(user_id: str) -> Dict[str, Any]:
    """What the Account page shows: connected or not, as whom, until when."""
    row = _user_row(user_id) if user_id else None
    base = {"configured": configured(), "key_configured": _fernet() is not None}
    if row is None:
        return {**base, "connected": False, "login": "", "access_expires_at": "",
                "refresh_expires_at": ""}
    return {**base, "connected": True, "login": row["login"] or "",
            "access_expires_at": row["access_expires_at"] or "",
            "refresh_expires_at": row["refresh_expires_at"] or "",
            "scopes": row["scopes"] or "", "connected_at": row["created_at"] or ""}


#: The brief's name for it; ``status`` alone shadows too many things in routes.
status = status_for


# ── the hand-out ─────────────────────────────────────────────────────────────

def identity_of(agent_id: Optional[str]) -> str:
    if not agent_id:
        return IDENTITY_APP
    from agents.registry import get_agent
    spec = get_agent(agent_id)
    value = str(getattr(spec, "github_identity", "") or IDENTITY_APP) if spec else IDENTITY_APP
    return value if value in IDENTITIES else IDENTITY_APP


def token_for_run(workspace: str, agent_id: Optional[str],
                  user_id: Optional[str]) -> Optional[str]:
    """The GitHub token a run gets when its agent declares ``GITHUB_TOKEN``
    and no explicit secret supplies one. Never raises.

    The launching user's token first when the agent says
    ``github_identity: user`` and that user connected their account; else the
    installation token of the workspace's bound installation; else None.
    """
    try:
        if not configured():
            return None
        if identity_of(agent_id) == IDENTITY_USER and user_id:
            token = user_token(str(user_id))
            if token:
                return token
        installation_id = installation_for_workspace(workspace)
        if installation_id is None:
            return None
        return installation_token(installation_id)
    except Exception as exc:
        log.warning("github app: no token for %s in %s: %s", agent_id, workspace, exc)
        return None


__all__ = [
    "GitHubAppError", "IDENTITIES", "IDENTITY_APP", "IDENTITY_USER", "RENEW_BEFORE",
    "api_url", "app_id", "app_jwt", "bind_installation", "client_id", "configured",
    "connect_url", "disconnect", "exchange_code", "get_installation", "identity_of",
    "install_url", "installation_for_workspace", "installation_token", "installations",
    "list_installations", "private_key", "slug", "status", "status_for", "token_for_run",
    "unbind", "user_token", "web_url",
]
