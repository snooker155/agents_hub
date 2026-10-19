"""
Identity: the one operator, and the request guard that names them.

:mod:`common.auth` holds the constants and the :class:`~common.auth.Principal`
shape. This module holds the two things that have state: the current-user
context the stores stamp a new record's owner with, and the request guard the
backend's middleware calls. Both are trivial on purpose: this hub serves one
person, so every request is :data:`~common.auth.LOCAL_PRINCIPAL` and every
record belongs to ``LOCAL_OPERATOR_ID``.

The functions below keep the names the routes already call (``require_role``,
``request_principal``, ``claim_workspace``, ...) so that a route gating an
action on "admin" or "owner of this workspace" reads the same whether or not
there is anybody to refuse. There never is.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from common.auth import (
    LOCAL_OPERATOR_ID,
    LOCAL_PRINCIPAL,
    SINGLE,
    WS_OWNER,
    Principal,
)

#: Set by the middleware for the duration of one request, read by the stores
#: that stamp an owner onto a new record. A contextvar rather than a parameter
#: so ``create_task``/``save_chat``/``create_workspace_folder`` keep the
#: signatures every caller already uses, including the ones that run in an
#: agent subprocess where there is no request at all. A delegation relayed
#: from a run (routes/run_state.py) binds the launching user here too, so a
#: child run is charged to the same person as its parent.
_current_user: ContextVar[Optional[str]] = ContextVar("agents_hub_current_user",
                                                      default=None)


# ── mode ─────────────────────────────────────────────────────────────────────

def current_mode() -> str:
    """The identity posture in force: always ``single``.

    Kept as a function because routes and tools read it to skip the checks
    that only mattered when there were several people.
    """
    return SINGLE


# ── the current user, for the stores ─────────────────────────────────────────

def current_user_id() -> str:
    """Who owns whatever is being created right now: the operator, unless a
    relayed delegation bound its launching user for this context."""
    return _current_user.get() or LOCAL_OPERATOR_ID


def set_current_user(user_id: Optional[str]):
    """Bind the current user for this context; returns the reset token."""
    return _current_user.set(user_id)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def claim_workspace(workspace: str, user_id: Optional[str] = None) -> None:
    """Make the creator of a workspace its owner. A no-op: there is nobody to
    own anything when there is only one of you."""
    return None


# ── request resolution ───────────────────────────────────────────────────────

def current_principal(request) -> Optional[Principal]:
    """Resolve a request (or a WebSocket) to its principal: the operator."""
    return LOCAL_PRINCIPAL


def authorize_request(request) -> tuple[bool, Optional[Principal]]:
    """The whole middleware decision for one request: allowed, as the operator."""
    return True, LOCAL_PRINCIPAL


def require_role(principal: Optional[Principal], *, admin: bool = False,
                 workspace: Optional[str] = None,
                 role: str = WS_OWNER) -> None:
    """Raise unless the principal clears the bar. One operator is every role,
    so this never raises; routes keep calling it where the bar is."""
    return None


def request_principal(request) -> Optional[Principal]:
    """The principal the middleware put on the request, for a route to read.

    A route called without a request at all (the CLI's direct backend calls
    route functions in-process) is the operator acting on their own state,
    so it gets the same answer.
    """
    if request is None:
        return LOCAL_PRINCIPAL
    state = getattr(request, "state", None)
    principal = getattr(state, "principal", None) if state is not None else None
    return principal or LOCAL_PRINCIPAL


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
    "SINGLE", "authorize_request", "claim_workspace", "client_ip", "current_mode",
    "current_principal", "current_user_id",
    "request_principal", "require_role", "reset_current_user", "set_current_user",
]
