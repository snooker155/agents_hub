"""
Identity: the one operator, and the current-user context the stores read.

:mod:`common.auth` holds the constants and the :class:`~common.auth.Principal`
shape. This module holds the state: the current-user context the stores stamp
a new record's owner with. This hub serves one person, so every request is
:data:`~common.auth.LOCAL_PRINCIPAL` and every record belongs to
``LOCAL_OPERATOR_ID``.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from common.auth import LOCAL_OPERATOR_ID, LOCAL_PRINCIPAL, Principal

#: Read by the stores that stamp an owner onto a new record. A contextvar
#: rather than a parameter so ``create_task``/``save_chat``/``create_workspace_folder``
#: keep the signatures every caller already uses, including the ones that run
#: in an agent subprocess where there is no request at all. A delegation
#: relayed from a run (routes/run_state.py) binds the launching user here, so a
#: child run is charged to the same person as its parent.
_current_user: ContextVar[Optional[str]] = ContextVar("agents_hub_current_user",
                                                      default=None)


def current_user_id() -> str:
    """Who owns whatever is being created right now: the operator, unless a
    relayed delegation bound its launching user for this context."""
    return _current_user.get() or LOCAL_OPERATOR_ID


def set_current_user(user_id: Optional[str]):
    """Bind the current user for this context; returns the reset token."""
    return _current_user.set(user_id)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def request_principal(request) -> Principal:
    """The principal a route acts as: always the operator.

    Takes the request so a route reads the same whether it is served over
    HTTP or called in-process by the CLI's direct backend (``request=None``).
    """
    return LOCAL_PRINCIPAL


__all__ = [
    "current_user_id", "request_principal", "reset_current_user", "set_current_user",
]
