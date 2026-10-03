"""
Sequence guardrails: rules about the order and the totals of a run's tool calls.

The text guardrails (guardrails/runtime.py) look at what goes into a run and
what comes out of it. Some rules are about neither: "pay only after the
invoice was checked", "no more than 500 in transfers per run", "refund to the
account the order was paid from". Each of those is a fact about the calls a
run already made, so it can only be checked right before the next call, with
the earlier ones at hand. ``agents.hooks.ToolGuard`` asks here before every
wrapped call (:func:`check_call`, from ``ToolGuard._sequence``) and tells here
once a call ran (:func:`note_call`, from ``ToolGuard.after``).

A ``sequence`` guardrail (guardrails/models.py) holds one rule:

* ``after``: ``tool`` only once ``after_tool`` ran, with ``require_success``
  only once it ran without an error;
* ``sum_max``: the ``argument`` of every call of ``tools`` adds up to at most
  ``max``; a value that is there but is not a number stops the call, since a
  total that cannot be read cannot be kept;
* ``same_as``: ``argument`` of ``tool`` equals ``source_argument`` of an
  earlier call of ``source_tool``.

Tool names are matched as globs (``mcp__bank__*``), argument paths are dotted
(``payee.account``). The action is ``block`` (the call is refused, the reason
goes back as the tool's output) or ``ask`` (held for a person through the
guard's ``_hold``: a task parks, a chat turn waits for an answer).

What the rules look back at is the trail: per run, the calls that ran, each
with whether it succeeded and the values of the argument paths any rule
names (nothing else of the input is kept). The trail is keyed by the task
when the run has one, else by the run: a task resumed after an approval
starts a fresh run, and a total that reset on every resume would be no total
at all. It lives in memory and, once a call was noted, in the
``guardrail_sequence`` collection, so a run picking up in another process (a
checkpoint resume, a resumed task) reads it back on its first check. Calls
checked but not yet finished are reserved against ``sum_max`` totals, so two
parallel calls cannot both pass a limit only one of them fits.

Findings are recorded like the text kinds': in ``state.guardrails`` (the
run's ``loop.guardrails``), the guardrail events table with a masked excerpt
of the call's input, and the audit log (``guardrail.trip`` for a block,
``guardrail.ask`` for a hold). Passes are not recorded.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Trails kept in memory per process; the oldest are dropped (and read back
#: from the collection if their run asks again).
_MAX_TRAILS = 256
#: Calls kept per trail. A run past this many calls keeps its newest ones;
#: a ``sum_max`` total keeps counting the dropped ones through ``carried``.
_MAX_CALLS = 500
#: A reservation (a checked call that has not finished) counts for this long.
#: A call refused after the check (the tool policy, a person saying no) never
#: reports back, so its reservation must not hold a total down for ever.
_RESERVATION_TTL = 900.0
#: How long the applicable rules are reused before the store is read again,
#: for a call outside a loop run (no LoopState to cache them on).
_RULES_TTL = 5.0
_VALUE_MAX = 200
_EXCERPT_CHARS = 400
_SCRATCH_RULES = "_sequence_guardrails"

_store: Any = None
_lock = threading.Lock()
_trails: "OrderedDict[str, _Trail]" = OrderedDict()
_rules_cache: Dict[Tuple[str, Tuple[str, ...]], Tuple[float, List[Any]]] = {}


def _docstore() -> Any:
    global _store
    if _store is None:
        from common.docstore import DocStore
        _store = DocStore("guardrail_sequence")
    return _store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the trail ────────────────────────────────────────────────────────────────

class _Trail:
    """What one run (or task) did so far, as far as the rules care."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.lock = threading.Lock()
        #: ``{"tool", "ok", "values": {path: value}}`` per call that ran.
        self.calls: List[Dict[str, Any]] = []
        #: Per argument path: the sum of the numeric values of calls dropped
        #: from ``calls``, by tool name, so a long run's total stays whole.
        self.carried: Dict[str, Dict[str, float]] = {}
        #: Checked, not yet finished: ``fingerprint -> (tool, values, at)``.
        self.reserved: Dict[str, Tuple[str, Dict[str, Any], float]] = {}

    def load(self, doc: Dict[str, Any]) -> None:
        self.calls = [c for c in (doc.get("calls") or []) if isinstance(c, dict)]
        carried = doc.get("carried")
        self.carried = carried if isinstance(carried, dict) else {}

    def dump(self) -> Dict[str, Any]:
        return {"key": self.key, "calls": self.calls, "carried": self.carried, "at": _now_iso()}

    def live_reservations(self) -> List[Tuple[str, Dict[str, Any]]]:
        cutoff = time.monotonic() - _RESERVATION_TTL
        for fp in [fp for fp, (_t, _v, at) in self.reserved.items() if at < cutoff]:
            self.reserved.pop(fp, None)
        return [(tool, values) for tool, values, _at in self.reserved.values()]

    def add(self, call: Dict[str, Any]) -> None:
        self.calls.append(call)
        while len(self.calls) > _MAX_CALLS:
            old = self.calls.pop(0)
            for path, value in (old.get("values") or {}).items():
                number = _number(value)
                if number is not None:
                    by_tool = self.carried.setdefault(path, {})
                    by_tool[old["tool"]] = by_tool.get(old["tool"], 0.0) + number


def _trail_key(run_id: str, task_id: str) -> str:
    if task_id:
        return f"task:{task_id}"
    if run_id:
        return f"run:{run_id}"
    return ""


def _trail(key: str) -> _Trail:
    """The trail for *key*: from memory, else read back from the collection
    (a run picking up in a new process), else a new one."""
    with _lock:
        trail = _trails.get(key)
        if trail is not None:
            _trails.move_to_end(key)
            return trail
    fresh = _Trail(key)
    try:
        doc = _docstore().get(key)
        if isinstance(doc, dict):
            fresh.load(doc)
    except Exception:  # noqa: BLE001 - no stored trail (a container without the database) starts empty
        log.debug("sequence guardrails: could not read the trail %s", key, exc_info=True)
    with _lock:
        trail = _trails.setdefault(key, fresh)
        _trails.move_to_end(key)
        while len(_trails) > _MAX_TRAILS:
            _trails.popitem(last=False)
    return trail


def _save(trail: _Trail) -> None:
    try:
        _docstore().put(trail.key, trail.dump())
    except Exception:  # noqa: BLE001 - the memory copy still serves this process
        log.debug("sequence guardrails: could not store the trail %s", trail.key, exc_info=True)


def reset() -> None:
    """Forget every trail and cached rule list held in memory (tests)."""
    with _lock:
        _trails.clear()
        _rules_cache.clear()


def prune(retention_days: int) -> int:
    """Drop stored trails not written for ``retention_days``. Called with the
    guardrail events' own retention (guardrails.runtime.prune_events)."""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat()
    pruned = 0
    store = _docstore()
    for key, doc in store.all().items():
        if isinstance(doc, dict) and str(doc.get("at") or "") < cutoff and store.delete(key):
            pruned += 1
    return pruned


# ── reading a call ───────────────────────────────────────────────────────────

def _as_dict(tool_input: Any) -> Dict[str, Any]:
    """A call's arguments as a dict: a JSON string is parsed, a bare value is
    the single argument ``input`` (how a one-argument LangChain tool gets it)."""
    if isinstance(tool_input, dict):
        return tool_input
    if isinstance(tool_input, str):
        try:
            parsed = json.loads(tool_input)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"input": tool_input}


_MISSING = object()


def _lookup(args: Dict[str, Any], path: str) -> Any:
    """The value at a dotted path, :data:`_MISSING` when there is none. A
    numeric part indexes a list."""
    current: Any = args
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _scalar(value: Any) -> Any:
    """What the trail keeps of one value: a scalar as is, anything else as
    short JSON text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_VALUE_MAX]
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:_VALUE_MAX]
    except (TypeError, ValueError):
        return str(value)[:_VALUE_MAX]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace("_", ""))
        except ValueError:
            return None
    return None


def _same(a: Any, b: Any) -> bool:
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None:
        return na == nb
    return str(a).strip() == str(b).strip()


def _matches(pattern: str, tool: str) -> bool:
    return fnmatch.fnmatchcase(str(tool or ""), str(pattern or ""))


def _succeeded(output: Any) -> bool:
    """Whether a tool's output reads as a success. Covers the conventions
    the tools here use: a ToolMessage marked ``error``, an ``ERROR:``/
    ``Error:`` string (the executor's text for a call that raised), and an
    ``{"ok": false}`` or ``{"error": ...}`` JSON envelope."""
    if getattr(output, "status", None) == "error":
        return False
    content = getattr(output, "content", output)
    if isinstance(content, (dict, list)):
        parsed: Any = content
    else:
        text = str(content or "").lstrip()
        if text[:6].lower() == "error:":
            return False
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return True
    if isinstance(parsed, dict):
        if parsed.get("ok") is False or parsed.get("error"):
            return False
    return True


def _paths(rules: Iterable[Any]) -> List[str]:
    """Every argument path any rule reads, so the trail keeps those values."""
    out: List[str] = []
    for g in rules:
        cfg = g.config
        for key in ("argument", "source_argument"):
            path = cfg.get(key)
            if path and path not in out:
                out.append(path)
    return out


def _values(args: Dict[str, Any], paths: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for path in paths:
        value = _lookup(args, path)
        if value is not _MISSING:
            out[path] = _scalar(value)
    return out


# ── the rules ────────────────────────────────────────────────────────────────

def _fmt(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def evaluate(config: Dict[str, Any], calls: List[Dict[str, Any]], tool: str, args: Dict[str, Any],
             *, reserved: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
             carried: Optional[Dict[str, Dict[str, float]]] = None) -> Optional[str]:
    """Why one rule stops this call, or None when it lets it through.

    ``calls`` are the calls that ran before, oldest first, each
    ``{"tool", "ok", "values"}``; ``reserved`` the calls checked and still
    running (``sum_max`` counts them); ``carried`` the totals of calls the
    trail no longer lists. Pure: the Test box on the Guardrails page runs the
    same function over pasted calls.
    """
    rule = config.get("rule")
    if rule == "after":
        if not _matches(config["tool"], tool):
            return None
        need_ok = bool(config.get("require_success"))
        for call in calls:
            if _matches(config["after_tool"], call.get("tool", "")) and (call.get("ok", True) or not need_ok):
                return None
        if need_ok:
            return f"`{tool}` may only run after `{config['after_tool']}` has run successfully in this run"
        return f"`{tool}` may only run after `{config['after_tool']}` has run in this run"

    if rule == "sum_max":
        patterns = list(config.get("tools") or [])
        if not any(_matches(p, tool) for p in patterns):
            return None
        path = config["argument"]
        raw = _lookup(args, path)
        if raw is _MISSING or raw is None:
            return None
        this = _number(raw)
        if this is None:
            return f"`{path}` of `{tool}` is not a number, so the total cannot be kept"
        total = this
        for name, value in (carried or {}).get(path, {}).items():
            if any(_matches(p, name) for p in patterns):
                total += float(value)
        earlier = [(c.get("tool", ""), c.get("values") or {}) for c in calls] + list(reserved or [])
        for name, values in earlier:
            if any(_matches(p, name) for p in patterns):
                number = _number(values.get(path))
                if number is not None:
                    total += number
        limit = float(config.get("max") or 0)
        if total > limit:
            return (f"the total of `{path}` across {', '.join(f'`{p}`' for p in patterns)} "
                    f"would be {_fmt(total)}, over the limit of {_fmt(limit)} for this run")
        return None

    if rule == "same_as":
        if not _matches(config["tool"], tool):
            return None
        path, source_path = config["argument"], config["source_argument"]
        source_tool = config["source_tool"]
        value = _lookup(args, path)
        sources = [c.get("values", {}).get(source_path) for c in calls
                   if _matches(source_tool, c.get("tool", "")) and source_path in (c.get("values") or {})]
        if not sources:
            return (f"`{path}` of `{tool}` must match `{source_path}` of an earlier "
                    f"`{source_tool}` call, and there is none in this run")
        if value is _MISSING:
            return f"`{tool}` must name `{path}`, to be checked against an earlier `{source_tool}` call"
        if any(_same(value, s) for s in sources):
            return None
        return (f"`{path}` of `{tool}` does not match `{source_path}` of any earlier "
                f"`{source_tool}` call in this run")
    return None


def simulate(guardrail: Any, calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The Test box: run one sequence guardrail over pasted calls
    (``[{"tool", "input", "ok"}]``) as if a run made them in that order.
    Returns the index of the first call it would stop and why; nothing is
    recorded anywhere."""
    paths = _paths([guardrail])
    trail: List[Dict[str, Any]] = []
    for index, call in enumerate(calls or []):
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or "")
        args = _as_dict(call.get("input") if "input" in call else call.get("args") or {})
        reason = evaluate(guardrail.config, trail, tool, args)
        if reason:
            return {"passed": False, "index": index, "tool": tool, "reason": reason}
        trail.append({"tool": tool, "ok": call.get("ok", True) is not False,
                      "values": _values(args, paths)})
    return {"passed": True, "index": None, "reason": ""}


# ── which rules apply ────────────────────────────────────────────────────────

def _agent_ids(spec: Any) -> List[str]:
    return list(getattr(spec, "guardrails", None) or []) if spec is not None else []


def applicable(workspace: Optional[str], spec: Any = None) -> List[Any]:
    """The enabled sequence guardrails one agent's calls are held to."""
    from guardrails import service
    return [g for g in service.list_applicable(workspace or None, _agent_ids(spec))
            if g.kind == "sequence"]


def has_rules(workspace: Optional[str], spec: Any = None) -> bool:
    """Whether any sequence guardrail applies to this agent: decides, once
    per agent build, whether its tools are wrapped at all
    (agents.hooks.guard_action_tools). An unreadable store answers no, the
    same as the text kinds do."""
    try:
        return bool(applicable(workspace, spec))
    except Exception:  # noqa: BLE001 - a store that cannot be read has no rules, not a broken build
        log.debug("sequence guardrails: could not read the rules", exc_info=True)
        return False


def _rules(guard: Any) -> List[Any]:
    """The rules for this guard's calls: once per run on its LoopState, else
    for a few seconds per (workspace, selected ids)."""
    from agents.agent_loop import current_state
    state = current_state()
    if state is not None and _SCRATCH_RULES in state.scratch:
        return state.scratch[_SCRATCH_RULES]
    workspace = getattr(guard, "workspace", None)
    spec = getattr(guard, "spec", None)
    cache_key = (str(workspace or ""), tuple(_agent_ids(spec)))
    cached = _rules_cache.get(cache_key)
    if state is None and cached is not None and time.monotonic() - cached[0] < _RULES_TTL:
        return cached[1]
    rows = applicable(workspace, spec)
    if state is not None:
        state.scratch[_SCRATCH_RULES] = rows
    else:
        _rules_cache[cache_key] = (time.monotonic(), rows)
    return rows


# ── recording a finding ──────────────────────────────────────────────────────

def _excerpt(tool_input: Any) -> str:
    from guardrails import checks
    try:
        text = json.dumps(tool_input, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(tool_input)
    hits: List[str] = []
    for finder in (checks.find_emails, checks.find_credit_cards, checks.find_ibans,
                   checks.find_api_keys, checks.find_phones):
        hits.extend(finder(text))
    return checks.mask(text, hits)[:_EXCERPT_CHARS]


def _record(guard: Any, guardrail: Any, *, tool: str, tool_input: Any, reason: str,
            run_id: str, task_id: str) -> None:
    from agents.agent_loop import current_state
    from guardrails import store
    state = current_state()
    workspace = getattr(guard, "workspace", None) or None
    agent_id = str(getattr(guard, "agent_id", "") or "")
    if state is not None:
        state.guardrails.append({
            "guardrail_id": guardrail.id, "name": guardrail.name, "stage": "tool",
            "kind": guardrail.kind, "passed": False, "reason": reason,
            "action": guardrail.action, "tool": tool,
        })
    try:
        store.add_event({
            "run_id": run_id, "task_id": task_id, "agent_id": agent_id, "workspace": workspace,
            "stage": "tool", "guardrail_id": guardrail.id, "guardrail_name": guardrail.name,
            "kind": guardrail.kind, "action": guardrail.action, "reason": reason,
            "tool": tool, "excerpt": _excerpt(tool_input), "at": _now_iso(),
        })
    except Exception:  # noqa: BLE001 - the event log is best-effort, the decision stands
        log.warning("sequence guardrails: could not record an event", exc_info=True)
    try:
        from common import audit
        audit.record(
            "guardrail.trip" if guardrail.action == "block" else "guardrail.ask",
            object_type="guardrail", object_id=guardrail.id, workspace=workspace,
            details={"name": guardrail.name, "run_id": run_id, "task_id": task_id,
                     "agent_id": agent_id, "tool": tool, "reason": reason},
        )
    except Exception:  # noqa: BLE001 - the audit log is best-effort, the decision stands
        log.warning("sequence guardrails: could not write the audit record", exc_info=True)


# ── the two hooks ToolGuard calls ────────────────────────────────────────────

def _fingerprint(tool_id: str, tool_input: Any) -> str:
    from tools.approval import call_fingerprint
    return call_fingerprint(tool_id, tool_input)


def check_call(guard: Any, tool_id: str, tool_input: Any, *, run_id: str = "",
               task_id: str = "") -> Optional[Dict[str, Any]]:
    """``{"action": "block" | "ask", "reason": str}`` when a rule stops this
    call, None when every rule lets it through.

    Every applicable rule is evaluated; a block wins over an ask, so a call
    two rules object to is refused rather than offered to a person. A call
    that passes is reserved against the ``sum_max`` totals until
    :func:`note_call` sees it finish.
    """
    rules = _rules(guard)
    key = _trail_key(run_id, task_id)
    if not rules or not key:
        return None
    args = _as_dict(tool_input)
    trail = _trail(key)
    with trail.lock:
        reserved = trail.live_reservations()
        hits: List[Tuple[Any, str]] = []
        for guardrail in rules:
            reason = evaluate(guardrail.config, trail.calls, tool_id, args,
                              reserved=reserved, carried=trail.carried)
            if reason:
                hits.append((guardrail, reason))
        if not hits:
            trail.reserved[_fingerprint(tool_id, tool_input)] = (
                tool_id, _values(args, _paths(rules)), time.monotonic())
            return None
    guardrail, reason = next(((g, r) for g, r in hits if g.action == "block"), hits[0])
    _record(guard, guardrail, tool=tool_id, tool_input=tool_input, reason=reason,
            run_id=run_id, task_id=task_id)
    return {
        "action": guardrail.action,
        "reason": f"The '{guardrail.name}' guardrail stopped this call: {reason}.",
        "guardrail_id": guardrail.id,
    }


def note_call(guard: Any, tool_id: str, tool_input: Any, output: Any, *, run_id: str = "",
              task_id: str = "") -> None:
    """Remember a call that ran, for the rules that look back. A run with no
    sequence rule keeps no trail at all."""
    rules = _rules(guard)
    key = _trail_key(run_id, task_id)
    if not rules or not key:
        return
    trail = _trail(key)
    with trail.lock:
        trail.reserved.pop(_fingerprint(tool_id, tool_input), None)
        trail.add({"tool": tool_id, "ok": _succeeded(output),
                   "values": _values(_as_dict(tool_input), _paths(rules))})
        _save(trail)


__all__ = [
    "applicable", "check_call", "evaluate", "has_rules", "note_call", "prune", "reset",
    "simulate",
]
