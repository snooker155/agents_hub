"""The one message a flow chat turn ends with in the Summary view.

A flow turn answers with one bubble per node. In the flow chat's Summary
view the person sees none of them: once the last node is done, a model reads
what each agent node answered and writes one message saying what was done for
the request, the results that matter and anything that failed. The bubbles
are still there, in the Chat and Build views and in the run log.

The summary is written by the model of the last agent node that ran (the
turn's own provider and key), with no tools, so it can only answer. A turn
with a single agent answer and no failure needs no summary call: that answer
already is the summary.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

log = logging.getLogger(__name__)

SUMMARY_MAX_TOKENS = 1500
# How much of each node's answer the summary model reads, and of all of them.
NODE_CHARS = 6000
TOTAL_CHARS = 30000

SUMMARY_SYSTEM = (
    "You write the single reply a person gets after a multi-step workflow handled "
    "their request. Several agents worked on it one after another; you are given "
    "the request and what each step answered. Write the reply as if you did the "
    "work: what was done, then the results themselves (keep concrete facts, "
    "numbers, names, file paths and links exactly as the steps gave them), then "
    "anything that failed or needs the person's attention. When the last step "
    "produced the deliverable (a text, a report, an answer), give it in full "
    "rather than describing it. Do not walk through the steps one by one unless "
    "the order matters to the person, and do not mention agents, nodes or the "
    "workflow itself. Reply in the language of the request. Markdown is fine."
)


def _chunk_text(chunk: Any) -> str:
    """The answer text of one streamed chunk: text blocks only, so a
    provider's reasoning blocks never reach the summary."""
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text":
                out.append(str(block.get("text") or ""))
        return "".join(out)
    return ""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n… (cut)"


def summary_prompt(user_message: str, responses: list[dict]) -> str:
    """The request and every step's answer, as the summary model reads them."""
    parts = ["## The request", _clip(user_message, NODE_CHARS) or "(no message)", "", "## What the steps answered"]
    used = 0
    for i, r in enumerate(responses, 1):
        label = r.get("agent_label") or r.get("node_id") or f"step {i}"
        if r.get("error"):
            body = f"FAILED: {r.get('error')}"
        else:
            body = _clip(str(r.get("response") or ""), NODE_CHARS) or "(no answer)"
        if used + len(body) > TOTAL_CHARS:
            body = _clip(body, max(TOTAL_CHARS - used, 500))
        used += len(body)
        parts += [f"### Step {i}: {label}", body, ""]
    return "\n".join(parts).strip()


def direct_summary(responses: list[dict]) -> Optional[str]:
    """The summary when no model call is needed: one agent answered, nothing
    failed. None otherwise."""
    agents = [r for r in responses if r.get("is_agent", True)]
    if len(agents) != 1 or any(r.get("error") for r in responses):
        return None
    text = str(agents[0].get("response") or "").strip()
    return text or None


def agent_model(agent_id: Optional[str]) -> dict:
    """The provider and model of an agent, for a summary written after the
    turn (the turn's own model is gone with it). Empty for an agent not found:
    the summary then uses the default model."""
    if not agent_id:
        return {}
    try:
        from agents.registry import get_agent
        spec = get_agent(agent_id)
    except Exception:  # noqa: BLE001 - an unknown agent falls back to the default model
        log.debug("summary: agent %s not found", agent_id, exc_info=True)
        return {}
    if spec is None:
        return {}
    return {"provider": getattr(spec, "provider", None), "model": getattr(spec, "model", None)}


async def write_summary(user_message: str, responses: list[dict], **model: Any) -> str:
    """The whole summary at once (stream_summary, collected)."""
    return "".join([part async for part in stream_summary(user_message, responses, **model)]).strip()


async def stream_summary(
    user_message: str,
    responses: list[dict],
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> AsyncIterator[str]:
    """Yield the summary as it is written. Yields nothing when there is
    nothing to sum up or no model answers; the caller then shows the last
    node's answer instead."""
    if not responses:
        return
    direct = direct_summary(responses)
    if direct is not None:
        yield direct
        return
    try:
        from agents.agent_utils import build_chat_model
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = build_chat_model(
            provider=provider, model=model, api_key=api_key, base_url=base_url, temperature=0.2,
            max_tokens=SUMMARY_MAX_TOKENS, streaming=True, thinking_level="off",
        )
        messages = [SystemMessage(content=SUMMARY_SYSTEM),
                    HumanMessage(content=summary_prompt(user_message, responses))]
        async for chunk in llm.astream(messages):
            text = _chunk_text(chunk)
            if text:
                yield text
    except Exception:  # noqa: BLE001 - no summary means the last answer is shown instead
        log.warning("flow summary could not be written", exc_info=True)
