"""
The pure half of authorization: predicates, no database, no request object.

Two layers live here. The original one is :func:`is_authorized`, the predicate
behind the optional shared API token: off by default (no token configured) so
the local-only workflow is unchanged; when a token is set every ``/api``
request must present it, as an ``Authorization: Bearer`` header, an
``X-Api-Token`` header, or a ``?token=`` query parameter (the query form lets
the browser's EventSource, which cannot set headers, authenticate the SSE
stream).

On top of it sits the three-mode identity layer (``single``, ``token``,
``multi``; see :func:`effective_auth_mode` and :func:`authorize`). Everything
in it is a function of its arguments, so the whole decision table is testable
without a server. The stateful half, which turns a request into a
:class:`Principal` by looking up sessions and memberships, is
:mod:`common.identity`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


def extract_bearer(auth_header: Optional[str]) -> Optional[str]:
    """Return the token from an ``Authorization: Bearer <token>`` header, else None."""
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def auth_headers() -> Dict[str, str]:
    """Headers a same-machine relay should attach to authenticate as the operator.

    Reads ``AGENTS_HUB_API_TOKEN`` straight from the environment rather than
    importing ``common.config``: the callers (``agents/callbacks/streaming.py``,
    ``common/session_broker.py``) post from a background thread or a bare
    subprocess and should not pull in the settings/pydantic import graph just to
    decide whether to add one header. ``common.subprocess_env.base_subprocess_env``
    is what guarantees this variable actually reaches those processes even when
    the token was only ever configured via ``.env`` (see its docstring).

    ``AGENTS_HUB_SERVICE_TOKEN`` is the fallback, for ``AUTH_MODE=multi`` with
    no shared API token: a subprocess has no session and no password, so the
    backend mints one random service credential per process and exports it the
    same way (``common.identity.service_token``). It is checked second so a
    deployment that has both keeps using the token it configured.

    ``AGENTS_HUB_API_KEY`` is a personal API key (``common.api_keys``): it is
    what the CLI in REST mode, an A2A client or a CI job presents under
    ``AUTH_MODE=multi`` instead of the shared token, and it acts as its owner.
    It is checked last so a relay subprocess that was handed the service
    credential keeps using that.

    Empty when none is configured, matching ``is_authorized``'s
    open-by-default behaviour, so an unconfigured deployment posts exactly as
    it did before.
    """
    token = (os.environ.get("AGENTS_HUB_API_TOKEN", "").strip()
             or os.environ.get("AGENTS_HUB_SERVICE_TOKEN", "").strip()
             or os.environ.get("AGENTS_HUB_API_KEY", "").strip())
    return {"Authorization": f"Bearer {token}"} if token else {}


# Prefixes that carry their own credential and must not be gated on the
# operator's token. ``/api/ingest`` is authenticated per connection
# (``connections/store.py``): an external service reporting its runs is given a
# token that can only report, and requiring the global token instead would hand
# every such service a key to the whole dashboard.
#
# ``/scim/v2`` is the provisioning API an identity provider drives with its
# own bearer token (``AUTH_SCIM_TOKEN``, see dashboard/backend/routes/scim.py):
# it sits outside ``/api`` so the operator token never applies to it, and is
# listed here so the intent is in one place.
SELF_AUTHENTICATING_PREFIXES = ("/api/ingest", "/scim/v2")


def _is_self_authenticating(path: str) -> bool:
    """True for a path that authenticates itself rather than as the operator.

    Matched on a path boundary, so a route like ``/api/ingestion-report`` added
    later is *not* accidentally exempted by a prefix meant for ``/api/ingest``.
    """
    for prefix in SELF_AUTHENTICATING_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def is_authorized(
    *,
    configured_token: str,
    method: str,
    path: str,
    auth_header: Optional[str] = None,
    x_api_token: Optional[str] = None,
    query_token: Optional[str] = None,
) -> bool:
    """True when a request may proceed.

    Open (returns True) when no token is configured, for CORS preflight
    (``OPTIONS``), for non-``/api`` paths, and for ``/api/ingest``. Otherwise
    the presented token (header or query) must match exactly.
    """
    if not configured_token:
        return True
    if method == "OPTIONS":
        return True
    if not path.startswith("/api"):
        return True
    if _is_self_authenticating(path):
        return True
    presented = extract_bearer(auth_header) or x_api_token or query_token
    return presented == configured_token


# ============================================================================
# Identity modes
# ============================================================================
# Three explicit postures, chosen with ``AUTH_MODE`` (see docs/identity.md).
# Everything below is pure: no database, no request object, no HTTP. The
# database-backed half (users, sessions, memberships) lives in
# ``common.identity`` and calls into these functions, so the whole decision
# table can be tested without a server.

SINGLE = "single"
TOKEN = "token"
MULTI = "multi"
AUTH_MODES = (SINGLE, TOKEN, MULTI)

#: The owner id every ownable record gets in ``single`` mode. There is exactly
#: one operator, so a real user id would be a fiction; this constant says so.
LOCAL_OPERATOR_ID = "local"

#: Global roles.
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
GLOBAL_ROLES = (ROLE_ADMIN, ROLE_MEMBER)

#: Per-workspace membership roles, weakest first.
WS_VIEWER = "viewer"
WS_EDITOR = "editor"
WS_OWNER = "owner"
WORKSPACE_ROLES = (WS_VIEWER, WS_EDITOR, WS_OWNER)
_WS_RANK = {WS_VIEWER: 1, WS_EDITOR: 2, WS_OWNER: 3}

#: Methods that only read. Everything else is a write and needs ``editor``.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Routes that must answer before anyone is authenticated: the mode probe the
#: frontend renders from, the login form's target, and the one-shot bootstrap.
PUBLIC_AUTH_ROUTES = frozenset({
    ("GET", "/api/auth/mode"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/bootstrap"),
    # Single sign-on (common/oidc.py): the redirect to the provider, and the
    # redirect back from it, both happen before there is a session.
    ("GET", "/api/auth/oidc/start"),
    ("GET", "/api/auth/oidc/callback"),
})

#: Carries its own credential in the path rather than as the operator, the same
#: reasoning as ``SELF_AUTHENTICATING_PREFIXES`` above.
EXTERNAL_PREFIXES = ("/api/external",)

#: Sub-paths of a workspace that only its owner (or an admin) may touch at all.
#: These configure how the workspace behaves rather than what is in it, so read
#: access is restricted too: an env block is a list of secret *names*, and a
#: policy is the shape of the guardrails somebody would have to get around.
OWNER_SCOPED_SEGMENTS = frozenset({"policy", "env", "settings-overrides", "members",
                                   "secrets"})

#: Path segments directly under ``/api/workspaces/`` that are routes, not
#: workspace names. Without this, ``POST /api/workspaces/attach`` would be read
#: as a request against a workspace called "attach".
_NOT_A_WORKSPACE_NAME = frozenset({"attach"})


@dataclass(frozen=True)
class Principal:
    """Who a request is acting as, once the mode has been resolved.

    ``kind`` says where the identity came from, which matters for the UI more
    than for the decision table: ``local`` is the single-operator constant,
    ``token`` is the shared API token, ``service`` is the credential the hub's
    own subprocess relays carry, and ``user`` is a named account.
    """
    id: str
    username: str = ""
    role: str = ROLE_MEMBER
    kind: str = "user"
    #: How the credential was presented: ``session`` (a login), ``api_key``,
    #: ``oidc`` (a session opened by single sign-on), ``token``, ``service``
    #: or ``local``. Informational, except that an ``api_key`` principal may
    #: carry a ``scope``.
    via: str = ""
    #: For a personal API key narrowed to some workspaces: the tuple of names
    #: it may reach. ``None`` means the owner's full reach. Checked before
    #: everything else in :func:`authorize`, admin or not: a key never acts
    #: wider than it was cut.
    scope: Optional[tuple] = None
    #: The row id of the session or key that authenticated this request, so a
    #: route can name it (the "current session" marker on the sessions page)
    #: without the credential ever being echoed.
    credential_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def reaches(self, workspace: Optional[str]) -> bool:
        """Whether this principal's credential is allowed to name ``workspace``
        at all. Only a scoped API key ever says no."""
        if self.scope is None or not workspace:
            return True
        return workspace in self.scope


#: The one operator, in ``single`` mode. Admin, because in that mode there is
#: nobody to be an admin over: nothing is refused.
LOCAL_PRINCIPAL = Principal(id=LOCAL_OPERATOR_ID, username=LOCAL_OPERATOR_ID,
                            role=ROLE_ADMIN, kind="local")

#: The shared API token's principal in ``token`` mode.
TOKEN_PRINCIPAL = Principal(id=LOCAL_OPERATOR_ID, username=LOCAL_OPERATOR_ID,
                            role=ROLE_ADMIN, kind="token")


def effective_auth_mode(configured_mode: Optional[str], configured_token: str) -> str:
    """The mode actually in force, given the settings.

    Backwards compatibility is the whole point of this function. Setting only
    ``AGENTS_HUB_API_TOKEN`` used to be the one way to close the API, and it
    still is: an unset (or explicitly ``single``) ``AUTH_MODE`` with a token
    configured resolves to ``token``, so an existing deployment behaves exactly
    as it did before this shipped. An unrecognised value falls back to
    ``single`` rather than failing the process, for the same reason the rest of
    the settings are forgiving: a typo in ``.env`` must not make the hub
    unstartable, and ``single`` is the documented default anyway.
    """
    mode = (configured_mode or "").strip().lower()
    if mode not in AUTH_MODES:
        mode = SINGLE
    if mode == SINGLE and (configured_token or "").strip():
        return TOKEN
    return mode


def _has_prefix(path: str, prefixes) -> bool:
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def is_open_path(method: str, path: str) -> bool:
    """True for a request that needs no principal, whatever the mode.

    CORS preflight, everything outside ``/api``, the self-authenticating ingest
    routes, the token-in-path external routes, and the three public auth
    routes. Kept separate from :func:`authorize` so the middleware can skip the
    (database-touching) principal lookup entirely for these.
    """
    method = (method or "").upper()
    if method == "OPTIONS":
        return True
    if not path.startswith("/api"):
        return True
    if _is_self_authenticating(path):
        return True
    if _has_prefix(path, EXTERNAL_PREFIXES):
        return True
    return (method, path.rstrip("/") or path) in PUBLIC_AUTH_ROUTES


def workspace_from_request(
    *,
    path: str,
    query_workspace: Optional[str] = None,
    header_workspace: Optional[str] = None,
) -> Optional[str]:
    """The workspace a request targets, or None when it names none.

    Three sources, in the order the request states them most explicitly: the
    path (``/api/workspaces/{name}/…``), the ``workspace`` query parameter, and
    the ``X-Workspace`` header. Request *bodies* are deliberately not consulted
    — reading one in middleware means buffering and replaying the stream for
    every request, and a body is not where the routes that matter name their
    workspace anyway.
    """
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 3 and segments[0] == "api" and segments[1] == "workspaces":
        name = segments[2]
        if name not in _NOT_A_WORKSPACE_NAME:
            return name
    for candidate in (query_workspace, header_workspace):
        name = (candidate or "").strip()
        if name:
            return name
    return None


def required_workspace_role(method: str, path: str) -> str:
    """The membership role a request needs in the workspace it names.

    Reads need any membership; writes need ``editor``; deleting the workspace
    itself and touching how it is configured need ``owner``. Note that only the
    exact ``DELETE /api/workspaces/{name}`` is workspace deletion —
    ``DELETE /api/workspaces/{name}/files`` deletes a file *in* it and is an
    ordinary write.
    """
    method = (method or "").upper()
    segments = [s for s in path.split("/") if s]
    is_ws_path = len(segments) >= 3 and segments[0] == "api" and segments[1] == "workspaces"
    if is_ws_path:
        if len(segments) >= 4 and segments[3] in OWNER_SCOPED_SEGMENTS:
            return WS_OWNER
        if method == "DELETE" and len(segments) == 3:
            return WS_OWNER
    return WS_VIEWER if method in SAFE_METHODS else WS_EDITOR


def role_satisfies(held: Optional[str], required: str) -> bool:
    """Whether a held membership role covers the required one."""
    return _WS_RANK.get((held or "").strip(), 0) >= _WS_RANK.get(required, 0)


def authorize(
    mode: str,
    *,
    principal: Optional[Principal],
    method: str,
    path: str,
    workspace: Optional[str] = None,
    membership_role: Optional[str] = None,
) -> bool:
    """Whether a request may proceed, given an already-resolved principal.

    Pure: the caller does the lookups (session token to principal, membership
    row to role) and this decides. ``single`` allows everything, ``token``
    needs any principal at all (the token either matched or it did not), and
    ``multi`` runs the role matrix. Admins bypass membership entirely, and so
    does the service credential the hub's own subprocess relays carry.
    """
    if mode == SINGLE:
        return True
    if is_open_path(method, path):
        return True
    if principal is None:
        return False
    if mode == TOKEN:
        return True
    if not principal.reaches(workspace):
        # A scoped API key naming a workspace outside its scope, admin or not.
        return False
    if principal.is_admin:
        return True
    if not workspace:
        # Record-level ownership is out of scope: any authenticated user may
        # use the parts of the API that name no workspace.
        return True
    return role_satisfies(membership_role, required_workspace_role(method, path))
