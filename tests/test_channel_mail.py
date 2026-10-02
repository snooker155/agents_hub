"""
The mail channel: IMAP polling, SMTP replies, parsing.

Nothing here opens a socket: ``connectors.mail.service.open_imap`` and
``open_smtp`` are monkeypatched to fakes that serve canned RFC822 bytes and
record what was sent, the same seam ``connectors/mail/imap.py`` and
``smtp.py`` expose for production. Async bodies run with ``asyncio.run`` (no
pytest-asyncio), matching ``tests/test_telegram_allowlist.py`` and
``tests/test_channels_core.py``.

Run: ``python -m pytest tests/test_channel_mail.py -q``
"""
from __future__ import annotations

import asyncio
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.channels.store import ChannelStore  # noqa: E402
from connectors.channels.turns import TurnResult  # noqa: E402
from connectors.mail.parse import parse_message, strip_quoted  # noqa: E402
from connectors.mail.service import MailService, _reply_subject  # noqa: E402


# ── building raw RFC822 fixtures ────────────────────────────────────────────

def _multipart_mail(
    *, from_addr: str = "alice@example.com", from_name: str = "Alice",
    subject: str = "Hello", message_id: str = "<m1@example.com>",
    plain: str = "Hi there.\n\n> quoted old text\nOn Mon, Jan 1, 2024, Bob wrote:\nold stuff",
    with_html: bool = True, with_attachment: bool = True,
    headers: Optional[dict[str, str]] = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = "bot@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 01 Jan 2024 10:00:00 +0000"
    for key, value in (headers or {}).items():
        msg[key] = value
    msg.set_content(plain)
    if with_html:
        msg.add_alternative(f"<p>{plain.splitlines()[0]}</p>", subtype="html")
    if with_attachment:
        msg.add_attachment(b"file-bytes-here", maintype="text", subtype="plain",
                            filename="notes.txt")
    return bytes(msg)


# ── parse.py ─────────────────────────────────────────────────────────────────

def test_multipart_message_prefers_plain_text_and_keeps_the_attachment():
    raw = _multipart_mail()
    parsed = parse_message(raw)
    assert parsed["from_addr"] == "alice@example.com"
    assert parsed["from_name"] == "Alice"
    assert parsed["subject"] == "Hello"
    assert parsed["message_id"] == "<m1@example.com>"
    assert "Hi there." in parsed["text"]
    assert len(parsed["attachments"]) == 1
    att = parsed["attachments"][0]
    assert att["filename"] == "notes.txt"
    assert att["mime_type"] == "text/plain"
    assert att["store_to_workspace"] is True
    import base64
    assert base64.b64decode(att["content_b64"]) == b"file-bytes-here"


def test_quoted_history_is_stripped():
    raw = _multipart_mail(plain="My answer.\n\n> old line one\n> old line two\nOn Mon, Jan 1, 2024, Bob wrote:\nancient text")
    parsed = parse_message(raw)
    assert parsed["text"] == "My answer."
    assert "old line" not in parsed["text"]
    assert "ancient text" not in parsed["text"]


def test_strip_quoted_handles_original_message_marker():
    text = "Reply text\n-----Original Message-----\nFrom: bob@example.com\nOld body"
    assert strip_quoted(text) == "Reply text"


def test_html_only_message_is_stripped_to_text():
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Hi"
    msg["Message-ID"] = "<m2@example.com>"
    msg.add_header("Content-Type", "text/html")
    msg.set_content("<p>Hello <b>world</b></p>", subtype="html")
    parsed = parse_message(bytes(msg))
    assert "Hello" in parsed["text"] and "world" in parsed["text"]
    assert "<p>" not in parsed["text"]


def test_auto_reply_is_flagged():
    raw = _multipart_mail(headers={"Auto-Submitted": "auto-replied"}, with_html=False, with_attachment=False)
    parsed = parse_message(raw)
    assert parsed["auto_reply"] is True


def test_normal_message_is_not_an_auto_reply():
    raw = _multipart_mail(with_html=False, with_attachment=False)
    parsed = parse_message(raw)
    assert parsed["auto_reply"] is False


def test_reply_subject_does_not_double_the_prefix():
    assert _reply_subject("quarterly numbers", "Re:") == "Re: quarterly numbers"
    assert _reply_subject("Re: quarterly numbers", "Re:") == "Re: quarterly numbers"
    assert _reply_subject("RE: quarterly numbers", "Re:") == "RE: quarterly numbers"
    assert _reply_subject("", "Re:") == "Re:"


# ── fakes for the IMAP / SMTP seams ─────────────────────────────────────────

class FakeImap:
    """Stands in for ``imap.ImapClient``: a fixed set of {uid: raw} messages."""

    def __init__(self, messages: dict[int, bytes]):
        self.messages = messages
        self.seen: list[int] = []
        self.connected = 0

    def connect(self) -> None:
        self.connected += 1

    def logout(self) -> None:
        pass

    def search_uids_since(self, last_uid: int) -> list[int]:
        return sorted(uid for uid in self.messages if uid > last_uid)

    def fetch_rfc822(self, uid: int) -> bytes:
        return self.messages[uid]

    def mark_seen(self, uid: int) -> None:
        self.seen.append(uid)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.logout()


class FakeSmtp:
    """Stands in for ``smtp.SmtpClient``: records every sent message."""

    def __init__(self, config: Any = None):
        self.config = config
        self.sent: list[EmailMessage] = []
        self.connected = 0

    def connect(self) -> None:
        self.connected += 1

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def quit(self) -> None:
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.quit()


def _config() -> dict[str, Any]:
    return {
        "imap_host": "imap.example.com", "imap_user": "bot@example.com",
        "imap_password": "secret", "smtp_host": "smtp.example.com",
        "from_address": "bot@example.com",
    }


def _service(monkeypatch, *, imap_messages: Optional[dict[int, bytes]] = None,
             smtp: Optional[FakeSmtp] = None) -> tuple[MailService, FakeImap, FakeSmtp]:
    store = ChannelStore("mail-test", secret_fields=("imap_password", "smtp_password"),
                          defaults={"subject_prefix": "Re:", "poll_seconds": 60})
    store.set_config(_config())
    store.set_enabled(True)
    svc = MailService(store)

    fake_imap = FakeImap(imap_messages or {})
    fake_smtp = smtp if smtp is not None else FakeSmtp()

    import connectors.mail.service as service_mod
    monkeypatch.setattr(service_mod, "open_imap", lambda cfg: fake_imap)
    monkeypatch.setattr(service_mod, "open_smtp", lambda cfg: fake_smtp)
    return svc, fake_imap, fake_smtp


# ── _connect ─────────────────────────────────────────────────────────────────

def test_connect_sets_identity_from_address(monkeypatch):
    svc, fake_imap, fake_smtp = _service(monkeypatch)
    asyncio.run(svc._connect())
    assert svc._status["identity"] == "bot@example.com"
    assert fake_imap.connected == 1
    assert fake_smtp.connected == 1


# ── cursor advance ───────────────────────────────────────────────────────────

def test_cursor_advances_and_a_second_poll_fetches_nothing_new(monkeypatch):
    raw1 = _multipart_mail(from_addr="carol@example.com", message_id="<a@x>")
    raw2 = _multipart_mail(from_addr="carol@example.com", message_id="<b@x>")
    svc, fake_imap, _ = _service(monkeypatch, imap_messages={1: raw1, 2: raw2})
    svc.store.set_allowed(["carol@example.com"])

    first = svc._fetch_new_messages()
    assert [uid for _, uid in first] == [1, 2]
    assert fake_imap.seen == [1, 2]
    for _, uid in first:
        svc.store.set_cursor("last_uid", uid)

    second = svc._fetch_new_messages()
    assert second == []


# ── allowlist ────────────────────────────────────────────────────────────────

def test_sender_not_on_allowlist_is_dropped(monkeypatch):
    raw = _multipart_mail(from_addr="stranger@example.com")
    svc, _, fake_smtp = _service(monkeypatch)
    svc.store.set_allowed(["someone-else@example.com"])

    result = asyncio.run(svc._handle_raw_message(raw))
    assert result is None
    assert fake_smtp.sent == []


def test_domain_allowlist_entry_matches_any_sender_at_that_domain(monkeypatch):
    raw = _multipart_mail(from_addr="bob@example.com")
    svc, _, fake_smtp = _service(monkeypatch)
    svc.store.set_allowed(["@example.com"])

    asyncio.run(svc._handle_raw_message(raw))
    # No binding yet: the unbound reply still goes out, proving the message
    # passed the allowlist gate.
    assert len(fake_smtp.sent) == 1
    assert fake_smtp.sent[0]["To"] == "bob@example.com"


# ── unbound sender ───────────────────────────────────────────────────────────

def test_unbound_sender_gets_the_unbound_reply_threaded(monkeypatch):
    raw = _multipart_mail(from_addr="dana@example.com", subject="Need help",
                           message_id="<thread-1@x>")
    svc, _, fake_smtp = _service(monkeypatch)
    svc.store.set_allowed(["dana@example.com"])

    asyncio.run(svc._handle_raw_message(raw))
    assert len(fake_smtp.sent) == 1
    sent = fake_smtp.sent[0]
    assert "operator" in str(sent.get_content()).lower()
    assert sent["In-Reply-To"] == "<thread-1@x>"
    assert sent["Subject"] == "Re: Need help"


# ── bound sender runs a turn ─────────────────────────────────────────────────

def test_bound_sender_runs_a_turn_and_subject_is_not_doubled(monkeypatch):
    raw = _multipart_mail(from_addr="erin@example.com", subject="Re: project status",
                           message_id="<thread-2@x>")
    svc, _, fake_smtp = _service(monkeypatch)
    svc.store.set_allowed(["erin@example.com"])
    svc.store.upsert_binding(chat_key="erin@example.com", agent_id="demo_agent",
                              workspace="demo", conversation_id="conv-1")

    seen: dict[str, Any] = {}

    async def fake_run_turn(store, chat_key, binding, text, attachments=None, **kw):
        seen.update(chat_key=chat_key, text=text, source=kw.get("source"))
        return TurnResult(text="hi", ok=True)

    import connectors.channels.service as channels_service
    monkeypatch.setattr(channels_service, "run_turn", fake_run_turn)

    result = asyncio.run(svc._handle_raw_message(raw))
    assert result.text == "hi"
    assert seen["chat_key"] == "erin@example.com"
    assert seen["source"] == "mail"

    assert len(fake_smtp.sent) == 1
    sent = fake_smtp.sent[0]
    assert sent["Subject"] == "Re: project status"  # not "Re: Re: project status"
    assert sent["In-Reply-To"] == "<thread-2@x>"
    assert str(sent.get_content()).strip() == "hi"


def test_a_command_on_the_first_line_is_answered_without_running_a_turn(monkeypatch):
    raw = _multipart_mail(from_addr="frank@example.com", subject="status check",
                           message_id="<thread-3@x>", plain="/status\n\nSent from my phone")
    svc, _, fake_smtp = _service(monkeypatch)
    svc.store.set_allowed(["frank@example.com"])
    svc.store.upsert_binding(chat_key="frank@example.com", agent_id="demo_agent", workspace="demo")

    called = False

    async def fake_run_turn(*a, **k):
        nonlocal called
        called = True
        return TurnResult(text="should not run", ok=True)

    import connectors.channels.service as channels_service
    monkeypatch.setattr(channels_service, "run_turn", fake_run_turn)

    asyncio.run(svc._handle_raw_message(raw))
    assert called is False
    assert len(fake_smtp.sent) == 1
    assert "demo" in str(fake_smtp.sent[0].get_content())


# ── sending ──────────────────────────────────────────────────────────────────

def test_send_text_sync_sends_via_smtp(monkeypatch):
    svc, _, fake_smtp = _service(monkeypatch)
    svc.send_text_sync("gina@example.com", "Hello there")
    assert len(fake_smtp.sent) == 1
    sent = fake_smtp.sent[0]
    assert sent["To"] == "gina@example.com"
    assert str(sent.get_content()).strip() == "Hello there"
    assert sent["Subject"] == "Message from Agents Hub"


def test_send_result_renders_buttons_as_a_numbered_list(monkeypatch):
    svc, _, fake_smtp = _service(monkeypatch)
    result = TurnResult(text="Pick one", ok=True, response_obj={
        "kind": "buttons",
        "buttons": [{"label": "Yes"}, {"label": "No"}],
    })
    asyncio.run(svc.send_result("hank@example.com", result))
    body = str(fake_smtp.sent[0].get_content())
    assert "1. Yes" in body and "2. No" in body
