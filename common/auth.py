"""
The one operator: the constants and the :class:`Principal` shape the routes
and the stores read.

This hub runs for exactly one person on a machine only they can reach. There
is no login, no accounts, no sessions, no API keys and no workspace
membership: every request is the local operator, who may do everything. The
stateful side (the current-user context the stores stamp records with) is
:mod:`common.identity`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

#: The owner id every ownable record gets. There is exactly one operator, so
#: a real user id would be a fiction; this constant says so.
LOCAL_OPERATOR_ID = "local"


def auth_headers() -> Dict[str, str]:
    """Headers a same-machine relay attaches to authenticate as the operator.

    Empty: the API carries no credential, so a run's live events, the
    session broker's relays and the CLI over REST post exactly as the
    browser does. Kept as a function because those callers already call it.
    """
    return {}


@dataclass(frozen=True)
class Principal:
    """Who a request is acting as: always the local operator.

    One value exists, :data:`LOCAL_PRINCIPAL`. Routes read ``principal.id``
    and ``principal.username`` off it to stamp who did something (a steering
    message's author, a terminal session's owner).
    """
    id: str
    username: str = ""
    #: The one kind there is.
    kind: str = "local"


#: The one operator.
LOCAL_PRINCIPAL = Principal(id=LOCAL_OPERATOR_ID, username=LOCAL_OPERATOR_ID, kind="local")


__all__ = ["LOCAL_OPERATOR_ID", "LOCAL_PRINCIPAL", "Principal", "auth_headers"]
