"""The guardrail record and its validation.

A guardrail is a workspace object like an environment (environments/models.py):
a small, named, versionless profile that a run is checked against rather than
launched inside. Every field the check functions (guardrails/checks.py) read
is validated once, here, so a stored record is always safe to run: a bad
regex, an empty instruction or an unknown PII detector never reaches a run,
it is refused at create/update time instead.

One kind is not about text at all: ``sequence`` (stage ``tool``) is a rule
about the order and the totals of a run's tool calls, checked by
guardrails/sequence.py before each call rather than on the run's input or
output. Its action is ``block`` or ``ask`` (hold the call for a person); the
text kinds keep ``block`` or ``warn``.

``config`` is the one field whose shape depends on another field (``kind``):
:func:`validate_config` is the single place that knows what each kind needs,
called from the model's own validator so ``Guardrail(...)`` can never hold a
config its own kind cannot use.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

Stage = Literal["input", "output", "both", "tool"]
Kind = Literal["regex", "keywords", "pii", "max_chars", "judge", "sequence"]
Action = Literal["block", "warn", "ask"]
AppliesTo = Literal["all", "selected"]

#: Built-in PII detectors a ``pii`` guardrail may pick from (guardrails/checks.py).
PII_DETECTORS = ("email", "phone", "credit_card", "iban", "api_key")

_NAME_MAX = 120
_DESCRIPTION_MAX = 2000
_INSTRUCTION_MAX = 4000
_REGEX_FLAGS = ("IGNORECASE", "MULTILINE", "DOTALL")

#: The rule types a ``sequence`` guardrail may hold (guardrails/sequence.py).
SEQUENCE_RULES = ("after", "sum_max", "same_as")
#: Actions per family of kinds: a text check warns, a tool call can wait.
SEQUENCE_ACTIONS = ("block", "ask")
TEXT_ACTIONS = ("block", "warn")
_PATTERN_MAX = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_config(kind: str, config: Any) -> Dict[str, Any]:
    """The config shape one rule kind needs, normalized to plain JSON.

    Raises ``ValueError`` (caught by the model's validator, and by
    guardrails.service, which turns it into a 400) for anything the matching
    check function in guardrails/checks.py could not run on: a pattern that
    does not compile, an empty keyword list, an unknown PII detector, a
    non-positive character limit, a judge with no instruction.
    """
    config = dict(config or {})
    if kind == "regex":
        pattern = str(config.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("a regex guardrail needs a 'pattern'")
        raw_flags = config.get("flags") or []
        if isinstance(raw_flags, str):
            raw_flags = [raw_flags]
        flags = [str(f).strip().upper() for f in raw_flags if str(f).strip()]
        bad = [f for f in flags if f not in _REGEX_FLAGS]
        if bad:
            raise ValueError(f"unknown regex flag(s): {', '.join(bad)}")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"'pattern' is not a valid regular expression: {exc}") from exc
        return {"pattern": pattern, "flags": flags}

    if kind == "keywords":
        raw = config.get("keywords") or []
        if isinstance(raw, str):
            raw = [w for w in re.split(r"[\n,]+", raw) if w.strip()]
        words: List[str] = []
        for w in raw:
            w = str(w).strip()
            if w and w not in words:
                words.append(w)
        if not words:
            raise ValueError("a keywords guardrail needs at least one keyword")
        if len(words) > 200:
            raise ValueError("at most 200 keywords")
        return {"keywords": words}

    if kind == "pii":
        raw = config.get("detectors") or []
        if isinstance(raw, str):
            raw = [raw]
        detectors: List[str] = []
        for d in raw:
            d = str(d).strip().lower()
            if d and d not in detectors:
                detectors.append(d)
        bad = [d for d in detectors if d not in PII_DETECTORS]
        if bad:
            raise ValueError(f"unknown PII detector(s): {', '.join(bad)}")
        return {"detectors": detectors or list(PII_DETECTORS)}

    if kind == "max_chars":
        raw_max = config.get("max_chars")
        try:
            max_chars = int(raw_max)
        except (TypeError, ValueError) as exc:
            raise ValueError("a max_chars guardrail needs a numeric 'max_chars'") from exc
        if max_chars <= 0:
            raise ValueError("'max_chars' must be a positive number")
        return {"max_chars": max_chars}

    if kind == "sequence":
        return _validate_sequence(config)

    if kind == "judge":
        instruction = str(config.get("instruction") or "").strip()
        if not instruction:
            raise ValueError("a judge guardrail needs an 'instruction'")
        if len(instruction) > _INSTRUCTION_MAX:
            raise ValueError(f"'instruction' is longer than {_INSTRUCTION_MAX} characters")
        return {"instruction": instruction}

    raise ValueError(f"unknown guardrail kind '{kind}'")


def _tool_pattern(value: Any, field: str) -> str:
    """A tool name or a glob over tool names (``mcp__bank__*``)."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"a sequence rule needs '{field}'")
    if len(text) > _PATTERN_MAX:
        raise ValueError(f"'{field}' is longer than {_PATTERN_MAX} characters")
    return text


def _argument_path(value: Any, field: str) -> str:
    """A dotted path into a call's arguments (``amount``, ``payee.account``)."""
    text = str(value or "").strip().strip(".")
    if not text:
        raise ValueError(f"a sequence rule needs '{field}'")
    if len(text) > _PATTERN_MAX or any(not part for part in text.split(".")):
        raise ValueError(f"'{field}' is not a valid argument path")
    return text


def _validate_sequence(config: Dict[str, Any]) -> Dict[str, Any]:
    """One rule about a run's tool calls (guardrails/sequence.py reads it):

    * ``after``: ``tool`` runs only once ``after_tool`` has run in the run,
      and with ``require_success`` only once it ran without an error;
    * ``sum_max``: the ``argument`` values of every call of ``tools`` add up
      to at most ``max``;
    * ``same_as``: ``argument`` of ``tool`` equals ``source_argument`` of an
      earlier call of ``source_tool``.
    """
    rule = str(config.get("rule") or "").strip().lower()
    if rule not in SEQUENCE_RULES:
        raise ValueError(f"a sequence guardrail needs 'rule', one of: {', '.join(SEQUENCE_RULES)}")
    if rule == "after":
        return {
            "rule": rule,
            "tool": _tool_pattern(config.get("tool"), "tool"),
            "after_tool": _tool_pattern(config.get("after_tool"), "after_tool"),
            "require_success": bool(config.get("require_success")),
        }
    if rule == "sum_max":
        raw = config.get("tools") or config.get("tool") or []
        if isinstance(raw, str):
            raw = [t for t in re.split(r"[\n,]+", raw) if t.strip()]
        tools: List[str] = []
        for t in raw:
            t = _tool_pattern(t, "tools")
            if t not in tools:
                tools.append(t)
        if not tools:
            raise ValueError("a sum_max rule needs at least one tool in 'tools'")
        try:
            limit = float(config.get("max"))
        except (TypeError, ValueError) as exc:
            raise ValueError("a sum_max rule needs a numeric 'max'") from exc
        if limit != limit or limit < 0:  # NaN or negative
            raise ValueError("'max' must be zero or more")
        return {"rule": rule, "tools": tools,
                "argument": _argument_path(config.get("argument"), "argument"),
                "max": int(limit) if limit.is_integer() else limit}
    argument = _argument_path(config.get("argument"), "argument")
    return {
        "rule": rule,
        "tool": _tool_pattern(config.get("tool"), "tool"),
        "argument": argument,
        "source_tool": _tool_pattern(config.get("source_tool"), "source_tool"),
        "source_argument": _argument_path(config.get("source_argument") or argument,
                                          "source_argument"),
    }


class Guardrail(BaseModel):
    """One check a run's input or output is held to.

    ``workspace`` None makes it global (every workspace's runs are checked
    against it, on top of that workspace's own). ``applies_to`` narrows a
    guardrail beyond its scope: ``"all"`` checks every agent that can see it,
    ``"selected"`` only the agents that list this id in their own
    ``AgentSpec.guardrails``. ``model`` is a judge guardrail's own model
    override (``"provider/model"``); unset, guardrails.checks falls back to
    ``AGENTS_HUB_GUARDRAIL_MODEL``, then the workspace's default.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    description: str = ""
    workspace: Optional[str] = None
    stage: Stage = "input"
    kind: Kind = "regex"
    config: Dict[str, Any] = Field(default_factory=dict)
    action: Action = "block"
    applies_to: AppliesTo = "all"
    enabled: bool = True
    #: A judge error blocks when True, passes through (with a logged warning)
    #: when False. Meaningless for a rule kind, which never errors.
    fail_closed: bool = True
    model: Optional[str] = None
    archived_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        name = str(value or "").strip()
        if not name:
            raise ValueError("name is required")
        if len(name) > _NAME_MAX:
            raise ValueError(f"name is longer than {_NAME_MAX} characters")
        return name

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value):
        text = str(value or "").strip()
        if len(text) > _DESCRIPTION_MAX:
            raise ValueError(f"description is longer than {_DESCRIPTION_MAX} characters")
        return text

    @field_validator("workspace", mode="before")
    @classmethod
    def _workspace(cls, value):
        text = str(value or "").strip()
        return text or None

    @field_validator("model", mode="before")
    @classmethod
    def _model(cls, value):
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _config_matches_kind(self) -> "Guardrail":
        # Re-normalizing on every load (not only on create/update) means a
        # kind's own rules can tighten later without a migration: an old
        # record either still validates or is reported by the store as
        # absent, the same way environments/store.py treats one that does not.
        self.config = validate_config(self.kind, self.config)
        # A sequence rule is about tool calls and only about them: its stage
        # is always ``tool``, and ``tool`` is only ever a sequence rule's.
        if self.kind == "sequence":
            self.stage = "tool"
            if self.action not in SEQUENCE_ACTIONS:
                raise ValueError("a sequence guardrail's action is 'block' or 'ask'")
        else:
            if self.stage == "tool":
                raise ValueError("only a sequence guardrail checks the 'tool' stage")
            if self.action not in TEXT_ACTIONS:
                raise ValueError(f"a {self.kind} guardrail's action is 'block' or 'warn'")
        return self

    @property
    def archived(self) -> bool:
        return bool(self.archived_at)


__all__ = [
    "Guardrail", "Stage", "Kind", "Action", "AppliesTo", "PII_DETECTORS",
    "SEQUENCE_RULES", "SEQUENCE_ACTIONS", "TEXT_ACTIONS",
    "validate_config", "now_iso",
]
