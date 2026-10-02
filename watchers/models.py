"""
The watcher record (docs/watchers.md).

A watcher belongs to a workspace, has a kind and a kind-specific ``config``,
polls every ``interval_seconds`` and keeps its last observed ``state`` here,
so a restart continues from where it left off instead of replaying every
message or change it already reported. Secrets never sit in ``config``: a
field that needs one names a workspace secret (``password_secret``,
``headers_secret``) and the probe reads the value at poll time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

KINDS = ("imap", "http")

#: Poll intervals a watcher may use, in seconds. The floor keeps a chatty
#: watcher from hammering a mail server; the ceiling keeps "every day" out of
#: a feature that is about noticing things soon.
MIN_INTERVAL_SECONDS = 15
MAX_INTERVAL_SECONDS = 6 * 3600
DEFAULT_INTERVAL_SECONDS = 120

#: Poll failures in a row that pause a watcher on their own (0 turns it off).
DEFAULT_AUTO_PAUSE_AFTER = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Watcher(BaseModel):
    id: str = Field(default_factory=lambda: f"watch_{uuid4().hex[:12]}")
    workspace: str
    name: str
    kind: str
    # Kind-specific settings; see watchers.kinds.<kind>.CONFIG_FIELDS.
    config: Dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    enabled: bool = True
    # "manual" (the pause button) or "errors" (auto pause); None while running.
    paused_reason: Optional[str] = None
    auto_pause_after: int = DEFAULT_AUTO_PAUSE_AFTER

    # -------------------- observed state --------------------
    # Whatever the kind needs to tell "new" from "seen": the highest UID for
    # a mailbox, a hash of the body for an HTTP resource. Opaque outside the
    # kind; empty until the first poll, which only takes a baseline.
    state: Dict[str, Any] = Field(default_factory=dict)
    last_checked_at: Optional[datetime] = None
    last_changed_at: Optional[datetime] = None
    # A one-line account of the last change, for the header list.
    last_event: Optional[str] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    # How many events this watcher has handed to agents, all time.
    fired: int = 0

    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def active(self) -> bool:
        return bool(self.enabled) and not self.paused_reason


__all__ = [
    "KINDS", "MIN_INTERVAL_SECONDS", "MAX_INTERVAL_SECONDS", "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_AUTO_PAUSE_AFTER", "Watcher",
]
