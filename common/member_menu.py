"""
The sidebar items members of a multi-user hub do not see.

An administrator picks them on the Users page (``PUT /api/settings/member-menu``,
an administrator's write by ``common.auth.ADMIN_WRITE_PREFIXES``); every
member's ``GET /api/auth/me`` carries the list as ``hidden_menu`` and the
sidebar leaves those rows out, in the simple menu and the full one alike. An
administrator always sees the whole menu.

Hiding a row is not access control: what a member may not change is refused
by the API (the model catalog and the settings are the administrator's to
write, a system agent the administrator's to change). It keeps the pages a
member has no use for out of their way. Until an administrator saves a list,
:data:`DEFAULT_HIDDEN` applies: the pages that configure the service itself.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from common.docstore import DocStore

log = logging.getLogger(__name__)

_store = DocStore("member_menu")
_KEY = "hidden"

#: What members do not see until an administrator says otherwise: the model
#: catalog and the service's settings (both read-only to them), and the pages
#: about the machinery that runs the hub.
DEFAULT_HIDDEN = (
    "/models", "/settings", "/nodes", "/environments", "/containers", "/services",
    "/web-logs", "/agent-registry", "/registry",
)

#: Rows that never hide: where a member signs out and manages their keys, and
#: the chat.
NEVER_HIDDEN = frozenset({"/account", "/chat"})


def _clean(paths: Any) -> List[str]:
    out: List[str] = []
    for item in paths if isinstance(paths, list) else []:
        path = str(item or "").strip()
        if not path.startswith("/") or path in NEVER_HIDDEN or path in out:
            continue
        out.append(path[:100])
    return out


def hidden() -> List[str]:
    """The rows hidden from members: the administrator's list, or the default."""
    try:
        doc: Optional[Dict[str, Any]] = _store.get(_KEY)
    except Exception:  # noqa: BLE001 - an unreadable store hides the default rows
        log.debug("member menu: store unreadable", exc_info=True)
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("paths"), list):
        return _clean(doc["paths"])
    return list(DEFAULT_HIDDEN)


def is_default() -> bool:
    try:
        return not isinstance(_store.get(_KEY), dict)
    except Exception:  # noqa: BLE001 - see hidden()
        return True


def set_hidden(paths: Any) -> List[str]:
    """Save the administrator's list; None goes back to the default."""
    if paths is None:
        _store.delete(_KEY)
        return list(DEFAULT_HIDDEN)
    cleaned = _clean(paths)
    _store.put(_KEY, {"paths": cleaned})
    return cleaned


def hidden_for(principal: Any) -> List[str]:
    """The rows ``principal`` does not see: none for an administrator or
    anyone outside ``multi`` mode."""
    from common import identity
    from common.auth import MULTI
    if identity.current_mode() != MULTI or principal is None or principal.is_admin:
        return []
    return hidden()


__all__ = ["DEFAULT_HIDDEN", "NEVER_HIDDEN", "hidden", "hidden_for", "is_default", "set_hidden"]
