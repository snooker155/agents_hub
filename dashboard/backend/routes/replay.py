"""
Regression replay API.

``POST /api/runs/{run_id}/replay`` re-runs a recorded run (optionally against a
different provider/model) and returns a diff of the original vs replay output
plus each side's tokens and estimated cost. The replay is recorded as its own
``channel="replay"`` run, excluded from cost/usage/budget aggregation.

The re-invocation is a real (potentially slow, billable) LLM call, so the
handler is a plain ``def`` — FastAPI runs it in a worker thread instead of
blocking the event loop. While it runs, the page can follow it live: given a
``replay:<id>`` channel and its stream ``client_id``, the replay publishes its
tokens, thinking and tool calls there (``agents.callbacks.ChannelPublishCallback``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.agent_replay import replay_run, ReplayError

router = APIRouter(tags=["replay"])


class ReplayRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    # The page's stream connection and the channel it follows the replay on.
    # The client is subscribed here, before the replay starts, so no early
    # event is lost to a subscription still on its way.
    client_id: Optional[str] = None
    channel: Optional[str] = None


@router.post("/api/runs/{run_id}/replay")
def replay(run_id: str, req: Optional[ReplayRequest] = None):
    req = req or ReplayRequest()
    channel = (req.channel or "").strip() or None
    if channel and not channel.startswith("replay:"):
        raise HTTPException(status_code=400, detail="channel must start with 'replay:'")
    if channel and req.client_id:
        from common.session_broker import broker
        broker.add_channel(req.client_id, channel)
    try:
        return replay_run(run_id, provider=req.provider, model=req.model, channel=channel)
    except ReplayError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Replay failed: {e}")
