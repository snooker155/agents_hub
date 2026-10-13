"""
map_on_nodes: one instruction over many items, spread over the linked nodes
(docs/hub-link.md, "The pool").

Every item becomes its own model call to the node pool (the custom backend
``nodes``, hublink/scheduler.py), and the calls go out at once, as many as
the pool's nodes run together. Each lands on whichever node is least busy
when it is sent, so twenty summaries on four laptops take about the time of
five on one. The answer lists each item's output and the node that made it.

Which nodes may answer is not this tool's choice: the pool sends the run's
work only to nodes trusted for its data class (common/data_class.py), and a
call no node may take comes back as that item's error. An untrusted node
(only ever given public work) could answer anything, which is why the tool
counts as reading untrusted text (tools/capabilities.py).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MAX_ITEMS = 200
MAX_WORKERS = 32
OUTPUT_CHARS = 8000
REQUEST_TIMEOUT = 660.0


class MapOnNodesInput(BaseModel):
    instruction: str = Field(..., description=(
        "What to do with each item. Every item is sent as its own request: this instruction, "
        "then the item."))
    items: List[str] = Field(..., description=f"The items, one request each (1 to {MAX_ITEMS}).")
    model: Optional[str] = Field(None, description=(
        "A model the node pool serves (the 'nodes' backend on the Models page). "
        "Default: the one the most nodes share."))
    max_tokens: int = Field(1024, description="The longest answer per item, in tokens (16 to 8192).")


def _pool() -> Tuple[str, str]:
    """The pool's base URL (as this process reaches it) and key."""
    from providers import registry
    backend = registry.get_backend("nodes")
    if not backend or not backend.get("base_url"):
        raise RuntimeError("no node shares a model, so there is no node pool (Nodes page)")
    from common.hostnet import host_service_url
    return host_service_url(str(backend["base_url"]).rstrip("/")), str(backend.get("api_key") or "")


def _headers(key: str) -> Dict[str, str]:
    from common.data_class import caller_headers
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "X-Hub-Source": "hub", **caller_headers()}


def _catalog(base: str, key: str) -> Dict[str, Any]:
    import requests
    resp = requests.get(f"{base}/models", headers=_headers(key), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"the node pool did not list its models ({resp.status_code})")
    return resp.json()


def _one(base: str, key: str, model: str, instruction: str, item: str, max_tokens: int) -> Dict[str, Any]:
    import requests
    body = {"model": model, "max_tokens": max_tokens, "stream": False,
            "messages": [{"role": "user", "content": f"{instruction}\n\n{item}"}]}
    try:
        resp = requests.post(f"{base}/chat/completions", headers=_headers(key), json=body,
                             timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        return {"error": f"could not reach the node pool: {exc}"}
    node = resp.headers.get("X-Hub-Node") or ""
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text[:300]
        return {"node": node, "error": str(detail or resp.status_code)}
    try:
        data = resp.json()
        text = data["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return {"node": node, "error": "the node answered with something that is not a completion"}
    if len(text) > OUTPUT_CHARS:
        text = text[:OUTPUT_CHARS] + " [truncated]"
    return {"node": node, "output": text}


@tool("map_on_nodes", args_schema=MapOnNodesInput)
def map_on_nodes(instruction: str, items: List[str], model: Optional[str] = None,
                 max_tokens: int = 1024) -> str:
    """Run one instruction over many independent items in parallel on the linked nodes
    (each item is its own request; the least busy allowed node answers it). Use it for
    batch work such as summarising, classifying or extracting from a list of texts.
    Returns each item's output and the node that answered it."""
    items = [str(i) for i in items or []]
    if not items:
        return json.dumps({"error": "no items given"})
    if len(items) > MAX_ITEMS:
        return json.dumps({"error": f"at most {MAX_ITEMS} items per call; split the list"})
    max_tokens = max(16, min(8192, int(max_tokens or 1024)))
    try:
        base, key = _pool()
        catalog = _catalog(base, key)
    except Exception as exc:  # noqa: BLE001 - reported to the model as the tool's answer
        return json.dumps({"error": str(exc)})
    served = [m for m in catalog.get("data") or [] if isinstance(m, dict) and m.get("id")]
    if model and model not in {m["id"] for m in served}:
        return json.dumps({"error": f"the node pool does not serve {model}",
                           "models": [m["id"] for m in served]})
    if not model:
        if not served:
            return json.dumps({"error": "the node pool serves no model"})
        model = max(served, key=lambda m: int(m.get("nodes") or 0))["id"]
    workers = max(1, min(len(items), MAX_WORKERS, int(catalog.get("capacity") or len(items))))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="map-on-nodes") as pool:
        answers = list(pool.map(lambda item: _one(base, key, str(model), instruction, item, max_tokens), items))
    by_node: Dict[str, int] = {}
    results = []
    for index, answer in enumerate(answers):
        if answer.get("node") and "output" in answer:
            by_node[answer["node"]] = by_node.get(answer["node"], 0) + 1
        results.append({"index": index, **answer})
    failed = sum(1 for a in answers if "error" in a)
    return json.dumps({"model": model, "items": len(items), "done": len(items) - failed, "failed": failed,
                       "parallel": workers, "by_node": by_node, "results": results}, ensure_ascii=False)


NODE_MAP_TOOLS = [map_on_nodes]

__all__ = ["NODE_MAP_TOOLS", "map_on_nodes"]
