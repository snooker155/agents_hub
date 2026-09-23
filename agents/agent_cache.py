"""
Content-addressed agent build cache.

Building an agent (assembling the layered prompt, instantiating tools, and
constructing the LangChain chat model + executor) is repeated on every task run,
node poll and chat message. This cache reuses a built agent across runs the same
way a Docker build reuses layers: the agent is keyed by a *fingerprint* of its
definition inputs, and reused while that fingerprint is unchanged. Editing the
agent — its instructions, tools, memory binding, model, or the workspace
instructions/model settings that feed it — changes the fingerprint and rebuilds
automatically.

What the fingerprint covers (a change to any of these rebuilds):
- the three markdown files (``instructions.md`` / ``capabilities.md`` / ``usage.md``)
- the registry file ``agents.json`` (tools, model, provider, reasoning, memory
  binding, skills, response format, clarify gate, temperature, max tokens …)
- the workspace metadata store (workspace instructions, model override, settings)
- the procedures store (skills catalog, when the agent has skills enabled)
- the global ``.env`` (default provider/model)
- the target workspace, the build override params, and project-scope

The key (not only the fingerprint) carries the build overrides, and that is
where an A/B experiment arm lands: the factory adds ``definition_version`` to
the overrides of a run routed to an arm (``evals/experiments.py``), so each
stored version gets its own cache entry and two arms never share a build.

What it deliberately does NOT cover: live memory *contents*. The memory section
injected into the prompt is only a set of hints ("these slots/notes exist") —
the agent reads authoritative values through its tools at runtime, so a slightly
stale hint list never causes incorrect behaviour. The ``agent_cache_ttl`` bounds
how stale those hints (and the skills catalog) can get on a long-lived process.

Reusing a built agent across concurrent runs is safe: ``StandardAgent.run``
creates its per-run guards and callbacks fresh each call and the LangChain
executor holds no per-invocation state on ``self`` — only the stateless model
client, tools and prompt are shared.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

# Bounded LRU so many workspaces/agents can't grow the cache without limit.
_MAX_ENTRIES = 64

_lock = threading.RLock()
# key -> (fingerprint, agent, built_at_monotonic)
_cache: "OrderedDict[str, Tuple[str, Any, float]]" = OrderedDict()
_hits = 0
_misses = 0


def _now() -> float:
    return time.monotonic()


def _stat_sig(path: Path) -> str:
    """Cheap change signature for a file: mtime + size, or '-' if absent."""
    try:
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except Exception:
        return "-"


def _key(agent_id: str, workspace: Optional[str], override_params: Dict[str, Any]) -> str:
    return f"{agent_id}\x1f{workspace or ''}\x1f{_overrides_repr(override_params)}"


def _overrides_repr(override_params: Dict[str, Any]) -> str:
    try:
        return repr(sorted((str(k), repr(v)) for k, v in (override_params or {}).items()))
    except Exception:
        return repr(override_params)


def compute_fingerprint(
    agent_id: str,
    workspace: Optional[str],
    override_params: Dict[str, Any],
    *,
    definitions_dir: Path,
) -> str:
    """Hash the definition inputs that determine the built agent."""
    from common.docstore import DocStore
    from common.paths import PROJECT_ROOT
    from common.workspace_context import resolve_active_project

    defn = Path(definitions_dir) / agent_id
    parts = [
        "v1",
        agent_id,
        str(workspace or ""),
        _overrides_repr(override_params),
        "proj:" + ("1" if resolve_active_project() else "0"),
    ]
    for fname in ("instructions.md", "capabilities.md", "usage.md"):
        parts.append(f"{fname}={_stat_sig(defn / fname)}")
    parts.append("agents=" + DocStore("agents").signature())
    parts.append("ws=" + DocStore("workspaces").signature())
    parts.append("proc=" + DocStore("procedures").signature())
    parts.append("env=" + _stat_sig(Path(PROJECT_ROOT) / ".env"))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def get_or_build(
    agent_id: str,
    workspace: Optional[str],
    override_params: Dict[str, Any],
    *,
    definitions_dir: Path,
    builder: Callable[[], Any],
) -> Any:
    """Return a cached agent when its fingerprint (and TTL) still hold, else build.

    ``builder`` is the expensive full build; it runs outside the lock so a slow
    build never blocks concurrent lookups for other agents.
    """
    global _hits, _misses
    from common.config import settings

    if not settings.agent_cache_enabled:
        return builder()

    ttl = int(getattr(settings, "agent_cache_ttl", 0) or 0)
    fp = compute_fingerprint(agent_id, workspace, override_params, definitions_dir=definitions_dir)
    key = _key(agent_id, workspace, override_params)

    with _lock:
        entry = _cache.get(key)
        if entry is not None:
            cached_fp, agent, built_at = entry
            fresh = ttl <= 0 or (_now() - built_at) < ttl
            if cached_fp == fp and fresh:
                _cache.move_to_end(key)
                _hits += 1
                return agent

    agent = builder()

    with _lock:
        _cache[key] = (fp, agent, _now())
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)
        _misses += 1
    return agent


def invalidate(agent_id: Optional[str] = None) -> int:
    """Drop cached builds. All agents when ``agent_id`` is None, else just that
    agent (every workspace/override variant). Returns entries removed."""
    with _lock:
        if agent_id is None:
            n = len(_cache)
            _cache.clear()
            return n
        prefix = f"{agent_id}\x1f"
        doomed = [k for k in _cache if k.startswith(prefix)]
        for k in doomed:
            _cache.pop(k, None)
        return len(doomed)


def cache_stats() -> Dict[str, int]:
    """Hit/miss counters and current size, for the health endpoint."""
    with _lock:
        return {"entries": len(_cache), "hits": _hits, "misses": _misses}
