"""
Shared JSON envelope for LangChain tool results.

Every tool in this codebase answers in one of two shapes: ``{"ok": true, ...}``
on success, ``{"ok": false, "error": "...", "code": "...", ...}`` on failure.
Twenty-six private copies of these two functions existed across tools/ before
this module — one per file that needed them, byte-for-byte identical except
for a type hint spelled ``Dict[str, object]`` in a couple of them. This is the
one copy the modules this task owns import from; ``eval_ops.py`` and
``service_ops.py`` carry a third-argument variant (``default=str`` on
``json.dumps``, to serialize things like datetimes) that this module does not
reproduce because nothing in the owned modules needs it — see the refactor
report for the full inventory of what still has its own copy.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def json_ok(payload: Dict[str, Any]) -> str:
    """Wrap a successful tool result as ``{"ok": true, **payload}``."""
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def json_err(message: str, *, code: str = "bad_request",
             extra: Optional[Dict[str, Any]] = None) -> str:
    """Wrap a failed tool result as ``{"ok": false, "error": ..., "code": ...}``.

    ``extra`` is merged in on top, for fields like ``{"flow_id": ...}`` that
    let the caller act on the failure without re-parsing the message.
    """
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)
