"""
Turning an RFC822 byte string into the shape ``service.py`` wants.

The mail channel reads whole messages over IMAP (``imap.py``) and hands the
raw bytes here. :func:`parse_message` picks the plain text part (falling back
to a stripped-down reading of the HTML part), drops quoted reply history so
the agent only sees what the sender actually typed, pulls attachments into
the same dict shape ``chat.models.ChatAttachment`` expects (see
``connectors/telegram/telegram_runner.py::_build_attachments_from_message``),
and reports whether the message looks like an auto reply or a bounce so
``service.py`` can drop it before it ever reaches an agent.

Uses only the ``email`` stdlib package (via ``email.policy.default``, which
gives ``get_body``/``iter_attachments`` for free) plus a small hand rolled
HTML-to-text pass, no third party dependency.
"""
from __future__ import annotations

import base64
import html as html_lib
import re
from email import message_from_bytes
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

#: Per-attachment and whole-message attachment caps, matching the web chat /
#: Telegram limit (5 MB per file) with a total cap so one message can't pull
#: an unbounded amount of memory through the poll loop.
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024

_QUOTE_LINE = re.compile(r"^\s*>")
_ON_WROTE = re.compile(r"^On .* wrote:\s*$")
_ORIGINAL_MESSAGE = re.compile(r"^-----Original Message-----\s*$", re.IGNORECASE)

_AUTO_REPLY_PRECEDENCE = {"bulk", "auto_reply", "list"}
_SYSTEM_SENDER = re.compile(r"mailer-daemon|no-?reply|postmaster", re.IGNORECASE)


def strip_quoted(text: str) -> str:
    """Drop quoted history: '>' lines, and everything from an 'On ... wrote:'
    or '-----Original Message-----' marker onward."""
    out: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if _ON_WROTE.match(stripped) or _ORIGINAL_MESSAGE.match(stripped):
            break
        if _QUOTE_LINE.match(line):
            continue
        out.append(line)
    return "\n".join(out).strip()


def html_to_text(markup: str) -> str:
    """A minimal HTML to text reading: strip tags, unescape entities, keep
    paragraph breaks. Good enough for a reply body, not a renderer."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", markup or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[str] = []
    blank = True
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def is_auto_reply(msg: Any) -> bool:
    """Vacation responders and mailing-list auto replies, by header."""
    auto_submitted = str(msg.get("Auto-Submitted") or "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return True
    precedence = str(msg.get("Precedence") or "").strip().lower()
    if precedence in _AUTO_REPLY_PRECEDENCE:
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    return False


def is_system_sender(address: str) -> bool:
    """Bounces and no-reply addresses: never worth a turn or a reply."""
    return bool(_SYSTEM_SENDER.search(address or ""))


def _part_text(part: Any) -> str:
    try:
        content = part.get_content()
    except Exception:  # noqa: BLE001 - a malformed charset falls back below
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            content = payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            content = payload.decode("utf-8", errors="replace")
    return content if isinstance(content, str) else str(content)


def _extract_body(msg: Any) -> str:
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return _part_text(plain)
    rich = msg.get_body(preferencelist=("html",))
    if rich is not None:
        return html_to_text(_part_text(rich))
    return ""


def _extract_attachments(msg: Any) -> tuple[list[dict[str, Any]], list[str]]:
    attachments: list[dict[str, Any]] = []
    skipped: list[str] = []
    total = 0
    try:
        parts = list(msg.iter_attachments())
    except Exception:  # noqa: BLE001 - a non-MIME message has none
        parts = []
    for part in parts:
        try:
            data = part.get_content()
        except Exception:  # noqa: BLE001
            data = part.get_payload(decode=True) or b""
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray)):
            continue
        name = part.get_filename() or "attachment"
        size = len(data)
        if size > MAX_ATTACHMENT_BYTES or total + size > MAX_TOTAL_ATTACHMENT_BYTES:
            skipped.append(name)
            continue
        total += size
        attachments.append({
            "filename": name,
            "content_b64": base64.b64encode(bytes(data)).decode("ascii"),
            "mime_type": part.get_content_type(),
            "store_to_workspace": True,
        })
    return attachments, skipped


def parse_message(raw: bytes) -> dict[str, Any]:
    """Everything ``service.py`` needs from one RFC822 message."""
    from email.policy import default as default_policy

    msg = message_from_bytes(raw, policy=default_policy)
    from_name, from_addr = parseaddr(str(msg.get("From") or ""))
    from_addr = (from_addr or "").strip().lower()
    body = strip_quoted(_extract_body(msg))
    attachments, skipped = _extract_attachments(msg)

    date_header = msg.get("Date")
    date_iso = None
    if date_header:
        try:
            date_iso = parsedate_to_datetime(str(date_header)).isoformat()
        except Exception:  # noqa: BLE001 - an unparsable Date header is dropped
            date_iso = None

    return {
        "message_id": str(msg.get("Message-ID") or "").strip(),
        "in_reply_to": str(msg.get("In-Reply-To") or "").strip(),
        "references": str(msg.get("References") or "").strip(),
        "subject": str(msg.get("Subject") or "").strip(),
        "from_addr": from_addr,
        "from_name": (from_name or "").strip(),
        "date": date_iso,
        "text": body,
        "attachments": attachments,
        "skipped_attachments": skipped,
        "auto_reply": is_auto_reply(msg),
    }


__all__ = [
    "MAX_ATTACHMENT_BYTES", "MAX_TOTAL_ATTACHMENT_BYTES",
    "strip_quoted", "html_to_text", "is_auto_reply", "is_system_sender",
    "parse_message",
]
