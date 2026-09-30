"""OTLP spans, read as runs.

The integration that costs a team no code at all: an exporter they already run,
pointed at a second address. What it costs *this* side is trusting someone
else's vocabulary, so the tests are built on a payload a real exporter really
sent — ``tests/fixtures/otlp_langgraph.{bin,json}``, captured from
opentelemetry-sdk with OpenInference's LangChain instrumentation running two
LangGraph graphs, one of which raises. Hand-written spans would only prove that
the decoder agrees with whatever the test author remembered about OTLP.

The fixture holds two traces in one export:

* ``LangGraph`` → ``plan`` → ``price`` → the ``lookup_price`` tool: four spans.
* ``LangGraph`` → ``boom``, which raises ``RuntimeError('no such sku')``.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from connections import otel, otlp
from connections import service as ingest_service
from connections import store as connection_store
from managers import run_manager

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROTOBUF = (FIXTURES / "otlp_langgraph.bin").read_bytes()
JSON_PAYLOAD = (FIXTURES / "otlp_langgraph.json").read_bytes()


@pytest.fixture(autouse=True)
def isolated_connections():
    """No in-memory state carried between tests.

    The connections themselves live in the database, and the autouse
    ``fresh_db`` fixture already gives every test its own empty one.
    """
    ingest_service.reset_for_tests()
    otel.reset_for_tests()
    yield
    ingest_service.reset_for_tests()
    otel.reset_for_tests()


@pytest.fixture
def connection():
    record, _token = connection_store.create_connection(
        connection_id="billing-graph", name="Billing graph", kind="langgraph")
    return record


@pytest.fixture
def api_client():
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from routes import connections as connections_routes
    from routes import ingest as ingest_routes

    app = FastAPI()
    app.include_router(connections_routes.router)
    app.include_router(ingest_routes.router)
    return TestClient(app)


def _span(trace_id="t" * 32, span_id="s" * 16, parent="", name="span",
          start=1_000_000_000_000, end=1_001_000_000_000, attributes=None,
          events=None, status=None, resource=None):
    """A decoded span. Used where the *shape* is the subject, not the wire."""
    return {
        "trace_id": trace_id, "span_id": span_id, "parent_span_id": parent,
        "name": name, "kind": 1, "start_ns": start, "end_ns": end,
        "attributes": attributes or {}, "events": events or [],
        "status": status or {"code": 0, "message": ""},
        "resource": resource or {"service.name": "billing-graph"}, "scope": "test",
    }


def _runs_of(connection_id):
    return [r for r in run_manager.load_runs() if r.get("agent_id") == connection_id]


# ── the wire ────────────────────────────────────────────────────────────────

def test_a_real_exporters_payload_decodes_to_the_spans_it_described():
    spans = otlp.decode(PROTOBUF, "application/x-protobuf")

    assert sorted(s["name"] for s in spans) == [
        "LangGraph", "LangGraph", "boom", "lookup_price", "plan", "price"]
    price = next(s for s in spans if s["name"] == "price")
    assert price["attributes"]["openinference.span.kind"] == "CHAIN"
    assert price["start_ns"] > 0 and price["end_ns"] >= price["start_ns"]
    assert price["resource"]["service.name"] == "billing-graph"
    # The hierarchy has to survive: it is what tells a node from a tool inside it.
    root = next(s for s in spans if s["name"] == "lookup_price")
    assert root["parent_span_id"] == price["span_id"]


def test_the_two_encodings_describe_the_same_trace():
    """The Python exporter speaks protobuf and the JavaScript one speaks JSON.

    A hub that read them differently would render the same graph two ways
    depending on which language the team wrote it in.
    """
    from_proto = otlp.decode(PROTOBUF, "application/x-protobuf")
    from_json = otlp.decode(JSON_PAYLOAD, "application/json")

    def comparable(spans):
        return sorted((s["span_id"], s["name"], s["start_ns"],
                       json.dumps(s["attributes"], sort_keys=True)) for s in spans)

    assert comparable(from_proto) == comparable(from_json)


def test_ids_are_read_whether_they_arrive_as_hex_or_base64():
    """OTLP/JSON says hex; protobuf's own JSON printer says base64. Both ship."""
    as_base64 = json.loads(JSON_PAYLOAD)
    as_hex = json.loads(JSON_PAYLOAD)
    for resource_spans in as_hex["resourceSpans"]:
        for scope_spans in resource_spans["scopeSpans"]:
            for span in scope_spans["spans"]:
                for key in ("traceId", "spanId", "parentSpanId"):
                    if span.get(key):
                        span[key] = base64.b64decode(span[key]).hex()

    hex_ids = sorted(s["span_id"] for s in otlp.decode(json.dumps(as_hex).encode(), "application/json"))
    b64_ids = sorted(s["span_id"] for s in otlp.decode(json.dumps(as_base64).encode(), "application/json"))
    assert hex_ids == b64_ids
    assert all(len(i) == 16 for i in hex_ids)


def test_the_encoding_is_sniffed_when_the_client_does_not_say():
    assert len(otlp.decode(PROTOBUF)) == 6
    assert len(otlp.decode(JSON_PAYLOAD)) == 6


def test_a_malformed_payload_is_refused_rather_than_guessed_at():
    with pytest.raises(otlp.OtlpError):
        otlp.decode(b"\x0a\xff\xff\xff\x7f", "application/x-protobuf")
    with pytest.raises(otlp.OtlpError):
        otlp.decode(b"{not json", "application/json")
    with pytest.raises(otlp.OtlpError):
        # A varint that never terminates: the loop has to end on its own.
        otlp.decode(b"\x08" + b"\xff" * 64, "application/x-protobuf")


def test_a_payload_cannot_carry_more_spans_than_the_hub_will_read(monkeypatch):
    monkeypatch.setattr(otlp, "MAX_SPANS", 2)
    assert len(otlp.decode(PROTOBUF, "application/x-protobuf")) == 2


def test_an_attribute_cannot_carry_a_document(monkeypatch):
    monkeypatch.setattr(otlp, "MAX_VALUE_CHARS", 16)
    spans = otlp.decode(PROTOBUF, "application/x-protobuf")
    assert all(len(str(v)) <= 16 for s in spans for v in s["attributes"].values()
               if isinstance(v, str))


def test_partial_success_is_encoded_as_the_protocol_defines_it():
    """A 200 with an empty body claims everything landed; when a cap bit, it did not."""
    assert otlp.encode_partial_success(0, "") == b""
    encoded = otlp.encode_partial_success(3, "dropped")
    assert encoded.startswith(b"\x0a")
    assert b"dropped" in encoded
    # field 1 of the inner message is the rejected count
    assert encoded[2:4] == b"\x08\x03"


# ── the translation ─────────────────────────────────────────────────────────

def test_a_trace_becomes_an_ordinary_run(connection):
    otel.receive(connection, PROTOBUF, "application/x-protobuf")

    runs = _runs_of("billing-graph")
    assert len(runs) == 2, "one run per trace"
    good = next(r for r in runs if r["status"] == "completed")
    assert good["graph_path"] == ["plan", "price"], "in the order the work happened"
    assert good["origin"] == "ingest" and good["connection_id"] == "billing-graph"

    payload = run_manager.get_run_process(good["run_id"]) or {}
    assert [c["tool"] for c in payload["tool_calls"]] == ["lookup_price"]
    assert "widget" in payload["tool_calls"][0]["input"]
    assert payload["tool_calls"][0]["output"] == "widget: 42.00"
    assert payload["input_context"]["user_message"] == "widget"
    assert "plan for widget" in payload["response"]["text"]


def test_a_run_is_only_recorded_once_its_root_span_arrives(connection):
    """Spans are exported when they end, so the root is last. A run written from
    the children alone would be missing its input, its answer and its outcome."""
    spans = otlp.decode(PROTOBUF, "application/x-protobuf")
    good = [s for s in spans if s["trace_id"] == next(
        s["trace_id"] for s in spans if s["name"] == "plan")]
    children = [s for s in good if s["parent_span_id"]]
    root = next(s for s in good if not s["parent_span_id"])

    otel.record_spans(connection, children)
    assert _runs_of("billing-graph") == []
    assert otel.buffered_traces() == 1

    otel.record_spans(connection, [root])
    runs = _runs_of("billing-graph")
    assert len(runs) == 1 and runs[0]["graph_path"] == ["plan", "price"]
    assert otel.buffered_traces() == 0


def test_a_tool_lands_inside_the_node_that_called_it(connection):
    """Starts and ends are sorted as separate moments, so the nesting survives.

    Recording each span as one finished step instead would flatten a graph into
    a list and lose which node a tool belonged to.
    """
    spans = [s for s in otlp.decode(PROTOBUF, "application/x-protobuf")
             if s["name"] in ("plan", "price", "lookup_price", "LangGraph")]
    trace_id = next(s["trace_id"] for s in spans if s["name"] == "price")
    spans = [s for s in spans if s["trace_id"] == trace_id]
    root = otel._root_of(spans)

    kinds = [(f["type"], f.get("node") or f.get("name")) for f in otel._frames(spans, root)]
    assert kinds == [
        ("node_start", "plan"), ("node_end", "plan"),
        ("node_start", "price"),
        ("tool_start", "lookup_price"), ("tool_end", "lookup_price"),
        ("node_end", "price"),
    ]


def test_a_node_is_the_span_that_names_itself_one(connection):
    """Every span inside a node inherits the node's metadata, so presence alone
    would promote each of a node's internals into a step of the graph."""
    spans = otlp.decode(PROTOBUF, "application/x-protobuf")
    tool = next(s for s in spans if s["name"] == "lookup_price")
    node = next(s for s in spans if s["name"] == "price")

    assert otel._metadata(tool)["langgraph_node"] == "price", "inherited, not its own"
    assert otel._is_node(tool, 2, "tool") is False
    assert otel._is_node(node, 1, "chain") is True


def test_a_span_with_no_declared_node_name_counts_if_the_root_called_it():
    """Most agent traces are one root and one span per thing it did."""
    deep = _span(span_id="d" * 16, parent="c" * 16, name="inner")
    direct = _span(span_id="c" * 16, parent="r" * 16, name="retrieve")

    assert otel._is_node(direct, 1, "chain") is True
    assert otel._is_node(deep, 2, "chain") is False


def test_tokens_are_credited_from_either_convention(connection):
    """``gen_ai.*`` is the standard; ``llm.token_count.*`` is what the
    instrumentation most teams actually run emits."""
    trace_id = "a" * 32
    root = _span(trace_id=trace_id, span_id="r" * 16, name="chat",
                 attributes={"input.value": "hi", "output.value": "there"})
    gen_ai = _span(trace_id=trace_id, span_id="1" * 16, parent="r" * 16, name="openai.chat",
                   attributes={"gen_ai.operation.name": "chat",
                               "gen_ai.request.model": "gpt-4o-mini",
                               "gen_ai.provider.name": "openai",
                               "gen_ai.usage.input_tokens": 1200,
                               "gen_ai.usage.output_tokens": 300,
                               "gen_ai.usage.cache_read.input_tokens": 800})
    openinference = _span(trace_id=trace_id, span_id="2" * 16, parent="r" * 16, name="anthropic",
                          attributes={"openinference.span.kind": "LLM",
                                      "llm.model_name": "claude",
                                      "llm.token_count.prompt": 10,
                                      "llm.token_count.completion": 5})

    otel.record_spans(connection, [gen_ai, openinference, root])

    run = _runs_of("billing-graph")[0]
    usage = (run_manager.get_run_process(run["run_id"]) or {})["token_usage"]
    assert usage["inbound_tokens"] == 1210
    assert usage["outbound_tokens"] == 305
    assert usage["cached_tokens"] == 800
    assert run["model"] == "gpt-4o-mini" and run["provider"] == "openai"


def test_a_failed_trace_is_a_failed_run_with_a_reason_not_a_stacktrace(connection):
    otel.receive(connection, PROTOBUF, "application/x-protobuf")

    failed = next(r for r in _runs_of("billing-graph") if r["status"] == "failed")
    assert failed["error"] == "RuntimeError: no such sku"
    assert "Traceback" not in failed["error"]
    assert failed["graph_path"] == ["boom"]


def test_a_run_imported_late_keeps_the_time_it_actually_ran(connection):
    """Spans arrive after the work is over. A run list in which every imported
    run happened just now and took no time is worse than no timestamps."""
    long_ago = int((time.time() - 3600) * 1e9)
    root = _span(span_id="r" * 16, name="nightly", start=long_ago,
                 end=long_ago + 45_000_000_000)
    child = _span(span_id="c" * 16, parent="r" * 16, name="step",
                  start=long_ago + 1_000_000_000, end=long_ago + 44_000_000_000)

    otel.record_spans(connection, [child, root])

    run = _runs_of("billing-graph")[0]
    assert run["started_at"].startswith(
        time.strftime("%Y-%m-%dT%H", time.gmtime(long_ago / 1e9)))
    assert (run_manager.get_run_process(run["run_id"]) or {})["duration_ms"] == 45_000
    assert run["metadata"]["otel"]["imported"] is True


def test_a_trace_whose_root_never_arrives_is_still_recorded(connection, monkeypatch):
    """The process died, or the batch carrying the root was dropped. Those are
    the runs most worth seeing, so they are recorded from what did arrive."""
    monkeypatch.setattr(otel, "TRACE_TTL_SECONDS", 0)
    orphan = _span(span_id="c" * 16, parent="missing-root", name="step")

    otel.record_spans(connection, [orphan])
    otel.record_spans(connection, [])   # a later request does the sweeping

    runs = _runs_of("billing-graph")
    assert len(runs) == 1
    assert runs[0]["metadata"]["otel"]["root_reported"] is False


def test_a_late_span_does_not_open_a_second_run_for_the_same_work(connection):
    root = _span(span_id="r" * 16, name="graph")
    late = _span(span_id="c" * 16, parent="r" * 16, name="straggler")

    otel.record_spans(connection, [root])
    result = otel.record_spans(connection, [late])

    assert len(_runs_of("billing-graph")) == 1
    assert result["rejected_spans"] == 1


def test_two_connections_reporting_the_same_trace_id_stay_apart():
    """Trace ids are generated by whoever sent them, and two teams' exporters
    have never heard of each other."""
    first, _ = connection_store.create_connection(connection_id="first", name="First")
    second, _ = connection_store.create_connection(connection_id="second", name="Second")
    spans = [_span(span_id="r" * 16, name="one"), _span(span_id="x" * 16, parent="r" * 16)]

    otel.record_spans(first, spans[1:])
    otel.record_spans(second, spans[1:])
    assert otel.buffered_traces() == 2, "one buffer each, not one shared"

    otel.record_spans(first, spans[:1])
    otel.record_spans(second, spans[:1])
    assert len(_runs_of("first")) == 1 and len(_runs_of("second")) == 1


def test_an_abandoned_trace_is_never_filed_under_whoever_reported_next(monkeypatch):
    """Sweeping is per connection, because a swept trace is written as the
    caller. One connection's lost run must not appear in another's history."""
    monkeypatch.setattr(otel, "TRACE_TTL_SECONDS", 0)
    first, _ = connection_store.create_connection(connection_id="first", name="First")
    second, _ = connection_store.create_connection(connection_id="second", name="Second")

    otel.record_spans(first, [_span(span_id="c" * 16, parent="gone", name="orphan")])
    otel.record_spans(second, [_span(trace_id="b" * 32, span_id="r" * 16, name="own")])

    assert _runs_of("second") and all(r["title"] == "own" for r in _runs_of("second"))
    assert _runs_of("first") == [], "its own request will record it, nobody else's"


def test_the_last_trace_of_a_graph_that_died_is_recorded_by_maintenance(connection, monkeypatch):
    """A connection still exporting clears its own; one that stopped needs this."""
    monkeypatch.setattr(otel, "TRACE_TTL_SECONDS", 0)
    otel.record_spans(connection, [_span(span_id="c" * 16, parent="gone", name="orphan")])
    assert _runs_of("billing-graph") == []

    assert otel.flush_idle() == {"flushed_otel_traces": 1}

    runs = _runs_of("billing-graph")
    assert len(runs) == 1 and runs[0]["metadata"]["otel"]["root_reported"] is False


def test_a_flush_after_the_connection_was_deleted_drops_the_trace(connection, monkeypatch):
    monkeypatch.setattr(otel, "TRACE_TTL_SECONDS", 0)
    otel.record_spans(connection, [_span(span_id="c" * 16, parent="gone", name="orphan")])

    connection_store.delete_connection("billing-graph")

    assert otel.flush_idle() == {"flushed_otel_traces": 0}
    assert _runs_of("billing-graph") == []


def test_one_trace_cannot_fill_the_buffer(connection, monkeypatch):
    monkeypatch.setattr(otel, "MAX_SPANS_PER_TRACE", 3)
    children = [_span(span_id=f"{i:016x}", parent="r" * 16, name=f"n{i}") for i in range(10)]

    result = otel.record_spans(connection, children)
    assert result["rejected_spans"] == 7

    # The root is taken even so: it is what completes the trace and says how it
    # ended, and a cap that swallowed it would strand the whole run.
    otel.record_spans(connection, [_span(span_id="r" * 16, name="graph")])
    run = _runs_of("billing-graph")[0]
    assert run["metadata"]["otel"]["dropped_spans"] == 7
    assert run["metadata"]["otel"]["root_reported"] is True


def test_a_connection_cannot_hold_open_more_traces_than_the_cap(connection, monkeypatch):
    monkeypatch.setattr(otel, "MAX_OPEN_TRACES", 2)
    for i in range(4):
        otel.record_spans(connection, [
            _span(trace_id=f"{i:032x}", span_id="c" * 16, parent="r" * 16, name=f"n{i}")])

    assert otel.buffered_traces() <= 2
    # Evicted, not dropped: the oldest trace is the one least likely to complete.
    assert len(_runs_of("billing-graph")) == 2


# ── the endpoint ────────────────────────────────────────────────────────────

def test_an_exporter_reports_with_a_connection_token(api_client):
    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]

    accepted = api_client.post("/api/ingest/v1/traces", content=PROTOBUF,
                               headers={"content-type": "application/x-protobuf",
                                        "x-connection-token": token})
    refused = api_client.post("/api/ingest/v1/traces", content=PROTOBUF,
                              headers={"content-type": "application/x-protobuf"})

    assert accepted.status_code == 200
    assert refused.status_code == 401
    assert len(api_client.get("/api/connections/c1").json()["runs"]) == 2


def test_the_reply_is_an_otlp_response_in_the_encoding_that_was_used(api_client):
    """An exporter parses the reply as its own protocol; JSON back from a
    protobuf request is a failed export on a hub that accepted the data."""
    first = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]
    # A second connection, because the same fixture is being sent twice and a
    # trace already recorded is deliberately not recorded again.
    second = api_client.post("/api/connections", json={"id": "c2", "name": "C2"}).json()["token"]

    proto = api_client.post("/api/ingest/v1/traces", content=PROTOBUF,
                            headers={"x-connection-token": first,
                                     "content-type": "application/x-protobuf"})
    as_json = api_client.post("/api/ingest/v1/traces", content=JSON_PAYLOAD,
                              headers={"x-connection-token": second,
                                       "content-type": "application/json"})

    assert proto.headers["content-type"].startswith("application/x-protobuf")
    assert proto.content == b"", "nothing was dropped, so the response is empty"
    assert as_json.json() == {"partialSuccess": {}}


def test_the_same_trace_exported_twice_is_recorded_once(api_client):
    """Exporters retry, and a collector fanning out to two backends may send a
    batch again. The second copy must not double the run or its cost."""
    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]
    headers = {"x-connection-token": token, "content-type": "application/x-protobuf"}

    api_client.post("/api/ingest/v1/traces", content=PROTOBUF, headers=headers)
    again = api_client.post("/api/ingest/v1/traces", content=PROTOBUF, headers=headers)

    assert len(api_client.get("/api/connections/c1").json()["runs"]) == 2
    assert again.status_code == 200
    assert again.content != b"", "the reply says the spans were dropped"


def test_a_gzipped_export_is_accepted(api_client):
    import gzip

    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]
    response = api_client.post(
        "/api/ingest/v1/traces", content=gzip.compress(PROTOBUF),
        headers={"content-type": "application/x-protobuf", "content-encoding": "gzip",
                 "x-connection-token": token})

    assert response.status_code == 200
    assert len(api_client.get("/api/connections/c1").json()["runs"]) == 2


def test_a_body_that_expands_past_the_limit_is_refused(api_client, monkeypatch):
    """A gzip stream announces nothing about its decompressed size."""
    import gzip

    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]
    monkeypatch.setattr("routes.ingest._MAX_DECOMPRESSED", 64)
    response = api_client.post(
        "/api/ingest/v1/traces", content=gzip.compress(b"0" * 100_000),
        headers={"content-type": "application/x-protobuf", "content-encoding": "gzip",
                 "x-connection-token": token})

    assert response.status_code == 413


def test_a_payload_the_hub_could_not_read_is_a_client_error(api_client):
    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]

    response = api_client.post("/api/ingest/v1/traces", content=b"\xff\xff not otlp",
                               headers={"content-type": "application/x-protobuf",
                                        "x-connection-token": token})

    assert response.status_code == 400
    assert "malformed OTLP" in response.json()["detail"]


def test_the_hub_says_where_to_point_an_exporter(api_client):
    """Discoverable from the token alone: a client verifies the URL and learns
    the other way in without reading the documentation."""
    token = api_client.post("/api/connections", json={"id": "c1", "name": "C1"}).json()["token"]

    whoami = api_client.get("/api/ingest/self", headers={"x-connection-token": token}).json()

    assert whoami["otlp"]["traces"] == "/api/ingest/v1/traces"
    assert "application/x-protobuf" in whoami["otlp"]["encodings"]


def test_imported_runs_belong_to_the_connections_workspace(api_client):
    """An imported run is a run of this connection, and a connection lives in a
    workspace like everything else here."""
    token = api_client.post("/api/connections", json={
        "id": "c1", "name": "C1", "workspace": "team-a"}).json()["token"]

    api_client.post("/api/ingest/v1/traces", content=PROTOBUF,
                    headers={"content-type": "application/x-protobuf",
                             "x-connection-token": token})

    runs = _runs_of("c1")
    assert runs and all(r["workspace"] == "team-a" for r in runs)
