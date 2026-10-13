"""A node's sandbox: a snippet run on a linked machine (docs/hub-link.md).

``node:<id>`` names a node that announced a sandbox (hublink/nodes.py). The
request goes to this hub's own ``/api/node-link/<id>/exec`` route, which
checks that the run's workspace is one the node's owner allowed and hands it
to the node through the mailbox; the node runs it in its own provider
(docker where it has one) and the result comes back as a
:class:`SandboxResult`. The call is made over HTTP rather than in process
because the snippet runs inside an agent's own process, on any host, while
the mailbox is the API's.

``nodes`` is the pool of them (:class:`PoolProvider`): the hub's
``/api/node-link/pool/exec`` picks the least busy node with a sandbox that
may take the run's work (hublink/scheduler.py), so snippets of parallel
runs spread over the nodes.

The network policy travels with the request and the node's provider is held
to it: a node whose own provider cannot fence (``local``) refuses ``none``
and ``limited`` rather than running the snippet open. A host directory
cannot be mounted on another machine, so ``workspace`` is refused here.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Tuple

from sandbox.base import SandboxProvider, SandboxRequest, SandboxResult

log = logging.getLogger(__name__)

PREFIX = "node:"
POOL = "nodes"
REQUEST_TIMEOUT_MARGIN = 45.0


def node_id_of(name: str) -> str:
    return (name or "")[len(PREFIX):] if str(name or "").startswith(PREFIX) else ""


class NodeProvider(SandboxProvider):
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.name = f"{PREFIX}{node_id}"

    def is_available(self) -> Tuple[bool, str]:
        from hublink import nodes
        rec = nodes.get_node(self.node_id)
        if rec is None:
            return False, "no such node"
        if rec.get("disabled"):
            return False, "the node is disabled"
        if not (rec.get("capabilities") or {}).get("sandbox"):
            return False, "the node offers no sandbox"
        if not nodes.is_online(rec):
            return False, "the node is offline"
        return True, ""

    def run(self, request: SandboxRequest) -> SandboxResult:
        return _post(self.name, f"/api/node-link/{self.node_id}/exec", request)


class PoolProvider(SandboxProvider):
    """The pool of node sandboxes: the hub picks a node per snippet."""

    name = POOL

    def is_available(self) -> Tuple[bool, str]:
        from hublink import nodes
        found = nodes.sandbox_availability().get(POOL)
        if not found:
            return False, "no node offers a sandbox"
        return bool(found["available"]), str(found.get("reason") or "")

    def run(self, request: SandboxRequest) -> SandboxResult:
        return _post(self.name, "/api/node-link/pool/exec", request)


def _post(name: str, path: str, request: SandboxRequest) -> SandboxResult:
    """Send a snippet to one of the hub's exec routes."""
    if request.workspace:
        return SandboxResult(exit_code=-1, provider=name, error=(
            "mount_workspace is not available on a node: the workspace's files are on "
            "this hub, the snippet would run on another machine"))
    import os
    import requests
    from common.auth import auth_headers
    from common.hostnet import hub_base_url

    payload = asdict(request)
    payload["workspace_name"] = os.environ.get("AGENT_WORKSPACE") or ""
    try:
        from common.workspace_scope import current_agent
        payload["agent_id"] = current_agent() or ""
    except Exception:  # noqa: BLE001 - outside a run: the workspace's class alone applies
        payload["agent_id"] = ""
    url = f"{hub_base_url()}{path}"
    try:
        resp = requests.post(url, json=payload, headers=auth_headers(),
                             timeout=float(request.timeout or 60) + REQUEST_TIMEOUT_MARGIN)
    except requests.exceptions.RequestException as exc:
        return SandboxResult(exit_code=-1, provider=name,
                             error=f"could not reach the hub's node route: {exc}")
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text[:300]
        return SandboxResult(exit_code=-1, provider=name,
                             error=f"the node refused the snippet: {detail or resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return SandboxResult(exit_code=-1, provider=name, error="the node answered with no result")
    fields = {k: data.get(k) for k in ("exit_code", "stdout", "stderr", "truncated_stdout",
                                         "truncated_stderr", "duration_ms", "timed_out", "error")
              if k in data}
    fields.setdefault("exit_code", -1)
    node = data.get("node")
    return SandboxResult(provider=f"{name}/{node}" if node and name == POOL else name, **fields)


__all__ = ["NodeProvider", "POOL", "PREFIX", "PoolProvider", "node_id_of"]
