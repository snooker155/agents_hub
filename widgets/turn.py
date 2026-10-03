"""
One visitor message: the chat turn behind it, and what the visitor may see
of it.

The turn is the web chat's own (``chat.pipelines.run_chat_pipeline`` through
:class:`widgets.relay.TurnRelay`): the widget's workspace, the thread's
current agent, the thread as ``conversation_id`` so the runs group into one
conversation on the Messages page, ``source="widget"`` as the run's
``message_origin``, and the widget owner as the acting user. The visitor is
the turn's end user (``widget:<widget_id>:<visitor_id>``), whose own Google or
Microsoft consent the agent acts on (docs/consent.md).

What reaches the visitor is a whitelist, not the pipeline's stream with bits
removed: ``meta`` (the run id), ``token``, ``tool_start``/``tool_end`` with
the tool's name only, ``handoff`` (who took over, and the first agent's
reply), and one ``done`` (the reply, its sources as ``[n]``, ok or a stable
error code). Everything else (thinking, tool inputs and outputs, usage, entity
links, stored paths, provider error text) stays on the hub, where the owner
reads it on the run's page.

The thread is written on the relay's task (:meth:`VisitorTurn.on_event`), so
the reply is kept even when the visitor closed the page mid-answer; a visitor
who pressed stop gets the partial reply kept as ``stopped``.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from . import service, store
from .relay import TurnRelay

log = logging.getLogger(__name__)

#: Seconds of silence after which the stream sends an SSE comment, so a proxy
#: does not cut a turn that is busy in a long tool call.
PING_SECONDS = 15.0

#: The largest part of a cited passage a visitor sees.
SNIPPET_CHARS = 300

#: Extensions read as text and put in the prompt; anything else is binary and
#: is stored in the workspace for the agent's file tools.
_TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".html",
                    ".htm", ".log", ".ini", ".toml", ".py", ".js", ".ts", ".sql", ".css")
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIMES = ("application/json", "application/xml", "application/x-yaml",
               "application/yaml", "application/csv")


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── attachments ──────────────────────────────────────────────────────────────

def decode_attachments(raw: Any, limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate the body's attachments against the widget's limits and turn
    them into ``ChatAttachment`` fields. Raises :class:`service.WidgetError`.

    Each item is ``{name, mime_type, data_b64}``. A text file (by type or
    extension, and valid UTF-8) goes into the prompt; anything else is kept
    as bytes and stored in the workspace's ``chat_uploads`` folder, which is
    how the chat hands a binary upload to an agent's file tools.
    """
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise service.WidgetError(400, "bad_request", "attachments must be a list")
    max_count = int(limits.get("max_attachments") or 0)
    max_bytes = int(limits.get("attachment_max_bytes") or 0)
    if max_count <= 0 or max_bytes <= 0:
        raise service.WidgetError(400, "attachments_disabled", "This widget takes no attachments")
    if len(raw) > max_count:
        raise service.WidgetError(400, "too_many_attachments",
                                  f"At most {max_count} attachments per message")
    from chat.attachments import safe_attachment_filename
    out: List[Dict[str, Any]] = []
    total = 0
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise service.WidgetError(400, "bad_request", "each attachment is an object")
        name = safe_attachment_filename(str(item.get("name") or ""), idx)
        mime = str(item.get("mime_type") or "").strip()[:120] or None
        data = str(item.get("data_b64") or "")
        # A data URL's prefix is tolerated: FileReader hands one back.
        if data.startswith("data:") and "," in data:
            data = data.split(",", 1)[1]
        try:
            blob = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise service.WidgetError(400, "bad_attachment", f"'{name}' is not valid base64")
        if len(blob) > max_bytes:
            raise service.WidgetError(413, "attachment_too_large",
                                      f"'{name}' is larger than {max_bytes} bytes")
        total += len(blob)
        if total > service.MAX_TOTAL_ATTACHMENT_BYTES:
            raise service.WidgetError(413, "attachment_too_large", "The attachments are too large together")
        is_text_kind = ((mime or "").startswith(_TEXT_MIME_PREFIXES) or (mime or "") in _TEXT_MIMES
                        or name.lower().endswith(_TEXT_EXTENSIONS))
        text: Optional[str] = None
        if is_text_kind:
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                text = None
        if text is not None:
            out.append({"filename": name, "content": text, "mime_type": mime})
        else:
            out.append({"filename": name, "content": "", "mime_type": mime,
                        "content_b64": base64.b64encode(blob).decode("ascii"),
                        "store_to_workspace": True})
    return out


# ── what the visitor sees ────────────────────────────────────────────────────

def safe_citations(citations: Any) -> List[Dict[str, Any]]:
    """``[n]`` sources reduced to what a visitor can use: the number, a
    title (the file name and the heading path), a short snippet, and a link
    when the source is a web page. No pool, file or chunk ids."""
    out: List[Dict[str, Any]] = []
    if not isinstance(citations, list):
        return out
    for c in citations:
        if not isinstance(c, dict):
            continue
        try:
            n = int(c.get("n"))
        except (TypeError, ValueError):
            continue
        heading = c.get("heading_path")
        if isinstance(heading, (list, tuple)):
            heading = " › ".join(str(h) for h in heading if h)
        title = str(c.get("filename") or c.get("title") or "").strip()
        if heading:
            title = f"{title} › {heading}" if title else str(heading)
        entry: Dict[str, Any] = {"n": n, "title": title[:200] or f"[{n}]"}
        snippet = str(c.get("snippet") or "").strip()
        if snippet:
            entry["snippet"] = snippet[:SNIPPET_CHARS] + ("…" if len(snippet) > SNIPPET_CHARS else "")
        url = str(c.get("url") or "").strip()
        if url.lower().startswith(("https://", "http://")):
            entry["url"] = url[:1000]
        out.append(entry)
    return out


def _error_code(done: Dict[str, Any]) -> str:
    error = str(done.get("error") or "")
    if error in ("stopped by user", "cancelled") or done.get("response") == "Stopped by user":
        return "stopped"
    return "failed"


def _run_tokens(run_id: Optional[str]) -> int:
    """A run's tokens from its record, for the first half of a handoff turn
    (the ``done`` event's usage is the answering agent's)."""
    if not run_id:
        return 0
    try:
        from managers.run_manager import get_run_process
        usage = (get_run_process(run_id) or {}).get("token_usage") or {}
        return int(usage.get("total_tokens") or 0)
    except Exception:  # noqa: BLE001 - a missing record counts as no tokens, never a failed turn
        return 0


class VisitorTurn:
    """A visitor's message, the relay running it, and the thread it writes to."""

    def __init__(self, widget: Dict[str, Any], thread: Dict[str, Any], *, text: str,
                 attachments: List[Dict[str, Any]]) -> None:
        self.widget = widget
        self.thread = thread
        self.text = text
        self.attachments = attachments
        self.agent_id = service.answering_agent(widget, thread)
        self.user_message: Optional[Dict[str, Any]] = None
        self.reply_message_id: Optional[str] = None
        self._streamed: List[str] = []
        self._tool: Optional[str] = None
        self.relay: Optional[TurnRelay] = None

    # ── start ────────────────────────────────────────────────────────────────

    def _request(self, history: List[Dict[str, str]]):
        from chat.models import ChatAttachment, ChatHistoryMessage, ChatRequest
        names = [a["filename"] for a in self.attachments]
        message = self.text or ("(attached: " + ", ".join(names) + ")" if names else "")
        return ChatRequest(
            agent_id=self.agent_id,
            message=message,
            workspace=self.widget["workspace"],
            history=[ChatHistoryMessage(**h) for h in history],
            conversation_id=self.thread["thread_id"],
            conversation_title=self.thread.get("title") or None,
            attachments=[ChatAttachment(**a) for a in self.attachments],
            source="widget",
            # The widget's version pin, for its own agent only: an agent the
            # thread was handed to answers as it is.
            agent_version=(self.widget.get("agent_version")
                           if self.agent_id == self.widget.get("agent_id") else None),
        )

    def start(self) -> Dict[str, Any]:
        """Record the visitor's message and start the turn. The thread was
        claimed by the caller; it is released when the turn's ``done`` lands."""
        history = service.history_for(self.thread["thread_id"])
        names = [a["filename"] for a in self.attachments]
        self.user_message = store.insert_message(
            thread_id=self.thread["thread_id"], widget_id=self.widget["widget_id"], role="user",
            text=self.text, attachments=names)
        title = None
        if not self.thread.get("title"):
            first = (self.text or ", ".join(names)).strip().replace("\n", " ")
            title = first[:60] + ("…" if len(first) > 60 else "")
        store.touch_thread(self.thread["thread_id"], title=title)
        if title:
            self.thread = dict(self.thread, title=title)
        from common.secrets import widget_principal
        self.relay = TurnRelay(self._request(history), user_id=self.widget["owner_id"],
                               on_event=self.on_event,
                               end_user=widget_principal(self.widget["widget_id"],
                                                         self.thread["visitor_id"])).start()
        return self.user_message

    # ── the relay's task: keep the thread ────────────────────────────────────

    def on_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "token" and not event.get("delegation"):
            self._streamed.append(str(event.get("token") or ""))
        elif kind == "handoff":
            self._record_handoff(event)
        elif kind == "done":
            try:
                self._record_done(event)
            finally:
                service.release_thread(self.thread["thread_id"])

    def _record_handoff(self, event: Dict[str, Any]) -> None:
        text = str(event.get("from_response") or "".join(self._streamed)).strip()
        self._streamed = []
        to_agent = str(event.get("to_agent_id") or "")
        store.insert_message(
            thread_id=self.thread["thread_id"], widget_id=self.widget["widget_id"],
            role="assistant", text=text, run_id=event.get("run_id"),
            agent_id=event.get("from_agent_id") or self.agent_id,
            handoff={"to_agent_id": to_agent,
                     "to_agent_name": event.get("to_agent_name") or service.agent_name(to_agent)},
            tokens=_run_tokens(event.get("run_id")))
        if to_agent:
            self.agent_id = to_agent
            store.touch_thread(self.thread["thread_id"], agent_id=to_agent)

    def _record_done(self, event: Dict[str, Any]) -> None:
        ok = bool(event.get("ok"))
        status = "ok" if ok else _error_code(event)
        if ok:
            text = str(event.get("response") or "")
        elif status == "stopped":
            text = "".join(self._streamed)
        else:
            text = ""
        usage = event.get("usage") or {}
        try:
            tokens = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError, AttributeError):
            tokens = 0
        message = store.insert_message(
            thread_id=self.thread["thread_id"], widget_id=self.widget["widget_id"],
            role="assistant", text=text, run_id=event.get("run_id"),
            agent_id=event.get("agent_id") or self.agent_id,
            citations=safe_citations(event.get("citations")) if ok else [],
            status=status, tokens=tokens)
        self.reply_message_id = message["message_id"]
        store.touch_thread(self.thread["thread_id"])

    # ── the visitor's side ───────────────────────────────────────────────────

    def safe_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = event.get("type")
        if kind == "meta":
            return {"type": "meta", "run_id": event.get("run_id"),
                    "thread_id": self.thread["thread_id"],
                    "user_message_id": (self.user_message or {}).get("message_id")}
        # A delegated agent's events (run_agent_tool) carry ``delegation``:
        # its work is the owner's business, not the visitor's.
        if event.get("delegation"):
            return None
        if kind == "token":
            token = event.get("token")
            return {"type": "token", "token": str(token)} if token else None
        if kind == "tool_start":
            self._tool = str(event.get("tool") or "")
            return {"type": "tool_start", "tool": self._tool}
        if kind == "tool_end":
            tool, self._tool = self._tool, None
            return {"type": "tool_end", "tool": tool or ""}
        if kind == "handoff":
            to_agent = str(event.get("to_agent_id") or "")
            return {"type": "handoff",
                    "to_agent_name": event.get("to_agent_name") or service.agent_name(to_agent),
                    "from_response": str(event.get("from_response") or "")}
        if kind == "done":
            ok = bool(event.get("ok"))
            out: Dict[str, Any] = {"type": "done", "ok": ok,
                                   "thread_id": self.thread["thread_id"],
                                   "title": self.thread.get("title") or "",
                                   "message_id": self.reply_message_id}
            if ok:
                out["response"] = str(event.get("response") or "")
                out["citations"] = safe_citations(event.get("citations"))
            else:
                out["error"] = _error_code(event)
            return out
        return None

    async def stream(self) -> AsyncIterator[str]:
        """The SSE body. A visitor who leaves (the stop button aborts the
        fetch) stops the run; the relay still finishes and keeps the thread."""
        relay = self.relay
        assert relay is not None, "start() first"
        finished = False
        try:
            while True:
                try:
                    event = await relay.next_event(PING_SECONDS)
                except StopAsyncIteration:
                    finished = True
                    break
                if event is None:
                    yield ": ping\n\n"
                    continue
                safe = self.safe_event(event)
                if safe is not None:
                    yield _sse(safe)
        except (asyncio.CancelledError, GeneratorExit):
            relay.stop()
            raise
        finally:
            if not finished and not relay.finished:
                relay.stop()


__all__ = ["PING_SECONDS", "SNIPPET_CHARS", "VisitorTurn", "decode_attachments", "safe_citations"]
