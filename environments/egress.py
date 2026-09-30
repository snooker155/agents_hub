"""The egress proxy: holds a ``limited`` network to its allowlist.

What it is. A small asyncio HTTP proxy: plain HTTP requests in absolute form
are forwarded, ``CONNECT host:port`` opens a TCP tunnel (TLS stays end to end,
the proxy never sees inside it). Every request carries a token, and the token
decides which hosts it may reach.

How a run uses it. When an environment's network is ``limited`` and the proxy
is enabled, environments/launch.py registers a token for the run with the
run's allowlist (:func:`register`) and gives the run
``HTTP_PROXY=http://<token>@<host>:<port>`` (and the lowercase and HTTPS
twins). A client that honours those variables (requests, httpx, urllib, curl,
pip, npm, git over https) sends ``Proxy-Authorization: Basic <token>:`` with
every request, which is how the proxy learns whose request it is.

The allowlist. A host is allowed when it equals an entry or is a subdomain of
one (``example.com`` covers ``api.example.com``). Network ``none`` registers
an empty list, so only the infrastructure hosts below pass. The run's own
entries come from the environment (plus the package registries when it allows them); the
proxy adds the hosts of the configured model providers
(:func:`infrastructure_hosts`), since a run whose model calls were refused
could not do anything at all. Anything else answers 403, an unknown or
expired token 407.

What it does not do. It governs clients that honour the proxy variables. A
process that ignores them and opens sockets directly is not stopped by the
proxy, whatever the network type (containers stay on the agents-hub bridge,
since docker's ``--network none`` would cut the model off too). The hub's own
web and browser tools check ``AGENTS_HUB_NETWORK`` /
``AGENTS_HUB_ALLOWED_HOSTS`` themselves, proxy or not.

Where it runs. Enabled by ``AGENTS_HUB_EGRESS_PROXY=1`` (off by default),
started by the dashboard backend's lifespan (dashboard/backend/main.py), by
every worker (runtime/worker.py) or by anything else that calls
:func:`start_background`. It must run on the host the
run runs on, since the URL a run gets names ``127.0.0.1`` (local mode) or
``host.docker.internal`` (docker mode). Tokens are kept in this process and in
the ``egress_tokens`` DocStore, so a proxy in another process on the same
database (a worker host running its own copy) can serve a token registered by
the api replica that prepared the launch.

Settings (read live): ``AGENTS_HUB_EGRESS_PROXY`` (on/off),
``AGENTS_HUB_EGRESS_PROXY_PORT`` (8099), ``AGENTS_HUB_EGRESS_PROXY_HOST``
(bind address; 127.0.0.1, or 0.0.0.0 when agents run in docker so containers
reach it through host.docker.internal), ``AGENTS_HUB_EGRESS_PROXY_PUBLIC_HOST``
(the host a run is told to use, when neither default fits),
``AGENTS_HUB_EGRESS_TOKEN_TTL`` (seconds a token lives, 86400).

Limits: request head 64 KiB, 256 concurrent connections, 15 s to connect
upstream, a tunnel or forward closes after 300 s without a byte either way.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

DEFAULT_PORT = 8099
DEFAULT_TTL = 86400
MAX_HEAD_BYTES = 64 * 1024
MAX_CONNECTIONS = 256
CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 300.0

_TRUTHY = {"1", "true", "yes", "on"}


def _setting(key: str, default: str = "") -> str:
    try:
        from common.config import live_setting
        return live_setting(key, default)
    except Exception:  # noqa: BLE001 - config unavailable (a bare test): environment only
        import os
        return (os.environ.get(key) or "").strip() or default


def enabled() -> bool:
    return _setting("AGENTS_HUB_EGRESS_PROXY").lower() in _TRUTHY


def port() -> int:
    try:
        return int(_setting("AGENTS_HUB_EGRESS_PROXY_PORT", str(DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


def _docker_in_use() -> bool:
    """Whether a container may need to reach the proxy: the global mode is
    docker, or some live environment pins docker."""
    try:
        from common.config import agent_execution_mode
        if agent_execution_mode() == "docker":
            return True
    except Exception:  # noqa: BLE001, S110 - unknown reads as "not docker"
        pass
    try:
        from environments import store
        return any(e.mode == "docker" and not e.archived for e in store.all())
    except Exception:  # noqa: BLE001 - same
        return False


def bind_host() -> str:
    explicit = _setting("AGENTS_HUB_EGRESS_PROXY_HOST")
    if explicit:
        return explicit
    return "0.0.0.0" if _docker_in_use() else "127.0.0.1"


def public_host(execution_mode: str) -> str:
    """The host a run is told to send its requests to."""
    explicit = _setting("AGENTS_HUB_EGRESS_PROXY_PUBLIC_HOST")
    if explicit:
        return explicit
    return "host.docker.internal" if execution_mode == "docker" else "127.0.0.1"


def token_ttl() -> int:
    try:
        return max(60, int(_setting("AGENTS_HUB_EGRESS_TOKEN_TTL", str(DEFAULT_TTL))))
    except ValueError:
        return DEFAULT_TTL


# ── host matching ────────────────────────────────────────────────────────────

def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """``host`` equals an entry or is a subdomain of one. Case and a trailing
    dot are ignored; an empty host or list allows nothing."""
    host = (host or "").strip().lower().rstrip(".").strip("[]")
    if not host:
        return False
    for entry in allowed or ():
        entry = str(entry or "").strip().lower().strip(".")
        if entry and (host == entry or host.endswith("." + entry)):
            return True
    return False


_PROVIDER_DEFAULT_HOSTS = (
    "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
    "openrouter.ai", "api.mistral.ai", "api.groq.com", "api.deepseek.com",
    "api.together.xyz",
)


def infrastructure_hosts() -> List[str]:
    """Hosts every run may reach through the proxy: the model providers.

    The well-known provider APIs, the host of every ``*_BASE_URL`` /
    ``*_ENDPOINT`` / ``LANGFUSE_HOST`` setting, the hub's own services
    (``AGENTS_HUB_*_URL``: the browser service, the model runtime), and the base URL of every
    custom backend (providers/registry.py). Loopback and host.docker.internal
    never go through the proxy at all (they are in ``NO_PROXY``).
    """
    import os
    hosts: List[str] = list(_PROVIDER_DEFAULT_HOSTS)
    values: Dict[str, str] = dict(os.environ)
    try:
        from common.config import read_dot_env
        values.update({k: v for k, v in read_dot_env().items() if v})
    except Exception:  # noqa: BLE001, S110 - the process environment is enough
        pass
    urls = [v for k, v in values.items()
            if k.endswith(("_BASE_URL", "_ENDPOINT", "_API_BASE")) or k == "LANGFUSE_HOST"
            or (k.startswith("AGENTS_HUB_") and k.endswith("_URL"))]
    try:
        from providers.registry import list_backends
        urls += [str(b.get("base_url") or "") for b in list_backends()]
    except Exception:  # noqa: BLE001, S110 - no custom backends readable
        pass
    for url in urls:
        try:
            host = (urlsplit(str(url)).hostname or "").lower()
        except ValueError:
            continue
        if host and host not in hosts and host not in ("localhost", "127.0.0.1", "::1"):
            hosts.append(host)
    return hosts


# ── the token registry ───────────────────────────────────────────────────────

_TOKENS: Dict[str, Dict[str, Any]] = {}
_TOKENS_LOCK = threading.Lock()
_registrations = 0


def _docstore():
    from common.docstore import DocStore
    return DocStore("egress_tokens")


def register(hosts: Iterable[str], *, environment_id: Optional[str] = None,
             owner: Optional[Dict[str, Any]] = None, ttl: Optional[int] = None) -> str:
    """A new token that may reach ``hosts`` (plus :func:`infrastructure_hosts`).

    Kept in this process and in the ``egress_tokens`` DocStore (best effort).
    Returns the token, to put in the proxy URL's userinfo.
    """
    global _registrations
    token = secrets.token_urlsafe(24)
    entry = {
        "hosts": [str(h).lower() for h in hosts if h],
        "infra": infrastructure_hosts(),
        "environment_id": environment_id,
        "owner": dict(owner or {}),
        "expires_at": time.time() + (ttl or token_ttl()),
    }
    with _TOKENS_LOCK:
        now = time.time()
        for key in [k for k, v in _TOKENS.items() if v.get("expires_at", 0) < now]:
            _TOKENS.pop(key, None)
        _TOKENS[token] = entry
        _registrations += 1
        prune = _registrations % 50 == 0
    try:
        store = _docstore()
        store.put(token, entry)
        if prune:
            _prune_store(store)
    except Exception:  # noqa: BLE001 - the in-process copy still serves a proxy in this process
        log.debug("could not persist egress token", exc_info=True)
    return token


def _prune_store(store) -> int:
    now = time.time()
    removed = 0
    with store.transaction():
        for key, doc in store.all().items():
            if not isinstance(doc, dict) or float(doc.get("expires_at") or 0) < now:
                store.delete(key)
                removed += 1
    return removed


def lookup(token: str) -> Optional[Dict[str, Any]]:
    """The registration for ``token``, or None when unknown or expired."""
    if not token:
        return None
    with _TOKENS_LOCK:
        entry = _TOKENS.get(token)
    if entry is None:
        try:
            doc = _docstore().get(token)
        except Exception:  # noqa: BLE001 - no database here: in-process tokens only
            doc = None
        if isinstance(doc, dict):
            entry = doc
            with _TOKENS_LOCK:
                _TOKENS[token] = doc
    if entry is None or float(entry.get("expires_at") or 0) < time.time():
        return None
    return entry


def revoke(token: str) -> None:
    with _TOKENS_LOCK:
        _TOKENS.pop(token, None)
    try:
        _docstore().delete(token)
    except Exception:  # noqa: BLE001, S110 - it expires anyway
        pass


def proxy_url(token: str, execution_mode: str) -> str:
    return f"http://{token}@{public_host(execution_mode)}:{port()}"


# ── the proxy ────────────────────────────────────────────────────────────────

def _token_from_headers(headers: Dict[str, str]) -> str:
    raw = headers.get("proxy-authorization", "")
    scheme, _, value = raw.partition(" ")
    if scheme.lower() != "basic" or not value:
        return ""
    try:
        decoded = base64.b64decode(value.strip()).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""
    user, _, password = decoded.partition(":")
    return user or password


def _parse_head(head: bytes) -> Tuple[str, str, str, List[Tuple[str, str]]]:
    lines = head.decode("iso-8859-1").split("\r\n")
    method, target, version = lines[0].split(" ", 2)
    headers: List[Tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if sep:
            headers.append((name.strip(), value.strip()))
    return method.upper(), target, version, headers


def _response(status: int, reason: str, body: str = "", extra: str = "") -> bytes:
    payload = body.encode("utf-8")
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n{extra}\r\n").encode() + payload


_HOP_HEADERS = {"proxy-authorization", "proxy-connection", "connection", "keep-alive"}


class EgressProxy:
    """The proxy server. ``lookup`` maps a token to its registration (the
    module registry by default; tests pass their own)."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, *,
                 lookup: Callable[[str], Optional[Dict[str, Any]]] = lookup) -> None:
        self.host = host
        self.port = port
        self._lookup = lookup
        self._server: Optional[asyncio.AbstractServer] = None
        self._slots: Optional[asyncio.Semaphore] = None
        self.stats = {"allowed": 0, "denied": 0, "unauthorized": 0, "errors": 0}

    async def start(self) -> None:
        self._slots = asyncio.Semaphore(MAX_CONNECTIONS)
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        sock = self._server.sockets[0] if self._server.sockets else None
        if sock is not None:
            self.port = sock.getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001, S110 - closing is best effort
                pass
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert self._slots is not None
        async with self._slots:
            try:
                await self._serve(reader, writer)
            except Exception:  # noqa: BLE001 - one broken client never takes the proxy down
                self.stats["errors"] += 1
                log.debug("egress proxy connection failed", exc_info=True)
            finally:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001, S110
                    pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), CONNECT_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return
        if len(head) > MAX_HEAD_BYTES:
            writer.write(_response(431, "Request Header Fields Too Large"))
            return
        try:
            method, target, version, header_list = _parse_head(head[:-4])
        except ValueError:
            writer.write(_response(400, "Bad Request", "malformed request line"))
            return
        headers = {k.lower(): v for k, v in header_list}

        entry = self._lookup(_token_from_headers(headers))
        if entry is None:
            self.stats["unauthorized"] += 1
            writer.write(_response(407, "Proxy Authentication Required",
                                   "egress proxy: unknown or expired token",
                                   'Proxy-Authenticate: Basic realm="agents-hub-egress"\r\n'))
            return

        if method == "CONNECT":
            host, _, port_text = target.rpartition(":")
            host = host.strip("[]")
            try:
                upstream_port = int(port_text)
            except ValueError:
                writer.write(_response(400, "Bad Request", "CONNECT needs host:port"))
                return
            path_line = b""
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                writer.write(_response(400, "Bad Request", "only absolute http:// URLs are forwarded"))
                return
            host = parts.hostname
            upstream_port = parts.port or 80
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            kept = [(k, v) for k, v in header_list if k.lower() not in _HOP_HEADERS]
            lines = [f"{method} {path} {version}"] + [f"{k}: {v}" for k, v in kept] + ["Connection: close"]
            path_line = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")

        allowed = list(entry.get("hosts") or []) + list(entry.get("infra") or [])
        if not host_allowed(host, allowed):
            self.stats["denied"] += 1
            log.info("egress proxy refused %s (environment %s)", host, entry.get("environment_id"))
            writer.write(_response(403, "Forbidden",
                                   f"egress proxy: host {host!r} is not on this run's allowlist"))
            return

        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, upstream_port), CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as exc:
            writer.write(_response(502, "Bad Gateway", f"egress proxy: cannot reach {host}: {exc}"))
            return
        self.stats["allowed"] += 1

        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            up_writer.write(path_line)
            await up_writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except (OSError, RuntimeError):
            pass
        try:
            writer.close()
        except Exception:  # noqa: BLE001, S110
            pass


# ── running it in the background ─────────────────────────────────────────────

class _Background:
    def __init__(self, proxy: EgressProxy) -> None:
        self.proxy = proxy
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error: Optional[BaseException] = None
        self.thread = threading.Thread(target=self._run, name="egress-proxy", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.proxy.start())
        except BaseException as exc:  # noqa: BLE001 - reported to start_background
            self.error = exc
            self.ready.set()
            return
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.proxy.stop())
        self.loop.close()

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


_running: Optional[_Background] = None


def start_background(*, host: Optional[str] = None, port_: Optional[int] = None,
                     force: bool = False) -> Optional[EgressProxy]:
    """Start the proxy on its own thread and event loop, when enabled (or
    ``force``). Returns the running proxy, or None when disabled. Idempotent."""
    global _running
    if _running is not None:
        return _running.proxy
    if not (force or enabled()):
        return None
    bg = _Background(EgressProxy(host or bind_host(), port() if port_ is None else port_))
    bg.thread.start()
    bg.ready.wait(timeout=10)
    if bg.error is not None:
        raise RuntimeError(f"egress proxy could not start: {bg.error}")
    _running = bg
    log.info("egress proxy listening on %s:%s", bg.proxy.host, bg.proxy.port)
    return bg.proxy


def stop_background() -> None:
    global _running
    if _running is not None:
        _running.stop()
        _running = None


def running() -> Optional[EgressProxy]:
    return _running.proxy if _running is not None else None


__all__ = [
    "EgressProxy", "enabled", "port", "bind_host", "public_host", "host_allowed",
    "infrastructure_hosts", "register", "lookup", "revoke", "proxy_url",
    "start_background", "stop_background", "running",
]
