"""
The IMAP side of the mail channel: a thin wrapper over stdlib ``imaplib``.

``service.py`` never touches ``imaplib`` directly. It calls :func:`open_imap`
with the channel's config dict and gets back an :class:`ImapClient`, used as
a context manager (connect on enter, logout on exit) inside
``asyncio.to_thread`` since ``imaplib`` is blocking. Tests replace
:func:`open_imap` with a fake that serves canned messages from memory, so no
socket is ever opened.

The poll loop asks for UIDs above a stored cursor (``UID SEARCH UID
n+1:*``) rather than ``UNSEEN``, so a message a human already read in another
client is still picked up, and a restart never reprocesses old mail.
"""
from __future__ import annotations

import imaplib
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class ImapConfig:
    host: str
    port: int
    user: str
    password: str
    folder: str
    ssl: bool


def config_from_dict(cfg: dict[str, Any]) -> ImapConfig:
    ssl = str(cfg.get("imap_ssl") if cfg.get("imap_ssl") is not None else "yes").strip().lower() != "no"
    return ImapConfig(
        host=str(cfg.get("imap_host") or "").strip(),
        port=int(cfg.get("imap_port") or 993),
        user=str(cfg.get("imap_user") or "").strip(),
        password=str(cfg.get("imap_password") or ""),
        folder=str(cfg.get("imap_folder") or "INBOX").strip() or "INBOX",
        ssl=ssl,
    )


def _default_connection(config: ImapConfig) -> Any:
    if config.ssl:
        return imaplib.IMAP4_SSL(config.host, config.port)
    return imaplib.IMAP4(config.host, config.port)


class ImapClient:
    """One IMAP session. Every method is synchronous; the caller runs it on
    a worker thread. ``connection_factory`` is the seam tests replace."""

    def __init__(self, config: ImapConfig, *,
                 connection_factory: Optional[Callable[[ImapConfig], Any]] = None) -> None:
        self.config = config
        self._factory = connection_factory or _default_connection
        self._conn: Any = None

    def connect(self) -> None:
        self._conn = self._factory(self.config)
        self._conn.login(self.config.user, self.config.password)
        self._conn.select(self.config.folder)

    def logout(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        try:
            self._conn.logout()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    def search_uids_since(self, last_uid: int) -> list[int]:
        """UIDs strictly greater than ``last_uid`` in the selected folder."""
        typ, data = self._conn.uid("search", None, f"UID {int(last_uid) + 1}:*")
        if typ != "OK" or not data or not data[0]:
            return []
        found = {int(x) for x in data[0].split() if x.isdigit()}
        return sorted(u for u in found if u > last_uid)

    def fetch_rfc822(self, uid: int) -> bytes:
        typ, data = self._conn.uid("fetch", str(uid), "(RFC822)")
        if typ != "OK" or not data:
            raise RuntimeError(f"IMAP fetch failed for uid {uid}")
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                return part[1]
        raise RuntimeError(f"IMAP fetch returned no body for uid {uid}")

    def mark_seen(self, uid: int) -> None:
        self._conn.uid("store", str(uid), "+FLAGS", r"(\Seen)")

    def __enter__(self) -> "ImapClient":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.logout()


def open_imap(config: dict[str, Any]) -> ImapClient:
    """Build a session from a channel config dict. Tests monkeypatch this."""
    return ImapClient(config_from_dict(config))


__all__ = ["ImapClient", "ImapConfig", "config_from_dict", "open_imap"]
