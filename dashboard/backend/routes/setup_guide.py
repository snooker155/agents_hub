"""
The guided setup's routes (common/setup_guide.py, docs/assistant.md "Guided setup").

``GET /api/setup-guide`` is the person's guide: every step with its status,
what is next, whether the guide runs and how far a voice download has got
(each read moves that download on, common/setup_ops.py ``advance_work``).
``POST /api/setup-guide`` starts, skips, marks or ends it. The browser says
whether the welcome tour was taken (``tour_done``), since only it knows.

``POST /api/setup-guide/model`` is the one step before the assistant can talk:
the welcome window's form for a first model provider. It checks the key with
the provider, saves it and makes the provider's balanced model the default,
for an administrator (anyone outside ``multi`` mode).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from common import identity

router = APIRouter(prefix="/api/setup-guide", tags=["setup-guide"])


class GuideAction(BaseModel):
    action: str
    step: Optional[str] = None
    mode: Optional[str] = None
    tour_done: Optional[bool] = None


class FirstModel(BaseModel):
    provider: str
    api_key: str = ""
    base_url: str = ""


def _read(principal, tour_done: Optional[bool]) -> dict:
    from common import setup_guide, setup_ops
    setup_ops.advance_work(principal)
    return setup_guide.guide(principal, tour_done=tour_done)


@router.get("")
async def get_guide(request: Request, tour_done: Optional[bool] = None):
    return await run_in_threadpool(_read, identity.request_principal(request), tour_done)


@router.post("")
async def act(body: GuideAction, request: Request):
    from common import setup_guide
    principal = identity.request_principal(request)
    try:
        return await run_in_threadpool(setup_guide.act, principal, body.action, body.step,
                                       mode=body.mode, tour_done=body.tour_done)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/model")
async def first_model(body: FirstModel, request: Request):
    from common import setup_ops
    principal = identity.request_principal(request)
    try:
        result = await run_in_threadpool(setup_ops.first_model, body.provider, body.api_key, body.base_url,
                                         principal=principal)
    except setup_ops.SetupOpError as exc:
        raise HTTPException(status_code=403 if exc.code == "forbidden" else 400,
                            detail={"code": exc.code, "message": str(exc)})
    return result


__all__ = ["router"]
