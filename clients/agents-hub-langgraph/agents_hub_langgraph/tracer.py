"""The callback handler: one line in a customer's code, nothing else touched.

    graph.invoke(state, config={"callbacks": [HubTracer(url=..., token=...)]})

LangGraph propagates callbacks into every node, so this sees model tokens, tool
calls, node boundaries and token usage without the graph knowing it is watched.
That is the whole integration: no adapter process, no endpoint to expose, no
diff in the graph.

**Node identity comes from ``run_id``, not from names.** LangChain gives a chain
start its name in the keyword arguments and gives the matching end nothing but
the same ``run_id``. Matching on names would close the wrong node the moment a
graph loops or contains a subgraph, so every open boundary is kept by its id and
closed by it.

**A node is a chain whose name is its ``langgraph_node``.** LangGraph runs each
node as a chain and everything inside it as more chains, all carrying the same
``langgraph_node`` metadata. Only the outermost one *is* the node; the rest are
its internals and belong in the run log, not on the graph picture.

What is sent is the frame vocabulary in ``common/agent_frames.py``, the same one
an imported agent streams, so a pushed run and a pulled one render identically.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
import weakref
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - exercised by which package is installed, not by a test
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover
    class BaseCallbackHandler:  # type: ignore[no-redef]
        """Stand-in so the package imports without langchain-core present."""

from agents_hub_langgraph.client import HubClient, Transport, warn_once
from agents_hub_langgraph.topology import graph_topology

MAX_FIELD = 4_000

# How a paused graph is polled for its answer. A person is on the other end, so
# the interval is measured in seconds and the default wait in hours: polling
# faster would not make anyone answer sooner.
ANSWER_POLL_SECONDS = 2.0
ANSWER_WAIT_SECONDS = 6 * 60 * 60

# Every live tracer that asked to be flushed at exit, and the one handler that
# does it. One ``atexit.register`` per tracer would leak a callback per
# invocation in a server, which is exactly the usage this package recommends.
# A weak set means a finished tracer is collected normally.
_LIVE_TRACERS: "weakref.WeakSet" = weakref.WeakSet()
_ATEXIT_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False
# Bounded so a hub that is unreachable delays a process's exit by seconds, not
# by a wait per tracer.
EXIT_FLUSH_TIMEOUT = 2.0


def _flush_live_tracers() -> None:
    for tracer in list(_LIVE_TRACERS):
        try:
            tracer.flush(timeout=EXIT_FLUSH_TIMEOUT)
        except Exception:
            pass


def _flush_at_exit(tracer: "HubTracer") -> None:
    global _ATEXIT_REGISTERED
    with _ATEXIT_LOCK:
        _LIVE_TRACERS.add(tracer)
        if not _ATEXIT_REGISTERED:
            atexit.register(_flush_live_tracers)
            _ATEXIT_REGISTERED = True


def _clip(value: Any, limit: int = MAX_FIELD) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _text_of(message: Any) -> str:
    """Text of a message whose content may be a string or a list of blocks.

    Which shape arrives depends on the provider the graph was configured with,
    so both have to work here.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _state_text(value: Any) -> str:
    """Best-effort text for a graph's input or output state."""
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, (list, tuple)) and len(last) == 2:
                return str(last[1])
            return _text_of(last)
        for candidate in ("input", "question", "query", "prompt", "text"):
            if isinstance(value.get(candidate), str):
                return value[candidate]
        return ""
    return _text_of(value)


def _question_of(interrupt: Any) -> Dict[str, Any]:
    """Turn one LangGraph ``Interrupt`` into something a person can answer.

    Its ``value`` is whatever the graph's author passed to ``interrupt()``:
    application data, not a contract. A dict with a question field if they
    wrote one, a bare string if they did not — and this has to reach a person
    either way rather than only in the shape our own example happens to use.
    """
    value = getattr(interrupt, "value", interrupt)
    key = str(getattr(interrupt, "id", "") or getattr(interrupt, "interrupt_id", "") or "")

    if isinstance(value, dict):
        question = value.get("question") or value.get("prompt") or value.get("message")
        choices = value.get("choices") or value.get("options") or []
        # No recognised question field: show the payload itself rather than an
        # empty prompt, so the person is asked *something* they can act on.
        return {
            "question": _clip(question if question is not None else value, 2000),
            "choices": [_clip(c, 200) for c in choices if isinstance(c, (str, int, float))],
            "key": key,
        }
    return {"question": _clip(value, 2000), "choices": [], "key": key}


def _is_interrupt(error: Any) -> bool:
    """Whether this exception is a graph pausing, not a graph failing.

    LangGraph suspends by raising ``GraphInterrupt`` through the node, so the
    callback that reports a node dying and the callback that reports a node
    waiting for a person are the same one. Told apart wrongly, every pause is
    recorded as a failure.

    Matched by class first and by name second, so the check keeps working
    against a LangGraph whose internals moved, and against one that is not
    importable from here at all.
    """
    try:
        from langgraph.errors import GraphInterrupt

        if isinstance(error, GraphInterrupt):
            return True
    except Exception:
        pass
    return type(error).__name__ in ("GraphInterrupt", "Interrupt", "NodeInterrupt")


def _pending_question(graph: Any, thread_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Ask the graph what it stopped to ask, or None if it is not stopped.

    Read from the checkpointer rather than from the callback, because the
    callback never sees it: LangGraph adds ``__interrupt__`` to what ``invoke``
    *returns*, after the root chain has already ended. The graph itself knows,
    so the graph is asked — which is also why a tracer given no graph can report
    that a run is paused but not what it is waiting for.
    """
    if graph is None or not thread_id:
        return None
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return None

    interrupts = list(getattr(snapshot, "interrupts", None) or [])
    if not interrupts:
        for task in getattr(snapshot, "tasks", None) or []:
            interrupts.extend(getattr(task, "interrupts", None) or [])
    if not interrupts:
        return None

    pending = _question_of(interrupts[0])
    # ``next`` names the node the graph will re-enter, which is the node it
    # stopped inside: the one thing someone looking at a paused graph wants.
    nxt = getattr(snapshot, "next", None) or ()
    if nxt:
        pending["node"] = str(nxt[0])
    return pending


class HubTracer(BaseCallbackHandler):
    """Report a LangChain or LangGraph run to an Agents Hub, and never break it.

    Every callback is wrapped: an exception raised here would propagate into the
    customer's graph, which is an unacceptable price for telemetry. Failures are
    counted and, if an ``on_error`` was given, handed to it.

    One tracer follows one run at a time, which is how a callback handler is
    used: it is passed per invocation. Reuse across concurrent invocations is
    not supported and is refused loudly through ``on_error`` rather than
    silently interleaving two runs into one record.
    """

    def __init__(
        self,
        *,
        url: str = "",
        token: str = "",
        thread: Optional[str] = None,
        title: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        # The compiled graph, when you have it here. Optional, and worth
        # passing: with it a paused run carries the question it is waiting on,
        # and `report_graph()` needs no argument. Without it a pause is still
        # reported, as "waiting in <node>".
        graph: Optional[Any] = None,
        transport: Optional[Any] = None,
        on_error: Optional[Any] = None,
        flush_interval: float = 0.5,
        flush_on_exit: bool = True,
    ):
        # Environment fallbacks so a deployment can be configured without a code
        # change, which is often the difference between "we'll try it" and "that
        # needs a release".
        url = url or os.environ.get("AGENTS_HUB_URL", "")
        token = token or os.environ.get("AGENTS_HUB_TOKEN", "")
        self._client = HubClient(
            transport or Transport(url, token),
            flush_interval=flush_interval,
            on_error=on_error,
        )
        self._thread = thread
        self._title = title
        self._model = model
        self._provider = provider
        self._metadata = dict(metadata or {})
        self._graph = graph
        self._thread_id: Optional[str] = None
        # Set when a node suspends. The root end then parks the run instead of
        # completing it.
        self._paused = False
        # The node most recently entered, so a pause can name where it stopped
        # even when the node's own bookkeeping has already been cleared.
        self._last_node = ""
        self._on_error = on_error
        # Failures on this side of the wire: a callback that went wrong, a graph
        # that could not be read. The transport keeps its own count, and
        # :attr:`errors` is the two together.
        self._errors = 0

        self._lock = threading.Lock()
        self._root: Optional[Any] = None
        self._nodes: Dict[Any, str] = {}
        self._tools: Dict[Any, str] = {}
        self._depth = 0
        self._text_parts: List[str] = []
        self._last_message = ""
        # Set when the last run parked for a question, and passed on the next
        # run so the hub can show the two as one piece of work.
        self._resumed_from: Optional[str] = None

        if flush_on_exit:
            # A script that ends the moment its graph returns would otherwise
            # exit with the run half reported.
            _flush_at_exit(self)

    # ── plumbing ────────────────────────────────────────────────────────────

    def _fail(self, exc: Exception) -> None:
        self._errors += 1
        if self._on_error is None:
            warn_once(exc)
            return
        try:
            self._on_error(exc)
        except Exception:
            pass

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait for everything queued to reach the hub."""
        try:
            return self._client.flush(timeout)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return False

    def close(self, timeout: float = 5.0) -> bool:
        """Send what is left and stop reporting. True if everything got through.

        Not required: the worker thread ends on its own after a while idle, and
        a process that keeps running never needs this. It is for a process that
        is about to stop, or anywhere a thread that outlives its tracer would be
        a problem of its own.
        """
        try:
            drained = self._client.stop(timeout)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return False
        _LIVE_TRACERS.discard(self)
        return drained

    def report_graph(self, graph: Any = None) -> None:
        """Send the graph's shape, so the hub can draw it.

        Called once, usually at startup next to where the graph is compiled. In
        this direction the hub cannot come and ask, so a graph that never
        reports its shape is monitored as a list of runs rather than a picture.
        """
        try:
            target = graph if graph is not None else self._graph
            if target is None:
                self._fail(ValueError("report_graph needs a graph, here or on the tracer"))
                return
            self._graph = self._graph or target
            self._client.topology(graph_topology(target))
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    @property
    def run_id(self) -> Optional[str]:
        """The hub's id for the run being reported, once it has been opened."""
        return self._client.run_id

    @property
    def errors(self) -> int:
        """How many failures this tracer has swallowed.

        Everything here is swallowed by design, which leaves a mistyped token
        looking exactly like a working setup. This is the number to assert on in
        a test and to check once after wiring it up; ``on_error`` is the way to
        see the failures themselves.
        """
        return self._errors + self._client.errors

    def _emit(self, frame: Dict[str, Any]) -> None:
        self._client.emit(frame)

    # ── chains: the run, and the graph's nodes ──────────────────────────────

    def on_chain_start(self, serialized, inputs, *, run_id=None, parent_run_id=None,
                       tags=None, metadata=None, **kwargs) -> None:
        try:
            name = str(kwargs.get("name") or (serialized or {}).get("name") or "")
            node = str((metadata or {}).get("langgraph_node") or "")

            if parent_run_id is None:
                with self._lock:
                    if self._root is not None:
                        self._fail(RuntimeError(
                            "HubTracer is already following a run; pass a new tracer per "
                            "invocation rather than sharing one across concurrent runs"
                        ))
                        return
                    self._root = run_id
                    self._text_parts = []
                    self._last_message = ""
                with self._lock:
                    resumed_from, self._resumed_from = self._resumed_from, None
                    # LangGraph puts the thread on the root run's metadata, and
                    # the thread is how the graph is asked what it is waiting
                    # for once it pauses.
                    self._thread_id = str((metadata or {}).get("thread_id") or "") or None
                self._client.open({
                    "input": _clip(_state_text(inputs)),
                    "resumed_from": resumed_from,
                    "thread": self._thread,
                    "title": self._title or name or None,
                    "model": self._model,
                    "provider": self._provider,
                    "metadata": self._metadata,
                })
                return

            # Only the chain that *is* the node, not everything running inside it.
            if node and name == node:
                with self._lock:
                    self._nodes[run_id] = node
                    depth = self._depth
                    self._depth += 1
                with self._lock:
                    self._last_node = node
                self._emit({"type": "node_start", "node": node, "depth": depth})
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def on_chain_end(self, outputs, *, run_id=None, parent_run_id=None, **kwargs) -> None:
        try:
            with self._lock:
                is_root = self._root is not None and run_id == self._root
                node = self._nodes.pop(run_id, None)
                if node is not None:
                    self._depth = max(0, self._depth - 1)

            if node is not None:
                self._emit({"type": "node_end", "node": node, "ok": True})
                return

            if is_root:
                text = _state_text(outputs) or "".join(self._text_parts).strip() or self._last_message
                with self._lock:
                    open_nodes = list(self._nodes.values())
                    thread_id = self._thread_id
                    paused, self._paused = self._paused, False
                # Nothing in the callback payload says a graph paused: the root
                # end looks the same either way, and ``__interrupt__`` is added
                # to what ``invoke`` returns afterwards. The signal is the
                # GraphInterrupt that came through the node, and — for a
                # framework that suspends without raising — a node still open.
                if paused or open_nodes:
                    pending = _pending_question(self._graph, thread_id) or {
                        "question": "",
                        "choices": [],
                        "key": "",
                        "node": (open_nodes or [self._last_node])[-1] if (open_nodes or self._last_node) else "",
                    }
                    self._close_open_nodes(interrupted=True)
                    self._client.interrupt({**pending, "output": _clip(text)})
                else:
                    self._client.close({"ok": True, "output": _clip(text)})
                with self._lock:
                    self._root = None
                    self._nodes.clear()
                    self._tools.clear()
                    self._depth = 0
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def on_chain_error(self, error, *, run_id=None, parent_run_id=None, **kwargs) -> None:
        try:
            with self._lock:
                is_root = self._root is not None and run_id == self._root
                node = self._nodes.pop(run_id, None)
                if node is not None:
                    self._depth = max(0, self._depth - 1)

            if node is not None:
                if _is_interrupt(error):
                    # Not a failure: the graph stopped inside this node to ask
                    # somebody something, and will re-enter it when answered.
                    with self._lock:
                        self._paused = True
                    self._emit({"type": "node_end", "node": node,
                                "status": "interrupted", "ok": True})
                    return
                self._emit({"type": "node_end", "node": node, "ok": False, "error": _clip(error)})
                return

            if is_root:
                # The run failed, and its partial output is still worth keeping:
                # a graph that died in its last node has usually already done
                # most of the work.
                self._client.close({
                    "ok": False,
                    "error": _clip(error),
                    "output": _clip("".join(self._text_parts).strip()),
                })
                with self._lock:
                    self._root = None
                    self._nodes.clear()
                    self._tools.clear()
                    self._depth = 0
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def _close_open_nodes(self, *, interrupted: bool = False) -> None:
        """End the node boundaries still open, saying why they never closed.

        An interrupted node has no end event of its own: the graph suspends
        inside it. Leaving it open would draw a run that is paused as a run that
        is still working, which is the opposite of what someone watching needs.
        """
        with self._lock:
            open_nodes = list(self._nodes.values())
            self._nodes.clear()
            self._depth = 0
        for node in reversed(open_nodes):
            self._emit({
                "type": "node_end", "node": node,
                "status": "interrupted" if interrupted else "",
                "ok": not interrupted,
            })

    # ── the pause ───────────────────────────────────────────────────────────

    def wait_for_answer(
        self,
        *,
        timeout: float = ANSWER_WAIT_SECONDS,
        poll_interval: float = ANSWER_POLL_SECONDS,
    ) -> Any:
        """Block until somebody answers the parked run's question.

        Returns the answer, or ``None`` if nobody answered within ``timeout``.

        Polling, because in this direction the hub never calls out: your graph
        reports to it, nothing in it reaches back into your process. So the
        party that needs the answer asks for it.

        Meant to be used around your own resume, which only you can perform::

            result = graph.invoke(state, config=cfg)
            while "__interrupt__" in result:
                answer = tracer.wait_for_answer()
                result = graph.invoke(Command(resume=answer), config=cfg)
        """
        self.flush(timeout=10)
        run_id = self._client.parked_run_id
        if not run_id:
            return None

        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            body = self._client.read_answer(run_id) or {}
            if body.get("status") == "answered":
                # The run this answer belongs to is what the next one continues.
                self._resumed_from = run_id
                return body.get("value")
            if body.get("status") not in ("waiting", None):
                # Parked, then closed or deleted by someone. Waiting longer
                # would be waiting on a run that no longer exists.
                return None
            time.sleep(poll_interval)
        return None

    @property
    def parked_run_id(self) -> Optional[str]:
        """The run waiting for an answer, if the last one was interrupted."""
        return self._client.parked_run_id

    # ── models ──────────────────────────────────────────────────────────────

    def on_llm_new_token(self, token, **kwargs) -> None:
        try:
            if token:
                self._text_parts.append(str(token))
                self._emit({"type": "token", "token": str(token)})
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def on_llm_end(self, response, **kwargs) -> None:
        """Record the answer and, crucially, what it cost.

        This is the only place the token counts exist: the model call happened
        in this process, so a run whose tracer ignores them is a run the hub can
        show but never price.
        """
        try:
            message = None
            generations = getattr(response, "generations", None) or []
            if generations and generations[0]:
                message = getattr(generations[0][0], "message", None)
                text = _text_of(message) if message is not None else getattr(generations[0][0], "text", "")
                if text:
                    self._last_message = text

            usage = getattr(message, "usage_metadata", None) if message is not None else None
            if not usage:
                usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            if isinstance(usage, dict) and usage:
                frame = {"type": "usage"}
                # Both spellings pass straight through: the hub normalises them,
                # and guessing wrong here would zero a run's cost.
                for key in ("input_tokens", "output_tokens", "total_tokens",
                            "prompt_tokens", "completion_tokens"):
                    if usage.get(key) is not None:
                        frame[key] = int(usage[key])
                details = usage.get("input_token_details")
                if isinstance(details, dict) and details.get("cache_read"):
                    frame["cached_tokens"] = int(details["cache_read"])
                if len(frame) > 1:
                    self._emit(frame)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    # ── tools ───────────────────────────────────────────────────────────────

    def on_tool_start(self, serialized, input_str, *, run_id=None, **kwargs) -> None:
        try:
            name = str((serialized or {}).get("name") or kwargs.get("name") or "tool")
            with self._lock:
                self._tools[run_id] = name
            self._emit({"type": "tool_start", "name": name, "input": _clip(input_str)})
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        try:
            with self._lock:
                name = self._tools.pop(run_id, "") or str(kwargs.get("name") or "")
            self._emit({"type": "tool_end", "name": name, "output": _clip(_text_of(output))})
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        try:
            with self._lock:
                name = self._tools.pop(run_id, "") or str(kwargs.get("name") or "")
            self._emit({"type": "tool_error", "name": name, "error": _clip(error)})
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)


__all__ = ["HubTracer"]
