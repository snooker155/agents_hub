"""The echo server again, served over streamable HTTP on a port the test picks.

The stdio fixture proves the hub's MCP path against a real server it spawns
itself. This one proves the other half of the transport list: a server that
is already running somewhere, reached by URL, with headers instead of an
environment. Same three tools so the assertions can be shared; the transport
is the only thing under test.

Run as ``python tests/fixtures/mcp_http_server.py <port>``. The server prints
one ``READY`` line to stdout once it is listening, so a test can wait for that
instead of sleeping.
"""
from __future__ import annotations

import os
import sys
import threading

from mcp.server.fastmcp import FastMCP


def build(port: int) -> FastMCP:
    mcp = FastMCP("echo", host="127.0.0.1", port=port, stateless_http=True,
                  json_response=True, log_level="WARNING")

    @mcp.tool()
    def echo(text: str) -> str:
        """Return the text it was given, unchanged."""
        return f"echo: {text}"

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @mcp.tool()
    def report_env(name: str) -> str:
        """Report whether an environment variable reached this process."""
        return f"{name}={os.environ.get(name, '<unset>')}"

    return mcp


def _announce_when_listening(port: int) -> None:
    """Print READY once the port accepts a connection. Polled from a thread
    because FastMCP.run blocks and offers no startup hook."""
    import socket
    import time

    for _ in range(200):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                print("READY", flush=True)
                return
        except OSError:
            time.sleep(0.05)


if __name__ == "__main__":
    port = int(sys.argv[1])
    threading.Thread(target=_announce_when_listening, args=(port,), daemon=True).start()
    build(port).run(transport="streamable-http")
