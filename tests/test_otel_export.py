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
