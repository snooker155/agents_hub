"""
The background side of the link: the outbox drainer and the node loop,
started by the backend's lifespan and refreshed when the link changes.

Both are daemon threads and both read the link record on every pass, so a
change made on the Settings page takes effect without a restart. The
drainer holds a lease (``common/leases.py``) so one replica of a
multi-replica hub sends; the node loop runs on every replica that has the
record, which is harmless (each poll hands out distinct jobs) and keeps a
node answering while one replica restarts.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from hublink import store
from hublink.node_client import NodeClient

log = logging.getLogger(__name__)

DRAIN_EVERY_SECONDS = 5.0
LEASE_ROLE = "hublink-mirror"

node_client = NodeClient()
_drainer: Optional[threading.Thread] = None
_stop = threading.Event()
_state: Dict[str, Any] = {"drainer": False, "last_drain": None, "last_result": {}}


def _drain_loop() -> None:
    from common import leases
    from hublink import mirror
    while not _stop.is_set():
        try:
            doc = store.get()
            if doc and any((doc.get("mirror") or {}).values()) and leases.hold(LEASE_ROLE, 30.0):
                result = mirror.drain()
                _state["last_drain"] = time.time()
                _state["last_result"] = result
        except Exception:  # noqa: BLE001 - the loop must survive a bad pass
            log.debug("hub link drainer failed", exc_info=True)
        _stop.wait(DRAIN_EVERY_SECONDS)


def start() -> None:
    """Start what the link record asks for. Safe to call again."""
    global _drainer
    doc = store.get()
    if doc is None:
        return
    if _drainer is None or not _drainer.is_alive():
        _stop.clear()
        _drainer = threading.Thread(target=_drain_loop, name="hublink-drain", daemon=True)
        _drainer.start()
        _state["drainer"] = True
    if store.node_enabled(doc):
        node_client.start()


def stop() -> None:
    _stop.set()
    node_client.stop()
    _state["drainer"] = False


def refresh() -> None:
    """After the link record changed: start or stop the parts accordingly."""
    doc = store.get()
    if doc is None:
        stop()
        return
    start()
    if not store.node_enabled(doc):
        node_client.stop()


def hello_now() -> bool:
    return node_client.hello_now()


def status() -> Dict[str, Any]:
    return {"drainer": bool(_state.get("drainer")) and _drainer is not None and _drainer.is_alive(),
            "last_drain_at": _state.get("last_drain"), "last_drain": _state.get("last_result"),
            "node": dict(node_client.state)}


__all__ = ["DRAIN_EVERY_SECONDS", "hello_now", "node_client", "refresh", "start", "status", "stop"]
