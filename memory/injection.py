from __future__ import annotations

from typing import Any, Dict


def inject_memory_into_definition(agent_id: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    """Wire memory tools into an agent definition and append a capability hint to its system prompt.

    No pool data is included in the prompt — the agent discovers contents at runtime via tools.
    Structured slot names are listed in the system prompt; data is fetched at runtime via tools.

    Returns the (possibly mutated) definition dict.
    """
    from agents.registry import get_agent as _reg_get
    spec = _reg_get(agent_id)
    if not (spec and spec.memory_type == "shared" and spec.memory_data):
        return definition

    pool_id = str(spec.memory_data)

    # The factory in agent_factory.py replaces the tool list with bound instances.
    # Here we only ensure the logical tool names are present so _create_tools knows
    # to include memory capability, and we build the system prompt hint.
    tool_list = list(definition.get("tools") or [])
    for name in ("recall", "remember", "record_episode", "recall_episodes", "link", "traverse"):
        if name not in tool_list:
            tool_list.append(name)

    from memory.rag_query import is_rag_configured
    has_rag = is_rag_configured()

    lines = [
        "\n\n## Shared Memory",
        "You have access to a shared memory pool. Use these tools in order:",
        "1. Check your current context first — if the answer is already in the conversation, use it directly.",
        "2. Use `recall` to search memory (structured slots → notes → knowledge graph → RAG documents). "
        "When a `recall` result includes a `relations` list, those 1-hop graph edges ARE part of the answer — "
        "include them when the question is about how things are connected.",
        "3. Use `remember` to store new facts, structured records, or notes.",
        "",
        "### How to choose a slot for `remember`",
        "Slots are dict-shaped records keyed by name. Before writing, decide:",
        "- **Add a field to an existing record** → call `remember` with the SAME slot name and only the new field(s) in `data`. The tool merges by default; existing fields are preserved.",
        "  Example: the slot `project` already holds `{name, url}`. To add a priority, call `remember(slot=\"project\", data={\"priority\": \"high\"})` — do NOT create a new slot called `project_priority`.",
        "- **Store a new, unrelated fact** → pick a fresh slot name that doesn't collide with the existing ones listed below.",
        "- **Replace the whole record** → call with `replace=True` (rare; use only when the previous content is wrong).",
        "If unsure which slot a piece of information belongs in, call `recall` with the topic first and inspect the result.",
    ]
    if not has_rag:
        lines.append("(RAG search is not configured — recall searches plain data only.)")

    # Structured slots + journal index — inject names so agent knows what exists
    try:
        from memory.store import MemoryStore
        from memory.tool import JOURNAL_PREFIX, _today_journal_title
        mem = MemoryStore().get(pool_id)
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
        stats = EpisodeStore(pool_id).stats()
        total = stats.get("total", 0)
        by_kind = stats.get("by_kind") or {}
        by_outcome = stats.get("by_outcome") or {}

        lines.append("\n### Episodic memory")
        lines.append(
            "Episodes capture discrete events (kind: interaction|task|decision|error|observation). "
            f"The pool keeps the most recent {MAX_EPISODES_PER_POOL} — interaction/observation are pruned first. "
            "Use `record_episode` to log notable events; use `recall_episodes` to look up past events "
            "(filter by kind/outcome/since, or rank by keyword)."
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
        gstats = GraphStore(pool_id).stats()
        node_count = gstats.get("node_count", 0)
        edge_count = gstats.get("edge_count", 0)
        types = gstats.get("types") or {}
        relations = gstats.get("relations") or {}

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
