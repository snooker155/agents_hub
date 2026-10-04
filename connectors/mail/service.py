"""
The mail channel's ``ChannelService``: an IMAP poll loop in, SMTP replies out.

Where Telegram holds a long-polling HTTPS connection and Slack/Discord hold a
websocket, mail has no push transport at all: ``_run`` wakes up every
``poll_seconds``, asks the mailbox for UIDs past the stored cursor
(``imap.py``), fetches and parses each one (``parse.py``), and feeds it to
:meth:`ChannelService.handle_message` exactly like every other channel. A
sender's lowercased address is the chat key, so the allowlist
(``connectors/channels/store.py``) is a list of addresses, extended here to
also accept a ``@domain`` entry.

Sending is the other half: :meth:`send_text` builds a ``text/plain``
``EmailMessage`` from the channel's ``from_address`` and, when a
``reply_to`` is given (message id, subject, references), sets ``In-Reply-To``
and ``References`` so mail clients thread the reply under the original
message instead of starting a new conversation.

With a Google sign in (``auth_mode: google``) a workspace's own mail bot
signs in with the Google connector in effect in its workspace (that
workspace's own, else the default's), and the default workspace's bot with
the default's (docs/connectors.md "Connectors per workspace").
"""
from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage
from typing import Any, Optional

from connectors.channels.commands import is_command
from connectors.channels.service import ChannelService
from connectors.channels.turns import TurnResult

from .imap import open_imap
from .parse import is_system_sender, parse_message
from .smtp import open_smtp

log = logging.getLogger("channels.mail")

_DEFAULT_SUBJECT = "Message from Agents Hub"


def _reply_subject(original_subject: str, prefix: str) -> str:
    """The subject for a reply: ``prefix`` once, never doubled.

    A sender's ``Re: quarterly numbers`` stays ``Re: quarterly numbers``; a
    bare ``quarterly numbers`` becomes ``Re: quarterly numbers``. Comparison
    is case-insensitive since mail clients are inconsistent about casing.
    """
    subject = (original_subject or "").strip()
    prefix = (prefix or "").strip()
    if not prefix:
        return subject or _DEFAULT_SUBJECT
    if not subject:
        return prefix
    if subject.lower().startswith(prefix.lower()):
        return subject
    return f"{prefix} {subject}".strip()


#: What a password sign in cannot start without.
_PASSWORD_REQUIRED = ("imap_host", "imap_user", "imap_password", "smtp_host", "from_address")


def _google_mode(config: dict[str, Any]) -> bool:
    return str(config.get("auth_mode") or "").strip().lower() == "google"


def _from_address(config: dict[str, Any], workspace: Optional[str] = None) -> str:
    """The address replies go out from: the configured one, or with a
    Google sign in the account of the Google connector in effect in
    ``workspace`` (the running code's when None) when none is set."""
    configured = str(config.get("from_address") or "").strip()
    if configured or not _google_mode(config):
        return configured
    from connectors.google import store_for
    return str(store_for(workspace).get("account_email") or "").strip()


class MailService(ChannelService):
    name = "mail"

    @property
    def required_fields(self) -> tuple[str, ...]:  # type: ignore[override]
        """With a Google sign in the hosts, user and address all default to
        the connected Gmail account, so ``auth_mode`` alone makes it
        configured; whether Google really grants Gmail shows up on connect,
        as the status card's error."""
        if _google_mode(self.store.get_config()):
            return ("auth_mode",)
        return _PASSWORD_REQUIRED

    # ── connection check ─────────────────────────────────────────────────────

    async def _connect(self) -> None:
        config = self.store.get_config()
        await asyncio.to_thread(self._check_imap, config)
        await asyncio.to_thread(self._check_smtp, config)
        self._status["identity"] = _from_address(config, self.workspace)

    # A workspace's own bot names its workspace, so a Google sign in uses
    # that workspace's Google connector; the default's bot runs under its
    # scope (ChannelService.scope), which is the default workspace's.
    def _open_imap(self, config: dict[str, Any]):
        own = self.own_workspace
        return open_imap(config, workspace=own) if own else open_imap(config)

    def _open_smtp(self, config: dict[str, Any]):
        own = self.own_workspace
        return open_smtp(config, workspace=own) if own else open_smtp(config)

    def _check_imap(self, config: dict[str, Any]) -> None:
        client = self._open_imap(config)
        client.connect()
        client.logout()

    def _check_smtp(self, config: dict[str, Any]) -> None:
        client = self._open_smtp(config)
        client.connect()
        client.quit()

    # ── poll loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self.stopping():
            try:
                fetched = await asyncio.to_thread(self._fetch_new_messages)
                for raw, uid in fetched:
                    try:
                        await self._handle_raw_message(raw)
                    finally:
                        # Advance the cursor even for a message we chose to
                        # ignore (an auto reply, a disallowed sender), so it
                        # is never re-fetched on the next poll.
                        self.store.set_cursor("last_uid", uid)
                self._touch()
                self._status["last_error"] = None
            except Exception as exc:  # noqa: BLE001 - surfaced on the status card
                self._set_error(exc)
                log.warning("mail poll error: %s", exc)
            poll_seconds = float(self.store.get("poll_seconds", 60) or 60)
            await self._sleep(poll_seconds)

    def _fetch_new_messages(self) -> list[tuple[bytes, int]]:
        """Synchronous: runs on a worker thread. Returns (raw, uid) pairs in
        ascending uid order, each already marked ``\\Seen``."""
        config = self.store.get_config()
        last_uid = int(self.store.get_cursor("last_uid", 0) or 0)
        out: list[tuple[bytes, int]] = []
        with self._open_imap(config) as imap:
            for uid in imap.search_uids_since(last_uid):
                try:
                    raw = imap.fetch_rfc822(uid)
                    imap.mark_seen(uid)
                except Exception as exc:  # noqa: BLE001 - one bad message must not stop the poll
                    log.warning("mail fetch failed for uid %s: %s", uid, exc)
                    continue
                out.append((raw, uid))
        return out

    async def _handle_raw_message(self, raw: bytes) -> Optional[TurnResult]:
        parsed = parse_message(raw)
        from_addr = parsed["from_addr"]
        if not from_addr:
            return None

        config = self.store.get_config()
        self_address = _from_address(config, self.workspace).lower()
        if from_addr == self_address:
            return None
        if parsed["auto_reply"] or is_system_sender(from_addr):
            log.info("mail message from %s dropped: auto reply or system sender", from_addr)
            return None

        title = f"{parsed['from_name']} <{from_addr}>" if parsed["from_name"] else from_addr
        reply_to = {
            "message_id": parsed["message_id"],
            "subject": parsed["subject"],
            "references": parsed["references"],
        }

        # Commands live on the first line of the body ("/agent swe_agent",
        # then a human sign-off below it); only that line is checked, so a
        # command isn't lost in a signature and a signature isn't mistaken
        # for one when it comes right after.
        body = parsed["text"] or ""
        first_line = body.partition("\n")[0].strip()
        message_text = first_line if is_command(first_line) else body

        return await self.handle_message(
            from_addr, message_text,
            attachments=parsed["attachments"],
            title=title,
            reply_to=reply_to,
        )

    # ── allowlist: addresses, plus a "@domain" entry for a whole domain ────────

    def chat_allowed(self, chat_key: str) -> bool:
        key = str(chat_key or "").strip().lower()
        if not key:
            return False
        if self.store.is_allowed(key):
            return True
        domain = key.rsplit("@", 1)[-1] if "@" in key else ""
        if domain:
            for entry in self.store.get_allowed():
                entry = str(entry or "").strip().lower()
                if entry.startswith("@") and entry[1:] == domain:
                    return True
        if key not in self._logged_disallowed:
            self._logged_disallowed.add(key)
            log.info("mail message dropped: sender %s is not on the allowlist", key)
        return False

    # ── outbound ─────────────────────────────────────────────────────────────

    def _build_message(self, chat_key: str, text: str,
                        reply_to: Optional[dict[str, Any]]) -> EmailMessage:
        config = self.store.get_config()
        from_address = _from_address(config, self.workspace)
        prefix = str(config.get("subject_prefix") or "Re:")
        reply_to = reply_to or {}

        message = EmailMessage()
        message["From"] = from_address
        message["To"] = chat_key
        subject = reply_to.get("subject")
        message["Subject"] = _reply_subject(subject, prefix) if subject else _DEFAULT_SUBJECT

        message_id = str(reply_to.get("message_id") or "").strip()
        if message_id:
            message["In-Reply-To"] = message_id
            references = str(reply_to.get("references") or "").strip()
            if references and message_id not in references:
                message["References"] = f"{references} {message_id}"
            else:
                message["References"] = references or message_id

        message.set_content(text or "")
        return message

    def _send_sync(self, chat_key: str, text: str, reply_to: Optional[dict[str, Any]]) -> None:
        message = self._build_message(chat_key, text, reply_to)
        config = self.store.get_config()
        with self._open_smtp(config) as smtp:
            smtp.send(message)

    async def send_text(self, chat_key: str, text: str, **kwargs: Any) -> None:
        reply_to = kwargs.get("reply_to")
        await asyncio.to_thread(self._send_sync, chat_key, text, reply_to)

    def send_text_sync(self, chat_key: str, text: str, **kwargs: Any) -> None:
        """Used for outbound notifications (``connectors/channels/notify.py``),
        called from a worker thread that has no event loop of its own."""
        self._send_sync(chat_key, text, kwargs.get("reply_to"))

    async def send_result(self, chat_key: str, result: TurnResult, **kwargs: Any) -> None:
        text = result.reply
        obj = result.response_obj
        if isinstance(obj, dict) and obj.get("kind") == "buttons":
            buttons = obj.get("buttons") or []
            if buttons:
                options = "\n".join(
                    f"{i}. {b.get('label') or b.get('text') or b.get('value') or ''}"
                    for i, b in enumerate(buttons, start=1)
                )
                text = f"{text}\n\nReply with one of:\n{options}".strip()
        await self.send_text(chat_key, text, **kwargs)


__all__ = ["MailService"]
