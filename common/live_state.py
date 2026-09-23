"""
Periodic publisher for externally-changing state.

Some data changes outside the backend's knowledge — Docker container status,
node heartbeats, log files growing — so there is no mutation point to hook a
``notify_change`` onto. This background task polls those sources *once* on the
server and fans the result out over the single multiplexed SSE stream, but only
for channels that actually have subscribers. The browser therefore never polls:
it just receives snapshots while the relevant page is open.

Channels:
- ``nodes``                  → ``{type: "snapshot", data: [...]}``  (node registry + heartbeats)
- ``containers``             → ``{type: "snapshot", data: [...]}``  (docker ps)
- ``logs:node:<node_id>``    → ``{type: "logs", content: "..."}``
- ``logs:container:<name>``  → ``{type: "logs", content: "..."}``
- ``logs:member:<id>``       → ``{type: "logs", content: "..."}``  (a replica's or worker's own log)
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from common.session_broker import broker

log = logging.getLogger(__name__)

INTERVAL = 3.0  # seconds between snapshot passes

# Last payload published per channel, so we only emit on an actual change and
# don't wake up idle pages every tick.
_last: dict[str, str] = {}


async def _publish_if_changed(channel: str, event: dict) -> None:
    key = json.dumps(event, sort_keys=True, default=str)
    if _last.get(channel) == key:
        return
    _last[channel] = key
    await broker.apublish(channel, event)


def _forget(channel: str) -> None:
    _last.pop(channel, None)


def _node_log(node_id: str) -> Optional[str]:
    from managers import node_manager
    node = node_manager.get_node(node_id)
    if not node:
        return None
    log_file = node.get("log_file")
    if not log_file or not Path(log_file).exists():
        return "(no logs yet)"
    try:
        return Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _member_log(member_id: str) -> Optional[str]:
    from common import members
    try:
        return members.read_log(member_id, tail=400)
    except Exception:  # noqa: BLE001 - a log tail for one open panel must not break the publisher
        log.debug("member log read failed for %s", member_id, exc_info=True)
        return None


def _container_log(name: str) -> Optional[str]:
    from managers import container_manager
    try:
        return container_manager.get_logs(name, tail=200)
    except Exception:  # noqa: BLE001 - a log tail for one open panel must not break the publisher
        log.debug("container log read failed for %s", name, exc_info=True)
        return None


async def _run_blocking(fn, *args):
    """Run a blocking source (docker/file IO) off the event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


async def _publish_snapshots() -> None:
    # Node registry + heartbeats
    if broker.has_subscribers("nodes"):
        from managers import node_manager
        try:
            data = await _run_blocking(node_manager.list_nodes)
            await _publish_if_changed("nodes", {"type": "snapshot", "data": data})
        except Exception:  # noqa: BLE001 - background loop, next tick retries
            log.debug("nodes snapshot failed", exc_info=True)
    else:
        _forget("nodes")

    # Docker containers
    if broker.has_subscribers("containers"):
        from managers import container_manager
        try:
            data = await _run_blocking(container_manager.list_containers)
            await _publish_if_changed("containers", {"type": "snapshot", "data": data})
        except Exception:  # noqa: BLE001 - background loop, next tick retries
            log.debug("containers snapshot failed", exc_info=True)
    else:
        _forget("containers")

    # On-demand log tails for open detail panels
    active_logs = set(broker.active_channels("logs:"))
    for ch in active_logs:
        try:
            _, kind, resource = ch.split(":", 2)
        except ValueError:
            continue
        if kind == "node":
            content = await _run_blocking(_node_log, resource)
        elif kind == "container":
            content = await _run_blocking(_container_log, resource)
        elif kind == "member":
            content = await _run_blocking(_member_log, resource)
        else:
            continue
        if content is not None:
            await _publish_if_changed(ch, {"type": "logs", "content": content})
    # Drop de-dup cache for log channels nobody is watching anymore
    for ch in [c for c in _last if c.startswith("logs:") and c not in active_logs]:
        _forget(ch)


LEASE_ROLE = "publisher"


def _bridged() -> bool:
    """Whether events reach every replica (common/broker_bridge.py). Only then
    can one publisher serve all of them; without the bridge each replica must
    poll for its own subscribers."""
    try:
        from common.config import settings
        return bool((settings.broker_url or "").strip())
    except ImportError:
        return False


async def run_external_publisher() -> None:
    """Background loop — cancel on shutdown.

    With the event bridge on, only the holder of the ``publisher`` lease polls
    Docker and the node registry; the snapshots it publishes reach every
    replica's subscribers through the bridge. Without it, every replica polls
    for its own subscribers, as before.
    """
    from common import leases

    bridged = _bridged()
    while True:
        try:
            if not bridged or await asyncio.to_thread(leases.hold, LEASE_ROLE, INTERVAL * 20):
                await _publish_snapshots()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - background loop, must keep running until cancelled
            log.debug("external publisher tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL)
