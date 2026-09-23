"""Per-workspace MCP server configuration.

Stored in the workspace metadata under ``settings["mcp_servers"]`` rather than
in a file of its own. A server attached to one workspace must not be reachable
from another: an agent is built with a workspace, the tools it gets are looked
up with that workspace, and keeping the configuration *inside* the workspace
record is what makes that true by construction instead of by a filter somebody
has to remember to write.

**Secrets.** A header or an env value is stored as the operator typed it: this
hub already holds provider keys, and inventing a second secret store for six
strings would be a worse answer than the one it has. What does not happen is
handing those values back out. :func:`masked` replaces the value of any key that
looks like a credential (TOKEN, KEY, SECRET, PASSWORD, case-insensitive) with
its last four characters, and :func:`unmask_secrets` puts the stored value back
when an edit sends the masked form in unchanged. That round trip is why the
page can show "which token is this" without the value ever leaving the backend,
and why saving a form nobody edited cannot overwrite a token with dots.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional

log = logging.getLogger(__name__)

# Where the list lives inside a workspace's ``settings`` dict.
MCP_SETTINGS_KEY = "mcp_servers"

TRANSPORTS = ("stdio", "streamable_http", "sse", "websocket")

# The URL schemes each non-stdio transport can actually be reached on. Used by
# :func:`validate_url` to catch a pasted http:// URL on a websocket server (or
# the reverse) at save time rather than at the next agent build.
_URL_SCHEMES: Dict[str, tuple] = {
    "streamable_http": ("http", "https"),
    "sse": ("http", "https"),
    "websocket": ("ws", "wss"),
}

# Substrings that make a header or env *name* a credential. Matched on the name
# rather than the value: a value that happens to look like a token is not one,
# and a value that does not is still secret if it is called PASSWORD.
_SECRET_NAME_PARTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTHORIZATION")

# What a masked value looks like on the wire. The tail is kept so two
# credentials can be told apart without either being readable.
_MASK = "••••"

# A server id becomes part of a LangChain tool name (``mcp__<id>__<tool>``), so
# it has to survive that unchanged: lowercase, digits, underscore and hyphen,
# and no double underscore that would make the id ambiguous to split on.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

_CAPABILITY_KEYS = ("ingests_untrusted", "reads_private", "can_exfiltrate")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_id(server_id: str) -> str:
    """The id, or a ValueError saying why it cannot be one.

    Stricter than it needs to be for a JSON key, because the id is not only a
    key: it is half of every tool name this server produces and the thing the
    capability model parses back out of that name.
    """
    sid = str(server_id or "").strip().lower()
    if not _ID_RE.match(sid) or "__" in sid:
        raise ValueError(
            "An MCP server id must be lowercase letters, digits, underscore or "
            "hyphen, start with a letter or digit, and contain no double "
            "underscore (the double underscore separates the server from the "
            "tool in mcp__<server>__<tool>)."
        )
    return sid


def validate_url(transport: str, url: str) -> None:
    """Raise if a non-stdio transport's URL cannot possibly work with it.

    An empty URL is left alone: connecting already refuses a missing URL with
    a clearer message than this could give, and there is a moment (a new
    server, half filled in) where that is the honest state. This only catches
    a URL that is present but of the wrong kind, such as an ``http://`` pasted
    where a websocket transport wants ``ws://``.
    """
    schemes = _URL_SCHEMES.get(str(transport or ""))
    text = str(url or "").strip()
    if not schemes or not text:
        return
    from urllib.parse import urlparse

    scheme = urlparse(text).scheme.lower()
    if scheme not in schemes:
        wanted = " or ".join(f"{s}://" for s in schemes)
        raise ValueError(f"A {transport} server's URL must start with {wanted}")


def is_secret_name(name: str) -> bool:
    upper = str(name or "").upper()
    return any(part in upper for part in _SECRET_NAME_PARTS)


def mask_value(value: Any) -> str:
    """A credential's last four characters, behind dots."""
    text = "" if value is None else str(value)
    return f"{_MASK}{text[-4:]}" if text else ""


def is_masked(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_MASK)


def _mask_map(values: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in (values or {}).items():
        out[str(key)] = mask_value(value) if is_secret_name(key) else str(value)
    return out


def _unmask_map(incoming: Any, stored: Any) -> Dict[str, str]:
    """Incoming values, with every masked one replaced by what is stored.

    An unknown key whose value arrives masked is dropped rather than stored as
    dots: it can only come from a client echoing something it was shown for a
    key that has since been renamed, and a credential of literal bullet points
    would fail at connect time with a confusing error.
    """
    stored = stored or {}
    out: Dict[str, str] = {}
    for key, value in (incoming or {}).items():
        key = str(key)
        if is_masked(value):
            if key in stored:
                out[key] = str(stored[key])
            continue
        out[key] = str(value)
    return out


def _normalize_capabilities(value: Any) -> Dict[str, bool]:
    """The three capability ticks, always all three, always booleans.

    Absent means false — an operator who has not said a server reads private
    data has not claimed that it does. What must never happen is a *missing*
    key reading as "unknown" somewhere downstream: the capability model has no
    third state, and a tri-state here would invent one.
    """
    raw = value if isinstance(value, dict) else {}
    return {key: bool(raw.get(key)) for key in _CAPABILITY_KEYS}


def _normalize_approval(value: Any) -> Any:
    """``"none"``, ``"all"``, or the list of tool names that need a yes."""
    if isinstance(value, (list, tuple, set)):
        return sorted({str(v).strip() for v in value if str(v).strip()})
    text = str(value or "none").strip().lower()
    return text if text in ("none", "all") else "none"


def _normalize(record: Dict[str, Any]) -> Dict[str, Any]:
    """One stored entry, with every field present and of the right type."""
    transport = str(record.get("transport") or "stdio").strip().lower()
    if transport not in TRANSPORTS:
        transport = "stdio"
    return {
        "id": str(record.get("id") or ""),
        "name": str(record.get("name") or record.get("id") or ""),
        "description": str(record.get("description") or ""),
        "transport": transport,
        "command": str(record.get("command") or ""),
        "args": [str(a) for a in (record.get("args") or [])],
        "url": str(record.get("url") or ""),
        "headers": {str(k): str(v) for k, v in (record.get("headers") or {}).items()},
        "env": {str(k): str(v) for k, v in (record.get("env") or {}).items()},
        "enabled": bool(record.get("enabled", True)),
        "capabilities": _normalize_capabilities(record.get("capabilities")),
        "approval": _normalize_approval(record.get("approval")),
        "tool_allowlist": [str(t) for t in (record.get("tool_allowlist") or [])],
        "created_at": str(record.get("created_at") or _utc_now_iso()),
        # Status, written by the client on every connect attempt so the page can
        # say why a server produced no tools without connecting again itself.
        "last_error": record.get("last_error") or "",
        "last_seen": record.get("last_seen") or None,
        "tool_names": [str(t) for t in (record.get("tool_names") or [])],
    }


# ── Reading ───────────────────────────────────────────────────────────────────

def _settings(workspace: Optional[str]) -> Dict[str, Any]:
    if not workspace:
        return {}
    try:
        from workspace import get_workspace_metadata
        meta = get_workspace_metadata(workspace) or {}
    except Exception:  # noqa: BLE001 - never raises (see list_servers docstring below)
        log.debug("mcp: cannot read metadata for workspace %r", workspace, exc_info=True)
        return {}
    settings = meta.get("settings")
    return settings if isinstance(settings, dict) else {}


def list_servers(workspace: Optional[str]) -> List[Dict[str, Any]]:
    """Every MCP server configured in this workspace, normalized.

    Never raises: a workspace that does not exist, or whose metadata cannot be
    read, has no MCP servers. This is on the agent build path, where "the
    config is unreadable" must degrade to "no external tools" rather than to a
    failed build.
    """
    raw = _settings(workspace).get(MCP_SETTINGS_KEY)
    if not isinstance(raw, list):
        return []
    return [_normalize(r) for r in raw if isinstance(r, dict) and r.get("id")]


def get_server(workspace: Optional[str], server_id: str) -> Optional[Dict[str, Any]]:
    sid = str(server_id or "").strip().lower()
    for record in list_servers(workspace):
        if record["id"] == sid:
            return record
    return None


def enabled_servers(workspace: Optional[str]) -> List[Dict[str, Any]]:
    return [s for s in list_servers(workspace) if s.get("enabled")]


# ── Writing ───────────────────────────────────────────────────────────────────

def _save(workspace: str, records: List[Dict[str, Any]]) -> None:
    from workspace import get_workspace_metadata, update_workspace_metadata

    settings = dict((get_workspace_metadata(workspace) or {}).get("settings") or {})
    settings[MCP_SETTINGS_KEY] = records
    update_workspace_metadata(workspace, {"settings": settings})


def create_server(workspace: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Add a server to this workspace. The id must be free and well formed."""
    record = _normalize({**(data or {}), "id": validate_id((data or {}).get("id"))})
    validate_url(record["transport"], record["url"])
    records = list_servers(workspace)
    if any(r["id"] == record["id"] for r in records):
        raise ValueError(f"An MCP server with id '{record['id']}' already exists here")
    records.append(record)
    _save(workspace, records)
    return record


def update_server(workspace: str, server_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge changes into one server. Masked secrets keep their stored value.

    The id is immutable: it is baked into every tool name the server produces
    and into whatever agent records already reference those names, so renaming
    it in place would silently detach an agent from its tools. Deleting and
    re-adding is the honest way to change one.
    """
    sid = str(server_id or "").strip().lower()
    records = list_servers(workspace)
    for index, current in enumerate(records):
        if current["id"] != sid:
            continue
        merged = {**current}
        for key, value in (changes or {}).items():
            if key in ("id", "created_at") or value is None:
                continue
            if key in ("headers", "env"):
                merged[key] = _unmask_map(value, current.get(key))
            else:
                merged[key] = value
        normalized = _normalize(merged)
        validate_url(normalized["transport"], normalized["url"])
        records[index] = normalized
        _save(workspace, records)
        return records[index]
    return None


def delete_server(workspace: str, server_id: str) -> bool:
    sid = str(server_id or "").strip().lower()
    records = list_servers(workspace)
    remaining = [r for r in records if r["id"] != sid]
    if len(remaining) == len(records):
        return False
    _save(workspace, remaining)
    return True


def record_status(
    workspace: Optional[str],
    server_id: str,
    *,
    error: str = "",
    tool_names: Optional[List[str]] = None,
) -> None:
    """Write the outcome of a connect attempt onto the server entry.

    Best effort on purpose. This is called from the agent build path, where a
    failure to *record* a failure must not itself break the build, and from a
    worker thread that may not be able to take the metadata lock. A status that
    is one attempt stale is worth far less than a build that refuses to happen.
    """
    if not workspace:
        return
    try:
        changes: Dict[str, Any] = {"last_error": error or ""}
        if not error:
            changes["last_seen"] = _utc_now_iso()
        if tool_names is not None:
            changes["tool_names"] = list(tool_names)
        records = list_servers(workspace)
        sid = str(server_id or "").strip().lower()
        for index, current in enumerate(records):
            if current["id"] == sid:
                records[index] = _normalize({**current, **changes})
                _save(workspace, records)
                return
    except Exception:  # noqa: BLE001 - best-effort by design (see docstring)
        log.debug("mcp: could not record status for %r", server_id, exc_info=True)


# ── Masking, for anything that leaves the backend ────────────────────────────

def masked(record: Dict[str, Any]) -> Dict[str, Any]:
    """One server, safe to send to a browser: credentials down to four chars."""
    out = dict(record or {})
    out["headers"] = _mask_map(record.get("headers"))
    out["env"] = _mask_map(record.get("env"))
    return out


def unmask_secrets(workspace: Optional[str], server_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """An incoming edit with masked values resolved back to the stored ones.

    :func:`update_server` already does this for the fields it merges; this is
    the same operation for a caller that wants to inspect the resolved payload
    (the routes, when they validate a connection before storing it).
    """
    current = get_server(workspace, server_id) or {}
    out = dict(changes or {})
    for key in ("headers", "env"):
        if key in out:
            out[key] = _unmask_map(out[key], current.get(key))
    return out


# ── The capability claim ─────────────────────────────────────────────────────

def server_capabilities(workspace: Optional[str], server_id: str) -> FrozenSet[str]:
    """What the operator declared this server's tools can do.

    The grant is per server, not per tool, and deliberately so. Nothing about a
    remote tool's name or JSON schema is a security claim: a server is free to
    call an exfiltration endpoint ``get_weather``. The only party who can
    honestly classify the collection is the person who attached it, and the
    coarse answer they can actually give ("this one reaches the internet") is
    worth more than a fine-grained one nobody can verify.

    A server that is not configured here grants nothing, which is also what an
    id from another workspace resolves to.
    """
    record = get_server(workspace, server_id)
    if record is None:
        return frozenset()
    caps = record.get("capabilities") or {}
    return frozenset(key for key in _CAPABILITY_KEYS if caps.get(key))


def config_hash(record: Dict[str, Any]) -> str:
    """A stable digest of everything that changes what connecting produces.

    Status fields are excluded: recording a successful connect must not
    invalidate the cache entry that connect just filled.
    """
    payload = {
        key: record.get(key)
        for key in ("transport", "command", "args", "url", "headers", "env",
                    "tool_allowlist", "enabled")
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


__all__ = [
    "MCP_SETTINGS_KEY",
    "TRANSPORTS",
    "config_hash",
    "create_server",
    "delete_server",
    "enabled_servers",
    "get_server",
    "is_masked",
    "is_secret_name",
    "list_servers",
    "mask_value",
    "masked",
    "record_status",
    "server_capabilities",
    "unmask_secrets",
    "update_server",
    "validate_id",
    "validate_url",
]
