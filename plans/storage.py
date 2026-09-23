"""Scheduled jobs and the notification inbox, as document collections.

Both stores are lists of pydantic models keyed by ``id``, kept in the
``documents`` table through :class:`common.docstore.DocStore` (one row per
job or notification). The scheduler in the backend process and any agent
subprocess that creates jobs through tools all go through the database, so
the lease in :meth:`PlanStore.claim_due_jobs` holds across processes and
hosts, which a lock file never did. An existing ``plans.json`` /
``notifications.json`` is imported once on first use and renamed
``.migrated``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as _dt_timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Type, TypeVar
from uuid import UUID

from pydantic import BaseModel

from common.docstore import DocStore
from common.paths import PLANS_FILE as DEFAULT_PLANS_FILE
from common.paths import NOTIFICATIONS_FILE as DEFAULT_NOTIFICATIONS_FILE
from .models import JobStatus, Notification, ScheduledJob

M = TypeVar("M", bound=BaseModel)


def _model_to_dict(obj: BaseModel) -> dict:
    # JSON mode: enums as their values, datetimes as ISO strings, UUIDs as
    # strings, exactly what the JSON files used to hold.
    return obj.model_dump(mode="json")


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC, same convention as plans.service."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt_timezone.utc)
    return dt


def _record_key(rec: Any) -> Optional[str]:
    return str(rec["id"]) if isinstance(rec, dict) and rec.get("id") else None


class _ModelStore:
    """A list of pydantic models keyed by ``id`` over a :class:`DocStore`.

    ``path`` names the legacy JSON file the collection is imported from on
    first use (the constructor argument tests and callers already pass);
    the data itself lives in the database.
    """

    model: Type[BaseModel]

    def __init__(self, name: str, path: Path | str | None, model: Type[M]):
        self.path = Path(path) if path else None
        self.model = model
        self.docs = DocStore(name, legacy_file=self.path, legacy_key=_record_key)

    # ------------- public API -------------
    def load(self, timeout: float = 10.0) -> List[M]:
        return self._items(self.docs.all())

    def list(self, timeout: float = 10.0) -> List[M]:
        return self.load(timeout=timeout)

    def get(self, item_id: UUID | str, timeout: float = 10.0) -> Optional[M]:
        doc = self.docs.get(str(item_id))
        return self._parse_or_none(doc) if doc is not None else None

    def add(self, item: M, timeout: float = 10.0) -> M:
        self.docs.put(str(item.id), _model_to_dict(item))
        return item

    def update(self, item_id: UUID | str, *, timeout: float = 10.0, **fields) -> Optional[M]:
        iid = str(item_id)
        with self.docs.transaction():
            doc = self.docs.get(iid)
            if not isinstance(doc, dict):
                return None
            data = dict(doc)
            data.update(fields)
            updated = self._parse(data)
            if hasattr(updated, "touch"):
                updated.touch()
            self.docs.put(iid, _model_to_dict(updated))
            return updated

    def delete(self, item_id: UUID | str, timeout: float = 10.0) -> int:
        return 1 if self.docs.delete(str(item_id)) else 0

    def save(self, items: Iterable[M], timeout: float = 10.0) -> None:
        self.docs.replace_all({str(i.id): _model_to_dict(i) for i in items})

    # ------------- internals -------------
    def _parse(self, data: dict) -> M:
        return self.model.model_validate(data)

    def _parse_or_none(self, data: Any) -> Optional[M]:
        try:
            return self._parse(data)
        except Exception:
            return None

    def _items(self, docs: Dict[str, Any]) -> List[M]:
        items: List[M] = []
        for obj in docs.values():
            parsed = self._parse_or_none(obj)
            if parsed is not None:
                items.append(parsed)
        return items


class PlanStore(_ModelStore):
    def __init__(self, path: Path | str | None = None):
        super().__init__("plans", path or DEFAULT_PLANS_FILE, ScheduledJob)

    def claim_due_jobs(
        self,
        now: datetime,
        owner: str,
        lease_seconds: float = 120.0,
        timeout: float = 10.0,
    ) -> List[ScheduledJob]:
        """Atomically select due jobs with no live lease and stamp a lease on them.

        Runs entirely under one write transaction (read, filter, write), so
        two schedulers calling this concurrently against the same database,
        two backend replicas on different hosts, or an overlapping slow tick,
        can never both see the same job as claimable: whichever gets the
        transaction second reads the lease the first one just wrote.
        """
        now = _aware(now)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.docs.transaction():
            claimed: List[ScheduledJob] = []
            for key, doc in self.docs.all().items():
                item = self._parse_or_none(doc)
                if item is None:
                    continue
                held = item.lease_until is not None and _aware(item.lease_until) > now
                due = item.status == JobStatus.scheduled and _aware(item.run_at) <= now
                if due and not held:
                    data = _model_to_dict(item)
                    data["lease_until"] = lease_until.isoformat()
                    data["lease_owner"] = owner
                    updated = self._parse(data)
                    if hasattr(updated, "touch"):
                        updated.touch()
                    claimed.append(updated)
                    self.docs.put(key, _model_to_dict(updated))
            return claimed

    def force_claim_job(
        self,
        job_id: UUID | str,
        owner: str,
        lease_seconds: float = 120.0,
        timeout: float = 10.0,
    ) -> Optional[ScheduledJob]:
        """Claim one specific job for an immediate manual fire (run-now).

        Ignores ``run_at`` (that is the point of run-now) but still refuses a
        job whose lease is held by someone else and not yet expired, so a
        manual fire can never overlap an in-flight automatic one.
        """
        iid = str(job_id)
        now = datetime.now(_dt_timezone.utc)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.docs.transaction():
            item = self.get(iid)
            if item is None:
                return None
            held = item.lease_until is not None and _aware(item.lease_until) > now
            eligible = item.status in (JobStatus.scheduled, JobStatus.paused) and not held
            if not eligible:
                return None
            data = _model_to_dict(item)
            data["lease_until"] = lease_until.isoformat()
            data["lease_owner"] = owner
            updated = self._parse(data)
            if hasattr(updated, "touch"):
                updated.touch()
            self.docs.put(iid, _model_to_dict(updated))
            return updated


class NotificationStore(_ModelStore):
    # Inbox cap — oldest entries are dropped when exceeded.
    MAX_ENTRIES = 500

    def __init__(self, path: Path | str | None = None):
        super().__init__("notifications", path or DEFAULT_NOTIFICATIONS_FILE, Notification)

    def add(self, item: Notification, timeout: float = 10.0) -> Notification:
        with self.docs.transaction():
            self.docs.put(str(item.id), _model_to_dict(item))
            keys = self.docs.keys()
            for key in keys[:-self.MAX_ENTRIES] if len(keys) > self.MAX_ENTRIES else []:
                self.docs.delete(key)
        return item

    def mark_all_read(self, workspace: Optional[str] = None, timeout: float = 10.0) -> int:
        with self.docs.transaction():
            changed = 0
            for key, doc in self.docs.all().items():
                n = self._parse_or_none(doc)
                if n is None or n.read:
                    continue
                if workspace and (n.workspace or "") != workspace:
                    continue
                n.read = True
                self.docs.put(key, _model_to_dict(n))
                changed += 1
            return changed


__all__ = ["PlanStore", "NotificationStore"]
