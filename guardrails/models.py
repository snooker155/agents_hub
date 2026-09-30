"""The guardrail record and its validation.

A guardrail is a workspace object like an environment (environments/models.py):
a small, named, versionless profile that a run is checked against rather than
launched inside. Every field the check functions (guardrails/checks.py) read
is validated once, here, so a stored record is always safe to run: a bad
regex, an empty instruction or an unknown PII detector never reaches a run,
it is refused at create/update time instead.

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

Stage = Literal["input", "output", "both"]
Kind = Literal["regex", "keywords", "pii", "max_chars", "judge"]
Action = Literal["block", "warn"]
AppliesTo = Literal["all", "selected"]

#: Built-in PII detectors a ``pii`` guardrail may pick from (guardrails/checks.py).
PII_DETECTORS = ("email", "phone", "credit_card", "iban", "api_key")

_NAME_MAX = 120
_DESCRIPTION_MAX = 2000
_INSTRUCTION_MAX = 4000
_REGEX_FLAGS = ("IGNORECASE", "MULTILINE", "DOTALL")


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

    if kind == "judge":
        instruction = str(config.get("instruction") or "").strip()
        if not instruction:
            raise ValueError("a judge guardrail needs an 'instruction'")
        if len(instruction) > _INSTRUCTION_MAX:
            raise ValueError(f"'instruction' is longer than {_INSTRUCTION_MAX} characters")
        return {"instruction": instruction}

    raise ValueError(f"unknown guardrail kind '{kind}'")


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
        return self

    @property
    def archived(self) -> bool:
        return bool(self.archived_at)


__all__ = [
    "Guardrail", "Stage", "Kind", "Action", "AppliesTo", "PII_DETECTORS",
    "validate_config", "now_iso",
]
