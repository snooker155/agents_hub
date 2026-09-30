"""
Demo workspace routes: whether the demo is present, and adding or removing it
from Settings. See common/demo_workspace.py and docs/demo.md.

Two routes only, both cheap: the demo is either there or it isn't, and the
Settings toggle is the one caller. Neither takes a workspace argument — the
demo is always the fixed ``demo`` workspace, never a per-workspace concept.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from common.demo_workspace import demo_status, ensure_demo_workspace, remove_demo_workspace

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoToggle(BaseModel):
    present: bool


@router.get("")
async def get_demo_status():
    """Whether the demo is enabled (the ``DEMO_WORKSPACE`` setting), whether it
    is actually seeded, and a rough count of what it holds."""
    return await run_in_threadpool(demo_status)


@router.post("")
async def set_demo_status(body: DemoToggle):
    """Seed or remove the demo workspace on request, from the Settings toggle.

    Independent of the ``DEMO_WORKSPACE`` install flag/setting: an operator can
    turn the demo on or off here regardless of what the service started with,
    and ``demo_status().enabled`` still reports the setting for context.
    """
    if body.present:
        await run_in_threadpool(ensure_demo_workspace)
    else:
        await run_in_threadpool(remove_demo_workspace)
    return await run_in_threadpool(demo_status)
