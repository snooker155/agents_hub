"""
The audit trail API: GET /api/audit, /api/audit/actions, /api/audit/export.

All three answer 404 in ``single`` mode, the same way the rest of stage 3
does: with one operator there is nobody to be audited for, so the trail does
not exist rather than being empty.

Read access is not the same for everyone. An administrator (and, since the
shared token and the local operator both carry an admin role, ``token`` mode
too) reads every row. Anybody else reads only what concerns them: the rows of
a workspace they own, plus every row where they are the actor, so a member can
always see their own history even outside a workspace they own. That is two
queries against :func:`common.audit.query` merged and re-sorted here rather
than a change to that function's SQL, which keeps the permission rule in the
one place that already knows what an "owner" is.

The merge trades an exact global ``total`` and a fully exact deep offset for
simplicity: each underlying query is capped at :data:`common.audit`'s own
5000-row ceiling, so a non-admin reader paging past that many matching rows
in one go can see the page run short. Nobody using the Audit page manually
hits that; a script that needs every row uses the export endpoint instead,
which pages through the full result rather than one window of it.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, Iterable, Iterator, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from common import audit, identity
from common.auth import SINGLE, WS_OWNER

router = APIRouter(tags=["audit"])

#: Column order for the export formats. ``details`` is serialised to a JSON
#: string in both, so a spreadsheet opening the CSV gets one cell, not a
#: ragged row per key.
_COLUMNS = (
    "id", "at", "actor_id", "actor_kind", "actor_name", "action",
    "object_type", "object_id", "workspace", "ip", "method", "path",
    "result", "details",
)

#: A page of the underlying query, and the export's own chunk size
#: (docs/audit.md, "Export"): 1000 rows at a time, up to this many total.
_EXPORT_CHUNK = 1000
_EXPORT_MAX_ROWS = 100_000


def _require_enabled() -> None:
    if identity.current_mode() == SINGLE:
        raise HTTPException(status_code=404,
                            detail="The audit trail needs AUTH_MODE=token or multi")


def _principal(request: Request):
    principal = identity.request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


def _owned_workspaces(principal) -> List[str]:
    """Every workspace this user owns, for the non-admin read scope."""
    return [w for w in identity.workspaces_for_user(principal.id)
            if identity.membership_role(w, principal.id) == WS_OWNER]


def _filters(actor: Optional[str], action: Optional[str], workspace: Optional[str],
            object_type: Optional[str], object_id: Optional[str], since: Optional[str],
            until: Optional[str], text: Optional[str], result: Optional[str]) -> Dict[str, Any]:
    return {
        "actor": actor, "action": action, "workspace": workspace,
        "object_type": object_type, "object_id": object_id, "since": since,
        "until": until, "text": text, "result": result,
    }


def _merge_pages(*pages: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rows from several :func:`audit.query` results, deduplicated by id and
    newest first: a row that is both in the owner's workspace and by the
    reader themselves must not appear twice."""
    merged: Dict[Any, Dict[str, Any]] = {}
    for page in pages:
        for row in page.get("items", []):
            merged[row["id"]] = row
    return sorted(merged.values(), key=lambda r: r["id"], reverse=True)


def _accessible_page(principal, filters: Dict[str, Any], limit: int, offset: int) -> Dict[str, Any]:
    """One page of rows this principal may read.

    An admin (or the token / local principal, both admin by construction)
    reads straight off :func:`audit.query`. Everyone else gets the merge
    described in the module docstring, re-paged in Python.
    """
    if principal.is_admin:
        return audit.query(limit=limit, offset=offset, **filters)
    owned = _owned_workspaces(principal)
    # The reader's own scope IS the actor filter for the second query, so an
    # ``actor`` the caller also passed in (an admin-shaped filter this route
    # tolerates from anyone) is dropped from the two scoped queries and
    # applied afterwards instead, on the merged result.
    wanted_actor = filters.get("actor")
    scoped = {k: v for k, v in filters.items() if k != "actor"}
    fetch = min(offset + limit, 5000)
    by_workspace = (audit.query(workspaces=owned, limit=fetch, offset=0, **scoped)
                    if owned else {"items": [], "total": 0})
    by_actor = audit.query(actor=principal.id, limit=fetch, offset=0, **scoped)
    ordered = _merge_pages(by_workspace, by_actor)
    if wanted_actor:
        ordered = [r for r in ordered
                  if r.get("actor_id") == wanted_actor or r.get("actor_name") == wanted_actor]
    # Not an exact count of every matching row past the fetch window: see the
    # module docstring. Good enough for "are there more pages", which is all
    # the Audit page's pager needs it for.
    total = int(by_workspace.get("total", 0)) + int(by_actor.get("total", 0))
    return {"items": ordered[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset}


def _query_all(**filters: Any) -> Iterator[Dict[str, Any]]:
    """Every row matching ``filters``, paging past ``audit.query``'s own
    5000-row ceiling. For the admin export path."""
    offset = 0
    while offset < _EXPORT_MAX_ROWS:
        page = audit.query(limit=min(_EXPORT_CHUNK, _EXPORT_MAX_ROWS - offset),
                           offset=offset, **filters)
        items = page.get("items", [])
        if not items:
            return
        yield from items
        if len(items) < _EXPORT_CHUNK:
            return
        offset += _EXPORT_CHUNK


def _export_rows(principal, filters: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Every row this principal may read, for the export.

    An admin pages straight through, in order, up to the cap. A non-admin's
    scope is a merge of two queries (see the module docstring), so it has to
    be gathered whole and sorted before it can be paged at all; the cap keeps
    that bounded at 100000 rows the same as the admin path.
    """
    if principal.is_admin:
        yield from _query_all(**filters)
        return
    owned = _owned_workspaces(principal)
    wanted_actor = filters.get("actor")
    scoped = {k: v for k, v in filters.items() if k != "actor"}
    merged: Dict[Any, Dict[str, Any]] = {}
    if owned:
        for row in _query_all(workspaces=owned, **scoped):
            merged[row["id"]] = row
    for row in _query_all(actor=principal.id, **scoped):
        merged[row["id"]] = row
    ordered = sorted(merged.values(), key=lambda r: r["id"], reverse=True)
    if wanted_actor:
        ordered = [r for r in ordered
                  if r.get("actor_id") == wanted_actor or r.get("actor_name") == wanted_actor]
    yield from ordered[:_EXPORT_MAX_ROWS]


def _csv_stream(rows: Iterable[Dict[str, Any]]) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue()
    for row in rows:
        buf.seek(0)
        buf.truncate(0)
        out = dict(row)
        out["details"] = json.dumps(out.get("details") or {}, ensure_ascii=False, default=str)
        writer.writerow(out)
        yield buf.getvalue()


def _jsonl_stream(rows: Iterable[Dict[str, Any]]) -> Iterator[str]:
    for row in rows:
        yield json.dumps(row, ensure_ascii=False, default=str) + "\n"


@router.get("/api/audit")
async def get_audit(
    request: Request,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    workspace: Optional[str] = None,
    object_type: Optional[str] = None,
    object_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    text: Optional[str] = None,
    result: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """A page of audit rows this caller may read, newest first."""
    _require_enabled()
    principal = _principal(request)
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    filters = _filters(actor, action, workspace, object_type, object_id, since, until, text, result)
    return _accessible_page(principal, filters, limit, offset)


@router.get("/api/audit/actions")
async def get_audit_actions(request: Request) -> List[str]:
    """Every distinct action recorded so far, for the filter dropdown."""
    _require_enabled()
    _principal(request)
    return audit.actions()


@router.get("/api/audit/export")
async def export_audit(
    request: Request,
    format: str = "csv",
    actor: Optional[str] = None,
    action: Optional[str] = None,
    workspace: Optional[str] = None,
    object_type: Optional[str] = None,
    object_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    text: Optional[str] = None,
    result: Optional[str] = None,
):
    """Every accessible row matching the filters, streamed as csv or jsonl.

    Up to 100000 rows, fetched in chunks of 1000 so the whole export is never
    held in memory at once on the admin path (a non-admin's merge needs the
    whole scope in memory regardless, to sort and dedupe it; see
    :func:`_export_rows`).
    """
    _require_enabled()
    principal = _principal(request)
    fmt = (format or "csv").strip().lower()
    if fmt not in ("csv", "jsonl"):
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'jsonl'")
    filters = _filters(actor, action, workspace, object_type, object_id, since, until, text, result)
    rows = _export_rows(principal, filters)
    if fmt == "csv":
        stream, media_type = _csv_stream(rows), "text/csv"
    else:
        stream, media_type = _jsonl_stream(rows), "application/x-ndjson"
    headers = {"Content-Disposition": f'attachment; filename="audit-export.{fmt}"'}
    return StreamingResponse(stream, media_type=media_type, headers=headers)
