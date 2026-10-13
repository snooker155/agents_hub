"""
The node pool's scheduler: which node answers a request sent to the pool
(docs/hub-link.md, "The pool").

A request names what it needs (a model id, or a sandbox) and whose work it
is (a workspace, perhaps an agent). :func:`eligible` lists the nodes that
may take it and says why each other node may not: disabled, offline, no
such model, no sandbox, not allowed for the workspace, or not trusted
enough for the work's data class (common/data_class.py). :func:`dispatch`
then hands the request to the least busy of them:

* **load** is a node's open jobs (waiting, taken, streaming) over its
  capacity, read from the mailbox, so every API replica sees the same;
* a node at capacity gets nothing new; when every eligible node is full the
  request waits up to :data:`QUEUE_SECONDS` for a slot;
* a node that does not take the job within :data:`PICK_SECONDS` (asleep,
  network gone, its loop stuck) loses it to the next node. Only a job
  nobody took moves: once a node has it, it runs there, so a request never
  runs twice.

Independent requests therefore spread over the nodes on their own: a
flow's parallel branches, an agent's delegated subtasks, the cases of an
eval, ``map_on_nodes`` (tools/node_map.py), each on whichever node is
freest when it is sent.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from common import data_class
from hublink import jobs, nodes

log = logging.getLogger(__name__)

#: How long a node has to take a job before it goes to another.
PICK_SECONDS = 8.0
#: How long a request waits for a free slot when every eligible node is full.
QUEUE_SECONDS = 120.0
QUEUE_POLL_SECONDS = 0.25


@dataclass
class Need:
    """What a request needs and whose work it is."""
    model: Optional[str] = None
    sandbox: bool = False
    workspace: str = "default"
    agent: Optional[str] = None
    data_class: str = data_class.DEFAULT_CLASS


#: Kinds of reason a node is left out: it may come back (``away``), it lacks
#: what the request needs (``missing``), or the rules keep the work from it
#: (``policy``).
AWAY = "away"
MISSING = "missing"
POLICY = "policy"


@dataclass
class Candidates:
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    #: ``{"id", "name", "kind", "reason"}`` for every node left out.
    excluded: List[Dict[str, str]] = field(default_factory=list)


def need_for(*, workspace: Optional[str], agent: Optional[str] = None, model: Optional[str] = None,
             sandbox: bool = False) -> Need:
    ws = (workspace or "").strip() or "default"
    return Need(model=model, sandbox=sandbox, workspace=ws, agent=agent or None,
                data_class=data_class.effective_class(ws, agent))


def why_not(record: Dict[str, Any], need: Need) -> Optional[Tuple[str, str]]:
    """``(kind, reason)`` when ``record`` may not take a request with
    ``need``, else None."""
    if need.model and need.model not in {str(m.get("id")) for m in record.get("models") or []
                                         if isinstance(m, dict)}:
        return MISSING, f"does not share {need.model}"
    if need.sandbox and not (record.get("capabilities") or {}).get("sandbox"):
        return MISSING, "offers no sandbox"
    if not nodes.allows_workspace(record, need.workspace):
        return POLICY, f"not allowed for workspace '{need.workspace}'"
    trust = nodes.trust(record)
    if not data_class.trust_allows(trust, need.data_class):
        return POLICY, data_class.refusal(str(record.get("name") or record.get("id")), trust, need.data_class)
    if record.get("disabled"):
        return AWAY, "disabled"
    if not nodes.is_online(record):
        return AWAY, "offline"
    return None


def eligible(need: Need, *, skip: Optional[Set[str]] = None,
             load: Optional[Dict[str, int]] = None) -> Candidates:
    """The nodes that may take the request, least loaded first."""
    load = jobs.in_flight() if load is None else load
    out = Candidates()
    for rec in nodes.list_nodes():
        if skip and rec["id"] in skip:
            out.excluded.append({"id": rec["id"], "name": str(rec.get("name") or ""), "kind": AWAY,
                                 "reason": "did not pick up a request just now"})
            continue
        found = why_not(rec, need)
        if found:
            out.excluded.append({"id": rec["id"], "name": str(rec.get("name") or ""),
                                 "kind": found[0], "reason": found[1]})
            continue
        out.nodes.append(rec)
    # Shuffle first so equal loads do not always land on the oldest node.
    random.shuffle(out.nodes)
    out.nodes.sort(key=lambda r: (load.get(r["id"], 0) / nodes.capacity(r), load.get(r["id"], 0)))
    return out


def _no_node_message(need: Need, cands: Candidates) -> str:
    what = f"model {need.model}" if need.model else "a sandbox"
    if not cands.excluded:
        return f"no node offers {what}"
    reasons = "; ".join(f"{e['name'] or e['id']}: {e['reason']}" for e in cands.excluded[:6])
    return (f"no node may take this {need.data_class} work in workspace '{need.workspace}' "
            f"({what}). {reasons}")


def _no_node_status(cands: Candidates) -> int:
    """503 when a node that is away might come back, 403 when the rules keep
    the work from every node that has what it needs, 404 when none has it."""
    kinds = {e["kind"] for e in cands.excluded}
    if AWAY in kinds:
        return 503
    if POLICY in kinds:
        return 403
    return 404


class NoNode(RuntimeError):
    """No node may take the request; ``status`` is what a route answers."""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


def _pick(need: Need, tried: Set[str]) -> Tuple[Optional[Dict[str, Any]], Candidates]:
    load = jobs.in_flight()
    cands = eligible(need, skip=tried, load=load)
    free = [r for r in cands.nodes if load.get(r["id"], 0) < nodes.capacity(r)]
    return (free[0] if free else None), cands


async def dispatch(need: Need, *, method: str, path: str, headers: Dict[str, str], body: bytes,
                   timeout: float) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Hand the request to the least busy eligible node. Returns the node's
    record, the job id (the caller streams its body and discards it) and the
    response head. Raises :class:`NoNode` or :class:`jobs.JobFailed`."""
    tried: Set[str] = set()
    queue_deadline = time.monotonic() + QUEUE_SECONDS
    while True:
        pick, cands = await asyncio.to_thread(_pick, need, tried)
        if pick is None:
            if not cands.nodes:
                # Nobody left: every node is ruled out, or the ones that were
                # not have just failed to take the request.
                if tried:
                    raise NoNode("no eligible node picked the request up (they may be offline)", 503)
                raise NoNode(_no_node_message(need, cands), _no_node_status(cands))
            if time.monotonic() > queue_deadline:
                raise NoNode(f"every eligible node stayed busy for {QUEUE_SECONDS:.0f}s", 503)
            await asyncio.sleep(QUEUE_POLL_SECONDS)
            continue
        job_id = await asyncio.to_thread(jobs.submit, pick["id"], method=method, path=path,
                                         headers=headers, body=body, timeout=timeout)
        try:
            head = await jobs.wait_head(job_id, timeout=timeout, take_timeout=PICK_SECONDS)
        except jobs.JobNotTaken:
            await asyncio.to_thread(jobs.discard, job_id)
            log.info("node pool: %s did not take %s, trying another", pick["id"], path)
            tried.add(pick["id"])
            continue
        except jobs.JobFailed:
            await asyncio.to_thread(jobs.discard, job_id)
            raise
        return pick, job_id, head


def summary(need: Optional[Need] = None) -> Dict[str, Any]:
    """The pool as the Nodes page shows it: its models and which nodes
    have each, the total capacity and load, and, for a workspace, which
    nodes may take its work and why the others may not."""
    load = jobs.in_flight()
    recs = nodes.list_nodes()
    online = [r for r in recs if not r.get("disabled") and nodes.is_online(r)]
    out: Dict[str, Any] = {
        "backend_id": nodes.POOL_BACKEND if nodes.pool_models() else None,
        "sandbox_provider": nodes.POOL_BACKEND if any(
            (r.get("capabilities") or {}).get("sandbox") for r in recs if not r.get("disabled")) else None,
        "models": nodes.pool_models(),
        "online": len(online),
        "capacity": sum(nodes.capacity(r) for r in online),
        "in_flight": sum(load.get(r["id"], 0) for r in recs),
    }
    if need is not None:
        cands = eligible(need, load=load)
        out["workspace"] = need.workspace
        out["data_class"] = need.data_class
        out["eligible"] = [{"id": r["id"], "name": r.get("name")} for r in cands.nodes]
        out["excluded"] = cands.excluded
    return out


__all__ = ["AWAY", "Candidates", "MISSING", "NoNode", "Need", "PICK_SECONDS", "POLICY",
           "QUEUE_SECONDS", "dispatch", "eligible", "need_for", "summary", "why_not"]
