"""A two-tool MCP server over stdio, for the end-to-end test.

Small on purpose. The end-to-end test is not testing MCP itself, it is testing
that this hub's path — connect, list, rename, allowlist, call synchronously from
a thread that has no event loop — works against a real server rather than
against a mock that agrees with whatever the code does.

``report_env`` exists for the security half: it answers with whether a variable
the operator set reached the child, and whether one of the backend's provider
keys did. Scrubbing is a claim about a subprocess, and the only honest way to
test a claim about a subprocess is to ask the subprocess.

Run as ``python -m tests.fixtures.mcp_echo_server`` or by path; both work,
because the hub spawns it by path.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


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


if __name__ == "__main__":
    mcp.run(transport="stdio")
