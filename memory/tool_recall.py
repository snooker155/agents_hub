"""Tool builders for the recall tool, bound to one memory binding."""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from .store import MemoryStore

log = logging.getLogger(__name__)
from .tool_support import (
    CITE_INSTRUCTION, JOURNAL_PREFIX, RECALL_TOP_K, _annotate_with_graph_hints, _cite_results,
    _rag_payload, _search_graph,
)


def build_recall_tool(ctx):
    multi = ctx.multi
    pool_ids = ctx.pool_ids

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
        """Rank every layer of every attached pool for `query`.

        Blocks, slots, notes and episodes are ranked together with BM25; when a
        vector store answers, its hits join the same ranking through reciprocal
        rank fusion (see memory/ranking.py). Each result carries the layer it
        came from and its score, so the caller can see why it is where it is.
        """
        try:
            from memory.ranking import Candidate, pool_candidates, rank_candidates

            store = MemoryStore()

            # The trace records what each layer contributed so callers (and the
            # chat UI) can show WHERE each piece of data came from. With several
            # pools the counts are aggregated; each result carries a `pool`
            # field for provenance instead.
            counts = {"block": 0, "structured": 0, "note": 0, "episode": 0, "rag": 0}
            searched = {"block": 0, "structured": 0, "note": 0, "episode": 0}

            results: list[dict] = []
            cited = False            # some result got a citation number
            found_pools: list = []   # (pid, mem) for pool ids that resolved
            missing: list[str] = []
            graph_hits = 0

            try:
                from memory.rag_query import is_rag_configured
                rag_on = is_rag_configured()
            except Exception:  # noqa: BLE001 - RAG is an optional layer, treat a failed probe as off
                log.debug("rag configuration probe failed", exc_info=True)
                rag_on = False

            for pid in pool_ids:
                mem = store.get(pid)
                if not mem:
                    missing.append(pid)
                    continue
                found_pools.append((pid, mem))

                candidates = pool_candidates(mem, pool_id=str(pid))
                for cand in candidates:
                    src = "structured" if cand.layer == "slot" else cand.layer
                    searched[src] = searched.get(src, 0) + 1

                # Indexed passages join the same ranking: the passage
                # retriever's own order (BM25 over the chunk store, fused with
                # vector similarity when a vector store is configured) is the
                # second ranked list fused with BM25 over the pool's items.
                vector_keys: list[str] = []
                try:
                    from memory.rag_query import search_rag
                    for hit in search_rag(query, str(pid), top_k=5):
                        key = f"{pid}:rag:{hit.get('file_id', '')}:{hit.get('chunk_idx', 0)}"
                        candidates.append(Candidate(
                            key=key, layer="rag", text=hit.get("text", ""), payload=_rag_payload(hit),
                        ))
                        vector_keys.append(key)
                except Exception:  # noqa: BLE001 - the passage search is one layer; the others still answer
                    log.debug("passage search failed", exc_info=True)

                ranked = rank_candidates(query, candidates, vector_keys=vector_keys)
                pool_results: list[dict] = []
                for cand, score in ranked[:RECALL_TOP_K]:
                    entry = {**cand.payload, "layer": cand.layer, "score": score}
                    pool_results.append(entry)
                    src = "structured" if cand.layer == "slot" else cand.layer
                    counts[src] = counts.get(src, 0) + 1

                # Decorate slot/note results with graph hints so the agent knows
                # when to follow relations with `traverse`.
                _annotate_with_graph_hints(pid, pool_results)
                if _cite_results(str(pid), mem, pool_results):
                    cited = True

                # Graph search — keyword match over node type/name/properties.
                # Skip nodes already surfaced above to avoid duplicating an entity.
                already = {("slot", str(r["slot"]).strip().lower()) for r in pool_results if r.get("source") == "structured"}
                already |= {("note", str(r["title"]).strip().lower()) for r in pool_results if r.get("source") == "note"}
                graph_results = _search_graph(pid, query, exclude=already, limit=8)
                if graph_results:
                    for g in graph_results:
                        g["layer"] = "graph"
                    pool_results.extend(graph_results)
                graph_hits += len(graph_results)

                if multi:
                    for r in pool_results:
                        r["pool"] = mem.name
                results.extend(pool_results)

            if not found_pools:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {', '.join(missing or pool_ids)}"})

            # Highest score first across all pools.
            results.sort(key=lambda r: r.get("score", 0.0), reverse=True)

            trace: list[dict] = [
                {"layer": "blocks", "searched": searched.get("block", 0), "hits": counts["block"]},
                {"layer": "structured_slots", "searched": searched.get("structured", 0), "hits": counts["structured"]},
                {"layer": "notes", "searched": searched.get("note", 0), "hits": counts["note"]},
                {"layer": "episodes", "searched": searched.get("episode", 0), "hits": counts["episode"]},
                {"layer": "graph", "hits": graph_hits},
            ]
            # Indexed passages are always searched (BM25 over the chunk store);
            # ``vector`` says whether vector similarity took part.
            trace.append({"layer": "rag", "hits": counts["rag"], "vector": rag_on})

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
                    block_names: list[str] = []
                    graph_types: set = set()
                    for pid, mem in found_pools:
                        slot_names.extend(mem.structured_data.keys())
                        note_titles.extend(n["title"] for n in mem.notes if not n.get("title", "").startswith(JOURNAL_PREFIX))
                        block_names.extend(b.name for b in (mem.blocks or []))
                        from memory.graph import GraphStore
                        gstats = GraphStore(pid).stats()
                        graph_types.update((gstats.get("types") or {}).keys())
                    if block_names:
                        available["blocks"] = sorted(set(block_names))
                    if slot_names:
                        available["slots"] = slot_names[:30]
                    if note_titles:
                        available["notes"] = note_titles[:20]
                    if graph_types:
                        available["graph_types"] = sorted(graph_types)
                except Exception:  # noqa: BLE001 - the available-items hint is optional
                    log.debug("available-items hint skipped", exc_info=True)
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

            if cited:
                base["citations"] = CITE_INSTRUCTION
            return json.dumps({**base, "found": True, "results": results}, default=str)

        except Exception as e:  # noqa: BLE001 - the tool returns the error to the model as a string
            return json.dumps({"ok": False, "error": f"recall failed: {e}"})

    recall_tool = StructuredTool.from_function(
        name="recall",
        description=(
            "Look up information from shared memory. "
            "Ranks core memory blocks, structured slots, notes and episodes together (BM25, fused "
            "with vector similarity when a vector store is configured), then adds knowledge-graph "
            "matches; every result carries the `layer` it came from and a `score`. "
            "Passages of the pool's indexed documents are ranked alongside, and a passage or a "
            "note may carry a `cite` number to quote as [n] in the answer. "
            "The core memory blocks are already in your system prompt, so a block in the results "
            "is a confirmation, not news. "
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

    return recall_tool
