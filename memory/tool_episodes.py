"""Tool builders for the record_episode and recall_episodes tools, bound to one memory binding."""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool


log = logging.getLogger(__name__)


def build_episode_tools(ctx):
    _pool_name = ctx._pool_name
    _refuse_write = ctx._refuse_write
    _ro_ids = ctx._ro_ids
    multi = ctx.multi
    pool_id = ctx.pool_id
    pool_ids = ctx.pool_ids

    # -----------------------------------------------------------------------
    # record_episode — log a discrete event
    # -----------------------------------------------------------------------

    from typing import List as _List

    class _RecordEpisodeInput(BaseModel):
        kind: str = Field(
            ...,
            description=(
                "One of: 'interaction' (user exchange), 'task' (a task you ran), "
                "'decision' (a non-trivial choice), 'error' (something failed), "
                "'observation' (noteworthy fact you noticed)."
            ),
        )
        summary: str = Field(..., description="One to three sentences describing what happened.")
        actor: Optional[str] = Field(None, description="Who/what acted — 'user', your agent id, or a tool name.")
        subject: Optional[str] = Field(None, description="What the episode was about — task id, file, topic.")
        outcome: Optional[str] = Field(
            None,
            description="One of: 'success', 'failure', 'partial', 'n/a'. Omit if not applicable.",
        )
        tags: Optional[_List[str]] = Field(None, description="Optional list of short tags for later filtering.")
        details: Optional[dict] = Field(None, description="Optional free-form payload (error message, diff, etc).")
        pinned: bool = Field(
            False,
            description=(
                "Pin this episode so it is never pruned, whatever the retention cap. "
                "Use sparingly, for events you must still be able to find in a year."
            ),
        )

    def _record_episode_impl(
        kind: str,
        summary: str,
        actor: Optional[str] = None,
        subject: Optional[str] = None,
        outcome: Optional[str] = None,
        tags: Optional[list] = None,
        details: Optional[dict] = None,
        pinned: bool = False,
    ) -> str:
        if pool_id in _ro_ids:
            return _refuse_write(pool_id)
        try:
            from memory.episodic import Episode, EpisodeStore
            valid_kinds = {"interaction", "task", "decision", "error", "observation"}
            if kind not in valid_kinds:
                return json.dumps({"ok": False, "error": f"kind must be one of {sorted(valid_kinds)}"})
            valid_outcomes = {"success", "failure", "partial", "n/a"}
            if outcome is not None and outcome not in valid_outcomes:
                return json.dumps({"ok": False, "error": f"outcome must be one of {sorted(valid_outcomes)} or omitted"})

            ep = Episode(
                pool_id=pool_id,
                kind=kind,  # type: ignore[arg-type]
                summary=summary,
                actor=actor,
                subject=subject,
                outcome=outcome,  # type: ignore[arg-type]
                tags=tags or [],
                details=details or {},
                # The agent called this itself, so the episode is deliberate:
                # retention never prunes it in favour of an automatic write.
                explicit=True,
                pinned=bool(pinned),
            )
            EpisodeStore(pool_id).add(ep)
            return json.dumps({"ok": True, "id": str(ep.id), "kind": ep.kind, "explicit": True, "pinned": ep.pinned})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"record_episode failed: {e}"})

    record_episode_tool = StructuredTool.from_function(
        name="record_episode",
        description=(
            "Log a discrete episodic event to shared memory. "
            "Use for things worth remembering as events: notable user interactions, completed/failed tasks, "
            "decisions you made, errors you hit, observations about the work. "
            "Keep `summary` short (1-3 sentences). An episode you record here is never pruned to make room "
            "for the automatic per-exchange entries, so recording deliberately is how something survives. "
            "Set `pinned=True` for the rare event that must outlive everything else."
            + (" Episodes are recorded in the primary pool." if multi else "")
        ),
        func=_record_episode_impl,
        args_schema=_RecordEpisodeInput,
    )

    # -----------------------------------------------------------------------
    # recall_episodes — query past events
    # -----------------------------------------------------------------------

    class _RecallEpisodesInput(BaseModel):
        query: Optional[str] = Field(None, description="Optional keyword query over summary/subject/tags/details.")
        kind: Optional[str] = Field(None, description="Filter by kind: interaction|task|decision|error|observation.")
        outcome: Optional[str] = Field(None, description="Filter by outcome: success|failure|partial|n/a.")
        since: Optional[str] = Field(None, description="ISO timestamp; only return episodes at or after this time.")
        limit: int = Field(10, description="Max number of episodes to return (default 10).")

    def _recall_episodes_impl(
        query: Optional[str] = None,
        kind: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        try:
            from memory.episodic import EpisodeStore
            from datetime import datetime
            since_dt = None
            if since:
                try:
                    since_dt = datetime.fromisoformat(since)
                except Exception:
                    return json.dumps({"ok": False, "error": f"could not parse `since` as ISO datetime: {since!r}"})

            capped = max(1, min(int(limit), 50))
            hits: list[tuple] = []  # (episode, pool_id)
            for pid in pool_ids:
                for e in EpisodeStore(pid).query(
                    query=query,
                    kind=kind,
                    outcome=outcome,
                    since=since_dt,
                    limit=capped,
                ):
                    hits.append((e, pid))
            # Merge across pools by recency, then trim to the requested limit.
            hits.sort(key=lambda t: t[0].occurred_at, reverse=True)
            hits = hits[:capped]
            return json.dumps({
                "ok": True,
                "count": len(hits),
                "episodes": [
                    {
                        "id": str(e.id),
                        "kind": e.kind,
                        "summary": e.summary,
                        "actor": e.actor,
                        "subject": e.subject,
                        "outcome": e.outcome,
                        "tags": e.tags,
                        "details": e.details,
                        "occurred_at": e.occurred_at.isoformat(),
                        **({"pool": _pool_name(pid)} if multi else {}),
                    }
                    for e, pid in hits
                ],
            }, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"recall_episodes failed: {e}"})

    recall_episodes_tool = StructuredTool.from_function(
        name="recall_episodes",
        description=(
            "Query past episodic events from shared memory. "
            "Filter by kind, outcome, or time, and/or rank by a keyword query. "
            "Use to answer 'what happened before?' / 'have I tried this?' / 'what failures have I seen?'"
            + (" All attached pools are searched; each episode carries a `pool` field." if multi else "")
        ),
        func=_recall_episodes_impl,
        args_schema=_RecallEpisodesInput,
    )

    return record_episode_tool, recall_episodes_tool
