"""OTLP spans, read as runs.

The lowest-friction way into this product: a team already exporting traces to
LangSmith, Langfuse, Phoenix or a collector adds a second exporter and changes
no code at all. What arrives is not what the tracer sends, and the difference
decides everything here.

**Spans arrive when they end, children first.** There is no "a run started"
signal in OTLP, and the root span — the one that knows the input, the output and
the outcome — is the *last* to be exported, because it ends last. So a trace is
buffered until its root arrives and then recorded in one go, sorted back into
start order. Recording spans as they landed would produce a run whose steps run
backwards, which is worse than a run that appears a few seconds late.

**A trace whose root never comes** (the process died, the root was sampled away,
the exporter dropped a batch) is recorded anyway once it has been quiet for
``TRACE_TTL_SECONDS``, from whatever spans did arrive. Losing the run entirely
would hide exactly the executions most worth seeing.

**It is an import, not a live view.** These runs are back-dated to when the work
actually happened and are complete on arrival; nothing here can be watched in
flight or interrupted. A team that wants that uses the tracer, which reports
while the graph runs. Both land in the same run records, so the two are
comparable, and the same page renders either.

**Whose vocabulary.** OTLP says nothing about agents, so every emitter invents
its own attributes: OpenInference (``openinference.span.kind``, ``input.value``)
for Phoenix and Arize, OpenLLMetry (``traceloop.*``), Langfuse and LangSmith's
own, and the OpenTelemetry GenAI conventions (``gen_ai.*``) that are meant to
replace all of them. The tables below read all of these, because the emitter is
not ours to choose, and fall back to the span tree when none of them is present.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from connections import otlp
from connections import service as ingest_service

# A payload larger than this is refused unread. The default OTLP batch is 512
# spans; this is several of those.
MAX_BODY_BYTES = 4 * 1024 * 1024
# Spans kept for one trace. A graph that emits more than this is instrumented
# far below the level anyone reads a run at.
MAX_SPANS_PER_TRACE = 1000
# Traces buffered per connection at once.
MAX_OPEN_TRACES = 200
# How long a trace waits for its root before it is recorded without one.
TRACE_TTL_SECONDS = 300
# Trace ids remembered after recording, so spans that arrive late (a straggling
# batch, a retry) are dropped instead of opening a second run for the same work.
RECENT_TRACES = 4096

# Clipping for values that end up in a run record. The run payload has its own
# limit; this keeps a single attribute from filling it.
MAX_TEXT = 4000
# An error is a line in a run list, not a document.
MAX_ERROR = 500

STATUS_ERROR = 2


# ── the attribute tables ─────────────────────────────────────────────────────

_KIND_KEYS = ("openinference.span.kind", "traceloop.span.kind", "langsmith.span.kind",
              "langfuse.observation.type", "gen_ai.operation.name", "span.kind")
_TOOL_KINDS = {"tool", "execute_tool"}
_LLM_KINDS = {"llm", "generation", "chat", "text_completion", "embedding", "embeddings"}

_INPUT_KEYS = ("input.value", "gen_ai.prompt", "traceloop.entity.input",
               "gen_ai.input.messages", "llm.input_messages", "input")
_OUTPUT_KEYS = ("output.value", "gen_ai.completion", "traceloop.entity.output",
                "gen_ai.output.messages", "llm.output_messages", "output")
_MODEL_KEYS = ("gen_ai.request.model", "gen_ai.response.model", "llm.model_name",
               "llm.request.model", "model")
_PROVIDER_KEYS = ("gen_ai.provider.name", "gen_ai.system", "llm.provider", "llm.system")
_TOOL_NAME_KEYS = ("tool.name", "gen_ai.tool.name", "traceloop.entity.name")
_SESSION_KEYS = ("session.id", "gen_ai.conversation.id", "langfuse.session.id",
                 "thread.id", "langsmith.metadata.thread_id", "conversation.id")
_NODE_KEYS = ("langgraph.node", "langsmith.metadata.langgraph_node", "gen_ai.agent.name")
_METADATA_KEYS = ("metadata", "langsmith.metadata", "traceloop.association.properties")

_PROMPT_TOKENS = ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens",
                  "llm.token_count.prompt", "llm.usage.prompt_tokens")
_COMPLETION_TOKENS = ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens",
                      "llm.token_count.completion", "llm.usage.completion_tokens")
_TOTAL_TOKENS = ("gen_ai.usage.total_tokens", "llm.token_count.total")
_CACHED_TOKENS = ("gen_ai.usage.cache_read.input_tokens",
                  "llm.token_count.prompt_details.cache_read",
                  "llm.token_count.prompt_details.cache_input")


# ── the buffer ───────────────────────────────────────────────────────────────

class _PendingTrace:
    """One trace, while its spans are still arriving."""

    def __init__(self, connection_id: str, trace_id: str):
        self.connection_id = connection_id
        self.trace_id = trace_id
        self.spans: List[Dict[str, Any]] = []
        self.first_seen = time.time()
        self.touched = time.time()
        self.root_seen = False
        self.dropped = 0


_lock = threading.Lock()
_traces: "OrderedDict[Tuple[str, str], _PendingTrace]" = OrderedDict()
# Insertion-ordered set: the value is never read, only the eviction order.
_recent: "OrderedDict[Tuple[str, str], bool]" = OrderedDict()


def _remember(key: Tuple[str, str]) -> None:
    _recent[key] = True
    while len(_recent) > RECENT_TRACES:
        _recent.popitem(last=False)


def receive(connection: Dict[str, Any], body: bytes, content_type: str = "") -> Dict[str, Any]:
    """Accept one OTLP export. Returns what was taken and what was refused.

    Recording happens outside the lock and after the buffer has been updated,
    so a slow database write never holds up the next exporter's request.
    """
    if len(body) > MAX_BODY_BYTES:
        raise otlp.OtlpError(
            f"payload is {len(body)} bytes; this endpoint accepts {MAX_BODY_BYTES}")

    return record_spans(connection, otlp.decode(body, content_type))


def record_spans(connection: Dict[str, Any], spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """File decoded spans and write the runs they complete.

    Separate from ``receive`` because the wire and the meaning are separate
    problems: this half is what a second receiver — a collector's own format, a
    log of spans replayed — would reuse, and it is where the behaviour worth
    testing lives.
    """
    ready, rejected = _collect(str(connection["id"]), spans)
    recorded = []
    for pending in ready:
        run_id = _record(connection, pending)
        if run_id:
            recorded.append(run_id)
    return {
        "accepted_spans": len(spans) - rejected,
        "rejected_spans": rejected,
        "runs": recorded,
    }


def _collect(connection_id: str, spans: List[Dict[str, Any]]) -> Tuple[List[_PendingTrace], int]:
    """File spans under their traces; return the traces that may now be recorded."""
    now = time.time()
    ready: List[_PendingTrace] = []
    rejected = 0
    with _lock:
        for span in spans:
            trace_id = span.get("trace_id") or ""
            if not trace_id or not span.get("span_id"):
                rejected += 1
                continue
            key = (connection_id, trace_id)
            if key in _recent:
                # The run for this trace is already written. Folding a straggler
                # in would mean rewriting a finished run, and opening a second
                # one would double-count the work.
                rejected += 1
                continue
            pending = _traces.get(key)
            if pending is None:
                pending = _traces[key] = _PendingTrace(connection_id, trace_id)
            is_root = not span.get("parent_span_id")
            if len(pending.spans) >= MAX_SPANS_PER_TRACE and not is_root:
                # The root is always taken: it is the span that says what the
                # run was, how it ended, and that the trace is complete. Letting
                # the cap discard it would strand every over-long trace until it
                # timed out, and record it without its outcome.
                pending.dropped += 1
                rejected += 1
                continue
            pending.spans.append(span)
            pending.touched = now
            if is_root:
                pending.root_seen = True

        # Only this connection's traces: everything the caller gets back is
        # recorded *as* the caller, so another connection's abandoned trace must
        # never be swept up here and filed under the wrong name. Those are
        # ``flush_idle``'s, which looks each one's own connection up.
        for key, pending in list(_traces.items()):
            if pending.connection_id != connection_id:
                continue
            if pending.root_seen or now - pending.touched > TRACE_TTL_SECONDS:
                _traces.pop(key, None)
                _remember(key)
                ready.append(pending)

        # Oldest first, because a trace whose root is lost is the one that will
        # never complete on its own.
        overflow = [k for k, p in _traces.items() if p.connection_id == connection_id]
        while len(overflow) > MAX_OPEN_TRACES:
            key = overflow.pop(0)
            pending = _traces.pop(key, None)
            _remember(key)
            if pending is not None:
                ready.append(pending)
    return ready, rejected


def flush_idle(now: Optional[float] = None) -> Dict[str, int]:
    """Record every connection's traces that waited too long for a root.

    A connection that keeps exporting sweeps its own abandoned traces on its
    next request. This is for the one that stopped: without it, the last trace
    of a graph that died would sit in memory until the process restarted, and
    the run nobody got to see is exactly the interesting one. Called from the
    daily maintenance pass, which is a backstop rather than a timer.
    """
    from connections import store as connection_store

    moment = time.time() if now is None else now
    stale: List[_PendingTrace] = []
    with _lock:
        for key, pending in list(_traces.items()):
            if moment - pending.touched > TRACE_TTL_SECONDS:
                _traces.pop(key, None)
                _remember(key)
                stale.append(pending)

    written = 0
    for pending in stale:
        connection = connection_store.get_connection(pending.connection_id)
        if connection is None:
            # The connection was deleted while its trace waited. Nothing to
            # file the run under, so it goes.
            continue
        if _record(connection, pending):
            written += 1
    return {"flushed_otel_traces": written}


# ── span → run ───────────────────────────────────────────────────────────────

def _attr(span: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    attributes = span.get("attributes") or {}
    for key in keys:
        if key in attributes and attributes[key] not in (None, ""):
            return attributes[key]
    meta = _metadata(span)
    for key in keys:
        short = key.rsplit(".", 1)[-1]
        if short in meta and meta[short] not in (None, ""):
            return meta[short]
    return None


def _metadata(span: Dict[str, Any]) -> Dict[str, Any]:
    """The emitter's own metadata bag, parsed once.

    LangChain's instrumentation puts the interesting part of a LangGraph run —
    the node name, the step number, the thread id — into one attribute holding
    a JSON *string*, so the keys the product cares about are invisible to a
    plain attribute lookup.
    """
    cached = span.get("_metadata")
    if cached is not None:
        return cached
    parsed: Dict[str, Any] = {}
    attributes = span.get("attributes") or {}
    for key in _METADATA_KEYS:
        value = attributes.get(key)
        if isinstance(value, dict):
            parsed.update(value)
        elif isinstance(value, str) and value.startswith("{"):
            try:
                loaded = json.loads(value[:otlp.MAX_VALUE_CHARS])
            except ValueError:
                continue
            if isinstance(loaded, dict):
                parsed.update(loaded)
    span["_metadata"] = parsed
    return parsed


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:MAX_TEXT]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:MAX_TEXT]
    except (TypeError, ValueError):
        return str(value)[:MAX_TEXT]


def _number(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _role(span: Dict[str, Any]) -> str:
    """What kind of work this span did: ``tool``, ``llm`` or ``chain``."""
    declared = str(_attr(span, _KIND_KEYS) or "").strip().lower()
    if declared in _TOOL_KINDS:
        return "tool"
    if declared in _LLM_KINDS:
        return "llm"
    if _attr(span, _TOOL_NAME_KEYS) and declared not in ("chain", "agent"):
        return "tool"
    if any((span.get("attributes") or {}).get(k) is not None
           for k in _PROMPT_TOKENS + _COMPLETION_TOKENS + _MODEL_KEYS[:3]):
        return "llm"
    return "chain"


def _usage(span: Dict[str, Any]) -> Dict[str, int]:
    """One span's token counts, in the frame vocabulary.

    ``cache_read_input_tokens`` because that is one of the spellings the hub's
    own cache accounting reads (``run_statistics.cached_input_tokens``); a
    cached count under any other name is silently priced as a fresh one.
    """
    usage = {
        "prompt_tokens": _number(_attr(span, _PROMPT_TOKENS)),
        "completion_tokens": _number(_attr(span, _COMPLETION_TOKENS)),
        "total_tokens": _number(_attr(span, _TOTAL_TOKENS)),
        "cache_read_input_tokens": _number(_attr(span, _CACHED_TOKENS)),
    }
    return {k: v for k, v in usage.items() if v}


def _error_of(span: Dict[str, Any]) -> str:
    """What went wrong, in one line.

    The exception *event* is read before the status message, although the
    status is where an error is nominally declared. The event is structured —
    a type and a message — while the message is free text, and the
    instrumentation in the field puts a full stacktrace there, run together
    with the exception's repr and no separator to split on. A run record holds
    a reason, not a stacktrace; the stacktrace stays in whatever the team
    already sends its traces to.
    """
    status = span.get("status") or {}
    if status.get("code") != STATUS_ERROR:
        return ""
    for event in span.get("events") or []:
        attributes = event.get("attributes") or {}
        message = str(attributes.get("exception.message") or "").strip()
        kind = str(attributes.get("exception.type") or "").strip()
        if message or kind:
            return f"{kind}: {message}".strip(": ")[:MAX_ERROR]
    message = str(status.get("message") or "").strip()
    return message.split("\n", 1)[0][:MAX_ERROR] if message else "failed"


def _is_node(span: Dict[str, Any], depth: int, role: str) -> bool:
    """Whether this span is a step worth showing in the run's path.

    Two ways to qualify, and the first is the one that matters. A graph that
    names its nodes says so in its metadata, and the span *for* that node is the
    one whose name matches it — every span nested inside a node inherits the
    same metadata, so matching on presence alone would promote each of a node's
    internals into a step of its own.

    Without a declared name, a direct child of the root is taken as a step. That
    is the shape of nearly every agent trace: one root for the whole run and one
    span per thing it did. Deeper spans still contribute their tools and their
    tokens; they just do not each become a line in the path.
    """
    if role != "chain":
        return False
    declared = _attr(span, _NODE_KEYS)
    if isinstance(declared, str) and declared:
        return declared == span.get("name")
    return depth == 1


def _frames(spans: List[Dict[str, Any]], root: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ordered frames a trace's spans translate into.

    Starts and ends are emitted as separate timed events and then sorted, so a
    tool inside a node lands inside that node's window and a subgraph nests the
    way it ran. Emitting each span's start and end together would flatten all of
    that into a list of completed steps.
    """
    by_id = {s["span_id"]: s for s in spans}
    depths: Dict[str, int] = {}

    def depth_of(span: Dict[str, Any], guard: int = 0) -> int:
        span_id = span["span_id"]
        if span_id in depths:
            return depths[span_id]
        parent = by_id.get(span.get("parent_span_id") or "")
        # guard: a malformed payload can describe a parent cycle, and this walk
        # is over data a stranger sent.
        value = 0 if parent is None or guard > 32 else depth_of(parent, guard + 1) + 1
        depths[span_id] = value
        return value

    timed: List[Tuple[int, int, int, Dict[str, Any]]] = []
    for index, span in enumerate(spans):
        if span["span_id"] == root["span_id"]:
            continue
        role = _role(span)
        start, end = span.get("start_ns", 0), max(span.get("end_ns", 0), span.get("start_ns", 0))
        error = _error_of(span)
        if role == "tool":
            name = _text(_attr(span, _TOOL_NAME_KEYS) or span.get("name"))
            timed.append((start, 0, index, {
                "type": "tool_start", "name": name,
                "input": _text(_attr(span, _INPUT_KEYS)),
            }))
            timed.append((end, 1, index, {
                "type": "tool_error" if error else "tool_end", "name": name,
                **({"error": error} if error else
                   {"output": _text(_attr(span, _OUTPUT_KEYS))}),
            }))
        elif role == "llm":
            usage = _usage(span)
            if usage:
                timed.append((end, 1, index, {"type": "usage", **usage}))
        if _is_node(span, depth_of(span), role):
            node = span.get("name") or ""
            timed.append((start, 0, index, {
                "type": "node_start", "node": node, "depth": max(depth_of(span) - 1, 0),
                "input": _text(_attr(span, _INPUT_KEYS)),
            }))
            timed.append((end, 1, index, {
                "type": "node_end", "node": node, "ok": not error,
                "output": _text(_attr(span, _OUTPUT_KEYS)),
                **({"error": error} if error else {}),
            }))

    timed.sort(key=lambda item: (item[0], item[1], item[2]))
    return [frame for _ts, _end, _i, frame in timed]


def _root_of(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The span the run is reported as.

    Normally the one with no parent. A trace recorded without its root — the
    process died, the batch was lost — is reported from its earliest span
    instead, which is the closest thing to the whole run that arrived.
    """
    known = {s["span_id"] for s in spans}
    orphans = [s for s in spans
               if not s.get("parent_span_id") or s["parent_span_id"] not in known]
    candidates = orphans or spans
    return min(candidates, key=lambda s: (s.get("start_ns", 0), s.get("span_id", "")))


def _iso(nanos: int) -> Optional[str]:
    if not nanos:
        return None
    try:
        return datetime.fromtimestamp(nanos / 1e9, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _record(connection: Dict[str, Any], pending: _PendingTrace) -> Optional[str]:
    """Write one buffered trace as a run, through the same calls a tracer uses.

    Deliberately not a second way to create runs: an imported trace goes through
    ``connections.service`` exactly as a reported one does, so it gets the same
    record, the same session, the same live events and the same retention.
    """
    spans = sorted(pending.spans, key=lambda s: (s.get("start_ns", 0), s.get("span_id", "")))
    if not spans:
        return None

    root = _root_of(spans)
    resource = root.get("resource") or {}
    service_name = str(resource.get("service.name") or "")
    thread = _attr(root, _SESSION_KEYS)
    error = _error_of(root)
    started = _iso(root.get("start_ns", 0))
    finished = _iso(max(s.get("end_ns", 0) for s in spans))

    model = _text(_attr(root, _MODEL_KEYS)) or next(
        (_text(_attr(s, _MODEL_KEYS)) for s in spans if _attr(s, _MODEL_KEYS)), "")
    provider = _text(_attr(root, _PROVIDER_KEYS)) or next(
        (_text(_attr(s, _PROVIDER_KEYS)) for s in spans if _attr(s, _PROVIDER_KEYS)), "")

    try:
        opened = ingest_service.open_run(
            connection,
            input_text=_text(_attr(root, _INPUT_KEYS)),
            thread=_text(thread) if thread else None,
            title=(root.get("name") or service_name or "")[:200] or None,
            model=model or None,
            provider=provider or None,
            started_at=started,
            metadata={"otel": {
                "trace_id": pending.trace_id,
                "service": service_name,
                "scope": root.get("scope") or "",
                "spans": len(spans),
                "dropped_spans": pending.dropped,
                # An honest marker on the record itself: this run was imported
                # after it finished, so nobody reads its live view as missing.
                "imported": True,
                "root_reported": bool(pending.root_seen),
            }},
        )
    except ingest_service.IngestError:
        # Out of open-run budget, or the store refused. The trace is already out
        # of the buffer; dropping it is better than retrying into the same wall.
        return None

    run_id = opened["run_id"]
    frames = _frames(spans, root)
    for start in range(0, len(frames), ingest_service.MAX_EVENTS_PER_REQUEST):
        ingest_service.ingest_events(
            connection, run_id, frames[start:start + ingest_service.MAX_EVENTS_PER_REQUEST])

    # The work's own duration, from its spans. Measuring it here would time the
    # import instead, which is the one number nobody wants.
    span_ms = max(int((max(s.get("end_ns", 0) for s in spans) - root.get("start_ns", 0)) / 1e6), 0)
    ingest_service.close_run(
        connection, run_id,
        ok=not error,
        output=_text(_attr(root, _OUTPUT_KEYS)),
        error=error or None,
        finished_at=finished,
        duration_ms=span_ms,
    )
    return run_id


def reset_for_tests() -> None:
    with _lock:
        _traces.clear()
        _recent.clear()


def buffered_traces() -> int:
    with _lock:
        return len(_traces)


__all__ = ["MAX_BODY_BYTES", "MAX_OPEN_TRACES", "MAX_SPANS_PER_TRACE", "TRACE_TTL_SECONDS",
           "buffered_traces", "flush_idle", "receive", "record_spans", "reset_for_tests"]
