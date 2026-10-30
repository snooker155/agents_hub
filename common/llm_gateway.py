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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
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
_KEY_HEADERS = ("authorization", "x-api-key", "x-goog-api-key", "api-key", "x-subscription-token")

#: Provider keys an agent container never gets (replaced by SENTINEL when the
#: hub has one, dropped otherwise).
PROVIDER_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                     "RAG_EMBEDDING_API_KEY", "WEB_SEARCH_API_KEY")

#: Workspace settings fields that hold a provider key.
SETTINGS_KEY_FIELDS = ("openai_api_key", "anthropic_api_key", "google_api_key")

DEFAULT_HOSTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
    # The web search providers that take WEB_SEARCH_API_KEY (tools/web.py).
    "brave": "https://api.search.brave.com",
    "tavily": "https://api.tavily.com",
    "exa": "https://api.exa.ai",
}

#: Search providers: the key is WEB_SEARCH_API_KEY, sent the provider's way.
SEARCH_KINDS = frozenset({"brave", "tavily", "exa"})


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
    for host in _browser_netlocs():
        out[host] = ("browser", "")
    try:
        from providers.registry import _stored_backends
        for backend in _stored_backends():
            host = _netloc(backend.get("base_url"))
            if host and backend.get("api_key") and not _is_local(host):
                out.setdefault(host, ("backend", str(backend.get("id") or "")))
    except Exception:  # noqa: BLE001 - no custom providers: the three above
        log.debug("custom providers unreadable", exc_info=True)
    return out


def _browser_netlocs() -> List[str]:
    """The browser service as the hub reaches it, and as a container does
    (its loopback address rewritten to the host gateway). Proxied wherever it
    is: the token it takes opens every workspace's sessions."""
    try:
        from common.browser_service import configured_url
        from common.hostnet import to_host_gateway
        url = configured_url()
    except Exception:  # noqa: BLE001 - no browser service configured
        return []
    if not url:
        return []
    return list(dict.fromkeys(h for h in (_netloc(url), _netloc(to_host_gateway(url))) if h))


def browser_target() -> str:
    """The browser service's address as the hub reaches it."""
    from common.browser_service import configured_url
    return _netloc(configured_url())


def container_env(env: Mapping[str, str], workspace: Optional[str] = None) -> Dict[str, str]:
    """``env`` for an agent container: every provider key replaced by the
    reference the gateway resolves, the hosts to proxy named."""
    out = dict(env)
    for name in (*PROVIDER_KEY_VARS, "AGENTS_HUB_BROWSER_TOKEN"):
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
    elif kind in SEARCH_KINDS:
        key = _setting("web_search_api_key")
    elif kind == "browser":
        from common.browser_service import configured_token
        key = configured_token()
    else:
        key = _workspace_key(kind, workspace)
    if not key:
        raise LookupError(f"no key for {netloc}")
    if kind == "brave":
        extra["x-subscription-token"] = key
    elif kind == "exa":
        extra["x-api-key"] = key
    elif kind == "tavily":
        pass  # in the body (patch_body)
    elif kind == "anthropic":
        extra["x-api-key"] = key
    elif kind == "google":
        extra["x-goog-api-key"] = key
    else:
        extra["authorization"] = f"Bearer {key}"
    return extra, query


def patch_body(netloc: str, body: bytes, workspace: Optional[str]) -> bytes:
    """The request body with a key the provider reads from it put in: Tavily
    takes ``api_key`` in its JSON. Any other body is returned as it is."""
    kind = provider_hosts(workspace).get(netloc, ("", ""))[0]
    if kind != "tavily" or not body:
        return body
    import json
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    if isinstance(doc, dict):
        doc["api_key"] = _setting("web_search_api_key")
        return json.dumps(doc).encode("utf-8")
    return body


def kind_of(netloc: str, workspace: Optional[str]) -> str:
    return provider_hosts(workspace).get(netloc, ("", ""))[0]


def browser_policy_floor(policy: Any, workspace: Optional[str]) -> Dict[str, Any]:
    """A session policy a container sent, made no looser than its workspace's:
    the workspace's own (isolated: read only, its site list), its denied hosts
    added, its allow list intersected. The agent's own lists and the run's
    environment, which only narrow it, are the container's to add."""
    from common.workspace_context import _workspace_ctx
    token = _workspace_ctx.set(workspace or "default")
    try:
        from tools.browser import isolated_session_policy, session_policy
        from tools.web import intersect_domains, isolated_workspace
        iso = isolated_workspace(workspace or "default")
        if iso:
            return isolated_session_policy(iso)
        hub = session_policy()
    finally:
        _workspace_ctx.reset(token)
    mine = dict(policy) if isinstance(policy, dict) else {}
    deny = list(dict.fromkeys([*(hub.get("deny_domains") or []), *(mine.get("deny_domains") or [])]))
    if hub.get("allowlist_enabled"):
        hub_allow = list(hub.get("allow_domains") or [])
        allow = (list(intersect_domains(mine.get("allow_domains") or [], hub_allow))
                 if mine.get("allowlist_enabled") else hub_allow)
        enabled = True
    else:
        allow, enabled = list(mine.get("allow_domains") or []), bool(mine.get("allowlist_enabled"))
    out = {**mine, **{k: v for k, v in hub.items()
                      if k not in ("deny_domains", "allow_domains", "allowlist_enabled")}}
    out.update({"deny_domains": deny, "allow_domains": allow, "allowlist_enabled": enabled})
    return out


def guard_browser(method: str, path: str, query: str, body: bytes, workspace: Optional[str],
                  session_workspace) -> Tuple[str, bytes]:
    """``(query, body)`` of a container's browser service call, kept to its
    workspace: a new session is its workspace's with the policy floor
    applied, a listing names its workspace, a session it names must be one
    of its workspace (``session_workspace(id)`` asks the service). Raises
    PermissionError otherwise."""
    import json
    from urllib.parse import parse_qsl, urlencode
    from common.remote_state import _same_workspace
    parts = [p for p in path.strip("/").split("/") if p]
    ws = workspace or "default"
    if parts[:1] != ["sessions"]:
        if parts in ([], ["healthz"]):
            return query, body
        raise PermissionError(f"/{path} is not a browser call an agent makes")
    if len(parts) == 1:
        if method == "POST":
            try:
                doc = json.loads(body or b"{}")
            except ValueError as exc:
                raise PermissionError("a session needs a JSON body") from exc
            doc["workspace"] = ws
            doc["policy"] = browser_policy_floor(doc.get("policy"), ws)
            return query, json.dumps(doc).encode("utf-8")
        pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "workspace"]
        return urlencode([*pairs, ("workspace", ws)]), body
    owner = session_workspace(parts[1])
    if owner is None or not _same_workspace(owner, ws):
        raise PermissionError(f"browser session {parts[1]} is not this run's workspace's")
    return query, body


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
           "forward_headers", "install", "patch_body", "provider_hosts", "resolve_auth", "strip_backend_key",
           "strip_record_key", "strip_settings_keys"]
