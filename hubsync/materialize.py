"""
The remote's half of a plain sync: records in the vault become real pages here.

A person who syncs a workspace without end-to-end encryption expects the
remote to be a hub like their own: the chat on its Chat page, the agent
runnable there, an edit made on the remote's site travelling back to their
laptop. So for every plain space this hub runs the same engine pass a client
runs (hubsync/engine.py), against its own vault (LocalTransport), writing
into the workspace of that name as the person who owns the records. Its
pushes come back to the clients as ordinary changes.

An encrypted space is never materialized: this hub cannot read it, and the
point is that it cannot. Its records are listed as encrypted in the
workspace and open only in the person's browser (hubsync/crypto.py).

Runs on one replica at a time (a lease), every few seconds and right after a
push, so a synced chat shows up on the remote within moments.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from common.docstore import DocStore
from hubsync import engine

log = logging.getLogger(__name__)

PASS_SECONDS = 15.0
LEASE_ROLE = "hubsync-materializer"
_cursors = DocStore("hubsync_cursors")


def _key(owner: str, space: str) -> str:
    return f"{owner}\x1f{space}"


def plain_spaces() -> List[Dict[str, Any]]:
    from common import db
    return [dict(r) for r in db.get_conn().execute(
        "SELECT owner_user, space FROM sync_spaces WHERE encrypted = 0").fetchall()]


def materialize(owner: str, space: str) -> Dict[str, Any]:
    from common import identity
    cursor = int((_cursors.get(_key(owner, space)) or {}).get("seq") or 0)
    spec = engine.Spec(workspace=space, space=space, transport=engine.LocalTransport(owner, space),
                       state_owner=owner, actor=owner, key=None, cursor=cursor)
    # Stores that stamp the current user (chats, tasks) stamp the record's owner.
    token = identity.set_current_user(owner)
    try:
        result = engine.run(spec)
    finally:
        identity.reset_current_user(token)
    _cursors.put(_key(owner, space), {"seq": result.cursor})
    return result.as_dict()


def run_all() -> List[Dict[str, Any]]:
    out = []
    for row in plain_spaces():
        try:
            out.append({"owner": row["owner_user"], "space": row["space"],
                        **materialize(row["owner_user"], row["space"])})
        except Exception as exc:  # noqa: BLE001 - one space failing must not stop the rest
            log.warning("sync: materializing %s for %s failed", row["space"], row["owner_user"], exc_info=True)
            out.append({"owner": row["owner_user"], "space": row["space"], "error": str(exc)})
    return out


_wake_event = threading.Event()
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def wake() -> None:
    _wake_event.set()


def _loop() -> None:
    from common import leases
    while not _stop_event.is_set():
        _wake_event.wait(PASS_SECONDS)
        _wake_event.clear()
        if _stop_event.is_set():
            break
        try:
            if plain_spaces() and leases.hold(LEASE_ROLE, 60):
                run_all()
        except Exception:  # noqa: BLE001 - the loop outlives any one pass
            log.warning("sync: materializer pass failed", exc_info=True)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="hubsync-materializer", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()
    _wake_event.set()


__all__ = ["materialize", "plain_spaces", "run_all", "start", "stop", "wake"]
