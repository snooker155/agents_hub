"""
Git connector API (GitHub / GitLab).

Endpoints:
- GET  /api/git/config   — { github: {has_token}, gitlab: {has_token, base_url} }
- PUT  /api/git/config   — set/clear a provider token, set GitLab base_url
- POST /api/git/test     — verify the configured token, returns the login
- GET  /api/git/repos    — list repos accessible with the configured token
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from connectors.git import store as git_store
from connectors.git.providers import get_provider, GitProviderError


router = APIRouter(prefix="/api/git", tags=["git"])


class GitConfigUpdate(BaseModel):
    provider: str
    token: Optional[str] = None   # write-only; None = keep existing
    clear_token: bool = False
    base_url: Optional[str] = None  # gitlab only


class GitTestRequest(BaseModel):
    provider: str


def _check_provider(provider: str) -> str:
    if provider not in git_store.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown git provider: {provider!r}")
    return provider


@router.get("/config")
async def get_config():
    return git_store.public_config()


@router.put("/config")
async def update_config(payload: GitConfigUpdate):
    provider = _check_provider(payload.provider)
    if payload.clear_token:
        git_store.set_token(provider, "")
    elif payload.token is not None and payload.token.strip():
        git_store.set_token(provider, payload.token)
    if payload.base_url is not None and provider == "gitlab":
        git_store.set_base_url("gitlab", payload.base_url)
    return git_store.public_config()


@router.post("/test")
async def test_connection(payload: GitTestRequest):
    provider = _check_provider(payload.provider)
    try:
        return await asyncio.to_thread(get_provider(provider).test_connection)
    except GitProviderError as e:
        return {"ok": False, "login": None, "error": str(e)}


@router.get("/repos")
async def list_repos(provider: str = Query(...), search: Optional[str] = Query(None)):
    _check_provider(provider)
    try:
        return await asyncio.to_thread(get_provider(provider).list_repos, search)
    except GitProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
