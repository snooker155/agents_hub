"""Connecting to an MCP server and turning it into hub tools.

Three jobs, in order.

**Connecting.** ``langchain_mcp_adapters`` is async throughout, and this hub is
not: the dashboard runs in an event loop, an agent subprocess does not, and the
same function is called from both. :func:`_run_async` therefore always hands the
coroutine to a dedicated thread with its own loop and waits for it. That is one
answer for both callers instead of two, it cannot deadlock on a loop that is
already running, and it keeps the loop that owns an stdio subprocess confined to
the thread that created it. A route that does not want to block its own loop
wraps the call in ``asyncio.to_thread``.

**Naming.** Every tool is renamed ``mcp__<server id>__<tool name>``. The prefix
is what the capability model parses to find the server whose declared grants
apply (``tools/capabilities.grants_of``), what the approval gate matches on, and
what an agent record stores, so it has to be stable and unambiguous: the server
id is validated to contain no double underscore precisely so this split has
exactly one reading.

**Caching.** A tool list is a round trip to a remote service (for stdio, a whole
process start), and an agent is built far more often than a server's tool list
changes. The cache is keyed by workspace, server id and a hash of the server's
configuration, so editing a server invalidates it without anybody clearing
anything, and it expires after a few minutes so a server that gained a tool is
picked up without a restart.

What the cache does *not* hold is a live session. The adapter opens a fresh
session per tool call (``load_mcp_tools(None, connection=...)`` passes the
connection, not a session, into every converted tool), so a stdio server is
spawned for the call and reaped when that call's context manager exits. There is
no long-lived child process to terminate when an entry expires, and holding a
client object to own one would be holding something that owns nothing. The cost
of that design is a process start per call; the benefit is that a crashed or
hung server can never outlive the call that started it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from mcp_client import store

log = logging.getLogger(__name__)

# The separator between the server id and the remote tool's own name. Double,
# because a single underscore is legal inside both halves.
TOOL_PREFIX = "mcp__"
SEPARATOR = "__"
# How an agent record names a whole server, alongside the built-in group
# aliases ("filesystem", "service_ops", ...).
ALIAS_PREFIX = "mcp:"

# Seconds a cached tool list stays fresh. Long enough that a burst of agent
# builds costs one connect, short enough that a server gaining a tool shows up
# without anybody restarting the backend.
CACHE_TTL = float(os.getenv("AGENTS_HUB_MCP_TOOL_TTL", "300"))
# Listing tools is a handshake, so it gets a short leash: a server that cannot
# answer in this long is down as far as an agent build is concerned.
CONNECT_TIMEOUT = float(os.getenv("AGENTS_HUB_MCP_CONNECT_TIMEOUT", "30"))
# Running one is real work and gets the leash a tool call deserves.
CALL_TIMEOUT = float(os.getenv("AGENTS_HUB_MCP_CALL_TIMEOUT", "300"))


# ── Running async adapter calls from sync code ───────────────────────────────

def _run_async(factory, *, timeout: float):
    """Run ``factory()`` (a zero-argument coroutine function) to completion.

    Always in a fresh thread with a fresh loop, whether or not the caller has
    one running. The alternative — ``asyncio.run`` when there is no loop and
    something cleverer when there is — has two code paths, and the clever half
    is the one that deadlocks inside a running dashboard.
    """
    box: Dict[str, Any] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            box["value"] = loop.run_until_complete(
                asyncio.wait_for(factory(), timeout=timeout)
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    thread = threading.Thread(target=_runner, name="mcp-client", daemon=True)
    thread.start()
    # A grace period on top of the inner wait_for: the coroutine is meant to
    # time out on its own, and this only catches a thread wedged somewhere the
    # timeout cannot reach.
    thread.join(timeout + 10)
    if thread.is_alive():
        raise TimeoutError(f"MCP call did not finish within {timeout:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ── Connection configuration ─────────────────────────────────────────────────

def connection_config(record: Dict[str, Any]) -> Dict[str, Any]:
    """The adapter's connection dict for one stored server.

    For stdio this is where the secret boundary is drawn. The child starts from
    :func:`tools.shell.scrubbed_env` — the same scrubbed environment an agent's
    shell command gets — with the server's own variables merged on top. An MCP
    server is third-party code this hub spawns; it gets the variables the
    operator gave it and not the backend's provider keys.
    """
    transport = record.get("transport") or "stdio"
    if transport == "stdio":
        from tools.shell import scrubbed_env

        env = dict(scrubbed_env())
        env.update({str(k): str(v) for k, v in (record.get("env") or {}).items()})
        return {
            "transport": "stdio",
            "command": str(record.get("command") or ""),
            "args": [str(a) for a in (record.get("args") or [])],
            "env": env,
        }
    return {
        "transport": transport,
        "url": str(record.get("url") or ""),
        "headers": {str(k): str(v) for k, v in (record.get("headers") or {}).items()},
    }


def tool_id(server_id: str, tool_name: str) -> str:
    """``mcp__<server>__<tool>``, the id everything downstream matches on."""
    return f"{TOOL_PREFIX}{server_id}{SEPARATOR}{tool_name}"


def split_tool_id(name: str) -> Optional[Tuple[str, str]]:
    """``(server id, remote tool name)`` for an MCP tool id, else ``None``."""
    text = str(name or "")
    if not text.startswith(TOOL_PREFIX):
        return None
    rest = text[len(TOOL_PREFIX):]
    server, sep, tool = rest.partition(SEPARATOR)
    if not sep or not server or not tool:
        return None
    return server, tool


# ── The JSON schema an MCP tool declares, as a pydantic model ────────────────

_JSON_TYPES: Dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": List[Any],
    "object": Dict[str, Any],
}


def _field_type(spec: Any) -> Any:
    """A property's Python type, or ``Any`` when the schema does not say."""
    if not isinstance(spec, dict):
        return Any
    declared = spec.get("type")
    if isinstance(declared, list):
        declared = next((d for d in declared if d != "null"), None)
    if isinstance(declared, str):
        return _JSON_TYPES.get(declared, Any)
    for key in ("anyOf", "oneOf"):
        for option in spec.get(key) or []:
            if isinstance(option, dict) and option.get("type") != "null":
                return _field_type(option)
    return Any


def args_model(name: str, schema: Any):
    """A pydantic model for an MCP tool's ``inputSchema``.

    The adapter hands the raw JSON schema through as ``args_schema``, which
    LangChain accepts but ``agents.hooks.GuardedTool`` does not: it declares
    ``args_schema: Optional[Type[BaseModel]]``, so an MCP tool with a dict
    schema could not be wrapped, and an unwrappable tool is one that escapes
    the hooks and the approval gate. Converting here is what keeps every remote
    tool inside those gates, and it gives the catalog real parameter entries at
    the same time.

    A schema this cannot model faithfully (a property name that is not a Python
    identifier) becomes an open model that accepts anything, rather than a
    model that silently drops the argument: the remote server validates its own
    input, and losing an argument on the way there is worse than passing one it
    will reject.
    """
    from pydantic import ConfigDict, Field, create_model

    schema = schema if isinstance(schema, dict) else {}
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = {str(r) for r in (schema.get("required") or [])}
    model_name = "".join(ch if ch.isalnum() else "_" for ch in str(name)) + "Args"

    if any(not str(key).isidentifier() for key in properties):
        return create_model(model_name, __config__=ConfigDict(extra="allow"))

    fields: Dict[str, Any] = {}
    for key, spec in properties.items():
        annotation = _field_type(spec)
        description = (spec or {}).get("description") if isinstance(spec, dict) else None
        default = ... if key in required else None
        if key not in required:
            annotation = Optional[annotation]
        fields[str(key)] = (annotation, Field(default, description=description))
    return create_model(model_name, **fields)


# ── Renaming a loaded tool into a hub tool ───────────────────────────────────

def _rename(tool: Any, server_id: str) -> Any:
    """One adapter tool, as a hub tool: prefixed name, sync-callable, wrappable.

    Three things change and nothing else does.

    *The name* gains the ``mcp__<server>__`` prefix, so the capability model and
    the approval gate can find the server behind it.

    *The schema* becomes a pydantic model (see :func:`args_model`), without
    which the tool cannot be wrapped by the hook/approval guard.

    *A sync entry point* is added. The adapter builds a ``StructuredTool`` with
    only a ``coroutine``, and ``StructuredTool._run`` raises
    ``NotImplementedError`` on sync invocation — which is exactly how a hub
    agent calls its tools, since ``AgentExecutor`` runs synchronously. The
    wrapper drives the original tool's async path on the helper thread.
    """
    from langchain_core.tools import StructuredTool

    inner = tool
    name = tool_id(server_id, getattr(tool, "name", "tool"))

    def _call(**kwargs: Any) -> Any:
        return _run_async(lambda: inner.ainvoke(dict(kwargs)), timeout=CALL_TIMEOUT)

    async def _acall(**kwargs: Any) -> Any:
        return await inner.ainvoke(dict(kwargs))

    return StructuredTool(
        name=name,
        description=getattr(tool, "description", "") or "",
        args_schema=args_model(name, getattr(tool, "args_schema", None)),
        func=_call,
        coroutine=_acall,
        metadata={"mcp_server": server_id, "mcp_tool": getattr(tool, "name", "")},
    )


# ── Loading ───────────────────────────────────────────────────────────────────

def load_server_tools(server_cfg: Dict[str, Any]) -> List[Any]:
    """Connect to one server and return its tools, renamed and allowlisted.

    Raises on a connection failure rather than swallowing it: the caller that
    is building an agent wants a skipped server, the caller that is testing one
    from the page wants the error text, and only they can tell which. See
    :func:`tools_for` for the "never break the build" half.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    server_id = str(server_cfg.get("id") or "")
    connection = connection_config(server_cfg)
    client = MultiServerMCPClient({server_id: connection})
    loaded = _run_async(
        lambda: client.get_tools(server_name=server_id), timeout=CONNECT_TIMEOUT
    )

    allowlist = {str(t) for t in (server_cfg.get("tool_allowlist") or [])}
    if allowlist:
        loaded = [t for t in loaded if getattr(t, "name", "") in allowlist]
    return [_rename(t, server_id) for t in loaded]


# ── The cache ─────────────────────────────────────────────────────────────────

_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def refresh(workspace: Optional[str], server_id: Optional[str] = None) -> None:
    """Drop cached tools for one server, or for the whole workspace.

    Called when a server is edited, tested or deleted. The config hash already
    makes an edit invalidate its own entry; this is for the cases the hash
    cannot see, such as the remote server itself having changed.
    """
    ws = str(workspace or "")
    with _cache_lock:
        for key in [k for k in _cache if k[0] == ws and (server_id is None or k[1] == server_id)]:
            _cache.pop(key, None)


def tools_for(workspace: Optional[str], server_id: str) -> List[Any]:
    """This server's tools, from cache when it is fresh, never raising.

    A server that will not connect produces an empty list, a log line and a
    ``last_error`` on its own record. That last part is what makes the failure
    visible: an agent build must not die because somebody's ticket system is
    down, but silently building an agent with fewer tools than its record asks
    for is how an operator ends up debugging a prompt instead of a firewall.
    """
    record = store.get_server(workspace, server_id)
    if record is None or not record.get("enabled"):
        return []

    key = (str(workspace or ""), str(server_id))
    stamp = store.config_hash(record)
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry["hash"] == stamp and now - entry["loaded_at"] < CACHE_TTL:
            return list(entry["tools"])

    try:
        tools = load_server_tools(record)
    except Exception as exc:
        log.warning("mcp: server %r in workspace %r did not connect: %s",
                    server_id, workspace, exc)
        store.record_status(workspace, server_id, error=str(exc) or exc.__class__.__name__)
        return []

    with _cache_lock:
        _cache[key] = {"hash": stamp, "tools": tools, "loaded_at": now}
    store.record_status(
        workspace, server_id, error="",
        tool_names=[getattr(t, "name", "") for t in tools],
    )
    return list(tools)


def cached_tool_names(workspace: Optional[str], server_id: str) -> Optional[List[str]]:
    """Tool names already in the cache, or ``None`` when nothing is cached.

    Lets a listing say "12 tools" for a server it has loaded without connecting
    to one it has not: a GET that quietly starts a dozen subprocesses is not a
    GET anybody wants on a page that refreshes.
    """
    with _cache_lock:
        entry = _cache.get((str(workspace or ""), str(server_id)))
    if not entry:
        return None
    return [getattr(t, "name", "") for t in entry["tools"]]


# ── What an agent record asks for ────────────────────────────────────────────

def expand_ids(tool_ids: List[str], workspace: Optional[str]) -> List[Any]:
    """Tool objects for the MCP ids and aliases in an agent's tool list.

    ``mcp:<server>`` takes the whole server, the way ``filesystem`` takes the
    whole filesystem group; ``mcp__<server>__<tool>`` takes one tool. Loading
    is per server, so naming three tools from one server connects once.
    """
    wanted: Dict[str, Optional[set]] = {}
    for raw in tool_ids or []:
        name = str(raw)
        if name.startswith(ALIAS_PREFIX):
            server = name[len(ALIAS_PREFIX):].strip().lower()
            if server:
                wanted[server] = None  # the whole server wins over a subset
            continue
        split = split_tool_id(name)
        if split is None:
            continue
        server, _tool = split
        if server in wanted and wanted[server] is None:
            continue
        wanted.setdefault(server, set()).add(name)

    out: List[Any] = []
    for server, selected in wanted.items():
        for tool in tools_for(workspace, server):
            if selected is None or getattr(tool, "name", "") in selected:
                out.append(tool)
    return out


def append_mcp_tools(tools: List[Any], tool_ids: List[str], workspace: Optional[str]) -> List[Any]:
    """The agent's resolved tools plus whatever MCP tools its record asks for.

    Called from ``AgentFactory._build_agent`` immediately before
    ``guard_action_tools``, which is the only position that works: appended
    after it, a remote tool would run outside the workspace's hooks and outside
    the approval gate, which is the one thing an externally defined tool must
    not do.
    """
    if not any(str(t).startswith((TOOL_PREFIX, ALIAS_PREFIX)) for t in (tool_ids or [])):
        return tools
    try:
        extra = expand_ids(list(tool_ids or []), workspace)
    except Exception:
        log.exception("mcp: could not expand MCP tools for workspace %r", workspace)
        return tools
    have = {getattr(t, "name", "") for t in tools}
    return [*tools, *[t for t in extra if getattr(t, "name", "") not in have]]


# ── The catalog side ─────────────────────────────────────────────────────────

def mcp_tool_specs(workspace: Optional[str]) -> List[Any]:
    """This workspace's MCP tools as ``ToolSpec`` entries, for the editor.

    Deliberately not merged into ``tools/registry.TOOL_CATALOG``. The catalog is
    a static, auditable list every id of which is classified in
    ``tools/capabilities.py``, and the classification test asserts exactly that;
    folding a per-workspace, remotely-defined set into it would make that
    assertion unstateable. These are a second list the agent editor shows
    alongside the first, carrying the grants their server declares.
    """
    from tools.registry import spec_from_tool

    specs: List[Any] = []
    for record in store.enabled_servers(workspace):
        for tool in tools_for(workspace, record["id"]):
            specs.append(spec_from_tool(tool, category=f"{ALIAS_PREFIX}{record['id']}"))
    return specs


def discover(server_cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Connect now and describe what is there, for the page's Test button.

    Reports every tool the server offers, including ones the allowlist would
    filter out, because the point of the button is to help write that allowlist.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    server_id = str(server_cfg.get("id") or "")
    client = MultiServerMCPClient({server_id: connection_config(server_cfg)})
    loaded = _run_async(
        lambda: client.get_tools(server_name=server_id), timeout=CONNECT_TIMEOUT
    )
    allowlist = {str(t) for t in (server_cfg.get("tool_allowlist") or [])}
    return [
        {
            "name": getattr(t, "name", ""),
            "id": tool_id(server_id, getattr(t, "name", "")),
            "description": (getattr(t, "description", "") or "").strip(),
            "allowed": not allowlist or getattr(t, "name", "") in allowlist,
        }
        for t in loaded
    ]


__all__ = [
    "ALIAS_PREFIX",
    "CACHE_TTL",
    "SEPARATOR",
    "TOOL_PREFIX",
    "append_mcp_tools",
    "args_model",
    "cached_tool_names",
    "connection_config",
    "discover",
    "expand_ids",
    "load_server_tools",
    "mcp_tool_specs",
    "refresh",
    "split_tool_id",
    "tool_id",
    "tools_for",
]
