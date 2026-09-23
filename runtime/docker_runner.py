"""Docker execution helpers — thin shim over container_manager.

This module is the public interface used by node_manager and run_manager.
All heavy lifting (image resolution, network management, path translation)
lives in ``managers/container_manager.py``.

Public API
----------
start_node_container(node_id, agent_id, inner_cmd, workspace, env) -> dict
start_run_container(run_id, agent_id, inner_cmd, cwd, env)         -> dict
stop_container(name)                                                -> bool
container_running(name)                                             -> bool
container_name_for_node(node_id)                                    -> str
container_name_for_run(run_id)                                      -> str
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from managers.container_manager import (
    container_name_for_node,
    container_name_for_run,
    container_running,
    start_container,
    stop_container,
)

__all__ = [
    "start_node_container",
    "start_run_container",
    "stop_container",
    "container_running",
    "container_name_for_node",
    "container_name_for_run",
]


def start_node_container(
    node_id: str,
    agent_id: str,
    inner_cmd: List[str],
    workspace: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    http_expose: bool = False,
    http_port: int = 8080,
    http_host_port: Optional[int] = None,
) -> Dict[str, Any]:
    """Start a detached container for a persistent agent node.

    The node reads the registries from a snapshot too (common/snapshot.py),
    written under ``node-<id>``; a registry change reaches it on restart."""
    from common import snapshot
    name = container_name_for_node(node_id)
    return start_container(
        container_name=name,
        agent_id=agent_id,
        cmd=inner_cmd,
        workspace=workspace,
        env=env,
        http_expose=http_expose,
        http_port=http_port,
        http_host_port=http_host_port,
        snapshot_dir=str(snapshot.write_snapshots(f"node-{node_id}")),
    )


def start_run_container(
    run_id: str,
    agent_id: str,
    inner_cmd: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Start a detached, sandboxed container for a one-shot agent run.

    Unlike a node container (long-lived, keeps today's permissive mounts), a
    run container gets the hardened profile: resource limits, a read-only
    root filesystem, a scrubbed environment, and the registry snapshot
    (agents, custom providers, model catalog: common/snapshot.py) mounted
    read-only. See managers.container_manager.build_run_command and
    docs/containers.md.
    """
    from common import snapshot
    name = container_name_for_run(run_id)
    return start_container(
        container_name=name,
        agent_id=agent_id,
        cmd=inner_cmd,
        workspace=cwd,
        env=env,
        hardened=True,
        snapshot_dir=str(snapshot.write_snapshots(run_id)),
    )
