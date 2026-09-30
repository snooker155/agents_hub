"""
Version history for skills (fourth-cycle stage 3).

A skill (``memory.procedural.Procedure``) changes in many places: the Skills
page, an agent's ``create_skill``, an install that copies it, a sync from a
project's ``.claude/skills`` folder. :func:`record_if_changed` is the hook the
procedure store calls on every ``add`` and ``update``: it hashes the content
fields (:data:`CONTENT_FIELDS`) and writes a row to ``skill_versions``
(``common/migrations/0022_skill_versions.py``) only when the hash differs
from the latest row, so the use-count bump ``get_skill`` does on every call
never turns into a version. This module is the only reader and writer of
that table.

An agent can be pinned to one version of an attached skill
(``Procedure.pinned_version``); :func:`effective_content` is what the prompt
catalog and ``get_skill`` read, so a pinned agent keeps seeing the text it
was tested with while the skill itself moves on.

Unlike memory versions, a failure here is not swallowed inside the store
write: a skill whose history cannot be written is not saved either, since a
version number the agent is pinned to must never be missing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db

log = logging.getLogger(__name__)

#: The fields a version captures. Everything else on a skill (use count,
#: sharing, attachment, pin) is bookkeeping, not content.
CONTENT_FIELDS = ("name", "description", "steps", "body", "tags", "resources", "allowed_tools")

OP_CREATE = "create"
OP_UPDATE = "update"
OP_RESTORE = "restore"
OP_IMPORT = "import"
OP_SYNC = "sync"
OP_INSTALL = "install"
OP_ORIGIN = "origin"

OPS = (OP_CREATE, OP_UPDATE, OP_RESTORE, OP_IMPORT, OP_SYNC, OP_INSTALL, OP_ORIGIN)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_of(procedure: Any) -> Dict[str, Any]:
    """The content fields of a skill as plain JSON values."""
    if isinstance(procedure, dict):
        source = procedure
    else:
        source = procedure.model_dump(mode="json")
    out: Dict[str, Any] = {}
    for key in CONTENT_FIELDS:
        value = source.get(key)
        if key in ("steps", "tags", "resources", "allowed_tools"):
            value = [str(v) for v in (value or [])]
        else:
            value = str(value or "")
        out[key] = value
    return out


def content_hash(content: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _resolve_actor() -> Dict[str, Optional[str]]:
    """Who is changing the skill: an agent in a run, a signed-in user, or the
    system (a sync on project pull, a migration). Same rules as
    ``memory.versions._resolve_actor``."""
    agent_id = None
    try:
        from common.agent_context import current_agent_id
        agent_id = current_agent_id.get()
    except Exception:  # noqa: BLE001 - actor resolution must not block a skill save
        agent_id = None
    if not agent_id:
        agent_id = os.environ.get("AGENT_ID") or None
    if agent_id:
        return {"actor_kind": "agent", "actor_id": str(agent_id)}
    try:
        from common.identity import current_user_id
        user_id = current_user_id()
    except Exception:  # noqa: BLE001 - actor resolution must not block a skill save
        user_id = None
    if user_id:
        return {"actor_kind": "user", "actor_id": str(user_id)}
    return {"actor_kind": "system", "actor_id": None}


def _row(row: Any, *, with_snapshot: bool = True) -> Dict[str, Any]:
    out = {
        "skill_id": row["skill_id"],
        "version": int(row["version"]),
        "op": row["op"],
        "content_hash": row["content_hash"],
        "actor_kind": row["actor_kind"],
        "actor_id": row["actor_id"],
        "note": row["note"] or "",
        "at": row["at"],
    }
    if with_snapshot:
        try:
            out["snapshot"] = json.loads(row["snapshot"])
        except (TypeError, ValueError):
            out["snapshot"] = {}
    return out


def latest(skill_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY version DESC LIMIT 1",
        (str(skill_id),)).fetchone()
    return _row(row) if row else None


def get_version(skill_id: str, version: int) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM skill_versions WHERE skill_id = ? AND version = ?",
        (str(skill_id), int(version))).fetchone()
    return _row(row) if row else None


def list_versions(skill_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
    """Newest first, without snapshots (read one with :func:`get_version`)."""
    rows = db.get_conn().execute(
        "SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY version DESC LIMIT ?",
        (str(skill_id), max(1, int(limit)))).fetchall()
    return [_row(r, with_snapshot=False) for r in rows]


def record_if_changed(procedure: Any, *, op: Optional[str] = None,
                      note: str = "") -> Optional[int]:
    """Write a version row when the skill's content differs from its latest
    version. Returns the new version number, or None when nothing changed.

    ``op`` defaults to ``create`` for a skill with no history and ``update``
    otherwise. The caller stamps the returned number on the skill
    (``Procedure.version``) before it saves the skill's record, inside the
    same transaction.
    """
    skill_id = str(procedure.id)
    content = content_of(procedure)
    digest = content_hash(content)
    last = latest(skill_id)
    if last is not None and last["content_hash"] == digest:
        return None
    version = (last["version"] + 1) if last else 1
    op = op if op in OPS else (OP_CREATE if last is None else OP_UPDATE)
    actor = _resolve_actor()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO skill_versions (skill_id, version, op, snapshot, content_hash, "
            "actor_kind, actor_id, note, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (skill_id, version, op, json.dumps(content, ensure_ascii=False), digest,
             actor["actor_kind"], actor["actor_id"], (note or "")[:500], _now()))
    return version


def delete_history(skill_id: str) -> int:
    """Drop every version of a deleted skill."""
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM skill_versions WHERE skill_id = ?", (str(skill_id),))
    return int(cursor.rowcount or 0)


def effective_content(procedure: Any) -> Dict[str, Any]:
    """What an agent sees of this skill: its pinned version's content when it
    is pinned and that version exists, its current content otherwise. Carries
    ``version`` (the number actually served) and ``pinned`` alongside."""
    pinned = getattr(procedure, "pinned_version", None)
    if pinned:
        row = get_version(str(procedure.id), int(pinned))
        if row is not None:
            return {**content_of(row["snapshot"]), "version": row["version"], "pinned": True}
        log.warning("skill %s is pinned to missing version %s; serving current",
                    procedure.id, pinned)
    return {**content_of(procedure), "version": getattr(procedure, "version", None) or None,
            "pinned": False}


__all__ = [
    "CONTENT_FIELDS", "OPS", "OP_CREATE", "OP_IMPORT", "OP_INSTALL", "OP_ORIGIN",
    "OP_RESTORE", "OP_SYNC", "OP_UPDATE", "content_hash", "content_of", "delete_history",
    "effective_content", "get_version", "latest", "list_versions", "record_if_changed",
]
