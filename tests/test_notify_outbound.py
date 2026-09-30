"""Outbound delivery: signing, retrying, the Slack payload shape, and the
worker thread that keeps a caller from blocking on an HTTP call.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from notify import outbound


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """No test should sleep for the real backoff."""
    monkeypatch.setattr(outbound, "RETRY_BACKOFF_SECONDS", 0)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _event(**data_overrides):
    data = {"title": "Hello", "body": "World", "severity": "info"}
    data.update(data_overrides)
    return {
        "id": "evt-1",
        "type": "notification",
        "workspace": "default",
        "created_at": "2026-01-01T00:00:00+00:00",
        "data": data,
    }


def test_webhook_signature_and_headers(monkeypatch):
    calls = []

    def _post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return _FakeResponse(200)

    monkeypatch.setattr(outbound.requests, "post", _post)

    endpoint = {"id": "e1", "kind": "webhook", "url": "https://example.com/hook", "secret": "s3cr3t"}
    outbound.deliver(endpoint, _event())

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://example.com/hook"
    assert call["timeout"] == outbound.REQUEST_TIMEOUT_SECONDS
    headers = call["headers"]
    assert headers["X-AgentsHub-Event"] == "notification"
    assert "X-AgentsHub-Delivery" in headers
    assert "X-AgentsHub-Timestamp" in headers

    body = call["data"]
    expected = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    assert headers["X-AgentsHub-Signature"] == expected

    # The body on the wire is exactly the event, unmodified.
    assert json.loads(body) == _event()


def test_webhook_without_secret_sends_no_signature(monkeypatch):
    calls = []
    monkeypatch.setattr(outbound.requests, "post",
                         lambda url, data=None, headers=None, timeout=None: calls.append(headers) or _FakeResponse(200))

    endpoint = {"id": "e1", "kind": "webhook", "url": "https://example.com/hook", "secret": ""}
    outbound.deliver(endpoint, _event())

    assert "X-AgentsHub-Signature" not in calls[0]


def test_retry_once_on_500(monkeypatch):
    calls = []

    def _post(url, data=None, headers=None, timeout=None):
        calls.append(1)
        return _FakeResponse(500 if len(calls) == 1 else 200)

    monkeypatch.setattr(outbound.requests, "post", _post)

    endpoint = {"id": "e1", "kind": "webhook", "url": "https://example.com/hook", "secret": "s"}
    outbound.deliver(endpoint, _event())

    assert len(calls) == 2


def test_gives_up_after_one_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(outbound.requests, "post",
                         lambda *a, **k: calls.append(1) or _FakeResponse(500))

    endpoint = {"id": "e1", "kind": "webhook", "url": "https://example.com/hook", "secret": "s"}
    outbound.deliver(endpoint, _event())  # must not raise

    assert len(calls) == 2  # one attempt + one retry, then give up


def test_retry_on_connection_error(monkeypatch):
    import requests as real_requests

    calls = []

    def _post(url, data=None, headers=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise real_requests.exceptions.ConnectionError("boom")
        return _FakeResponse(200)

    monkeypatch.setattr(outbound.requests, "post", _post)

    endpoint = {"id": "e1", "kind": "webhook", "url": "https://example.com/hook", "secret": "s"}
    outbound.deliver(endpoint, _event())  # must not raise

    assert len(calls) == 2


def test_deliver_never_raises_on_unknown_kind():
    # No requests.post stubbed at all — an unknown kind must be a no-op, not
    # an AttributeError.
    outbound.deliver({"id": "e1", "kind": "carrier-pigeon", "url": "x"}, _event())


def test_slack_payload_shape(monkeypatch):
    calls = []

    def _post(url, data=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json.loads(data), "headers": headers})
        return _FakeResponse(200)

    monkeypatch.setattr(outbound.requests, "post", _post)

    endpoint = {"id": "e2", "kind": "slack", "url": "https://hooks.slack.com/services/x"}
    outbound.deliver(endpoint, _event(title="Run failed", body="agent boom"))

    assert len(calls) == 1
    payload = calls[0]["body"]
    assert "text" in payload and "Run failed" in payload["text"]
    assert "blocks" in payload and isinstance(payload["blocks"], list)
    assert any("agent boom" in json.dumps(b) for b in payload["blocks"])
    # Slack never gets the webhook signature headers.
    assert "X-AgentsHub-Signature" not in calls[0]["headers"]


def test_worker_thread_delivers(monkeypatch):
    """dispatch() must not call deliver() synchronously; the worker thread does."""
    delivered = []
    monkeypatch.setattr(outbound, "deliver", lambda endpoint, event: delivered.append((endpoint, event)))

    endpoint = {"id": "e3", "kind": "webhook", "url": "https://example.com/hook", "secret": "s"}
    event = _event()
    outbound.dispatch(endpoint, event)

    # Wait for the queue to drain instead of sleeping an arbitrary amount.
    outbound._queue.join()

    assert delivered == [(endpoint, event)]


def test_dispatch_does_not_block_caller(monkeypatch):
    """A slow endpoint must not make dispatch() itself slow."""
    import time as time_mod

    def _slow_post(url, data=None, headers=None, timeout=None):
        time_mod.sleep(0.2)
        return _FakeResponse(200)

    monkeypatch.setattr(outbound.requests, "post", _slow_post)

    endpoint = {"id": "e4", "kind": "webhook", "url": "https://example.com/hook", "secret": "s"}
    start = time_mod.monotonic()
    outbound.dispatch(endpoint, _event())
    elapsed = time_mod.monotonic() - start

    assert elapsed < 0.1
    outbound._queue.join()  # let the worker finish before the next test
