"""Condition entity: route by comparing a state value against a list of cases.

``state_branch`` only knows truthy and falsy. A switch checks its cases in
order and follows the first that matches; ``default`` is taken when none does,
and with no default every successor is pruned. ``all_matches`` follows every
matching case instead of the first, which runs several branches at once.

Operators: ``equals``, ``not_equals``, ``contains``, ``not_contains``,
``matches`` (a regular expression), ``gt``, ``gte``, ``lt``, ``lte`` (numbers),
``empty``, ``not_empty``, ``count_gt``, ``count_lt`` (the length of a list or
text). Text comparisons ignore case.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from flow.registry import FlowEntitySpec
from flow.state import NodeResult

OPERATORS = (
    "equals", "not_equals", "contains", "not_contains", "matches",
    "gt", "gte", "lt", "lte", "empty", "not_empty", "count_gt", "count_lt",
)

SPEC = FlowEntitySpec(
    id="state_switch",
    name="Switch",
    category="condition",
    group="Control",
    entrypoint="flow.entities.conditions.state_switch:run",
    description="Route to the first case whose condition on a state value holds.",
    inputs=[],
    outputs=[],
    config_schema={
        "key": {"type": "str", "default": "", "description": "State key to test"},
        "cases": {"type": "list", "default": [],
                  "description": "[{op, value, target}] checked in order; op is one of "
                                 + ", ".join(OPERATORS)},
        "default": {"type": "str", "default": "",
                    "description": "Target node id when no case matches"},
        "all_matches": {"type": "bool", "default": False,
                        "description": "Follow every matching case, not just the first"},
    },
    icon="branch",
)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    return float(match.group(0).replace(",", ".")) if match else None


def _length(value: Any) -> int:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    text = str(value or "").strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return len(parsed)
        except ValueError:
            pass
    return len(text)


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False).lower()
    return str(value if value is not None else "").strip().lower()


def matches(op: str, actual: Any, expected: Any) -> bool:
    op = str(op or "equals").strip().lower()
    if op == "empty":
        return _length(actual) == 0
    if op == "not_empty":
        return _length(actual) > 0
    if op in ("count_gt", "count_lt"):
        limit = _number(expected)
        if limit is None:
            return False
        n = _length(actual)
        return n > limit if op == "count_gt" else n < limit
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _number(actual), _number(expected)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "matches":
        try:
            return re.search(str(expected or ""), str(actual or ""), re.IGNORECASE) is not None
        except re.error:
            return False
    a, b = _text(actual), _text(expected)
    if op == "equals":
        return a == b
    if op == "not_equals":
        return a != b
    if op == "contains":
        return b in a
    if op == "not_contains":
        return b not in a
    return False


def case_targets(config: Dict[str, Any]) -> List[str]:
    """Every node id the switch can send the run to (for validation)."""
    out: List[str] = []
    for case in (config or {}).get("cases") or []:
        if isinstance(case, dict) and str(case.get("target") or "").strip():
            out.append(str(case["target"]).strip())
    default = str((config or {}).get("default") or "").strip()
    if default:
        out.append(default)
    return out


def run(state: Any, config: Dict[str, Any], ctx: Any) -> NodeResult:
    cfg = config or {}
    key = str(cfg.get("key") or "").strip()
    value = state.get(key) if (key and hasattr(state, "get")) else None
    chosen: List[str] = []
    for case in cfg.get("cases") or []:
        if not isinstance(case, dict):
            continue
        target = str(case.get("target") or "").strip()
        if target and matches(case.get("op"), value, case.get("value")):
            if target not in chosen:
                chosen.append(target)
            if not cfg.get("all_matches"):
                break
    if not chosen:
        default = str(cfg.get("default") or "").strip()
        if default:
            chosen = [default]
    preview = str(value)[:80] if value is not None else "(missing)"
    return NodeResult(goto=chosen, text=f"{key}={preview} → {', '.join(chosen) or '(no branch)'}")
