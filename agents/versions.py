"""
Definition fingerprint and version history for a registered agent.

An agent's *definition* is everything that determines what it actually does
at runtime: its registry record (tools, model/provider, reasoning config,
memory binding, capability override, approval lists, delegates) plus the text
of its three markdown files (``instructions.md`` / ``capabilities.md`` /
``usage.md``). This module gives that definition a stable, content-addressed
hash and keeps a history of it in the ``agent_versions`` table.

This is a different fingerprint than ``agents.agent_cache.compute_fingerprint``:
the build cache hashes cheap *change signatures* (mtime + size) to decide
whether a built agent object can be reused within one process on one machine.
This module hashes the *content itself* (canonical JSON, sha256), so the same
definition produces the same hash across processes, machines and restarts —
the property needed to store it on a run record or compare it across a
version history. The set of inputs is the same in spirit (registry fields +
the three markdown files); this module simply hashes their content instead of
their filesystem signature.

Public API:
- ``definition_fingerprint(agent_id)`` — ``{"hash": ..., "parts": {...}}`` for
  the agent's current, live definition.
- ``snapshot_if_changed(agent_id, ...)`` — write the agent's *current* stored
  state into history, when it differs from the last snapshot on file. Called
  by ``agents.registry.add_agent`` right before it overwrites the record, and
  by the dashboard's definition-editor route right before it rewrites the
  markdown files, so history never has a gap between what a snapshot and the
  next write.
- ``list_versions`` / ``get_version_row`` / ``current_snapshot`` /
  ``diff_entries`` / ``rollback_to`` — read side and rollback for the
  ``/api/agents/{id}/versions*`` routes.
- ``ensure_current_version(agent_id)`` — the stored version holding the live
  definition, snapshotting it when needed, so an A/B experiment arm can name
  "what runs now" (``evals/experiments.py``). Rows are never rewritten, so a
  later snapshot or a rollback never changes what an arm builds.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import db
from agents import prompt_assembly

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------------------- Fingerprint --------------------

def _spec_parts(spec: Any) -> Dict[str, Any]:
    """The registry fields that determine agent *behaviour*, in a stable shape.

    Deliberately excludes pure bookkeeping (name, description, domain,
    owner_workspace, shared, capacity, node_type, http_*, commands,
    is_default_chat_agent, system/user_modified) that does not change what the
    agent does when it runs.
    """
    return {
        "tools": sorted(spec.tools or []),
        "provider": spec.provider,
        "model": spec.model,
        "base_url": spec.base_url,
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
        "reasoning": spec.reasoning or {},
        "memory_type": spec.memory_type,
        "memory_data": spec.memory_data,
        "capability_override": bool(spec.capability_override),
        "approval_tools": sorted(spec.approval_tools or []),
        "approval_exempt": sorted(spec.approval_exempt or []),
        "secrets": sorted(getattr(spec, "secrets", None) or []),
        "delegates": sorted(spec.delegates or []),
        "skills_enabled": bool(spec.skills_enabled),
        "episodic_write_enabled": spec.episodic_write_enabled,
        "response_format": spec.response_format,
        "clarify_gate": bool(spec.clarify_gate),
        "allow_self_delegation": bool(spec.allow_self_delegation),
    }


def _spec_parts_from_dict(spec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Same shape as ``_spec_parts``, from a stored ``to_dict()`` snapshot.

    ``to_dict()`` omits fields left at their default to keep agents.json
    clean, so a plain dict lookup would not agree with a live ``AgentSpec``.
    Routing it back through the registry's own validator restores the
    defaults exactly the way loading agents.json does, so a stored snapshot
    and a live spec are always comparable on equal terms.
    """
    from agents.registry import _validate_agent_dict
    return _spec_parts(_validate_agent_dict(spec_dict))


def _hash_parts(parts: Dict[str, Any]) -> str:
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint_spec(spec: Any) -> Dict[str, Any]:
    def_id = spec.def_id()
    parts = _spec_parts(spec)
    parts["instructions"] = prompt_assembly.read_instructions(def_id)
    parts["capabilities"] = prompt_assembly.read_capabilities(def_id)
    parts["usage"] = prompt_assembly.read_usage(def_id)
    return {"hash": _hash_parts(parts), "parts": parts}


def definition_fingerprint(agent_id: str) -> Dict[str, Any]:
    """Content hash of an agent's current, live definition.

    Returns ``{"hash": <sha256 hex>, "parts": {...}}``. Stable across
    processes and independent of dict/JSON key ordering (the hash is taken
    over a ``sort_keys=True`` serialization). Raises ``ValueError`` when the
    agent is not registered — callers that must never fail (run recording)
    catch this themselves.
    """
    from agents.registry import get_agent

    spec = get_agent(agent_id)
    if spec is None:
        raise ValueError(f"Agent '{agent_id}' not found in registry")
    return _fingerprint_spec(spec)


# -------------------- History (agent_versions table) --------------------

def _latest_version_row(agent_id: str):
    conn = db.get_conn()
    return conn.execute(
        "SELECT * FROM agent_versions WHERE agent_id = ? ORDER BY version DESC LIMIT 1",
        (str(agent_id),),
    ).fetchone()


def snapshot_if_changed(
    agent_id: str,
    *,
    next_spec: Any = None,
    next_definition: Optional[Dict[str, str]] = None,
    actor: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[int]:
    """Snapshot the agent's *current* stored state into history, unless the
    pending write is a no-op.

    ``next_spec`` (an ``AgentSpec`` not yet persisted) and/or
    ``next_definition`` (a ``{instructions, capabilities, usage}`` dict of
    markdown about to be written) describe what the caller is about to
    replace the current state with. When given, the snapshot is skipped
    unless that pending state actually differs from what is on disk now —
    otherwise a no-op save (or an unrelated field changing through a
    different route) would pad history with a row identical to the one
    before it. ``agents.registry.add_agent`` passes ``next_spec``; the
    dashboard definition-editor route passes ``next_definition``.

    Regardless of what is passed, a snapshot is also always skipped when the
    current on-disk state already *is* the latest row in history — otherwise
    two calls that both capture the same not-yet-superseded state (one
    explicit, one from a following ``add_agent`` hook) would double up.

    Returns the new version number, or ``None`` when nothing was written
    (no-op, already captured, or the agent does not exist). Best-effort: a
    snapshot failure must never block a legitimate registry write, so this
    never raises.
    """
    from agents.registry import get_agent

    try:
        spec = get_agent(agent_id)
        if spec is None:
            return None
        fp = _fingerprint_spec(spec)
        latest = _latest_version_row(agent_id)

        if latest is not None and latest["hash"] == fp["hash"]:
            return None  # current state is already the newest thing in history

        if next_spec is not None or next_definition is not None:
            probe = next_spec if next_spec is not None else spec
            next_parts = _spec_parts(probe)
            defn = next_definition or {}
            next_parts["instructions"] = defn.get("instructions", fp["parts"]["instructions"])
            next_parts["capabilities"] = defn.get("capabilities", fp["parts"]["capabilities"])
            next_parts["usage"] = defn.get("usage", fp["parts"]["usage"])
            if _hash_parts(next_parts) == fp["hash"]:
                return None  # the pending write changes nothing

        version = int(latest["version"]) + 1 if latest is not None else 1
        spec_json = json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)
        definition_json = json.dumps(
            {
                "instructions": fp["parts"].get("instructions", ""),
                "capabilities": fp["parts"].get("capabilities", ""),
                "usage": fp["parts"].get("usage", ""),
            },
            ensure_ascii=False,
        )
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_versions "
                "(agent_id, version, hash, created_at, actor, spec_json, definition_json, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(agent_id), version, fp["hash"], _utc_now_iso(), actor,
                 spec_json, definition_json, note),
            )
        return version
    except Exception:
        log.warning("agent_versions: snapshot of '%s' failed, continuing without it",
                    agent_id, exc_info=True)
        return None


def ensure_current_version(agent_id: str, *, actor: Optional[str] = None) -> Optional[int]:
    """The version number that holds the agent's live definition, writing a
    snapshot first when history does not have it yet.

    History records a state just before it is replaced, so the live state is
    usually *not* in history. An A/B experiment arm must point at a stored
    row (``evals/experiments.py``), which is what this is for: "the current
    definition" as an arm becomes a real, immutable version. Returns None
    when the agent does not exist or the snapshot could not be written.
    """
    from agents.registry import get_agent

    spec = get_agent(agent_id)
    if spec is None:
        return None
    fp = _fingerprint_spec(spec)
    latest = _latest_version_row(agent_id)
    if latest is not None and latest["hash"] == fp["hash"]:
        return int(latest["version"])
    written = snapshot_if_changed(agent_id, actor=actor, note="snapshot for an experiment")
    if written is not None:
        return int(written)
    latest = _latest_version_row(agent_id)
    if latest is not None and latest["hash"] == fp["hash"]:
        return int(latest["version"])
    return None


def _row_to_entry(row) -> Dict[str, Any]:
    return {
        "version": row["version"],
        "hash": row["hash"],
        "created_at": row["created_at"],
        "actor": row["actor"],
        "note": row["note"],
        "spec": json.loads(row["spec_json"] or "{}"),
        "definition": json.loads(row["definition_json"] or "{}"),
    }


def get_version_row(agent_id: str, version: int) -> Optional[Dict[str, Any]]:
    """One stored version's full content, or None."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT version, hash, created_at, actor, note, spec_json, definition_json "
        "FROM agent_versions WHERE agent_id = ? AND version = ?",
        (str(agent_id), int(version)),
    ).fetchone()
    return _row_to_entry(row) if row is not None else None


def current_snapshot(agent_id: str) -> Optional[Dict[str, Any]]:
    """The live registry state, shaped like a stored version entry, so it can
    be diffed against a historical one (``against=current``)."""
    from agents.registry import get_agent

    spec = get_agent(agent_id)
    if spec is None:
        return None
    fp = _fingerprint_spec(spec)
    return {
        "version": None,
        "hash": fp["hash"],
        "created_at": None,
        "actor": None,
        "note": None,
        "spec": spec.to_dict(),
        "definition": {
            "instructions": fp["parts"].get("instructions", ""),
            "capabilities": fp["parts"].get("capabilities", ""),
            "usage": fp["parts"].get("usage", ""),
        },
    }


def _summarize_change(prev: Optional[Dict[str, Any]], cur: Dict[str, Any]) -> Dict[str, Any]:
    """What changed between two ``_spec_parts``-shaped dicts (plus the three
    markdown files), for the versions list."""
    if prev is None:
        return {
            "tools_added": [], "tools_removed": [],
            "model_changed": False, "from_model": None, "to_model": None,
            "files_changed": [],
            "initial": True,
        }
    prev_tools = set(prev.get("tools") or [])
    cur_tools = set(cur.get("tools") or [])
    files_changed = [
        f for f in ("instructions", "capabilities", "usage")
        if (prev.get(f) or "") != (cur.get(f) or "")
    ]
    prev_model = (prev.get("provider"), prev.get("model"))
    cur_model = (cur.get("provider"), cur.get("model"))
    return {
        "tools_added": sorted(cur_tools - prev_tools),
        "tools_removed": sorted(prev_tools - cur_tools),
        "model_changed": prev_model != cur_model,
        "from_model": prev.get("model"),
        "to_model": cur.get("model"),
        "files_changed": files_changed,
        "initial": False,
    }


def list_versions(agent_id: str) -> List[Dict[str, Any]]:
    """History entries for an agent, oldest first, each with a summary of what
    changed relative to the entry before it."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT version, hash, created_at, actor, note, spec_json, definition_json "
        "FROM agent_versions WHERE agent_id = ? ORDER BY version ASC",
        (str(agent_id),),
    ).fetchall()

    out: List[Dict[str, Any]] = []
    prev_parts: Optional[Dict[str, Any]] = None
    for row in rows:
        entry = _row_to_entry(row)
        parts = {**_spec_parts_from_dict(entry["spec"]), **entry["definition"]}
        summary = _summarize_change(prev_parts, parts)
        out.append({
            "version": entry["version"],
            "hash": entry["hash"],
            "created_at": entry["created_at"],
            "actor": entry["actor"],
            "note": entry["note"],
            "summary": summary,
        })
        prev_parts = parts
    return out


# -------------------- Diff --------------------

_DIFF_PARTS = ("spec", "instructions", "capabilities", "usage")


def _part_text(entry: Dict[str, Any], part: str) -> str:
    if part == "spec":
        parts = _spec_parts_from_dict(entry["spec"]) if entry.get("spec") else {}
        return json.dumps(parts, indent=2, sort_keys=True, ensure_ascii=False)
    return (entry.get("definition") or {}).get(part, "") or ""


def _label(entry: Dict[str, Any]) -> str:
    v = entry.get("version")
    return f"v{v}" if v is not None else "current"


def diff_entries(from_entry: Dict[str, Any], to_entry: Dict[str, Any]) -> Dict[str, str]:
    """Unified diff text per part (spec / instructions / capabilities / usage)
    between two version entries. Parts with no difference are omitted."""
    out: Dict[str, str] = {}
    for part in _DIFF_PARTS:
        a_text = _part_text(from_entry, part)
        b_text = _part_text(to_entry, part)
        if a_text == b_text:
            continue
        diff = difflib.unified_diff(
            a_text.splitlines(keepends=True),
            b_text.splitlines(keepends=True),
            fromfile=f"{part}@{_label(from_entry)}",
            tofile=f"{part}@{_label(to_entry)}",
        )
        out[part] = "".join(diff)
    return out


# -------------------- Rollback --------------------

def rollback_to(agent_id: str, version: int, *, actor: Optional[str] = None) -> Dict[str, Any]:
    """Restore a historical version as the agent's current state.

    Goes through ``agents.registry.add_agent`` to apply the restored record,
    so the capability guard runs on the restored tool set exactly as it would
    on any other edit (a rollback to a now-blocked combination is refused the
    same way), and a system agent gets stamped ``user_modified`` the same way
    a normal edit does. ``add_agent`` itself snapshots whatever is on disk
    right before overwriting it, so the state this rollback replaces is
    captured in history too — there is no separate snapshot call here.

    Raises ``ValueError`` when the version does not exist, or when
    ``add_agent`` refuses the restored tool set.
    """
    entry = get_version_row(agent_id, version)
    if entry is None:
        raise ValueError(f"Agent '{agent_id}' has no version {version}")

    from agents.registry import _validate_agent_dict, add_agent

    restored_spec = _validate_agent_dict(entry["spec"])
    add_agent(restored_spec, user_edit=True, actor=actor, note=f"rollback to v{version}")

    def_id = restored_spec.def_id()
    defn = entry["definition"] or {}
    _write_definition_files(def_id, defn)
    return restored_spec.to_dict()


def _write_definition_files(def_id: str, defn: Dict[str, Any]) -> None:
    """Rewrite the three markdown files to match a stored snapshot.

    Mirrors the semantics of the dashboard's PUT /definition route: a
    non-empty value is written, an empty one deletes the file (capabilities
    and usage are optional; instructions is required and never rolled back to
    empty since ``add_agent`` would already have refused an unusable agent).
    """
    instructions = defn.get("instructions") or ""
    if instructions.strip():
        prompt_assembly.write_instructions(def_id, instructions)

    caps_path = prompt_assembly.agent_dir(def_id) / prompt_assembly.CAPABILITIES_FILE
    capabilities = defn.get("capabilities") or ""
    if capabilities.strip():
        prompt_assembly.write_capabilities(def_id, capabilities)
    elif caps_path.exists():
        caps_path.unlink()

    usage_path = prompt_assembly.agent_dir(def_id) / prompt_assembly.USAGE_FILE
    usage = defn.get("usage") or ""
    if usage.strip():
        prompt_assembly.write_usage(def_id, usage)
    elif usage_path.exists():
        usage_path.unlink()
