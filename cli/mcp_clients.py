"""How each MCP client is pointed at the hub's ``/v1/mcp`` (docs/hub-as-mcp-server.md).

The server is the same for every client; only the place and the shape of the
client's own configuration differ. :func:`snippet` gives what to paste,
:func:`config_path` where a client keeps it, :func:`merge` writes the hub's
entry into an existing file without touching the other servers in it. The
Distribution page builds the same snippets in the browser
(dashboard/frontend/src/components/distribution/mcpClients.js); a format
changed here changes there too.
"""
from __future__ import annotations

import json
import os
import platform
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

SERVER_NAME = "agents-hub"

#: Client id -> what people call it. The order is the order of the help text.
CLIENTS: Dict[str, str] = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "vscode": "VS Code",
    "windsurf": "Windsurf",
    "claude-desktop": "Claude Desktop",
    "codex": "Codex CLI",
    "gemini": "Gemini CLI",
    "other": "Any other MCP client",
}

#: Clients whose configuration is a JSON file this module can merge into.
JSON_CLIENTS = ("cursor", "vscode", "windsurf", "claude-desktop", "gemini")


def claude_code_argv(url: str, headers: Dict[str, str]) -> List[str]:
    argv = ["claude", "mcp", "add", "--transport", "http", SERVER_NAME, url]
    for name, value in headers.items():
        argv += ["--header", f"{name}: {value}"]
    return argv


def vscode_add_argv(url: str, headers: Dict[str, str]) -> List[str]:
    """``code --add-mcp``: VS Code's own way to add a server to the user profile."""
    entry = {"name": SERVER_NAME, **entry_for("vscode", url, headers)}
    return ["code", "--add-mcp", json.dumps(entry)]


def entry_for(client: str, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """The hub's entry in a JSON client's server map."""
    if client == "vscode":
        return {"type": "http", "url": url, **({"headers": headers} if headers else {})}
    if client == "windsurf":
        return {"serverUrl": url, **({"headers": headers} if headers else {})}
    if client == "gemini":
        return {"httpUrl": url, **({"headers": headers} if headers else {})}
    if client == "claude-desktop":
        # Claude Desktop starts local (stdio) servers from its config file; a
        # remote one with a custom header goes through the mcp-remote bridge.
        # The values travel through env so a space in "Bearer ..." survives
        # Windows' argument quoting, as mcp-remote's own docs advise.
        args = ["-y", "mcp-remote", url]
        env: Dict[str, str] = {}
        for i, (name, value) in enumerate(headers.items()):
            var = "AUTH_HEADER" if name.lower() == "authorization" else f"HUB_HEADER_{i}"
            args += ["--header", f"{name}:${{{var}}}"]
            env[var] = value
        return {"command": "npx", "args": args, **({"env": env} if env else {})}
    return {"url": url, **({"headers": headers} if headers else {})}


def _servers_key(client: str) -> str:
    return "servers" if client == "vscode" else "mcpServers"


def codex_toml(url: str, headers: Dict[str, str]) -> str:
    lines = [f"[mcp_servers.{SERVER_NAME}]", f"url = {json.dumps(url)}"]
    if headers:
        pairs = ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in headers.items())
        lines.append(f"http_headers = {{ {pairs} }}")
    return "\n".join(lines) + "\n"


def snippet(client: str, url: str, headers: Dict[str, str]) -> str:
    """What to paste, as text."""
    if client == "claude-code":
        return " ".join(shlex.quote(a) for a in claude_code_argv(url, headers))
    if client == "codex":
        return codex_toml(url, headers)
    if client == "other":
        lines = [f"URL: {url}", "Transport: Streamable HTTP"]
        lines += [f"Header: {name}: {value}" for name, value in headers.items()]
        return "\n".join(lines)
    return json.dumps({_servers_key(client): {SERVER_NAME: entry_for(client, url, headers)}},
                      indent=2)


def config_path(client: str, *, project: bool = False, cwd: Optional[Path] = None,
                home: Optional[Path] = None) -> Optional[Path]:
    """Where a client keeps its servers: the user-wide file, or with
    ``project`` the one in the current folder. None where the client has no
    file of that kind (VS Code's user servers live in its profile and are
    added with ``code --add-mcp``; Windsurf and Claude Desktop have no
    per-project file)."""
    cwd = cwd or Path.cwd()
    home = home or Path.home()
    if client == "cursor":
        return (cwd if project else home) / ".cursor" / "mcp.json"
    if client == "vscode":
        return cwd / ".vscode" / "mcp.json" if project else None
    if client == "gemini":
        return (cwd if project else home) / ".gemini" / "settings.json"
    if client == "codex":
        return None if project else home / ".codex" / "config.toml"
    if project:
        return None
    if client == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    if client == "claude-desktop":
        system = platform.system()
        if system == "Darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if system == "Windows":
            appdata = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    return None


def merge(client: str, path: Path, url: str, headers: Dict[str, str]) -> None:
    """Write the hub's entry into ``path``, keeping everything else in it.
    ``ValueError`` when the existing file cannot be read safely."""
    if client == "codex":
        text = path.read_text("utf-8") if path.is_file() else ""
        if f"[mcp_servers.{SERVER_NAME}]" in text:
            raise ValueError(f"{path} already has [mcp_servers.{SERVER_NAME}]; "
                             "edit it by hand or remove it first")
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + sep + codex_toml(url, headers), "utf-8")
        return
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text("utf-8") or "{}")
        except ValueError:
            raise ValueError(f"{path} is not valid JSON; fix it or paste the snippet by hand") from None
        if not isinstance(data, dict):
            raise ValueError(f"{path} does not hold a JSON object")
    data.setdefault(_servers_key(client), {})[SERVER_NAME] = entry_for(client, url, headers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")


__all__ = ["CLIENTS", "JSON_CLIENTS", "SERVER_NAME", "snippet", "entry_for", "config_path",
           "merge", "claude_code_argv", "vscode_add_argv", "codex_toml"]
