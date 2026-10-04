"""
What each watcher kind watches, and how (docs/watchers.md, "Kinds").

A kind is a ``probe(watcher, secret)`` function: given the record and a
function that resolves a workspace secret by name, it looks at the source
once and returns a :class:`Probe`: the new ``state`` to store, and the
``events`` since the stored state (empty when nothing changed). The first
probe of a watcher, with no stored state, only takes a baseline: nothing
that already existed when the watcher was created is reported, so a mailbox
with ten thousand old messages does not wake anybody ten thousand times.

Every kind also declares ``CONFIG_FIELDS`` for :func:`validate_config` and
the form on the Watchers page: name, type (``str``, ``int``, ``bool``,
``secret``), whether it is required, and a default. ``optional_when`` and
``hidden_when`` (``{field: value}``) relax a required field, or hide it on
the form, while an earlier field holds that value.

``imap``
    A mailbox over IMAP (the standard library's ``imaplib``). Config: host,
    port, ssl, username, ``password_secret`` (a workspace secret holding the
    password or app password), folder (INBOX), optional ``from_filter`` and
    ``subject_filter`` substrings. With ``use_google`` on, the Google
    connector's account signs in over XOAUTH2 (connectors/mail/oauth.py)
    instead: no password secret, and an empty host or username means Gmail's
    and the account's. State: the highest UID seen. One event per new
    message: sender, subject, date and the first lines of the text body.
``http``
    An HTTP resource. Config: url, method (GET), optional ``json_path``
    (dotted, ``data.items.0.status``) to watch one field instead of the whole
    body, optional ``headers_secret`` (a workspace secret holding a JSON
    object of request headers, for a token). State: a hash of what is
    watched. One event when it changes, carrying the new value trimmed. The
    URL must resolve to a public address (common/ssrf.py), like the web tools.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import hashlib
import imaplib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SecretResolver = Callable[[str], Optional[str]]

#: Events one probe may hand over at most; a mailbox that got a hundred
#: messages at once reports the newest ones and says how many it skipped.
MAX_EVENTS_PER_PROBE = 10
#: How much of a message body or a resource value an event carries.
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
    "imap": [
        {"name": "use_google", "type": "bool", "required": False, "default": False},
        {"name": "host", "type": "str", "required": True, "optional_when": {"use_google": True}},
        {"name": "port", "type": "int", "required": False, "default": 993},
        {"name": "ssl", "type": "bool", "required": False, "default": True},
        {"name": "username", "type": "str", "required": True, "optional_when": {"use_google": True}},
        {"name": "password_secret", "type": "secret", "required": True,
         "optional_when": {"use_google": True}, "hidden_when": {"use_google": True}},
        {"name": "folder", "type": "str", "required": False, "default": "INBOX"},
        {"name": "from_filter", "type": "str", "required": False, "default": ""},
        {"name": "subject_filter", "type": "str", "required": False, "default": ""},
    ],
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
    if kind == "imap" and not (1 <= out["port"] <= 65535):
        raise ValueError("'port' must be between 1 and 65535")
    if kind == "imap" and out.get("use_google"):
        # A Google sign in needs no password, and reaches Gmail.
        out["password_secret"] = ""
        if not out["host"]:
            out["host"], out["port"], out["ssl"] = "imap.gmail.com", 993, True
    return out


# ── imap ──────────────────────────────────────────────────────────────────────

def _decode_header(value: Any) -> str:
    if not value:
        return ""
    try:
        parts = email.header.decode_header(str(value))
        out = []
        for text, charset in parts:
            if isinstance(text, bytes):
                out.append(text.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(str(text))
        return "".join(out).strip()
    except Exception:  # noqa: BLE001 - an undecodable header is shown raw
        return str(value)


def _body_text(msg: Any) -> str:
    """The first text/plain part, or a tag-stripped text/html one, trimmed."""
    html_fallback = ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - a part we cannot decode is skipped
            continue
        if ctype == "text/plain" and text.strip():
            return text.strip()[:TEXT_LIMIT]
        if ctype == "text/html" and not html_fallback:
            html_fallback = re.sub(r"<[^>]+>", " ", text)
            html_fallback = re.sub(r"\s+", " ", html_fallback).strip()
    return html_fallback[:TEXT_LIMIT]


@dataclass
class GoogleToken:
    """What ``_imap_connect`` gets instead of a password under ``use_google``."""
    address: str
    token: str


def _imap_connect(cfg: Dict[str, Any], password: Any) -> Any:
    client_cls = imaplib.IMAP4_SSL if cfg.get("ssl", True) else imaplib.IMAP4
    try:
        client = client_cls(cfg["host"], int(cfg.get("port") or (993 if cfg.get("ssl", True) else 143)), timeout=20)
        if isinstance(password, GoogleToken):
            from connectors.mail.oauth import imap_authenticate
            imap_authenticate(client, password.address, password.token)
        else:
            client.login(cfg["username"], password)
    except Exception as e:  # noqa: BLE001 - every connection failure is one probe error
        raise ProbeError(f"IMAP connection failed: {e}") from e
    return client


#: Kept in an imap watcher's state under ``use_google``: the workspace whose
#: Google connector the watcher was set up to sign in with (stamped by the
#: route on create and update, by the first poll for an older watcher).
GOOGLE_FROM = "google_from"


def google_source(workspace: Optional[str]) -> str:
    """The workspace whose Google connector a watcher in ``workspace`` signs
    in with: its own when it defines one, else the default workspace."""
    from connectors.channels.store import in_workspace
    from connectors.google import STORE as GOOGLE_STORE
    return in_workspace(workspace, GOOGLE_STORE.effective_workspace)


def _check_google_source(watcher: Any) -> str:
    """The Google connector a watcher uses must stay the one it was set up
    with. A watcher a workspace editor pointed at the workspace's own account
    must not read the operator's mailbox once the workspace drops its own
    connector and falls back to the default's: that takes an administrator
    saving the watcher again (dashboard/backend/routes/watchers.py)."""
    source = google_source(getattr(watcher, "workspace", None))
    approved = str((getattr(watcher, "state", None) or {}).get(GOOGLE_FROM) or "")
    if approved and approved != source:
        raise ProbeError(
            f"the Google account this watcher signs in with is now the '{source}' workspace's, "
            f"not the '{approved}' one it was set up with; save the watcher again to confirm")
    return source


def _google_credentials(cfg: Dict[str, Any], login: Optional[Callable[[], tuple]],
                        workspace: Optional[str] = None) -> GoogleToken:
    """Sign in with the Google connector in effect in the watcher's workspace:
    its own when it defines one, else the default workspace's, never another
    workspace's (connectors/channels/store.py). The poller runs outside any
    run, so the workspace is passed explicitly."""
    from connectors.channels.store import in_workspace
    from connectors.mail import oauth
    try:
        address, token = in_workspace(workspace, oauth.google_login, login)
        oauth.check_address(cfg.get("username") or "", address)
    except oauth.MailOAuthError as e:
        raise ProbeError(str(e)) from e
    return GoogleToken(address=address, token=token)


def probe_imap(watcher: Any, secret: SecretResolver, *, connect: Optional[Callable[..., Any]] = None,
               google_login: Optional[Callable[[], tuple]] = None) -> Probe:
    cfg = dict(watcher.config or {})
    keep: Dict[str, Any] = {}
    if cfg.get("use_google"):
        keep[GOOGLE_FROM] = _check_google_source(watcher)
        password: Any = _google_credentials(cfg, google_login, getattr(watcher, "workspace", None))
        cfg["username"] = password.address
    else:
        password = secret(cfg.get("password_secret") or "")
        if not password:
            raise ProbeError(f"secret '{cfg.get('password_secret')}' is missing or empty")
    client = (connect or _imap_connect)(cfg, password)
    try:
        status, _ = client.select(cfg.get("folder") or "INBOX", readonly=True)
        if status != "OK":
            raise ProbeError(f"could not open folder '{cfg.get('folder') or 'INBOX'}'")
        last_uid = int((watcher.state or {}).get("last_uid") or 0)
        status, data = client.uid("search", None, f"UID {last_uid + 1}:*" if last_uid else "ALL")
        if status != "OK":
            raise ProbeError("IMAP search failed")
        uids = [int(u) for u in (data[0].split() if data and data[0] else []) if int(u) > last_uid]
        uids.sort()
        if not uids:
            return Probe(state={"last_uid": last_uid, **keep}, summary="no new messages")
        if last_uid == 0:
            # First look: take the baseline, report nothing that was already there.
            return Probe(state={"last_uid": uids[-1], **keep}, summary=f"baseline taken, {len(uids)} messages in the folder")
        from_filter = (cfg.get("from_filter") or "").lower()
        subject_filter = (cfg.get("subject_filter") or "").lower()
        events: List[Dict[str, Any]] = []
        skipped = 0
        for uid in uids[-MAX_EVENTS_PER_PROBE:]:
            status, parts = client.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = None
            for item in parts or []:
                if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                    raw = bytes(item[1])
                    break
            if status != "OK" or raw is None:
                continue
            msg = email.message_from_bytes(raw)
            sender = _decode_header(msg.get("From"))
            subject = _decode_header(msg.get("Subject"))
            if from_filter and from_filter not in sender.lower():
                skipped += 1
                continue
            if subject_filter and subject_filter not in subject.lower():
                skipped += 1
                continue
            events.append({
                "uid": uid, "from": sender, "subject": subject, "date": _decode_header(msg.get("Date")),
                "message_id": _decode_header(msg.get("Message-ID")), "body": _body_text(msg),
                "summary": f"new mail from {sender or 'unknown'}: {subject or '(no subject)'}",
            })
        unreported = len(uids) - min(len(uids), MAX_EVENTS_PER_PROBE)
        bits = [f"{len(events)} new message{'s' if len(events) != 1 else ''}"]
        if skipped:
            bits.append(f"{skipped} filtered out")
        if unreported:
            bits.append(f"{unreported} older ones not reported")
        return Probe(state={"last_uid": uids[-1], **keep}, events=events, summary=", ".join(bits))
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 - a failed logout changes nothing
            log.debug("imap logout failed", exc_info=True)


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


PROBES: Dict[str, Callable[..., Probe]] = {"imap": probe_imap, "http": probe_http}


def probe(watcher: Any, secret: SecretResolver) -> Probe:
    fn = PROBES.get(watcher.kind)
    if fn is None:
        raise ProbeError(f"unknown watcher kind '{watcher.kind}'")
    return fn(watcher, secret)


__all__ = [
    "CONFIG_FIELDS", "MAX_EVENTS_PER_PROBE", "Probe", "ProbeError", "PROBES",
    "validate_config", "probe", "probe_imap", "probe_http", "GoogleToken", "GOOGLE_FROM",
    "google_source",
]
