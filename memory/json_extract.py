"""Robust JSON-object extraction from LLM output.

Shared by the knowledge- and graph-extraction pipelines. Reasoning models
(Qwen3, DeepSeek-R1, gpt-oss, … served via LM Studio/Ollama) may emit their
chain-of-thought inline in the message content as ``<think>…</think>`` before the
JSON answer. That reasoning text routinely contains braces, which defeats a naive
"first ``{`` … last ``}``" span. These helpers strip such blocks and locate the
first *balanced* JSON object so trailing prose or brace-laden reasoning can't
corrupt the parse.
"""
from __future__ import annotations

import json
import re
from typing import Optional

_REASONING_BLOCK_RE = re.compile(
    r"<(think|thought|thinking|reasoning)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# An unclosed opener (output truncated mid-reasoning, or the answer follows a
# dangling <think>) — drop everything up to and including the tag.
_REASONING_OPEN_RE = re.compile(
    r"^.*?<(?:think|thought|thinking|reasoning)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)


def strip_reasoning(text: str) -> str:
    """Remove ``<think>``/``<reasoning>`` blocks (closed or dangling) from text."""
    text = _REASONING_BLOCK_RE.sub("", text)
    if re.search(r"<(?:think|thought|thinking|reasoning)\b", text, re.IGNORECASE):
        text = _REASONING_OPEN_RE.sub("", text)
    return text


def first_balanced_object(text: str) -> Optional[dict]:
    """Return the first complete, balanced ``{...}`` object that parses as JSON.

    Scans brace-by-brace (string/escape aware) so trailing prose after the object
    — or an earlier ``{`` that opens a non-JSON fragment — doesn't corrupt the
    parse. Falls back through successive ``{`` candidates until one parses.
    """
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, n):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[i : j + 1])
                    except Exception:
                        break  # this candidate didn't parse; try the next "{"
                    if isinstance(obj, dict):
                        return obj
                    break
        i += 1
    return None


def extract_json_object(raw: str) -> Optional[dict]:
    """Best-effort extraction of a single JSON object from raw LLM output."""
    if not raw:
        return None
    cleaned = strip_reasoning(raw).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    obj = first_balanced_object(cleaned)
    if obj is not None:
        return obj
    # Last-ditch fallback: the original naive span, in case the balanced scan
    # missed a valid object wrapped in unusual ways.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start : end + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None
