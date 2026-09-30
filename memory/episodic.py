from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from common.docstore import DocStore
from common.paths import pool_episodes_file


EpisodeKind = Literal["interaction", "task", "decision", "error", "observation"]
EpisodeOutcome = Literal["success", "failure", "partial", "n/a"]

# Per-pool retention cap. When exceeded, prune oldest interaction/observation
# first; if still over, prune oldest of the remaining prunable kinds.
#
# At 10 the cap was smaller than a single conversation: the automatic
# interaction episodes written after every exchange evicted the ones the agent
# had recorded deliberately, which is the opposite of what a memory should do.
# Two rules now protect the deliberate ones: an episode the agent recorded
# itself is `explicit`, and one the operator wants kept forever is `pinned`.
# Neither is ever pruned.
MAX_EPISODES_PER_POOL = 200
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
    # True when the agent called record_episode itself; False for the automatic
    # extractor's writes. Explicit episodes are never pruned.
    explicit: bool = False
    # Operator/agent pin: never pruned, whatever the cap says.
    pinned: bool = False
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _model_to_dict(episode: Episode) -> dict:
    # JSON mode: UUIDs and datetimes as strings, exactly what the JSON files
    # used to hold.
    return episode.model_dump(mode="json")


def _record_key(rec: Any) -> Optional[str]:
    return str(rec.get("id")) if isinstance(rec, dict) and rec.get("id") else None


class EpisodeStore:
    """Store for Episode objects, scoped to one shared-memory pool, kept in
    the ``documents`` table (one row per episode, collection
    ``episodes:<pool_id>``) through :class:`common.docstore.DocStore`."""

    def __init__(self, pool_id: str):
        self.pool_id = str(pool_id)
        # Legacy per-pool file, imported once on first use.
        self.path = pool_episodes_file(self.pool_id)
        self.docs = DocStore(f"episodes:{self.pool_id}", legacy_file=self.path,
                              legacy_key=_record_key)

    def load(self, timeout: float = 10.0) -> List[Episode]:
        return self._build(self.docs.values())

    @staticmethod
    def _build(raw) -> List[Episode]:
        out: List[Episode] = []
        for obj in raw:
            try:
                out.append(Episode(**obj))
            except Exception:
                continue
        return out

    def add(self, episode: Episode, timeout: float = 10.0) -> Episode:
        with self.docs.transaction():
            episodes = self.load()
            episodes.append(episode)
            episodes = _apply_retention(episodes)
            self.docs.replace_all({str(e.id): _model_to_dict(e) for e in episodes})
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
        return self.docs.delete(str(episode_id))

    def set_pinned(self, episode_id: UUID | str, pinned: bool = True, timeout: float = 10.0) -> bool:
        """Pin or unpin one episode. A pinned episode is never pruned."""
        eid = str(episode_id)
        with self.docs.transaction():
            doc = self.docs.get(eid)
            if doc is None:
                return False
            try:
                episode = Episode(**doc)
            except Exception:
                return False
            episode.pinned = bool(pinned)
            self.docs.put(eid, _model_to_dict(episode))
            return True

    def stats(self, timeout: float = 10.0) -> dict:
        episodes = self.load(timeout=timeout)
        by_kind: dict[str, int] = {}
        by_outcome: dict[str, int] = {}
        explicit = 0
        pinned = 0
        for e in episodes:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            if e.outcome:
                by_outcome[e.outcome] = by_outcome.get(e.outcome, 0) + 1
            if e.explicit:
                explicit += 1
            if e.pinned:
                pinned += 1
        return {
            "total": len(episodes),
            "by_kind": by_kind,
            "by_outcome": by_outcome,
            "explicit": explicit,
            "pinned": pinned,
            "cap": MAX_EPISODES_PER_POOL,
        }


def _is_protected(episode: Episode) -> bool:
    """Pinned and explicitly recorded episodes survive every pruning pass."""
    return bool(getattr(episode, "pinned", False) or getattr(episode, "explicit", False))


def _apply_retention(episodes: List[Episode]) -> List[Episode]:
    """Enforce MAX_EPISODES_PER_POOL: prune low-signal kinds first by oldest.

    Protected episodes (explicit or pinned) are never dropped, so a pool over
    its cap on protected episodes alone simply stays over it: silently deleting
    what the agent chose to record would be worse than holding a few extra.
    """
    if len(episodes) <= MAX_EPISODES_PER_POOL:
        return episodes

    overflow = len(episodes) - MAX_EPISODES_PER_POOL
    prunable = [
        (idx, ep) for idx, ep in enumerate(episodes) if not _is_protected(ep)
    ]
    prunable.sort(key=lambda pair: pair[1].occurred_at)
    to_drop: set[int] = set()

    for idx, ep in prunable:
        if overflow <= 0:
            break
        if ep.kind in LOW_SIGNAL_KINDS:
            to_drop.add(idx)
            overflow -= 1

    if overflow > 0:
        for idx, _ep in prunable:
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
