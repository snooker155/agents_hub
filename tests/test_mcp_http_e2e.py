"""End to end against an MCP server reached over HTTP, not spawned over stdio.

``tests/test_mcp_client.py`` already proves the hub's path (connect, rename,
allowlist, a sync call from a thread with no event loop) against a real server
it starts itself over stdio. Everything in ``mcp_client`` that is specific to
the HTTP transports (the URL, the headers, no environment at all) was covered
only by mocks until now. This starts the same echo server as a separate
process listening on a loopback port and goes through the hub's own
``streamable_http`` configuration to reach it.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_http_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server():
    """The echo server on a free port, torn down after the test."""
    pytest.importorskip("mcp.server.fastmcp")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURE_SERVER), str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if line.strip() == "READY":
                ready = True
                break
        if not ready:
            pytest.skip(f"MCP HTTP fixture did not start (exit {proc.poll()})")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.fixture
def workspace(monkeypatch):
    """A workspace whose metadata lives in memory, the same stand-in
    ``tests/test_mcp_client.py`` uses: the store under test is the MCP one,
    not ``workspace.storage``."""
    from mcp_client import client as mcp_client

    state = {"settings": {}}

    def _get(name):
        return {"settings": dict(state["settings"])}

    def _update(name, updates):
        state["settings"] = dict(updates.get("settings") or {})
        return {"settings": dict(state["settings"])}

    monkeypatch.setattr("workspace.get_workspace_metadata", _get)
    monkeypatch.setattr("workspace.update_workspace_metadata", _update)
    monkeypatch.setenv("AGENT_WORKSPACE", "acme")
    mcp_client._cache.clear()
    yield "acme"
    mcp_client._cache.clear()


@pytest.mark.skipif(not FIXTURE_SERVER.exists(), reason="fixture server missing")
def test_end_to_end_against_a_real_http_server(http_server, workspace):
    from mcp_client import client as mcp_client
    from mcp_client import store as mcp_store

    mcp_store.create_server(workspace, {
        "id": "echo",
        "name": "Echo over HTTP",
        "transport": "streamable_http",
        "url": http_server,
        "headers": {"X-Probe": "given"},
        "tool_allowlist": ["echo", "add"],
    })

    discovered = {t["name"]: t for t in mcp_client.discover(
        mcp_store.get_server(workspace, "echo"))}
    assert {"echo", "add", "report_env"} <= set(discovered)
    assert discovered["echo"]["id"] == "mcp__echo__echo"
    assert discovered["report_env"]["allowed"] is False

    tools = {t.name: t for t in mcp_client.tools_for(workspace, "echo")}
    assert set(tools) == {"mcp__echo__echo", "mcp__echo__add"}
    assert "echo: hello" in str(tools["mcp__echo__echo"].invoke({"text": "hello"}))
    assert "5" in str(tools["mcp__echo__add"].invoke({"a": 2, "b": 3}))
