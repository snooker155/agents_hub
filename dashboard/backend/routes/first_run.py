"""
The first run's routes (common/first_run.py, docs/installation.md, "The first run in the browser").

``GET /api/first-run`` says whether the browser shows the first run instead of
the app, and on which step; the app reads it once on load, so it stays cheap.
``POST /api/first-run`` saves the step, the language and the look, finishes
or starts it again.

The steps read and change the hub through these, besides routes of their own
(a workspace's personal memory, ``/api/demo``):

* ``POST /api/first-run/model``: connect a provider and make its balanced
  model the default (common/setup_ops.py ``first_model``), the key checked
  against the provider first;
* ``GET /api/first-run/context``: what is already set (providers, the default
  model, demo), no provider asked;
* ``GET /api/first-run/options``: each connected provider's model tiers
  (common/setup_ops.py ``options``), which asks the providers;
* ``POST /api/first-run/op``: one setup operation (choose_model, seed_demo).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool


router = APIRouter(prefix="/api/first-run", tags=["first-run"])

#: What a first run may change through ``/op``.
_OPERATIONS = ("choose_model", "seed_demo")


class FirstRunAction(BaseModel):
    action: str
    step: Optional[str] = None
    language: Optional[str] = None
    theme: Optional[str] = None


class FirstRunOp(BaseModel):
    operation: str
    args: Dict[str, Any] = Field(default_factory=dict)


class FirstModel(BaseModel):
    provider: str
    api_key: str = ""
    base_url: str = ""


@router.get("")
async def get_status():
    from common import first_run
    return await run_in_threadpool(first_run.status)


@router.post("")
async def act(body: FirstRunAction):
    from common import first_run
    try:
        return await run_in_threadpool(first_run.act, body.action, step=body.step,
                                       language=body.language, theme=body.theme)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/model")
async def first_model(body: FirstModel):
    from common import setup_ops
    try:
        return await run_in_threadpool(setup_ops.first_model, body.provider, body.api_key, body.base_url)
    except setup_ops.SetupOpError as exc:
        raise HTTPException(status_code=400,
                            detail={"code": exc.code, "message": str(exc)})


@router.get("/context")
async def get_context():
    from common import first_run
    return await run_in_threadpool(first_run.context)


@router.get("/options")
async def get_options():
    from common import setup_ops
    return await run_in_threadpool(setup_ops.options)


@router.post("/op")
async def run_op(body: FirstRunOp):
    from common import setup_ops
    if body.operation not in _OPERATIONS:
        raise HTTPException(status_code=400, detail={"code": "unknown_operation",
                                                     "message": f"operation must be one of {', '.join(_OPERATIONS)}"})
    try:
        return await run_in_threadpool(setup_ops.perform, body.operation, body.args)
    except setup_ops.SetupOpError as exc:
        raise HTTPException(status_code=400,
                            detail={"code": exc.code, "message": str(exc)})


__all__ = ["router"]
