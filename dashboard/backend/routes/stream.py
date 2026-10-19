"""
Single multiplexed SSE endpoint.

The whole dashboard holds ONE EventSource connection to ``/api/stream`` instead
of one connection per stream plus dozens of polling loops. Every event carries a
``channel`` field so the client routes it. A client may add/remove channel
interest on its existing connection (no reconnect) as the user opens detail pages.

Default channels:
- ``app``              resource-change invalidations + periodic external snapshots
- ``__notifications__``  user notification push (legacy channel name, kept as-is)
- ``<session_id>``       when ``?session=`` is supplied (chat token stream)

Reconnects
----------
``EventSource`` reconnects on its own (network blip, tab wake) and remembers
the last ``id:`` line it saw, sending it back as ``Last-Event-ID``. The
frontend also sends back its previous ``?client=`` id. When that client is
still known to the broker (see ``SessionBroker.resume_client``), the
connection resumes on the same id and channel set and replays what it missed
as a burst of ``id:``-numbered frames before going live again; the ``_meta
ready`` event says so with ``resumed: true`` and how many were replayed.

When it is not known (a restart) a fresh client is opened and ``resumed``
comes back false, telling the frontend to refetch rather than trust a stream
that may have skipped a beat.
"""
import json
from typing import List, Optional, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from common.session_broker import broker, APP_CHANNEL, notify_change, notify_delta
from plans.service import NOTIFICATIONS_CHANNEL

router = APIRouter(prefix="/api/stream", tags=["stream"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class ChannelUpdate(BaseModel):
    add: List[str] = []
    remove: List[str] = []


class PublishEvent(BaseModel):
    channel: str
    event: dict


class NotifyChange(BaseModel):
    resource: str
    meta: dict = {}
    # When true the meta is one row's changed fields, not a list invalidation —
    # relayed to notify_delta so it coalesces with other rows of the same
    # resource instead of making every tab refetch.
    delta: bool = False


def _parse_last_event_id(raw: Optional[str]) -> Optional[int]:
    """``Last-Event-ID`` as EventSource sends it back automatically (or the
    ``since`` query param standing in for it, see ``stream()`` below): the
    broker's per-client counter, or None when absent or not a number. Never
    fails the request over it."""
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


@router.get("")
async def stream(request: Request, session: Optional[str] = None, client: Optional[str] = None,
                  since: Optional[str] = None, channels: Optional[str] = None):
    """Open the single multiplexed SSE connection for this browser tab.

    ``client`` and ``Last-Event-ID`` are how a reconnecting browser asks to
    resume rather than start over — see the module docstring. ``since`` is the
    same value as a query parameter: ``EventSource`` cannot set a custom
    ``Last-Event-ID`` header on a freshly created connection, only on the
    browser's own silent retry of an existing one, so the frontend (which
    replaces the connection itself on error) sends it this way instead.
    ``channels``, comma-separated, is the frontend's current channel set,
    added to the defaults when a fresh client has to be opened.
    """
    last_event_id = _parse_last_event_id(request.headers.get("last-event-id") or since)
    replayed: List[dict] = []
    resumed = False

    resume = broker.resume_client(client, last_event_id) if client else None
    if resume is not None:
        client_id = client
        resumed = True
        replayed = resume
    else:
        default_channels = [APP_CHANNEL, NOTIFICATIONS_CHANNEL]
        if session:
            default_channels.append(session)
        for extra in (channels or "").split(","):
            extra = extra.strip()
            if extra and extra not in default_channels:
                default_channels.append(extra)
        client_id, _ = broker.open_client(default_channels)

    async def _generate():
        # Hand the client its id first so it can manage dynamic channels.
        yield f"data: {json.dumps({'channel': '_meta', 'type': 'ready', 'client_id': client_id, 'resumed': resumed, 'replayed': len(replayed)})}\n\n"
        for event in replayed:
            yield f"id: {event['id']}\ndata: {json.dumps(event)}\n\n"
        events = broker.client_events(cast(str, client_id))
        try:
            async for event in events:
                if await request.is_disconnected():
                    break
                event_id = event.get("id")
                prefix = f"id: {event_id}\n" if event_id is not None else ""
                yield f"{prefix}data: {json.dumps(event)}\n\n"
        finally:
            # Close the inner generator explicitly rather than leaving it to be
            # garbage-collected whenever that happens to occur: that is what
            # marks this client disconnected (see SessionBroker.client_events),
            # starting its short replay window, so it must happen promptly.
            await events.aclose()

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/{client_id}/channels")
async def update_channels(client_id: str, body: ChannelUpdate):
    """Add/remove channel interest on an existing connection (detail pages)."""
    for ch in body.add:
        broker.add_channel(client_id, ch)
    for ch in body.remove:
        broker.remove_channel(client_id, ch)
    return {"client_id": client_id, "added": body.add, "removed": body.remove}


@router.post("/notify")
async def relay_notify(body: NotifyChange):
    """Re-publish a `<resource>.changed` event coming from a subprocess.

    Agent/flow subprocesses have no event loop, so they POST here and the backend
    (which owns the broker loop) fans the change out to all connected clients.
    """
    meta = body.meta or {}
    if body.delta:
        key = str(meta.get("instance_id") or meta.get("run_id") or meta.get("id") or "")
        notify_delta(body.resource, key, meta)
    else:
        notify_change(body.resource, **meta)
    return {"ok": True}


@router.post("/publish")
async def relay_publish(body: PublishEvent):
    """Re-publish one channel event coming from a process without the broker.

    The counterpart of ``common.session_broker.publish_event``: an instance
    runner or an agent subprocess has no event loop of its own, so a view op
    it applies (``view:<id>``) or any other live event is POSTed here and
    fanned out by the backend, which owns the broker, to the tabs following
    that channel. Unlike ``/notify`` nothing is coalesced: every event is
    delivered, in the order it arrived.
    """
    channel = body.channel.strip()
    if not channel:
        raise HTTPException(status_code=400, detail="channel is required")
    if not body.event.get("type"):
        raise HTTPException(status_code=400, detail="event needs a type")
    await broker.apublish(channel, body.event)
    return {"ok": True}
