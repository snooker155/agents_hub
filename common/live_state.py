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
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from common.session_broker import broker

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
    except Exception:
        return None


def _container_log(name: str) -> Optional[str]:
    from managers import container_manager
    try:
        return container_manager.get_logs(name, tail=200)
    except Exception:
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
        except Exception:
            pass
    else:
        _forget("nodes")

    # Docker containers
    if broker.has_subscribers("containers"):
        from managers import container_manager
        try:
            data = await _run_blocking(container_manager.list_containers)
            await _publish_if_changed("containers", {"type": "snapshot", "data": data})
        except Exception:
            pass
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
        else:
            continue
        if content is not None:
            await _publish_if_changed(ch, {"type": "logs", "content": content})
    # Drop de-dup cache for log channels nobody is watching anymore
    for ch in [c for c in _last if c.startswith("logs:") and c not in active_logs]:
        _forget(ch)


async def run_external_publisher() -> None:
    """Background loop — cancel on shutdown."""
    while True:
        try:
            await _publish_snapshots()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(INTERVAL)
