"""A LangGraph graph behind the Agents Hub HTTP contract, without touching it.

The situation this exists for: a team already has a working LangGraph flow, in
their repository, with their prompts, their models, their checkpointer and their
deployment. They want the hub's chat, run records, cost accounting and live
view. They do not want their graph moved into the hub, rewritten as a hub flow,
or edited at all.

So nothing here imports *their* code at build time. The graph is named at
startup with ``AGENTHUB_GRAPH``, in the same ``module:attribute`` spelling
LangGraph's own ``langgraph.json`` uses::

    AGENTHUB_GRAPH=my_project.agent:graph

which means the integration is one process, one env var and zero diffs in the
graph. Point it at a compiled graph (``StateGraph.compile()``) or at a
zero-argument factory returning one; both are common ways a repository exposes
its graph, and guessing wrong would be the first support question.

What it serves, the contract from ``agents/remote_agent.py``:

    GET  /health        liveness, plus what graph was loaded
    POST /run           {"prompt","run_id","workspace"} -> {"ok","output","error","steps"},
                        or {"ok","interrupt":{…}} when the graph stopped to ask
    POST /run/stream    same body -> NDJSON frames
    POST /resume        {"run_id","value"} -> the same frames, continuing a pause
    GET  /graph         the graph's own topology, declared as runtime.graph_path

The hub's ``run_id`` is used as the graph's ``thread_id``, which is what makes
``/resume`` possible at all: the checkpointer finds the suspended run by it. A
graph compiled without a checkpointer cannot pause, and this adapter serves it
exactly as before.

The interesting half is the translation in :func:`translate`. LangGraph already
narrates itself through ``astream_events``: every model token, every tool call,
every node entry and exit arrives as an event with the node name in its
metadata. Those events map almost one to one onto the frame vocabulary the hub
already renders, so a graph that was never built for this hub streams into it
exactly like a built-in agent.

Node boundaries are sent as ``node_start``/``node_end``. The hub renders them as
steps inside this agent's reply and, with ``/graph`` below, lights the matching
node on a read-only mirror of the graph. A hub old enough not to know those
frames ignores them rather than failing (``RemoteAgent._handle_stream_event``
drops unknown types), so declaring them is safe either way.
"""
from __future__ import annotations

import importlib
import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="LangGraph — Agents Hub adapter")

# Which graph to serve, as module:attribute. No default: an adapter that
# silently served the bundled demo graph instead of the graph someone meant to
# point it at would be a confusing way to lose an afternoon.
GRAPH_SPEC = (os.environ.get("AGENTHUB_GRAPH") or "").strip()

# How the prompt enters the graph's state. Most message graphs take
# ``{"messages": [...]}``; a graph with a custom state takes its own key, and
# then the prompt is passed as that key's plain string value.
INPUT_KEY = (os.environ.get("AGENTHUB_INPUT_KEY") or "messages").strip()

# Where the answer is read from in the final state. Empty means "same as the
# input key", which is right for message graphs and for most single-key states.
OUTPUT_KEY = (os.environ.get("AGENTHUB_OUTPUT_KEY") or "").strip()

# Tool output can be a whole retrieved document. The frame is for a human
# reading a run, not for reprocessing, so it is bounded.
MAX_FRAME_CHARS = int(os.environ.get("AGENTHUB_MAX_FRAME_CHARS", "4000"))


class RunRequest(BaseModel):
    prompt: str
    run_id: Optional[str] = None
    workspace: Optional[str] = None


# ── loading the graph ────────────────────────────────────────────────────────

_graph: Any = None


def load_graph() -> Any:
    """Import the graph named by ``AGENTHUB_GRAPH`` and cache it.

    Cached because importing a real project's graph module builds its models,
    its tool clients and sometimes its checkpointer connection. Doing that per
    request would add seconds to every run and open a connection pool per call.
    """
    global _graph
    if _graph is not None:
        return _graph
    if not GRAPH_SPEC:
        raise RuntimeError(
            "AGENTHUB_GRAPH is not set. Point it at your compiled graph, "
            "for example AGENTHUB_GRAPH=my_project.agent:graph"
        )
    if ":" not in GRAPH_SPEC:
        raise RuntimeError(
            f"AGENTHUB_GRAPH must be 'module:attribute', got '{GRAPH_SPEC}'"
        )
    module_name, attr = GRAPH_SPEC.split(":", 1)
    obj = getattr(importlib.import_module(module_name), attr.strip())
    # A repository may export the compiled graph, or a factory that builds it.
    # ``ainvoke`` is what tells the two apart without calling anything.
    if not hasattr(obj, "ainvoke") and callable(obj):
        obj = obj()
    if not hasattr(obj, "astream_events"):
        raise RuntimeError(
            f"'{GRAPH_SPEC}' is not a compiled LangGraph graph "
            f"(no astream_events). Did you export the StateGraph instead of "
            f"the result of .compile()?"
        )
    _graph = obj
    return _graph


def _graph_input(prompt: str) -> Dict[str, Any]:
    """Build the graph's input state from one prompt."""
    if INPUT_KEY == "messages":
        return {"messages": [("user", prompt)]}
    return {INPUT_KEY: prompt}


def _graph_config(req: RunRequest) -> Dict[str, Any]:
    """Config for one run.

    ``thread_id`` is set from the hub's run id so a graph with a checkpointer
    keeps each hub run in its own thread instead of appending every run in the
    product to one shared conversation. ``workspace`` is passed through
    untouched: the hub knows what it means, this adapter does not.
    """
    return {
        "configurable": {
            "thread_id": req.run_id or "agenthub",
            "agenthub_run_id": req.run_id,
            "agenthub_workspace": req.workspace,
        }
    }


def _text_of(message: Any) -> str:
    """Best-effort text of a message or state value.

    Content can be a string or the list-of-blocks shape the newer chat models
    return; both have to render, because which one a graph produces depends on
    the provider it was configured with, not on anything visible here.
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


def _final_output(state: Any) -> str:
    """Pull the answer out of the graph's final state."""
    key = OUTPUT_KEY or INPUT_KEY
    if not isinstance(state, dict):
        return _text_of(state)
    value = state.get(key, state.get("messages"))
    if isinstance(value, list) and value:
        return _text_of(value[-1])
    return _text_of(value)


# ── event translation ────────────────────────────────────────────────────────

class StreamState:
    """What one streamed run accumulates while it is translated.

    ``depth`` exists because a subgraph re-emits node events of its own. Without
    tracking the checkpoint namespace, a graph containing a subgraph would
    report the inner nodes as if they were top-level, and the run would read as
    a flat list of nodes that do not match the picture of the graph.
    """

    def __init__(self) -> None:
        self.text_parts: List[str] = []
        self.node_stack: List[str] = []
        self.usage_sent = False
        # The graph's final state, captured from the root chain event. Preferred
        # over the concatenated tokens because a graph may post-process what the
        # model said, and because a graph whose model does not stream produces
        # no tokens at all while still answering perfectly well.
        self.final_state: Any = None
        self.last_message: str = ""
        # A finished node's frame, held back until the next node starts so it
        # can name the branch that was taken. LangGraph does not announce the
        # routing decision itself: the only evidence of which way a conditional
        # edge went is which node runs next, and a graph's branches are the main
        # thing someone watching it wants answered. The wait is the microseconds
        # between one node returning and the next being entered.
        self.pending_end: Optional[Dict[str, Any]] = None

    def flush_end(self, next_node: str = "", next_depth: int = -1) -> List[Dict[str, Any]]:
        """Release the held ``node_end``, naming the next node when it fits.

        ``next`` is only filled for a node at the same depth: a subgraph's last
        node is not followed by its sibling but by whatever comes after the
        subgraph, and claiming otherwise would draw an edge that does not exist.
        """
        frame, self.pending_end = self.pending_end, None
        if frame is None:
            return []
        if next_node and next_depth == frame.pop("_depth", -1):
            frame["next"] = next_node
        frame.pop("_depth", None)
        return [frame]

    def output(self) -> str:
        """The answer, from the most authoritative source that has one."""
        if self.final_state is not None:
            text = _final_output(self.final_state)
            if text:
                return text
        joined = "".join(self.text_parts).strip()
        return joined or self.last_message


def _frame(payload: Dict[str, Any]) -> str:
    """One NDJSON frame. The hub accepts NDJSON or SSE; this is the shorter one."""
    return json.dumps(payload, ensure_ascii=False, default=str) + "\n"


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= MAX_FRAME_CHARS else text[:MAX_FRAME_CHARS] + "…"


def _node_of(event: Dict[str, Any]) -> str:
    meta = event.get("metadata") or {}
    return str(meta.get("langgraph_node") or "")


def _is_node_boundary(event: Dict[str, Any]) -> bool:
    """Whether this chain event is a graph node starting or finishing.

    LangGraph runs every node as a chain, so ``on_chain_start`` fires for the
    node, for the runnable inside it and for each nested chain. Only the event
    whose *name* is the node's own name is the node boundary; the rest are its
    internals, which belong in the run log, not on the graph picture.
    """
    node = _node_of(event)
    return bool(node) and str(event.get("name") or "") == node


def _usage_of(message: Any) -> Dict[str, int]:
    """Token counts a model reported, in the hub's spelling.

    This is the only place the numbers exist: the model call happens in this
    process, so a run the hub launched has no other way to learn what it cost.
    ``usage_metadata`` is the modern, provider-independent field; the older
    ``response_metadata['token_usage']`` is still what some integrations fill,
    so both are read.
    """
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        details = usage.get("input_token_details") or {}
        out = {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
        if isinstance(details, dict) and details.get("cache_read"):
            out["cached_tokens"] = int(details["cache_read"])
        return out
    legacy = (getattr(message, "response_metadata", None) or {}).get("token_usage") or {}
    if isinstance(legacy, dict) and legacy:
        return {
            "prompt_tokens": int(legacy.get("prompt_tokens") or 0),
            "completion_tokens": int(legacy.get("completion_tokens") or 0),
            "total_tokens": int(legacy.get("total_tokens") or 0),
        }
    return {}


def translate(event: Dict[str, Any], state: StreamState) -> List[Dict[str, Any]]:
    """Map one LangGraph event onto zero or more hub frames.

    Kept pure, and separate from the HTTP layer, so it can be unit tested
    against recorded events and reused by the push-mode tracer, which needs the
    same mapping over the same events from inside a graph the hub never called.
    """
    kind = str(event.get("event") or "")
    data = event.get("data") or {}
    frames: List[Dict[str, Any]] = []

    if kind == "on_chat_model_stream":
        token = _text_of(data.get("chunk"))
        if token:
            state.text_parts.append(token)
            frames.append({"type": "token", "token": token})
        return frames

    if kind == "on_chat_model_end":
        message = data.get("output")
        text = _text_of(message)
        if text:
            state.last_message = text
        usage = _usage_of(message)
        if usage:
            state.usage_sent = True
            frames.append({"type": "usage", **usage})
        return frames

    if kind == "on_tool_start":
        frames.append({
            "type": "tool_start",
            "name": event.get("name") or "tool",
            "input": _clip(json.dumps(data.get("input"), ensure_ascii=False, default=str)),
        })
        return frames

    if kind == "on_tool_end":
        frames.append({
            "type": "tool_end",
            "name": event.get("name") or "tool",
            "output": _clip(_text_of(data.get("output"))),
        })
        return frames

    # The root chain end carries the graph's final state. Depth is read from
    # ``parent_ids``: the root event is the only one with no parents, and
    # matching on that rather than on the graph's name works whatever the graph
    # is called.
    if kind == "on_chain_end" and not (event.get("parent_ids") or []):
        state.final_state = data.get("output")
        return frames

    if kind == "on_chain_start" and _is_node_boundary(event):
        node = _node_of(event)
        state.node_stack.append(node)
        depth = len(state.node_stack) - 1
        frames.extend(state.flush_end(next_node=node, next_depth=depth))
        frames.append({"type": "node_start", "node": node, "depth": depth})
        return frames

    if kind == "on_chain_end" and _is_node_boundary(event):
        node = _node_of(event)
        depth = max(0, len(state.node_stack) - 1)
        if state.node_stack and state.node_stack[-1] == node:
            state.node_stack.pop()
        # Two nodes cannot be pending at once: a node ends before the next one
        # starts, and the start is what releases the previous end.
        frames.extend(state.flush_end())
        state.pending_end = {"type": "node_end", "node": node, "ok": True, "_depth": depth}
        return frames

    if kind == "on_chain_error" and _is_node_boundary(event):
        # A failed node has no branch to report — nothing ran next.
        frames.extend(state.flush_end())
        frames.append({
            "type": "node_end",
            "node": _node_of(event),
            "ok": False,
            "error": str(data.get("error") or event.get("error") or ""),
        })
        return frames

    return frames


# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness, plus enough to debug a graph that did not load."""
    try:
        graph = load_graph()
    except Exception as exc:  # noqa: BLE001 - reported, not raised: this is the probe
        return {"status": "error", "graph": GRAPH_SPEC, "error": f"{type(exc).__name__}: {exc}"}
    try:
        nodes = len(graph.get_graph().nodes)
    except Exception:  # noqa: BLE001
        nodes = None
    return {"status": "ok", "graph": GRAPH_SPEC, "nodes": nodes, "input_key": INPUT_KEY}


@app.get("/graph")
def graph_topology() -> Dict[str, Any]:
    """The graph's own shape, so the hub can draw it instead of guessing.

    LangGraph knows its topology and will hand it over
    (``get_graph().to_json()``), which is what makes a *read-only mirror* of a
    foreign graph cheap: the hub never parses anyone's Python to learn the
    picture, it asks the graph.

    Normalised here rather than in the hub, because the hub should not learn
    LangGraph's JSON shape to support one framework.
    """
    try:
        raw = load_graph().get_graph().to_json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "nodes": [], "edges": []}

    nodes = []
    for node in raw.get("nodes") or []:
        node_id = str(node.get("id"))
        nodes.append({
            "id": node_id,
            "label": str(node.get("name") or node_id),
            # __start__/__end__ are LangGraph's own terminals. Marking them lets
            # a renderer draw them as terminals rather than as work.
            "kind": "terminal" if node_id in ("__start__", "__end__") else "node",
        })
    edges = []
    for edge in raw.get("edges") or []:
        edges.append({
            "source": str(edge.get("source")),
            "target": str(edge.get("target")),
            "label": str(edge.get("data") or "") or None,
            # A conditional edge is a branch the run may or may not take, which
            # is exactly what someone watching wants to see resolved live.
            "conditional": bool(edge.get("conditional")),
        })
    return {"ok": True, "framework": "langgraph", "nodes": nodes, "edges": edges}


@app.post("/run")
async def run(req: RunRequest) -> Dict[str, Any]:
    """Run the graph once and report the outcome.

    Failures come back as ``{"ok": false, "error": ...}`` with HTTP 200: the hub
    records both identically, and the body keeps the graph's own explanation,
    which a bare 500 would throw away.
    """
    try:
        graph = load_graph()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"{type(exc).__name__}: {exc}"}

    try:
        state = await graph.ainvoke(_graph_input(req.prompt), config=_graph_config(req))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": "", "error": f"{type(exc).__name__}: {exc}"}

    pending = _pending_interrupt(graph, req.run_id)
    if pending is not None:
        # Paused, not finished, and this is the path a run takes when nobody is
        # streaming it. Saying "ok, here is your answer" here would leave the
        # graph suspended with nothing that will ever answer it.
        return {
            "ok": True,
            "status": "awaiting_input",
            "interrupt": {k: v for k, v in pending.items() if k != "type"},
            "output": pending.get("question") or "",
            "error": None,
        }

    output = _final_output(state)
    return {
        "ok": True,
        "output": output or "The graph finished without producing output.",
        "error": None,
    }


def _pending_interrupt(graph: Any, run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """What the graph stopped to ask, or None if it ran to the end.

    Asked of the graph rather than read from the stream, because the stream does
    not carry it: LangGraph adds ``__interrupt__`` to what ``invoke`` *returns*,
    and the suspended node simply never ends. The checkpointer knows, and the
    hub's run id is the thread it is filed under.
    """
    if graph is None or not run_id:
        return None
    try:
        snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
    except Exception:
        return None

    interrupts = list(getattr(snapshot, "interrupts", None) or [])
    if not interrupts:
        for task in getattr(snapshot, "tasks", None) or []:
            interrupts.extend(getattr(task, "interrupts", None) or [])
    if not interrupts:
        return None

    value = getattr(interrupts[0], "value", interrupts[0])
    key = str(getattr(interrupts[0], "id", "") or "")
    nxt = getattr(snapshot, "next", None) or ()
    frame: Dict[str, Any] = {"type": "interrupt", "key": key, "node": str(nxt[0]) if nxt else ""}
    if isinstance(value, dict):
        question = value.get("question") or value.get("prompt") or value.get("message")
        frame["question"] = _clip(question if question is not None else value)
        frame["choices"] = [str(c)[:200] for c in (value.get("choices") or value.get("options") or [])]
    else:
        frame["question"] = _clip(value)
        frame["choices"] = []
    return frame


async def _stream(req: RunRequest) -> AsyncIterator[str]:
    """Translate one graph execution into NDJSON frames.

    The ``done`` frame is authoritative for the hub, so it is sent on every
    path, including failure: a run must have exactly one outcome even when the
    stream was noisy or died halfway.
    """
    try:
        graph = load_graph()
    except Exception as exc:  # noqa: BLE001
        yield _frame({"type": "done", "ok": False, "output": "",
                      "error": f"{type(exc).__name__}: {exc}"})
        return

    state = StreamState()
    try:
        async for event in graph.astream_events(_graph_input(req.prompt), config=_graph_config(req)):
            for payload in translate(event, state):
                yield _frame(payload)
    except Exception as exc:  # noqa: BLE001
        for payload in state.flush_end():
            yield _frame(payload)
        # Partial output is kept: a graph that died in its last node has usually
        # already shown the user real work, and discarding it helps nobody.
        yield _frame({
            "type": "done", "ok": False,
            "output": state.output(),
            "error": f"{type(exc).__name__}: {exc}",
        })
        return

    # The last node of a run has nothing following it, so its held frame is
    # released here without a branch.
    for payload in state.flush_end():
        yield _frame(payload)

    pending = _pending_interrupt(graph, req.run_id)
    if pending is not None:
        # Paused, not finished. No ``done`` frame: the hub parks the run with
        # this question on it and waits for a person, and /resume picks it up.
        if state.node_stack:
            yield _frame({"type": "node_end", "node": state.node_stack[-1],
                          "status": "interrupted", "ok": True})
        yield _frame(pending)
        return

    yield _frame({
        "type": "done",
        "ok": True,
        "output": state.output() or "The graph finished without producing output.",
        "error": None,
    })


class ResumeRequest(BaseModel):
    run_id: str
    value: Any = None
    key: str = ""
    workspace: Optional[str] = None


async def _resume_stream(req: ResumeRequest) -> AsyncIterator[str]:
    """Continue a suspended run and stream what the graph does next."""
    try:
        graph = load_graph()
    except Exception as exc:  # noqa: BLE001
        yield _frame({"type": "done", "ok": False, "output": "",
                      "error": f"{type(exc).__name__}: {exc}"})
        return

    try:
        from langgraph.types import Command
    except ImportError as exc:  # pragma: no cover - langgraph is a hard dep here
        yield _frame({"type": "done", "ok": False, "output": "", "error": str(exc)})
        return

    config = {"configurable": {"thread_id": req.run_id, "agenthub_run_id": req.run_id}}
    state = StreamState()
    try:
        async for event in graph.astream_events(Command(resume=req.value), config=config):
            for payload in translate(event, state):
                yield _frame(payload)
    except Exception as exc:  # noqa: BLE001
        for payload in state.flush_end():
            yield _frame(payload)
        yield _frame({"type": "done", "ok": False, "output": state.output(),
                      "error": f"{type(exc).__name__}: {exc}"})
        return

    for payload in state.flush_end():
        yield _frame(payload)

    # A graph may stop again on the way: an approval flow with two approvals is
    # an ordinary thing, and each pause is reported the same way as the first.
    pending = _pending_interrupt(graph, req.run_id)
    if pending is not None:
        if state.node_stack:
            yield _frame({"type": "node_end", "node": state.node_stack[-1],
                          "status": "interrupted", "ok": True})
        yield _frame(pending)
        return

    yield _frame({
        "type": "done",
        "ok": True,
        "output": state.output() or "The graph finished without producing output.",
        "error": None,
    })


@app.post("/resume")
async def resume(req: ResumeRequest) -> StreamingResponse:
    """Hand a paused run the answer it was waiting for.

    The run id is the thread the checkpointer filed the suspension under, which
    is why the hub sends the id of the run that paused rather than a new one.
    """
    return StreamingResponse(
        _resume_stream(req),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/run/stream")
async def run_stream(req: RunRequest) -> StreamingResponse:
    """Stream one graph execution as NDJSON frames."""
    return StreamingResponse(
        _stream(req),
        media_type="application/x-ndjson",
        # A buffering proxy in front of this would defeat streaming entirely,
        # and the symptom (everything arrives at the end) looks like a bug here.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
