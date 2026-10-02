"""
The SMTP side of the mail channel: a thin wrapper over stdlib ``smtplib``.

The mirror of ``imap.py`` for the outbound half of a mailbox, usually a
different host and credentials from the inbound side. ``service.py`` calls
:func:`open_smtp` with the channel's config dict and gets back an
:class:`SmtpClient`, used as a context manager (connect, optionally STARTTLS
and login, on enter; quit on exit) inside ``asyncio.to_thread``. Tests
replace :func:`open_smtp` with a fake that records sent messages instead of
opening a socket.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Callable, Optional


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    #: "starttls" (default), "ssl" or "none".
    security: str
    from_address: str


def config_from_dict(cfg: dict[str, Any]) -> SmtpConfig:
    return SmtpConfig(
        host=str(cfg.get("smtp_host") or "").strip(),
        port=int(cfg.get("smtp_port") or 587),
        user=str(cfg.get("smtp_user") or "").strip(),
        password=str(cfg.get("smtp_password") or ""),
        security=(str(cfg.get("smtp_security") or "starttls").strip().lower() or "starttls"),
        from_address=str(cfg.get("from_address") or "").strip(),
    )


def _default_connection(config: SmtpConfig) -> Any:
    if config.security == "ssl":
        return smtplib.SMTP_SSL(config.host, config.port, timeout=30)
    return smtplib.SMTP(config.host, config.port, timeout=30)


class SmtpClient:
    """One SMTP session. Every method is synchronous; the caller runs it on
    a worker thread. ``connection_factory`` is the seam tests replace."""

    def __init__(self, config: SmtpConfig, *,
                 connection_factory: Optional[Callable[[SmtpConfig], Any]] = None) -> None:
        self.config = config
        self._factory = connection_factory or _default_connection
        self._conn: Any = None

    def connect(self) -> None:
        self._conn = self._factory(self.config)
        if self.config.security == "starttls":
            self._conn.starttls()
        if self.config.user:
            self._conn.login(self.config.user, self.config.password)

    def send(self, message: EmailMessage) -> None:
        if self._conn is None:
            raise RuntimeError("SMTP client is not connected")
        self._conn.send_message(message)

    def quit(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.quit()
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        self._conn = None

    def __enter__(self) -> "SmtpClient":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.quit()


def open_smtp(config: dict[str, Any]) -> SmtpClient:
    """Build a session from a channel config dict. Tests monkeypatch this."""
    return SmtpClient(config_from_dict(config))


__all__ = ["SmtpClient", "SmtpConfig", "config_from_dict", "open_smtp"]
