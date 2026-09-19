"""
The view op protocol — how a live view is mutated step by step.

A live view is not written once; it is built by an ordered log of **ops**, and
the materialized document is ``fold(base, ops)``. Each op addresses a path in
the view document tree (``spec.nodes.n1``, ``spec.vega_lite.mark``,
``controls.roughness``, ``title`` …) and does one of:

- ``add`` / ``update`` — set ``value`` at ``path`` (intermediate objects are
  created as needed). ``add`` and ``update`` are the same set-semantics; the two
  names carry the agent's intent for the status strip.
- ``remove`` — delete the key at ``path``.
- ``clear``  — reset the subtree at ``path`` (or the whole spec) to ``{}``.

Collections built incrementally (graph nodes/edges, scene objects, controls) are
**keyed maps** — ``spec.nodes`` is ``{id: node}``, not a list — so every element
is addressable and ops are idempotent (re-applying an ``add`` for the same id
updates in place). Renderers convert maps to the arrays their libraries expect.

Paths are dot-separated dict keys; there is deliberately no array indexing, so
the model builds collections as maps and never has to reason about shifting
indices.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional

OP_KINDS = ("add", "update", "remove", "clear")


class OpError(ValueError):
    """An op is malformed (bad kind, empty path, non-object where one is needed)."""


def _split(path: str) -> List[str]:
    parts = [p for p in str(path or "").split(".") if p != ""]
    if not parts:
        raise OpError("op path must be a non-empty dotted path")
    return parts


def apply_op(doc: Dict[str, Any], op: Dict[str, Any]) -> Dict[str, Any]:
    """Apply one op to ``doc`` in place and return it.

    Raises :class:`OpError` on a malformed op; a caller applying a batch should
    let that abort the batch so a bad op never half-mutates the document.
    """
    kind = op.get("op")
    if kind not in OP_KINDS:
        raise OpError(f"unknown op kind {kind!r}; expected one of {OP_KINDS}")

    # `clear` with no/empty path resets the whole spec subtree.
    if kind == "clear" and not str(op.get("path") or "").strip():
        doc["spec"] = {}
        return doc

    parts = _split(op.get("path"))
    parent = doc
    for seg in parts[:-1]:
        nxt = parent.get(seg)
        if not isinstance(nxt, dict):
            if kind in ("remove", "clear"):
                return doc  # nothing to remove/clear along a missing path
            nxt = {}
            parent[seg] = nxt
        parent = nxt
    leaf = parts[-1]

    if kind in ("add", "update"):
        parent[leaf] = copy.deepcopy(op.get("value"))
    elif kind == "remove":
        parent.pop(leaf, None)
    elif kind == "clear":
        parent[leaf] = {}
    return doc


def fold(base: Dict[str, Any], ops: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Materialize a document by applying ``ops`` in order onto a copy of ``base``."""
    doc = copy.deepcopy(base or {})
    for op in ops:
        apply_op(doc, op)
    return doc


def normalize_ops(raw: Any) -> List[Dict[str, Any]]:
    """Coerce agent-supplied ops into a validated list of op dicts.

    Accepts a single op dict or a list; validates each op's kind and path so a
    bad batch is rejected as a whole (returned to the agent to fix) rather than
    partially applied. ``value`` is required for add/update.
    """
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise OpError("ops must be an object or a list of objects")
    out: List[Dict[str, Any]] = []
    for i, op in enumerate(raw):
        if not isinstance(op, dict):
            raise OpError(f"op #{i} is not an object")
        kind = op.get("op")
        if kind not in OP_KINDS:
            raise OpError(f"op #{i}: unknown kind {kind!r}; expected {OP_KINDS}")
        has_path = bool(str(op.get("path") or "").strip())
        if kind == "clear":
            pass  # clear may omit path (whole-spec reset)
        elif not has_path:
            raise OpError(f"op #{i}: '{kind}' requires a non-empty path")
        if kind in ("add", "update") and "value" not in op:
            raise OpError(f"op #{i}: '{kind}' requires a 'value'")
        clean = {"op": kind, "path": op.get("path", "")}
        if kind in ("add", "update"):
            clean["value"] = op.get("value")
        out.append(clean)
    return out


def get_at(doc: Dict[str, Any], path: Optional[str]) -> Any:
    """Read the value at ``path`` in ``doc`` (whole doc when path is empty)."""
    if not str(path or "").strip():
        return doc
    node: Any = doc
    for seg in _split(path):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


__all__ = ["apply_op", "fold", "normalize_ops", "get_at", "OpError", "OP_KINDS"]
