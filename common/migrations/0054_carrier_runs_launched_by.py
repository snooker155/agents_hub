"""
0054: chat turns an instance carrier ran are charged to their person.

A carrier has no database: it opens its runs through the hub
(``common.remote_state``), and the hub stamped ``launched_by`` with its own
context, the local operator or the service credential. Every chat turn on an
instance, and every agent it delegated to, then counted for nobody, so the
Users and Account pages showed $0 of spend. Now the API opens an agent turn's
run itself, and the hub reads the person of any other run a turn opens from
its own records (``common.charge``).

The person is still on record: the turn's mailbox message
(``instance_inbox.payload.user_id``, ``key_id``) names them. This sets
``launched_by`` (and ``key_id`` when missing) on each such carrier run from its
message, then on the runs below it (``parent_run_id``), level by level. Runs
whose message names no person keep what they have.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from common.migrations import table_exists

#: What the hub stamped when no person was in its context.
_NOBODY = ("local", "service")


def _load(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _unattributed(extra: Dict[str, Any]) -> bool:
    return str(extra.get("launched_by") or "local") in _NOBODY


def upgrade(conn: Any, dialect: str) -> None:
    for table in ("runs", "instance_inbox", "users"):
        if not table_exists(conn, dialect, table):
            return
    people = {str(r[0]) for r in conn.execute("SELECT user_id FROM users").fetchall()}
    if not people:
        return
    rows = conn.execute("SELECT run_id, parent_run_id, extra FROM runs").fetchall()
    runs: Dict[str, Tuple[Optional[str], Dict[str, Any]]] = {
        str(r[0]): (str(r[1]) if r[1] else None, _load(r[2])) for r in rows}

    # Who each turn's message was for.
    charged: Dict[str, Tuple[str, Optional[str]]] = {}
    for run_id, (_parent, extra) in runs.items():
        msg_id = extra.get("inbox_msg_id")
        if not extra.get("carrier_run") or not msg_id or not _unattributed(extra):
            continue
        msg = conn.execute("SELECT payload FROM instance_inbox WHERE msg_id = ?",
                           (str(msg_id),)).fetchone()
        payload = _load(msg[0]) if msg is not None else {}
        user_id = str(payload.get("user_id") or "")
        if user_id in people:
            charged[run_id] = (user_id, str(payload.get("key_id") or "") or None)

    # The agents those turns delegated to, and theirs in turn.
    frontier = set(charged)
    while frontier:
        found = set()
        for run_id, (parent, extra) in runs.items():
            if parent in frontier and run_id not in charged and _unattributed(extra):
                charged[run_id] = charged[parent]
                found.add(run_id)
        frontier = found

    for run_id, (user_id, key_id) in charged.items():
        extra = runs[run_id][1]
        extra["launched_by"] = user_id
        if key_id and not extra.get("key_id"):
            extra["key_id"] = key_id
        conn.execute("UPDATE runs SET extra = ? WHERE run_id = ?", (json.dumps(extra), run_id))
