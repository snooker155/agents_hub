"""
The first run's routes (common/first_run.py, docs/installation.md, "The first run in the browser").

``GET /api/first-run`` says whether the browser shows the first run instead of
the app, and on which step; the app reads it once on load, so it stays cheap.
``POST /api/first-run`` saves the step, the language and the look, finishes
or starts it again.

The steps read and change the hub through these, besides routes of their own
(``/api/setup-guide/model`` for the first key, ``/api/settings`` for web
search, a workspace's personal memory, ``/api/demo``):

* ``GET /api/first-run/context``: what is already set (providers, the default
  model, the runtime and its ready set, search, demo, voice), no provider asked;
* ``GET /api/first-run/options``: each connected provider's model tiers and the
  voices (common/setup_ops.py ``options``), which asks the providers;
* ``POST /api/first-run/op``: one setup operation (choose_model, voice_cloud,
  voice_local, seed_demo), the same the assistant's ``setup_step`` makes after
  a yes; here pressing the button is the yes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from common import identity

router = APIRouter(prefix="/api/first-run", tags=["first-run"])

#: What a first run may change through ``/op``; ``local_set`` has its own route.
_OPERATIONS = ("choose_model", "voice_cloud", "voice_local", "seed_demo")


class FirstRunAction(BaseModel):
    action: str
    step: Optional[str] = None
    language: Optional[str] = None
    theme: Optional[str] = None


class FirstRunOp(BaseModel):
    operation: str
    args: Dict[str, Any] = Field(default_factory=dict)


def _applies() -> None:
    from common import first_run
    if not first_run.applies():
        raise HTTPException(status_code=404, detail="the first run does not apply in multi mode")


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


@router.get("/context")
async def get_context():
    from common import first_run
    _applies()
    return await run_in_threadpool(first_run.context)


@router.get("/options")
async def get_options():
    from common import setup_ops
    _applies()
    return await run_in_threadpool(setup_ops.options)


@router.post("/op")
async def run_op(body: FirstRunOp, request: Request):
    from common import setup_ops
    _applies()
    if body.operation not in _OPERATIONS:
        raise HTTPException(status_code=400, detail={"code": "unknown_operation",
                                                     "message": f"operation must be one of {', '.join(_OPERATIONS)}"})
    principal = identity.request_principal(request)
    try:
        return await run_in_threadpool(setup_ops.perform, body.operation, body.args, principal=principal)
    except setup_ops.SetupOpError as exc:
        raise HTTPException(status_code=403 if exc.code == "forbidden" else 400,
                            detail={"code": exc.code, "message": str(exc)})


__all__ = ["router"]
