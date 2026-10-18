"""
Identity API: what the frontend bootstraps from.

- ``GET /api/auth/mode``  the posture this hub is in (``single``) and the
                          identity features to render (none)
- ``GET /api/auth/me``    who the caller is: the local operator

There is one operator and nothing to sign in to, so there is no login, no
logout, no accounts and no membership here. The two routes stay because the
SPA, the CLI (``ah auth whoami``) and an API client read them to learn that.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from common import identity

router = APIRouter(tags=["auth"])


@router.get("/api/auth/mode")
async def get_mode():
    """The posture this hub is in. ``operator`` is who every request acts as."""
    return {
        "mode": identity.current_mode(),
        "bootstrap_required": False,
        "features": identity.mode_features(),
        "operator": identity.principal_dict(identity.request_principal(None)),
    }


@router.get("/api/auth/me")
async def me(request: Request):
    """Who the caller is: the constant local operator."""
    return identity.principal_dict(identity.request_principal(request))
