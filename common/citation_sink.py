"""
Citation sink: a context-var channel for the sources a run's answer rests on.

A tool that hands the model retrieved text (``search_memory`` over a pool's
indexed files) records each passage here with :func:`record_citation`, and
gets back the number the model should cite it by (``[1]``, ``[2]``). The chat
pipelines install a :class:`CitationSink` for the duration of a turn, the same
way they install ``common.entity_sink``; :func:`chat.streaming.drive_streaming_run`
copies :meth:`CitationSink.payloads` onto the turn's ``done`` event and the run
record (``process.citations``), and the reply renders them as a sources list.

A task run installs one too (``runtime/agent_run.py``), so the run record of a
task keeps ``process.citations`` the same way a chat run does; a flow chat
installs one per node (``chat/flow_driver.py``).

Outside those (CLI, tests) no sink is installed and :func:`record_citation`
returns ``None``: the tool still answers, the passages just carry no number.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, List, Optional, Tuple

#: Longest snippet kept per citation; the full passage stays in the pool.
SNIPPET_CHARS = 280

CitationKey = Tuple[str, str, str]


class CitationSink:
    """Ordered, de-duplicated record of the passages one run was shown.

    Keyed by ``(pool_id, file key, chunk index)``: the same passage returned by
    two searches keeps its first number, so the model's ``[n]`` stays stable
    for the whole turn.
    """

    def __init__(self) -> None:
        self._records: Dict[CitationKey, Dict[str, Any]] = {}

    def record(self, *, pool_id: str = "", file_id: str = "", filename: str = "",
               chunk_idx: Any = 0, heading_path: Any = None, text: str = "",
               score: Any = None, workspace_file_id: str = "", layer: str = "rag") -> int:
        key = (str(pool_id or ""), str(file_id or filename or ""), str(chunk_idx or 0))
        existing = self._records.get(key)
        if existing is not None:
            return int(existing["n"])
        n = len(self._records) + 1
        snippet = " ".join(str(text or "").split())
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[: SNIPPET_CHARS - 1].rstrip() + "…"
        self._records[key] = {
            "n": n,
            "pool_id": str(pool_id or ""),
            "file_id": str(file_id or ""),
            "filename": str(filename or ""),
            "chunk_idx": chunk_idx,
            "heading_path": heading_path,
            "snippet": snippet,
            "score": score,
            "workspace_file_id": str(workspace_file_id or ""),
            # "rag" for a passage of an indexed document, "note" for a pool
            # note (memory/tool.py decides which layers get numbers).
            "layer": str(layer or "rag"),
        }
        return n

    def payloads(self) -> List[Dict[str, Any]]:
        return sorted((dict(r) for r in self._records.values()), key=lambda r: r["n"])


_SINK: contextvars.ContextVar[Optional[CitationSink]] = contextvars.ContextVar(
    "citation_sink", default=None)


def set_sink(sink: Optional[CitationSink]) -> contextvars.Token:
    return _SINK.set(sink)


def reset_sink(token: contextvars.Token) -> None:
    _SINK.reset(token)


def current() -> Optional[CitationSink]:
    return _SINK.get()


def record_citation(**fields: Any) -> Optional[int]:
    """Record one passage on the current run's sink; its citation number, or
    None when no sink is installed."""
    sink = _SINK.get()
    if sink is None:
        return None
    return sink.record(**fields)


__all__ = ["CitationSink", "SNIPPET_CHARS", "current", "record_citation",
           "reset_sink", "set_sink"]
