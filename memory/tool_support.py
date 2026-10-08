"""Shared helpers for the memory tools: graph search, citations, persistence and journal constants."""
from __future__ import annotations

import logging


from .store import MemoryStore

log = logging.getLogger(__name__)


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
    except Exception:  # noqa: BLE001 - the graph layer is optional, the other layers still answer
        log.debug("graph layer lookup failed", exc_info=True)
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
    except Exception:  # noqa: BLE001 - graph hints are optional, results are returned without them
        log.debug("graph hint enrichment failed", exc_info=True)
        return


def _persist(store: MemoryStore, mem) -> None:
    mem.touch()
    memories = store.load()
    for i, m in enumerate(memories):
        if m.id == mem.id:
            memories[i] = mem
            break
    store.save(memories)



# ---------------------------------------------------------------------------
# Citations: numbering the passages a search hands the model
# ---------------------------------------------------------------------------
#
# ``search_memory`` and ``recall`` record every document passage (layer
# ``rag``) and every note they return on the run's citation sink
# (common/citation_sink.py) and put the number it hands back on the result as
# ``cite``; the tool output then tells the model to cite what it uses as [n].
# The chat renders the sources under the reply, each linked to its workspace
# file or its pool. Blocks, slots and episodes are not numbered: blocks are
# already in the agent's prompt as core memory, and a slot or an episode is
# structured data or a log entry rather than a source a reader can open.
# Outside a chat turn or a task run no sink is installed and nothing changes.

CITE_INSTRUCTION = (
    "Results with a `cite` number are sources. When your answer uses one, put its number "
    "in square brackets right after the statement it supports, like [1]. Cite only the "
    "numbers given here, never invent one."
)


def _rag_payload(hit: dict) -> dict:
    return {
        "source": "rag",
        "text": hit.get("text", ""),
        "file_id": hit.get("file_id", ""),
        "filename": hit.get("filename") or str(hit.get("file_id", "")).rpartition("::")[2],
        "chunk_idx": hit.get("chunk_idx", 0),
        "heading_path": hit.get("heading_path") or [],
        "vector_score": hit.get("score"),
    }


def _cite_results(pool_id: str, mem, results: list[dict]) -> bool:
    """Number every citable result on the current run's citation sink and
    set ``cite`` on it. True when at least one result got a number."""
    from common.citation_sink import record_citation

    workspace_files = {
        str(f.get("filename")): str(f.get("workspace_file_id") or "")
        for f in (getattr(mem, "rag_files", None) or []) if isinstance(f, dict)
    }
    cited = False
    for r in results:
        layer = r.get("layer")
        if layer == "rag":
            filename = str(r.get("filename") or "")
            n = record_citation(
                pool_id=str(pool_id), file_id=str(r.get("file_id") or ""), filename=filename,
                chunk_idx=r.get("chunk_idx", 0), heading_path=r.get("heading_path") or [],
                text=r.get("text", ""), score=r.get("score"),
                workspace_file_id=workspace_files.get(filename, ""), layer="rag",
            )
        elif layer == "note":
            title = str(r.get("title") or "")
            n = record_citation(
                pool_id=str(pool_id), file_id=f"note:{title}", filename=title, chunk_idx=0,
                text=r.get("content", ""), score=r.get("score"), layer="note",
            )
        else:
            continue
        if n is not None:
            r["cite"] = n
            cited = True
    return cited


# ---------------------------------------------------------------------------
# journal — append-only daily log across agent sessions
# ---------------------------------------------------------------------------

JOURNAL_PREFIX = "journal:"

# How many ranked hits one pool contributes to a recall. Enough that a second
# relevant note still comes back, small enough that a large pool cannot fill the
# answer with near-misses.
RECALL_TOP_K = 12


def _today_journal_title() -> str:
    from datetime import datetime, timezone
    return JOURNAL_PREFIX + datetime.now(timezone.utc).strftime("%Y-%m-%d")
