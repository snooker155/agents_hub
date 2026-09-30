"""Builtin interrupt entity: ask a person a question and wait for the answer.

The node is the flow's equivalent of an agent's ``ask_user``. What it produces
is only the question; the answer is written into state under the node's first
declared output key when the run is resumed, so a successor that declares
``input: [answer]`` reads what the person actually said.

``question`` may reference state keys in braces — ``"Ship {draft}?"`` — which
are filled from the live flow state, so the person is asked about the work in
front of them rather than in the abstract.
"""
from __future__ import annotations

from typing import Any, Dict

from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="human_interrupt",
    name="Human Interrupt",
    category="interrupt",
    group="Control",
    entrypoint="flow.entities.interrupts.human_interrupt:run",
    description=(
        "Pause the flow and ask a person. The run parks in awaiting_input and "
        "resumes from its checkpoint with the answer in state."
    ),
    inputs=[],
    outputs=["answer"],
    config_schema={
        "question": {
            "type": "str", "default": "",
            "description": "What to ask. {state_key} is filled from flow state.",
        },
        "choices": {
            "type": "list", "default": [],
            "description": "Optional fixed answers to offer instead of free text.",
        },
    },
    icon="question",
)


def _fill(template: str, state: Any) -> str:
    """Substitute ``{key}`` references from flow state, leaving unknown keys as
    written — a question is shown to a person, so a missing key must not turn
    the whole prompt into a KeyError."""
    if "{" not in template or not hasattr(state, "snapshot"):
        return template
    out = template
    for key, value in state.snapshot().items():
        token = "{" + str(key) + "}"
        if token in out:
            out = out.replace(token, str(value))
    return out


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    """Return ``{"question", "choices"}``. Writes nothing: the declared output
    key belongs to the answer, which does not exist yet."""
    cfg = config or {}
    question = _fill(str(cfg.get("question") or "").strip(), state)
    raw = cfg.get("choices") or []
    if isinstance(raw, str):
        raw = [c.strip() for c in raw.split(",") if c.strip()]
    choices = [str(c) for c in raw] if isinstance(raw, (list, tuple)) else []
    return {"question": question, "choices": choices}
