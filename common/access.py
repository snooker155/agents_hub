"""
Record-level visibility: whether one already-fetched row is this caller's to see.

This hub runs for one operator, who sees every record. The routes still call
these helpers with the request's :class:`~common.auth.Principal` and a row
(or a page of rows) that carries a ``workspace`` column and, sometimes, an
``owner``, so the answers live in one place: everything is visible.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from common.auth import Principal


def visible_workspaces(principal: Optional[Principal]) -> Optional[Set[str]]:
    """The workspaces this principal may see records from. ``None`` means
    "every workspace, no filtering needed", which is always the answer."""
    return None


def can_see_workspace(principal: Optional[Principal], workspace: Optional[str]) -> bool:
    """Whether this principal may see a record filed under ``workspace``. Always."""
    return True


def owner_or_admin(principal: Optional[Principal], owner_id: Optional[str]) -> bool:
    """Whether this principal is a record's owner, or outranks ownership.
    Always: every record is the one operator's."""
    return True


def filter_by_workspace(principal: Optional[Principal], items: Iterable[Dict[str, Any]],
                        key: str = "workspace") -> List[Dict[str, Any]]:
    """The rows of ``items`` this principal may see: all of them."""
    return list(items)


def require_visible(principal: Optional[Principal], workspace: Optional[str]) -> None:
    """Raise 403 unless this principal may see records filed under
    ``workspace``. Never raises."""
    return None


__all__ = [
    "visible_workspaces", "can_see_workspace", "owner_or_admin",
    "filter_by_workspace", "require_visible",
]
