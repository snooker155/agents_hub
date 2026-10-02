"""
From an inbound channel message to a reply: the bridge into the chat pipeline.

This is the transport-free half of ``connectors/telegram/telegram_runner.py``'s
``_run_agent_for_telegram``: build a ``ChatRequest`` from a binding, drive
``run_chat_pipeline`` (or the flow pipeline), collect the final text, the
entities the run touched and a possible handoff, and hand back one
:class:`TurnResult`. A channel sends the text its own way (a Slack thread, a
Discord reply, an email) and may render ``response_obj`` natively if it can.

Every channel turn goes through the same path the web Chat page uses, so
sessions, tool history, logs, cost and the capability guard apply the same
way: ``source`` becomes the run's ``message_origin`` and
``tools/capabilities.py`` treats these channels as untrusted input.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from common.session_broker import notify_change

from .store import ChannelStore

log = logging.getLogger("channels.turns")


@dataclass
class TurnResult:
    text: str = ""
    ok: bool = False
    error: Optional[str] = None
    response_obj: Optional[dict[str, Any]] = None
    entities: list[dict[str, Any]] = field(default_factory=list)
    #: Set when the agent handed the conversation to another agent; the
    #: binding is already updated to point at it.
    handoff_to: Optional[str] = None
    is_flow: bool = False

    @property
    def reply(self) -> str:
        """What to send back: the answer, else the error, else a placeholder."""
        if self.text and (self.ok or self.is_flow):
            return self.text
        if self.error:
            return f"Error: {self.error}"
        return "(no response)"


def view_ref_note(ref: dict[str, Any]) -> str:
    """A short note for a view_ref reply: kind, title, summary, Studio link.

    The deep link needs ``AGENTS_HUB_PUBLIC_URL``; without it the note just
    says where to look."""
    title = ref.get("title") or "view"
    summary = (ref.get("summary") or "").strip()
    lines = [f"📊 {title} ({ref.get('view_kind') or 'view'})"]
    if summary:
        lines.append(summary)
    base = (os.environ.get("AGENTS_HUB_PUBLIC_URL") or "").rstrip("/")
    if base and ref.get("view_id"):
        lines.append(f"Open in the Studio: {base}/studio/{ref['view_id']}")
    else:
        lines.append("Open the Visualization Studio to view it.")
    return "\n".join(lines)


def format_flow_reply(responses: list[dict[str, Any]]) -> str:
    """Join a flow's per-node outputs; a single node reads as a plain reply."""
    parts = [r for r in responses if str(r.get("response") or "").strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return str(parts[0].get("response") or "").strip()
    return "\n\n".join(
        f"*{r.get('agent_label') or r.get('agent_id') or 'step'}*\n{str(r.get('response') or '').strip()}"
        for r in parts
    )


def format_handoff(event: dict[str, Any]) -> str:
    """The handing agent's reply and where the conversation went."""
    reply = str(event.get("from_response") or "").strip()
    name = event.get("to_agent_name") or event.get("to_agent_id") or "another agent"
    reason = str(event.get("reason") or "").strip()
    marker = f"(handed over to {name}" + (f": {reason})" if reason else ")")
    return f"{reply}\n\n{marker}" if reply and reply != reason else marker


async def run_turn(
    store: ChannelStore,
    chat_key: str,
    binding: dict[str, Any],
    text: str,
    attachments: Optional[list[dict[str, Any]]] = None,
    *,
    source: Optional[str] = None,
    on_handoff: Optional[Callable[[str], Awaitable[None]]] = None,
    on_typing: Optional[Callable[[], Awaitable[None]]] = None,
) -> TurnResult:
    """Run one turn for ``chat_key`` and return what to send back.

    ``on_handoff`` is called with the handing agent's message while the
    receiving agent is still working, so a channel can post it as its own
    message; ``on_typing`` is called before the run and after a handoff so a
    channel can show a typing indicator. Both are optional.
    """
    # Lazy imports: agents/ and routes/ import each other at module load.
    from chat import run_chat_pipeline, run_chat_flow_pipeline
    from chat.models import ChatRequest, ChatAttachment
    from chat.runs import build_conversation_history

    attachments = attachments or []
    workspace = binding.get("workspace")
    result = TurnResult(is_flow=bool(binding.get("flow_id")))
    if attachments and not workspace:
        result.error = "Attachments need a workspace on this binding. Set a workspace, then resend."
        return result

    is_flow = result.is_flow
    conv_id = binding.get("conversation_id") or str(uuid.uuid4())
    if not binding.get("conversation_id"):
        store.upsert_binding(
            chat_key=chat_key,
            agent_id="" if is_flow else (binding.get("agent_id") or ""),
            flow_id=binding.get("flow_id") if is_flow else None,
            workspace=workspace,
            conversation_id=conv_id,
            title=binding.get("title"),
        )
        notify_change(f"channel_{store.name}", chat_key=chat_key)

    # No client-side transcript on a channel: rebuild the prior turns from
    # this conversation's completed runs, else every message runs context-free.
    history = build_conversation_history(conv_id)

    request = ChatRequest(
        agent_id=None if is_flow else binding.get("agent_id"),
        flow_id=binding.get("flow_id") if is_flow else None,
        message=text or "",
        workspace=workspace,
        history=history,
        conversation_id=conv_id,
        conversation_title=binding.get("title") or f"{store.name} chat {chat_key}",
        attachments=[ChatAttachment(**a) for a in attachments],
        source=source or store.name,
    )

    if on_typing is not None:
        try:
            await on_typing()
        except Exception:  # noqa: BLE001 - a typing indicator is cosmetic
            log.debug("channel %s typing indicator failed", store.name, exc_info=True)

    try:
        pipeline = run_chat_flow_pipeline(request) if is_flow else run_chat_pipeline(request)
        async for event in pipeline:
            etype = event.get("type")
            if etype == "node_done":
                result.entities.extend(event.get("entities") or [])
            elif etype == "handoff":
                if on_handoff is not None:
                    try:
                        await on_handoff(format_handoff(event))
                    except Exception:  # noqa: BLE001 - the final reply still goes out
                        log.debug("channel %s handoff message failed", store.name, exc_info=True)
                if on_typing is not None:
                    try:
                        await on_typing()
                    except Exception:  # noqa: BLE001 - a typing indicator is cosmetic
                        log.debug("channel %s typing indicator failed", store.name, exc_info=True)
            elif etype == "done":
                if event.get("handoff") and event.get("agent_id") and not is_flow:
                    # The chat stays with the agent that answered.
                    store.upsert_binding(
                        chat_key=chat_key, agent_id=str(event["agent_id"]),
                        workspace=workspace, conversation_id=conv_id,
                    )
                    notify_change(f"channel_{store.name}", chat_key=chat_key)
                    result.handoff_to = str(event["agent_id"])
                result.entities.extend(event.get("entities") or [])
                result.ok = bool(event.get("ok"))
                result.error = event.get("error")
                if is_flow:
                    result.text = format_flow_reply(event.get("responses") or [])
                else:
                    result.text = str(event.get("response") or "")
                    obj = event.get("response_obj")
                    result.response_obj = obj if isinstance(obj, dict) else None
                break
    except Exception as exc:  # noqa: BLE001 - reported to the chat as text
        result.error = str(exc)

    store.touch_binding(chat_key)
    log.info("channel %s reply chat=%s ok=%s text_len=%d",
             store.name, chat_key, result.ok, len(result.text or ""))

    if result.response_obj and result.response_obj.get("kind") == "view_ref":
        note = view_ref_note(result.response_obj)
        result.text = f"{result.text}\n\n{note}".strip() if result.text else note

    if result.entities and (result.ok or is_flow):
        try:
            from common.entity_links import append_entity_links
            result.text = append_entity_links(result.text, result.entities)
        except Exception:  # noqa: BLE001 - links are a courtesy
            log.debug("entity links failed", exc_info=True)
    return result


def wake_unanswered(channel: str, chat_key: str, workspace: Optional[str], text: str) -> list[str]:
    """A message nobody is bound to answer still reaches the proactive agents
    listening for this channel (proactive/events.py). Best-effort, sync."""
    try:
        from proactive.events import channel_unanswered
        return channel_unanswered(channel, str(chat_key), workspace, text)
    except Exception:  # noqa: BLE001 - the help reply already went out
        log.debug("%s wake dispatch failed for chat %s", channel, chat_key, exc_info=True)
        return []


async def wake_unanswered_async(channel: str, chat_key: str, workspace: Optional[str], text: str) -> None:
    await asyncio.to_thread(wake_unanswered, channel, chat_key, workspace, text)


__all__ = ["TurnResult", "run_turn", "view_ref_note", "format_flow_reply", "format_handoff",
           "wake_unanswered", "wake_unanswered_async"]
