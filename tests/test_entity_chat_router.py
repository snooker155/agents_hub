"""
The entity chat router factory — ``build_entity_chat_router``.

Ten pages used to carry a hand-written copy of the same four routes (history,
clear, send, stop) around ``chat.entity_chat``. This module is what replaced
them: one factory, driven by an :class:`EntityChatRoute` spec. What is worth
testing here is the factory itself, not any one kind's prompt — that every
spec produces the four expected routes at the right paths, and that wiring
them onto a router actually plumbs a turn through ``run_entity_chat_turn`` (the
one call every converted handler used to make by hand), the store, and the
hooks a kind can plug in (``load_for_send``, ``summarize``, ``context_setup``,
``post_turn``).

``run_entity_chat_turn`` is monkeypatched: what it itself does (spawn an
agent, write a run record) is covered by ``test_entity_chat.py``. Here we only
need to know the router calls it with the right entity id, prompt and
summarizer, and does the right thing with whatever it puts on the queue.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import chat.entity_chat_router as router_module
from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router
from common.entity_chat_store import EntityChatStore


# ── the factory's shape ─────────────────────────────────────────────────────

def _minimal_route(path: str = "/{widget_id}/chat") -> EntityChatRoute:
    return EntityChatRoute(
        kind="widget",
        path=path,
        load=lambda request: SimpleNamespace(entity_id=request.path_params.get("widget_id", "w")),
        prompt=lambda ctx, history, msg: f"prompt for {ctx.entity_id}",
        spec=lambda ctx: EntityChatSpec(kind="widget", agent_id="widget_agent", title="widget"),
    )


def test_the_factory_emits_exactly_the_four_expected_routes():
    """GET / DELETE / POST at ``path``, plus POST at ``path`` + ``/stop`` —
    nothing more, nothing at a different path or method."""
    sub = build_entity_chat_router(_minimal_route("/{widget_id}/chat"))
    seen = {(r.path, tuple(sorted(r.methods))) for r in sub.routes}
    assert seen == {
        ("/{widget_id}/chat", ("DELETE",)),
        ("/{widget_id}/chat", ("GET",)),
        ("/{widget_id}/chat", ("POST",)),
        ("/{widget_id}/chat/stop", ("POST",)),
    }


def test_the_stop_path_is_derived_even_from_a_trailing_slash():
    """``path.rstrip("/")`` is what keeps a spec that ends the path in ``/``
    from producing ``//stop``."""
    sub = build_entity_chat_router(_minimal_route("/chat/"))
    stop_paths = {r.path for r in sub.routes if "stop" in r.path}
    assert stop_paths == {"/chat/stop"}


# ── wired onto an app, end to end ───────────────────────────────────────────

@pytest.fixture
def chat_store(tmp_path, monkeypatch):
    import common.entity_chat_store as store_module

    store = EntityChatStore(path=tmp_path / "chats.json")
    monkeypatch.setattr(store_module, "entity_chat_store", lambda: store)
    return store


@pytest.fixture
def widget_app(chat_store):
    """A tiny app built from one kind ("widget"), the way a real routes module
    wires ``build_entity_chat_router`` onto its own router.

    The entity itself is nothing but a dict in ``WIDGETS`` — enough to exercise
    "not found", ``load_for_send`` carrying extra state to the hooks, and
    ``context_setup`` / ``post_turn`` / ``summarize`` all firing in the order the
    real routes rely on.
    """
    WIDGETS = {"w1": {"label": "first"}}
    calls: list = []

    def _load(request):
        widget_id = request.path_params["widget_id"]
        if widget_id not in WIDGETS:
            raise HTTPException(status_code=404, detail="Widget not found")
        return SimpleNamespace(entity_id=widget_id, widget=WIDGETS[widget_id])

    def _load_for_send(request, body):
        ctx = _load(request)
        ctx.extra = body.get("extra")
        return ctx

    def _summarize(ctx):
        return lambda: f"summarized {ctx.entity_id}"

    def _context_setup(ctx):
        calls.append(("context_setup", ctx.entity_id))

    async def _post_turn(queue, ctx):
        calls.append(("post_turn", ctx.entity_id))
        await queue.put({"type": "widget", "widget": ctx.widget})

    route = EntityChatRoute(
        kind="widget",
        path="/{widget_id}/chat",
        load=_load,
        load_for_send=_load_for_send,
        prompt=lambda ctx, history, msg: f"prompt for {ctx.entity_id}: {msg}",
        spec=lambda ctx: EntityChatSpec(kind="widget", agent_id="widget_agent",
                                        title=f"{ctx.widget['label']} · widget"),
        summarize=_summarize,
        context_setup=_context_setup,
        post_turn=_post_turn,
    )

    app = FastAPI()
    app.include_router(build_entity_chat_router(route), prefix="/api/widgets")
    return TestClient(app), WIDGETS, calls


def test_the_get_route_404s_on_an_unknown_entity(widget_app):
    client, _, _ = widget_app
    assert client.get("/api/widgets/nope/chat").status_code == 404


def test_the_get_route_returns_the_stored_transcript_and_a_chat_ref(widget_app, chat_store):
    client, _, _ = widget_app
    chat_store.append_message("widget", "w1", "user", "hello")

    body = client.get("/api/widgets/w1/chat").json()
    assert body["messages"][0]["content"] == "hello"
    assert body["trace"] == []
    assert body["chat_ref"] == {"kind": "widget", "id": "w1"}


def test_delete_clears_the_transcript_and_bumps_the_session(widget_app, chat_store):
    client, _, _ = widget_app
    chat_store.append_message("widget", "w1", "user", "hello")

    body = client.delete("/api/widgets/w1/chat").json()
    assert body == {"cleared": True, "session_epoch": 1}
    assert chat_store.get_messages("widget", "w1") == []


def test_stopping_with_nothing_in_flight_says_so(widget_app):
    client, _, _ = widget_app
    body = client.post("/api/widgets/w1/chat/stop").json()
    assert body == {"stopped": False, "cancelled": 0}


def test_send_refuses_an_empty_message(widget_app):
    client, _, _ = widget_app
    response = client.post("/api/widgets/w1/chat", json={"message": "   "})
    assert response.status_code == 400


def test_send_404s_before_ever_calling_the_turn(widget_app, monkeypatch):
    """``load_for_send`` runs (and can 404) before the turn is ever started —
    a bad entity id must not spin up an agent run at all."""
    client, _, _ = widget_app
    called = []
    monkeypatch.setattr(router_module, "run_entity_chat_turn",
                        lambda *a, **k: called.append(1))
    assert client.post("/api/widgets/nope/chat", json={"message": "hi"}).status_code == 404
    assert called == []


def test_a_turn_runs_the_hooks_in_order_and_streams_what_it_puts_on_the_queue(
        widget_app, chat_store, monkeypatch):
    """The one end-to-end path: ``run_entity_chat_turn`` monkeypatched to a
    stub that records the exact arguments the router built for it, then the
    router's own ``post_turn`` hook fires and its event reaches the stream.
    """
    client, WIDGETS, calls = widget_app
    seen = {}

    async def fake_run_entity_chat_turn(queue, spec, entity_id, user_message,
                                        build_prompt, *, summarize=None):
        seen["spec"] = spec
        seen["entity_id"] = entity_id
        seen["user_message"] = user_message
        # The prompt builder is exactly what ``EntityChatRoute.prompt`` wraps,
        # called with whatever history the store has for this entity.
        seen["prompt"] = build_prompt([])
        seen["summary"] = summarize() if summarize else None
        await queue.put({"type": "message", "role": "assistant", "content": "ok"})

    monkeypatch.setattr(router_module, "run_entity_chat_turn", fake_run_entity_chat_turn)

    with client.stream("POST", "/api/widgets/w1/chat",
                       json={"message": "make it blue", "extra": "x"}) as response:
        frames = [line for line in response.iter_lines() if line.startswith("data: ")]

    assert seen["entity_id"] == "w1"
    assert seen["user_message"] == "make it blue"
    assert seen["prompt"] == "prompt for w1: make it blue"
    assert seen["summary"] == "summarized w1"
    assert isinstance(seen["spec"], EntityChatSpec)
    assert seen["spec"].title == "first · widget"

    # context_setup before the turn, post_turn after it — both hooks fired.
    assert calls == [("context_setup", "w1"), ("post_turn", "w1")]

    # meta first, then whatever the turn (and post_turn) put on the queue.
    import json as _json
    events = [_json.loads(line[len("data: "):]) for line in frames]
    assert events[0] == {"type": "meta", "kind": "widget", "id": "w1"}
    assert {"type": "message", "role": "assistant", "content": "ok"} in events
    assert {"type": "widget", "widget": {"label": "first"}} in events
