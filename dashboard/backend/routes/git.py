"""
Git connector API (GitHub / GitLab / Bitbucket Cloud / Gitea).

Endpoints, each for one workspace (``?workspace=``, the default workspace
when omitted):
- GET    /api/git/config   — per-provider public config (has_token flags, plus
                             base_url for gitlab/gitea, username for
                             bitbucket) in effect there, and per provider
                             whether the workspace defines it (``sources``)
- PUT    /api/git/config   — set/clear a provider token, set gitlab/gitea
                             base_url, set the bitbucket username; on a
                             workspace other than the default this defines
                             the provider there
- DELETE /api/git/config   — ``?provider=``: drop a workspace's own entry, so
                             it uses the default workspace's again
- POST   /api/git/test     — verify the token in effect there, returns the login
- GET    /api/git/repos    — list repos accessible with the token in effect there

A provider lives in the workspace that defines it, the default workspace's
everywhere else (connectors/git/store.py). The middleware reads
``?workspace=`` like any other request: reading needs membership, changing
needs an editor of that workspace.
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

from common.session_broker import notify_change
from connectors.git import store as git_store
from connectors.git.providers import get_provider, GitProviderError


router = APIRouter(prefix="/api/git", tags=["git"])


class GitConfigUpdate(BaseModel):
    provider: str
    token: Optional[str] = None   # write-only; None = keep existing
    clear_token: bool = False
    base_url: Optional[str] = None  # gitlab, gitea
    username: Optional[str] = None  # bitbucket


class GitTestRequest(BaseModel):
    provider: str


def _check_provider(provider: str) -> str:
    if provider not in git_store.PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown git provider: {provider!r}")
    return provider


def _ws(workspace: Optional[str]) -> str:
    """The workspace named by ``?workspace=`` (the default when empty); 404
    for one that does not exist."""
    from common.workspace_context import normalize_workspace_name
    ws = normalize_workspace_name(workspace or "") or git_store.DEFAULT_WORKSPACE
    if ws != git_store.DEFAULT_WORKSPACE:
        from workspace import get_workspace_folder
        if get_workspace_folder(ws) is None:
            raise HTTPException(status_code=404, detail=f"Workspace '{ws}' does not exist")
    return ws


@router.get("/config")
async def get_config(workspace: Optional[str] = None):
    return git_store.public_config(_ws(workspace))


@router.put("/config")
async def update_config(payload: GitConfigUpdate, workspace: Optional[str] = None):
    provider = _check_provider(payload.provider)
    ws = _ws(workspace)
    if payload.clear_token:
        git_store.set_token(provider, "", ws)
    elif payload.token is not None and payload.token.strip():
        git_store.set_token(provider, payload.token, ws)
    if payload.base_url is not None and provider in ("gitlab", "gitea"):
        git_store.set_base_url(provider, payload.base_url, ws)
    if payload.username is not None and provider == "bitbucket":
        git_store.set_username("bitbucket", payload.username, ws)
    notify_change("connector_git", workspace=ws)
    return git_store.public_config(ws)


@router.delete("/config")
async def remove_config(provider: str = Query(...), workspace: Optional[str] = None):
    """The workspace stops defining ``provider`` and uses the default's."""
    provider = _check_provider(provider)
    ws = _ws(workspace)
    if ws == git_store.DEFAULT_WORKSPACE:
        raise HTTPException(status_code=400, detail="The default workspace's git connector is cleared "
                                                    "field by field, not removed")
    git_store.remove_workspace(provider, ws)
    notify_change("connector_git", workspace=ws)
    return git_store.public_config(ws)


@router.post("/test")
async def test_connection(payload: GitTestRequest, workspace: Optional[str] = None):
    provider = _check_provider(payload.provider)
    ws = _ws(workspace)
    try:
        return await asyncio.to_thread(get_provider(provider, ws).test_connection)
    except GitProviderError as e:
        return {"ok": False, "login": None, "error": str(e)}


@router.get("/repos")
async def list_repos(provider: str = Query(...), search: Optional[str] = Query(None),
                     workspace: Optional[str] = None):
    _check_provider(provider)
    ws = _ws(workspace)
    try:
        return await asyncio.to_thread(get_provider(provider, ws).list_repos, search)
    except GitProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
