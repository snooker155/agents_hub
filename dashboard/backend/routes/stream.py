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
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Request
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


class NotifyChange(BaseModel):
    resource: str
    meta: dict = {}
    # When true the meta is one row's changed fields, not a list invalidation —
    # relayed to notify_delta so it coalesces with other rows of the same
    # resource instead of making every tab refetch.
    delta: bool = False


@router.get("")
async def stream(request: Request, session: Optional[str] = None):
    """Open the single multiplexed SSE connection for this browser tab."""
    channels = [APP_CHANNEL, NOTIFICATIONS_CHANNEL]
    if session:
        channels.append(session)
    client_id, _ = broker.open_client(channels)

    async def _generate():
        # Hand the client its id first so it can manage dynamic channels.
        yield f"data: {json.dumps({'channel': '_meta', 'type': 'ready', 'client_id': client_id})}\n\n"
        async for event in broker.client_events(client_id):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event)}\n\n"

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
