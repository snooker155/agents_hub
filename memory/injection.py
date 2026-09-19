from __future__ import annotations

from typing import Any, Dict


def inject_memory_into_definition(agent_id: str, definition: Dict[str, Any], workspace: str | None = None, episodic_write: bool = True, pool_override: Any = None) -> Dict[str, Any]:
    """Wire memory tools into an agent definition and append a capability hint to its system prompt.

    No pool data is included in the prompt — the agent discovers contents at runtime via tools.
    Structured slot names are listed in the system prompt; data is fetched at runtime via tools.

    The pool assignment is resolved for ``workspace`` — agent records shared
    across workspaces carry a separate assignment per workspace. ``pool_override``
    pins it for this build instead (see ``memory.binding``).

    Returns the (possibly mutated) definition dict.
    """
    from agents.registry import get_agent as _reg_get
    from memory.binding import effective_memory_pools
    spec = _reg_get(agent_id)
    if not spec:
        return definition

    tool_list = list(definition.get("tools") or [])
    _EXTRACTION_TOOLS = {"extract_from_text", "save_extraction", "extract_to_memory"}
    is_extraction_agent = "extract_from_text" in tool_list or "extract_to_memory" in tool_list
    # A PURE extraction agent declares only extraction tools — it gets the compact
    # two-step pipeline and nothing else. A HYBRID agent (extraction + other tools,
    # e.g. a general assistant) also needs the conversational recall/remember/forget
    # set so it can read, write, and DELETE memory like any other agent.
    is_pure_extraction = is_extraction_agent and not [
        t for t in tool_list if t not in _EXTRACTION_TOOLS
    ]

    pools = effective_memory_pools(spec, workspace, pool_override)
    if not pools:
        # An extraction agent without a pool gets an explicit notice instead of
        # a silent degradation — its instructions promise tools that won't bind.
        if is_extraction_agent:
            return _inject_no_pool_notice(definition)
        return definition

    # Primary pool first — writes go there; the rest are read-only context.
    pool_id = pools[0]
    extra_pools = pools[1:]

    # Extraction agents declare the extraction tools explicitly: they get the
    # dedicated two-step pipeline (extract_from_text → save_extraction). A PURE
    # extraction agent stops here with a compact prompt section. A HYBRID agent
    # keeps the extraction tools AND falls through to receive the full
    # conversational recall/remember/forget set below.
    if is_extraction_agent:
        for name in ("extract_from_text", "save_extraction"):
            if name not in tool_list:
                tool_list.append(name)
        tool_list = [t for t in tool_list if t != "extract_to_memory"]
        definition["tools"] = tool_list
        if is_pure_extraction:
            return _inject_extraction_context(pool_id, definition)
        # Hybrid: append the extraction prompt section now, then continue to add
        # the conversational tools and their prompt section.
        definition = _inject_extraction_context(pool_id, definition)

    # The factory in agent_factory.py replaces the tool list with bound instances.
    # Here we only ensure the logical tool names are present so _create_tools knows
    # to include memory capability, and we build the system prompt hint.
    # The episodic WRITE tool is gated per agent (caller passes the effective
    # decision — see resolve_episodic_write). When disabled, never attach
    # record_episode (and strip it if a definition declared it explicitly) so the
    # model is not tempted to call a tool it doesn't have.
    _mem_names = ["recall", "remember", "forget", "recall_episodes", "link", "traverse"]
    if episodic_write:
        _mem_names.insert(3, "record_episode")
    else:
        tool_list = [t for t in tool_list if t != "record_episode"]
    for name in _mem_names:
        if name not in tool_list:
            tool_list.append(name)

    from memory.rag_query import is_rag_configured
    has_rag = is_rag_configured()

    _write_list = "`remember`, `record_episode`, `link`" if episodic_write else "`remember`, `link`"
    intro = (
        "You have access to a shared memory pool. Use these tools in order:"
        if not extra_pools else
        f"You have access to {len(pools)} shared memory pools. The first one is the PRIMARY pool — "
        f"all writes ({_write_list}) go there. The other pools are read-only "
        "context that `recall`, `recall_episodes`, and `traverse` also search. Use these tools in order:"
    )
    lines = [
        "\n\n## Shared Memory",
        intro,
        "1. Check your current context first — if the answer is already in the conversation, use it directly.",
        "2. Use `recall` to search memory (structured slots → notes → knowledge graph → RAG documents). "
        "When a `recall` result includes a `relations` list, those 1-hop graph edges ARE part of the answer — "
        "include them when the question is about how things are connected.",
        "3. Use `remember` to store new facts, structured records, or notes.",
        "4. Use `forget` to delete a slot or note the user asks you to remove. `remember` cannot delete — "
        "never claim something was removed without a successful `forget` call.",
        "",
        "### Slot or note?",
        "Slots are dict-shaped records keyed by name — use them for structured facts with named fields. "
        "When the user asks for a NOTE (free text: an idea, draft, reminder, description), save it with "
        "`note_title`+`note_content`, NOT as a slot.",
        "",
        "### How to choose a slot for `remember`",
        "Before writing a slot, decide:",
        "- **Add a field to an existing record** → call `remember` with the SAME slot name and only the new field(s) in `data`. The tool merges by default; existing fields are preserved.",
        "  Example: the slot `project` already holds `{name, url}`. To add a priority, call `remember(slot=\"project\", data={\"priority\": \"high\"})` — do NOT create a new slot called `project_priority`.",
        "- **Store a new, unrelated fact** → pick a fresh slot name that doesn't collide with the existing ones listed below.",
        "- **Replace the whole record** → call with `replace=True` (rare; use only when the previous content is wrong).",
        "- **Remove a record** → call `forget` with the slot name (or note title).",
        "If unsure which slot a piece of information belongs in, call `recall` with the topic first and inspect the result.",
    ]
    if not has_rag:
        lines.append("(RAG search is not configured — recall searches plain data only.)")

    # Per-pool snapshots: structured slots + journal index, episode stats, and
    # graph stats — so the agent knows what exists without a blind recall.
    # Capability explanations (episodic/graph) are emitted once, with the
    # primary pool; extra pools get a header and their stat lines only.
    for idx, pid in enumerate(pools):
        is_primary = idx == 0
        if extra_pools:
            try:
                from memory.store import MemoryStore
                _pmem = MemoryStore().get(pid)
                _pname = _pmem.name if _pmem else pid
            except Exception:
                _pname = pid
            role = "PRIMARY — writes go here" if is_primary else "read-only context"
            lines.append(f"\n### Pool: {_pname} ({role})")

        # Structured slots + journal index — inject names so agent knows what exists
        try:
            from memory.store import MemoryStore
            from memory.tool import JOURNAL_PREFIX, _today_journal_title
            mem = MemoryStore().get(pid)
            if mem:
                if mem.structured_data:
                    lines.append("\n**Existing structured slots** — re-use these names to extend; pick a different name only for unrelated facts:")
                    for slot, data in mem.structured_data.items():
                        if isinstance(data, dict):
                            keys = ", ".join(f"`{k}`" for k in list(data.keys())[:8])
                            more = "" if len(data) <= 8 else f" …(+{len(data) - 8} more)"
                            lines.append(f"- `{slot}` — fields: {keys}{more}")
                        else:
                            lines.append(f"- `{slot}`")

                plain_notes = [n["title"] for n in mem.notes if not n.get("title", "").startswith(JOURNAL_PREFIX)]
                if plain_notes:
                    lines.append(f"\n**Notes:** {', '.join(f'`{t}`' for t in plain_notes)}")

                journal_titles = sorted(
                    [n["title"] for n in mem.notes if n.get("title", "").startswith(JOURNAL_PREFIX)],
                    reverse=True,
                )
                if journal_titles:
                    today = _today_journal_title()
                    lines.append(f"\n**Journal** ({len(journal_titles)} entries — use `recall` with the date title to read):")
                    for title in journal_titles[:7]:
                        marker = " ← today" if title == today else ""
                        lines.append(f"- `{title}`{marker}")
        except Exception:
            pass

        # Episodic memory — capability hint + lightweight stats so the agent knows
        # what's already been recorded and when to use record_episode/recall_episodes.
        try:
            from memory.episodic import EpisodeStore, MAX_EPISODES_PER_POOL
            stats = EpisodeStore(pid).stats()
            total = stats.get("total", 0)
            by_kind = stats.get("by_kind") or {}
            by_outcome = stats.get("by_outcome") or {}

            if is_primary:
                lines.append("\n### Episodic memory")
                _episodic_write_hint = (
                    "Use `record_episode` to log notable events; use `recall_episodes` to look up past events "
                    if episodic_write else
                    "Use `recall_episodes` to look up past events "
                )
                lines.append(
                    "Episodes capture discrete events (kind: interaction|task|decision|error|observation). "
                    f"The pool keeps the most recent {MAX_EPISODES_PER_POOL} — interaction/observation are pruned first. "
                    + _episodic_write_hint
                    + "(filter by kind/outcome/since, or rank by keyword)."
                )
            if total:
                kind_str = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())) or "—"
                outcome_str = ", ".join(f"{k}={v}" for k, v in sorted(by_outcome.items())) or "—"
                lines.append(f"**Episodes:** {total} total — kinds: {kind_str}; outcomes: {outcome_str}")
            else:
                lines.append("**Episodes:** none yet.")
        except Exception:
            pass

        # Graph memory — capability hint + lightweight stats so the agent knows
        # which entity types and relations already exist (encourages consistent naming).
        try:
            from memory.graph import GraphStore
            gstats = GraphStore(pid).stats()
            node_count = gstats.get("node_count", 0)
            edge_count = gstats.get("edge_count", 0)
            types = gstats.get("types") or {}
            relations = gstats.get("relations") or {}

            if is_primary:
                lines.append("\n### Graph memory")
                lines.append(
                    "The graph stores entities (nodes with type+name) and directed relationships (edges with a relation name). "
                    "Use `link` to record a relationship between two entities — both are upserted by (type, name). "
                    "Use `traverse` to walk from a starting entity and answer questions like "
                    "'what does X own?' or 'what depends on Y?'. "
                    "Re-use existing type and relation names below to keep the graph consistent."
                )
            if node_count or edge_count:
                top_types = sorted(types.items(), key=lambda kv: -kv[1])[:6]
                top_rels = sorted(relations.items(), key=lambda kv: -kv[1])[:6]
                type_str = ", ".join(f"{k}={v}" for k, v in top_types) or "—"
                rel_str = ", ".join(f"{k}={v}" for k, v in top_rels) or "—"
                lines.append(f"**Graph:** {node_count} nodes, {edge_count} edges — top types: {type_str}; top relations: {rel_str}")
            else:
                lines.append("**Graph:** empty.")
        except Exception:
            pass

    definition["system_prompt"] = (definition.get("system_prompt") or "") + "\n".join(lines)
    definition["tools"] = tool_list

    return definition


def _inject_no_pool_notice(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Prompt notice for extraction agents whose record has no memory pool attached."""
    notice = (
        "\n\n## Shared Memory (extraction mode) — NO POOL ATTACHED\n"
        "No shared memory pool is attached to this agent, so the extraction tools "
        "(`extract_from_text`, `save_extraction`) are NOT available in this session and "
        "you cannot extract or store anything. If the operator asks you to extract, do not "
        "attempt any tool call and never claim something was saved. Explain the situation "
        "and give the remedy: attach a shared memory pool in the dashboard (Agent settings "
        "→ Memory), then send the request again."
    )
    definition["system_prompt"] = (definition.get("system_prompt") or "") + notice
    return definition


def _inject_extraction_context(pool_id: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    """Prompt section for extraction agents (those with the two-step extraction tools).

    The extraction pipeline builds its own merge context internally; this section
    only gives the agent a snapshot of what the pool holds so it can steer the
    tool (focus hints, chunking) and write an accurate report.
    """
    lines = [
        "\n\n## Shared Memory (extraction mode)",
        "You are bound to a shared memory pool through a two-step extraction pipeline: "
        "`extract_from_text` produces a reviewable proposal (nothing is stored yet) and "
        "returns an `extraction_id`; `save_extraction` persists that exact proposal. "
        "The pipeline already knows the pool's existing slots, notes, and graph vocabulary "
        "and merges into them. Your job is to feed it well-chosen text chunks (and optional "
        "`focus` hints), present proposals for review, and save what is approved.",
    ]
    try:
        from memory.store import MemoryStore
        from memory.tool import JOURNAL_PREFIX

        mem = MemoryStore().get(pool_id)
        if mem:
            if mem.structured_data:
                slot_names = ", ".join(f"`{s}`" for s in list(mem.structured_data.keys())[:20])
                lines.append(f"\n**Existing slots:** {slot_names}")
            titles = [n["title"] for n in mem.notes if not n.get("title", "").startswith(JOURNAL_PREFIX)]
            if titles:
                lines.append(f"**Existing notes:** {', '.join(f'`{t}`' for t in titles[:20])}")
            if not mem.structured_data and not titles:
                lines.append("\n**The pool is currently empty.**")
    except Exception:
        pass
    try:
        from memory.episodic import EpisodeStore
        from memory.graph import GraphStore

        estats = EpisodeStore(pool_id).stats()
        gstats = GraphStore(pool_id).stats()
        lines.append(
            f"**Episodes:** {estats.get('total', 0)} stored (cap {estats.get('cap')}). "
            f"**Graph:** {gstats.get('node_count', 0)} nodes, {gstats.get('edge_count', 0)} edges."
        )
    except Exception:
        pass

    definition["system_prompt"] = (definition.get("system_prompt") or "") + "\n".join(lines)
    return definition
