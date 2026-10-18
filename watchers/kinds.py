"""
What each watcher kind watches, and how (docs/watchers.md, "Kinds").

A kind is a ``probe(watcher, secret)`` function: given the record and a
function that resolves a workspace secret by name, it looks at the source
once and returns a :class:`Probe`: the new ``state`` to store, and the
``events`` since the stored state (empty when nothing changed). The first
probe of a watcher, with no stored state, only takes a baseline: nothing
that already existed when the watcher was created is reported.

Every kind also declares ``CONFIG_FIELDS`` for :func:`validate_config` and
the form on the Watchers page: name, type (``str``, ``int``, ``bool``,
``secret``), whether it is required, and a default. ``optional_when`` and
``hidden_when`` (``{field: value}``) relax a required field, or hide it on
the form, while an earlier field holds that value.

``http``
    An HTTP resource. Config: url, method (GET), optional ``json_path``
    (dotted, ``data.items.0.status``) to watch one field instead of the whole
    body, optional ``headers_secret`` (a workspace secret holding a JSON
    object of request headers, for a token). State: a hash of what is
    watched. One event when it changes, carrying the new value trimmed. The
    URL must resolve to a public address (common/ssrf.py), like the web tools.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SecretResolver = Callable[[str], Optional[str]]

#: How much of a resource value an event carries.
TEXT_LIMIT = 1500
#: HTTP body size the probe reads at most.
HTTP_MAX_BYTES = 512 * 1024
HTTP_TIMEOUT_SECONDS = 20.0


@dataclass
class Probe:
    state: Dict[str, Any]
    events: List[Dict[str, Any]] = field(default_factory=list)
    # A short line for the Watchers page ("3 new messages", "unchanged").
    summary: str = ""


class ProbeError(Exception):
    """The source could not be read (bad credentials, host down, bad config)."""


# ── config fields ─────────────────────────────────────────────────────────────

CONFIG_FIELDS: Dict[str, List[Dict[str, Any]]] = {
    "http": [
        {"name": "url", "type": "str", "required": True},
        {"name": "method", "type": "str", "required": False, "default": "GET"},
        {"name": "json_path", "type": "str", "required": False, "default": ""},
        {"name": "headers_secret", "type": "secret", "required": False, "default": ""},
    ],
}


def validate_config(kind: str, config: Any) -> Dict[str, Any]:
    """The config with defaults filled and types coerced, or ``ValueError``."""
    fields = CONFIG_FIELDS.get(kind)
    if fields is None:
        raise ValueError(f"unknown watcher kind '{kind}'; one of {', '.join(CONFIG_FIELDS)}")
    src = dict(config) if isinstance(config, dict) else {}
    out: Dict[str, Any] = {}
    for spec in fields:
        name, typ = spec["name"], spec["type"]
        raw = src.get(name, spec.get("default"))
        if raw is None or raw == "":
            relaxed = any(out.get(k) == v for k, v in (spec.get("optional_when") or {}).items())
            if spec["required"] and not relaxed:
                raise ValueError(f"{kind} watcher needs '{name}'")
            out[name] = spec.get("default", "")
            continue
        if typ == "int":
            try:
                out[name] = int(raw)
            except (TypeError, ValueError) as e:
                raise ValueError(f"'{name}' must be a whole number") from e
        elif typ == "bool":
            out[name] = str(raw).strip().lower() in ("1", "true", "yes", "on") if not isinstance(raw, bool) else raw
        else:
            out[name] = str(raw).strip()
    if kind == "http":
        parsed = urlparse(out["url"])
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("'url' must be an http(s) URL")
        out["method"] = (out["method"] or "GET").upper()
        if out["method"] not in ("GET", "HEAD"):
            raise ValueError("'method' must be GET or HEAD; a watcher only reads")
    return out


# ── http ──────────────────────────────────────────────────────────────────────

def _json_path(value: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _http_fetch(cfg: Dict[str, Any], headers: Dict[str, str]) -> str:
    from common.ssrf import resolve_and_check
    parsed = urlparse(cfg["url"])
    ok, reason = resolve_and_check(parsed.hostname or "")
    if not ok:
        raise ProbeError(reason)
    import httpx
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
            with client.stream(cfg.get("method") or "GET", cfg["url"], headers=headers) as resp:
                if resp.status_code >= 400:
                    raise ProbeError(f"HTTP {resp.status_code} from {parsed.hostname}")
                chunks: List[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > HTTP_MAX_BYTES:
                        raise ProbeError(f"response larger than {HTTP_MAX_BYTES} bytes")
                    chunks.append(chunk)
                return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
    except ProbeError:
        raise
    except Exception as e:  # noqa: BLE001 - every transport failure is one probe error
        raise ProbeError(f"request failed: {e}") from e


def probe_http(watcher: Any, secret: SecretResolver, *, fetch: Optional[Callable[..., str]] = None) -> Probe:
    cfg = dict(watcher.config or {})
    headers: Dict[str, str] = {}
    if cfg.get("headers_secret"):
        raw = secret(cfg["headers_secret"])
        if not raw:
            raise ProbeError(f"secret '{cfg['headers_secret']}' is missing or empty")
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise ProbeError(f"secret '{cfg['headers_secret']}' is not a JSON object of headers") from e
        if not isinstance(parsed, dict):
            raise ProbeError(f"secret '{cfg['headers_secret']}' is not a JSON object of headers")
        headers = {str(k): str(v) for k, v in parsed.items()}
    body = (fetch or _http_fetch)(cfg, headers)
    watched: Any = body
    path = (cfg.get("json_path") or "").strip()
    if path:
        try:
            watched = _json_path(json.loads(body), path)
        except ValueError as e:
            raise ProbeError("response is not JSON, but json_path is set") from e
        if watched is None:
            raise ProbeError(f"json_path '{path}' matched nothing")
    text = watched if isinstance(watched, str) else json.dumps(watched, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    previous = (watcher.state or {}).get("hash")
    state = {"hash": digest, "preview": text[:200]}
    if not previous:
        return Probe(state=state, summary="baseline taken")
    if previous == digest:
        return Probe(state=state, summary="unchanged")
    label = f"{cfg['url']}" + (f" [{path}]" if path else "")
    return Probe(state=state, summary="changed", events=[{
        "url": cfg["url"], "json_path": path or None, "value": text[:TEXT_LIMIT],
        "previous_preview": (watcher.state or {}).get("preview"),
        "summary": f"{label} changed: {text[:160]}",
    }])


PROBES: Dict[str, Callable[..., Probe]] = {"http": probe_http}


def probe(watcher: Any, secret: SecretResolver) -> Probe:
    fn = PROBES.get(watcher.kind)
    if fn is None:
        raise ProbeError(f"unknown watcher kind '{watcher.kind}'")
    return fn(watcher, secret)


__all__ = [
    "CONFIG_FIELDS", "Probe", "ProbeError", "PROBES",
    "validate_config", "probe", "probe_http",
]
