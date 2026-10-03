"""
Signing in to Gmail with the connected Google account instead of a password.

Both mail paths use it: the mail channel (``imap.py`` and ``smtp.py`` here,
when the channel's ``auth_mode`` is ``google``) and the IMAP watcher
(``watchers/kinds.py``, when ``use_google`` is on). The token is the Google
connector's OAuth refresh token's (``connectors/google/auth.py``,
:func:`gmail_login`), which needs the Gmail scope granted through "Connect
with Gmail" on the Connectors page. The mailbox is always the connected
account's: XOAUTH2 names the user, and Gmail refuses a token for anybody
else.

The SASL exchange: the client sends ``user=<address>^Aauth=Bearer
<token>^A^A``. On success the server says OK; on failure Gmail first sends a
continuation carrying a JSON error, which the client answers with an empty
line to get the final NO. The two authenticators below do exactly that.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

#: Gmail's servers; a Google sign in fills them when the form left them empty.
GMAIL_IMAP = ("imap.gmail.com", 993)
GMAIL_SMTP = ("smtp.gmail.com", 587, "starttls")

LoginFn = Callable[[], tuple[str, str]]


class MailOAuthError(Exception):
    """The connected Google account cannot sign in; the message is UI-safe."""


def google_login(login: Optional[LoginFn] = None) -> tuple[str, str]:
    """``(address, token)`` from the Google connector, or :class:`MailOAuthError`."""
    if login is not None:
        return login()
    from connectors.google.auth import GoogleError, gmail_login
    try:
        return gmail_login()
    except GoogleError as exc:
        raise MailOAuthError(str(exc)) from exc


def check_address(wanted: str, address: str) -> None:
    """Refuse a configured mailbox that is not the connected account's."""
    if wanted and wanted.strip().lower() != address.strip().lower():
        raise MailOAuthError(
            f"Signing in with Google reaches {address} only, not {wanted}. "
            "Leave the user empty or connect that account instead.")


def _xoauth2(address: str, token: str) -> str:
    from connectors.google.auth import xoauth2_string
    return xoauth2_string(address, token)


def imap_authenticate(conn: Any, address: str, token: str) -> None:
    """XOAUTH2 on an ``imaplib`` connection (``IMAP4.authenticate``)."""
    payload = _xoauth2(address, token).encode("utf-8")
    sent = [False]

    def _respond(_challenge: bytes) -> bytes:
        # First continuation: the credentials. A second one carries Gmail's
        # error details; an empty answer ends the exchange with NO.
        if sent[0]:
            return b""
        sent[0] = True
        return payload

    conn.authenticate("XOAUTH2", _respond)


def smtp_authenticate(conn: Any, address: str, token: str) -> None:
    """XOAUTH2 on an ``smtplib`` connection, after STARTTLS when there is one."""
    payload = _xoauth2(address, token)

    def _respond(challenge: Optional[bytes] = None) -> str:
        # Called with no argument for the initial response, and with the
        # server's challenge (Gmail's JSON error) on a 334, answered empty.
        return "" if challenge else payload

    conn.ehlo_or_helo_if_needed()
    conn.auth("XOAUTH2", _respond, initial_response_ok=True)


__all__ = [
    "GMAIL_IMAP", "GMAIL_SMTP", "MailOAuthError", "google_login", "check_address",
    "imap_authenticate", "smtp_authenticate",
]
