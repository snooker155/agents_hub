"""
Loop extension: structured output (see agents/agent_loop.py).

Two independent policies live here, both driven by ``AgentSpec`` fields
(agents/registry.py):

* ``output_schema``: the agent's final answer must be a JSON value matching a
  JSON Schema. ``extension_for`` appends an instruction (with the schema) to
  the agent's system prompt at build time; ``finalize_output`` (called by
  ``StandardAgent._structured_output`` after the run finishes) extracts JSON
  from the answer, validates it, and on failure makes up to two repair calls
  before giving up.
* Strict tool schemas: when the workspace turns on ``settings.loop.strict_tools``
  and every tool bound for a call has a strict-compatible schema, ``bind_kwargs``
  asks the model to enforce it. Only OpenAI's ``bind_tools`` accepts a
  ``strict`` keyword in the installed langchain-openai; the installed
  langchain-anthropic has no equivalent (see the module docstring below the
  compatibility check), so this is OpenAI-only for now.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agents.agent_loop import LoopExtension

log = logging.getLogger(__name__)

#: How many separate repair calls a failed validation gets before finalize_output
#: gives up and reports the schema error.
MAX_REPAIR_ATTEMPTS = 2


# ── JSON extraction and schema validation ────────────────────────────────────

def _extract_json(text: str) -> Any:
    """Parse JSON from an answer that may carry prose or a code fence around it.

    Same approach as ``evals.graders._extract_json`` (fence first, then the
    outermost brace/bracket span), kept as a local copy so this module has no
    import-time dependency on ``evals``.
    """
    text = (text or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - fall through to the brace-span heuristic below
        log.debug("structured: whole-text JSON parse failed", exc_info=True)
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:  # noqa: BLE001 - try the other bracket pair
                continue
    return None


def _validate(value: Any, schema: Dict[str, Any]) -> List[str]:
    """Validation errors for ``value`` against ``schema`` (empty when it passes).

    ``value is None`` (no parseable JSON at all) is reported as its own error
    rather than a schema mismatch, so a repair attempt sees a clear reason.
    """
    if value is None:
        return ["no parseable JSON in the output"]
    try:
        import jsonschema
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.path))
        return [f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors]
    except jsonschema.exceptions.SchemaError as e:  # noqa: BLE001 - reported as data, not raised
        return [f"output_schema is not a valid JSON Schema: {e}"]
    except Exception as e:  # noqa: BLE001 - a validator crash counts as invalid, never breaks the run
        return [f"schema validation failed: {type(e).__name__}: {e}"]


def _schema_instruction(schema: Dict[str, Any]) -> str:
    return (
        "\n\nYour final answer must be a single JSON value matching this JSON "
        "Schema exactly, with nothing else before or after it (no prose, no code "
        "fence):\n" + json.dumps(schema, ensure_ascii=False)
    )


# ── Repair calls ──────────────────────────────────────────────────────────────

def _provider_of(agent: Any) -> str:
    try:
        return (agent.effective_provider(getattr(agent, "_llm", None)) or "").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable provider just skips the OpenAI-specific path
        return (getattr(agent, "provider", "") or "").strip().lower()


def _build_repair_model(agent: Any) -> Any:
    from agents.agent_utils import build_chat_model
    return build_chat_model(
        provider=getattr(agent, "provider", None),
        model=getattr(agent, "model", None),
        temperature=0,
        api_key=getattr(agent, "api_key", None),
        base_url=getattr(agent, "base_url", None),
    )


def _repair_prompt(original_text: str, schema: Dict[str, Any], errors: List[str]) -> str:
    errs = "\n".join(f"- {e}" for e in errors[:10])
    return (
        "Your previous answer did not match the required JSON Schema.\n\n"
        f"SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"YOUR PREVIOUS ANSWER:\n{original_text}\n\n"
        f"VALIDATION ERRORS:\n{errs}\n\n"
        "Reply with a single JSON value that matches the schema exactly: no "
        "prose, no code fence, nothing before or after the JSON."
    )


def _repair_openai(llm: Any, schema: Dict[str, Any], prompt: str) -> Tuple[Any, Optional[str]]:
    """Repair through OpenAI's strict Structured Outputs (``method="json_schema"``,
    ``strict=True``), the one combination the installed langchain-openai
    guarantees will validate exactly. On any failure (a schema shape the
    strict mode rejects, a transport error) the caller falls back to a plain
    JSON prompt, so this never has to be the only path."""
    tool_schema = {
        "name": "structured_output",
        "description": "The final answer, matching the required schema exactly.",
        "input_schema": schema,
    }
    try:
        structured = llm.with_structured_output(tool_schema, method="json_schema",
                                                 strict=True, include_raw=True)
        result = structured.invoke(prompt)
    except Exception as e:  # noqa: BLE001 - a rejected strict schema falls back to a plain prompt
        return None, f"strict structured-output call failed: {type(e).__name__}: {e}"
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if parsed is None:
        perr = result.get("parsing_error") if isinstance(result, dict) else None
        return None, f"model did not return a value matching the schema{f': {perr}' if perr else ''}"
    return parsed, None


def _repair_plain(llm: Any, prompt: str) -> Tuple[Any, Optional[str]]:
    """Repair through a plain JSON-only prompt: the fallback for every provider
    whose langchain integration has no strict/schema-forced structured-output
    call (Anthropic, in the installed langchain-anthropic; see the module
    docstring)."""
    from agents.callbacks.run_statistics import content_text
    try:
        raw = llm.invoke(prompt)
    except Exception as e:  # noqa: BLE001 - a failed repair call is reported, not raised
        return None, f"repair call failed: {type(e).__name__}: {e}"
    text = content_text(getattr(raw, "content", raw))
    value = _extract_json(text)
    if value is None:
        return None, "repair call did not return parseable JSON"
    return value, None


def _repair(agent: Any, original_text: str, schema: Dict[str, Any],
            errors: List[str]) -> Tuple[Any, Optional[str]]:
    try:
        llm = _build_repair_model(agent)
    except Exception as e:  # noqa: BLE001 - no repair model available is a failed attempt, not a crash
        return None, f"could not build a repair model: {type(e).__name__}: {e}"
    prompt = _repair_prompt(original_text, schema, errors)
    if _provider_of(agent) == "openai":
        parsed, err = _repair_openai(llm, schema, prompt)
        if parsed is not None:
            return parsed, None
        log.debug("structured: strict repair failed (%s), falling back to a plain prompt", err)
    return _repair_plain(llm, prompt)


# ── Public entry point (called directly by StandardAgent._structured_output) ─

def finalize_output(agent: Any, state: Any, text: str) -> Tuple[str, Optional[str]]:
    """``(text, error)`` for the final answer; error is None when it passes.

    Without an output schema this is a no-op: ``(text, None)`` and nothing is
    recorded on ``state``. With one, the answer is extracted and validated;
    on failure up to :data:`MAX_REPAIR_ATTEMPTS` repair calls are made (see
    :func:`_repair`), each re-validated. ``state.structured`` always ends up
    holding ``{"attempts", "valid", "errors"}`` once a schema is configured,
    whether or not repair succeeded.
    """
    spec = getattr(agent, "spec", None)
    schema = getattr(spec, "output_schema", None) if spec is not None else None
    if not isinstance(schema, dict) or not schema:
        return text, None

    attempts: List[Dict[str, Any]] = []
    value = _extract_json(text)
    errors = _validate(value, schema)
    attempts.append({"source": "answer", "valid": not errors, "errors": list(errors)})

    for i in range(1, MAX_REPAIR_ATTEMPTS + 1):
        if not errors:
            break
        repaired, repair_error = _repair(agent, text, schema, errors)
        if repair_error and repaired is None:
            attempts.append({"source": f"repair_{i}", "valid": False, "errors": [repair_error]})
            continue
        value = repaired
        errors = _validate(value, schema)
        attempts.append({"source": f"repair_{i}", "valid": not errors, "errors": list(errors)})

    valid = not errors
    if state is not None:
        try:
            state.structured = {"attempts": attempts, "valid": valid, "errors": list(errors)}
        except Exception:  # noqa: BLE001 - a state we cannot annotate still finalizes the answer
            log.debug("structured: could not record state.structured", exc_info=True)

    if valid:
        return json.dumps(value, ensure_ascii=False), None
    return text, "The final answer does not match the output schema: " + "; ".join(errors[:5])


# ── Strict tool schemas ───────────────────────────────────────────────────────
#
# OpenAI's strict tool-calling mode (``bind_tools(strict=True)``) requires
# every object in a bound tool's JSON Schema to list *every* one of its
# properties in ``required`` and to never allow additional properties
# (https://platform.openai.com/docs/guides/structured-outputs/supported-schemas);
# an optional argument (the common shape for a pydantic ``Optional[...]``
# field with a default) breaks that. langchain-openai enforces the
# ``additionalProperties: false`` half automatically once ``strict=True`` is
# passed, but not the "every property required" half, so a schema that fails
# it is only caught here, before the call, or by OpenAI as a 400 otherwise.
# The compatibility rule below is exactly that structural check, applied to
# every property recursively (nested objects, array items, and any
# ``$defs``/``definitions`` a pydantic schema draws on).
#
# The installed langchain-anthropic (0.3.14) has no equivalent: its
# ``bind_tools`` takes no ``strict`` keyword, and its own tool conversion
# (``convert_to_anthropic_tool``) drops any such key from a tool dict that is
# not already Anthropic-shaped. Anthropic's raw Messages API does support a
# per-tool ``strict: true`` field (no beta header), but nothing in this
# dependency stack sends it, so strict tool binding here is OpenAI-only until
# langchain-anthropic adds the parameter.

def _schema_all_required(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        if props:
            required = set(schema.get("required") or [])
            if set(props.keys()) - required:
                return False
            for sub in props.values():
                if not _schema_all_required(sub):
                    return False
        if schema.get("additionalProperties") is True:
            return False
    items = schema.get("items")
    if isinstance(items, dict) and not _schema_all_required(items):
        return False
    for key in ("$defs", "definitions"):
        for sub in (schema.get(key) or {}).values():
            if not _schema_all_required(sub):
                return False
    return True


def _tool_is_strict_compatible(tool: Any) -> bool:
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool
        spec = convert_to_openai_tool(tool)
        params = (spec.get("function") or {}).get("parameters") or {}
    except Exception:  # noqa: BLE001 - a tool langchain cannot describe is not strict-compatible
        return False
    return _schema_all_required(params)


# ── Workspace setting (agents.loop_ext.settings.loop_setting, stream E) ─────

def _strict_tools_setting(agent: Any) -> bool:
    """Whether ``settings.loop.strict_tools`` is on for this agent's workspace.

    Reads through ``agents.loop_ext.settings.loop_setting`` when it exists
    (stream E); until it lands, ``_fallback_loop_setting`` reads the same
    workspace field directly so this module works standalone.
    """
    try:
        from agents.loop_ext.settings import loop_setting
        return bool(loop_setting(agent, "strict_tools", False))
    except ImportError:
        return _fallback_loop_setting(agent, "strict_tools", False)
    except Exception:  # noqa: BLE001 - a broken settings helper means no strict tools, not a crash
        log.debug("structured: loop_setting lookup failed", exc_info=True)
        return False


def _fallback_loop_setting(agent: Any, key: str, default: bool) -> bool:
    """Local stand-in for ``agents.loop_ext.settings.loop_setting`` (stream E)
    until that module exists: reads ``settings.loop.<key>`` off the agent's
    workspace metadata the same way the real helper is expected to."""
    try:
        from workspace import get_workspace_metadata
        ws = getattr(agent, "workspace", None) or ""
        if not ws:
            return default
        meta = get_workspace_metadata(ws) or {}
        loop_cfg = (meta.get("settings") or {}).get("loop") or {}
        value = loop_cfg.get(key)
        return default if value is None else bool(value)
    except Exception:  # noqa: BLE001 - a workspace lookup failure keeps the default
        return default


class StructuredOutputExtension(LoopExtension):
    """Appends the schema instruction (done once, at build time, by
    ``extension_for``) and, when strict tools are on and every bound tool
    qualifies, asks OpenAI's ``bind_tools`` to enforce them."""

    name = "structured"

    def __init__(self, provider: str, strict_tools_enabled: bool) -> None:
        self.provider = provider
        self.strict_tools_enabled = strict_tools_enabled

    def bind_kwargs(self, state: Any, llm: Any, tools: List[Any]) -> Dict[str, Any]:
        if not self.strict_tools_enabled or self.provider != "openai":
            return {}
        if not tools or not all(_tool_is_strict_compatible(t) for t in tools):
            return {}
        return {"strict": True}


def extension_for(agent: Any) -> Optional[LoopExtension]:
    spec = getattr(agent, "spec", None)
    raw_schema = getattr(spec, "output_schema", None) if spec is not None else None
    schema = raw_schema if isinstance(raw_schema, dict) and raw_schema else None
    strict_enabled = _strict_tools_setting(agent)

    if schema is None and not strict_enabled:
        return None

    if schema is not None:
        try:
            agent.system_prompt = (getattr(agent, "system_prompt", "") or "") + _schema_instruction(schema)
        except Exception:  # noqa: BLE001 - a prompt we cannot extend still finalizes the answer at the end
            log.warning("structured: could not append the schema instruction to %s",
                        getattr(agent, "agent_id", "?"), exc_info=True)

    return StructuredOutputExtension(provider=_provider_of(agent), strict_tools_enabled=strict_enabled)
