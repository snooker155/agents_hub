"""Native model reasoning — capture thinking emitted by the model itself.

Some models reason natively in the API response instead of (or in addition to)
using the `think` scratchpad tool — e.g. LM Studio / Ollama reasoning models
(qwen3, deepseek-r1, ...). The reasoning arrives in one of two shapes:

- a separate ``reasoning_content`` field on the message (LM Studio's
  "separate reasoning_content" setting, Ollama's ``reasoning=True``), surfaced
  by LangChain in ``message.additional_kwargs``;
- inline ``<think>...</think>`` tags inside the message content (LM Studio's
  default pass-through). NOTE: langchain-openai (<= 0.3.x) does NOT copy a
  separate ``reasoning_content`` response field into ``additional_kwargs``, so
  with LM Studio the inline-tags shape is the one that reliably survives.

This module extracts that reasoning so the harness can:

1. show it in the UI like a `think` step (see ChatStreamCallback), and
2. let it satisfy the step-by-step ThinkGate — a model that already reasoned
   natively should not be forced to repeat itself through the `think` tool.
   The scratchpad tools and gates stay in place for models without native
   reasoning; detection is per-response, so a model that emits no reasoning
   is still gated as before.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from langchain_core.callbacks import BaseCallbackHandler


# Inline reasoning markers used by LM Studio / open reasoning models.
_THINK_TAG_RE = re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
# An unclosed opening tag at the start of output (some models omit the closer
# when the whole message is reasoning, or the closer was cut by max_tokens).
_OPEN_THINK_RE = re.compile(r"^\s*<think(?:ing)?>(.*)\Z", re.DOTALL | re.IGNORECASE)


def _message_text(content: Any) -> str:
    """Flatten a message content (string or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def extract_reasoning_from_message(message: Any) -> str:
    """Return the native reasoning text carried by *message*, or ''."""
    if message is None:
        return ""
    # 1. Separate field (Ollama reasoning=True, DeepSeek-style APIs, future
    #    langchain-openai versions).
    kwargs = getattr(message, "additional_kwargs", None) or {}
    reasoning = kwargs.get("reasoning_content") or kwargs.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    # 2. Structured content blocks (Anthropic-style thinking blocks).
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("thinking", "reasoning_content"):
                text = block.get("thinking") or block.get("reasoning_content") or block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        if parts:
            return "\n\n".join(parts)
    # 3. Inline <think> tags in the text (LM Studio default).
    text = _message_text(content)
    if text:
        closed = [m.strip() for m in _THINK_TAG_RE.findall(text) if m.strip()]
        if closed:
            return "\n\n".join(closed)
        open_match = _OPEN_THINK_RE.match(text)
        if open_match and open_match.group(1).strip():
            return open_match.group(1).strip()
    return ""


def extract_reasoning_from_llm_result(response: Any) -> str:
    """Return the native reasoning from an ``LLMResult``'s generations, or ''."""
    parts = []
    try:
        for grp in (getattr(response, "generations", []) or []):
            for g in grp:
                text = extract_reasoning_from_message(getattr(g, "message", None))
                if text:
                    parts.append(text)
    except Exception:
        return ""
    return "\n\n".join(parts)


def strip_think_tags(text: str) -> str:
    """Remove inline ``<think>...</think>`` reasoning from an output string.

    The reasoning is surfaced separately (UI think events), so the final answer
    should not repeat it. Unclosed leading tags drop the tag but keep the text —
    better to show reasoning-as-answer than an empty reply.
    """
    if not text or "<think" not in text.lower():
        return text
    cleaned = _THINK_TAG_RE.sub("", text)
    cleaned = re.sub(r"^\s*<think(?:ing)?>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


class ThinkTagStreamFilter:
    """Split a live token stream into visible text and ``<think>`` reasoning.

    Streaming models emit the inline reasoning token-by-token, so without
    filtering it pours into the chat answer bubble and is then shown again by
    the reasoning trail and the final (cleaned) response — three outputs for
    one turn, in the wrong order. Feed every token through this filter:
    text inside ``<think>``/``<thinking>`` tags is withheld and returned as one
    completed reasoning chunk when the tag closes (i.e. at its true position in
    the stream); everything else passes through. Tags split across token
    boundaries are handled by holding back the longest tail that could still
    be a partial tag.

    While a ``<think>`` block is still open its text is buffered rather than
    returned, so nothing about the reasoning would reach the UI until the tag
    closes. ``reasoning_delta`` exposes the reasoning text consumed by the most
    recent :meth:`feed` call so a caller can stream the thought live (a ticker
    under the "working" indicator) before the completed chunk arrives.

    One instance per LLM call — ``reset()`` is cheap, call it on llm start.
    """

    _OPEN_TAGS = ("<thinking>", "<think>")
    _CLOSE_TAGS = ("</thinking>", "</think>")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._pending = ""          # unprocessed tail (may end in a partial tag)
        self._inside = False
        self._reasoning_parts: list = []
        # Reasoning text consumed by the last feed() call — the live delta.
        self.reasoning_delta = ""

    @staticmethod
    def _find_first(low: str, tags) -> tuple[Optional[int], str]:
        best_idx, best_tag = None, ""
        for tag in tags:
            idx = low.find(tag)
            if idx != -1 and (best_idx is None or idx < best_idx):
                best_idx, best_tag = idx, tag
        return best_idx, best_tag

    @staticmethod
    def _partial_suffix(low: str, tags) -> int:
        """Longest tail of *low* that is a strict prefix of one of *tags*."""
        keep = 0
        for tag in tags:
            for n in range(min(len(low), len(tag) - 1), 0, -1):
                if low.endswith(tag[:n]):
                    keep = max(keep, n)
                    break
        return keep

    def feed(self, token: str) -> tuple[str, str]:
        """Process one token; returns ``(visible_text, completed_reasoning)``."""
        self._pending += token
        visible: list = []
        completed: list = []
        delta: list = []
        while self._pending:
            low = self._pending.lower()
            tags = self._CLOSE_TAGS if self._inside else self._OPEN_TAGS
            idx, tag = self._find_first(low, tags)
            if idx is not None:
                chunk = self._pending[:idx]
                self._pending = self._pending[idx + len(tag):]
                if self._inside:
                    self._reasoning_parts.append(chunk)
                    delta.append(chunk)
                    reasoning = "".join(self._reasoning_parts).strip()
                    if reasoning:
                        completed.append(reasoning)
                    self._reasoning_parts = []
                else:
                    visible.append(chunk)
                self._inside = not self._inside
                continue
            keep = self._partial_suffix(low, tags)
            chunk = self._pending[:-keep] if keep else self._pending
            self._pending = self._pending[-keep:] if keep else ""
            if self._inside:
                self._reasoning_parts.append(chunk)
                delta.append(chunk)
            else:
                visible.append(chunk)
            break
        self.reasoning_delta = "".join(delta)
        return "".join(visible), "\n\n".join(completed)

    def flush(self) -> tuple[str, str]:
        """End of the LLM call: return leftover ``(visible_text, reasoning)``.

        An unclosed ``<think>`` (cut off by max_tokens, or a model that omits
        the closer) yields its buffered text as reasoning; a leftover partial
        tag that never completed is returned as visible text.
        """
        visible, reasoning = "", ""
        if self._inside:
            reasoning = ("".join(self._reasoning_parts) + self._pending).strip()
        else:
            visible = self._pending
        self.reset()
        return visible, reasoning


class NativeReasoningCallback(BaseCallbackHandler):
    """Lets a model's own reasoning output satisfy the step-by-step ThinkGate.

    Attached by StandardAgent when the agent has thinking enabled. On every
    LLM response that carries native reasoning, the gate is cleared exactly as
    if the agent had called the `think` tool — so gated action tools and the
    finish review don't force a redundant scratchpad call. Responses without
    native reasoning leave the gate untouched (full enforcement as before).
    """

    def __init__(
        self,
        gate: Optional[Any] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()
        self.gate = gate
        self.on_reasoning = on_reasoning

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            reasoning = extract_reasoning_from_llm_result(response)
        except Exception:
            return
        if not reasoning:
            return
        if self.gate is not None:
            try:
                self.gate.note_think()
            except Exception:
                pass
        if self.on_reasoning is not None:
            try:
                self.on_reasoning(reasoning)
            except Exception:
                pass


__all__ = [
    "NativeReasoningCallback",
    "ThinkTagStreamFilter",
    "extract_reasoning_from_message",
    "extract_reasoning_from_llm_result",
    "strip_think_tags",
]
