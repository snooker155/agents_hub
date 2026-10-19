"""
Authorization for one operator: the names the routes read, and nothing to decide.

This hub runs for exactly one person on a machine only they can reach. There
is no login, no accounts, no sessions, no API keys and no workspace
membership: every request is the local operator, who holds every role. The
stateful side (the request guard, the current-user context the stores stamp
records with) is :mod:`common.identity`; this module holds the constants and
the :class:`Principal` shape those routes and stores already use.

The role vocabulary (``admin``, ``owner`` / ``editor`` / ``viewer``) is kept
because the routes compare against it and the records carry it; with one
operator the comparisons are always satisfied.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

#: The one identity posture.
SINGLE = "single"

#: The owner id every ownable record gets. There is exactly one operator, so
#: a real user id would be a fiction; this constant says so.
LOCAL_OPERATOR_ID = "local"

#: Global roles. The operator is the admin.
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
GLOBAL_ROLES = (ROLE_ADMIN, ROLE_MEMBER)

#: Per-workspace roles, weakest first. Records and routes still name them.
WS_VIEWER = "viewer"
WS_EDITOR = "editor"
WS_OWNER = "owner"
WORKSPACE_ROLES = (WS_VIEWER, WS_EDITOR, WS_OWNER)
_WS_RANK = {WS_VIEWER: 1, WS_EDITOR: 2, WS_OWNER: 3}


def auth_headers() -> Dict[str, str]:
    """Headers a same-machine relay attaches to authenticate as the operator.

    Empty: the API carries no credential, so a run's live events, the
    session broker's relays and the CLI over REST post exactly as the
    browser does. Kept as a function because those callers already call it.
    """
    return {}


@dataclass(frozen=True)
class Principal:
    """Who a request is acting as.

    One value exists, :data:`LOCAL_PRINCIPAL`. The shape stays a dataclass
    because routes read ``principal.id``, ``principal.is_admin``,
    ``principal.kind`` and ``principal.reaches(workspace)`` off it, and the
    terminal tickets carry its fields.
    """
    id: str
    username: str = ""
    role: str = ROLE_ADMIN
    kind: str = "local"
    #: How the credential was presented. Informational.
    via: str = ""
    #: A narrowing to some workspaces. Always ``None``: nothing is narrowed.
    scope: Optional[tuple] = None
    #: The credential that authenticated this request. Always empty.
    credential_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def reaches(self, workspace: Optional[str]) -> bool:
        """Whether this principal may name ``workspace`` at all. Always."""
        if self.scope is None or not workspace:
            return True
        return workspace in self.scope


#: The one operator. Admin, because there is nobody to be an admin over:
#: nothing is refused.
LOCAL_PRINCIPAL = Principal(id=LOCAL_OPERATOR_ID, username=LOCAL_OPERATOR_ID,
                            role=ROLE_ADMIN, kind="local", via="local")


def role_satisfies(held: Optional[str], required: str) -> bool:
    """Whether a held workspace role covers the required one."""
    return _WS_RANK.get((held or "").strip(), 0) >= _WS_RANK.get(required, 0)


__all__ = [
    "GLOBAL_ROLES", "LOCAL_OPERATOR_ID", "LOCAL_PRINCIPAL",
    "Principal", "ROLE_ADMIN", "ROLE_MEMBER", "SINGLE", "WORKSPACE_ROLES",
    "WS_EDITOR", "WS_OWNER", "WS_VIEWER", "auth_headers", "role_satisfies",
]
