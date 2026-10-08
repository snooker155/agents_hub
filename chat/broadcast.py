"""
One turn, every viewer.

A chat turn used to belong to the browser that asked for it: the events went
down the response body of that one request, so a second tab, a second device, or
the run's own page saw nothing until the turn was over. That made the live part
of the product — watching the answer being written — a property of the window
you happened to type in.

This wrapper puts the turn on a channel instead. Every event a pipeline yields
is published to ``chat:<conversation_id>`` as well as handed to the caller, so
anyone with that conversation open follows the same generation, and is recorded
in :mod:`common.live_runs` so anyone who arrives late can catch up before
following it.

Three points are deliberate:

* **It wraps the pipelines, not the routes.** Telegram, the instance inbox and
  the CLI drive the same generators; wrapping here means a turn started from any
  of them streams into an open chat page, rather than only turns typed into one.
* **The originating client is named on every event** (``origin_client``), which
  is how the tab that started the turn recognises its own echo and ignores it.
  Without it the sender would render every token twice.
* **A finished turn is written to the chat even if the browser is gone.** The
  transcript is a service record now; closing the tab mid-answer must not be the
  thing that loses it. The write is skipped when the browser already saved that
  run, so the browser's richer version stays authoritative.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional

from common import live_runs

log = logging.getLogger(__name__)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def announce_running_change(conversation_id: Optional[str]) -> None:
    """Tell every open Chat page that a conversation's turn began or ended, so
    the list can mark the conversations being answered (GET /api/chats/running
    says which; the event names none, since not every page may see every chat).

    Not from a service replica: its turns are announced by the backend it
    posts their events to (routes/instances.py), which is the one that keeps
    them (common/live_runs.record_conversation_event).
    """
    if not conversation_id:
        return
    from instances.registry import ENV_INSTANCE_ID
    if os.environ.get(ENV_INSTANCE_ID):
        return
    try:
        from common.session_broker import notify_change
        notify_change("chat_turns")
    except Exception:  # noqa: BLE001 - a mark in a list is an extra, never the turn
        log.debug("could not announce a running change", exc_info=True)


def channel_for(conversation_id: Optional[str]) -> Optional[str]:
    """The channel a conversation's live turn is published on."""
    return f"chat:{conversation_id}" if conversation_id else None


async def _publish(channel: Optional[str], event: Dict[str, Any]) -> None:
    if not channel:
        return
    from common.session_broker import broker

    # Nobody watching: a turn still records into live_runs (a viewer may arrive
    # mid-answer), but there is no one to hand a copy of every token to.
    if not broker.has_subscribers(channel):
        return
    try:
        await broker.apublish(channel, event)
    except Exception:
        # A live view is an extra, never the run. A broken publish must not take
        # the turn down with it.
        pass


def _publish_now(channel: Optional[str], event: Dict[str, Any]) -> None:
    """Publish without awaiting.

    For the one path where awaiting is not allowed: the consumer abandoned the
    generator (the browser navigated away mid-turn), so this is running inside
    ``GeneratorExit``, where an await raises "async generator ignored
    GeneratorExit" and would swallow the cleanup. The viewers still watching
    must be told the turn is over, or their mirror stays live for good.
    """
    if not channel:
        return
    from common.session_broker import broker

    try:
        broker.publish_threadsafe(channel, event)
    except Exception:
        pass


def _persist_turn(conversation_id: str, request, event: Dict[str, Any]) -> bool:
    """Record a completed turn in the stored chat, unless the browser did.

    Only for the web chat, and only for a conversation the dashboard already
    keeps. A Telegram thread is a mirror of a transcript that lives on Telegram
    and is rebuilt from the runs behind it; an instance delivery has no
    conversation page at all. Writing either into the chat store would fill the
    sidebar with conversations nothing opens, or append a second copy of a
    transcript that is reconstructed anyway. Returns whether anything was written.
    """
    from common import chat_store

    if (getattr(request, "source", None) or "chat") != "chat":
        return False
    run_id = str(event.get("run_id") or "")
    response = str(event.get("response") or "")
    if not run_id or not response.strip() or not event.get("ok"):
        return False
    user_message = str(getattr(request, "message", "") or "")
    # A turn that changed hands (chat/handoff.py) is one bubble per agent: each
    # handing agent's reply, then the answer, which carries the handoff that
    # brought it (the chat draws the divider from it). The user's message goes
    # in front of the first bubble only.
    handoffs = [h for h in (event.get("handoffs") or []) if isinstance(h, dict)]
    written = False
    try:
        for i, h in enumerate(handoffs):
            written |= chat_store.append_turn(
                conversation_id,
                run_id=str(h.get("run_id") or ""),
                user_message=user_message if i == 0 else "",
                agent_message=str(h.get("from_response") or ""),
                agent_id=h.get("from_agent_id"),
                usage=h.get("usage"),
                duration_ms=h.get("duration_ms"),
                extra={"handoff": handoffs[i - 1]} if i > 0 else None,
            )
        # The reply's sources and links travel with the bubble, so a chat
        # reloaded from the server still shows them.
        extra = {k: event[k] for k in ("citations", "entities") if event.get(k)}
        if handoffs:
            extra["handoff"] = handoffs[-1]
        written |= chat_store.append_turn(
            conversation_id,
            run_id=run_id,
            user_message="" if handoffs else user_message,
            agent_message=response,
            agent_id=event.get("agent_id") or getattr(request, "agent_id", None),
            usage=event.get("usage"),
            duration_ms=event.get("duration_ms"),
            extra=extra or None,
        )
        # The conversation continues with the agent that answered.
        if handoffs and event.get("agent_id"):
            chat_store.set_agent(conversation_id, str(event["agent_id"]))
    except Exception:  # noqa: BLE001 - the safety net must never fail the turn it records
        pass
    return written


async def broadcast_turn(request, pipeline: AsyncIterator[Dict[str, Any]]):
    """Yield every event of *pipeline* through, publishing and recording a copy.

    The caller sees exactly what it saw before this existed, in the same order;
    everything added here happens on the way past.
    """
    conversation_id = getattr(request, "conversation_id", None)
    channel = channel_for(conversation_id)
    origin_client = getattr(request, "client_id", None)
    source = getattr(request, "source", None) or "chat"
    user_message = str(getattr(request, "message", "") or "")

    turn_id = live_runs.start_turn(conversation_id=conversation_id,
                                   user_message=user_message, source=source)
    announce_running_change(conversation_id)
    stamp = {"conversation_id": conversation_id, "origin_client": origin_client}
    client_turn_id = getattr(request, "client_turn_id", None)
    if client_turn_id:
        stamp["client_turn_id"] = client_turn_id

    await _publish(channel, {
        **stamp,
        "type": "turn_start",
        "message": user_message,
        "source": source,
        "agent_id": getattr(request, "agent_id", None),
        "flow_id": getattr(request, "flow_id", None),
        "team_id": getattr(request, "team_id", None),
        "started_at": _iso(),
    })

    end_event = {**stamp, "type": "chat_stream_end"}
    try:
        async for event in pipeline:
            live_runs.record(turn_id, event)
            await _publish(channel, {**event, **stamp})
            if event.get("type") == "done" and conversation_id:
                if _persist_turn(conversation_id, request, event):
                    # A page with the chat open reloads it, as after any other
                    # save; the page still writing this turn waits until it is
                    # done (components/chatStore.js).
                    await _publish(channel, {"type": "chat_saved", "chat_id": conversation_id,
                                             "conversation_id": conversation_id,
                                             "origin_client": None})
            yield event
    except GeneratorExit:
        # The caller walked away — its browser navigated off the page. Whoever
        # else is watching is told, without awaiting (see _publish_now).
        live_runs.finish(turn_id, status="finished")
        announce_running_change(conversation_id)
        _publish_now(channel, end_event)
        raise
    except Exception as e:
        live_runs.finish(turn_id, status="failed", error=str(e))
        announce_running_change(conversation_id)
        await _publish(channel, {**stamp, "type": "done", "ok": False, "error": str(e)})
        await _publish(channel, end_event)
        raise
    else:
        live_runs.finish(turn_id, status="finished")
        announce_running_change(conversation_id)
        # The sentinel says the turn is over whatever happened, so a follower can
        # stop showing it as live without waiting on a terminal event it might
        # never get.
        await _publish(channel, end_event)


__all__ = ["announce_running_change", "broadcast_turn", "channel_for"]
