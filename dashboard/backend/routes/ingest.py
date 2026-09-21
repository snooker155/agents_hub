"""The push half of external connections: runs reported into this hub.

Everything else in this API is called by the operator and drives the product.
This router is the opposite: it is called by a machine that only reports, and
its token says *which* machine. That is an identity, not a permission — the hub
is a single-tenant install with no accounts — and it is what lets the hub file a
report under the right connection and stop listening to one reporter without
touching the others.

It lives apart from ``/api/connections`` (which manages connections, including
issuing those tokens) and ``common.auth`` exempts this prefix from the optional
global API token, so a reporting service never has to be handed a value that
also opens the dashboard.

The contract a client implements::

    POST /api/ingest/runs                  {"input", "thread"?, "title"?, ...}
      -> {"run_id", "session_id"}
    POST /api/ingest/runs/{run_id}/events  {"events": [frame, ...]}
      -> {"accepted", "published"}
    POST /api/ingest/runs/{run_id}/close   {"ok", "output"?, "error"?, "usage"?}
      -> {"status", "usage", "duration_ms"}
    POST /api/ingest/runs/{run_id}/interrupt  {"question", "choices"?, "key"?, "node"?}
      -> the run is parked until a person answers
    GET  /api/ingest/runs/{run_id}/answer     has anyone answered it yet?
    POST /api/ingest/topology              {"framework", "nodes", "edges"}
    GET  /api/ingest/self                  who am I, and what does this hub accept
    POST /api/ingest/v1/traces             OTLP spans, for a team that already
                                           exports them (``connections.otel``)

The frames are the same vocabulary an imported agent streams
(:mod:`common.agent_frames`), so one adapter serves both directions and a run
renders identically whether the hub called the agent or the agent called the
hub.

Events are batched on purpose: a graph that reported one HTTP request per token
would spend more time on this API than on its own work.
"""
from __future__ import annotations

import gzip
import zlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from connections import otel as otel_receiver
from connections import otlp
from connections import service as ingest_service
from connections import store as connection_store

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class OpenRunRequest(BaseModel):
    input: str = ""
    # The run this one continues, when a client resumes a graph that had
    # stopped to ask a question. The hub shows the two as one piece of work
    # without pretending a run stayed open across a human's lunch break.
    resumed_from: Optional[str] = None
    # The client's own grouping id (a conversation, a ticket, a job). Runs that
    # share one land in a single session, the way a chat's turns do.
    thread: Optional[str] = None
    title: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EventsRequest(BaseModel):
    events: List[Dict[str, Any]] = Field(default_factory=list)


class CloseRunRequest(BaseModel):
    ok: Optional[bool] = None
    output: Optional[str] = None
    error: Optional[str] = None
    usage: Dict[str, Any] = Field(default_factory=dict)


class InterruptRequest(BaseModel):
    question: str = ""
    choices: List[str] = Field(default_factory=list)
    # The graph's own id for this interrupt, echoed back with the answer.
    key: str = ""
    node: str = ""
    output: Optional[str] = None


class TopologyRequest(BaseModel):
    framework: str = ""
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)


def _authenticate(authorization: Optional[str], x_connection_token: Optional[str]) -> Dict[str, Any]:
    """Resolve the connection this request speaks for, or refuse it.

    Two header spellings because the clients differ: a tracer library sends a
    bearer token, while a shell script or a webhook configuration is often
    easier to point at a named header.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = token or (x_connection_token or "").strip()

    connection = connection_store.resolve_token(token) if token else None
    if connection is None:
        # One message for a missing, malformed, revoked and disabled token
        # alike: telling them apart tells an attacker which tokens exist.
        raise HTTPException(status_code=401, detail="Invalid or missing connection token")

    try:
        ingest_service.check_rate(connection["id"])
    except ingest_service.IngestError as exc:
        # Throttling happens during authentication, so it has to be turned into
        # a response here; letting it escape would answer a rate limit with a
        # 500 and tell the client to retry harder.
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return connection


def _handle(exc: ingest_service.IngestError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/self")
async def whoami(
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """What this token authenticates, and what this hub accepts from it.

    A client's first call: it verifies the token, the URL and the clock in one
    round trip, and returns the limits so a tracer can size its batches instead
    of discovering them through 413s.
    """
    connection = _authenticate(authorization, x_connection_token)
    return {
        "connection": {
            "id": connection["id"],
            "name": connection["name"],
            "kind": connection["kind"],
            "mode": connection["mode"],
            "workspace": connection["workspace"],
        },
        "limits": {
            "events_per_request": ingest_service.MAX_EVENTS_PER_REQUEST,
            "open_runs": ingest_service.MAX_OPEN_RUNS,
            "requests_per_minute": ingest_service.RATE_LIMIT_PER_MINUTE,
        },
        "frames": [
            "token", "thinking", "tool_start", "tool_end", "tool_error",
            "node_start", "node_end", "interrupt", "usage", "done", "error",
        ],
        # Advertised so a client can discover the other way in without reading
        # the documentation: point an OTLP exporter at this URL and stop there.
        "otlp": {
            "traces": "/api/ingest/v1/traces",
            "encodings": ["application/x-protobuf", "application/json"],
            "max_body_bytes": otel_receiver.MAX_BODY_BYTES,
        },
    }


@router.post("/runs", status_code=201)
async def open_run(
    body: OpenRunRequest,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Start recording a run that is beginning somewhere else."""
    connection = _authenticate(authorization, x_connection_token)
    try:
        opened = ingest_service.open_run(
            connection,
            input_text=body.input,
            thread=body.thread,
            resumed_from=body.resumed_from,
            title=body.title,
            model=body.model,
            provider=body.provider,
            metadata=body.metadata,
        )
    except ingest_service.IngestError as exc:
        raise _handle(exc)
    connection_store.touch(connection["id"])
    return opened


@router.post("/runs/{run_id}/events")
async def report_events(
    run_id: str,
    body: EventsRequest,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Report what the run is doing. One batch, in frame order."""
    connection = _authenticate(authorization, x_connection_token)
    try:
        return ingest_service.ingest_events(connection, run_id, body.events)
    except ingest_service.IngestError as exc:
        raise _handle(exc)


@router.post("/runs/{run_id}/close")
async def close_run(
    run_id: str,
    body: CloseRunRequest,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Finish the run, with the outcome and cost of record."""
    connection = _authenticate(authorization, x_connection_token)
    try:
        closed = ingest_service.close_run(
            connection, run_id,
            ok=body.ok, output=body.output, error=body.error, usage=body.usage,
        )
    except ingest_service.IngestError as exc:
        raise _handle(exc)
    connection_store.touch(connection["id"])
    return closed


@router.post("/runs/{run_id}/interrupt")
async def interrupt_run(
    run_id: str,
    body: InterruptRequest,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Park this run: the graph has stopped to ask a human something."""
    connection = _authenticate(authorization, x_connection_token)
    try:
        parked = ingest_service.interrupt_run(
            connection, run_id,
            question=body.question, choices=body.choices,
            key=body.key, node=body.node, output=body.output,
        )
    except ingest_service.IngestError as exc:
        raise _handle(exc)
    connection_store.touch(connection["id"])
    return parked


@router.get("/runs/{run_id}/answer")
async def read_answer(
    run_id: str,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Has anyone answered this run's question yet?

    Polled by the client, because in this direction the hub never calls out: it
    cannot deliver the answer, so the party that wants it comes and asks. A
    reply of ``answered`` also closes the parked run, so a client that polls
    twice gets the answer once — it must act on the first one.
    """
    connection = _authenticate(authorization, x_connection_token)
    try:
        return ingest_service.answer_for(connection, run_id)
    except ingest_service.IngestError as exc:
        raise _handle(exc)


@router.post("/topology")
async def report_topology(
    body: TopologyRequest,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Report the shape of the graph behind this connection.

    Sent once at startup by a client that knows its own topology, which is how
    an observed graph gets a picture at all: nothing here can go and ask for it,
    because in this direction the hub never calls out.

    Normalised and bounded by the same function that guards a pulled topology —
    it is the same untrusted input arriving by a different road.
    """
    connection = _authenticate(authorization, x_connection_token)
    from agents.remote_agent import normalize_topology

    topology = normalize_topology({
        "framework": body.framework or connection.get("kind") or "",
        "nodes": body.nodes,
        "edges": body.edges,
    })
    connection_store.set_topology(connection["id"], topology)
    return {"connection_id": connection["id"], "topology": topology}


# ── OTLP ─────────────────────────────────────────────────────────────────────

# What an exporter sends when nothing is configured; the path is the one the
# OTLP/HTTP specification fixes, so pointing an exporter at
# ``…/api/ingest`` is the entire integration.
_MAX_DECOMPRESSED = otel_receiver.MAX_BODY_BYTES


def _decompress(body: bytes, encoding: str) -> bytes:
    """Undo an exporter's compression, refusing a body that expands too far.

    A gzip stream can claim any size at all until it is decompressed, so the
    limit is enforced while decompressing rather than on the result.
    """
    encoding = (encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return body
    if encoding not in ("gzip", "deflate"):
        raise HTTPException(status_code=415, detail=f"unsupported content-encoding '{encoding}'")
    try:
        wbits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        stream = zlib.decompressobj(wbits)
        out = stream.decompress(body, _MAX_DECOMPRESSED + 1)
        if len(out) > _MAX_DECOMPRESSED:
            raise HTTPException(
                status_code=413,
                detail=f"compressed payload expands past {_MAX_DECOMPRESSED} bytes")
        return out
    except (zlib.error, OSError, gzip.BadGzipFile) as exc:
        raise HTTPException(status_code=400, detail=f"could not decompress body: {exc}")


@router.post("/v1/traces")
async def receive_traces(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_connection_token: Optional[str] = Header(None),
):
    """Accept OTLP trace spans and record the traces they complete.

    The one integration that costs no code: a team already exporting spans adds
    a second exporter and points it here. The reply is an
    ``ExportTraceServiceResponse`` in the request's own encoding, carrying the
    count of anything dropped — a bare 200 would claim a payload landed whole
    every time a cap bit into it.
    """
    connection = _authenticate(authorization, x_connection_token)

    body = await request.body()
    if len(body) > otel_receiver.MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"payload exceeds {otel_receiver.MAX_BODY_BYTES} bytes")
    body = _decompress(body, request.headers.get("content-encoding", ""))

    content_type = request.headers.get("content-type", "")
    try:
        result = otel_receiver.receive(connection, body, content_type)
    except otlp.OtlpError as exc:
        raise HTTPException(status_code=400, detail=f"malformed OTLP payload: {exc}")
    connection_store.touch(connection["id"])

    rejected = int(result.get("rejected_spans") or 0)
    message = f"{rejected} span(s) dropped by this hub's limits" if rejected else ""
    if "json" in content_type.lower():
        # OTLP/JSON writes uint64 as a string, partial success included.
        partial = {"rejectedSpans": str(rejected), "errorMessage": message} if rejected else {}
        return {"partialSuccess": partial}
    return Response(
        content=otlp.encode_partial_success(rejected, message),
        media_type="application/x-protobuf",
    )
