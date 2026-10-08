"""Silent auto-journal and auto-episode writes, called by the chat and run layer rather than by the agent."""
from __future__ import annotations

import logging
from typing import Optional


from .store import MemoryStore

log = logging.getLogger(__name__)
from .tool_support import JOURNAL_PREFIX, _persist




# ---------------------------------------------------------------------------
# Silent auto-journal — called by the chat/run layer, not by the agent
# ---------------------------------------------------------------------------

def silent_journal_append(
    pool_id: str,
    agent_id: str,
    user_message: str,
    response: str,
    *,
    run_id: Optional[str] = None,
) -> None:
    """Append a compact exchange summary to the pool journal without agent involvement.

    Truncates message/response to keep entries scannable. Silently swallows all
    errors so a journal failure never breaks a chat response.
    """
    try:
        from datetime import datetime, timezone
        from uuid import uuid4

        MAX_MSG = 300
        MAX_RESP = 500

        msg_short = user_message.strip()
        if len(msg_short) > MAX_MSG:
            msg_short = msg_short[:MAX_MSG] + "…"
        resp_short = response.strip()
        if len(resp_short) > MAX_RESP:
            resp_short = resp_short[:MAX_RESP] + "…"

        now = datetime.now(timezone.utc)
        title = JOURNAL_PREFIX + now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        parts = [time_str, f"agent:{agent_id}"]
        if run_id:
            parts.append(f"run:{run_id[:8]}")
        header = "[" + " | ".join(parts) + "]"

        entry = f"{header}\n**User:** {msg_short}\n**Response:** {resp_short}"

        store = MemoryStore()
        mem = store.get(pool_id)
        if not mem:
            return

        existing = next((n for n in mem.notes if n.get("title") == title), None)
        if existing:
            existing["content"] = existing["content"] + "\n\n---\n\n" + entry
        else:
            mem.notes.append({
                "id": str(uuid4()),
                "title": title,
                "content": entry,
                "created_at": now.isoformat(),
            })

        _persist(store, mem)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Silent auto-episode helpers — called by the chat/run layer, not by the agent
# ---------------------------------------------------------------------------

def _condense(text: str, max_chars: int) -> str:
    """Return the first sentence (or paragraph) of `text`, capped at max_chars.

    The full text lives in the journal — the episode only needs a scannable lead.
    """
    if not text:
        return ""
    cleaned = " ".join(text.strip().split())  # collapse whitespace + newlines
    if not cleaned:
        return ""
    # Find the first sentence terminator (., !, ?, …) followed by space or end.
    end = -1
    for i, ch in enumerate(cleaned):
        if ch in ".!?…":
            nxt = cleaned[i + 1] if i + 1 < len(cleaned) else " "
            if nxt == " " or i == len(cleaned) - 1:
                end = i + 1
                break
    snippet = cleaned[:end] if end > 0 else cleaned
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rstrip() + "…"
    return snippet


def silent_interaction_episode(
    pool_id: str,
    agent_id: str,
    user_message: str,
    response: str,
    *,
    run_id: Optional[str] = None,
) -> None:
    """Record a one-line summary of an exchange as an episode.

    The full exchange lives in the journal; this episode is a compact, scannable
    pointer for `recall_episodes` queries. Best-effort: errors are swallowed.
    """
    try:
        from memory.episodic import Episode, EpisodeStore

        ask = _condense(user_message, max_chars=140)
        gist = _condense(response, max_chars=180)

        if ask and gist:
            summary = f"Asked: {ask} | Replied: {gist}"
        elif ask:
            summary = f"Asked: {ask}"
        elif gist:
            summary = f"Replied: {gist}"
        else:
            summary = "Empty exchange"

        ep = Episode(
            pool_id=str(pool_id),
            agent_id=agent_id,
            run_id=run_id,
            kind="interaction",
            summary=summary,
            actor="user",
            outcome="n/a",
        )
        EpisodeStore(str(pool_id)).add(ep)
    except Exception:
        pass


def silent_task_episode(
    pool_id: str,
    agent_id: str,
    *,
    task_id: Optional[str] = None,
    run_id: Optional[str] = None,
    status: str,
    exit_code: Optional[int] = None,
    error: Optional[str] = None,
    workspace: Optional[str] = None,
) -> None:
    """Record a 'task' episode at the end of an agent run.

    `status` is the run status from run_manager (completed/failed/stopped/...);
    we map it to an episode outcome. Best-effort, errors are swallowed.
    """
    try:
        from memory.episodic import Episode, EpisodeStore

        outcome_map = {
            "completed": "success",
            "failed": "failure",
            "error": "failure",
            "stopped": "partial",
            "stop": "partial",
        }
        outcome = outcome_map.get(status, "n/a")
        summary_parts = [f"Task run {status}"]
        if exit_code is not None:
            summary_parts.append(f"exit_code={exit_code}")
        if error:
            err_short = error.strip()
            if len(err_short) > 240:
                err_short = err_short[:240] + "…"
            summary_parts.append(f"error: {err_short}")
        summary = "; ".join(summary_parts)

        details: dict = {}
        if exit_code is not None:
            details["exit_code"] = exit_code
        if error:
            details["error"] = error

        ep = Episode(
            pool_id=str(pool_id),
            agent_id=agent_id,
            workspace=workspace,
            run_id=run_id,
            kind="task",
            summary=summary,
            actor=agent_id,
            subject=task_id,
            outcome=outcome,  # type: ignore[arg-type]
            details=details,
        )
        EpisodeStore(str(pool_id)).add(ep)
    except Exception:
        pass
