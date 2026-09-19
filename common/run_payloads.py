"""
Canonical structured run payload.

Every execution mode (chat SSE, local subprocess, node worker, docker
container, flow node) historically attached a ``process`` dict of slightly
different shape to its run record. This module defines the ONE canonical
structure every run payload is normalised into before storage, and the
legacy-alias view served back to existing readers.

Canonical payload::

    {
      "input_context": {                # the prompt, split into blocks
          "system_prompt": str,
          "history":       [{role, content}, ...],
          "user_message":  str,
      },
      "response": {                     # the run's final answer
          "text":       str,           # plain-text output
          "structured": dict | None,   # AgentResponse payload (buttons, ...)
      },
      "tool_calls":        [{step, tool, input, output}, ...],
      "reasoning":         [str, ...],  # chronological thinking/trace lines
      "llm_invocations":   [...],       # per-LLM-call structured records
      "llm_raw_responses": [...],       # raw LLMResult dumps
      "artifacts":         [...],       # file-change diffs
      "entities":          [...],       # links to service entities the run touched
      "token_usage": {inbound_tokens, outbound_tokens, total_tokens},
      "duration_ms": int,
    }

Metadata (agent, model, status, timestamps, log-file link, ...) lives on the
run record itself; the plain-text log stays a file referenced by
``record["log_file"]``.

Accepted legacy input keys (normalised away): ``llm_input_context`` (with the
response text and ``llm_invocations`` embedded), ``thinking``,
``llm_invoke_responses``, ``response_text`` / ``response_obj``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Keys of the canonical payload that carry "heavy" data. A payload containing
# none of these (e.g. only token_usage/duration_ms) is a stats-only update and
# must not overwrite a previously stored full payload.
HEAVY_KEYS = (
    "input_context", "llm_input_context",
    "response", "response_text", "response_obj",
    "tool_calls", "reasoning", "thinking",
    "llm_invocations", "llm_invoke_responses", "llm_raw_responses",
    "artifacts", "entities",
)


def _usage(proc: Dict[str, Any]) -> Dict[str, int]:
    tu = proc.get("token_usage") or {}
    return {
        "inbound_tokens": int(tu.get("inbound_tokens") or tu.get("prompt_tokens") or 0),
        "outbound_tokens": int(tu.get("outbound_tokens") or tu.get("completion_tokens") or 0),
        "total_tokens": int(tu.get("total_tokens") or 0),
        "cached_tokens": int(tu.get("cached_tokens") or 0),
    }


def has_heavy_data(proc: Optional[Dict[str, Any]]) -> bool:
    """True when the payload carries more than the slim token/duration stats."""
    if not isinstance(proc, dict):
        return False
    return any(k in proc for k in HEAVY_KEYS)


def canonicalize(proc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise any historical or current ``process`` payload shape into the
    canonical structure documented above. Idempotent."""
    lic = proc.get("llm_input_context")
    lic = lic if isinstance(lic, dict) else {}

    input_context = proc.get("input_context")
    if not isinstance(input_context, dict):
        input_context = {
            "system_prompt": str(lic.get("system_prompt") or ""),
            "history": list(lic.get("history") or []),
            "user_message": str(lic.get("user_message") or ""),
        }
    else:
        input_context = {
            "system_prompt": str(input_context.get("system_prompt") or ""),
            "history": list(input_context.get("history") or []),
            "user_message": str(input_context.get("user_message") or ""),
        }

    response = proc.get("response")
    if not isinstance(response, dict):
        response = {
            "text": str(proc.get("response_text") or lic.get("response") or ""),
            "structured": proc.get("response_obj"),
        }
    else:
        response = {
            "text": str(response.get("text") or ""),
            "structured": response.get("structured"),
        }

    reasoning = proc.get("reasoning")
    if not isinstance(reasoning, list):
        reasoning = list(proc.get("thinking") or [])

    llm_invocations = proc.get("llm_invocations")
    if not isinstance(llm_invocations, list):
        llm_invocations = list(lic.get("llm_invocations") or [])

    llm_raw = proc.get("llm_raw_responses")
    if not isinstance(llm_raw, list):
        llm_raw = list(proc.get("llm_invoke_responses") or [])

    return {
        "input_context": input_context,
        "response": response,
        "tool_calls": list(proc.get("tool_calls") or []),
        "reasoning": reasoning,
        "llm_invocations": llm_invocations,
        "llm_raw_responses": llm_raw,
        "artifacts": list(proc.get("artifacts") or []),
        "entities": list(proc.get("entities") or []),
        "token_usage": _usage(proc),
        "duration_ms": int(proc.get("duration_ms") or 0),
    }


def with_legacy_aliases(canonical: Dict[str, Any]) -> Dict[str, Any]:
    """Return the canonical payload extended with the legacy key aliases the
    existing dashboard routes and helpers read (``llm_input_context`` with
    embedded response/invocations, ``thinking``, ``llm_invoke_responses``)."""
    ic = canonical.get("input_context") or {}
    resp = canonical.get("response") or {}
    legacy_lic = {
        "system_prompt": ic.get("system_prompt", ""),
        "history": ic.get("history", []),
        "user_message": ic.get("user_message", ""),
        "response": resp.get("text", ""),
        "llm_invocations": canonical.get("llm_invocations", []),
    }
    return {
        **canonical,
        "llm_input_context": legacy_lic,
        "thinking": canonical.get("reasoning", []),
        "llm_invoke_responses": canonical.get("llm_raw_responses", []),
    }


def slim_stats(proc: Dict[str, Any]) -> Dict[str, Any]:
    """The projection kept inline on the run record for cheap list views."""
    return {"token_usage": _usage(proc), "duration_ms": int(proc.get("duration_ms") or 0)}
