"""A remote agent that reports its own dollar cost on a ``usage`` frame gets it
credited to the run record as ``reported_cost_usd``, and
``managers.runs.groups.runs_cost`` prefers that figure over catalog pricing.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agents.remote_agent import RemoteAgent


# ── cost crediting ───────────────────────────────────────────────────────────

class _CostStubHandler(BaseHTTPRequestHandler):
    """A remote that reports its usage, including a cost_usd, on a `usage`
    frame: the shape a CLI-wrapping agent produces."""

    stream_frames = [
        {"type": "token", "token": "hi"},
        {"type": "usage", "prompt_tokens": 900, "completion_tokens": 300,
         "total_tokens": 1200, "cached_tokens": 100, "cost_usd": 0.0412},
        {"type": "done", "ok": True, "output": "hi"},
    ]

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for frame in type(self).stream_frames:
            self.wfile.write((json.dumps(frame) + "\n").encode())
            self.wfile.flush()


@pytest.fixture
def cost_stub_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CostStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


class _RecordingEmitter:
    def __init__(self):
        self.events = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def emit_external(self, payload):
        self.events.append(payload)


def _open_test_run(run_id: str, **extra: Any) -> None:
    from managers.runs.lifecycle import open_run
    open_run(run_id, "cost-remote", status="running", **extra)


def test_reported_cost_is_credited_to_the_run_record(cost_stub_service):
    """A remote's own reported cost_usd lands on the run as reported_cost_usd,
    which is the field managers.runs.groups.runs_cost and the run views
    prefer over catalog pricing."""
    from managers.runs.store import get_runs_by_ids

    run_id = "run-cost-1"
    _open_test_run(run_id)

    agent = RemoteAgent(
        agent_id="cost-remote", name="Cost Remote",
        remote={"url": cost_stub_service, "stream_path": "/run/stream"},
    )
    listener = _RecordingEmitter()
    result = agent.run("go", callbacks=[listener], run_id=run_id)

    assert result.ok is True
    record = get_runs_by_ids([run_id])[run_id]
    assert record["reported_cost_usd"] == pytest.approx(0.0412)
    # Tokens are still credited exactly as before this change.
    assert listener.prompt_tokens == 900
    assert listener.completion_tokens == 300


def test_runs_cost_prefers_the_reported_figure_over_catalog_pricing(monkeypatch):
    from managers.runs import groups
    from managers.runs.store import update_run

    run_id = "run-cost-2"
    _open_test_run(run_id, provider="anthropic", model="claude-priced-test")
    update_run(run_id, {
        "reported_cost_usd": 5.0,
        "process": {"token_usage": {"inbound_tokens": 1000, "outbound_tokens": 1000,
                                     "total_tokens": 2000, "cached_tokens": 0}},
    })

    # A price that, applied to the tokens above, would give a very different
    # (and much cheaper) figure than the reported one: proof runs_cost is not
    # merely falling back to zero-priced-unknown-model.
    monkeypatch.setattr(
        "common.pricing.load_price_map",
        lambda: {("anthropic", "claude-priced-test"): (1.0, 1.0, 0.1)},
    )

    assert groups.runs_cost([run_id]) == pytest.approx(5.0)


def test_runs_cost_falls_back_to_catalog_pricing_without_a_reported_cost(monkeypatch):
    from managers.runs import groups

    run_id = "run-cost-3"
    _open_test_run(run_id, provider="anthropic", model="claude-priced-test")
    from managers.runs.store import update_run
    update_run(run_id, {
        "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 1_000_000,
                                     "total_tokens": 2_000_000, "cached_tokens": 0}},
    })
    monkeypatch.setattr(
        "common.pricing.load_price_map",
        lambda: {("anthropic", "claude-priced-test"): (2.0, 4.0, 0.2)},
    )
    # 1M input tokens @ $2/M + 1M output tokens @ $4/M = $6.
    assert groups.runs_cost([run_id]) == pytest.approx(6.0)


def test_cost_is_not_credited_without_a_run_id(cost_stub_service):
    """RemoteAgent.run() called with no run_id (the exact shape the existing
    test suite in test_agent_import.py exercises) must not raise just because
    there is nowhere to persist a reported cost."""
    agent = RemoteAgent(
        agent_id="cost-remote", name="Cost Remote",
        remote={"url": cost_stub_service, "stream_path": "/run/stream"},
    )
    listener = _RecordingEmitter()
    result = agent.run("go", callbacks=[listener])
    assert result.ok is True
