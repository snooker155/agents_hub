"""
Memory consolidation ("dreams", fifth-cycle stage 2): fold a pool's own
content and a handful of its recent sessions into a NEW pool, so duplicate
notes and outdated facts do not pile up forever in the one the agent is
using right now.

The source pool (``memory_id``) is never modified: :func:`start` only ever
reads it, and the job's output is a second pool a person can review next to
the first (:mod:`dashboard.backend.routes.memory_consolidation` renders the
diff) and either switch a binding to or discard. ``memory_consolidations``
(``common/migrations/0033_memory_consolidation.py``) is the one table this
module owns, the row for one attempt: queued, then running, then done (with
the new pool id and the diff) or failed (with a reason).

The job runs in a plain background thread, like a one-off chore rather than
a tracked agent run: it has no tools, no loop, and nothing to approve, just
one model call. ``runtime/jobs.py``'s mailbox is for a runner replica's own
work queue (chat turns, entity turns) and would need a replica and an inbox
message for something that is neither; a thread the backend process owns
and polls by id is the simpler fit here, the same choice
``evals/batch.py``'s own poll makes for its slower-moving class of job.

Sessions: "a run (or chat conversation) that used this pool" is approximated
as a recent run of an agent whose binding includes the pool, checked against
the agent's home workspace record and every other workspace's override alike
(:func:`_agents_bound_to`, ``memory.binding.effective_memory_pools``,
``workspace.storage.list_workspace_folders``). This is an O(agents ×
workspaces) scan, acceptable here because it runs once per consolidation
firing rather than on a hot path, and workspace and agent counts are small
enough in practice (tens, not thousands) that it finishes well under a
second; a deployment with very many workspaces would want this cached or
indexed instead of scanned fresh each time.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from common import db

log = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_DISCARDED = "discarded"

DEFAULT_SESSION_LIMIT = 10
MAX_SESSION_LIMIT = 50
#: Per-session excerpt fed to the model; several of these plus the pool's own
#: content must still fit in one prompt.
MAX_SESSION_CHARS = 3000
MAX_SESSIONS_AGENTS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row["id"], "memory_id": row["memory_id"], "new_memory_id": row["new_memory_id"],
        "workspace": row["workspace"], "status": row["status"],
        "session_limit": row["session_limit"],
        "session_ids": json.loads(row["session_ids"]) if row["session_ids"] else [],
        "diff": json.loads(row["diff"]) if row["diff"] else None,
        "summary": row["summary"], "error": row["error"],
        "actor_kind": row["actor_kind"], "actor_id": row["actor_id"], "trigger": row["trigger"],
        "provider": row["provider"], "model": row["model"],
        "created_at": row["created_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def get(job_id: Any) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM memory_consolidations WHERE id = ?", (str(job_id),)).fetchone()
    return _row_to_dict(row) if row else None


def list_for_pool(memory_id: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """A pool's consolidation attempts, newest first."""
    limit = max(1, min(int(limit or 20), 200))
    rows = db.get_conn().execute(
        "SELECT * FROM memory_consolidations WHERE memory_id = ? "
        "ORDER BY created_at DESC LIMIT ?", (str(memory_id), limit)).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── gathering the sessions ──────────────────────────────────────────────────

def _all_workspace_names() -> set:
    """Every workspace that has a folder, so a per-workspace override can be
    checked everywhere it might live (``workspace.storage``)."""
    try:
        from workspace.storage import list_workspace_folders
        return {p.name for p in list_workspace_folders()}
    except Exception:  # noqa: BLE001 - an unreadable workspaces root yields none to scan
        log.debug("consolidation: could not list workspace folders", exc_info=True)
        return set()


def _agents_bound_to(memory_id: str) -> List[str]:
    """Agent ids whose binding includes *memory_id*: the agent's own record
    in its home workspace, or a per-workspace override in any other
    workspace (``memory.binding.effective_memory_pools``,
    ``memory.binding.home_workspace``). One pass over every workspace per
    agent, not just the home one: a session run through an override would
    otherwise never be found, and a home workspace other than "default"
    would otherwise be missed too (effective_memory_pools' own workspace
    default is "default", not "whichever workspace spec.owner_workspace
    names"). See the module docstring for the cost this accepts.
    """
    from agents.registry import list_agents
    from memory.binding import effective_memory_pools, home_workspace
    workspace_names = _all_workspace_names()
    out: List[str] = []
    for spec in list_agents():
        try:
            candidates = {home_workspace(spec)} | workspace_names
            for ws in candidates:
                if memory_id in effective_memory_pools(spec, ws):
                    out.append(spec.id)
                    break
        except Exception:  # noqa: BLE001 - one unreadable spec must not stop the scan
            log.debug("consolidation: could not resolve pools for %s", getattr(spec, "id", "?"), exc_info=True)
    return out


def _recent_sessions(memory_id: str, limit: int) -> List[Dict[str, Any]]:
    """Up to *limit* most recent run records (chat turns or task runs alike)
    of an agent bound to *memory_id*, newest first."""
    from managers import run_manager as rm
    agent_ids = _agents_bound_to(memory_id)[:MAX_SESSIONS_AGENTS]
    if not agent_ids:
        return []
    items: List[Dict[str, Any]] = []
    per_agent = max(limit, 5)
    for aid in agent_ids:
        try:
            page = rm.query_runs(agent_id=aid, limit=per_agent)
            items.extend(page.get("items") or [])
        except Exception:  # noqa: BLE001 - one agent's runs must not block the rest
            log.debug("consolidation: could not list runs for %s", aid, exc_info=True)
    items.sort(key=lambda r: str(r.get("started_at") or r.get("created_at") or ""), reverse=True)
    return items[:limit]


def _session_excerpt(run: Dict[str, Any]) -> str:
    when = run.get("started_at") or run.get("created_at") or ""
    title = run.get("title") or run.get("agent_id") or ""
    from managers.run_manager import run_exchange
    user_in, out = run_exchange(run)
    body = f"[{when}] {title}\nUser: {user_in}\nAgent: {out}".strip()
    return body if len(body) <= MAX_SESSION_CHARS else body[:MAX_SESSION_CHARS] + "\n...[truncated]"


def _pool_content(mem: Any) -> str:
    lines: List[str] = []
    for block in (mem.blocks or []):
        lines.append(f"### Block: {block.name}\n{block.value or '(empty)'}")
    for note in (mem.notes or []):
        lines.append(f"### Note: {note.get('title')}\n{note.get('content') or ''}")
    if mem.structured_data:
        lines.append("### Structured slots\n" + json.dumps(mem.structured_data, ensure_ascii=False, default=str))
    return "\n\n".join(lines) if lines else "(the pool is currently empty)"


# ── validating the model's answer ───────────────────────────────────────────

def _validate_proposal(obj: Any) -> Optional[Dict[str, Any]]:
    """Normalize the model's JSON answer, or None when its shape is unusable."""
    if not isinstance(obj, dict):
        return None
    blocks_raw = obj.get("blocks")
    notes_raw = obj.get("notes")
    slots_raw = obj.get("structured_data")
    if blocks_raw is None and notes_raw is None and slots_raw is None:
        return None
    blocks: List[Dict[str, str]] = []
    for b in (blocks_raw or []):
        if isinstance(b, dict) and str(b.get("name") or "").strip():
            blocks.append({"name": str(b["name"]).strip(), "value": str(b.get("value") or "")})
    notes: List[Dict[str, str]] = []
    for n in (notes_raw or []):
        if isinstance(n, dict) and str(n.get("title") or "").strip():
            notes.append({"title": str(n["title"]).strip(), "content": str(n.get("content") or "")})
    slots: Dict[str, Any] = {}
    if isinstance(slots_raw, dict):
        for key, val in slots_raw.items():
            if isinstance(val, dict):
                slots[str(key)] = val
    return {
        "blocks": blocks, "notes": notes, "structured_data": slots,
        "summary": str(obj.get("summary") or "").strip(),
    }


# ── diffing the source pool against the proposal ────────────────────────────

def _diff_kind(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is None and a is not None:
            out.append({"key": key, "op": "added", "after": a})
        elif b is not None and a is None:
            out.append({"key": key, "op": "removed", "before": b})
        elif b != a:
            out.append({"key": key, "op": "changed", "before": b, "after": a})
    return out


def _build_diff(source: Any, proposal: Dict[str, Any]) -> Dict[str, Any]:
    before_blocks = {b.name: b.value for b in (source.blocks or [])}
    after_blocks = {b["name"]: b["value"] for b in proposal["blocks"]}
    before_notes = {n.get("title"): n.get("content") for n in (source.notes or []) if n.get("title")}
    after_notes = {n["title"]: n["content"] for n in proposal["notes"]}
    before_slots = dict(source.structured_data or {})
    after_slots = dict(proposal["structured_data"])
    return {
        "blocks": _diff_kind(before_blocks, after_blocks),
        "notes": _diff_kind(before_notes, after_notes),
        "slots": _diff_kind(before_slots, after_slots),
    }


# ── the job itself ───────────────────────────────────────────────────────────

def _insert_queued(memory_id: str, *, workspace: Optional[str], session_limit: int,
                   trigger: str, actor_kind: Optional[str], actor_id: Optional[str]) -> str:
    job_id = str(uuid4())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO memory_consolidations (id, memory_id, workspace, status, session_limit, "
            "trigger, actor_kind, actor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, str(memory_id), workspace, STATUS_QUEUED, int(session_limit),
             trigger, actor_kind, actor_id, _now()),
        )
    return job_id


def _update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.transaction() as conn:
        conn.execute(f"UPDATE memory_consolidations SET {cols} WHERE id = ?",
                     (*fields.values(), job_id))


def _run(job_id: str) -> None:
    from memory.store import MemoryStore

    row = get(job_id)
    if row is None:
        return
    _update(job_id, status=STATUS_RUNNING, started_at=_now())
    try:
        store = MemoryStore()
        source = store.get(row["memory_id"])
        if source is None:
            raise ValueError(f"memory pool {row['memory_id']} not found")

        sessions = _recent_sessions(row["memory_id"], row["session_limit"])
        session_ids = [str(s.get("run_id")) for s in sessions if s.get("run_id")]
        sessions_text = ("\n\n".join(_session_excerpt(s) for s in sessions)
                         if sessions else "(no recent sessions found)")

        from agents.agent_utils import build_chat_model
        from memory.consolidation_prompt import CONSOLIDATION_PROMPT
        from memory.graph_extract import _resolve_workspace_model

        overrides = dict(_resolve_workspace_model(row["memory_id"]) or {})
        overrides.setdefault("temperature", 0.2)
        overrides.setdefault("streaming", False)
        llm = build_chat_model(**overrides)

        prompt = (CONSOLIDATION_PROMPT
                 .replace("{pool_content}", _pool_content(source))
                 .replace("{sessions}", sessions_text))
        resp = llm.invoke(prompt)

        try:
            from common import aux_usage
            aux_usage.record("memory_consolidate", llm=llm, response=resp)
        except Exception:  # noqa: BLE001 - accounting must never fail the job it describes
            log.debug("consolidation %s: aux usage accounting failed", job_id, exc_info=True)

        raw = getattr(resp, "content", None)
        if isinstance(raw, list):
            raw = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
        raw = raw if isinstance(raw, str) else str(raw or "")

        from memory.json_extract import extract_json_object
        obj = extract_json_object(raw)
        proposal = _validate_proposal(obj)
        if proposal is None:
            _update(job_id, status=STATUS_FAILED, finished_at=_now(),
                   session_ids=json.dumps(session_ids),
                   error="the model did not return a usable JSON proposal")
            return

        from memory.models import MemoryBlock, SharedMemory
        new_pool = SharedMemory(
            name=f"{source.name} (consolidated)",
            type=source.type,
            description=source.description,
            workspace=source.workspace,
            # Always a plain shared pool, even when the source is personal:
            # this is a reviewable candidate, not a second pool for the
            # owning user's own lookup (memory/personal.py) to trip over.
            kind="shared",
            owner_user=None,
            blocks=[MemoryBlock(name=b["name"], value=b["value"]) for b in proposal["blocks"]]
                   or [MemoryBlock(name=blk.name, value=blk.value, description=blk.description,
                                   limit_chars=blk.limit_chars) for blk in (source.blocks or [])],
            notes=[{"id": str(uuid4()), "title": n["title"], "content": n["content"],
                   "created_at": _now()} for n in proposal["notes"]],
            structured_data=proposal["structured_data"],
        )
        store.add(new_pool, record_history=False)

        diff = _build_diff(source, proposal)
        provider = str(overrides.get("provider") or getattr(llm, "model_name", "") or "")
        model_name = str(overrides.get("model") or getattr(llm, "model_name", "") or getattr(llm, "model", "") or "")
        _update(
            job_id, status=STATUS_DONE, finished_at=_now(), new_memory_id=str(new_pool.id),
            session_ids=json.dumps(session_ids), diff=json.dumps(diff, default=str),
            summary=proposal["summary"], provider=provider, model=model_name,
        )
    except Exception as exc:  # noqa: BLE001 - the failure IS this job's result
        log.warning("consolidation %s failed", job_id, exc_info=True)
        _update(job_id, status=STATUS_FAILED, finished_at=_now(), error=str(exc) or exc.__class__.__name__)


def start(memory_id: Any, *, session_limit: int = DEFAULT_SESSION_LIMIT,
         workspace: Optional[str] = None, trigger: str = "manual",
         actor_kind: Optional[str] = None, actor_id: Optional[str] = None) -> Dict[str, Any]:
    """Queue a consolidation of *memory_id* and start it in a background
    thread. Returns the queued row; poll :func:`get` for its status."""
    from memory.store import MemoryStore
    source = MemoryStore().get(memory_id)
    if source is None:
        raise ValueError(f"memory pool {memory_id} not found")
    limit = max(1, min(int(session_limit or DEFAULT_SESSION_LIMIT), MAX_SESSION_LIMIT))
    job_id = _insert_queued(str(memory_id), workspace=workspace or source.workspace,
                            session_limit=limit, trigger=trigger,
                            actor_kind=actor_kind, actor_id=actor_id)
    thread = threading.Thread(target=_run, args=(job_id,), name=f"memory-consolidate-{job_id[:8]}",
                              daemon=True)
    thread.start()
    return get(job_id) or {"id": job_id, "status": STATUS_QUEUED}


def apply_to_agent(job_id: Any, agent_id: str, *, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Point *agent_id*'s binding at the consolidation's new pool instead of
    its source pool, in *workspace* (default: the agent's home workspace).

    The source pool id is replaced wherever it appears in the agent's bound
    pool list; every other pool, and the ``read_only`` flag on a binding
    entry (``agents.registry.memory_pool_read_only_ids``), is kept as it was.
    """
    row = get(job_id)
    if row is None:
        raise ValueError(f"consolidation {job_id} not found")
    if row["status"] != STATUS_DONE or not row["new_memory_id"]:
        raise ValueError(f"consolidation {job_id} has no result to switch to")

    import dataclasses

    from agents.registry import add_agent, get_agent
    from memory.binding import effective_memory, home_workspace

    spec = get_agent(agent_id)
    if spec is None:
        raise ValueError(f"agent {agent_id} not found")
    ws = str(workspace or home_workspace(spec))

    _memory_type, memory_data = effective_memory(spec, ws)
    raw = memory_data if isinstance(memory_data, (list, tuple)) else ([memory_data] if memory_data else [])
    replaced = False
    new_list: List[Any] = []
    for raw_entry in raw:
        entry_id = raw_entry.get("id") if isinstance(raw_entry, dict) else raw_entry
        if str(entry_id or "").strip() == str(row["memory_id"]):
            replaced = True
            new_list.append({**raw_entry, "id": row["new_memory_id"]}
                            if isinstance(raw_entry, dict) else row["new_memory_id"])
        else:
            new_list.append(raw_entry)
    if not replaced:
        raise ValueError(f"agent {agent_id} is not bound to pool {row['memory_id']} in workspace {ws}")
    new_memory_data = new_list[0] if len(new_list) == 1 and not isinstance(new_list[0], dict) else new_list

    if ws == home_workspace(spec):
        new_spec = dataclasses.replace(spec, memory_type="shared", memory_data=new_memory_data)
        add_agent(new_spec)
    else:
        from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
        create_workspace_folder(ws)
        overrides = dict((get_workspace_metadata(ws) or {}).get("agent_memory_overrides") or {})
        overrides[agent_id] = {"memory_type": "shared", "memory_data": new_memory_data}
        update_workspace_metadata(ws, {"agent_memory_overrides": overrides})

    return {"agent_id": agent_id, "workspace": ws, "memory_data": new_memory_data,
            "old_memory_id": row["memory_id"], "new_memory_id": row["new_memory_id"]}


def discard(job_id: Any) -> Dict[str, Any]:
    """Drop a finished consolidation's candidate pool and mark the row
    discarded. Raises ``ValueError`` when the job is not in a terminal state
    or does not exist."""
    row = get(job_id)
    if row is None:
        raise ValueError(f"consolidation {job_id} not found")
    if row["status"] not in (STATUS_DONE, STATUS_FAILED):
        raise ValueError(f"consolidation {job_id} is still {row['status']}")
    if row["new_memory_id"]:
        from memory.store import MemoryStore
        MemoryStore().delete(row["new_memory_id"], record_history=False)
    _update(str(job_id), status=STATUS_DISCARDED)
    return get(job_id)


__all__ = [
    "DEFAULT_SESSION_LIMIT", "MAX_SESSION_LIMIT",
    "STATUS_QUEUED", "STATUS_RUNNING", "STATUS_DONE", "STATUS_FAILED", "STATUS_DISCARDED",
    "apply_to_agent", "discard", "get", "list_for_pool", "start",
]
