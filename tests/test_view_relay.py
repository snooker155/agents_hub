"""
Live view events from a process without the broker's loop.

An agent that applies a view op (tools/views.py, tools/geometry.py) runs on a
service replica or in a subprocess as often as in the backend, and only the
backend owns the event loop the Studio's ``/api/stream`` hangs off. The op is
persisted either way; what used to be lost out of process was the
``view_op`` broadcast, so the open Studio showed no intermediate scenes until
it was reloaded. ``common.session_broker.publish_event`` relays such events
to ``POST /api/stream/publish``; these tests cover both ends.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import common.session_broker as sb  # noqa: E402

# conftest's autouse fixture stubs the relay for every test; the original is
# captured here at import time (same trick as tests/test_token_relay.py).
_real_relay_publish = sb._relay_publish

TOKEN = "s3cret"


def _capture_post(monkeypatch, captured):
    def fake_post(url, json=None, headers=None, timeout=None):
        captured.setdefault("calls", []).append({"url": url, "json": json, "headers": headers})
    monkeypatch.setattr("requests.post", fake_post)


# ── the relay itself ─────────────────────────────────────────────────────────

def test_relay_publish_posts_channel_and_event_with_bearer(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_API_TOKEN", TOKEN)
    monkeypatch.setenv("DASHBOARD_PORT", "8123")
    captured = {}
    _capture_post(monkeypatch, captured)

    _real_relay_publish("view:vw_1", {"type": "view_op", "op": {"seq": 1}})

    [call] = captured["calls"]
    assert call["url"] == "http://localhost:8123/api/stream/publish"
    assert call["json"] == {"channel": "view:vw_1", "event": {"type": "view_op", "op": {"seq": 1}}}
    assert call["headers"] == {"Authorization": f"Bearer {TOKEN}"}


def test_relay_publish_survives_an_unreachable_backend(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr("requests.post", boom)
    _real_relay_publish("view:vw_1", {"type": "view_op"})  # must not raise


# ── publish_event picks the path by the broker's loop ────────────────────────

def test_publish_event_relays_when_no_loop_is_running(monkeypatch):
    relayed = []
    monkeypatch.setattr(sb, "_relay_publish", lambda ch, ev: relayed.append((ch, ev)))
    monkeypatch.setattr(sb.broker, "_loop", None)
    local = []
    monkeypatch.setattr(sb.broker, "publish_threadsafe", lambda ch, ev, **k: local.append((ch, ev)))

    sb.publish_event("view:vw_1", {"type": "view_op", "op": {"seq": 1}})

    assert relayed == [("view:vw_1", {"type": "view_op", "op": {"seq": 1}})]
    assert local == []


def test_publish_event_publishes_locally_inside_the_backend(monkeypatch):
    relayed = []
    monkeypatch.setattr(sb, "_relay_publish", lambda ch, ev: relayed.append((ch, ev)))
    local = []
    monkeypatch.setattr(sb.broker, "publish_threadsafe", lambda ch, ev, **k: local.append((ch, ev)))

    class _Loop:
        def is_running(self):
            return True
    monkeypatch.setattr(sb.broker, "_loop", _Loop())

    sb.publish_event("view:vw_1", {"type": "frame"})

    assert local == [("view:vw_1", {"type": "frame"})]
    assert relayed == []


# ── the view store goes through it, op by op and in order ────────────────────

def test_append_ops_relays_every_op_in_order(monkeypatch):
    from views.store import create_live_view, append_ops

    sent = []
    monkeypatch.setattr(sb, "publish_event", lambda ch, ev: sent.append((ch, ev)))
    env = create_live_view("graph", "G")
    vid = env.view_id

    append_ops(vid, [
        {"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}},
        {"op": "add", "path": "spec.nodes.b", "value": {"label": "B"}},
    ])

    assert [ch for ch, _ in sent] == [f"view:{vid}"] * 2
    assert [ev["type"] for _, ev in sent] == ["view_op", "view_op"]
    assert [ev["op"]["seq"] for _, ev in sent] == [1, 2]
    assert [ev["op"]["path"] for _, ev in sent] == ["spec.nodes.a", "spec.nodes.b"]


def test_revert_relays_a_reset(monkeypatch):
    from views.store import create_live_view, append_ops, revert_to

    sent = []
    monkeypatch.setattr(sb, "publish_event", lambda ch, ev: sent.append((ch, ev)))
    env = create_live_view("graph", "G")
    vid = env.view_id
    append_ops(vid, [{"op": "add", "path": "spec.nodes.a", "value": {"label": "A"}}])
    append_ops(vid, [{"op": "update", "path": "spec.nodes.a.label", "value": "Alpha"}])
    sent.clear()

    assert revert_to(vid, 1) is True

    [(_ch, ev)] = sent
    assert ev["type"] == "view_reset"
    assert ev["seq"] == 1
    assert ev["doc"]["spec"]["nodes"]["a"]["label"] == "A"


# ── the backend end: POST /api/stream/publish ────────────────────────────────

@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import stream as stream_routes

    app = FastAPI()
    app.include_router(stream_routes.router)
    return TestClient(app)


def test_publish_route_fans_the_event_out_on_its_channel(client, monkeypatch):
    published = []

    async def fake_apublish(channel, event, **kwargs):
        published.append((channel, event))
    monkeypatch.setattr(sb.broker, "apublish", fake_apublish)

    r = client.post("/api/stream/publish",
                    json={"channel": "view:vw_1", "event": {"type": "view_op", "op": {"seq": 3}}})

    assert r.status_code == 200, r.text
    assert published == [("view:vw_1", {"type": "view_op", "op": {"seq": 3}})]


def test_publish_route_rejects_an_event_without_a_type(client, monkeypatch):
    async def fake_apublish(channel, event, **kwargs):
        raise AssertionError("must not publish")
    monkeypatch.setattr(sb.broker, "apublish", fake_apublish)

    assert client.post("/api/stream/publish", json={"channel": "view:x", "event": {}}).status_code == 400
    assert client.post("/api/stream/publish", json={"channel": " ", "event": {"type": "x"}}).status_code == 400


def test_publish_route_reaches_a_subscribed_client(client):
    """End to end inside one process: a stream client subscribed to the view
    channel receives what a loop-less process relayed through the route."""
    loop = asyncio.new_event_loop()
    try:
        sb.broker.set_loop(loop)
        client_id, queue = sb.broker.open_client(["view:vw_e2e"])
        try:
            # TestClient runs the route on its own loop; apublish delivers via
            # the broker's registered loop, so drive that loop to flush.
            r = client.post("/api/stream/publish",
                            json={"channel": "view:vw_e2e", "event": {"type": "view_op", "op": {"seq": 1}}})
            assert r.status_code == 200, r.text
            loop.run_until_complete(asyncio.sleep(0))

            async def _next():
                return await asyncio.wait_for(queue.get(), timeout=2.0)
            ev = loop.run_until_complete(_next())
            assert ev["channel"] == "view:vw_e2e"
            assert ev["type"] == "view_op"
        finally:
            sb.broker.close_client(client_id)
    finally:
        sb.broker._loop = None
        loop.close()
