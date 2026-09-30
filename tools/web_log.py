"""
Web access log — every ``web_search`` and ``fetch_url`` call, with what came
back and what looked wrong with it.

Two jobs, both of them review jobs:

* **Did the tool read the response correctly?** Extraction is lossy by design
  (HTML is stripped, JSON is re-serialised, text is truncated). When an agent
  reasons from a page and gets it wrong, the question is always "what did the
  tool actually hand it?" — so the exact returned text is stored, alongside the
  status, content type, redirect chain and timing.

* **Was the response trying something?** Retrieved content is untrusted
  (``tools/web.py`` wraps it and says so), but a wrapper is not a detector.
  ``scan_content`` flags the patterns worth a human look: injection phrasing,
  instructions hidden from human readers but legible to a model, attempts to
  close the untrusted-content envelope, credential-shaped strings, and
  exfiltration-shaped requests.

Flags are *signals, not verdicts*. They are deliberately eager — a page
quoting "ignore all previous instructions" in an article about prompt
injection gets flagged, and that is the right trade for a review surface.
Nothing here blocks a call; enforcement lives in the SSRF guard, the domain
policy and the capability model.

Storage is the ``web_log`` :class:`~common.docstore.DocStore`, one document
per entry keyed by the entry's own ``id``: ``append`` puts the new document
and trims the oldest keys past ``web_log_max_entries`` inside one
``store.transaction()``, atomic across every process and host in place of the
file lock this used to take. An existing ``web_requests.jsonl`` is imported
once, line by line, and renamed ``.migrated``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

from common.docstore import DocStore
from common.paths import WEB_LOG_FILE

log = logging.getLogger(__name__)

_store = DocStore("web_log")

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


# ── Security heuristics ───────────────────────────────────────────────────────
#
# (code, severity, compiled pattern, human explanation). Ordered high → low so
# a truncated flag list keeps the interesting ones.

_PATTERNS: List[tuple] = [
    # ── Prompt injection ──────────────────────────────────────────────────
    ("injection.override", "high",
     re.compile(r"\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(your\s+|the\s+)?"
                r"(previous|prior|earlier|above|preceding)\s+"
                r"(instructions?|prompts?|rules?|directions?|messages?)", re.I),
     "Text instructing the reader to discard its previous instructions."),
    ("injection.new_instructions", "high",
     re.compile(r"\b(new|updated|revised|real|actual)\s+(system\s+)?"
                r"(instructions?|directives?|task|prompt)\s*[:\-]", re.I),
     "Text presenting itself as a fresh instruction set."),
    ("injection.role_switch", "high",
     re.compile(r"\byou\s+are\s+(now|no\s+longer)\b|\bfrom\s+now\s+on\s*,?\s*you\b", re.I),
     "Text attempting to reassign the reader's role."),
    ("injection.fake_speaker", "high",
     re.compile(r"(?m)^\s*(system|assistant|developer|human)\s*:\s*\S|"
                r"</?\s*(system|assistant|developer)\s*>|"
                r"\[/?INST\]|<\|im_(start|end)\|>", re.I),
     "Chat-transcript or role markers, used to fake a system/assistant turn."),
    ("injection.secrecy", "high",
     re.compile(r"\b(do\s+not|don't|never)\s+(tell|inform|mention\s+(this\s+)?to|reveal\s+this\s+to)\s+"
                r"(the\s+)?(user|human|operator)\b|\bwithout\s+(telling|informing)\s+the\s+user\b", re.I),
     "Text asking the reader to hide something from the user."),
    ("injection.prompt_disclosure", "high",
     re.compile(r"\b(reveal|print|repeat|output|show)\s+(me\s+)?(your|the)\s+"
                r"(system\s+prompt|instructions|initial\s+prompt|rules)", re.I),
     "Text asking the reader to disclose its system prompt."),
    ("injection.addressed_to_ai", "medium",
     re.compile(r"\b(attention|note\s+to|message\s+for|important\s+for)\s+"
                r"(the\s+)?(ai|llm|assistant|agent|chatbot|language\s+model)\b", re.I),
     "Content addressed to an AI reader rather than a human one."),

    # ── Tool / command abuse ──────────────────────────────────────────────
    ("tooling.command", "medium",
     re.compile(r"\b(run|execute|paste|type)\s+(the\s+)?(following|this|these)\s+"
                r"(command|commands|code|script|snippet)\b", re.I),
     "Text instructing the reader to run a command."),
    ("tooling.destructive", "high",
     re.compile(r"\brm\s+-rf\s+[/~]|\bmkfs\b|\bdd\s+if=/dev/|:\(\)\s*\{\s*:\|:&\s*\}", re.I),
     "A destructive shell command appears in the response."),
    ("tooling.pipe_to_shell", "high",
     re.compile(r"\b(curl|wget)\b[^\n|]{0,200}\|\s*(sudo\s+)?(ba)?sh\b", re.I),
     "A download-piped-to-shell installer appears in the response."),
    ("tooling.tool_call", "medium",
     re.compile(r"\b(call|invoke|use)\s+(the\s+)?(tool|function)\s+[`\"']?"
                r"[a-z_]{3,32}[`\"']?|\"tool_calls?\"\s*:|\bfunction_call\b", re.I),
     "Text shaped like a tool-call instruction."),

    # ── Exfiltration ──────────────────────────────────────────────────────
    ("exfil.send_data", "high",
     re.compile(r"\b(send|post|upload|forward|transmit|email)\s+"
                r"(?:[\w'\-]+\s+){0,4}"
                r"(results?|data|contents?|files?|credentials?|keys?|tokens?|"
                r"history|conversation|context)\s+to\b", re.I),
     "Text instructing the reader to send data to a third party."),
    ("exfil.url_sink", "medium",
     re.compile(r"https?://[^\s\"'<>]{0,200}[?&]\s*"
                r"(q|data|payload|token|key|secret|content|text|msg)="
                r"[^\s\"'<>]{0,40}\{", re.I),
     "A URL with a placeholder query parameter — an outbound channel template."),

    # ── Credential-shaped strings ─────────────────────────────────────────
    ("secret.api_key", "medium",
     re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}|"
                r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", re.I),
     "A string shaped like a live API key or token."),
    ("secret.private_key", "high",
     re.compile(r"-----BEGIN\s+[A-Z ]*PRIVATE\s+KEY-----"),
     "A private key block appears in the response."),

    # ── Obfuscation ───────────────────────────────────────────────────────
    ("obfuscation.data_uri", "medium",
     re.compile(r"data:(text/html|application/javascript|image/svg\+xml)[^,\s]*;base64,", re.I),
     "A base64 data: URI — content that hides what it is until decoded."),
]

# Invisible characters used to smuggle text past a human reviewer. Counted
# rather than matched once: a handful is formatting, a hundred is a payload.
_ZERO_WIDTH = re.compile("[\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u2064\\ufeff]")
_ZERO_WIDTH_THRESHOLD = 24

MAX_FLAGS = 24


def _excerpt(text: str, start: int, end: int, pad: int = 70) -> str:
    """One-line context window around a match, for the log UI."""
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    snippet = text[lo:hi].replace("\n", " ").replace("\r", " ")
    snippet = re.sub(r"\s{2,}", " ", snippet).strip()
    if lo > 0:
        snippet = "…" + snippet
    if hi < len(text):
        snippet = snippet + "…"
    return snippet[:240]


def scan_content(text: str, *, where: str = "response") -> List[Dict[str, Any]]:
    """Flag patterns in retrieved content that deserve a human look.

    ``where`` labels the region scanned ("response", "hidden text", "search
    results") so a flag raised against text hidden from human readers reads
    differently from the same phrase in the visible body.
    """
    if not text:
        return []
    flags: List[Dict[str, Any]] = []
    for code, severity, pattern, explanation in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        # Content hidden from human readers is a strictly worse place to find
        # any of this, so an otherwise-medium pattern escalates there.
        if where == "hidden text" and severity == "medium":
            severity = "high"
        flags.append({
            "code": code,
            "severity": severity,
            "where": where,
            "detail": explanation,
            "excerpt": _excerpt(text, match.start(), match.end()),
            "count": len(pattern.findall(text)),
        })
        if len(flags) >= MAX_FLAGS:
            return flags

    zw = len(_ZERO_WIDTH.findall(text))
    if zw >= _ZERO_WIDTH_THRESHOLD:
        flags.append({
            "code": "obfuscation.zero_width",
            "severity": "medium",
            "where": where,
            "detail": f"{zw} zero-width/bidi control characters — invisible to a reader, "
                      "legible to a model.",
            "excerpt": "",
            "count": zw,
        })
    return flags[:MAX_FLAGS]


def max_severity(flags: List[Dict[str, Any]]) -> str:
    worst = "none"
    for f in flags:
        if SEVERITY_ORDER.get(f.get("severity", "none"), 0) > SEVERITY_ORDER[worst]:
            worst = f["severity"]
    return worst


# ── Call record ───────────────────────────────────────────────────────────────

class WebCall:
    """Builder for one log entry. Never raises into the caller."""

    def __init__(self, kind: str, **fields: Any):
        self.started = time.monotonic()
        self.data: Dict[str, Any] = {
            "id": uuid4().hex,
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,             # "search" | "fetch"
            "status": "ok",           # ok | refused | error | not_configured
            "query": None,
            "url": None,
            "final_url": None,
            "redirects": [],
            "provider": None,
            "http_status": None,
            "content_type": None,
            "result_count": None,
            "blocked_results": 0,
            "cache_hit": False,
            "response_bytes": None,
            "returned_chars": 0,
            "truncated": False,
            "error": None,
            "flags": [],
            "body": "",
            "hidden_text": "",
            **_caller_context(),
        }
        self.data.update(fields)

    def set(self, **fields: Any) -> "WebCall":
        self.data.update(fields)
        return self

    def add_flag(self, code: str, severity: str, detail: str, *, excerpt: str = "") -> "WebCall":
        """Attach a flag the tool itself observed (a refusal, a redirect, an HTTP error)."""
        self.data["flags"].append({
            "code": code, "severity": severity, "where": "request",
            "detail": detail, "excerpt": excerpt, "count": 1,
        })
        return self

    def scan(self, text: str, *, where: str = "response") -> "WebCall":
        try:
            self.data["flags"].extend(scan_content(text, where=where))
        except Exception as e:  # a heuristic must never break a web call
            log.debug("web_log scan failed: %s", e)
        return self

    def finish(self, returned: str) -> str:
        """Record the call and return ``returned`` unchanged, so callers can
        ``return call.finish(result)`` at every exit point."""
        try:
            self.data["duration_ms"] = int((time.monotonic() - self.started) * 1000)
            self.data["returned_chars"] = len(returned or "")
            self.data["flags"] = self.data["flags"][:MAX_FLAGS]
            self.data["max_severity"] = max_severity(self.data["flags"])
            if not self.data["body"]:
                self.data["body"] = returned or ""
            append(self.data)
        except Exception as e:
            log.debug("web_log append failed: %s", e)
        return returned


def _caller_context() -> Dict[str, Any]:
    """Who made this call — workspace, agent, session, task. Best effort."""
    ctx: Dict[str, Any] = {
        "workspace": None, "agent_id": None, "session_id": None, "task_id": None,
    }
    try:
        from common.workspace_context import resolve_active_workspace
        ctx["workspace"] = resolve_active_workspace()
    except Exception:
        pass
    try:
        from common.agent_context import current_agent_id, current_session_id, current_task_id
        ctx["agent_id"] = current_agent_id.get()
        ctx["session_id"] = current_session_id.get()
        ctx["task_id"] = current_task_id.get()
    except Exception:
        pass
    return ctx


# ── Storage ───────────────────────────────────────────────────────────────────

def _config() -> tuple:
    try:
        from common.config import settings
        return (
            bool(settings.web_log_enabled),
            int(settings.web_log_max_entries),
            int(settings.web_log_body_chars),
        )
    except Exception:
        return True, 2000, 20_000


def _truncate_bodies(record: Dict[str, Any], body_chars: int) -> Dict[str, Any]:
    out = dict(record)
    for field in ("body", "hidden_text"):
        value = out.get(field) or ""
        if len(value) > body_chars:
            out[field] = value[:body_chars] + f"\n\n[log preview truncated at {body_chars} characters]"
            out[f"{field}_truncated"] = True
    return out


def _ensure_legacy_imported() -> None:
    """Import ``web_requests.jsonl`` once, one document per line, keyed by
    each entry's own ``id``.

    A store that already has rows is left alone (:meth:`DocStore.import_legacy`
    re-checks this itself, atomically); the cheap existence check here just
    avoids reading and parsing the file on every call once it is gone.
    """
    if not WEB_LOG_FILE.exists():
        return
    if _store.count() > 0:
        return
    try:
        text = WEB_LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    docs: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("id"):
            docs[str(obj["id"])] = obj
    _store.import_legacy(docs, WEB_LOG_FILE)


def append(record: Dict[str, Any]) -> None:
    enabled, max_entries, body_chars = _config()
    if not enabled:
        return
    _ensure_legacy_imported()
    payload = _truncate_bodies(record, body_chars)
    key = str(payload.get("id") or uuid4().hex)
    payload.setdefault("id", key)
    with _store.transaction():
        _store.put(key, payload)
        keys = _store.keys()
        overflow = len(keys) - max_entries
        if overflow > 0:
            for old_key in keys[:overflow]:
                _store.delete(old_key)


def _read_all() -> List[Dict[str, Any]]:
    _ensure_legacy_imported()
    return list(_store.values())


def _summarize(record: Dict[str, Any]) -> Dict[str, Any]:
    """List-row shape: everything except the stored bodies."""
    row = {k: v for k, v in record.items() if k not in ("body", "hidden_text")}
    row["body_chars"] = len(record.get("body") or "")
    row["flag_count"] = len(record.get("flags") or [])
    row["flag_codes"] = sorted({f.get("code") for f in (record.get("flags") or []) if f.get("code")})
    row.pop("flags", None)
    return row


def query(
    *,
    limit: int = 200,
    offset: int = 0,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    min_severity: Optional[str] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Newest-first page of log rows plus the totals for the current filter."""
    records = list(reversed(_read_all()))
    floor = SEVERITY_ORDER.get((min_severity or "none").lower(), 0)
    needle = (search or "").strip().lower()

    def keep(r: Dict[str, Any]) -> bool:
        if kind and r.get("kind") != kind:
            return False
        if status and r.get("status") != status:
            return False
        if workspace and (r.get("workspace") or "") != workspace:
            return False
        if agent_id and (r.get("agent_id") or "") != agent_id:
            return False
        if floor and SEVERITY_ORDER.get(r.get("max_severity") or "none", 0) < floor:
            return False
        if needle:
            hay = " ".join(str(r.get(f) or "") for f in
                           ("query", "url", "final_url", "agent_id", "provider", "error"))
            if needle not in hay.lower():
                return False
        return True

    matched = [r for r in records if keep(r)]
    page = matched[offset: offset + max(1, min(limit, 500))]
    return {
        "items": [_summarize(r) for r in page],
        "total": len(matched),
        "offset": offset,
        "limit": limit,
    }


def get(entry_id: str) -> Optional[Dict[str, Any]]:
    _ensure_legacy_imported()
    return _store.get(entry_id)


def stats() -> Dict[str, Any]:
    """Counters for the log header: volume, failures, flagged calls, top hosts."""
    records = _read_all()
    by_kind: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    by_severity: Dict[str, int] = {"none": 0, "low": 0, "medium": 0, "high": 0}
    hosts: Dict[str, int] = {}
    flag_codes: Dict[str, int] = {}
    for r in records:
        by_kind[r.get("kind") or "?"] = by_kind.get(r.get("kind") or "?", 0) + 1
        by_status[r.get("status") or "?"] = by_status.get(r.get("status") or "?", 0) + 1
        sev = r.get("max_severity") or "none"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        url = r.get("final_url") or r.get("url")
        if url:
            host = (urlparse(url).hostname or "").lower()
            if host:
                hosts[host] = hosts.get(host, 0) + 1
        for code in (r.get("flag_codes") or []):
            flag_codes[code] = flag_codes.get(code, 0) + 1
        for f in (r.get("flags") or []):
            code = f.get("code")
            if code:
                flag_codes[code] = flag_codes.get(code, 0) + 1
    return {
        "total": len(records),
        "by_kind": by_kind,
        "by_status": by_status,
        "by_severity": by_severity,
        "flagged": sum(v for k, v in by_severity.items() if k != "none"),
        "top_hosts": sorted(
            ({"host": h, "count": c} for h, c in hosts.items()),
            key=lambda x: x["count"], reverse=True,
        )[:10],
        "top_flags": sorted(
            ({"code": c, "count": n} for c, n in flag_codes.items()),
            key=lambda x: x["count"], reverse=True,
        )[:10],
        "enabled": _config()[0],
    }


def clear() -> int:
    """Delete every entry. Returns how many were removed."""
    _ensure_legacy_imported()
    try:
        return _store.clear()
    except Exception as e:
        log.warning("web_log clear failed: %s", e)
        return 0


__all__ = [
    "WebCall", "scan_content", "max_severity", "append", "query", "get", "stats", "clear",
    "SEVERITY_ORDER",
]
