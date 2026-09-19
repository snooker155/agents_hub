"""Sample builtin flow entity: a non-LLM text summarizer (truncation).

Demonstrates the drop-a-file extension path. The registry discovers this via
its module-level ``SPEC``. The ``run`` contract — ``run(state, config, ctx)``
reading declared ``inputs`` from state and writing ``outputs`` back — is wired
up by the flow engine in a later phase; defined here so the contract is
concrete and the entity is executable once dispatch lands.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="summarize",
    name="Summarize",
    category="processor",
    group="Text",
    entrypoint="flow.entities.processors.summarize:run",
    description="Condense the input text to roughly the first N words. Pure, no LLM.",
    inputs=["text"],
    outputs=["summary"],
    config_schema={
        "max_words": {"type": "int", "default": 80, "description": "Word cap for the summary"},
    },
    icon="text",
)


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    """Read ``text`` from state, return ``{"summary": <first max_words words>}``.

    ``state`` is read via ``.get`` (dict-like). The flow engine applies the
    returned dict to state under this node's declared ``outputs``.
    """
    max_words = int((config or {}).get("max_words", 80))
    text = ""
    if hasattr(state, "get"):
        text = state.get("text", "") or ""
    text = str(text)
    words = text.split()
    summary = " ".join(words[:max_words])
    if len(words) > max_words:
        summary += " …"
    return {"summary": summary}
