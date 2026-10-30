"""
The model gateway: an agent container calls model providers without holding
a single provider key.

In a container (``AGENTS_HUB_REMOTE_STATE=1``) every key the agent's code
would send is a reference, never the key: ``hub-gateway`` for the workspace's
or the hub's own key of that provider, ``hub-agent-key:<agent>`` for an
agent's own key, ``hub-backend-key:<provider>`` for a custom provider's. The
model drivers and SDKs run unchanged and still believe they talk to
``api.openai.com``; :func:`install` intercepts their HTTP requests (httpx and
requests) to the hosts the hub named (``AGENTS_HUB_LLM_HOSTS``) and sends each
to the hub instead (``/api/run-state/llm/<scheme>/<host>/<path>``) with the
container's run token. The hub (:func:`forward`, routes/run_state.py) checks
the token and the host, puts the real key in, forwards the request as it is
and streams the answer back.

Hosts the hub never proxies (a local model server, the hub's own model
runtime) are reached directly, as before: they hold no key worth hiding.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: The key a container holds for a provider the hub keeps the key of.
SENTINEL = "hub-gateway"
AGENT_PREFIX = "hub-agent-key:"
BACKEND_PREFIX = "hub-backend-key:"

#: Hosts the container sends through the hub (comma separated host[:port]).
HOSTS_ENV = "AGENTS_HUB_LLM_HOSTS"

#: The prefix of the hub's proxy route.
ROUTE = "/api/run-state/llm"

#: Headers that carry a provider key in one SDK or another.
_KEY_HEADERS = ("authorization", "x-api-key", "x-goog-api-key", "api-key")

#: Provider keys an agent container never gets (replaced by SENTINEL when the
#: hub has one, dropped otherwise).
PROVIDER_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                     "RAG_EMBEDDING_API_KEY")

#: Workspace settings fields that hold a provider key.
SETTINGS_KEY_FIELDS = ("openai_api_key", "anthropic_api_key", "google_api_key")

DEFAULT_HOSTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
}


# ── the hub's side ───────────────────────────────────────────────────────────

def _netloc(url: Optional[str]) -> str:
    parts = urlsplit(str(url or "").strip())
    return (parts.netloc or "").lower()


#: Proxy providers on the local network too (a test's fake provider; never
#: needed in a deployment, where a local model server holds no key).
LOCAL_ENV = "AGENTS_HUB_LLM_GATEWAY_LOCAL"


def _is_local(netloc: str) -> bool:
    if os.environ.get(LOCAL_ENV, "").strip().lower() in ("1", "true", "yes"):
        return False
    host = netloc.split(":", 1)[0]
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal") or \
        host.endswith(".local") or host.endswith(".internal")


def _setting(name: str) -> str:
    from common.config import live_setting, settings
    value = live_setting(name.upper(), "")
    if value:
        return value
    return str(getattr(settings, name.lower(), "") or "")


def provider_hosts(workspace: Optional[str] = None) -> Dict[str, Tuple[str, str]]:
    """``{netloc: (kind, id)}`` of every host the hub proxies: the three
    providers (at their configured base URL, the workspace's included) and
    every custom provider with a key that is not on the local network.
    ``kind`` is openai, anthropic, google or backend; ``id`` names the backend."""
    out: Dict[str, Tuple[str, str]] = {}
    for kind, default in DEFAULT_HOSTS.items():
        out[_netloc(default)] = (kind, "")
    for kind, var in (("openai", "OPENAI_BASE_URL"), ("anthropic", "ANTHROPIC_BASE_URL")):
        host = _netloc(_setting(var))
        if host and not _is_local(host):
            out.setdefault(host, (kind, ""))
    if workspace:
        try:
            from workspace.storage import get_workspace_metadata
            settings = (get_workspace_metadata(workspace) or {}).get("settings") or {}
            host = _netloc(settings.get("openai_base_url"))
            if host and not _is_local(host):
                out.setdefault(host, ("openai", ""))
        except Exception:  # noqa: BLE001 - the hub's own hosts still answer
            log.debug("workspace base URL unreadable", exc_info=True)
    try:
        from providers.registry import _stored_backends
        for backend in _stored_backends():
            host = _netloc(backend.get("base_url"))
            if host and backend.get("api_key") and not _is_local(host):
                out.setdefault(host, ("backend", str(backend.get("id") or "")))
    except Exception:  # noqa: BLE001 - no custom providers: the three above
        log.debug("custom providers unreadable", exc_info=True)
    return out


def container_env(env: Mapping[str, str], workspace: Optional[str] = None) -> Dict[str, str]:
    """``env`` for an agent container: every provider key replaced by the
    reference the gateway resolves, the hosts to proxy named."""
    out = dict(env)
    for name in PROVIDER_KEY_VARS:
        if out.get(name):
            out[name] = SENTINEL
    out[HOSTS_ENV] = ",".join(sorted(provider_hosts(workspace)))
    return out


def strip_record_key(record: Dict[str, Any]) -> Dict[str, Any]:
    """An agent record as a container gets it: its own key, when it has one,
    as a reference."""
    if isinstance(record, dict) and record.get("api_key"):
        return {**record, "api_key": AGENT_PREFIX + str(record.get("id") or "")}
    return record


def strip_backend_key(backend: Dict[str, Any]) -> Dict[str, Any]:
    """A custom provider as a container gets it: a reference for its key and
    no headers (they may carry credentials; the gateway adds them)."""
    if not isinstance(backend, dict):
        return backend
    out = dict(backend)
    if out.get("api_key"):
        out["api_key"] = BACKEND_PREFIX + str(out.get("id") or "")
    if out.get("headers"):
        out["headers"] = {}
    return out


def strip_settings_keys(metadata: Any) -> Any:
    """Workspace metadata as a container gets it: its provider keys as the
    reference (the workspace's own key is what the gateway then uses)."""
    if not isinstance(metadata, dict):
        return metadata
    settings = metadata.get("settings")
    if not isinstance(settings, dict):
        return metadata
    clean = {k: (SENTINEL if k in SETTINGS_KEY_FIELDS and v else v) for k, v in settings.items()}
    return {**metadata, "settings": clean}


def _workspace_key(kind: str, workspace: Optional[str]) -> str:
    field = {"openai": "openai_api_key", "anthropic": "anthropic_api_key", "google": "google_api_key"}[kind]
    if workspace:
        # The workspace's own key, set in its settings (as agent_factory reads
        # it: only an override the workspace made), else the hub's.
        try:
            from workspace.storage import get_workspace_metadata
            settings = (get_workspace_metadata(workspace) or {}).get("settings") or {}
            value = str(settings.get(field) or "")
            if value and value != SENTINEL:
                return value
        except Exception:  # noqa: BLE001 - the hub's own key below
            log.debug("workspace key unreadable", exc_info=True)
    return _setting(field)


def _presented_key(headers: Mapping[str, str], query: str) -> str:
    """The reference the container's SDK sent. Authorization carries the run
    token on the way in (the container moved the SDK's own bearer key to
    x-api-key), so it is never read as a key here."""
    for name in ("x-api-key", "x-goog-api-key", "api-key"):
        value = headers.get(name) or ""
        if value:
            return value[7:].strip() if value.lower().startswith("bearer ") else value.strip()
    for part in query.split("&"):
        if part.startswith("key="):
            return part[4:]
    return ""


def resolve_auth(netloc: str, headers: Mapping[str, str], query: str,
                 workspace: Optional[str]) -> Tuple[Dict[str, str], str]:
    """``(headers to set, query)`` with the real key of the provider at
    ``netloc`` for a request presented with a reference. Raises LookupError
    for a host the hub does not proxy or a key it does not know."""
    hosts = provider_hosts(workspace)
    if netloc not in hosts:
        raise LookupError(f"{netloc} is not a model provider this hub proxies")
    kind, backend_id = hosts[netloc]
    presented = _presented_key({k.lower(): v for k, v in headers.items()}, query)
    query = "&".join(p for p in query.split("&") if p and not p.startswith("key="))
    extra: Dict[str, str] = {}
    if presented.startswith(AGENT_PREFIX):
        from agents.registry import load_all_raw
        agent_id = presented[len(AGENT_PREFIX):]
        record = next((r for r in load_all_raw() if r.get("id") == agent_id), None) or {}
        key = str(record.get("api_key") or "")
    elif presented.startswith(BACKEND_PREFIX) or kind == "backend":
        from providers.registry import get_backend
        backend = get_backend(presented[len(BACKEND_PREFIX):] if presented.startswith(BACKEND_PREFIX)
                              else backend_id) or {}
        if _netloc(backend.get("base_url")) != netloc:
            raise LookupError("that provider's key is not for this host")
        key = str(backend.get("api_key") or "")
        extra.update({str(k): str(v) for k, v in (backend.get("headers") or {}).items()})
        kind = "openai"
    else:
        key = _workspace_key(kind, workspace)
    if not key:
        raise LookupError(f"no key for {netloc}")
    if kind == "anthropic":
        extra["x-api-key"] = key
    elif kind == "google":
        extra["x-goog-api-key"] = key
    else:
        extra["authorization"] = f"Bearer {key}"
    return extra, query


#: Request headers never forwarded to the provider.
_HOP = frozenset({"host", "content-length", "connection", "keep-alive", "transfer-encoding",
                  "te", "upgrade", "proxy-authorization", "x-hub-run-token", "accept-encoding",
                  *_KEY_HEADERS})


def forward_headers(headers: Iterable[Tuple[str, str]], auth: Dict[str, str]) -> Dict[str, str]:
    out = {k: v for k, v in headers if k.lower() not in _HOP}
    out.update(auth)
    return out


# ── the container's side ─────────────────────────────────────────────────────

_installed = False
_install_lock = threading.Lock()


def _hosts() -> frozenset:
    raw = os.environ.get(HOSTS_ENV, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _route(url: str) -> Optional[str]:
    """The hub URL a request to ``url`` goes to instead, or None."""
    parts = urlsplit(url)
    if parts.netloc.lower() not in _hosts():
        return None
    from common.hostnet import hub_base_url
    path = parts.path or "/"
    target = f"{hub_base_url()}{ROUTE}/{parts.scheme}/{parts.netloc}{path}"
    return target + (f"?{parts.query}" if parts.query else "")


def _token_header() -> Dict[str, str]:
    from common.run_tokens import ENV
    token = os.environ.get(ENV, "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def install() -> None:
    """Route this process's model calls through the hub. Called once in an
    agent container (common/remote_state.py); never raises."""
    global _installed
    if _installed or not _hosts():
        return
    with _install_lock:
        if _installed:
            return
        try:
            _patch_httpx()
        except Exception:  # noqa: BLE001 - logged; the provider then refuses the reference key
            log.warning("could not route httpx through the model gateway", exc_info=True)
        try:
            _patch_requests()
        except Exception:  # noqa: BLE001 - as above
            log.warning("could not route requests through the model gateway", exc_info=True)
        _installed = True


def _rewrite_httpx(request: Any) -> Any:
    import httpx
    target = _route(str(request.url))
    if target is None:
        return request
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "authorization")}
    # The provider key the SDK put in Authorization moves to x-api-key, where
    # the gateway reads the reference; Authorization carries the run token.
    presented = request.headers.get("authorization")
    if presented:
        headers["x-api-key"] = presented
    headers.update(_token_header())
    return httpx.Request(request.method, target, headers=headers, content=request.content,
                         extensions=request.extensions)


def _patch_httpx() -> None:
    import httpx
    send, asend = httpx.Client.send, httpx.AsyncClient.send

    def patched_send(self, request, *args, **kwargs):
        if not request.stream or isinstance(request.stream, httpx.ByteStream):
            request.read()
        return send(self, _rewrite_httpx(request), *args, **kwargs)

    async def patched_asend(self, request, *args, **kwargs):
        if not request.stream or isinstance(request.stream, httpx.ByteStream):
            await request.aread()
        return await asend(self, _rewrite_httpx(request), *args, **kwargs)

    httpx.Client.send = patched_send  # type: ignore[method-assign]
    httpx.AsyncClient.send = patched_asend  # type: ignore[method-assign]


def _patch_requests() -> None:
    import requests
    send = requests.Session.send

    def patched_send(self, request, **kwargs):
        target = _route(request.url)
        if target is not None:
            presented = request.headers.pop("Authorization", None)
            if presented:
                request.headers["x-api-key"] = presented
            request.headers.update(_token_header())
            request.url = target
        return send(self, request, **kwargs)

    requests.Session.send = patched_send  # type: ignore[method-assign]


__all__ = ["AGENT_PREFIX", "BACKEND_PREFIX", "HOSTS_ENV", "ROUTE", "SENTINEL", "container_env",
           "forward_headers", "install", "provider_hosts", "resolve_auth", "strip_backend_key",
           "strip_record_key", "strip_settings_keys"]
