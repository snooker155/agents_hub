"""
Watchers for the sync engine: the IMAP and HTTP observers that wake an agent.

A watcher polls an outside source, so two hubs holding the same enabled one
would both see a new mail and both wake an agent. A watcher therefore arrives
**disabled** on the hub that did not create it, and ``enabled`` and the pause
reason are not part of the document: an existing watcher keeps its own, so
switching it on here is not undone by the next sync.

Also left out because it is this hub's observation, not the definition: the
cursor in ``state`` (mailbox UID, body hash), last check, last change, last
event and error, the error streak and the fired count. A changed source
starts with a clean state, the same rule the Watchers page applies on edit.
The config names secrets (``password_secret``, ``headers_secret``) and keeps
those names; the values stay in each hub's secret store.

"Sign in with Google" follows the Watchers page's rule
(dashboard/backend/routes/watchers.py): a watcher in a workspace with no
Google connector of its own would read the operator's mailbox, so only an
administrator may point one there. Sync writes with the rights of the person
who owns the records, so a new or changed Google config from someone else
is refused, and the approved-source stamp (``google_from``) survives a
config change instead of being cleared with the rest of the state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register

_DOC_KEYS = ("name", "kind", "config", "interval_seconds", "auto_pause_after", "created_at")


def _uses_google(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    value = config.get("use_google")
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


def _may_use_operator_google(owner: str) -> bool:
    """Whether ``owner`` may point a watcher at the default workspace's
    Google connector: outside ``multi`` there is one operator, every role."""
    from common import identity
    from common.auth import LOCAL_OPERATOR_ID, MULTI, ROLE_ADMIN
    if identity.current_mode() != MULTI or owner == LOCAL_OPERATOR_ID:
        return True
    user = identity.get_user(owner) or {}
    return user.get("role") == ROLE_ADMIN and not user.get("disabled")


def _check_google(workspace: str, config: Any, owner: str) -> None:
    if not _uses_google(config):
        return
    from connectors.channels.store import DEFAULT_WORKSPACE
    from connectors.google import STORE as GOOGLE_STORE
    if workspace != DEFAULT_WORKSPACE and GOOGLE_STORE.defines(workspace):
        return  # the workspace's own account: its editors may use it
    if not _may_use_operator_google(owner):
        raise AdapterError("this watcher signs in with the hub operator's Google account; "
                           "an administrator sets that up here, or the workspace connects its own")


class WatcherAdapter(Adapter):
    kind = "watcher"
    order = 48
    label = "Watchers"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from watchers.store import store
        return sorted(w.id for w in store.list(workspace))

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from watchers.store import _dump, store
        watcher = store.get(record_id)
        if watcher is None:
            return None
        data = _dump(watcher)
        return {k: data[k] for k in _DOC_KEYS}

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from watchers import kinds, service
        from watchers.models import Watcher
        from watchers.store import store
        prev = store.get(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"watcher {record_id} lives in another workspace here")
        if prev is None or doc.get("config") != prev.config or doc.get("kind") != prev.kind:
            _check_google(workspace, doc.get("config"), owner)
        try:
            fields = service._validate(
                {**{k: doc.get(k) for k in _DOC_KEYS if k in doc},
                 "enabled": prev.enabled if prev is not None else False},
                current=prev)
        except service.WatcherError as exc:
            raise AdapterError(str(exc)) from exc
        changed = prev is None or fields["kind"] != prev.kind or fields["config"] != prev.config
        if prev is None:
            extra = {"created_at": doc["created_at"]} if doc.get("created_at") else {}
            store.put(Watcher(id=record_id, workspace=workspace, created_by=owner,
                              paused_reason="synced", **fields, **extra))
        else:
            if changed:
                # A new source starts clean, except for the approval of whose
                # Google account it signs in with: dropping that would let the
                # first poll stamp whatever account is in effect then.
                approved = (prev.state or {}).get(kinds.GOOGLE_FROM)
                fields.update(state={kinds.GOOGLE_FROM: approved} if approved else {}, last_error=None,
                              consecutive_errors=0)
            store.update(record_id, **fields)
        if changed and _uses_google(fields.get("config")):
            # Past the check above, as the route stamps past its role check.
            service.stamp_google_source(record_id)
        service._notify_changed(record_id)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from watchers import service
        service.delete(record_id)


register(WatcherAdapter())
