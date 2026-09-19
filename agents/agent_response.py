"""
Structured agent responses.

An agent normally replies with plain text. When it wants a richer surface — a
set of buttons, a Telegram-native inline keyboard, … — it appends a structured
block to its output:

    Some normal prose the user reads.
    <<<ui>>>
    {"kind": "buttons", "text": "Pick one:",
     "buttons": [{"label": "Approve", "value": "approve"},
                 {"label": "Docs", "url": "https://example.com"}]}
    <<<end>>>

:func:`parse_agent_response` strips that block from the visible text and turns
it into an :class:`AgentResponse` subclass instance. The structured object then
travels alongside the plain text through the chat pipeline's ``done`` event, and
each surface renders what it understands:

- the web chat renders ``buttons`` / ``telegram`` as clickable buttons;
- the Telegram connector maps them onto an ``inline_keyboard``;
- any surface that doesn't understand a ``kind`` falls back to ``fallback_text``.

``AgentResponse`` is the base every concrete format inherits from; ``kind`` is the
discriminator surfaces dispatch on. ``TelegramResponse`` is just one concrete
format (Telegram-native layout); ``ButtonsResponse`` is a surface-agnostic one.
New formats are added by subclassing and registering with ``@register_response``.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from typing import ClassVar, Optional

from pydantic import BaseModel


log = logging.getLogger("agent_response")


# A trailing ``<<<ui>>> { json } <<<end>>>`` block. DOTALL so the JSON can span
# lines; non-greedy so only the first block is consumed. Markers are matched
# leniently — case-insensitive with optional inner whitespace — because models
# don't reproduce them byte-for-byte (e.g. ``<<< UI >>>``, ``<<</end>>>``).
_SENTINEL_RE = re.compile(
    r"<<<\s*ui\s*>>>\s*(.*?)\s*<<<\s*/?\s*end\s*>>>",
    re.DOTALL | re.IGNORECASE,
)

# A markdown code fence the model may wrap the JSON in (```json … ```).
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _loads_lenient(raw: str):
    """Parse the block body into a dict, tolerating common model deviations.

    Handles a wrapping markdown code fence and Python-literal syntax (single
    quotes) in addition to strict JSON. Returns the parsed object or None.
    """
    raw = raw.strip()
    fence = _FENCE_RE.match(raw)
    if fence:
        raw = fence.group(1).strip()
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(raw)
        except Exception:
            continue
    return None

# kind -> AgentResponse subclass, populated by @register_response.
_REGISTRY: dict[str, type["AgentResponse"]] = {}


def register_response(cls: type["AgentResponse"]) -> type["AgentResponse"]:
    """Register a response subclass so the parser can build it from its ``kind``."""
    _REGISTRY[cls.kind] = cls
    return cls


class Button(BaseModel):
    """One action button.

    ``value`` is opaque callback data sent back as the user's next message when
    the button is pressed (mutually exclusive with ``url``, which opens a link).
    """
    label: str
    value: Optional[str] = None
    url: Optional[str] = None


class AgentResponse(BaseModel):
    """Base class for structured agent responses.

    Every response carries ``fallback_text`` — what a surface shows when it can't
    render the structured form natively. Subclasses add structured fields and set
    a unique ``kind`` (the discriminator surfaces dispatch on).
    """
    kind: ClassVar[str] = "base"
    fallback_text: str = ""

    def to_payload(self) -> dict:
        """Serialize for transport in the chat ``done`` event / SSE / Telegram.

        ``kind`` is a ClassVar (not a model field), so it is injected explicitly
        here and stripped again by :func:`parse_agent_response` on the way back.
        """
        data = self.model_dump()
        data["kind"] = self.kind
        return data


@register_response
class TextResponse(AgentResponse):
    """Plain text. The default when no structured block is present."""
    kind: ClassVar[str] = "text"


@register_response
class ButtonsResponse(AgentResponse):
    """Surface-agnostic buttons rendered as a grid ``columns`` wide."""
    kind: ClassVar[str] = "buttons"
    text: str = ""
    buttons: list[Button] = []
    columns: int = 1


@register_response
class TelegramResponse(AgentResponse):
    """Telegram-native response: explicit inline-keyboard rows + parse mode.

    Use this when the agent wants full control of the Telegram layout; for
    portable UI that also renders on the web, prefer :class:`ButtonsResponse`.
    """
    kind: ClassVar[str] = "telegram"
    text: str = ""
    parse_mode: Optional[str] = None
    inline_keyboard: list[list[Button]] = []


@register_response
class ViewResponse(AgentResponse):
    """A rich view (chart, table, diagram, …) emitted inline in an answer.

    The discriminator is ``kind="view"``; ``view_kind`` names the actual view
    kind. The chat pipeline persists this through the view store and replaces it
    with a lightweight ``view_ref`` for transport (see ``views.publish``), so
    the client never receives the full spec inline and run records stay slim.
    Large or multi-file views should use the ``create_view`` tool instead.
    """
    kind: ClassVar[str] = "view"
    view_kind: str = ""
    title: str = ""
    summary: str = ""
    spec: dict = {}
    data: Optional[dict] = None
    controls: list = []
    actions: list = []
    complexity: str = "inline"

    def to_envelope(self) -> dict:
        """Shape this response as a raw view envelope for ``views.create_view``."""
        return {
            "kind": self.view_kind,
            "title": self.title,
            "summary": self.summary,
            "spec": self.spec or {},
            "data": self.data,
            "controls": self.controls or [],
            "actions": self.actions or [],
            "complexity": self.complexity or "inline",
            "fallback": {"text": self.fallback_text or self.summary or ""},
        }


# ── System-prompt injection ──────────────────────────────────────────────────
#
# Selected per agent on the Agent details page (AgentSpec.response_format) and
# injected into the system prompt by agent_factory, the same way reasoning /
# delegation guidance is. "none" injects nothing.

RESPONSE_FORMAT_CHOICES = ("none", "buttons", "telegram", "views")

_BUTTONS_PROMPT = """## Interactive replies

When the user has a clear set of choices, end your reply with an interactive
block so they can act with one tap instead of typing. Append it as the very last
thing in your reply.

The example below shows the STRUCTURE only. Always write your own real labels
and values for the current reply — never copy the example text, and never output
angle brackets or placeholder words like "label" or "text":

<<<ui>>>
{"kind": "buttons", "text": "What would you like to do next?",
 "buttons": [{"label": "Start quest", "value": "Start the quest"},
             {"label": "See rewards", "value": "Show me the rewards"},
             {"label": "Open guide", "url": "https://example.com/guide"}],
 "columns": 2}
<<<end>>>

Rules:
- Write your normal answer BEFORE the block; it is shown to the user as usual.
- Every `label` is the short text ON the button; every `value` is the message
  sent back to you when it is tapped — both must be real, concrete text.
- A button has EITHER `value` OR `url` (a link) — never both.
- `columns` lays the buttons out in a grid that many wide.
- Output at most one block, and only when concrete choices genuinely help."""

_TELEGRAM_PROMPT = """## Interactive replies (Telegram inline keyboard)

When the user has a clear set of choices, end your reply with a Telegram inline
keyboard so they can act with one tap. Append it as the very last thing in your
reply.

The example below shows the STRUCTURE only. Always write your own real labels
and values for the current reply — never copy the example text, and never output
angle brackets or placeholder words:

<<<ui>>>
{"kind": "telegram", "text": "Choose your path:", "parse_mode": null,
 "inline_keyboard": [[{"label": "Fight the dragon", "value": "Fight the dragon"},
                      {"label": "Sneak past", "value": "Sneak past it"}],
                     [{"label": "Open guide", "url": "https://example.com/guide"}]]}
<<<end>>>

Rules:
- Write your normal answer BEFORE the block; it is shown to the user as usual.
- `inline_keyboard` is a list of rows; each row is a list of buttons. Each
  `label`/`value` must be real, concrete text for this reply.
- A button has EITHER `value` (sent back to you) OR `url` (a link), never both.
- `parse_mode` may be null, "Markdown", or "HTML"; leave it null unless needed.
- Output at most one block, and only when concrete choices genuinely help."""

_VIEWS_PROMPT = """## Rich views

When a result is clearer *shown* than told, render it as a view instead of (or
alongside) prose. Append the view as the very last thing in your reply, in this
block:

<<<ui>>>
{"kind": "view", "view_kind": "chart", "title": "Revenue by quarter",
 "summary": "Q3 dips 12% on churn, Q4 recovers",
 "spec": {"vega_lite": {"mark": "bar",
   "data": {"values": [{"q": "Q1", "rev": 90}, {"q": "Q2", "rev": 110},
                        {"q": "Q3", "rev": 97}, {"q": "Q4", "rev": 120}]},
   "encoding": {"x": {"field": "q", "type": "nominal"},
                "y": {"field": "rev", "type": "quantitative"}}}}}
<<<end>>>

Choosing the form — pick the SIMPLEST that does the job, never escalate without
payoff (prose < table < chart < diagram):
- `markdown`  — rich text / structured notes.        spec: {"markdown": "..."}
- `table`     — rows the user will scan or compare.
                spec: {"columns": ["City", "Pop"], "rows": [["Paris", 2.1]]}
- `chart`     — categories, series, trends, distributions (Vega-Lite grammar).
                spec: {"vega_lite": { ... }}
- `diagram`   — a process, sequence, hierarchy or state machine (Mermaid).
                spec: {"mermaid": "flowchart LR\\n  A-->B"}
- `image`     — an existing image by path or URL.  spec: {"src": "...", "caption": "..."}

Rules:
- Always write a short `summary` — it is what non-visual surfaces (and the chat
  card header) show. Write your normal prose answer BEFORE the block.
- Keep the inline spec small. For a large dataset, a 3D scene, or a multi-file
  interactive app, use the `create_view` tool instead of this block.
- Output at most one block, and only when a view genuinely helps."""

_FORMAT_PROMPTS = {"buttons": _BUTTONS_PROMPT, "telegram": _TELEGRAM_PROMPT,
                   "views": _VIEWS_PROMPT}


def build_response_format_prompt(response_format: Optional[str]) -> str:
    """Return the system-prompt snippet teaching *response_format*, or "".

    ``"none"`` / unknown / falsy values return an empty string (inject nothing).
    """
    return _FORMAT_PROMPTS.get((response_format or "none"), "")


def parse_agent_response(text: str) -> tuple[str, Optional[AgentResponse]]:
    """Split a ``<<<ui>>>{json}<<<end>>>`` block off the agent's output.

    Returns ``(clean_text, response_or_None)`` where ``clean_text`` has the block
    removed. ``fallback_text`` defaults to ``clean_text`` so non-UI surfaces still
    show something sensible.

    Once the markers are present the block is *always* stripped from the visible
    text — even when its body can't be parsed — so a malformed block degrades to
    a plain answer rather than leaking raw ``<<<ui>>>`` markers into the bubble.
    Parse failures are logged at WARNING so a misbehaving model is diagnosable.
    """
    if not text:
        return text, None
    match = _SENTINEL_RE.search(text)
    if not match:
        return text, None

    clean = (text[: match.start()] + text[match.end():]).strip()
    body = match.group(1)

    data = _loads_lenient(body)
    if not isinstance(data, dict):
        log.warning("UI block found but body did not parse as a JSON object: %r", body[:200])
        return clean, None

    cls = _REGISTRY.get(data.get("kind"))
    if cls is None:
        log.warning("UI block has unknown kind %r (known: %s)", data.get("kind"), list(_REGISTRY))
        return clean, None

    fields = {k: v for k, v in data.items() if k != "kind"}
    fields.setdefault("fallback_text", clean)
    try:
        resp = cls.model_validate(fields)
    except Exception as exc:
        log.warning("UI block kind=%s failed validation: %s", data.get("kind"), exc)
        return clean, None
    log.debug("parsed UI response kind=%s", data.get("kind"))
    return clean, resp


__all__ = [
    "AgentResponse",
    "Button",
    "TextResponse",
    "ButtonsResponse",
    "TelegramResponse",
    "ViewResponse",
    "register_response",
    "parse_agent_response",
    "RESPONSE_FORMAT_CHOICES",
    "build_response_format_prompt",
]
