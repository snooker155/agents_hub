"""Where guardrails and their events live: two DocStore collections.

``guardrails`` holds one document per guardrail record, keyed by id, exactly
like ``environments`` (environments/store.py). ``guardrail_events`` holds one
document per check that a rule or a judge actually ran and found something
(guardrails/runtime.py appends here for a block or a warn, never for a pass,
so this collection is a log of findings, not a log of every check). Neither
collection has a legacy file: both are new with this feature.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional
from uuid import uuid4

from common.docstore import DocStore

from .models import Guardrail

log = logging.getLogger(__name__)

_store = DocStore("guardrails")
_events = DocStore("guardrail_events")


@contextmanager
def transaction() -> Iterator[None]:
    """Atomic read-modify-write across the guardrails collection (uniqueness
    is checked and written under it, like environments/store.py)."""
    with _store.transaction():
        yield


# ── guardrails ───────────────────────────────────────────────────────────────

def get(guardrail_id: str) -> Optional[Guardrail]:
    raw = _store.get(str(guardrail_id or ""))
    if not isinstance(raw, dict):
        return None
    try:
        return Guardrail.model_validate(raw)
    except Exception:  # noqa: BLE001 - a record that no longer validates is reported as absent, not as a crash
        log.warning("guardrail %s does not validate, ignored", guardrail_id, exc_info=True)
        return None


def all() -> List[Guardrail]:  # noqa: A001 - mirrors DocStore.all / environments/store.all
    out: List[Guardrail] = []
    for key, raw in _store.all().items():
        if not isinstance(raw, dict):
            continue
        try:
            out.append(Guardrail.model_validate(raw))
        except Exception:  # noqa: BLE001 - same as get(): skip a record that no longer validates
            log.warning("guardrail %s does not validate, ignored", key, exc_info=True)
    return out


def put(guardrail: Guardrail) -> None:
    _store.put(guardrail.id, guardrail.model_dump(mode="json"))


def delete(guardrail_id: str) -> bool:
    return _store.delete(str(guardrail_id))


def clear() -> int:
    return _store.clear()


# ── events ───────────────────────────────────────────────────────────────────

def add_event(event: Dict[str, Any]) -> str:
    """Append one finding. Returns the event's id."""
    event_id = str(uuid4())
    _events.put(event_id, {"id": event_id, **event})
    return event_id


def list_events(*, workspace: Optional[str] = None, run_id: Optional[str] = None,
                guardrail_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """The most recent events matching every filter given, newest first.

    A full scan of the collection, like environments/service.py's usage
    queries: the events table stays small under its own retention window
    (:func:`prune_events`), so this is cheap enough not to need an index.
    """
    rows = [e for e in _events.values() if isinstance(e, dict)]
    if workspace:
        rows = [e for e in rows if e.get("workspace") == workspace]
    if run_id:
        rows = [e for e in rows if e.get("run_id") == run_id]
    if guardrail_id:
        rows = [e for e in rows if e.get("guardrail_id") == guardrail_id]
    rows.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    return rows[: max(1, min(int(limit), 2000))]


def prune_events(retention_days: int) -> int:
    """Drop events older than ``retention_days``. 0 (or less) keeps them all."""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat()
    pruned = 0
    for key, doc in _events.all().items():
        if isinstance(doc, dict) and str(doc.get("at") or "") < cutoff:
            if _events.delete(key):
                pruned += 1
    return pruned


def clear_events() -> int:
    return _events.clear()


__all__ = [
    "transaction", "get", "all", "put", "delete", "clear",
    "add_event", "list_events", "prune_events", "clear_events",
]
