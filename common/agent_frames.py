"""One translation from an external agent's frames to this hub's chat events.

Two things outside this service report what an agent is doing, and they report
it in the same small vocabulary:

* **pull** — :mod:`agents.remote_agent` streams an imported agent's run over
  HTTP, because the hub started that run.
* **push** — ``/api/ingest`` receives a run the hub did *not* start, from a
  graph running on someone else's trigger (see EXTERNAL_CONNECTIONS.md).

Both then have to produce the events the chat, the live view and the run record
already understand. Written twice, the two would drift, and the same run would
render differently depending on which direction it arrived from. So the mapping
lives here once and both call it.

The wire vocabulary, all fields optional unless named::

    {"type": "token",       "token": "..."}
    {"type": "thinking",    "message": "..."}
    {"type": "tool_start",  "name": "...", "input": "..."}
    {"type": "tool_end",    "name": "...", "output": "..."}
    {"type": "tool_error",  "name": "...", "error": "..."}
    {"type": "node_start",  "node": "...", "depth": 0}
    {"type": "node_end",    "node": "...", "ok": true, "next": "...", "error": "..."}
    {"type": "interrupt",   "question": "...", "choices": [...], "key": "..."}
    {"type": "usage",       "prompt_tokens": N, "completion_tokens": N}
    {"type": "done",        "ok": true, "output": "...", "error": null}
    {"type": "error",       "error": "..."}

What comes out is this hub's own event shapes. Two renamings are deliberate:

* ``node_start``/``node_end`` become ``graph_node_start``/``graph_node_end``,
  because ``node_start`` already means "a node of a hub *flow* started" on the
  chat stream and opens a bubble per node. An external agent's internal nodes
  are not flow nodes.
* ``thinking`` keeps its name but is a log line, not a reasoning step: the hub's
  own ``think``/``plan`` events carry model reasoning and are not something an
  external agent can claim.

``interrupt`` is how a graph says it has stopped to ask a human something. It is
not an error and not an end: the run is parked, the question goes to whoever is
watching, and the work continues later. See ``connections/service.py`` for what
parking means on this side, and note that the hub cannot push the answer back to
an agent that reports in — in that direction the client asks for it.

The translator is also the accumulator: a run's answer, its tool trail, the
nodes it passed through and the tokens it reported are all built from the same
frames, and having one object own both means a caller cannot forward an event
it forgot to record.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Bounds applied to anything that arrives from outside. These are strings that
# end up in a run record, in a browser and in a log; an agent that reports a
# whole retrieved document as one tool output must not be able to make any of
# those unusable.
MAX_TEXT = 40_000
MAX_FIELD = 4_000


def _clip(value: Any, limit: int = MAX_FIELD) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_usage(frame: Dict[str, Any]) -> Dict[str, int]:
    """Read a ``usage`` frame in whichever spelling it arrived in.

    Providers and frameworks disagree about these names, and an external agent
    reports whatever its own stack gave it. The remote is the only party that
    can know the numbers at all, since the model call happened in its process,
    so accepting the common spellings is the difference between a priced run
    and a run whose cost column reads zero.
    """
    prompt = _int(frame.get("prompt_tokens") or frame.get("input_tokens"))
    completion = _int(frame.get("completion_tokens") or frame.get("output_tokens"))
    total = _int(frame.get("total_tokens")) or (prompt + completion)
    if not (prompt or completion or total):
        return {}
    from agents.callbacks.run_statistics import cached_input_tokens
    cached = min(cached_input_tokens(frame), prompt)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
    }


class FrameTranslator:
    """Folds one external run's frames into hub events and a record of the run.

    Stateful on purpose: tool steps are numbered, a ``tool_end`` resolves the
    step a ``tool_start`` opened, and a node entered twice by a loop closes in
    the right order. Feeding frames out of order is the caller's problem; every
    method here tolerates a frame that resolves nothing rather than raising,
    because a malformed frame from a foreign service must not fail a run that
    is otherwise working.
    """

    def __init__(self) -> None:
        self.interrupt: Optional[Dict[str, Any]] = None
        self.text_parts: List[str] = []
        self.steps: List[Dict[str, Any]] = []
        self.nodes: List[Dict[str, Any]] = []
        self.usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0,
                                      "total_tokens": 0, "cached_tokens": 0}
        self.done: Optional[Dict[str, Any]] = None
        self.step = 0
        self._pending_tool: Optional[str] = None
        self._pending_input: str = ""
        self._open_nodes: List[str] = []

    # ── reads ───────────────────────────────────────────────────────────────

    @property
    def output(self) -> str:
        """The answer: the ``done`` frame's, or the tokens that were streamed."""
        if isinstance(self.done, dict):
            for key in ("output", "agent_output", "result", "response", "text", "answer"):
                value = self.done.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return "".join(self.text_parts).strip()

    @property
    def ok(self) -> Optional[bool]:
        """Whether the run succeeded, or None while it is still open."""
        if self.done is None:
            return None
        return self.done.get("ok") is not False

    @property
    def error(self) -> str:
        return str((self.done or {}).get("error") or "")

    def credit(self, usage: Dict[str, int]) -> None:
        for key, value in usage.items():
            if key in self.usage:
                self.usage[key] += value

    # ── the translation ─────────────────────────────────────────────────────

    def feed(self, frame: Any) -> List[Dict[str, Any]]:
        """Translate one frame. Returns the hub events it produces, if any."""
        if not isinstance(frame, dict):
            return []
        kind = str(frame.get("type") or "").strip()
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            # An unknown frame type is ignored rather than refused: the
            # vocabulary grows, and an agent written against a newer hub must
            # not break against an older one, or the reverse.
            return []
        return handler(frame)

    def _on_token(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        token = frame.get("token")
        if not isinstance(token, str) or not token:
            return []
        if sum(len(p) for p in self.text_parts) < MAX_TEXT:
            self.text_parts.append(token)
        return [{"type": "token", "token": token}]

    def _on_thinking(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        message = str(frame.get("message") or frame.get("text") or "").strip()
        return [{"type": "thinking", "message": _clip(message)}] if message else []

    # The two spellings a remote may use for the same thing.
    _on_progress = _on_thinking
    _on_log = _on_thinking

    def _on_tool_start(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.step += 1
        name = str(frame.get("name") or frame.get("tool") or "tool")
        self._pending_tool = name
        # Held for the matching end: the trail records what a tool was called
        # with, and only the *start* frame carries it.
        self._pending_input = _clip(frame.get("input"))
        return [{
            "type": "tool_start",
            "step": self.step,
            "tool": name,
            "input": _clip(frame.get("input")),
        }]

    def _on_tool_end(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        name = str(frame.get("name") or frame.get("tool") or self._pending_tool or "")
        output = _clip(frame.get("output"))
        args = frame.get("args") if isinstance(frame.get("args"), dict) else {}
        self.steps.append({"name": name, "args": args,
                           "input": self._pending_input, "output": output})
        self._pending_tool = None
        self._pending_input = ""
        return [{"type": "tool_end", "step": self.step, "tool": name, "output": output}]

    def _on_tool_error(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        name = str(frame.get("name") or frame.get("tool") or self._pending_tool or "")
        error = _clip(frame.get("error"))
        # A failed call belongs in the trail as much as a successful one; left
        # out, a run that died in a tool shows no tool at all.
        self.steps.append({"name": name, "args": {}, "input": self._pending_input,
                           "output": "", "error": error})
        self._pending_tool = None
        self._pending_input = ""
        return [{"type": "tool_error", "step": self.step, "tool": name,
                 "error": error}]

    def _on_node_start(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        node = str(frame.get("node") or frame.get("name") or "").strip()
        if not node:
            return []
        depth = _int(frame.get("depth"))
        self._open_nodes.append(node)
        self.nodes.append({"node": node, "depth": depth, "ok": None})
        return [{"type": "graph_node_start", "node": node, "depth": depth,
                 "input": _clip(frame.get("input"), 2000)}]

    _on_node_enter = _on_node_start

    def _on_node_end(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        node = str(frame.get("node") or frame.get("name") or "").strip()
        if not node:
            return []
        ok = frame.get("ok")
        ok = True if ok is None else bool(ok)
        # The most recent unresolved entry of this node closes: a loop visits
        # the same node repeatedly, and closing the first would leave every
        # later pass looking unfinished.
        for entry in reversed(self.nodes):
            if entry["node"] == node and entry["ok"] is None:
                entry["ok"] = ok
                break
        if node in self._open_nodes:
            at = len(self._open_nodes) - 1 - self._open_nodes[::-1].index(node)
            self._open_nodes = self._open_nodes[:at]
        interrupted = str(frame.get("status") or "") == "interrupted"
        return [{
            "type": "graph_node_end",
            "node": node,
            "ok": ok,
            # A node stopped by an interrupt neither succeeded nor failed: it is
            # where the run is waiting, which is the single most useful thing to
            # know about a paused graph.
            "interrupted": interrupted,
            "error": _clip(frame.get("error")) or None,
            "next": str(frame.get("next") or "") or None,
        }]

    _on_node_done = _on_node_end
    _on_node_exit = _on_node_end

    def _on_interrupt(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The graph has stopped to ask a human something.

        Recorded as well as forwarded, because the question outlives the event:
        somebody has to be able to find it hours later on a run that is still
        waiting, not only in a stream nobody was watching.
        """
        question = _clip(frame.get("question") or frame.get("message"), 2000)
        choices = [
            _clip(c, 200) for c in (frame.get("choices") or [])
            if isinstance(c, (str, int, float))
        ][:20]
        self.interrupt = {
            "question": question,
            "choices": choices,
            # The graph's own id for this interrupt, when it has one. Echoed back
            # with the answer so a client resuming several at once can tell them
            # apart.
            "key": _clip(frame.get("key") or frame.get("id"), 200),
            "node": _clip(frame.get("node"), 200),
        }
        return [{"type": "awaiting_input", **self.interrupt}]

    def _on_usage(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        usage = normalize_usage(frame)
        if not usage:
            return []
        self.credit(usage)
        return [{"type": "usage", **usage, "remote": True}]

    def _on_done(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.done = dict(frame)
        # A remote that reports its usage only at the end is common, and that
        # number is the run's whole cost — it is credited and forwarded exactly
        # like a mid-run usage frame.
        nested = frame.get("usage")
        events: List[Dict[str, Any]] = []
        if isinstance(nested, dict):
            usage = normalize_usage(nested)
            if usage:
                self.credit(usage)
                events.append({"type": "usage", **usage, "remote": True})
        # Nothing else: how a finished run is announced differs between the two
        # callers (a pull run returns an AgentResult, a pushed one closes a run
        # record), and inventing a common event here would fit neither.
        return events

    _on_final = _on_done
    _on_result = _on_done

    def _on_error(self, frame: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.done = {"ok": False, "error": str(frame.get("error") or "the agent reported an error")}
        return []

    # ── the run record ──────────────────────────────────────────────────────

    def token_usage(self) -> Dict[str, int]:
        """Usage in the spelling ``managers.run_manager`` stores."""
        return {
            "inbound_tokens": self.usage["prompt_tokens"],
            "outbound_tokens": self.usage["completion_tokens"],
            "total_tokens": self.usage["total_tokens"] or (
                self.usage["prompt_tokens"] + self.usage["completion_tokens"]),
            "cached_tokens": self.usage["cached_tokens"],
        }

    def tool_calls(self) -> List[Dict[str, Any]]:
        """The tool trail in the shape the run payload stores.

        ``args`` when the reporter sent structured arguments, the ``input``
        string from the start frame otherwise — which is what the documented
        vocabulary actually carries, and what both tracers send.
        """
        return [
            {
                "step": i + 1,
                "tool": s["name"],
                "input": _clip(s["args"]) if s.get("args") else s.get("input", ""),
                "output": s["output"],
                **({"error": s["error"]} if s.get("error") else {}),
            }
            for i, s in enumerate(self.steps)
        ]

    def path(self) -> List[str]:
        """The nodes this run passed through, in order, for the run record."""
        return [n["node"] for n in self.nodes]


__all__ = ["FrameTranslator", "normalize_usage", "MAX_TEXT", "MAX_FIELD"]
