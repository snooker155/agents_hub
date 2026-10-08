"""Exporting finished runs as OTLP spans (common/otel_export.py) and its hook
in managers.runs.store._update_run.
"""
from __future__ import annotations

import json

import pytest

from common import otel_export
from managers import run_manager as rm


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _run(**overrides):
    run = {
        "run_id": "run-1",
        "task_id": "task-1",
        "workspace": "default",
        "agent_id": "swe_agent",
        "status": "completed",
        "provider": "openai",
        "model": "gpt-4o",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "process": {
            "token_usage": {
                "inbound_tokens": 100, "outbound_tokens": 50,
                "total_tokens": 150, "cached_tokens": 10,
            },
            "duration_ms": 60000,
        },
    }
    run.update(overrides)
    return run


# ── configured() and header parsing ─────────────────────────────────────────

def test_configured_reflects_the_setting(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_url", "", raising=False)
    assert otel_export.configured() is False
    monkeypatch.setattr(settings, "otel_export_url", "http://collector/v1/traces", raising=False)
    assert otel_export.configured() is True


def test_headers_parse_key_equals_value_pairs(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_headers",
                        "Authorization=Bearer abc,X-Foo=bar", raising=False)
    headers = otel_export._headers()
    assert headers["Authorization"] == "Bearer abc"
    assert headers["X-Foo"] == "bar"
    assert headers["Content-Type"] == "application/json"


# ── the span shape ───────────────────────────────────────────────────────────

def test_build_span_shape():
    payload = otel_export.build_span(_run())
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    assert span["name"] == "run swe_agent"
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    assert span["parentSpanId"] == ""

    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["run_id"]["stringValue"] == "run-1"
    assert attrs["task_id"]["stringValue"] == "task-1"
    assert attrs["workspace"]["stringValue"] == "default"
    assert attrs["agent_id"]["stringValue"] == "swe_agent"
    assert attrs["status"]["stringValue"] == "completed"
    assert attrs["gen_ai.request.model"]["stringValue"] == "gpt-4o"
    assert attrs["gen_ai.provider.name"]["stringValue"] == "openai"
    assert attrs["gen_ai.usage.input_tokens"]["intValue"] == "100"
    assert attrs["gen_ai.usage.output_tokens"]["intValue"] == "50"
    assert attrs["gen_ai.usage.cache_read.input_tokens"]["intValue"] == "10"
    assert attrs["duration_ms"]["intValue"] == "60000"

    assert int(span["startTimeUnixNano"]) > 0
    assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
    assert span["status"]["code"] == 1  # OK


def test_build_span_marks_a_failed_run_as_an_error_with_its_message():
    payload = otel_export.build_span(_run(status="failed", error="boom"))
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["status"]["code"] == 2
    assert span["status"]["message"] == "boom"


def test_build_span_ids_are_stable_for_the_same_run_id():
    a = otel_export.build_span(_run())
    b = otel_export.build_span(_run())
    span_a = a["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    span_b = b["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span_a["traceId"] == span_b["traceId"]
    assert span_a["spanId"] == span_b["spanId"]


def test_the_hub_s_own_otlp_decoder_reads_the_export_back():
    """The whole point of mirroring the ingest shape: another Agents Hub's
    /api/ingest/v1/traces (connections/otlp.py + connections/otel.py) must be
    able to decode this export."""
    from connections import otlp

    payload = otel_export.build_span(_run())
    spans = otlp.decode(json.dumps(payload).encode("utf-8"), "application/json")
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "run swe_agent"
    assert span["attributes"]["run_id"] == "run-1"
    assert span["attributes"]["gen_ai.request.model"] == "gpt-4o"
    assert span["resource"]["service.name"] == "agents-hub"


# ── the HTTP call ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setattr(otel_export, "RETRY_BACKOFF_SECONDS", 0)


def test_post_succeeds_on_the_first_try(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_url", "http://collector/v1/traces", raising=False)
    calls = []
    monkeypatch.setattr(
        otel_export.requests, "post",
        lambda url, json=None, headers=None, timeout=None: calls.append(url) or _FakeResponse(200))

    otel_export._post(otel_export.build_span(_run()))
    assert calls == ["http://collector/v1/traces"]


def test_post_retries_once_on_a_5xx_then_gives_up(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_url", "http://collector/v1/traces", raising=False)
    calls = []
    monkeypatch.setattr(
        otel_export.requests, "post",
        lambda url, json=None, headers=None, timeout=None: calls.append(url) or _FakeResponse(500))

    otel_export._post(otel_export.build_span(_run()))
    assert len(calls) == 2


def test_post_is_a_no_op_with_no_url_configured(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_url", "", raising=False)
    calls = []
    monkeypatch.setattr(otel_export.requests, "post",
                        lambda *a, **k: calls.append(1) or _FakeResponse(200))

    otel_export._post(otel_export.build_span(_run()))
    assert calls == []


# ── the hook in managers.runs.store._update_run ─────────────────────────────

@pytest.fixture
def exporting(monkeypatch):
    """Point AGENTS_HUB_OTEL_EXPORT_URL somewhere and capture dispatch calls,
    without touching the network or the background thread: dispatch is the
    seam between "should this be exported" and "how it actually goes out",
    and only the first half is this hook's job."""
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_url", "http://collector/v1/traces", raising=False)
    calls = []
    monkeypatch.setattr(otel_export, "dispatch", lambda run: calls.append(run))
    return calls


def test_hook_fires_once_on_the_transition_into_a_terminal_status(exporting):
    rm.upsert_run({"run_id": "run-x", "agent_id": "swe_agent", "status": "running"})
    rm.update_run("run-x", {"status": "completed"})
    assert len(exporting) == 1
    assert exporting[0]["run_id"] == "run-x"

    # Already terminal: a later update (the output arriving, say) must not
    # export a second time.
    rm.update_run("run-x", {"output": "final answer"})
    assert len(exporting) == 1


def test_hook_does_not_fire_on_a_non_terminal_update(exporting):
    rm.upsert_run({"run_id": "run-y", "agent_id": "swe_agent", "status": "running"})
    rm.update_run("run-y", {"heartbeat_at": "2026-01-01T00:00:00+00:00"})
    assert exporting == []


def test_hook_fires_for_a_failed_run_too(exporting):
    rm.upsert_run({"run_id": "run-z", "agent_id": "swe_agent", "status": "running"})
    rm.update_run("run-z", {"status": "failed", "error": "boom"})
    assert len(exporting) == 1
    assert exporting[0]["status"] == "failed"


def test_no_config_means_no_dispatch(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_export_url", "", raising=False)
    calls = []
    monkeypatch.setattr(otel_export, "dispatch", lambda run: calls.append(run))

    rm.upsert_run({"run_id": "run-w", "agent_id": "swe_agent", "status": "running"})
    rm.update_run("run-w", {"status": "completed"})
    assert calls == []


# ── child spans for model and tool calls ────────────────────────────────────

def _run_with_calls(**over):
    proc = {
        "token_usage": {"inbound_tokens": 100, "outbound_tokens": 50, "total_tokens": 150, "cached_tokens": 10},
        "duration_ms": 60000,
        "llm_invocations": [
            {"kind": "llm", "model": "gpt-4o", "duration_ms": 2000, "response": "SECRET ANSWER",
             "token_usage": {"inbound_tokens": 60, "outbound_tokens": 20, "cached_tokens": 5}},
            {"kind": "tool", "model": "gpt-4o", "token_usage": {"inbound_tokens": 40, "outbound_tokens": 30}},
        ],
        "tool_calls": [{"tool": "web_search", "step": 1, "status": "ok", "duration_ms": 3000,
                        "input": "SECRET INPUT", "output": "SECRET OUTPUT"}],
    }
    return _run(process=proc, **over)


def _spans(payload):
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _attrs(span):
    return {a["key"]: a["value"] for a in span["attributes"]}


def test_child_spans_follow_the_genai_conventions():
    spans = _spans(otel_export.build_span(_run_with_calls()))
    parent, children = spans[0], spans[1:]
    assert [c["name"] for c in children] == ["chat gpt-4o", "execute_tool web_search", "chat gpt-4o"]
    assert all(c["parentSpanId"] == parent["spanId"] and c["traceId"] == parent["traceId"] for c in children)
    chat, tool = _attrs(children[0]), _attrs(children[1])
    assert chat["gen_ai.operation.name"]["stringValue"] == "chat"
    assert chat["gen_ai.usage.input_tokens"]["intValue"] == "60"
    assert chat["gen_ai.usage.output_tokens"]["intValue"] == "20"
    assert tool["gen_ai.operation.name"]["stringValue"] == "execute_tool"
    assert tool["gen_ai.tool.name"]["stringValue"] == "web_search"
    assert _attrs(parent)["gen_ai.operation.name"]["stringValue"] == "invoke_agent"
    # no prompt, answer or tool content leaves the hub
    assert "SECRET" not in json.dumps(spans)


def test_child_spans_sit_inside_the_run_window_in_order():
    spans = _spans(otel_export.build_span(_run_with_calls()))
    parent, children = spans[0], spans[1:]
    start, end = int(parent["startTimeUnixNano"]), int(parent["endTimeUnixNano"])
    cursor = start
    for c in children:
        assert int(c["startTimeUnixNano"]) == cursor
        cursor = int(c["endTimeUnixNano"])
    assert cursor <= end
    assert int(children[0]["endTimeUnixNano"]) - int(children[0]["startTimeUnixNano"]) == 2_000_000_000
    assert len({c["spanId"] for c in children}) == 3


def test_a_failed_tool_call_marks_its_span_as_an_error():
    run = _run_with_calls()
    run["process"]["tool_calls"][0]["status"] = "error"
    tool = _spans(otel_export.build_span(run))[2]
    assert tool["status"]["code"] == 2


def test_child_spans_are_bounded_and_the_parent_says_what_was_left_out(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "otel_max_child_spans", 2, raising=False)
    spans = _spans(otel_export.build_span(_run_with_calls()))
    assert len(spans) == 3
    assert _attrs(spans[0])["agents_hub.child_spans_dropped"]["intValue"] == "1"
    monkeypatch.setattr(settings, "otel_max_child_spans", 0, raising=False)
    assert len(_spans(otel_export.build_span(_run_with_calls()))) == 1


def test_a_run_without_call_records_is_still_one_span():
    assert len(_spans(otel_export.build_span(_run()))) == 1


def test_the_hub_s_decoder_reads_the_children_too():
    from connections import otlp
    spans = otlp.decode(json.dumps(otel_export.build_span(_run_with_calls())).encode(), "application/json")
    assert len(spans) == 4


def test_dispatch_queues_the_run_and_the_worker_builds_the_payload(monkeypatch):
    """Nothing is built or read on the caller's thread."""
    monkeypatch.setattr(otel_export, "_ensure_worker", lambda: None)
    while not otel_export._queue.empty():
        otel_export._queue.get_nowait()
        otel_export._queue.task_done()
    built = []
    monkeypatch.setattr(otel_export, "build_span", lambda run: built.append(run))
    otel_export.dispatch(_run())
    assert built == []
    queued = otel_export._queue.get_nowait()
    otel_export._queue.task_done()
    assert queued["run_id"] == "run-1"


def test_the_worker_reads_the_payload_for_the_child_spans(monkeypatch):
    stored = {"tool_calls": [{"tool": "read_file", "status": "ok"}], "llm_invocations": []}
    monkeypatch.setattr("managers.runs.store.get_run_process", lambda run_id: stored)
    run = otel_export._with_process(_run(process={"duration_ms": 5}))
    assert run["process"]["tool_calls"] == stored["tool_calls"]
    assert [s["name"] for s in _spans(otel_export.build_span(run))][1] == "execute_tool read_file"


# ── endpoints and headers: the standard variables as fallbacks ──────────────

@pytest.fixture
def clean_endpoints(monkeypatch):
    from common.config import settings
    for name in ("otel_export_url", "otel_export_headers", "otel_endpoint", "otel_headers", "otel_metrics_url"):
        monkeypatch.setattr(settings, name, "", raising=False)
    return settings


def test_the_standard_endpoint_is_the_fallback(clean_endpoints, monkeypatch):
    s = clean_endpoints
    assert not otel_export.configured() and not otel_export.metrics_configured()
    monkeypatch.setattr(s, "otel_endpoint", "http://collector:4318/", raising=False)
    assert otel_export.traces_url() == "http://collector:4318/v1/traces"
    assert otel_export.metrics_url() == "http://collector:4318/v1/metrics"
    assert otel_export.configured()
    monkeypatch.setattr(s, "otel_export_url", "http://other/v1/traces", raising=False)
    assert otel_export.traces_url() == "http://other/v1/traces"


def test_the_metrics_url_follows_the_traces_url_unless_set(clean_endpoints, monkeypatch):
    s = clean_endpoints
    monkeypatch.setattr(s, "otel_export_url", "http://collector/v1/traces", raising=False)
    assert otel_export.metrics_url() == "http://collector/v1/metrics"
    monkeypatch.setattr(s, "otel_export_url", "http://hub/api/ingest/v1/traces/x", raising=False)
    assert otel_export.metrics_url() == ""
    monkeypatch.setattr(s, "otel_metrics_url", "http://m/v1/metrics", raising=False)
    assert otel_export.metrics_url() == "http://m/v1/metrics"


def test_the_standard_headers_are_the_fallback_and_are_percent_decoded(clean_endpoints, monkeypatch):
    s = clean_endpoints
    monkeypatch.setattr(s, "otel_headers", "Authorization=Bearer%20abc,X-Team=core", raising=False)
    assert otel_export._headers()["Authorization"] == "Bearer abc"
    monkeypatch.setattr(s, "otel_export_headers", "Authorization=own", raising=False)
    h = otel_export._headers()
    assert h["Authorization"] == "own" and "X-Team" not in h


# ── OTLP metrics ────────────────────────────────────────────────────────────

def _fake_metrics():
    return [
        {"name": "agents_hub_runs_running", "help": "running", "type": "gauge", "samples": [({}, 3)]},
        {"name": "agents_hub_cost_usd_total", "help": "cost", "type": "gauge",
         "samples": [({"workspace": "default"}, 1.5)]},
        {"name": "agents_hub_runs_finished_total", "help": "finished", "type": "counter",
         "samples": [({"agent": "writer", "status": "completed"}, 7)]},
        {"name": "agents_hub_run_duration_seconds", "help": "dur", "type": "histogram", "samples": [],
         "histogram": {"bounds": [1.0, 5.0], "counts": [2, 1, 0], "sum": 4.5, "count": 3}},
        {"name": "agents_hub_empty", "help": "nothing", "type": "gauge", "samples": []},
    ]


def test_build_metrics_maps_gauges_counters_and_histograms():
    payload = otel_export.build_metrics(_fake_metrics(), now_ns=123)
    rm_ = payload["resourceMetrics"][0]
    res = {a["key"]: a["value"] for a in rm_["resource"]["attributes"]}
    assert res["service.name"]["stringValue"] == "agents-hub"
    by_name = {m["name"]: m for m in rm_["scopeMetrics"][0]["metrics"]}
    assert "agents_hub_empty" not in by_name
    assert by_name["agents_hub_runs_running"]["gauge"]["dataPoints"][0]["asInt"] == "3"
    assert by_name["agents_hub_cost_usd_total"]["gauge"]["dataPoints"][0]["asDouble"] == 1.5
    counter = by_name["agents_hub_runs_finished_total"]["sum"]
    assert counter["isMonotonic"] is True and counter["aggregationTemporality"] == 2
    point = counter["dataPoints"][0]
    assert point["asInt"] == "7" and "startTimeUnixNano" in point
    assert {a["key"] for a in point["attributes"]} == {"agent", "status"}
    hist = by_name["agents_hub_run_duration_seconds"]["histogram"]["dataPoints"][0]
    assert hist["bucketCounts"] == ["2", "1", "0"] and hist["explicitBounds"] == [1.0, 5.0]
    assert hist["count"] == "3" and hist["sum"] == 4.5


def test_build_metrics_from_the_live_collector_is_json():
    json.dumps(otel_export.build_metrics())


def test_push_metrics_posts_to_the_metrics_url_with_headers(clean_endpoints, monkeypatch):
    s = clean_endpoints
    monkeypatch.setattr(s, "otel_export_url", "http://collector/v1/traces", raising=False)
    monkeypatch.setattr(s, "otel_export_headers", "X-Key=1", raising=False)
    seen = []
    monkeypatch.setattr(otel_export.requests, "post",
                        lambda url, json=None, headers=None, timeout=None: seen.append((url, headers, json)) or _FakeResponse(200))
    assert otel_export.push_metrics_once() is True
    url, headers, body = seen[0]
    assert url == "http://collector/v1/metrics" and headers["X-Key"] == "1"
    assert "resourceMetrics" in body


def test_push_metrics_never_raises_and_does_nothing_unconfigured(clean_endpoints, monkeypatch):
    assert otel_export.push_metrics_once() is False
    monkeypatch.setattr(clean_endpoints, "otel_endpoint", "http://collector", raising=False)

    def boom(*a, **k):
        raise otel_export.requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(otel_export.requests, "post", boom)
    assert otel_export.push_metrics_once() is False


def test_the_exporter_thread_starts_only_when_configured(clean_endpoints, monkeypatch):
    assert otel_export.start_metrics_exporter() is False
    monkeypatch.setattr(clean_endpoints, "otel_endpoint", "http://collector", raising=False)
    monkeypatch.setattr(otel_export, "_metrics_loop", lambda: None)
    try:
        assert otel_export.start_metrics_exporter() is True
    finally:
        otel_export.stop_metrics_exporter()
        otel_export._metrics_thread = None
