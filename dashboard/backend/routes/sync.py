"""
Sync between hubs (hubsync/, docs/hub-sync.md): both ends' routes.

``/api/sync`` is the vault, on the hub people sync TO. Every route acts for
the caller alone: the owner of every record is the principal (a personal API
key from the client hub, or the person's own session in the browser), so one
person never reads, overwrites or even counts another's records. Writing
into a space needs editor rights on the workspace of that name, reading it
needs only sight of it.

``/api/hublink/sync`` is the client, on a person's own hub: its settings,
the key, a pass now, the conflicts. The status is readable by anyone signed
in (the header shows it to everyone, since everyone's data is what travels),
but only an administrator sees all of it: anyone else gets the mappings and
conflicts of workspaces they can see, and whether the last pass failed
rather than its error text, which names workspaces and record ids. Changing
anything is the administrator's.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/sync", tags=["sync"])
client_router = APIRouter(prefix="/api/hublink/sync", tags=["sync"])


def _owner(request: Request) -> str:
    from common import identity
    principal = identity.request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal.id


def _space_access(request: Request, space: str, *, write: bool) -> None:
    from common import access, identity
    from common.auth import WS_EDITOR, WS_VIEWER
    from workspace.storage import get_workspace_metadata
    principal = identity.request_principal(request)
    if not get_workspace_metadata(space):
        raise HTTPException(status_code=404, detail=f"no workspace '{space}' on this hub")
    access.require_visible(principal, space)
    identity.require_role(principal, workspace=space, role=WS_EDITOR if write else WS_VIEWER)


def _vault_call(fn, *args, **kwargs):
    from hubsync.vault import VaultError
    try:
        return fn(*args, **kwargs)
    except VaultError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


# ── the vault ────────────────────────────────────────────────────────────────

class KeyBody(BaseModel):
    key_id: str
    kdf: str = "PBKDF2-SHA256"
    salt: str
    iterations: int
    verifier: str


class SpaceBody(BaseModel):
    encrypted: bool = False
    key_id: Optional[str] = None
    origin: str = ""


class PushBody(BaseModel):
    records: List[Dict[str, Any]] = Field(default_factory=list)


@router.get("/keys")
async def list_keys(request: Request):
    from hubsync import vault
    return {"keys": vault.list_keys(_owner(request))}


@router.put("/keys")
async def put_key(body: KeyBody, request: Request):
    from hubsync import vault
    return _vault_call(vault.put_key, _owner(request), key_id=body.key_id, kdf=body.kdf, salt=body.salt,
                       iterations=body.iterations, verifier=body.verifier)


@router.delete("/keys/{key_id}")
async def delete_key(key_id: str, request: Request):
    """The caller's own key, and only while nothing is sealed with it (409
    otherwise): keys are kept per owner, so nobody reaches another's."""
    from hubsync import vault
    owner = _owner(request)
    if vault.get_key(owner, key_id) is None:
        raise HTTPException(status_code=404, detail="no such key")
    _vault_call(vault.delete_key, owner, key_id)
    return {"deleted": True}


@router.get("/spaces")
async def list_spaces(request: Request):
    from hubsync import vault
    return {"spaces": vault.list_spaces(_owner(request))}


@router.put("/spaces/{space}")
async def put_space(space: str, body: SpaceBody, request: Request):
    from hubsync import vault
    _space_access(request, space, write=True)
    owner = _owner(request)
    if body.encrypted and not vault.get_key(owner, body.key_id or ""):
        raise HTTPException(status_code=409, detail="register the key before an encrypted space")
    return _vault_call(vault.ensure_space, owner, space, encrypted=body.encrypted, key_id=body.key_id,
                       origin=body.origin)


@router.delete("/spaces/{space}")
async def delete_space(space: str, request: Request):
    from hubsync import vault
    _space_access(request, space, write=True)
    return {"deleted": vault.delete_space(_owner(request), space)}


@router.post("/spaces/{space}/push")
async def push(space: str, body: PushBody, request: Request):
    from hubsync import materialize, vault
    _space_access(request, space, write=True)
    owner = _owner(request)
    results = await asyncio.to_thread(_vault_call, vault.push, owner, space, body.records)
    row = vault.get_space(owner, space) or {}
    if not row.get("encrypted"):
        materialize.wake()
    return {"results": results}


@router.get("/spaces/{space}/changes")
async def changes(space: str, request: Request, since: int = 0, limit: int = 200):
    from hubsync import vault
    _space_access(request, space, write=False)
    return await asyncio.to_thread(vault.changes, _owner(request), space, since=since, limit=limit)


@router.get("/spaces/{space}/records")
async def list_records(space: str, request: Request, kind: Optional[str] = None):
    from hubsync import vault
    _space_access(request, space, write=False)
    return {"records": vault.list_records(_owner(request), space, kind=kind)}


@router.get("/spaces/{space}/records/{kind}/{record_id}")
async def get_record(space: str, kind: str, record_id: str, request: Request):
    from hubsync import vault
    _space_access(request, space, write=False)
    rec = vault.get_record(_owner(request), space, kind, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="no such record")
    return rec


@router.get("/here")
async def here(request: Request, workspace: str):
    """What the caller synced into this workspace: the banner's source, and
    for an encrypted space the public half of the key, so the browser can
    check a passphrase before opening anything."""
    from hubsync import vault
    _space_access(request, workspace, write=False)
    owner = _owner(request)
    space = vault.get_space(owner, workspace)
    if space is None:
        return {"synced": False}
    space["counts"] = vault.counts(owner, workspace)
    key = vault.get_key(owner, space.get("key_id") or "") if space.get("encrypted") else None
    return {"synced": True, "space": space, "key": key}


# ── the client ───────────────────────────────────────────────────────────────

def _admin(request: Request) -> None:
    from common import identity
    identity.require_role(identity.request_principal(request), admin=True)


def _is_admin(request: Request) -> bool:
    try:
        _admin(request)
    except HTTPException:
        return False
    return True


def _visible_spaces(request: Request, mappings: List[Dict[str, Any]]) -> set:
    """The remote spaces whose local workspace the caller can see."""
    from common import access, identity
    principal = identity.request_principal(request)
    return {m.get("space") for m in mappings
            if isinstance(m, dict) and m.get("workspace") and access.can_see_workspace(principal, m["workspace"])}


def _trimmed_status(request: Request, status: Dict[str, Any]) -> Dict[str, Any]:
    """The status as a non-administrator may see it (the assistant's lookup
    trims it the same way, chat/lookup_kinds/catalog.py)."""
    from hubsync import state
    if not status.get("linked") or _is_admin(request):
        return status
    spaces = _visible_spaces(request, status.get("mappings") or [])
    mappings = [m for m in status.get("mappings") or [] if isinstance(m, dict) and m.get("space") in spaces]
    result = {k: v for k, v in (status.get("last_result") or {}).items()
              if isinstance(v, (int, float)) and not isinstance(v, bool)}
    errors = (status.get("last_result") or {}).get("errors") or []
    if errors:
        result["error_count"] = len(errors)
    try:
        conflicts = sum(1 for c in state.conflicts("") if c.get("space") in spaces)
    except Exception:  # noqa: BLE001 - the same tolerance as client.status
        conflicts = 0
    return {**status, "mappings": mappings, "last_result": result, "last_error": "",
            "has_error": bool(status.get("last_error") or errors), "conflicts": conflicts, "actor": ""}


def _client_call(fn, *args, **kwargs):
    from hublink.remote import RemoteError
    from hubsync.client import SyncConfigError
    from hubsync.crypto import CryptoError
    try:
        return fn(*args, **kwargs)
    except SyncConfigError as exc:
        # Mostly a sentence; a key that already exists answers an object the
        # Settings page reads (hubsync/client.py, KeyExistsError).
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    except RemoteError as exc:
        raise HTTPException(status_code=502, detail=f"the linked hub answered: {exc}")
    except CryptoError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ClientBody(BaseModel):
    enabled: bool
    # The value for a mapping that carries no ``encrypted`` of its own.
    encrypted: bool = False
    # Every workspace sealed, default and the others, also later ones; None keeps it.
    encrypt_all: Optional[bool] = None
    mappings: List[Dict[str, Any]] = Field(default_factory=list)


class WorkspaceSyncBody(BaseModel):
    synced: bool
    space: Optional[str] = None
    encrypted: bool = False


class ClientKeyBody(BaseModel):
    passphrase: Optional[str] = None
    generate: bool = False
    # With generate: replace the account's key on the remote when no record
    # is sealed with it (a generated passphrase that was never written down).
    # Offered after a 409 whose detail is {"code": "key_exists",
    # "replace_unused": true}; any other 409 detail stays a sentence.
    replace_unused: bool = False


@client_router.get("")
async def client_status(request: Request):
    from hubsync import client
    _owner(request)
    return _trimmed_status(request, client.status())


@client_router.put("")
async def client_configure(body: ClientBody, request: Request):
    from hubsync import client
    _admin(request)
    await asyncio.to_thread(_client_call, client.configure, enabled=body.enabled, mappings=body.mappings,
                            encrypted=body.encrypted, encrypt_all=body.encrypt_all)
    return client.status()


@client_router.put("/workspaces/{workspace}")
async def client_configure_workspace(workspace: str, body: WorkspaceSyncBody, request: Request):
    """One workspace's mapping, from that workspace's Settings tab."""
    from hubsync import client
    _admin(request)
    await asyncio.to_thread(_client_call, client.set_workspace, workspace, synced=body.synced,
                            space=body.space, encrypted=body.encrypted)
    return client.status()


@client_router.post("/key")
async def client_key(body: ClientKeyBody, request: Request):
    from hubsync import client
    _admin(request)
    out = await asyncio.to_thread(_client_call, client.setup_key, passphrase=body.passphrase,
                                  generate=body.generate, replace_unused=body.replace_unused)
    return {**client.status(), "generated_passphrase": out.get("generated_passphrase") or ""}


@client_router.delete("/key")
async def client_forget_key(request: Request):
    from hubsync import client
    _admin(request)
    _client_call(client.forget_key)
    return client.status()


@client_router.post("/run")
async def client_run(request: Request):
    from hubsync import client
    _admin(request)
    result = await asyncio.to_thread(_client_call, client.run_pass)
    return {**client.status(), "result": result}


@client_router.get("/conflicts")
async def client_conflicts(request: Request):
    from hubsync import client, state
    _owner(request)
    rows = state.conflicts("")
    if not _is_admin(request):
        spaces = _visible_spaces(request, client.config().get("mappings") or [])
        rows = [c for c in rows if c.get("space") in spaces]
    return {"conflicts": rows}


@client_router.get("/conflicts/{conflict_id}")
async def client_conflict(conflict_id: int, request: Request):
    from hubsync import state
    _admin(request)
    rec = state.conflict_doc(conflict_id)
    if rec is None or rec.get("owner_user") != "":
        raise HTTPException(status_code=404, detail="no such conflict")
    return rec


@client_router.post("/conflicts/{conflict_id}/resolve")
async def client_resolve(conflict_id: int, request: Request):
    from hubsync import state
    _admin(request)
    state.resolve_conflict(conflict_id)
    return {"ok": True}
