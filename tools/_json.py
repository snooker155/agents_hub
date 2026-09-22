"""
Shared JSON envelope for LangChain tool results.

Every tool in this codebase answers in one of two shapes: ``{"ok": true, ...}``
on success, ``{"ok": false, "error": "...", "code": "...", ...}`` on failure.
Several private copies of these two functions existed across tools/ before
this module, one per file that needed them: byte-for-byte identical in
docs_tool.py, entity_runs.py and task_management.py, and a variant in
eval_ops.py and service_ops.py that passed ``default=str`` to ``json.dumps``
so a payload could carry a datetime. All five now import from here, with
``default`` as an optional keyword so the ``str``-serializing variant is the
same function rather than a fork of it.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional


def json_ok(payload: Dict[str, Any], *, default: Optional[Callable[[Any], Any]] = None) -> str:
    """Wrap a successful tool result as ``{"ok": true, **payload}``.

    ``default`` is passed straight to ``json.dumps`` (e.g. ``str``, for a
    payload that carries a datetime or similar non-JSON-native value).
    """
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2, default=default)


def json_err(message: str, *, code: str = "bad_request",
             extra: Optional[Dict[str, Any]] = None,
             default: Optional[Callable[[Any], Any]] = None) -> str:
    """Wrap a failed tool result as ``{"ok": false, "error": ..., "code": ...}``.

    ``extra`` is merged in on top, for fields like ``{"flow_id": ...}`` that
    let the caller act on the failure without re-parsing the message.
    """
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2, default=default)
