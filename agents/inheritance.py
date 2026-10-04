"""
Agent inheritance: one agent ``extends`` another (docs/agent-inheritance.md).

A child stores only what it sets itself; everything else comes from its
parent, resolved on read. The rules:

* :data:`LIST_FIELDS` merge: the parent's list, minus the child's
  ``remove`` delta, plus its ``add`` delta, order kept, no duplicates.
* :data:`SCALAR_FIELDS` are inherited unless named in the child's
  ``overrides``.
* :data:`MERGED_DICT_FIELDS` take the parent's keys, the child's keys win.
* Everything else (:data:`NEVER_INHERITED`) is always the child's own: its
  identity, ownership, memory binding, review state, the system flag, the
  proactive profile, secrets of its own HTTP exposure and, on purpose, the
  ``capability_override``: a security exemption is granted per agent, never
  passed down.

Read merges, write diffs. ``agents.registry.get_agent`` and ``list_agents``
return the EFFECTIVE spec (:func:`resolve_all`); ``add_agent`` turns an
effective spec back into the stored one (:func:`to_stored`), so a scalar set
to exactly the parent's value becomes inherited again.

The prompt follows the same idea (:func:`effective_parts`): the parent's
effective instructions are split into a preamble and ``## `` sections; the
child's preamble goes right after the parent's, a child section with the same
heading replaces the parent's in place (``{{parent}}`` inside it expands to
the parent section's body, a body of exactly ``{{remove}}`` deletes it), and
the child's other sections are appended in order. ``capabilities.md`` and
``usage.md`` are the child's own when present (with ``{{parent}}``
expansion), else the parent's.

Limits: no cycles, at most :data:`MAX_CHAIN` agents in a chain (grandparent,
parent, child), a system agent is never a child, a remote (imported) agent
is neither a parent nor a child, and a child does not share another agent's
definition folder (``definition_id``).
"""
from __future__ import annotations

import copy
import dataclasses
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


LIST_FIELDS: Tuple[str, ...] = (
    "tools", "delegates", "handoffs", "guardrails", "secrets", "fallback_models",
    "approval_tools", "approval_exempt", "allowed_domains", "blocked_domains",
)
SCALAR_FIELDS: Tuple[str, ...] = (
    "type", "entrypoint", "provider", "model", "base_url", "temperature", "max_tokens",
    "reasoning", "response_format", "clarify_gate", "allow_self_delegation",
    "skills_enabled", "episodic_write_enabled", "max_concurrent_delegates", "advisor_model",
    "output_schema", "tool_search", "compaction", "handoff_history", "default_outcome",
    "streaming", "verbose", "commands",
)
MERGED_DICT_FIELDS: Tuple[str, ...] = ("tool_policy", "default_params")
INHERITABLE_FIELDS: Tuple[str, ...] = SCALAR_FIELDS + LIST_FIELDS + MERGED_DICT_FIELDS
NEVER_INHERITED: Tuple[str, ...] = (
    "id", "name", "description", "domain", "capacity", "definition_id", "memory_type",
    "memory_data", "owner_workspace", "owner_user", "shared", "review_status", "review_note",
    "reviewed_by", "reviewed_at", "system", "user_modified", "is_default_chat_agent",
    "proactive", "api_key", "github_identity", "http_expose", "http_port", "http_host_port",
    "node_type", "remote", "default_workspace_only", "capability_override",
)

#: Agents in one chain at most: grandparent, parent, child.
MAX_CHAIN = 3
PARENT_TOKEN = "{{parent}}"
REMOVE_TOKEN = "{{remove}}"

# Keys a run snapshot (agents.registry.export_snapshot) adds to a resolved child.
SNAPSHOT_RESOLVED_KEY = "inheritance_resolved"
SNAPSHOT_PARTS_KEY = "effective_parts"
SNAPSHOT_CHAIN_KEY = "inheritance_chain"

_PROMPT_PARTS = ("instructions", "capabilities", "usage")


class InheritanceError(ValueError):
    """An ``extends`` the rules refuse (cycle, depth, system child, unknown
    parent, missing pinned version). A ValueError, so the routes' existing
    handlers answer 400."""


class AgentHasChildren(ValueError):
    """Deleting an agent other agents extend."""

    def __init__(self, agent_id: str, children: Sequence[str]):
        self.agent_id = agent_id
        self.children = list(children)
        super().__init__(
            f"Agent '{agent_id}' cannot be deleted: {', '.join(self.children)} "
            f"inherit from it. Detach or delete them first."
        )


# -------------------- Normalizers --------------------

def _clean(items: Any) -> List[str]:
    out: List[str] = []
    if isinstance(items, (list, tuple)):
        for item in items:
            value = str(item).strip()
            if value and value not in out:
                out.append(value)
    return out


def normalize_overrides(raw: Any) -> List[str]:
    """Known SCALAR/dict field names, in their canonical order."""
    names = set(_clean(raw))
    return [f for f in SCALAR_FIELDS + MERGED_DICT_FIELDS if f in names]


def normalize_list_deltas(raw: Any) -> Dict[str, Dict[str, List[str]]]:
    """``{field: {"add": [...], "remove": [...]}}`` for LIST_FIELDS, empty ones dropped."""
    out: Dict[str, Dict[str, List[str]]] = {}
    if not isinstance(raw, dict):
        return out
    for name in LIST_FIELDS:
        delta = raw.get(name)
        if not isinstance(delta, dict):
            continue
        add, remove = _clean(delta.get("add")), _clean(delta.get("remove"))
        if add or remove:
            out[name] = {"add": add, "remove": remove}
    return out


def merge_list(parent: Sequence[Any], delta: Optional[Dict[str, Any]]) -> List[str]:
    """The parent's list, then remove, then add; order kept, no duplicates."""
    delta = delta or {}
    removed = set(_clean(delta.get("remove")))
    out = [x for x in _clean(list(parent or [])) if x not in removed]
    for item in _clean(delta.get("add")):
        if item not in out:
            out.append(item)
    return out


def diff_list(parent: Sequence[Any], child: Sequence[Any]) -> Dict[str, List[str]]:
    """The delta that turns ``parent`` into ``child`` under :func:`merge_list`."""
    p, c = _clean(list(parent or [])), _clean(list(child or []))
    return {"add": [x for x in c if x not in p], "remove": [x for x in p if x not in c]}


def _field_default(name: str) -> Any:
    from agents.registry import AgentSpec
    for f in dataclasses.fields(AgentSpec):
        if f.name == name:
            if f.default is not dataclasses.MISSING:
                return copy.deepcopy(f.default)
            if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                return f.default_factory()  # type: ignore[misc]
    return None


def _resolve_id(agent_id: Any) -> str:
    from agents.registry import resolve_agent_id
    return resolve_agent_id(agent_id)


# -------------------- Spec merge and diff --------------------

def apply_parent(parent_eff: Any, raw: Any) -> Any:
    """The effective spec of ``raw`` (a stored child) under ``parent_eff``."""
    changes: Dict[str, Any] = {}
    overridden = set(raw.overrides or [])
    for name in SCALAR_FIELDS:
        if name not in overridden:
            changes[name] = copy.deepcopy(getattr(parent_eff, name))
    deltas = raw.list_deltas or {}
    for name in LIST_FIELDS:
        changes[name] = merge_list(getattr(parent_eff, name), deltas.get(name))
    for name in MERGED_DICT_FIELDS:
        merged = copy.deepcopy(dict(getattr(parent_eff, name) or {}))
        merged.update(copy.deepcopy(dict(getattr(raw, name) or {})))
        changes[name] = merged
    # An agent never hands the conversation to itself (registry validator).
    changes["handoffs"] = [h for h in changes["handoffs"] if h != raw.id]
    return dataclasses.replace(raw, **changes)


def _orphan(raw: Any) -> Any:
    """A child whose parent cannot be resolved: its own adds, its own scalars."""
    deltas = raw.list_deltas or {}
    return dataclasses.replace(raw, **{n: _clean((deltas.get(n) or {}).get("add")) for n in LIST_FIELDS})


def to_stored(spec: Any, parent_eff: Any) -> Any:
    """The stored record for an EFFECTIVE child spec: scalars equal to the
    parent's are inherited (reset to their default), different ones become
    overrides, lists become deltas, dicts keep only the keys that differ."""
    changes: Dict[str, Any] = {}
    overrides: List[str] = []
    for name in SCALAR_FIELDS:
        if getattr(spec, name) != getattr(parent_eff, name):
            overrides.append(name)
        elif name not in ("type", "entrypoint"):
            changes[name] = _field_default(name)
    deltas: Dict[str, Dict[str, List[str]]] = {}
    for name in LIST_FIELDS:
        child_list = getattr(spec, name)
        parent_list = getattr(parent_eff, name)
        if name == "handoffs":
            parent_list = [h for h in (parent_list or []) if h != spec.id]
        delta = diff_list(parent_list, child_list)
        if delta["add"] or delta["remove"]:
            deltas[name] = delta
        changes[name] = []
    for name in MERGED_DICT_FIELDS:
        parent_dict = dict(getattr(parent_eff, name) or {})
        changes[name] = {k: v for k, v in dict(getattr(spec, name) or {}).items()
                         if k not in parent_dict or parent_dict[k] != v}
    return dataclasses.replace(spec, overrides=overrides, list_deltas=deltas, **changes)


def new_child_spec(parent_eff: Any, *, extends_version: Optional[int] = None, **own: Any) -> Any:
    """A fresh EFFECTIVE spec for a new child of ``parent_eff``: every
    inheritable field taken from the parent, nothing else (``own`` supplies
    id, name and any never inherited field)."""
    from agents.registry import AgentSpec
    inherited = {n: copy.deepcopy(getattr(parent_eff, n)) for n in INHERITABLE_FIELDS}
    inherited.update(own)
    return AgentSpec(extends=parent_eff.id, extends_version=extends_version, **inherited)


# -------------------- Resolution --------------------

def pinned_spec(parent_id: str, version: int) -> Optional[Any]:
    """The parent's effective spec as stored in its version ``version``
    (a version row's spec is the effective record at that time)."""
    from agents import versions as agent_versions
    from agents.registry import _validate_agent_dict
    try:
        entry = agent_versions.get_version_row(parent_id, int(version))
    except Exception:  # noqa: BLE001 - an unreadable history resolves like a missing version
        log.warning("could not read version %s of '%s'", version, parent_id, exc_info=True)
        return None
    if entry is None or not entry.get("spec"):
        return None
    return _validate_agent_dict(entry["spec"])


def resolve_all(raws: Sequence[Any]) -> Dict[str, Any]:
    """Every stored spec in ``raws`` resolved to its effective spec, by id.
    A broken link (cycle, too deep, parent gone) is logged and the child
    keeps only its own fields, so one bad record never takes the registry
    down."""
    by_id = {s.id: s for s in raws}
    memo: Dict[str, Any] = {}
    pins: Dict[Tuple[str, int], Any] = {}

    def effective(aid: str, stack: Tuple[str, ...]) -> Any:
        if aid in memo:
            return memo[aid]
        raw = by_id[aid]
        if not raw.extends:
            memo[aid] = raw
            return raw
        if aid in stack or len(stack) >= MAX_CHAIN:
            log.warning("agent '%s': inheritance chain is cyclic or too deep, ignoring its parent", aid)
            memo[aid] = _orphan(raw)
            return memo[aid]
        pid = _resolve_id(raw.extends)
        parent: Any = None
        if raw.extends_version is not None:
            key = (pid, int(raw.extends_version))
            if key not in pins:
                pins[key] = pinned_spec(pid, raw.extends_version)
            parent = pins[key]
            if parent is None:
                log.warning("agent '%s': pinned parent %s@%s not found, following its current state",
                            aid, pid, raw.extends_version)
        if parent is None and pid in by_id:
            parent = effective(pid, stack + (aid,))
        if parent is None:
            log.warning("agent '%s': parent '%s' not found, ignoring it", aid, pid)
            memo[aid] = _orphan(raw)
        else:
            memo[aid] = apply_parent(parent, raw)
        return memo[aid]

    return {aid: effective(aid, ()) for aid in by_id}


def parent_effective(parent_id: str, version: Optional[int] = None) -> Optional[Any]:
    """The parent's effective spec: a pinned version, or its current state."""
    from agents.registry import get_agent
    pid = _resolve_id(parent_id)
    if version is not None:
        pinned = pinned_spec(pid, version)
        if pinned is not None:
            return pinned
    return get_agent(pid)


def _raw_map() -> Dict[str, Any]:
    from agents.registry import list_agents_raw
    return {s.id: s for s in list_agents_raw()}


def _depth_below(agent_id: str, raws: Dict[str, Any], seen: Optional[set] = None) -> int:
    seen = set(seen or ()) | {agent_id}
    depth = 0
    for spec in raws.values():
        if spec.extends and _resolve_id(spec.extends) == agent_id and spec.id not in seen:
            depth = max(depth, 1 + _depth_below(spec.id, raws, seen))
    return depth


def validate_extends(spec: Any) -> None:
    """Refuse an ``extends`` the rules do not allow (see the module docstring)."""
    if not spec.extends:
        return
    parent_id = _resolve_id(spec.extends)
    if spec.system:
        raise InheritanceError("A system agent cannot extend another agent.")
    if spec.is_remote():
        raise InheritanceError("An imported (remote) agent cannot extend another agent.")
    if spec.definition_id and spec.definition_id != spec.id:
        raise InheritanceError(
            "An agent that shares another agent's definition (definition_id) cannot extend an agent.")
    if parent_id == spec.id:
        raise InheritanceError("An agent cannot extend itself.")
    raws = _raw_map()
    parent = raws.get(parent_id)
    if parent is None:
        raise InheritanceError(f"Parent agent '{parent_id}' not found.")
    if parent.is_remote():
        raise InheritanceError(f"'{parent_id}' is an imported (remote) agent and cannot be a parent.")
    up = [parent_id]
    current = parent
    while current is not None and current.extends:
        next_id = _resolve_id(current.extends)
        if next_id == spec.id or next_id in up:
            raise InheritanceError(
                f"'{spec.id}' cannot extend '{parent_id}': the chain would loop back to itself.")
        up.append(next_id)
        current = raws.get(next_id)
    if len(up) + 1 + _depth_below(spec.id, raws) > MAX_CHAIN:
        raise InheritanceError(
            f"An inheritance chain holds at most {MAX_CHAIN} agents (grandparent, parent, child).")
    if spec.extends_version is not None:
        try:
            version = int(spec.extends_version)
        except (TypeError, ValueError):
            raise InheritanceError("extends_version must be a version number.") from None
        if pinned_spec(parent_id, version) is None:
            raise InheritanceError(f"Agent '{parent_id}' has no version {version}.")


def unpinned_descendants(agent_id: str, raws: Sequence[Any]) -> List[str]:
    """Ids of every agent that follows ``agent_id``'s current state, directly
    or through an unpinned parent, nearest first."""
    out: List[str] = []
    frontier = [agent_id]
    while frontier:
        nxt: List[str] = []
        for parent in frontier:
            for spec in raws:
                if (spec.extends and spec.extends_version is None
                        and _resolve_id(spec.extends) == parent
                        and spec.id not in out and spec.id != agent_id):
                    out.append(spec.id)
                    nxt.append(spec.id)
        frontier = nxt
    return out


def enforce_descendants(parent_id: str, descendants: Sequence[str],
                        resolved: Dict[str, Any], raws: Dict[str, Any]) -> None:
    """The capability guard on every descendant's NEW effective tool set.
    Raises :class:`agents.capability_guard.InheritedCapabilityViolation`
    naming the first child that would form a blocked combination."""
    from agents.capability_guard import (
        CapabilityViolation, InheritedCapabilityViolation, enforce_agent_tools,
    )
    from agents.registry import get_agent
    from tools.capabilities import secret_grant_ids

    for child_id in descendants:
        new = resolved.get(child_id)
        raw = raws.get(child_id)
        if new is None or raw is None:
            continue
        old = get_agent(child_id)
        try:
            enforce_agent_tools(
                child_id,
                list(new.tools or []) + secret_grant_ids(new.secrets),
                previous_tools=(list(old.tools or []) + secret_grant_ids(old.secrets)) if old else None,
                override=bool(raw.capability_override),
                workspace=raw.owner_workspace,
                delegates=list(new.delegates or []),
            )
        except InheritedCapabilityViolation:
            raise
        except CapabilityViolation as exc:
            raise InheritedCapabilityViolation(child_id, exc.violation, parent_id=parent_id) from None


def ancestor_ids(agent_id: str) -> List[str]:
    """Parent, grandparent ... of ``agent_id`` (pinned or not), nearest first."""
    raws = _raw_map()
    out: List[str] = []
    current = raws.get(_resolve_id(agent_id))
    while current is not None and current.extends and len(out) < MAX_CHAIN:
        pid = _resolve_id(current.extends)
        if pid in out or pid == agent_id:
            break
        out.append(pid)
        current = raws.get(pid)
    return out


def skill_owner_ids(agent_id: str) -> List[str]:
    """Whose skills an agent sees at run time: its own first, then each
    ancestor's (memory/procedural.py). Never raises."""
    try:
        return [agent_id] + ancestor_ids(agent_id)
    except Exception:  # noqa: BLE001 - an unreadable registry means own skills only
        return [agent_id]


def prompt_folder_ids(agent_id: str) -> List[str]:
    """Definition folders whose text reaches ``agent_id``'s prompt besides
    its own: every ancestor up to the first pinned link (a pinned parent's
    text comes from its stored version, which never changes). For the build
    cache fingerprint (agents/agent_cache.py)."""
    try:
        raws = _raw_map()
    except Exception:  # noqa: BLE001 - the cache then keys on the agent's own folder only
        return []
    out: List[str] = []
    current = raws.get(agent_id)
    while current is not None and current.extends and current.extends_version is None:
        pid = _resolve_id(current.extends)
        if pid in out or pid == agent_id or len(out) >= MAX_CHAIN:
            break
        parent = raws.get(pid)
        if parent is None:
            break
        out.append(parent.def_id())
        current = parent
    return out


def _snapshot_record(agent_id: str) -> Optional[Dict[str, Any]]:
    from common import snapshot
    if not snapshot.in_snapshot_mode():
        return None
    from agents.registry import load_all_raw
    for rec in load_all_raw():
        if str(rec.get("id") or "") == agent_id:
            return rec
    return None


def chain_pins(spec_or_id: Any) -> List[Dict[str, Any]]:
    """The chain as links, root first: ``[{id, extends_version}]`` ending with
    the agent itself. Part of a child's fingerprint (agents/versions.py)."""
    from agents.registry import get_agent_raw
    agent_id = spec_or_id if isinstance(spec_or_id, str) else spec_or_id.id
    rec = _snapshot_record(agent_id)
    if rec is not None and isinstance(rec.get(SNAPSHOT_CHAIN_KEY), list):
        return list(rec[SNAPSHOT_CHAIN_KEY])
    spec = get_agent_raw(spec_or_id) if isinstance(spec_or_id, str) else spec_or_id
    out: List[Dict[str, Any]] = []
    seen: set = set()
    while spec is not None and spec.id not in seen and len(out) <= MAX_CHAIN:
        seen.add(spec.id)
        out.insert(0, {"id": spec.id, "extends_version": spec.extends_version if spec.extends else None})
        if not spec.extends:
            break
        spec = get_agent_raw(_resolve_id(spec.extends))
    return out


def run_chain(agent_id: str, version: Optional[int] = None) -> List[Dict[str, Any]]:
    """The resolved chain a run or a version records, root first:
    ``[{id, version}]`` ending with ``agent_id`` at ``version``. A pinned
    link names its pinned version (and the chain that version recorded); an
    unpinned one the stored version holding the parent's live definition."""
    from agents import versions as agent_versions
    from agents.registry import get_agent_raw
    out: List[Dict[str, Any]] = [{"id": agent_id, "version": version}]
    raw = get_agent_raw(agent_id)
    seen = {agent_id}
    while raw is not None and raw.extends and len(out) <= MAX_CHAIN:
        pid = _resolve_id(raw.extends)
        if pid in seen:
            break
        seen.add(pid)
        if raw.extends_version is not None:
            row = agent_versions.get_version_row(pid, int(raw.extends_version))
            stored = ((row or {}).get("definition") or {}).get("chain")
            if isinstance(stored, list) and stored:
                return [dict(x) for x in stored] + out
            return [{"id": pid, "version": int(raw.extends_version)}] + out
        out.insert(0, {"id": pid, "version": agent_versions.ensure_current_version(pid)})
        raw = get_agent_raw(pid)
    return out


# -------------------- Prompt --------------------

_HEADING = re.compile(r"^ {0,3}##[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


def heading_key(heading: str) -> str:
    return " ".join(str(heading or "").split()).lower()


def split_sections(text: str) -> Tuple[str, List[Dict[str, str]]]:
    """``(preamble, [{heading, body}])`` of a markdown text split on ``## ``
    headings (fenced code blocks are left alone)."""
    preamble: List[str] = []
    sections: List[Dict[str, Any]] = []
    in_fence = False
    for line in str(text or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            sections.append({"heading": match.group(1).strip(), "lines": []})
        elif sections:
            sections[-1]["lines"].append(line)
        else:
            preamble.append(line)
    return ("\n".join(preamble).strip(),
            [{"heading": s["heading"], "body": "\n".join(s["lines"]).strip()} for s in sections])


def join_sections(preamble: str, sections: Sequence[Dict[str, Any]]) -> str:
    blocks: List[str] = [preamble.strip()] if preamble and preamble.strip() else []
    for section in sections:
        body = str(section.get("body") or "").strip()
        blocks.append(f"## {section['heading']}" + (f"\n\n{body}" if body else ""))
    return "\n\n".join(blocks)


def merge_instructions(parent_preamble: str, parent_sections: Sequence[Dict[str, Any]],
                       child_text: str, child_id: str) -> Dict[str, Any]:
    """Merge a child's own instructions into its parent's effective ones.
    Returns ``{preamble, sections: [{heading, body, source}], own_sections:
    [{heading, mode}]}``."""
    own_preamble, own_sections = split_sections(child_text)
    sections = [dict(s) for s in parent_sections]
    preamble = parent_preamble
    if own_preamble:
        if PARENT_TOKEN in own_preamble:
            preamble = own_preamble.replace(PARENT_TOKEN, parent_preamble).strip()
        else:
            preamble = (parent_preamble + "\n\n" + own_preamble).strip()
    modes: List[Dict[str, str]] = []
    for own in own_sections:
        key = heading_key(own["heading"])
        index = next((i for i, s in enumerate(sections) if heading_key(s["heading"]) == key), None)
        body = own["body"]
        if body.strip() == REMOVE_TOKEN:
            if index is not None:
                sections.pop(index)
            modes.append({"heading": own["heading"], "mode": "remove"})
            continue
        if index is None:
            sections.append({"heading": own["heading"], "body": body.replace(PARENT_TOKEN, "").strip(),
                             "source": child_id})
            modes.append({"heading": own["heading"], "mode": "new"})
            continue
        mode = "extend" if PARENT_TOKEN in body else "replace"
        sections[index] = {"heading": own["heading"],
                           "body": body.replace(PARENT_TOKEN, sections[index]["body"]).strip(),
                           "source": child_id}
        modes.append({"heading": own["heading"], "mode": mode})
    return {"preamble": preamble, "sections": sections, "own_sections": modes}


def _own_texts(spec: Any, definitions_dir: Optional[Path]) -> Dict[str, str]:
    from agents import prompt_assembly as pa
    def_id = spec.def_id()
    return {
        "instructions": pa.read_instructions(def_id, definitions_dir),
        "capabilities": pa.read_capabilities(def_id, definitions_dir),
        "usage": pa.read_usage(def_id, definitions_dir),
    }


def _expand(own: str, parent: str) -> str:
    if not own:
        return parent
    return own.replace(PARENT_TOKEN, parent).strip() if PARENT_TOKEN in own else own


def _pinned_tree(parent_id: str, version: int) -> Optional[Dict[str, Any]]:
    from agents import versions as agent_versions
    entry = agent_versions.get_version_row(parent_id, int(version))
    if entry is None:
        return None
    defn = entry.get("definition") or {}
    texts = defn.get("effective") if isinstance(defn.get("effective"), dict) else defn
    preamble, sections = split_sections(str(texts.get("instructions") or ""))
    return {
        "id": parent_id,
        "preamble": preamble,
        "sections": [{**s, "source": parent_id} for s in sections],
        "capabilities": str(texts.get("capabilities") or "").strip(),
        "usage": str(texts.get("usage") or "").strip(),
        "own_sections": [],
        "parent": None,
    }


def _tree(spec: Any, definitions_dir: Optional[Path], own: Optional[Dict[str, Any]],
          depth: int = 0) -> Dict[str, Any]:
    """The prompt of ``spec`` with provenance: preamble, sections with their
    source agent, capabilities, usage, the child's own section modes and the
    parent's tree."""
    texts = _own_texts(spec, definitions_dir)
    for part in _PROMPT_PARTS:
        if own and own.get(part) is not None:
            texts[part] = str(own[part] or "").strip()
    parent_tree: Optional[Dict[str, Any]] = None
    if spec.extends and depth < MAX_CHAIN:
        pid = _resolve_id(spec.extends)
        if spec.extends_version is not None:
            parent_tree = _pinned_tree(pid, spec.extends_version)
        if parent_tree is None:
            from agents.registry import get_agent
            parent = get_agent(pid)
            if parent is not None and parent.id != spec.id:
                parent_tree = _tree(parent, definitions_dir, None, depth + 1)
    if parent_tree is None:
        preamble, sections = split_sections(texts["instructions"])
        return {"id": spec.id, "preamble": preamble,
                "sections": [{**s, "source": spec.id} for s in sections],
                "capabilities": texts["capabilities"], "usage": texts["usage"],
                "own_sections": [{"heading": s["heading"], "mode": "new"} for s in sections],
                "parent": None}
    merged = merge_instructions(parent_tree["preamble"], parent_tree["sections"],
                                texts["instructions"], spec.id)
    return {"id": spec.id, "preamble": merged["preamble"], "sections": merged["sections"],
            "capabilities": _expand(texts["capabilities"], parent_tree["capabilities"]),
            "usage": _expand(texts["usage"], parent_tree["usage"]),
            "own_sections": merged["own_sections"], "parent": parent_tree}


def effective_parts(spec: Any, *, definitions_dir: Optional[Path] = None,
                    own: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """``{instructions, capabilities, usage}`` as the agent runs them. ``own``
    replaces the agent's own files (a pending definition edit)."""
    if own is None:
        rec = _snapshot_record(spec.id)
        if rec is not None and isinstance(rec.get(SNAPSHOT_PARTS_KEY), dict):
            parts = rec[SNAPSHOT_PARTS_KEY]
            return {p: str(parts.get(p) or "") for p in _PROMPT_PARTS}
    tree = _tree(spec, definitions_dir, own)
    return {"instructions": join_sections(tree["preamble"], tree["sections"]),
            "capabilities": tree["capabilities"], "usage": tree["usage"]}


def assemble(parts: Dict[str, str]) -> str:
    """The system prompt from its three parts, as prompt_assembly.assemble_prompt
    builds it from the files."""
    blocks = [str(parts.get("instructions") or "").strip()]
    if (parts.get("capabilities") or "").strip():
        blocks.append("## Capabilities\n\n" + parts["capabilities"].strip())
    if (parts.get("usage") or "").strip():
        blocks.append("## Usage\n\n" + parts["usage"].strip())
    return "\n\n".join(blocks)


def effective_prompt(spec: Any, *, definitions_dir: Optional[Path] = None) -> str:
    """The system prompt an agent runs with. A plain agent's is exactly
    ``prompt_assembly.assemble_prompt`` (which raises when instructions.md is
    missing); a child's is merged with its chain and may have no file of its own."""
    if not getattr(spec, "extends", None):
        from agents.prompt_assembly import assemble_prompt
        return assemble_prompt(spec.def_id(), definitions_dir=definitions_dir)
    return assemble(effective_parts(spec, definitions_dir=definitions_dir))


def inherited_instructions(spec: Any, *, definitions_dir: Optional[Path] = None) -> str:
    """The parent's effective instructions, as the child's own text is merged into."""
    if not getattr(spec, "extends", None):
        return ""
    tree = _tree(spec, definitions_dir, None)
    parent = tree.get("parent")
    return join_sections(parent["preamble"], parent["sections"]) if parent else ""


# -------------------- The inheritance view (GET /api/agents/{id}/inheritance) --------------------

def _provenance(agent_id: str, depth: int = 0) -> Tuple[Dict[str, str], Dict[str, List[Tuple[str, str]]]]:
    """``(scalar and dict field -> source agent id, list field -> [(value, source)])``."""
    from agents.registry import get_agent, get_agent_raw
    raw = get_agent_raw(agent_id)
    eff = get_agent(agent_id)
    if raw is None or eff is None:
        return {}, {}
    if not raw.extends or depth >= MAX_CHAIN:
        return ({n: raw.id for n in SCALAR_FIELDS + MERGED_DICT_FIELDS},
                {n: [(v, raw.id) for v in getattr(eff, n) or []] for n in LIST_FIELDS})
    pid = _resolve_id(raw.extends)
    if raw.extends_version is not None:
        pinned = pinned_spec(pid, raw.extends_version)
        p_fields = {n: pid for n in SCALAR_FIELDS + MERGED_DICT_FIELDS}
        p_lists = {n: [(v, pid) for v in (getattr(pinned, n) or [] if pinned else [])] for n in LIST_FIELDS}
    else:
        p_fields, p_lists = _provenance(pid, depth + 1)
    fields: Dict[str, str] = {}
    for name in SCALAR_FIELDS:
        fields[name] = raw.id if name in (raw.overrides or []) else p_fields.get(name, pid)
    for name in MERGED_DICT_FIELDS:
        fields[name] = raw.id if getattr(raw, name) else p_fields.get(name, pid)
    lists: Dict[str, List[Tuple[str, str]]] = {}
    for name in LIST_FIELDS:
        source = dict(p_lists.get(name) or [])
        lists[name] = [(v, source.get(v, raw.id)) for v in getattr(eff, name) or []]
    return fields, lists


def describe(agent_id: str, *, definitions_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Everything the agent page shows about inheritance, or None for an
    unknown agent. Shape: docs/agent-inheritance.md (API)."""
    from agents import versions as agent_versions
    from agents.registry import children_of, get_agent, get_agent_raw

    raw = get_agent_raw(agent_id)
    eff = get_agent(agent_id)
    if raw is None or eff is None:
        return None

    def _name(aid: str) -> str:
        spec = get_agent_raw(aid)
        return spec.name if spec is not None else aid

    def _latest(aid: str) -> Optional[int]:
        try:
            row = agent_versions._latest_version_row(aid)
        except Exception:  # noqa: BLE001 - the chain shows without version numbers then
            return None
        return int(row["version"]) if row is not None else None

    # Root first, ending with the agent itself. A link is pinned when the
    # agent below it pins it; otherwise its version is its latest stored one.
    links = chain_pins(raw.id)
    chain = []
    for i, link in enumerate(links):
        below = links[i + 1] if i + 1 < len(links) else None
        pinned = below is not None and below.get("extends_version") is not None
        chain.append({
            "id": link["id"],
            "name": _name(link["id"]),
            "version": int(below["extends_version"]) if pinned else _latest(link["id"]),
            "pinned": pinned,
        })

    fields_src, lists_src = _provenance(raw.id)
    overridden = set(raw.overrides or []) if raw.extends else set()
    fields = {}
    for name in SCALAR_FIELDS + MERGED_DICT_FIELDS:
        is_dict = name in MERGED_DICT_FIELDS
        fields[name] = {
            "value": copy.deepcopy(getattr(eff, name)),
            "source": fields_src.get(name, raw.id),
            "overridden": bool(raw.extends) and (bool(getattr(raw, name)) if is_dict else name in overridden),
        }
        if is_dict and raw.extends:
            fields[name]["own_keys"] = sorted((getattr(raw, name) or {}).keys())

    deltas = raw.list_deltas or {}
    effective_lists: Dict[str, Any] = {}
    for name in LIST_FIELDS:
        added = set((deltas.get(name) or {}).get("add") or []) if raw.extends else set()
        effective_lists[name] = [{"value": v, "source": s, "added": v in added}
                                 for v, s in lists_src.get(name, [])]
    effective_lists["removed"] = {n: list((deltas.get(n) or {}).get("remove") or [])
                                  for n in LIST_FIELDS if (deltas.get(n) or {}).get("remove")}

    tree = _tree(eff, definitions_dir, None)
    parent_tree = tree.get("parent")
    prompt = {
        "parent_sections": ([{"heading": s["heading"], "source": s.get("source")}
                             for s in parent_tree["sections"]] if parent_tree else []),
        "own_sections": tree["own_sections"],
        "inherited_instructions": (join_sections(parent_tree["preamble"], parent_tree["sections"])
                                   if parent_tree else ""),
        "effective": assemble({"instructions": join_sections(tree["preamble"], tree["sections"]),
                               "capabilities": tree["capabilities"], "usage": tree["usage"]}),
    }

    return {
        "agent_id": raw.id,
        "extends": raw.extends,
        "extends_version": raw.extends_version if raw.extends else None,
        "chain": chain,
        "children": [{"id": c, "name": _name(c)} for c in children_of(raw.id)],
        "overrides": list(raw.overrides or []) if raw.extends else [],
        "fields": fields,
        "list_deltas": copy.deepcopy(deltas) if raw.extends else {},
        "effective_lists": effective_lists,
        "prompt": prompt,
    }


def reset_override(raw: Any, field_name: str) -> Any:
    """``raw`` with one field back to inherited: a scalar leaves ``overrides``,
    a list field loses its deltas, a dict field its own keys."""
    if field_name in SCALAR_FIELDS:
        changes: Dict[str, Any] = {"overrides": [f for f in raw.overrides or [] if f != field_name]}
        if field_name not in ("type", "entrypoint"):
            changes[field_name] = _field_default(field_name)
        return dataclasses.replace(raw, **changes)
    if field_name in LIST_FIELDS:
        deltas = {k: v for k, v in (raw.list_deltas or {}).items() if k != field_name}
        return dataclasses.replace(raw, list_deltas=deltas)
    if field_name in MERGED_DICT_FIELDS:
        return dataclasses.replace(raw, **{field_name: {}})
    raise InheritanceError(f"'{field_name}' is not an inheritable field.")


__all__: List[str] = [
    "LIST_FIELDS", "SCALAR_FIELDS", "MERGED_DICT_FIELDS", "INHERITABLE_FIELDS", "NEVER_INHERITED",
    "MAX_CHAIN", "InheritanceError", "AgentHasChildren", "merge_list", "diff_list", "apply_parent",
    "to_stored", "new_child_spec", "resolve_all", "parent_effective", "validate_extends",
    "unpinned_descendants", "enforce_descendants", "ancestor_ids", "skill_owner_ids",
    "prompt_folder_ids", "chain_pins", "run_chain", "split_sections", "merge_instructions",
    "effective_parts", "effective_prompt", "inherited_instructions", "describe", "reset_override",
]
