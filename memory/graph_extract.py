"""LLM-based entity/relation extraction for graph memory.

Single entry point: `extract_and_persist(pool_id, text, ...)` — runs an LLM over
the text and upserts any (entity, relation, entity) triples it finds into the
pool's graph. Best-effort: returns counts and errors, never raises.

Used by the chat layer's `silent_graph_extract` hook. Off by default; agents
or admins can enable it per pool via the `GRAPH_AUTO_EXTRACT` env flag or by
calling `extract_and_persist` directly.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from memory.graph import GraphStore


_EXTRACT_PROMPT = """Extract entities and the relationships between them from the text below.

Return a JSON object with this exact shape and NO other prose:

{
  "triples": [
    {
      "source": {"type": "<entity_type>", "name": "<entity_name>"},
      "relation": "<relation_name>",
      "target": {"type": "<entity_type>", "name": "<entity_name>"}
    }
  ]
}

Rules:
- Use lowercase, snake_case for `type` and `relation` (e.g. "person", "project", "owns", "depends_on", "blocks", "mentions", "assigned_to").
- Use the entity's natural name in `name` (lowercased).
- Only extract relationships that are clearly stated. Do not invent.
- If nothing is extractable, return {"triples": []}.
- Skip vague pronouns ("he", "it") — only include named entities.
- Cap at 12 triples. Pick the highest-signal ones if there are more.

Text:
\"\"\"
{text}
\"\"\"
"""


_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 _.\-/:@]{0,80}$")


def _normalize_token(s: Any) -> Optional[str]:
    if not isinstance(s, str):
        return None
    s = s.strip().lower()
    if not s or len(s) > 80:
        return None
    if not _VALID_NAME_RE.match(s):
        # Allow if it's plausible but tighten very weird strings.
        s = re.sub(r"\s+", " ", s)
        if not _VALID_NAME_RE.match(s):
            return None
    return s


def _parse_triples(raw: str) -> list[dict]:
    """Parse the LLM output. Tolerates surrounding prose / code fences."""
    if not raw:
        return []
    # Robust against reasoning-model output (inline <think> blocks that contain
    # braces, code fences, trailing prose). See memory.json_extract.
    from memory.json_extract import extract_json_object
    obj = extract_json_object(raw)
    if not obj:
        return []
    triples = obj.get("triples")
    if not isinstance(triples, list):
        return []
    out: list[dict] = []
    for t in triples[:32]:
        if not isinstance(t, dict):
            continue
        src = t.get("source") or {}
        tgt = t.get("target") or {}
        rel = t.get("relation")
        if not isinstance(src, dict) or not isinstance(tgt, dict):
            continue
        s_type = _normalize_token(src.get("type"))
        s_name = _normalize_token(src.get("name"))
        t_type = _normalize_token(tgt.get("type"))
        t_name = _normalize_token(tgt.get("name"))
        relation = _normalize_token(rel)
        if not all([s_type, s_name, t_type, t_name, relation]):
            continue
        out.append({
            "source": {"type": s_type, "name": s_name},
            "relation": relation,
            "target": {"type": t_type, "name": t_name},
        })
    return out[:12]


def _resolve_workspace_model(pool_id: str) -> dict:
    """Return model overrides resolved from the pool's workspace, or {}.

    Mirrors the priority chain used by `AgentFactory._resolve_model_config`:
    workspace model_override → workspace settings → empty (caller falls back to .env).
    """
    try:
        from memory.store import MemoryStore
        from workspace import (
            get_workspace_metadata,
            get_workspace_default_model_config,
            get_effective_settings,
        )

        mem = MemoryStore().get(pool_id)
        if not mem or not mem.workspace:
            return {}
        ws_name = mem.workspace
        ws_meta = get_workspace_metadata(ws_name) or {}

        # 1. Explicit model_override
        override = ws_meta.get("model_override") if isinstance(ws_meta, dict) else None
        if isinstance(override, dict):
            op = (override.get("provider") or "").strip()
            if op and op not in ("global", "workspace_default") and override.get("model"):
                resolved = {"provider": op, "model": override["model"]}
                if override.get("base_url"):
                    resolved["base_url"] = override["base_url"]
                return resolved
            if op == "global":
                return {}  # explicit global — let .env win

        # 2. Workspace default model
        ws_default = get_workspace_default_model_config(ws_meta) if isinstance(ws_meta, dict) else {}
        if ws_default.get("provider") and ws_default.get("model"):
            resolved = {"provider": ws_default["provider"], "model": ws_default["model"]}
            # Pull base_url from workspace effective settings when relevant.
            try:
                eff = get_effective_settings(ws_name) or {}
                base_url_field = {
                    "ollama": "ollama_base_url",
                    "lmstudio": "lmstudio_base_url",
                    "openai": "openai_base_url",
                }.get(resolved["provider"])
                if base_url_field and eff.get(base_url_field):
                    resolved["base_url"] = eff[base_url_field]
            except Exception:
                pass
            return resolved
        return {}
    except Exception:
        return {}


def extract_and_persist(
    pool_id: str,
    text: str,
    *,
    max_text_chars: int = 4000,
    model_overrides: Optional[dict] = None,
) -> dict:
    """Extract triples from `text` with an LLM and upsert them into the pool's graph.

    Model resolution priority: caller-supplied overrides → workspace model
    (override → default) → global .env. Returns a summary dict; never raises.
    Skips silently if the text is too short or the LLM returns nothing usable.
    """
    summary = {"ok": True, "triples_found": 0, "triples_persisted": 0, "errors": []}
    try:
        if not text or len(text.strip()) < 40:
            return summary

        # Truncate aggressively — extraction quality drops sharply on long inputs
        # and we want this hook to stay cheap.
        snippet = text.strip()
        if len(snippet) > max_text_chars:
            snippet = snippet[:max_text_chars]

        from agents.agent_utils import build_chat_model
        # Layer overrides: workspace defaults first, caller-supplied on top.
        overrides: dict = {}
        ws_overrides = _resolve_workspace_model(pool_id)
        if ws_overrides:
            overrides.update(ws_overrides)
        if model_overrides:
            overrides.update(model_overrides)
        # Force temperature low; extraction should be deterministic.
        overrides.setdefault("temperature", 0.0)
        overrides.setdefault("streaming", False)
        summary["model_source"] = "workspace" if ws_overrides else "global_env"
        summary["resolved_model"] = {
            "provider": overrides.get("provider"),
            "model": overrides.get("model"),
        }
        try:
            llm = build_chat_model(**overrides)
        except Exception as e:
            summary["ok"] = False
            summary["errors"].append(f"build_chat_model failed: {e}")
            return summary

        prompt = _EXTRACT_PROMPT.replace("{text}", snippet)
        try:
            resp = llm.invoke(prompt)
        except Exception as e:
            summary["ok"] = False
            summary["errors"].append(f"llm.invoke failed: {e}")
            return summary

        raw = getattr(resp, "content", None)
        if isinstance(raw, list):
            # Some providers return content as a list of parts.
            raw = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
        if not isinstance(raw, str):
            raw = str(raw or "")

        triples = _parse_triples(raw)
        summary["triples_found"] = len(triples)
        if not triples:
            return summary

        gstore = GraphStore(pool_id)
        persisted = 0
        for t in triples:
            try:
                src_node, _ = gstore.upsert_node(t["source"]["type"], t["source"]["name"])
                tgt_node, _ = gstore.upsert_node(t["target"]["type"], t["target"]["name"])
                gstore.add_edge(src_node.id, tgt_node.id, t["relation"], properties={"source": "auto_extract"})
                persisted += 1
            except Exception as e:
                summary["errors"].append(f"persist failed for {t}: {e}")
        summary["triples_persisted"] = persisted
        return summary
    except Exception as e:
        summary["ok"] = False
        summary["errors"].append(f"extract_and_persist crashed: {e}")
        return summary


def silent_graph_extract(
    pool_id: str,
    user_message: str,
    response: str,
    *,
    enabled: Optional[bool] = None,
) -> None:
    """Best-effort hook called from the chat layer after each exchange.

    Off by default — set the env var GRAPH_AUTO_EXTRACT=1 (or pass enabled=True)
    to turn it on. The text passed to the LLM is the concatenation of the user
    message and the agent response, capped at 4000 chars total.
    """
    try:
        if enabled is None:
            enabled = os.environ.get("GRAPH_AUTO_EXTRACT", "").strip() in ("1", "true", "yes", "on")
        if not enabled or not pool_id:
            return
        text = f"User said:\n{user_message.strip()}\n\nAgent replied:\n{response.strip()}"
        extract_and_persist(str(pool_id), text)
    except Exception:
        pass
