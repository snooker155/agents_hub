"""
Outbound Telegram notifications (proactive, not request/response).

The Telegram poller (``telegram_runner``) is inbound-driven: a user messages
the bot and the bound agent replies. This module is the other direction — it
lets the notification layer *push* a message into the Telegram chats bound in
a workspace, e.g. when a scheduled job fires or an agent calls ``notify_user``.

It is deliberately synchronous and self-contained (only ``httpx`` + the
file-backed ``telegram_store``) so it works identically from the backend
process, the plan scheduler thread, and agent subprocesses — none of which
share the poller's event loop. Delivery is best-effort: any failure is logged
and swallowed so it never breaks inbox persistence, which stays the source of
truth.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from connectors.telegram import telegram_store

log = logging.getLogger("telegram.notify")

_TG_API = "https://api.telegram.org"
_MAX_LEN = 4000  # keep parity with the poller's outbound chunk size


def _chunk(text: str, limit: int = _MAX_LEN) -> list[str]:
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def chat_ids_for_workspace(workspace: Optional[str]) -> list[int]:
    """Chat IDs eligible for a workspace's notifications.

    A binding with no workspace, or one matching the target workspace, is
    eligible. When ``workspace`` is falsy, every bound chat is eligible.
    """
    target = (workspace or "").strip()
    out: list[int] = []
    for b in telegram_store.list_bindings():
        try:
            cid = int(b.get("chat_id", 0))
        except (TypeError, ValueError):
            continue
        if not cid:
            continue
        bws = (b.get("workspace") or "").strip()
        if not target or not bws or bws == target:
            out.append(cid)
    return out


def _send_text(token: str, chat_id: int, text: str) -> bool:
    base = f"{_TG_API}/bot{token}"
    ok = True
    for chunk in _chunk(text):
        try:
            resp = httpx.post(
                f"{base}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                timeout=10.0,
            )
            body = resp.json()
            if not body.get("ok"):
                ok = False
                log.warning("telegram sendMessage failed chat=%s: %s", chat_id, body.get("description"))
        except Exception as exc:
            ok = False
            log.warning("telegram sendMessage error chat=%s: %s", chat_id, exc)
    return ok


def notify_workspace(workspace: Optional[str], title: str, body: str = "") -> int:
    """Push a notification to every bound chat in ``workspace``.

    Returns the number of chats successfully notified. A no-op returning 0 when
    Telegram is disabled, has no token, or no chats are bound — callers can
    treat this as best-effort and ignore the result.
    """
    if not telegram_store.is_enabled() or not telegram_store.has_token():
        return 0
    token = telegram_store.get_token().strip()
    if not token:
        return 0
    text = title if not body else f"{title}\n\n{body}"
    sent = 0
    for cid in chat_ids_for_workspace(workspace):
        if _send_text(token, cid, text):
            sent += 1
    return sent


__all__ = ["chat_ids_for_workspace", "notify_workspace"]
