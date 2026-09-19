"""
Regression replay API.

``POST /api/runs/{run_id}/replay`` re-runs a recorded run (optionally against a
different provider/model) and returns a diff of the original vs replay output
plus each side's tokens and estimated cost. The replay is recorded as its own
``channel="replay"`` run, excluded from cost/usage/budget aggregation.

The re-invocation is a real (potentially slow, billable) LLM call, so the
handler is a plain ``def`` — FastAPI runs it in a worker thread instead of
blocking the event loop.
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


@router.post("/api/runs/{run_id}/replay")
def replay(run_id: str, req: Optional[ReplayRequest] = None):
    req = req or ReplayRequest()
    try:
        return replay_run(run_id, provider=req.provider, model=req.model)
    except ReplayError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Replay failed: {e}")
