from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, List, Literal, Optional
from uuid import UUID, uuid4

from filelock import FileLock
from pydantic import BaseModel, Field

from common.paths import EPISODES_DIR, pool_episodes_file


EpisodeKind = Literal["interaction", "task", "decision", "error", "observation"]
EpisodeOutcome = Literal["success", "failure", "partial", "n/a"]

# Per-pool retention cap. When exceeded, prune oldest interaction/observation
# first; if still over, prune oldest of any kind.
MAX_EPISODES_PER_POOL = 10
LOW_SIGNAL_KINDS: set[str] = {"interaction", "observation"}


class Episode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    pool_id: str
    agent_id: Optional[str] = None
    workspace: Optional[str] = None
    run_id: Optional[str] = None
    kind: EpisodeKind = "observation"
    summary: str
    actor: Optional[str] = None
    subject: Optional[str] = None
    outcome: Optional[EpisodeOutcome] = None
    tags: List[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _json_default(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o)!r} is not JSON serializable")


class EpisodeStore:
    """File-based store for Episode objects, scoped to one shared-memory pool."""

    def __init__(self, pool_id: str):
        self.pool_id = str(pool_id)
        self.path: Path = pool_episodes_file(self.pool_id)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        EPISODES_DIR.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write([])

    def load(self, timeout: float = 10.0) -> List[Episode]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def _load_unlocked(self) -> List[Episode]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            return [Episode(**obj) for obj in json.loads(text)]
        except Exception:
            return []

    def add(self, episode: Episode, timeout: float = 10.0) -> Episode:
        with FileLock(str(self.lock_path), timeout=timeout):
            episodes = self._load_unlocked()
            episodes.append(episode)
            episodes = _apply_retention(episodes)
            self._atomic_write([e.model_dump() for e in episodes])
        return episode

    def query(
        self,
        *,
        query: Optional[str] = None,
        kind: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 10,
        timeout: float = 10.0,
    ) -> List[Episode]:
        episodes = self.load(timeout=timeout)
        if kind:
            episodes = [e for e in episodes if e.kind == kind]
        if outcome:
            episodes = [e for e in episodes if e.outcome == outcome]
        if since:
            episodes = [e for e in episodes if e.occurred_at >= since]
        if query:
            q = query.strip().lower()
            episodes = sorted(
                episodes,
                key=lambda e: _score(e, q),
                reverse=True,
            )
            episodes = [e for e in episodes if _score(e, q) > 0.0]
        else:
            episodes = sorted(episodes, key=lambda e: e.occurred_at, reverse=True)
        return episodes[:limit]

    def delete(self, episode_id: UUID | str, timeout: float = 10.0) -> bool:
        eid = str(episode_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            episodes = self._load_unlocked()
            new_list = [e for e in episodes if str(e.id) != eid]
            if len(new_list) == len(episodes):
                return False
            self._atomic_write([e.model_dump() for e in new_list])
            return True

    def stats(self, timeout: float = 10.0) -> dict:
        episodes = self.load(timeout=timeout)
        by_kind: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        for e in episodes:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            if e.outcome:
                by_outcome[e.outcome] = by_outcome.get(e.outcome, 0) + 1
        return {
            "total": len(episodes),
            "by_kind": by_kind,
            "by_outcome": by_outcome,
            "cap": MAX_EPISODES_PER_POOL,
        }

    def _atomic_write(self, payload: Iterable[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(list(payload), ensure_ascii=False, indent=2, default=_json_default)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)


def _apply_retention(episodes: List[Episode]) -> List[Episode]:
    """Enforce MAX_EPISODES_PER_POOL: prune low-signal kinds first by oldest."""
    if len(episodes) <= MAX_EPISODES_PER_POOL:
        return episodes

    overflow = len(episodes) - MAX_EPISODES_PER_POOL
    indexed = sorted(enumerate(episodes), key=lambda pair: pair[1].occurred_at)
    to_drop: set[int] = set()

    for idx, ep in indexed:
        if overflow <= 0:
            break
        if ep.kind in LOW_SIGNAL_KINDS:
            to_drop.add(idx)
            overflow -= 1

    if overflow > 0:
        for idx, _ep in indexed:
            if overflow <= 0:
                break
            if idx not in to_drop:
                to_drop.add(idx)
                overflow -= 1

    return [e for i, e in enumerate(episodes) if i not in to_drop]


def _score(episode: Episode, query_lower: str) -> float:
    """Cheap keyword overlap over summary/subject/tags/details."""
    if not query_lower:
        return 0.0
    haystack_parts = [
        episode.summary or "",
        episode.subject or "",
        " ".join(episode.tags),
        json.dumps(episode.details, default=str) if episode.details else "",
    ]
    haystack = " ".join(haystack_parts).lower()
    if not haystack.strip():
        return 0.0
    q_words = set(query_lower.split())
    hay_words = set(haystack.split())
    if not q_words:
        return 0.0
    overlap = len(q_words & hay_words)
    if overlap == 0:
        return 1.0 if query_lower in haystack else 0.0
    return overlap / len(q_words)
