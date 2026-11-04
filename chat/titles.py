"""A conversation's title, named by a model after its first exchange.

A new chat starts titled with the person's first message, cut short. Once the
first answer is in, the Chat page asks for a real title: the model of the
agent the chat talks to reads the request and the start of the answer and
names what the conversation is about, in a few words and in the person's
language. The page puts it on the conversation and saves it the usual way
(routes/chats.py PUT), so the browser stays the one writer of its document.

No tools and no reasoning: one short answer. A model that fails or answers
nothing leaves the first-message title in place (``None``).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

TITLE_MAX_TOKENS = 32
TITLE_MAX_CHARS = 80
# How much of the request and of the answer the model reads.
MESSAGE_CHARS = 2000
REPLY_CHARS = 1500

TITLE_SYSTEM = (
    "You name chat conversations. Reply with a title of 2 to 6 words that says "
    "what the conversation is about, in the language of the user's message. "
    "Name the subject, not the act of asking (not 'Question about...', 'Request "
    "for...'). No quotes, no emoji, no trailing punctuation, nothing but the title."
)

_QUOTES = "\"'`«»“”„‘’"
_LABEL = re.compile(r"^(title|название|titel)\s*[:：]\s*", re.IGNORECASE)


def title_prompt(message: str, reply: str = "") -> str:
    parts = [f"User's message:\n{(message or '').strip()[:MESSAGE_CHARS]}"]
    if (reply or "").strip():
        parts.append(f"Start of the answer:\n{reply.strip()[:REPLY_CHARS]}")
    return "\n\n".join(parts)


def clean_title(text: str) -> Optional[str]:
    """The model's answer as a title: its first line, without a "Title:"
    label, quotes, Markdown emphasis or a closing full stop. None when nothing
    is left."""
    line = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    line = _LABEL.sub("", line.strip().lstrip("#").strip())
    line = line.strip("*_").strip().strip(_QUOTES).strip()
    line = re.sub(r"\s+", " ", line).rstrip(".!。").strip()
    if not line:
        return None
    if len(line) > TITLE_MAX_CHARS:
        line = line[:TITLE_MAX_CHARS - 1].rstrip() + "…"
    return line


async def suggest_title(message: str, reply: str = "", agent_id: Optional[str] = None) -> Optional[str]:
    """A title for a conversation that began with ``message`` and was
    answered with ``reply``, written by ``agent_id``'s model (the default model
    for an agent not found)."""
    if not (message or "").strip():
        return None
    try:
        from agents.agent_utils import build_chat_model
        from chat.compaction import message_text
        from chat.flow_summary import agent_model
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = build_chat_model(
            **agent_model(agent_id), temperature=0.3, max_tokens=TITLE_MAX_TOKENS,
            streaming=False, thinking_level="off",
        )
        answer = await llm.ainvoke([SystemMessage(content=TITLE_SYSTEM),
                                    HumanMessage(content=title_prompt(message, reply))])
        return clean_title(message_text(answer))
    except Exception:  # noqa: BLE001 - no title means the first-message one stays
        log.debug("title model call failed", exc_info=True)
        return None
