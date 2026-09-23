"""LLM-based knowledge extraction into a shared memory pool.

Two-step flow (the agent-facing path):
- `propose_extraction(pool_id, text, ...)` — runs one LLM call over the text and
  returns a validated proposal `{slots, notes, episodes, triples}` WITHOUT
  persisting it. The proposal is stored as a pending extraction with an id.
- `commit_extraction(pool_id, extraction_id, drop=...)` — persists a previously
  proposed extraction exactly as reviewed (minus optional dropped items).

One-shot flow (for hooks / programmatic use):
- `extract_knowledge(pool_id, text, ...)` — propose + persist in a single call.

All functions are best-effort: they return summary dicts and never raise.

Unlike the conversational `recall`/`remember` tools, this is a pipeline: the
extraction prompt is given the pool's existing slot names, note titles, and
graph vocabulary so the LLM merges into existing entries instead of creating
duplicates. All writes carry `source: knowledge_extract` provenance.

`create_extraction_tools(pool_id)` wraps the two-step flow as the pool-bound
`extract_from_text` + `save_extraction` tools for extraction agents
(e.g. memory_extractor).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional
from uuid import uuid4

from common.docstore import DocStore
from common.paths import pool_extractions_file
from memory.graph_extract import _normalize_token, _resolve_workspace_model

# Per-call caps — extraction should distill, not transcribe.
MAX_SLOTS_PER_CALL = 8
MAX_NOTES_PER_CALL = 3
MAX_EPISODES_PER_CALL = 3
MAX_TRIPLES_PER_CALL = 12

# Pending-extraction retention per pool (oldest pruned first).
MAX_PENDING_PER_POOL = 20

_VALID_EPISODE_KINDS = {"interaction", "task", "decision", "error", "observation"}
_VALID_EPISODE_OUTCOMES = {"success", "failure", "partial", "n/a"}
_SLOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,60}$")

PROVENANCE = "knowledge_extract"


_EXTRACT_PROMPT = """You are a knowledge-extraction engine for an agent memory system.
Distill the text below into durable memory items. Be selective: a few high-signal
items beat transcribing everything.

Return ONE JSON object with this exact shape and NO other prose:

{
  "slots": [{"slot": "<snake_case_name>", "data": {"<field>": <value>}, "reason": "<why durable + why this slot>", "evidence": "<short verbatim quote>"}],
  "notes": [{"title": "<short title>", "content": "<narrative content>", "reason": "<why worth keeping>", "evidence": "<short verbatim quote>"}],
  "episodes": [{"kind": "decision|error|task", "summary": "<1-3 sentences>", "subject": "<topic>", "outcome": "success|failure|partial|n/a", "tags": ["<tag>"], "reason": "<why notable>", "evidence": "<short verbatim quote>"}],
  "triples": [{"source": {"type": "<entity_type>", "name": "<entity_name>"}, "relation": "<relation>", "target": {"type": "<entity_type>", "name": "<entity_name>"}, "evidence": "<short verbatim quote>"}]
}

Rules:
- EVERY item must include "reason" (one sentence: why this is worth storing and why that
  destination) and "evidence" (a verbatim quote of at most 25 words from the text that
  supports it). Items without supporting evidence in the text must not be proposed.
- "slots" hold durable facts: identities, preferences, configurations, decisions in
  force. Use lowercase snake_case slot names. When a fact relates to an EXISTING slot
  listed below, use that SAME slot name and include only the new or changed fields —
  never invent a parallel slot like "project_status" when "project" exists.
- "notes" hold narrative context too long for a slot (summaries, rationale). Reuse an
  existing note title only to extend that topic.
- "episodes" capture discrete events worth remembering: decisions made, errors hit,
  tasks completed. Skip routine chatter.
- "triples" capture clearly stated relationships between named entities. Use lowercase
  snake_case for type and relation (e.g. "person", "project", "owns", "depends_on").
  Reuse the existing graph vocabulary listed below. Skip vague pronouns.
- When a slot and a triple entity refer to the SAME thing, give them the SAME name
  (underscores vs spaces are tolerated): if you propose the slot "emberglass_wand",
  the triple entity must be named "emberglass_wand" or "emberglass wand" — never a
  variant like "the emberglass wand" or "wand_of_ember".
- Skip greetings, transient status, speculation, secrets/credentials, and anything
  already in memory unchanged.
- Empty collections are fine. If nothing is worth storing, return all-empty.

Existing memory (merge targets — reuse these names where related):
{context}
{focus_section}
Text:
\"\"\"
{text}
\"\"\"
"""


# ---------------------------------------------------------------------------
# Existing-memory context for the prompt
# ---------------------------------------------------------------------------

def _build_pool_context(pool_id: str) -> str:
    """Compact listing of existing slots/notes/graph vocabulary for the prompt."""
    lines: list[str] = []
    try:
        from memory.store import MemoryStore
        from memory.tool import JOURNAL_PREFIX

        mem = MemoryStore().get(pool_id)
        if mem:
            if mem.structured_data:
                lines.append("Slots:")
                for slot, data in list(mem.structured_data.items())[:20]:
                    keys = ", ".join(list(data.keys())[:8]) if isinstance(data, dict) else ""
                    lines.append(f"- {slot}" + (f" (fields: {keys})" if keys else ""))
            titles = [n.get("title", "") for n in mem.notes if not n.get("title", "").startswith(JOURNAL_PREFIX)]
            if titles:
                lines.append("Notes: " + ", ".join(titles[:20]))
    except Exception:
        pass
    try:
        from memory.graph import GraphStore

        gstats = GraphStore(pool_id).stats()
        types = sorted((gstats.get("types") or {}).items(), key=lambda kv: -kv[1])[:8]
        rels = sorted((gstats.get("relations") or {}).items(), key=lambda kv: -kv[1])[:8]
        if types:
            lines.append("Graph entity types: " + ", ".join(k for k, _ in types))
        if rels:
            lines.append("Graph relations: " + ", ".join(k for k, _ in rels))
    except Exception:
        pass
    return "\n".join(lines) if lines else "(memory pool is empty)"


# ---------------------------------------------------------------------------
# Proposal parsing / validation
# ---------------------------------------------------------------------------

def _extract_json_object(raw: str) -> Optional[dict]:
    # Robust against reasoning-model output (inline <think> blocks, trailing
    # prose); shared with graph_extract via memory.json_extract.
    from memory.json_extract import extract_json_object
    return extract_json_object(raw)


def _normalize_slot_name(name: Any) -> Optional[str]:
    if not isinstance(name, str):
        return None
    s = name.strip().lower().replace(" ", "_").replace("-", "_")
    return s if _SLOT_NAME_RE.match(s) else None


def _clean_meta(item: dict) -> dict:
    """Optional per-item rationale carried for review UI; never persisted as data."""
    meta: dict = {}
    reason = item.get("reason")
    if isinstance(reason, str) and reason.strip():
        meta["reason"] = reason.strip()[:240]
    evidence = item.get("evidence")
    if isinstance(evidence, str) and evidence.strip():
        meta["evidence"] = evidence.strip()[:280]
    return meta


def _parse_proposal(raw: str) -> dict:
    """Validate the LLM output into a clean proposal dict. Tolerant; drops junk."""
    proposal: dict = {"slots": [], "notes": [], "episodes": [], "triples": []}
    obj = _extract_json_object(raw)
    if not obj:
        return proposal

    slots = obj.get("slots")
    slot_items: list = []
    if isinstance(slots, dict):
        # Legacy shape: {"name": {fields}} without per-item metadata.
        slot_items = [{"slot": k, "data": v} for k, v in slots.items()]
    elif isinstance(slots, list):
        slot_items = [s for s in slots if isinstance(s, dict)]
    seen_slots: set[str] = set()
    for item in slot_items[: MAX_SLOTS_PER_CALL * 2]:
        slot = _normalize_slot_name(item.get("slot") or item.get("name"))
        if not slot or slot in seen_slots:
            continue
        data = item.get("data")
        if data is not None and not isinstance(data, dict):
            data = {"value": data}
        if not data:
            continue
        seen_slots.add(slot)
        proposal["slots"].append({"slot": slot, "data": data, **_clean_meta(item)})
        if len(proposal["slots"]) >= MAX_SLOTS_PER_CALL:
            break

    from memory.tool import JOURNAL_PREFIX

    notes = obj.get("notes")
    if isinstance(notes, list):
        for n in notes:
            if not isinstance(n, dict):
                continue
            title = n.get("title")
            content = n.get("content")
            if not (isinstance(title, str) and title.strip() and isinstance(content, str) and content.strip()):
                continue
            if title.strip().startswith(JOURNAL_PREFIX):
                continue
            proposal["notes"].append({
                "title": title.strip()[:120],
                "content": content.strip(),
                **_clean_meta(n),
            })
            if len(proposal["notes"]) >= MAX_NOTES_PER_CALL:
                break

    episodes = obj.get("episodes")
    if isinstance(episodes, list):
        for e in episodes:
            if not isinstance(e, dict):
                continue
            kind = e.get("kind")
            summary = e.get("summary")
            if kind not in _VALID_EPISODE_KINDS or not (isinstance(summary, str) and summary.strip()):
                continue
            outcome = e.get("outcome")
            if outcome not in _VALID_EPISODE_OUTCOMES:
                outcome = None
            tags = e.get("tags")
            if not (isinstance(tags, list) and all(isinstance(t, str) for t in tags)):
                tags = []
            subject = e.get("subject")
            if not isinstance(subject, str):
                subject = None
            proposal["episodes"].append({
                "kind": kind,
                "summary": summary.strip(),
                "subject": subject,
                "outcome": outcome,
                "tags": tags[:6],
                **_clean_meta(e),
            })
            if len(proposal["episodes"]) >= MAX_EPISODES_PER_CALL:
                break

    triples = obj.get("triples")
    if isinstance(triples, list):
        for t in triples[: MAX_TRIPLES_PER_CALL * 2]:
            if not isinstance(t, dict):
                continue
            src = t.get("source") or {}
            tgt = t.get("target") or {}
            if not isinstance(src, dict) or not isinstance(tgt, dict):
                continue
            s_type = _normalize_token(src.get("type"))
            s_name = _normalize_token(src.get("name"))
            t_type = _normalize_token(tgt.get("type"))
            t_name = _normalize_token(tgt.get("name"))
            relation = _normalize_token(t.get("relation"))
            if not all([s_type, s_name, t_type, t_name, relation]):
                continue
            proposal["triples"].append({
                "source": {"type": s_type, "name": s_name},
                "relation": relation,
                "target": {"type": t_type, "name": t_name},
                **_clean_meta(t),
            })
            if len(proposal["triples"]) >= MAX_TRIPLES_PER_CALL:
                break

    return proposal


def _proposal_counts(proposal: dict) -> dict:
    return {
        "slots": len(proposal["slots"]),
        "notes": len(proposal["notes"]),
        "episodes": len(proposal["episodes"]),
        "triples": len(proposal["triples"]),
    }


def _apply_drops(proposal: dict, drop: Iterable[str]) -> tuple[dict, list[str], list[str]]:
    """Filter dropped items out of a proposal.

    Drop refs: "slot:<name>", "note:<title>", "episode:<index>", "triple:<index>".
    Returns (filtered_proposal, dropped, unmatched).
    """
    filtered = {
        "slots": list(proposal["slots"]),
        "notes": list(proposal["notes"]),
        "episodes": list(proposal["episodes"]),
        "triples": list(proposal["triples"]),
    }
    dropped: list[str] = []
    unmatched: list[str] = []
    ep_drop: set[int] = set()
    tr_drop: set[int] = set()

    for ref in drop:
        if not isinstance(ref, str) or ":" not in ref:
            unmatched.append(str(ref))
            continue
        category, _, key = ref.partition(":")
        category = category.strip().lower()
        key = key.strip()
        if category == "slot":
            before = len(filtered["slots"])
            filtered["slots"] = [s for s in filtered["slots"] if s["slot"] != key]
            (dropped if len(filtered["slots"]) < before else unmatched).append(ref)
        elif category == "note":
            before = len(filtered["notes"])
            filtered["notes"] = [n for n in filtered["notes"] if n["title"] != key]
            (dropped if len(filtered["notes"]) < before else unmatched).append(ref)
        elif category in ("episode", "triple"):
            try:
                idx = int(key)
            except ValueError:
                unmatched.append(ref)
                continue
            items = proposal["episodes"] if category == "episode" else proposal["triples"]
            if 0 <= idx < len(items):
                (ep_drop if category == "episode" else tr_drop).add(idx)
                dropped.append(ref)
            else:
                unmatched.append(ref)
        else:
            unmatched.append(ref)

    if ep_drop:
        filtered["episodes"] = [e for i, e in enumerate(proposal["episodes"]) if i not in ep_drop]
    if tr_drop:
        filtered["triples"] = [t for i, t in enumerate(proposal["triples"]) if i not in tr_drop]

    return filtered, dropped, unmatched


# ---------------------------------------------------------------------------
# Pending-extraction store (per pool, one row per proposal in the documents
# table, collection "extractions:<pool_id>")
# ---------------------------------------------------------------------------

def _record_key(rec: Any) -> Optional[str]:
    return str(rec.get("id")) if isinstance(rec, dict) and rec.get("id") else None


class PendingExtractionStore:
    """Store for proposed-but-not-yet-saved extractions of one pool, kept in
    the ``documents`` table through :class:`common.docstore.DocStore`."""

    def __init__(self, pool_id: str):
        self.pool_id = str(pool_id)
        # Legacy per-pool file, imported once on first use.
        self.path: Path = pool_extractions_file(self.pool_id)
        self.docs = DocStore(f"extractions:{self.pool_id}", legacy_file=self.path,
                              legacy_key=_record_key)

    def add(self, proposal: dict, *, focus: Optional[str] = None, text_chars: int = 0, timeout: float = 10.0) -> dict:
        entry = {
            "id": str(uuid4())[:8],
            "pool_id": self.pool_id,
            "status": "pending",
            "proposal": proposal,
            "focus": focus,
            "text_chars": text_chars,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "saved_at": None,
        }
        with self.docs.transaction():
            self.docs.put(entry["id"], entry)
            keys = self.docs.keys()
            if len(keys) > MAX_PENDING_PER_POOL:
                for old_key in keys[:-MAX_PENDING_PER_POOL]:
                    self.docs.delete(old_key)
        return entry

    def get(self, extraction_id: str, timeout: float = 10.0) -> Optional[dict]:
        return self.docs.get(extraction_id)

    def list(self, *, status: Optional[str] = None, timeout: float = 10.0) -> List[dict]:
        entries = self.docs.values()
        if status:
            entries = [e for e in entries if e.get("status") == status]
        return entries

    def mark_saved(self, extraction_id: str, timeout: float = 10.0) -> bool:
        with self.docs.transaction():
            entry = self.docs.get(extraction_id)
            if entry is None:
                return False
            entry = dict(entry)
            entry["status"] = "saved"
            entry["saved_at"] = datetime.now(timezone.utc).isoformat()
            self.docs.put(extraction_id, entry)
            return True


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _persist_proposal(pool_id: str, proposal: dict) -> dict:
    """Write a validated proposal into the pool. Returns per-layer results."""
    result: dict = {
        "slots_created": [], "slots_merged": [],
        "notes_created": [], "notes_extended": [], "notes_skipped": [],
        "episodes_recorded": 0, "edges_added": 0,
        "errors": [],
    }

    if proposal["slots"] or proposal["notes"]:
        try:
            from memory.store import MemoryStore

            store = MemoryStore()
            mem = store.get(pool_id)
            if not mem:
                result["errors"].append(f"Memory pool not found: {pool_id}")
                return result

            for item in proposal["slots"]:
                slot, data = item["slot"], item["data"]
                existing = mem.structured_data.get(slot)
                if isinstance(existing, dict):
                    mem.structured_data[slot] = {**existing, **data}
                    result["slots_merged"].append(slot)
                else:
                    mem.structured_data[slot] = dict(data)
                    result["slots_created"].append(slot)

            for note in proposal["notes"]:
                existing_note = next((n for n in mem.notes if n.get("title") == note["title"]), None)
                if existing_note:
                    if note["content"] in (existing_note.get("content") or ""):
                        result["notes_skipped"].append(note["title"])
                    else:
                        existing_note["content"] = (existing_note.get("content") or "") + "\n\n" + note["content"]
                        result["notes_extended"].append(note["title"])
                else:
                    from uuid import uuid4 as _uuid4

                    mem.notes.append({
                        "id": str(_uuid4()),
                        "title": note["title"],
                        "content": note["content"],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
                    result["notes_created"].append(note["title"])

            from memory.tool import _persist

            _persist(store, mem)
        except Exception as e:
            result["errors"].append(f"slot/note persist failed: {e}")

    if proposal["episodes"]:
        try:
            from memory.episodic import Episode, EpisodeStore

            estore = EpisodeStore(pool_id)
            for e in proposal["episodes"]:
                estore.add(Episode(
                    pool_id=pool_id,
                    kind=e["kind"],
                    summary=e["summary"],
                    subject=e.get("subject"),
                    outcome=e.get("outcome"),
                    tags=e.get("tags") or [],
                    actor=PROVENANCE,
                    details={"source": PROVENANCE},
                ))
                result["episodes_recorded"] += 1
        except Exception as e:
            result["errors"].append(f"episode persist failed: {e}")

    if proposal["triples"] or proposal["slots"]:
        try:
            from memory.graph import GraphStore

            gstore = GraphStore(pool_id)
            # Triples first, so their typed entity nodes exist before the slot
            # bridge looks for same-name twins.
            for t in proposal["triples"]:
                try:
                    src_node, _ = gstore.upsert_node(t["source"]["type"], t["source"]["name"])
                    tgt_node, _ = gstore.upsert_node(t["target"]["type"], t["target"]["name"])
                    gstore.add_edge(src_node.id, tgt_node.id, t["relation"], properties={"source": PROVENANCE})
                    result["edges_added"] += 1
                except Exception as ge:
                    result["errors"].append(f"edge persist failed for {t}: {ge}")
            # Mirror written slots into the graph so `traverse` can reach them.
            # When a typed entity node with the same name already exists (e.g.
            # ("item", "emberglass_wand") from a triple), attach the slot data
            # to THAT node instead of creating a parallel ("slot", ...) twin.
            slot_data = {s["slot"]: s["data"] for s in proposal["slots"]}
            for slot in [*result["slots_created"], *result["slots_merged"]]:
                try:
                    data = slot_data.get(slot) or {}
                    props = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool)) or v is None}
                    twin = next((n for n in gstore.find_twins(slot) if n.type != "slot"), None)
                    if twin is not None:
                        gstore.upsert_node(twin.type, twin.name, props)
                    else:
                        gstore.upsert_node("slot", slot, props)
                except Exception as ge:
                    result["errors"].append(f"slot graph bridge failed for {slot}: {ge}")
        except Exception as e:
            result["errors"].append(f"graph persist failed: {e}")

    return result


# ---------------------------------------------------------------------------
# LLM run (shared by propose and one-shot flows)
# ---------------------------------------------------------------------------

def _run_extraction(
    pool_id: str,
    text: str,
    *,
    focus: Optional[str],
    max_text_chars: int,
    model_overrides: Optional[dict],
    summary: dict,
) -> Optional[dict]:
    """Run the extraction LLM and return a validated proposal, or None on failure.

    Mutates `summary` with model info / truncation / errors.
    """
    snippet = text.strip()
    if len(snippet) > max_text_chars:
        snippet = snippet[:max_text_chars]
        summary["truncated_to_chars"] = max_text_chars

    from agents.agent_utils import build_chat_model

    overrides: dict = {}
    ws_overrides = _resolve_workspace_model(pool_id)
    if ws_overrides:
        overrides.update(ws_overrides)
    if model_overrides:
        overrides.update(model_overrides)
    overrides.setdefault("temperature", 0.0)
    overrides.setdefault("streaming", False)
    summary["model_source"] = "workspace" if ws_overrides else "global_env"

    try:
        llm = build_chat_model(**overrides)
    except Exception as e:
        summary["ok"] = False
        summary["errors"].append(f"build_chat_model failed: {e}")
        return None

    focus_section = f"\nFocus hint from the operator: {focus.strip()}\n" if focus and focus.strip() else "\n"
    prompt = (
        _EXTRACT_PROMPT
        .replace("{context}", _build_pool_context(pool_id))
        .replace("{focus_section}", focus_section)
        .replace("{text}", snippet)
    )

    try:
        resp = llm.invoke(prompt)
    except Exception as e:
        summary["ok"] = False
        summary["errors"].append(f"llm.invoke failed: {e}")
        return None

    raw = getattr(resp, "content", None)
    if isinstance(raw, list):
        raw = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
    if not isinstance(raw, str):
        raw = str(raw or "")

    return _parse_proposal(raw)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def propose_extraction(
    pool_id: str,
    text: str,
    *,
    focus: Optional[str] = None,
    max_text_chars: int = 8000,
    model_overrides: Optional[dict] = None,
) -> dict:
    """Step 1: extract a proposal from `text` and store it as pending. Never raises.

    Nothing is written to memory. The returned dict carries `extraction_id`,
    which `commit_extraction` uses to persist the reviewed proposal verbatim.
    """
    summary: dict = {"ok": True, "extraction_id": None, "proposal": None, "errors": []}
    try:
        if not text or len(text.strip()) < 40:
            summary["errors"].append("text too short to extract from (min 40 chars)")
            return summary

        proposal = _run_extraction(
            pool_id, text,
            focus=focus, max_text_chars=max_text_chars,
            model_overrides=model_overrides, summary=summary,
        )
        if proposal is None:
            return summary

        # Annotate where each slot/note will land (create vs merge/extend) so the
        # review UI can show destinations before anything is saved.
        try:
            from memory.store import MemoryStore

            mem = MemoryStore().get(pool_id)
            existing_slots = set((mem.structured_data or {}).keys()) if mem else set()
            existing_notes = {n.get("title") for n in mem.notes} if mem else set()
            for s in proposal["slots"]:
                s["mode"] = "merge" if s["slot"] in existing_slots else "create"
            for n in proposal["notes"]:
                n["mode"] = "extend" if n["title"] in existing_notes else "create"
        except Exception:
            pass

        summary["proposal"] = proposal
        summary["counts"] = _proposal_counts(proposal)

        if not any(summary["counts"].values()):
            summary["note"] = "Nothing extractable — no pending extraction was created."
            return summary

        entry = PendingExtractionStore(pool_id).add(
            proposal, focus=focus, text_chars=len(text.strip()),
        )
        summary["extraction_id"] = entry["id"]
        return summary
    except Exception as e:
        summary["ok"] = False
        summary["errors"].append(f"propose_extraction crashed: {e}")
        return summary


def commit_extraction(
    pool_id: str,
    extraction_id: str,
    *,
    drop: Optional[List[str]] = None,
) -> dict:
    """Step 2: persist a pending extraction into the pool. Never raises.

    `drop` removes reviewed-out items first: "slot:<name>", "note:<title>",
    "episode:<index>", "triple:<index>" (indexes as shown in the proposal).
    """
    summary: dict = {"ok": True, "extraction_id": extraction_id, "persisted": None, "errors": []}
    try:
        store = PendingExtractionStore(pool_id)
        entry = store.get(extraction_id)
        if not entry:
            summary["ok"] = False
            summary["errors"].append(
                f"Extraction '{extraction_id}' not found for this pool — run extract_from_text first."
            )
            return summary
        if entry.get("status") == "saved":
            summary["ok"] = False
            summary["errors"].append(f"Extraction '{extraction_id}' was already saved.")
            return summary

        proposal = entry["proposal"]
        if drop:
            proposal, dropped, unmatched = _apply_drops(proposal, drop)
            summary["dropped"] = dropped
            if unmatched:
                summary["errors"].append(f"drop refs not matched: {unmatched}")

        counts = _proposal_counts(proposal)
        summary["counts"] = counts
        if not any(counts.values()):
            summary["note"] = "All items were dropped — nothing saved."
            store.mark_saved(extraction_id)
            return summary

        persisted = _persist_proposal(pool_id, proposal)
        summary["persisted"] = persisted
        summary["errors"].extend(persisted.pop("errors", []))
        store.mark_saved(extraction_id)
        return summary
    except Exception as e:
        summary["ok"] = False
        summary["errors"].append(f"commit_extraction crashed: {e}")
        return summary


def extract_knowledge(
    pool_id: str,
    text: str,
    *,
    focus: Optional[str] = None,
    dry_run: bool = False,
    max_text_chars: int = 8000,
    model_overrides: Optional[dict] = None,
) -> dict:
    """One-shot: distill `text` into the pool in a single call. Never raises.

    For hooks and programmatic use. Agents should prefer the two-step
    propose/commit flow so the operator can review before saving.
    """
    summary: dict = {"ok": True, "dry_run": dry_run, "proposal": None, "persisted": None, "errors": []}
    try:
        if not text or len(text.strip()) < 40:
            summary["errors"].append("text too short to extract from (min 40 chars)")
            return summary

        proposal = _run_extraction(
            pool_id, text,
            focus=focus, max_text_chars=max_text_chars,
            model_overrides=model_overrides, summary=summary,
        )
        if proposal is None:
            return summary

        summary["proposal"] = proposal
        summary["counts"] = _proposal_counts(proposal)

        if dry_run or not any(summary["counts"].values()):
            return summary

        persisted = _persist_proposal(pool_id, proposal)
        summary["persisted"] = persisted
        summary["errors"].extend(persisted.pop("errors", []))
        return summary
    except Exception as e:
        summary["ok"] = False
        summary["errors"].append(f"extract_knowledge crashed: {e}")
        return summary


# ---------------------------------------------------------------------------
# Pool-bound tools for extraction agents
# ---------------------------------------------------------------------------

def create_extraction_tools(pool_id: str) -> list:
    """Return extraction tools bound to pool_id (the two-step flow).

    extract_from_text(text, focus) — step 1: run the extraction pipeline and get
    back a proposal + extraction_id. Nothing is written to memory yet.
    save_extraction(extraction_id, drop) — step 2: persist a reviewed proposal.
    """
    from pydantic import BaseModel, Field
    from langchain_core.tools import StructuredTool

    class _ExtractInput(BaseModel):
        text: str = Field(
            ...,
            description=(
                "The raw source material to distill — pass it verbatim (transcript chunk, "
                "document section, journal entries). Max ~8000 chars per call; split longer "
                "material into coherent chunks and call once per chunk."
            ),
        )
        focus: Optional[str] = Field(
            None,
            description="Optional steering hint, e.g. 'only decisions and owners' or 'user preferences'.",
        )

    def _extract_impl(text: str, focus: Optional[str] = None) -> str:
        result = propose_extraction(pool_id, text, focus=focus)
        return json.dumps(result, default=str)

    extract_tool = StructuredTool.from_function(
        name="extract_from_text",
        description=(
            "STEP 1 of 2: distill raw text into a structured extraction proposal — durable "
            "facts as slot updates, narrative context as notes, notable events as episodes, "
            "stated relationships as graph triples. The pipeline knows the pool's existing "
            "slots, notes, and graph vocabulary and proposes merges into them. NOTHING is "
            "written to memory by this call. Returns the proposal and an `extraction_id`; "
            "present the proposal for review, then call `save_extraction` to persist it."
        ),
        func=_extract_impl,
        args_schema=_ExtractInput,
    )

    class _SaveInput(BaseModel):
        extraction_id: str = Field(..., description="The id returned by extract_from_text.")
        drop: Optional[List[str]] = Field(
            None,
            description=(
                "Items reviewed out of the proposal before saving. Refs: 'slot:<name>', "
                "'note:<title>', 'episode:<index>', 'triple:<index>' (0-based, as ordered "
                "in the proposal). Omit to save the full proposal."
            ),
        )

    def _save_impl(extraction_id: str, drop: Optional[List[str]] = None) -> str:
        result = commit_extraction(pool_id, extraction_id, drop=drop)
        return json.dumps(result, default=str)

    save_tool = StructuredTool.from_function(
        name="save_extraction",
        description=(
            "STEP 2 of 2: persist a previously proposed extraction into the shared memory "
            "pool, exactly as reviewed — slots merged, notes created/extended, episodes "
            "recorded, graph edges added, all with knowledge_extract provenance. Pass `drop` "
            "to exclude items the review rejected. Each extraction can be saved only once."
        ),
        func=_save_impl,
        args_schema=_SaveInput,
    )

    return [extract_tool, save_tool]
