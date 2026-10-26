"""Condition entity: route by conditions on state values.

``state_branch`` only knows truthy and falsy. A switch has a list of routes,
each a next step and the conditions that lead there: every condition names
its own state key, and ``match`` says whether all of them (``all``, the
default) or any one (``any``) must hold. Routes are checked in order and the
first that holds is followed; ``all_matches`` follows every route that holds,
which runs several branches at once. ``default`` is taken when no route
holds, a switch with no conditions included, and with no default every
successor is pruned. A condition with ``enabled: false`` is switched off: it
is not checked, and a route whose conditions are all off never holds.

The first shape had one ``key`` for the whole switch and ``cases`` of
``{op, value, target}``; :func:`routes_of` still reads it, one route per case.

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

NO_VALUE_OPS = frozenset({"empty", "not_empty"})
MATCH_MODES = ("all", "any")

SPEC = FlowEntitySpec(
    id="state_switch",
    name="Switch",
    category="condition",
    group="Control",
    entrypoint="flow.entities.conditions.state_switch:run",
    description="Route to the first next step whose conditions on state values hold.",
    inputs=[],
    outputs=[],
    config_schema={
        "routes": {"type": "list", "default": [],
                   "description": "[{target, match: all|any, conditions: [{key, op, value}]}] "
                                  "checked in order; op is one of " + ", ".join(OPERATORS)},
        "default": {"type": "str", "default": "",
                    "description": "Target node id when no route holds"},
        "all_matches": {"type": "bool", "default": False,
                        "description": "Follow every route that holds, not just the first"},
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


def routes_of(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The switch's routes as ``{target, match, conditions: [{key, op, value}]}``.

    Reads ``routes``, or the first shape (one ``key``, ``cases`` of
    ``{op, value, target}``) as one route per case on that key.
    """
    cfg = config or {}
    out: List[Dict[str, Any]] = []
    # The engine fills ``routes: []`` from the schema default, so the first
    # shape is the one with cases and no routes.
    if cfg.get("routes") or not cfg.get("cases"):
        for route in cfg.get("routes") or []:
            if not isinstance(route, dict):
                continue
            conditions = [
                {"key": str(c.get("key") or "").strip(), "op": str(c.get("op") or "equals"),
                 "value": c.get("value"), "enabled": c.get("enabled") is not False}
                for c in route.get("conditions") or [] if isinstance(c, dict)
            ]
            out.append({"target": str(route.get("target") or "").strip(),
                        "match": str(route.get("match") or "all").strip().lower(),
                        "conditions": conditions})
        return out
    key = str(cfg.get("key") or "").strip()
    for case in cfg.get("cases") or []:
        if isinstance(case, dict):
            out.append({"target": str(case.get("target") or "").strip(), "match": "all",
                        "conditions": [{"key": key, "op": str(case.get("op") or "equals"),
                                        "value": case.get("value"), "enabled": True}]})
    return out


def case_targets(config: Dict[str, Any]) -> List[str]:
    """Every node id the switch can send the run to (for validation)."""
    out = [r["target"] for r in routes_of(config) if r["target"]]
    default = str((config or {}).get("default") or "").strip()
    if default:
        out.append(default)
    return out


def config_errors(config: Dict[str, Any], label: str) -> List[str]:
    """What makes a switch's routes unusable, for the check before a run.

    A route needs a next step and at least one condition; a condition that is
    on needs a key, a known operator and, unless it tests emptiness, a value
    (an empty one would make ``contains`` always hold). One switched off is not
    checked, so it is not held to that.
    """
    errors: List[str] = []
    for n, route in enumerate(routes_of(config), start=1):
        where = f"switch '{label}', route {n}" + (f" to '{route['target']}'" if route["target"] else "")
        if not route["target"]:
            errors.append(f"{where}: no next step chosen")
        if route["match"] not in MATCH_MODES:
            errors.append(f"{where}: match must be all or any")
        if not route["conditions"]:
            errors.append(f"{where}: no conditions")
        for i, cond in enumerate(route["conditions"], start=1):
            if not cond["enabled"]:
                continue
            if not cond["key"]:
                errors.append(f"{where}, condition {i}: no state key")
            if cond["op"] not in OPERATORS:
                errors.append(f"{where}, condition {i}: unknown operator '{cond['op']}'")
            elif cond["op"] not in NO_VALUE_OPS and str(cond["value"] if cond["value"] is not None else "").strip() == "":
                errors.append(f"{where}, condition {i}: no value to compare with")
    return errors


def _describe(cond: Dict[str, Any]) -> str:
    value = "" if cond["op"] in NO_VALUE_OPS else f" {cond['value']}"
    return f"{cond['key']} {cond['op']}{value}"


def run(state: Any, config: Dict[str, Any], ctx: Any) -> NodeResult:
    cfg = config or {}
    chosen: List[str] = []
    checked: List[Dict[str, Any]] = []
    for route in routes_of(cfg):
        active = [c for c in route["conditions"] if c["enabled"]]
        if not route["target"] or not active:
            continue
        conds = []
        for cond in active:
            key = cond["key"]
            value = state.get(key) if (key and hasattr(state, "get")) else None
            # A condition with no key tests nothing, so it never holds.
            held = bool(key) and matches(cond["op"], value, cond["value"])
            conds.append({"key": key, "op": cond["op"], "value": cond["value"],
                          "held": held, "missing": value is None})
        results = [c["held"] for c in conds]
        held = any(results) if route["match"] == "any" else all(results)
        checked.append({"target": route["target"], "match": route["match"],
                        "conditions": conds, "held": held})
        if held:
            if route["target"] not in chosen:
                chosen.append(route["target"])
            if not cfg.get("all_matches"):
                break
    by_default = False
    if not chosen:
        default = str(cfg.get("default") or "").strip()
        if default:
            chosen = [default]
            by_default = True
    held_routes = [r for r in checked if r["held"]]
    if held_routes:
        joiner = {"all": " and ", "any": " or "}
        on = "; ".join(joiner.get(r["match"], " and ").join(_describe(c) for c in r["conditions"])
                       for r in held_routes)
    else:
        on = "no condition held"
    return NodeResult(
        goto=chosen, text=f"{on} → {', '.join(chosen) or '(no branch)'}",
        detail={"routes": checked, "default": by_default},
    )
