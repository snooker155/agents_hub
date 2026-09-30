"""Graph maintenance helpers for shared memory pools.

The `remember` tool mirrors each slot/note as a graph node so `traverse` can
reach it. When a slot or note is deleted, that mirror must go too — the `forget`
tool and the dashboard delete routes do this inline. This module reconciles the
graph after the fact, removing `slot`/`note` mirror nodes whose backing slot or
note no longer exists (e.g. orphans left by deletes that predate the route fix).

Only `slot`/`note`-typed mirror nodes are ever removed. Typed entity nodes —
including the "twin" case where a slot was mirrored onto an existing entity node
(see memory/tool.py) — are never touched, matching the `forget` tool's contract.
"""
from __future__ import annotations

from typing import Dict, List

from memory.store import MemoryStore
from memory.graph import GraphStore, _normalize

_MIRROR_TYPES = {"slot", "note"}


def prune_orphan_mirrors(pool_id: str, *, dry_run: bool = False) -> Dict:
    """Remove `slot`/`note` mirror nodes with no backing slot/note in the pool.

    Returns a summary dict: {pool_id, removed: [{type, name}], dry_run}. When the
    pool does not exist, returns an `error` field instead of `removed`.
    """
    mem = MemoryStore().get(pool_id)
    if mem is None:
        return {"pool_id": pool_id, "error": "pool not found"}

    slot_keys = {_normalize(k) for k in mem.structured_data.keys()}
    note_titles = {_normalize(n["title"]) for n in mem.notes if n.get("title")}

    gstore = GraphStore(pool_id)
    nodes, _ = gstore.load()

    removed: List[Dict[str, str]] = []
    for node in nodes:
        ntype = _normalize(node.type)
        if ntype not in _MIRROR_TYPES:
            continue
        backing = slot_keys if ntype == "slot" else note_titles
        if _normalize(node.name) in backing:
            continue
        if dry_run or gstore.delete_node(node.id):
            removed.append({"type": node.type, "name": node.name})

    return {"pool_id": pool_id, "removed": removed, "dry_run": dry_run}


def prune_all_pools(*, dry_run: bool = False) -> List[Dict]:
    """Run `prune_orphan_mirrors` across every shared memory pool."""
    return [
        prune_orphan_mirrors(str(mem.id), dry_run=dry_run)
        for mem in MemoryStore().load()
    ]
