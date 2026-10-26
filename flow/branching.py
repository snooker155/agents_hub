"""
Branching that is decided while a flow runs (docs/flows.md, "Branching").

A flow is a DAG fixed before the run, and until this module the only way to
pick a branch was a condition entity reading a state key. Two node shapes make
the graph react to what the agents actually produce:

* **An agent picks the branch** (``route`` on an agent node). The engine adds
  the node's successors to the prompt and asks the agent to end its answer
  with ``ROUTE: <node id>``; the chosen ids become the node's ``goto``, so the
  engine prunes the other successors exactly as it does for a condition node.
  ``route: "many"`` lets the agent pick several. A reply that names no valid
  successor falls back to ``route_default`` when the node has one, and fails
  the node otherwise (its ``retry`` asks again).

* **One step per item** (the ``map`` entity). The node reads a list, from a
  state key or from its predecessors' output, and runs one agent per item, up
  to ``max_parallel`` at once. Each item is its own node run (``<node>#<n>``),
  so it gets its own run record, its own bubble in flow chat and its own line
  in the run log. The results land in the node's output key as a list, in
  item order. The number of items is known only at run time: this is the
  fan-out a fixed graph cannot express.

The engine (flow/engine.py) owns the scheduling; everything here is pure:
reading config, building the extra prompt text, parsing a reply.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The entity id of the fan-out node.
MAP_ENTITY_ID = "map"
#: Its registry category, which the engine checks to run it itself.
MAP_CATEGORY = "map"

#: How many items one map node may run when its config does not say.
DEFAULT_MAX_ITEMS = 50
#: The hard ceiling, whatever the config says.
MAX_ITEMS_LIMIT = 500
#: Items running at once when neither the node nor the flow says.
DEFAULT_ITEM_PARALLEL = 4

#: The line an agent ends with to pick its branch.
_ROUTE_LINE_RE = re.compile(r"^\s*\**\s*ROUTE\s*\**\s*[:=]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _field(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    if key in node:
        return node[key]
    data = node.get("data")
    if isinstance(data, dict) and key in data:
        return data[key]
    return default


# ── an agent picks the branch ───────────────────────────────────────────────

def route_mode(node: Dict[str, Any]) -> str:
    """``"one"``, ``"many"`` or ``""`` (the node does not route)."""
    raw = _field(node, "route")
    if raw is True:
        return "one"
    value = str(raw or "").strip().lower()
    if value in ("one", "single", "true", "yes", "1"):
        return "one"
    if value in ("many", "multi", "multiple", "any"):
        return "many"
    return ""


def route_targets(node: Dict[str, Any], node_id: str, successors: Dict[str, List[str]],
                  nodes_by_id: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str]]:
    """``(successor id, what it does)`` for a routing agent node, or ``[]``
    when the node does not route or has fewer than two successors to pick from."""
    if not route_mode(node):
        return []
    out: List[Tuple[str, str]] = []
    for sid in successors.get(node_id, []):
        target = nodes_by_id.get(sid) or {}
        label = str(_field(target, "label") or sid)
        what = str(_field(target, "description") or _field(target, "nodeTask") or "").strip()
        out.append((sid, f"{label}: {what}" if what else label))
    return out if len(out) >= 2 else []


def route_instructions(node: Dict[str, Any], targets: Sequence[Tuple[str, str]]) -> str:
    """The text appended to a routing node's prompt."""
    many = route_mode(node) == "many"
    lines = [
        "",
        "## Choose the next step",
        "After your answer, decide which step of the flow runs next. The steps are:",
    ]
    lines += [f"- `{sid}`: {what}" for sid, what in targets]
    if many:
        lines.append("End your reply with one line `ROUTE: <step id>, <step id>` naming every "
                     "step that should run (at least one).")
    else:
        lines.append("End your reply with exactly one line `ROUTE: <step id>` naming the one "
                     "step that should run.")
    hint = str(_field(node, "route_hint") or "").strip()
    if hint:
        lines.append(f"How to choose: {hint}")
    return "\n".join(lines)


def parse_route(output: str, node: Dict[str, Any],
                targets: Sequence[Tuple[str, str]]) -> Tuple[Optional[List[str]], str, str]:
    """``(goto, cleaned output, error)`` from a routing node's reply.

    The last ``ROUTE:`` line wins and is removed from the output, so the next
    agent reads the answer without the routing line. Ids are matched exactly,
    then without case. ``goto`` is None when nothing valid was named and the
    node has no usable ``route_default``; ``error`` then says why.
    """
    text = str(output or "")
    valid = [sid for sid, _ in targets]
    lower = {sid.lower(): sid for sid in valid}
    matches = list(_ROUTE_LINE_RE.finditer(text))
    chosen: List[str] = []
    if matches:
        last = matches[-1]
        raw = last.group(1).strip().strip("`*").strip()
        for part in re.split(r"[,;\s]+", raw):
            token = part.strip().strip("`*'\".").strip()
            if not token:
                continue
            sid = token if token in valid else lower.get(token.lower())
            if sid and sid not in chosen:
                chosen.append(sid)
        text = (text[:last.start()] + text[last.end():]).rstrip()
    if chosen and route_mode(node) != "many":
        chosen = chosen[:1]
    if chosen:
        return chosen, text, ""
    default = str(_field(node, "route_default") or "").strip()
    if default in valid:
        return [default], text, ""
    named = matches[-1].group(1).strip() if matches else ""
    error = (f"the agent named no valid next step (said {named!r}; valid: {', '.join(valid)})"
             if named else
             f"the agent did not end with a ROUTE line (valid steps: {', '.join(valid)})")
    return None, text, error


# ── one step per item ───────────────────────────────────────────────────────

def is_map_node(spec: Any) -> bool:
    return bool(spec is not None and getattr(spec, "category", "") == MAP_CATEGORY)


def map_config(node: Dict[str, Any]) -> Dict[str, Any]:
    """The map node's config with defaults filled in."""
    raw = _field(node, "config") or {}
    cfg = dict(raw) if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {
        "agent_id": str(cfg.get("agent_id") or "").strip(),
        "over": str(cfg.get("over") or "").strip(),
        "task": str(cfg.get("task") or "").strip(),
        "include_context": bool(cfg.get("include_context")),
        "allow_failures": bool(cfg.get("allow_failures")),
    }
    out["max_items"] = _bounded_int(cfg.get("max_items"), DEFAULT_MAX_ITEMS, 1, MAX_ITEMS_LIMIT)
    out["max_parallel"] = _bounded_int(cfg.get("max_parallel"), 0, 0, 64)
    return out


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _json_list(text: str) -> Optional[List[Any]]:
    candidates = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCED_JSON_RE.finditer(text)]
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        if not cand:
            continue
        try:
            value = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ("items", "results", "list"):
                if isinstance(value.get(key), list):
                    return value[key]
    return None


def items_from(value: Any) -> List[str]:
    """The items a map node runs over.

    A list is taken as it is. Text is read as a JSON list first (bare, in a
    fenced block, or the first ``[...]`` in it, also ``{"items": [...]}``),
    then as one item per non-empty line with list bullets removed. A dict or
    a scalar is one item. Each item becomes text: a dict or list as JSON.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw = list(value)
    elif isinstance(value, str):
        parsed = _json_list(value)
        if parsed is not None:
            raw = parsed
        else:
            raw = [_BULLET_RE.sub("", line).strip() for line in value.splitlines()]
    else:
        raw = [value]
    out: List[str] = []
    for item in raw:
        if isinstance(item, (dict, list)):
            text = json.dumps(item, ensure_ascii=False)
        else:
            text = str(item if item is not None else "").strip()
        if text:
            out.append(text)
    return out


def item_task(template: str, index: int, count: int) -> str:
    """The task one item's agent gets. ``{index}`` and ``{count}`` are filled;
    ``{item}`` points at the fenced item block the prompt carries below."""
    task = template or "Do this step for the item below."
    return (task.replace("{item}", "the item below")
                .replace("{index}", str(index))
                .replace("{count}", str(count)))


def fence_item(item: str, *, source: str) -> str:
    """One item as the agent reads it: fenced as data, since a list another
    step produced may repeat a hostile page it read (common/untrusted.py).
    Without that module the item still sits in its own labelled block."""
    try:
        from common.untrusted import FLOW_STEP_NOTE, agent_output_fence
    except ImportError:
        return f"<<<AGENT_OUTPUT>>>\nsource: {source}\n---\n{item}\n<<<END_AGENT_OUTPUT>>>"
    return agent_output_fence(item, source=source, note=FLOW_STEP_NOTE)


def item_node_id(node_id: str, index: int) -> str:
    return f"{node_id}#{index}"


def combined_output(results: Sequence[Dict[str, Any]]) -> str:
    """The map node's text output: every item's result under its number."""
    parts = []
    for r in results:
        head = f"### Item {r['index']}"
        body = r.get("output") if r.get("ok") else f"(failed: {r.get('error') or 'unknown error'})"
        parts.append(f"{head}\n{body or ''}".rstrip())
    return "\n\n".join(parts)



def node_route(ev: dict, flow: Dict[str, Any]) -> Optional[dict]:
    """What a node that picks the next step chose, for the chat bubble:
    ``{"targets": [{node_id, label}], "on": "key=value"}``. The ``on`` part
    is the condition half of the node's summary (state_switch/state_branch
    write ``key=value → target``). None for a node that chose nothing."""
    goto = ev.get("goto")
    if goto is None:
        return None
    labels = {}
    for n in flow.get("nodes") or []:
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        labels[n.get("id")] = n.get("label") or data.get("label") or n.get("id")
    text = str(ev.get("text") or "")
    detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else None
    return {
        "targets": [{"node_id": t, "label": labels.get(t) or t} for t in goto],
        "on": text.rsplit(" → ", 1)[0] if " → " in text else "",
        # The condition itself (state_switch/state_branch), for the bubble
        # to say what was tested and whether it held; `on` is the fallback.
        **({"detail": detail} if detail else {}),
    }


__all__ = [
    "DEFAULT_ITEM_PARALLEL", "DEFAULT_MAX_ITEMS", "MAP_CATEGORY", "MAP_ENTITY_ID", "MAX_ITEMS_LIMIT",
    "combined_output", "fence_item", "is_map_node", "item_node_id", "item_task", "items_from", "map_config",
    "node_route", "parse_route", "route_instructions", "route_mode", "route_targets",
]
