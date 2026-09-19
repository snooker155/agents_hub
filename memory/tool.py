from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from .store import MemoryStore


class ReadMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    note_title: Optional[str] = Field(None, description="Title of a specific note to read")
    kv_key: Optional[str] = Field(None, description="Key of a specific key-value pair to read")


def _read_memory_impl(
    memory_id: str,
    note_title: Optional[str] = None,
    kv_key: Optional[str] = None,
) -> str:
    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        if note_title:
            for n in mem.notes:
                if n["title"] == note_title:
                    return json.dumps({"ok": True, "type": "note", "memory_id": memory_id, "title": n["title"], "content": n["content"]})
            return json.dumps({"ok": False, "error": f"Note '{note_title}' not found in pool {memory_id}"})

        if kv_key:
            for kv in mem.kv_pairs:
                if kv["key"] == kv_key:
                    return json.dumps({"ok": True, "type": "kv", "memory_id": memory_id, "key": kv["key"], "value": kv["value"], "description": kv.get("description", "")})
            return json.dumps({"ok": False, "error": f"Key '{kv_key}' not found in pool {memory_id}"})

        return json.dumps({
            "ok": True,
            "memory_id": memory_id,
            "description": mem.description,
            "notes": [n["title"] for n in mem.notes],
            "kv_keys": [kv["key"] for kv in mem.kv_pairs],
        })
    except Exception as e:
        return json.dumps({"ok": False, "error": f"read_memory failed: {e}"})


read_memory_tool = StructuredTool.from_function(
    name="read_memory",
    description=(
        "Access a shared memory pool. Without extra args returns a summary listing notes and kv_keys. "
        "Pass note_title to read a note, or kv_key to read a key-value pair."
    ),
    func=_read_memory_impl,
    args_schema=ReadMemoryInput,
)


class WriteMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    note_title: Optional[str] = Field(None, description="Title of the note to create or update")
    note_content: Optional[str] = Field(None, description="Content of the note")
    kv_key: Optional[str] = Field(None, description="Key to create or update in the key-value store")
    kv_value: Optional[str] = Field(None, description="Value for the key-value pair")


def _write_memory_impl(
    memory_id: str,
    note_title: Optional[str] = None,
    note_content: Optional[str] = None,
    kv_key: Optional[str] = None,
    kv_value: Optional[str] = None,
) -> str:
    from datetime import datetime, timezone
    from uuid import uuid4

    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        if note_title is not None:
            if note_content is None:
                return json.dumps({"ok": False, "error": "note_content is required when writing a note"})
            existing = next((n for n in mem.notes if n.get("title") == note_title), None)
            if existing:
                existing["content"] = note_content
            else:
                mem.notes.append({
                    "id": str(uuid4()),
                    "title": note_title,
                    "content": note_content,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            _persist(store, mem)
            return json.dumps({"ok": True, "type": "note", "title": note_title})

        if kv_key is not None:
            if kv_value is None:
                return json.dumps({"ok": False, "error": "kv_value is required when writing a key-value pair"})
            existing = next((kv for kv in mem.kv_pairs if kv.get("key") == kv_key), None)
            if existing:
                existing["value"] = kv_value
            else:
                mem.kv_pairs.append({"key": kv_key, "value": kv_value, "description": ""})
            _persist(store, mem)
            return json.dumps({"ok": True, "type": "kv", "key": kv_key})

        return json.dumps({"ok": False, "error": "Provide note_title or kv_key to write"})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"write_memory failed: {e}"})


_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "by", "for", "to", "from", "with", "and", "or",
    "what", "who", "which", "where", "when", "why", "how", "does", "do", "did",
    "this", "that", "these", "those", "any", "some", "all", "i", "you", "we",
    "they", "it", "me", "us", "them", "my", "your", "our", "their", "its",
    "about", "tell", "show", "list", "give", "find", "have", "has", "had",
}


def _tokenize(query: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop stopwords and 1-char tokens.

    Each token is expanded with a naive singular form ("spells" → "spell",
    "entries" → "entry") so plural queries match singular stored names — the
    stored text is matched by substring, so the singular form covers both.
    """
    import re
    raw = re.split(r"[^a-z0-9_\-]+", query.lower())
    tokens = [t for t in raw if t and len(t) > 1 and t not in _STOPWORDS]
    expanded: list[str] = []
    for t in tokens:
        expanded.append(t)
        if t.endswith("ies") and len(t) > 4:
            expanded.append(t[:-3] + "y")
        elif t.endswith("es") and len(t) > 3:
            expanded.append(t[:-2])
            expanded.append(t[:-1])
        elif t.endswith("s") and len(t) > 3:
            expanded.append(t[:-1])
    # De-dup, preserving order.
    seen: set[str] = set()
    return [t for t in expanded if not (t in seen or seen.add(t))]


def _search_graph(pool_id: str, query: str, *, exclude: set, limit: int = 8) -> list[dict]:
    """Keyword search over graph nodes; returns matches with their 1-hop edges.

    Match logic: tokenize the query (drop stopwords), then a node matches if any
    token is a substring of its `name`, `type`, or any string-valued property.
    Nodes already surfaced via slot/note bridging are skipped via `exclude`.
    Results are ranked by how many tokens matched, so the best hits come first.

    The generic mirror types `slot` and `note` are excluded: those nodes are
    bookkeeping mirrors that exist only so `traverse` can reach slots/notes —
    their content lives in the (authoritative) structured/notes layers that run
    before graph search. Returning them here only surfaced content-less twins
    (e.g. a query of "note" matching every node whose *type* is "note").
    """
    _MIRROR_TYPES = {"slot", "note"}
    try:
        from memory.graph import GraphStore
        gstore = GraphStore(pool_id)
        nodes, edges = gstore.load()
        if not nodes:
            return []

        tokens = _tokenize(query)
        # Fall back to the raw lowercased query if tokenization stripped everything
        # (e.g. very short queries like "x" or all-stopword queries).
        if not tokens:
            tokens = [query.strip().lower()] if query.strip() else []
        if not tokens:
            return []

        def _score(n) -> int:
            haystack_parts = [n.name.lower(), n.type.lower()]
            for v in (n.properties or {}).values():
                if isinstance(v, str):
                    haystack_parts.append(v.lower())
            haystack = " ".join(haystack_parts)
            return sum(1 for t in tokens if t in haystack)

        scored = [
            (n, _score(n)) for n in nodes
            if n.type.strip().lower() not in _MIRROR_TYPES
            and (n.type.strip().lower(), n.name.strip().lower()) not in exclude
        ]
        matched_nodes = [n for n, s in sorted(scored, key=lambda x: -x[1]) if s > 0]
        if not matched_nodes:
            return []

        # Build adjacency once for the 1-hop summary.
        by_id = {str(n.id): n for n in nodes}
        adjacency: dict[str, list] = {}
        for e in edges:
            adjacency.setdefault(str(e.source_id), []).append(("out", e))
            adjacency.setdefault(str(e.target_id), []).append(("in", e))

        out: list[dict] = []
        for n in matched_nodes[:limit]:
            relations: list[str] = []
            for direction, e in adjacency.get(str(n.id), [])[:8]:
                if direction == "out":
                    tgt = by_id.get(str(e.target_id))
                    if tgt:
                        relations.append(f"-[{e.relation}]-> ({tgt.type}:{tgt.name})")
                else:
                    src = by_id.get(str(e.source_id))
                    if src:
                        relations.append(f"({src.type}:{src.name}) -[{e.relation}]->")
            out.append({
                "source": "graph",
                "node": {"type": n.type, "name": n.name, "properties": n.properties},
                "relations": relations,
                "traverse_hint": (
                    f"Call traverse(type={n.type!r}, name={n.name!r}) to walk all relations."
                ) if relations else None,
            })
        return out
    except Exception:
        return []


def _annotate_with_graph_hints(pool_id: str, results: list[dict]) -> None:
    """Inline 1-hop graph relations into slot/note results.

    For each matched slot/note we attach a `relations` list of human-readable
    edges (e.g. "-[uses]-> (library:jwt-library)") and a `traverse_hint` that
    tells the agent how to walk further if the user needs deeper context.
    Best-effort: errors are swallowed so a graph problem never breaks recall.
    """
    try:
        from memory.graph import GraphStore
        gstore = GraphStore(pool_id)
        nodes, edges = gstore.load()
        if not nodes or not edges:
            return

        by_id = {str(n.id): n for n in nodes}
        node_index: dict[tuple[str, str], str] = {
            (n.type.strip().lower(), n.name.strip().lower()): str(n.id)
            for n in nodes
        }
        adjacency: dict[str, list] = {}
        for e in edges:
            adjacency.setdefault(str(e.source_id), []).append(("out", e))
            adjacency.setdefault(str(e.target_id), []).append(("in", e))

        for res in results:
            if res.get("source") == "structured":
                key = ("slot", str(res.get("slot", "")).strip().lower())
                node_type, node_name = "slot", res.get("slot")
            elif res.get("source") == "note":
                key = ("note", str(res.get("title", "")).strip().lower())
                node_type, node_name = "note", res.get("title")
            else:
                continue
            nid = node_index.get(key)
            if not nid:
                continue
            adj = adjacency.get(nid, [])
            if not adj:
                continue

            relations: list[str] = []
            for direction, e in adj[:12]:
                if direction == "out":
                    tgt = by_id.get(str(e.target_id))
                    if tgt:
                        relations.append(f"-[{e.relation}]-> ({tgt.type}:{tgt.name})")
                else:
                    src = by_id.get(str(e.source_id))
                    if src:
                        relations.append(f"({src.type}:{src.name}) -[{e.relation}]->")
            if not relations:
                continue
            res["relations"] = relations
            res["traverse_hint"] = (
                f"For deeper exploration of related entities, call "
                f"traverse(type='{node_type}', name={node_name!r}) — "
                f"the relations above are only 1 hop."
            )
    except Exception:
        return


def _persist(store: MemoryStore, mem) -> None:
    mem.touch()
    memories = store.load()
    for i, m in enumerate(memories):
        if m.id == mem.id:
            memories[i] = mem
            break
    store.save(memories)


write_memory_tool = StructuredTool.from_function(
    name="write_memory",
    description=(
        "Write to a shared memory pool. "
        "Pass note_title + note_content to create/update a note. "
        "Pass kv_key + kv_value to create/update a key-value pair."
    ),
    func=_write_memory_impl,
    args_schema=WriteMemoryInput,
)


# ---------------------------------------------------------------------------
# search_memory — vector similarity search
# ---------------------------------------------------------------------------

class SearchMemoryInput(BaseModel):
    query: str = Field(..., description="Natural-language search query")
    memory_id: str = Field(..., description="ID of the shared memory pool to search")
    top_k: int = Field(default=5, description="Maximum number of chunks to return")


def _search_memory_impl(query: str, memory_id: str, top_k: int = 5) -> str:
    try:
        from memory.rag_query import search_rag, is_rag_configured
        if not is_rag_configured():
            return json.dumps({
                "ok": False,
                "error": "RAG is not configured. Set RAG_VECTOR_DB and RAG_EMBEDDING_PROVIDER in Settings.",
            })
        results = search_rag(query, memory_id, top_k=top_k)
        if not results:
            return json.dumps({"ok": True, "results": [], "note": "No relevant chunks found."})
        return json.dumps({"ok": True, "results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"search_memory failed: {e}"})


search_memory_tool = StructuredTool.from_function(
    name="search_memory_semantic",
    description=(
        "Semantic (vector) search over a shared memory pool. "
        "Embeds the query and returns the most relevant text chunks by similarity. "
        "Use when you need to find contextually related content without knowing the exact file or key. "
        "For reading a specific file, note, or key-value pair by name, use read_memory instead."
    ),
    func=_search_memory_impl,
    args_schema=SearchMemoryInput,
)


# ---------------------------------------------------------------------------
# structured memory — typed slots (e.g. user profile, project context)
# ---------------------------------------------------------------------------

class ReadStructuredMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    slot: Optional[str] = Field(None, description="Slot name to read (omit to list all slot names)")


def _read_structured_impl(memory_id: str, slot: Optional[str] = None) -> str:
    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        if slot is None:
            return json.dumps({"ok": True, "slots": list(mem.structured_data.keys())})

        data = mem.structured_data.get(slot)
        if data is None:
            return json.dumps({"ok": False, "error": f"Slot '{slot}' not found"})
        return json.dumps({"ok": True, "slot": slot, "data": data})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"read_structured_memory failed: {e}"})


read_structured_memory_tool = StructuredTool.from_function(
    name="read_structured_memory",
    description=(
        "Read structured data slots from a shared memory pool. "
        "Omit slot to list all available slot names and their schema types. "
        "Pass slot to retrieve the full data record for that slot (e.g. 'current_user', 'project_context')."
    ),
    func=_read_structured_impl,
    args_schema=ReadStructuredMemoryInput,
)


class WriteStructuredMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    slot: str = Field(..., description="Slot name to create or update (e.g. 'current_user')")
    data: dict = Field(..., description="The structured data to store")
    replace: bool = Field(
        False,
        description=(
            "If False (default), merge `data` into the existing slot — keys you pass overwrite "
            "matching keys, untouched keys are preserved. If True, replace the slot entirely "
            "with `data`."
        ),
    )


def _write_structured_impl(memory_id: str, slot: str, data: dict, replace: bool = False) -> str:
    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        existing = mem.structured_data.get(slot)
        existing_keys = list(existing.keys()) if isinstance(existing, dict) else []
        if replace or not isinstance(existing, dict):
            merged = dict(data)
            mode = "replaced" if existing is not None else "created"
        else:
            merged = {**existing, **data}
            mode = "merged"

        mem.structured_data[slot] = merged
        _persist(store, mem)
        return json.dumps({
            "ok": True,
            "slot": slot,
            "mode": mode,
            "keys_before": existing_keys,
            "keys_after": list(merged.keys()),
            "added": [k for k in data.keys() if k not in existing_keys],
            "overwritten": [k for k in data.keys() if k in existing_keys],
        })
    except Exception as e:
        return json.dumps({"ok": False, "error": f"write_structured_memory failed: {e}"})


write_structured_memory_tool = StructuredTool.from_function(
    name="write_structured_memory",
    description=(
        "Write or update a structured data slot in a shared memory pool. "
        "A `slot` is a NAMED RECORD (a dict). "
        "If the slot already exists, this call MERGES `data` into it — keys you pass are added/updated, untouched keys are preserved. "
        "Pick the SAME slot name when adding fields to an existing record. "
        "Pick a NEW slot name only for an unrelated fact. "
        "Pass `replace=True` to overwrite the whole slot (rare). "
        "Creates the slot if it doesn't exist."
    ),
    func=_write_structured_impl,
    args_schema=WriteStructuredMemoryInput,
)


# ---------------------------------------------------------------------------
# journal — append-only daily log across agent sessions
# ---------------------------------------------------------------------------

JOURNAL_PREFIX = "journal:"


def _today_journal_title() -> str:
    from datetime import datetime, timezone
    return JOURNAL_PREFIX + datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AppendJournalInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    summary: str = Field(..., description="What happened in this session — decisions, artifacts, findings, open questions")
    agent_id: Optional[str] = Field(None, description="Agent identifier to tag the entry")
    session_id: Optional[str] = Field(None, description="Session or run ID for traceability")


def _append_journal_impl(
    memory_id: str,
    summary: str,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    from datetime import datetime, timezone
    from uuid import uuid4

    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        now = datetime.now(timezone.utc)
        title = JOURNAL_PREFIX + now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        # Build header tag
        parts = [time_str]
        if agent_id:
            parts.append(f"agent:{agent_id}")
        if session_id:
            parts.append(f"session:{session_id[:8]}")
        header = "[" + " | ".join(parts) + "]"

        entry = f"{header}\n{summary.strip()}"

        existing = next((n for n in mem.notes if n.get("title") == title), None)
        if existing:
            existing["content"] = existing["content"] + "\n\n---\n\n" + entry
        else:
            mem.notes.append({
                "id": str(uuid4()),
                "title": title,
                "content": entry,
                "created_at": now.isoformat(),
            })

        _persist(store, mem)
        return json.dumps({"ok": True, "journal": title, "entries": existing["content"].count("\n\n---\n\n") + 1 if existing else 1})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"append_journal failed: {e}"})


append_journal_tool = StructuredTool.from_function(
    name="append_journal",
    description=(
        "Append a session summary to today's daily journal note in a shared memory pool. "
        "Each call adds a timestamped entry — never overwrites previous entries. "
        "Use at the end of a session or when something worth preserving happened: "
        "decisions made, artifacts produced, findings, open questions. "
        "Keep summaries concise (2-5 sentences)."
    ),
    func=_append_journal_impl,
    args_schema=AppendJournalInput,
)


# ---------------------------------------------------------------------------
# Bound tool factory — recall + remember + record_episode + recall_episodes.
# Journal is written automatically by the chat/run layer (silent_journal_append).
# ---------------------------------------------------------------------------

def create_memory_tools(pool_id: str, extra_pool_ids: Optional[list] = None, include_episodic_write: bool = True) -> list:
    """Return memory tools bound to pool_id for agents with shared memory.

    recall(query)            — cascading read: structured slots → notes → RAG
    remember(...)            — unified write: structured slot or note
    record_episode(...)      — log a discrete event (interaction/task/decision/error/observation)
    recall_episodes(...)     — query past episodes by kind/outcome/keyword/since

    pool_id is the primary pool: all writes (remember, record_episode, link)
    go there. extra_pool_ids are additional read-only pools — the read tools
    (recall, recall_episodes, traverse) search them too, primary first.
    """
    from pydantic import BaseModel, Field

    pool_ids: list[str] = []
    for _pid in (pool_id, *(extra_pool_ids or ())):
        _pid = str(_pid).strip()
        if _pid and _pid not in pool_ids:
            pool_ids.append(_pid)
    multi = len(pool_ids) > 1

    _pool_names: dict = {}

    def _pool_name(pid: str) -> str:
        if pid not in _pool_names:
            try:
                m = MemoryStore().get(pid)
                _pool_names[pid] = m.name if m else pid
            except Exception:
                return pid
        return _pool_names[pid]

    # -----------------------------------------------------------------------
    # recall — cascading read
    # -----------------------------------------------------------------------

    class _RecallInput(BaseModel):
        query: str = Field(
            ...,
            description=(
                "What you want to look up. Can be a slot name, topic, or natural-language question. "
                "The tool searches structured slots then notes, and falls back to RAG if nothing matches."
            ),
        )

    def _recall_impl(query: str) -> str:
        results: list[dict] = []
        try:
            store = MemoryStore()

            q = query.strip().lower()
            tokens = _tokenize(query)
            # Fall back to the whole query when tokenization strips everything.
            match_terms = tokens if tokens else ([q] if q else [])

            def _hit(haystack: str) -> bool:
                return any(t in haystack for t in match_terms)

            # The trace records the search cascade layer by layer so callers
            # (and the chat UI) can show WHERE each piece of data came from
            # and which layers were searched or skipped. With multiple pools
            # the counts are aggregated; each result carries a `pool` field
            # for provenance instead.
            slots_trace = {"layer": "structured_slots", "searched": 0, "hits": 0}
            notes_trace = {"layer": "notes", "searched": 0, "hits": 0}
            graph_trace = {"layer": "graph", "hits": 0}

            found_pools: list = []   # (pid, mem) for pool ids that resolved
            missing: list[str] = []

            for pid in pool_ids:
                mem = store.get(pid)
                if not mem:
                    missing.append(pid)
                    continue
                found_pools.append((pid, mem))
                pool_results: list[dict] = []

                # 1. Structured slots — name match or keyword in serialised data
                for slot, data in mem.structured_data.items():
                    data_str = json.dumps(data).lower()
                    if slot.lower() == q or _hit(slot.lower()) or _hit(data_str):
                        pool_results.append({
                            "source": "structured",
                            "slot": slot,
                            "data": data,
                        })
                slots_trace["searched"] += len(mem.structured_data)
                slots_trace["hits"] += len(pool_results)

                # 2. Notes — title or content match (skip journal notes)
                n_before = len(pool_results)
                plain_note_count = 0
                for note in mem.notes:
                    title = note.get("title", "")
                    if title.startswith(JOURNAL_PREFIX):
                        continue
                    plain_note_count += 1
                    content = note.get("content", "")
                    if _hit(title.lower()) or _hit(content.lower()):
                        pool_results.append({
                            "source": "note",
                            "title": title,
                            "content": content,
                        })
                notes_trace["searched"] += plain_note_count
                notes_trace["hits"] += len(pool_results) - n_before

                # Decorate slot/note results with graph hints so the agent knows
                # when to follow relations with `traverse`.
                _annotate_with_graph_hints(pid, pool_results)

                # 3. Graph search — keyword match over node type/name/properties.
                # Skip nodes that were already surfaced via the slot/note bridge to
                # avoid duplicating the same entity.
                already = {("slot", str(r["slot"]).strip().lower()) for r in pool_results if r.get("source") == "structured"}
                already |= {("note", str(r["title"]).strip().lower()) for r in pool_results if r.get("source") == "note"}
                graph_results = _search_graph(pid, query, exclude=already, limit=8)
                if graph_results:
                    pool_results.extend(graph_results)
                graph_trace["hits"] += len(graph_results)

                if multi:
                    for r in pool_results:
                        r["pool"] = mem.name
                results.extend(pool_results)

            if not found_pools:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {', '.join(missing or pool_ids)}"})

            trace: list[dict] = [slots_trace, notes_trace, graph_trace]

            # 4. RAG — only if nothing found in any earlier layer of any pool.
            if results:
                trace.append({"layer": "rag", "hits": 0, "skipped": "found in earlier layers"})
            else:
                try:
                    from memory.rag_query import search_rag, is_rag_configured
                    if is_rag_configured():
                        rag_total = 0
                        for pid, mem in found_pools:
                            rag_hits = search_rag(query, pid, top_k=5)
                            for hit in rag_hits:
                                entry = {"source": "rag", **hit}
                                if multi:
                                    entry["pool"] = mem.name
                                results.append(entry)
                            rag_total += len(rag_hits)
                        trace.append({"layer": "rag", "hits": rag_total})
                    else:
                        trace.append({"layer": "rag", "hits": 0, "skipped": "RAG not configured"})
                except Exception:
                    trace.append({"layer": "rag", "hits": 0, "skipped": "RAG query failed"})

            base = {"ok": True, "pool": ", ".join(m.name for _, m in found_pools), "query": query, "trace": trace}
            if missing:
                base["missing_pools"] = missing
            if not results:
                # Tell the caller what IS in the pools so it can re-query with
                # terms that actually exist (keyword search has no synonyms).
                available: dict = {}
                try:
                    slot_names: list[str] = []
                    note_titles: list[str] = []
                    graph_types: set = set()
                    for pid, mem in found_pools:
                        slot_names.extend(mem.structured_data.keys())
                        note_titles.extend(n["title"] for n in mem.notes if not n.get("title", "").startswith(JOURNAL_PREFIX))
                        from memory.graph import GraphStore
                        gstats = GraphStore(pid).stats()
                        graph_types.update((gstats.get("types") or {}).keys())
                    if slot_names:
                        available["slots"] = slot_names[:30]
                    if note_titles:
                        available["notes"] = note_titles[:20]
                    if graph_types:
                        available["graph_types"] = sorted(graph_types)
                except Exception:
                    pass
                return json.dumps({
                    **base,
                    "found": False,
                    "results": [],
                    "note": (
                        "Nothing found in memory for this query. Keyword search has no synonyms — "
                        "retry with one of the names/types listed under `available`."
                    ),
                    "available": available,
                })

            return json.dumps({**base, "found": True, "results": results})

        except Exception as e:
            return json.dumps({"ok": False, "error": f"recall failed: {e}"})

    recall_tool = StructuredTool.from_function(
        name="recall",
        description=(
            "Look up information from shared memory. "
            "Searches in this order: structured slots → notes → knowledge graph → RAG (fallback). "
            "Use this whenever you need to retrieve stored context before answering. "
            "Slot/note results carry a `relations` list (1-hop edges in the graph) when present — "
            "these are the entities this slot/note is connected to and you MUST include them in "
            "your answer to relational questions ('what does X use?', 'who owns Y?'). "
            "Direct graph matches come back with `source='graph'` and the same `relations` list. "
            "If a result includes `traverse_hint` and the user wants relations 2+ hops away "
            "(e.g. 'what does the project's library depend on?'), call `traverse` next. "
            "Prefer SHORT, CONCRETE queries (an entity name or category like 'spell') over full "
            "sentences. On a miss the result includes `available` (existing slot names, note "
            "titles, graph types) — retry with one of those instead of giving up."
            + (
                " Multiple memory pools are attached; all of them are searched and each "
                "result carries a `pool` field naming its source pool."
                if multi else ""
            )
        ),
        func=_recall_impl,
        args_schema=_RecallInput,
    )

    # -----------------------------------------------------------------------
    # remember — unified write
    # -----------------------------------------------------------------------

    class _RememberInput(BaseModel):
        slot: Optional[str] = Field(None, description="Slot name to store structured data (e.g. 'user_profile', 'api_version')")
        data: Optional[dict] = Field(None, description="Data dict for the slot. For a simple value use {\"value\": \"...\"}.")
        replace: bool = Field(
            False,
            description=(
                "If False (default), merge `data` into the existing slot — keys you pass overwrite "
                "matching keys, untouched keys are preserved. If True, replace the slot entirely."
            ),
        )
        note_title: Optional[str] = Field(None, description="Title of a free-text note to create or update")
        note_content: Optional[str] = Field(None, description="Content of the note")
        link_to_graph: bool = Field(
            True,
            description=(
                "If True (default), also mirror the slot as a graph node so `traverse` can reach it. "
                "Notes are mirrored too (as type='note') only when this is True. "
                "Set False to keep this write off the graph."
            ),
        )

    def _remember_impl(
        slot: Optional[str] = None,
        data: Optional[dict] = None,
        replace: bool = False,
        note_title: Optional[str] = None,
        note_content: Optional[str] = None,
        link_to_graph: bool = True,
    ) -> str:
        saved = []
        errors = []
        try:
            store = MemoryStore()
            mem = store.get(pool_id)
            if not mem:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {pool_id}"})

            if slot is not None:
                if data is None:
                    errors.append("data required when slot is provided")
                else:
                    existing = mem.structured_data.get(slot)
                    existing_keys = list(existing.keys()) if isinstance(existing, dict) else []
                    if replace or not isinstance(existing, dict):
                        merged = dict(data)
                        mode = "replaced" if existing is not None else "created"
                    else:
                        merged = {**existing, **data}
                        mode = "merged"
                    mem.structured_data[slot] = merged
                    saved.append({
                        "type": "structured",
                        "slot": slot,
                        "mode": mode,
                        "keys_before": existing_keys,
                        "keys_after": list(merged.keys()),
                        "added": [k for k in data.keys() if k not in existing_keys],
                        "overwritten": [k for k in data.keys() if k in existing_keys],
                    })

            if note_title is not None:
                if note_content is None:
                    errors.append("note_content required when note_title is provided")
                elif note_title.startswith(JOURNAL_PREFIX):
                    errors.append(f"note_title must not start with '{JOURNAL_PREFIX}' — journal entries are written automatically")
                else:
                    from datetime import datetime, timezone
                    from uuid import uuid4
                    existing_note = next((n for n in mem.notes if n.get("title") == note_title), None)
                    if existing_note:
                        existing_note["content"] = note_content
                    else:
                        mem.notes.append({
                            "id": str(uuid4()),
                            "title": note_title,
                            "content": note_content,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                    saved.append({"type": "note", "title": note_title})

            if not saved and not errors:
                return json.dumps({"ok": False, "error": "Provide at least one of: slot or note_title"})

            if saved:
                _persist(store, mem)

            # Bridge to the graph: mirror slots/notes as nodes for traversal.
            graph_links: list[dict] = []
            if link_to_graph and saved:
                try:
                    from memory.graph import GraphStore
                    gstore = GraphStore(pool_id)
                    for entry in saved:
                        if entry.get("type") == "structured":
                            slot_name = entry["slot"]
                            slot_data = mem.structured_data.get(slot_name) or {}
                            props = {
                                k: v for k, v in slot_data.items()
                                if isinstance(v, (str, int, float, bool)) or v is None
                            }
                            # Attach to an existing same-name entity node when one
                            # exists, instead of creating a parallel ("slot", ...) twin.
                            twin = next((n for n in gstore.find_twins(slot_name) if n.type != "slot"), None)
                            if twin is not None:
                                node, mode = gstore.upsert_node(twin.type, twin.name, props)
                            else:
                                node, mode = gstore.upsert_node("slot", slot_name, props)
                            graph_links.append({"type": node.type, "name": slot_name, "node_id": str(node.id), "mode": mode})
                        elif entry.get("type") == "note":
                            note_name = entry["title"]
                            node, mode = gstore.upsert_node("note", note_name, {})
                            graph_links.append({"type": "note", "name": note_name, "node_id": str(node.id), "mode": mode})
                except Exception as ge:
                    errors.append(f"graph bridge skipped: {ge}")

            return json.dumps({"ok": True, "saved": saved, "errors": errors, "graph_links": graph_links})

        except Exception as e:
            return json.dumps({"ok": False, "error": f"remember failed: {e}"})

    remember_tool = StructuredTool.from_function(
        name="remember",
        description=(
            "Store information in shared memory. "
            "Slot semantics: a `slot` is a NAMED RECORD (a dict) — use slots for structured facts with named fields. "
            "For free-text content (a note, reminder, draft, idea — anything the user calls a 'note') "
            "use note_title+note_content instead of a slot. "
            "If the slot already exists, this call MERGES `data` into it — keys you pass are added/updated, untouched keys are preserved. "
            "Pick the SAME slot name when adding fields to an existing record (e.g. extending the `project` slot with a new `priority` field). "
            "Pick a NEW slot name only for an unrelated fact. "
            "Pass `replace=True` to overwrite the whole slot (rare; use only when previous content is wrong). "
            "Simple values can use {\"value\": \"...\"}. "
            "Both slot and note can be saved in one call. "
            "This tool cannot delete anything — to remove a slot or note, use `forget`. "
            "By default each saved slot/note is also mirrored as a graph node (type='slot' or 'note') "
            "so `link` and `traverse` can reach it. Set `link_to_graph=False` to skip the bridge."
            + (
                f" Multiple memory pools are attached; writes always go to the primary pool ({_pool_name(pool_ids[0])})."
                if multi else ""
            )
        ),
        func=_remember_impl,
        args_schema=_RememberInput,
    )

    # -----------------------------------------------------------------------
    # forget — delete a slot or note (and its graph mirror)
    # -----------------------------------------------------------------------

    class _ForgetInput(BaseModel):
        slot: Optional[str] = Field(None, description="Name of the structured slot to delete")
        note_title: Optional[str] = Field(None, description="Title of the free-text note to delete")
        unlink_graph: bool = Field(
            True,
            description=(
                "If True (default), also delete the graph mirror node (type='slot' or 'note') "
                "created by `remember`, along with its edges. Typed entity nodes created via "
                "`link` are never touched."
            ),
        )

    def _forget_impl(
        slot: Optional[str] = None,
        note_title: Optional[str] = None,
        unlink_graph: bool = True,
    ) -> str:
        deleted = []
        errors = []
        try:
            store = MemoryStore()
            mem = store.get(pool_id)
            if not mem:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {pool_id}"})

            if slot is None and note_title is None:
                return json.dumps({"ok": False, "error": "Provide at least one of: slot or note_title"})

            if slot is not None:
                if slot in mem.structured_data:
                    del mem.structured_data[slot]
                    deleted.append({"type": "structured", "slot": slot})
                else:
                    errors.append(
                        f"Slot '{slot}' not found. Existing slots: "
                        f"{', '.join(mem.structured_data.keys()) or '(none)'}"
                    )

            if note_title is not None:
                if note_title.startswith(JOURNAL_PREFIX):
                    errors.append(
                        f"Notes titled '{JOURNAL_PREFIX}…' are the auto-managed journal and cannot be deleted"
                    )
                else:
                    before = len(mem.notes)
                    mem.notes = [n for n in mem.notes if n.get("title") != note_title]
                    if len(mem.notes) < before:
                        deleted.append({"type": "note", "title": note_title})
                    else:
                        plain = [
                            n["title"] for n in mem.notes
                            if not n.get("title", "").startswith(JOURNAL_PREFIX)
                        ]
                        errors.append(
                            f"Note '{note_title}' not found. Existing notes: "
                            f"{', '.join(plain) or '(none)'}"
                        )

            if deleted:
                _persist(store, mem)

            # Remove the graph mirror nodes (only the 'slot'/'note'-typed twins
            # written by remember's bridge — never typed entity nodes).
            graph_unlinked: list[dict] = []
            if unlink_graph and deleted:
                try:
                    from memory.graph import GraphStore
                    gstore = GraphStore(pool_id)
                    for entry in deleted:
                        if entry["type"] == "structured":
                            node = gstore.get_node("slot", entry["slot"])
                        else:
                            node = gstore.get_node("note", entry["title"])
                        if node is not None and gstore.delete_node(node.id):
                            graph_unlinked.append({"type": node.type, "name": node.name})
                except Exception as ge:
                    errors.append(f"graph unlink skipped: {ge}")

            return json.dumps({
                "ok": bool(deleted),
                "deleted": deleted,
                "errors": errors,
                "graph_unlinked": graph_unlinked,
            })
        except Exception as e:
            return json.dumps({"ok": False, "error": f"forget failed: {e}"})

    forget_tool = StructuredTool.from_function(
        name="forget",
        description=(
            "Delete a structured slot or a free-text note from shared memory. "
            "Pass `slot` to delete a slot, `note_title` to delete a note, or both in one call. "
            "Use when the user asks to remove, delete, or forget stored information, or to "
            "clean up after converting a slot into a note (or vice versa). "
            "The graph mirror node created by `remember` is removed too (set unlink_graph=False to keep it). "
            "Journal notes are auto-managed and cannot be deleted. "
            "Deletion is permanent — when the request is ambiguous, `recall` first to confirm what exists."
            + (
                f" Multiple memory pools are attached; deletes only affect the primary pool ({_pool_name(pool_ids[0])})."
                if multi else ""
            )
        ),
        func=_forget_impl,
        args_schema=_ForgetInput,
    )

    # -----------------------------------------------------------------------
    # record_episode — log a discrete event
    # -----------------------------------------------------------------------

    from typing import List as _List

    class _RecordEpisodeInput(BaseModel):
        kind: str = Field(
            ...,
            description=(
                "One of: 'interaction' (user exchange), 'task' (a task you ran), "
                "'decision' (a non-trivial choice), 'error' (something failed), "
                "'observation' (noteworthy fact you noticed)."
            ),
        )
        summary: str = Field(..., description="One to three sentences describing what happened.")
        actor: Optional[str] = Field(None, description="Who/what acted — 'user', your agent id, or a tool name.")
        subject: Optional[str] = Field(None, description="What the episode was about — task id, file, topic.")
        outcome: Optional[str] = Field(
            None,
            description="One of: 'success', 'failure', 'partial', 'n/a'. Omit if not applicable.",
        )
        tags: Optional[_List[str]] = Field(None, description="Optional list of short tags for later filtering.")
        details: Optional[dict] = Field(None, description="Optional free-form payload (error message, diff, etc).")

    def _record_episode_impl(
        kind: str,
        summary: str,
        actor: Optional[str] = None,
        subject: Optional[str] = None,
        outcome: Optional[str] = None,
        tags: Optional[list] = None,
        details: Optional[dict] = None,
    ) -> str:
        try:
            from memory.episodic import Episode, EpisodeStore
            valid_kinds = {"interaction", "task", "decision", "error", "observation"}
            if kind not in valid_kinds:
                return json.dumps({"ok": False, "error": f"kind must be one of {sorted(valid_kinds)}"})
            valid_outcomes = {"success", "failure", "partial", "n/a"}
            if outcome is not None and outcome not in valid_outcomes:
                return json.dumps({"ok": False, "error": f"outcome must be one of {sorted(valid_outcomes)} or omitted"})

            ep = Episode(
                pool_id=pool_id,
                kind=kind,  # type: ignore[arg-type]
                summary=summary,
                actor=actor,
                subject=subject,
                outcome=outcome,  # type: ignore[arg-type]
                tags=tags or [],
                details=details or {},
            )
            EpisodeStore(pool_id).add(ep)
            return json.dumps({"ok": True, "id": str(ep.id), "kind": ep.kind})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"record_episode failed: {e}"})

    record_episode_tool = StructuredTool.from_function(
        name="record_episode",
        description=(
            "Log a discrete episodic event to shared memory. "
            "Use for things worth remembering as events: notable user interactions, completed/failed tasks, "
            "decisions you made, errors you hit, observations about the work. "
            "Keep `summary` short (1-3 sentences). Episodes are pruned to a small cap, so prefer high-signal kinds "
            "('task', 'decision', 'error') over chatty 'interaction'/'observation' entries."
            + (" Episodes are recorded in the primary pool." if multi else "")
        ),
        func=_record_episode_impl,
        args_schema=_RecordEpisodeInput,
    )

    # -----------------------------------------------------------------------
    # recall_episodes — query past events
    # -----------------------------------------------------------------------

    class _RecallEpisodesInput(BaseModel):
        query: Optional[str] = Field(None, description="Optional keyword query over summary/subject/tags/details.")
        kind: Optional[str] = Field(None, description="Filter by kind: interaction|task|decision|error|observation.")
        outcome: Optional[str] = Field(None, description="Filter by outcome: success|failure|partial|n/a.")
        since: Optional[str] = Field(None, description="ISO timestamp; only return episodes at or after this time.")
        limit: int = Field(10, description="Max number of episodes to return (default 10).")

    def _recall_episodes_impl(
        query: Optional[str] = None,
        kind: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        try:
            from memory.episodic import EpisodeStore
            from datetime import datetime
            since_dt = None
            if since:
                try:
                    since_dt = datetime.fromisoformat(since)
                except Exception:
                    return json.dumps({"ok": False, "error": f"could not parse `since` as ISO datetime: {since!r}"})

            capped = max(1, min(int(limit), 50))
            hits: list[tuple] = []  # (episode, pool_id)
            for pid in pool_ids:
                for e in EpisodeStore(pid).query(
                    query=query,
                    kind=kind,
                    outcome=outcome,
                    since=since_dt,
                    limit=capped,
                ):
                    hits.append((e, pid))
            # Merge across pools by recency, then trim to the requested limit.
            hits.sort(key=lambda t: t[0].occurred_at, reverse=True)
            hits = hits[:capped]
            return json.dumps({
                "ok": True,
                "count": len(hits),
                "episodes": [
                    {
                        "id": str(e.id),
                        "kind": e.kind,
                        "summary": e.summary,
                        "actor": e.actor,
                        "subject": e.subject,
                        "outcome": e.outcome,
                        "tags": e.tags,
                        "details": e.details,
                        "occurred_at": e.occurred_at.isoformat(),
                        **({"pool": _pool_name(pid)} if multi else {}),
                    }
                    for e, pid in hits
                ],
            }, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"recall_episodes failed: {e}"})

    recall_episodes_tool = StructuredTool.from_function(
        name="recall_episodes",
        description=(
            "Query past episodic events from shared memory. "
            "Filter by kind, outcome, or time, and/or rank by a keyword query. "
            "Use to answer 'what happened before?' / 'have I tried this?' / 'what failures have I seen?'"
            + (" All attached pools are searched; each episode carries a `pool` field." if multi else "")
        ),
        func=_recall_episodes_impl,
        args_schema=_RecallEpisodesInput,
    )

    # -----------------------------------------------------------------------
    # link — create or merge an edge between two entities in the knowledge graph
    # -----------------------------------------------------------------------

    class _NodeRef(BaseModel):
        type: str = Field(..., description="Entity type, e.g. 'person', 'project', 'task', 'file', 'concept'.")
        name: str = Field(..., description="Human-readable name; unique per type within this pool.")
        properties: Optional[dict] = Field(None, description="Optional extra fields stored on the node (merged on upsert).")

    class _LinkInput(BaseModel):
        source: _NodeRef = Field(..., description="Source entity. Created if it doesn't exist; properties merged if it does.")
        target: _NodeRef = Field(..., description="Target entity. Same upsert semantics as source.")
        relation: str = Field(..., description="Directed relation, e.g. 'owns', 'depends_on', 'blocks', 'mentions', 'assigned_to'.")
        edge_properties: Optional[dict] = Field(None, description="Optional fields stored on the edge (merged if the edge already exists).")
        weight: Optional[float] = Field(None, description="Optional numeric weight on the edge.")

    def _link_impl(
        source,
        target,
        relation: str,
        edge_properties: Optional[dict] = None,
        weight: Optional[float] = None,
    ) -> str:
        try:
            from memory.graph import GraphStore

            def _coerce(ref) -> dict:
                if hasattr(ref, "model_dump"):
                    return ref.model_dump()
                if isinstance(ref, dict):
                    return ref
                raise ValueError(f"node ref must be dict or _NodeRef, got {type(ref).__name__}")

            src = _coerce(source)
            tgt = _coerce(target)
            store = GraphStore(pool_id)
            src_node, src_mode = store.upsert_node(
                type=src["type"],
                name=src["name"],
                properties=src.get("properties"),
            )
            tgt_node, tgt_mode = store.upsert_node(
                type=tgt["type"],
                name=tgt["name"],
                properties=tgt.get("properties"),
            )
            edge, edge_mode = store.add_edge(
                source_id=src_node.id,
                target_id=tgt_node.id,
                relation=relation,
                properties=edge_properties,
                weight=weight,
            )
            return json.dumps({
                "ok": True,
                "source": {"id": str(src_node.id), "type": src_node.type, "name": src_node.name, "mode": src_mode},
                "target": {"id": str(tgt_node.id), "type": tgt_node.type, "name": tgt_node.name, "mode": tgt_mode},
                "edge": {"id": str(edge.id), "relation": edge.relation, "mode": edge_mode},
            })
        except KeyError as e:
            return json.dumps({"ok": False, "error": f"missing required field: {e}"})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"link failed: {e}"})

    link_tool = StructuredTool.from_function(
        name="link",
        description=(
            "Record a relationship between two entities in the knowledge graph. "
            "Both entities are upserted by (type, name) — created if missing, properties merged if present. "
            "The edge is directed: source -[relation]-> target. "
            "Use for facts that connect things: who owns what, what depends on what, what mentions what. "
            "Pick consistent type and relation names (lowercase, snake_case) — they're matched case-insensitively but stored as given."
            + (" Edges are written to the primary pool's graph." if multi else "")
        ),
        func=_link_impl,
        args_schema=_LinkInput,
    )

    # -----------------------------------------------------------------------
    # traverse — walk the knowledge graph from a starting entity
    # -----------------------------------------------------------------------

    from typing import List as _ListT  # local alias to avoid clash with outer List import

    class _TraverseInput(BaseModel):
        type: str = Field(..., description="Type of the starting entity (e.g. 'person').")
        name: str = Field(..., description="Name of the starting entity (e.g. 'alice').")
        max_depth: int = Field(2, description="How many hops to follow (capped at 3).")
        direction: str = Field("out", description="One of: 'out' (follow outgoing edges), 'in' (incoming), 'both'.")
        relation_filter: Optional[_ListT[str]] = Field(None, description="Only follow edges with these relation names.")
        limit_per_level: int = Field(20, description="Max neighbors expanded per node per level.")

    def _traverse_impl(
        type: str,
        name: str,
        max_depth: int = 2,
        direction: str = "out",
        relation_filter: Optional[list] = None,
        limit_per_level: int = 20,
    ) -> str:
        try:
            from memory.graph import GraphStore
            if direction not in ("out", "in", "both"):
                return json.dumps({"ok": False, "error": "direction must be 'out', 'in', or 'both'"})
            # Start from the first attached pool whose graph has the node;
            # traversal stays within that pool's graph.
            store = None
            start = None
            start_pid = pool_ids[0]
            for pid in pool_ids:
                candidate = GraphStore(pid)
                node = candidate.get_node(type=type, name=name)
                if node is not None:
                    store, start, start_pid = candidate, node, pid
                    break
            if start is None:
                return json.dumps({
                    "ok": False,
                    "error": f"start node not found: type={type!r}, name={name!r}",
                })
            nodes, edges = store.traverse(
                start_node_id=start.id,
                max_depth=max_depth,
                relation_filter=relation_filter,
                direction=direction,  # type: ignore[arg-type]
                limit_per_level=limit_per_level,
            )
            by_id = {str(n.id): n for n in nodes}
            edge_lines = []
            for e in edges:
                src = by_id.get(str(e.source_id))
                tgt = by_id.get(str(e.target_id))
                if src and tgt:
                    edge_lines.append(f"({src.type}:{src.name}) -[{e.relation}]-> ({tgt.type}:{tgt.name})")
            return json.dumps({
                "ok": True,
                "start": {
                    "id": str(start.id), "type": start.type, "name": start.name,
                    **({"pool": _pool_name(start_pid)} if multi else {}),
                },
                "nodes": [
                    {"id": str(n.id), "type": n.type, "name": n.name, "properties": n.properties}
                    for n in nodes
                ],
                "edges": [
                    {
                        "id": str(e.id),
                        "source_id": str(e.source_id),
                        "target_id": str(e.target_id),
                        "relation": e.relation,
                        "properties": e.properties,
                        "weight": e.weight,
                    }
                    for e in edges
                ],
                "rendered": edge_lines,
            }, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"traverse failed: {e}"})

    traverse_tool = StructuredTool.from_function(
        name="traverse",
        description=(
            "Walk the knowledge graph from a starting entity. "
            "Returns visited nodes, traversed edges, and a `rendered` list of `(type:name) -[relation]-> (type:name)` lines for quick scanning. "
            "Use to answer relational questions: 'what does X own?', 'what depends on Y?', 'who is connected to Z?'. "
            "Set direction='in' to follow edges pointing INTO the start node, 'both' to ignore direction. "
            "Filter relation_filter to a subset of relations to keep traversal focused."
        ),
        func=_traverse_impl,
        args_schema=_TraverseInput,
    )

    tools = [
        recall_tool,
        remember_tool,
        forget_tool,
    ]
    if include_episodic_write:
        tools.append(record_episode_tool)
    tools.extend([
        recall_episodes_tool,
        link_tool,
        traverse_tool,
    ])
    return tools


# ---------------------------------------------------------------------------
# Silent auto-journal — called by the chat/run layer, not by the agent
# ---------------------------------------------------------------------------

def silent_journal_append(
    pool_id: str,
    agent_id: str,
    user_message: str,
    response: str,
    *,
    run_id: Optional[str] = None,
) -> None:
    """Append a compact exchange summary to the pool journal without agent involvement.

    Truncates message/response to keep entries scannable. Silently swallows all
    errors so a journal failure never breaks a chat response.
    """
    try:
        from datetime import datetime, timezone
        from uuid import uuid4

        MAX_MSG = 300
        MAX_RESP = 500

        msg_short = user_message.strip()
        if len(msg_short) > MAX_MSG:
            msg_short = msg_short[:MAX_MSG] + "…"
        resp_short = response.strip()
        if len(resp_short) > MAX_RESP:
            resp_short = resp_short[:MAX_RESP] + "…"

        now = datetime.now(timezone.utc)
        title = JOURNAL_PREFIX + now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        parts = [time_str, f"agent:{agent_id}"]
        if run_id:
            parts.append(f"run:{run_id[:8]}")
        header = "[" + " | ".join(parts) + "]"

        entry = f"{header}\n**User:** {msg_short}\n**Response:** {resp_short}"

        store = MemoryStore()
        mem = store.get(pool_id)
        if not mem:
            return

        existing = next((n for n in mem.notes if n.get("title") == title), None)
        if existing:
            existing["content"] = existing["content"] + "\n\n---\n\n" + entry
        else:
            mem.notes.append({
                "id": str(uuid4()),
                "title": title,
                "content": entry,
                "created_at": now.isoformat(),
            })

        _persist(store, mem)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Silent auto-episode helpers — called by the chat/run layer, not by the agent
# ---------------------------------------------------------------------------

def _condense(text: str, max_chars: int) -> str:
    """Return the first sentence (or paragraph) of `text`, capped at max_chars.

    The full text lives in the journal — the episode only needs a scannable lead.
    """
    if not text:
        return ""
    cleaned = " ".join(text.strip().split())  # collapse whitespace + newlines
    if not cleaned:
        return ""
    # Find the first sentence terminator (., !, ?, …) followed by space or end.
    end = -1
    for i, ch in enumerate(cleaned):
        if ch in ".!?…":
            nxt = cleaned[i + 1] if i + 1 < len(cleaned) else " "
            if nxt == " " or i == len(cleaned) - 1:
                end = i + 1
                break
    snippet = cleaned[:end] if end > 0 else cleaned
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rstrip() + "…"
    return snippet


def silent_interaction_episode(
    pool_id: str,
    agent_id: str,
    user_message: str,
    response: str,
    *,
    run_id: Optional[str] = None,
) -> None:
    """Record a one-line summary of an exchange as an episode.

    The full exchange lives in the journal; this episode is a compact, scannable
    pointer for `recall_episodes` queries. Best-effort: errors are swallowed.
    """
    try:
        from memory.episodic import Episode, EpisodeStore

        ask = _condense(user_message, max_chars=140)
        gist = _condense(response, max_chars=180)

        if ask and gist:
            summary = f"Asked: {ask} | Replied: {gist}"
        elif ask:
            summary = f"Asked: {ask}"
        elif gist:
            summary = f"Replied: {gist}"
        else:
            summary = "Empty exchange"

        ep = Episode(
            pool_id=str(pool_id),
            agent_id=agent_id,
            run_id=run_id,
            kind="interaction",
            summary=summary,
            actor="user",
            outcome="n/a",
        )
        EpisodeStore(str(pool_id)).add(ep)
    except Exception:
        pass


def silent_task_episode(
    pool_id: str,
    agent_id: str,
    *,
    task_id: Optional[str] = None,
    run_id: Optional[str] = None,
    status: str,
    exit_code: Optional[int] = None,
    error: Optional[str] = None,
    workspace: Optional[str] = None,
) -> None:
    """Record a 'task' episode at the end of an agent run.

    `status` is the run status from run_manager (completed/failed/stopped/...);
    we map it to an episode outcome. Best-effort, errors are swallowed.
    """
    try:
        from memory.episodic import Episode, EpisodeStore

        outcome_map = {
            "completed": "success",
            "failed": "failure",
            "error": "failure",
            "stopped": "partial",
            "stop": "partial",
        }
        outcome = outcome_map.get(status, "n/a")
        summary_parts = [f"Task run {status}"]
        if exit_code is not None:
            summary_parts.append(f"exit_code={exit_code}")
        if error:
            err_short = error.strip()
            if len(err_short) > 240:
                err_short = err_short[:240] + "…"
            summary_parts.append(f"error: {err_short}")
        summary = "; ".join(summary_parts)

        details: dict = {}
        if exit_code is not None:
            details["exit_code"] = exit_code
        if error:
            details["error"] = error

        ep = Episode(
            pool_id=str(pool_id),
            agent_id=agent_id,
            workspace=workspace,
            run_id=run_id,
            kind="task",
            summary=summary,
            actor=agent_id,
            subject=task_id,
            outcome=outcome,  # type: ignore[arg-type]
            details=details,
        )
        EpisodeStore(str(pool_id)).add(ep)
    except Exception:
        pass
