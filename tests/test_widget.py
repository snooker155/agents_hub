"""The embeddable chat widget (widgets/, routes/widget.py, docs/widget.md).

Over real HTTP through the whole app, so the edge middleware (``/widget.js``
and per-widget CORS), the open-path rule and the auth guard all take part.
The chat pipeline is a stub that yields a scripted turn and records the
ChatRequest it was given; nothing reaches a model.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"
SITE = "https://shop.example"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _limits():
    from widgets import service
    service.reset_limits()
    yield
    service.reset_limits()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def agent():
    from agents.registry import AgentSpec, add_agent
    spec = AgentSpec(id="shop-helper", name="Shop Helper", type="langchain",
                     entrypoint="agents.standard_agent:StandardAgent")
    add_agent(spec)
    second = AgentSpec(id="shop-billing", name="Billing Desk", type="langchain",
                       entrypoint="agents.standard_agent:StandardAgent")
    add_agent(second)
    return spec


def _default_turn(request):
    return [
        {"type": "meta", "run_id": "run-1", "session_id": "sess-1"},
        {"type": "thinking", "message": "a private thought"},
        {"type": "tool_start", "step": 1, "tool": "search_memory", "input": "secret input"},
        {"type": "tool_end", "output": "secret output /srv/hub/workspaces/default"},
        {"type": "tool_start", "delegation": True, "tool": "inner_tool", "input": "x"},
        {"type": "usage", "prompt_tokens": 10},
        {"type": "token", "token": "Hello "},
        {"type": "token", "token": "there [1]"},
        {"type": "done", "ok": True, "response": "Hello there [1]", "run_id": "run-1",
         "session_id": "sess-1", "usage": {"inbound_tokens": 90, "outbound_tokens": 30,
                                           "total_tokens": 120},
         "entities": [{"kind": "task", "id": "t1", "url": "/tasks/t1"}],
         "citations": [{"n": 1, "pool_id": "pool-9", "file_id": "f-9", "filename": "guide.md",
                        "chunk_idx": 3, "heading_path": ["Setup"], "snippet": "Install it first",
                        "score": 0.9, "workspace_file_id": "file_abc"}]},
    ]


@pytest.fixture
def pipeline(monkeypatch):
    """Replace the chat pipeline with a scripted turn; ``state["turn"]`` is a
    function of the ChatRequest returning the events."""
    from chat import pipelines
    state = {"requests": [], "turn": _default_turn}

    async def fake(request):
        state["requests"].append(request)
        for event in state["turn"](request):
            yield event

    monkeypatch.setattr(pipelines, "run_chat_pipeline", fake)
    return state


def _create(client, agent, **extra):
    payload = {"workspace": "default", "name": "Shop chat", "agent_id": agent.id,
               "allowed_origins": [SITE, "http://localhost:3000"], "title": "Ask us",
               "greeting": "Hi! How can we help?", **extra}
    response = client.post("/api/widgets", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _pub(widget, origin=SITE, token=None, **more):
    headers = {"X-Widget-Key": widget["public_key"]}
    if origin:
        headers["Origin"] = origin
    if token:
        headers["X-Visitor-Token"] = token
    headers.update(more)
    return headers


def _visitor(client, widget, origin=SITE):
    response = client.post(f"/api/widgets/public/{widget['widget_id']}/visitor",
                           headers=_pub(widget, origin))
    assert response.status_code == 200, response.text
    return response.json()["visitor_token"]


def _thread(client, widget, token, origin=SITE):
    response = client.post(f"/api/widgets/public/{widget['widget_id']}/threads",
                           headers=_pub(widget, origin, token), json={})
    assert response.status_code == 200, response.text
    return response.json()["thread_id"]


def _send(client, widget, token, thread_id, text="Where is my order?", origin=SITE, **body):
    return client.post(
        f"/api/widgets/public/{widget['widget_id']}/threads/{thread_id}/messages",
        headers=_pub(widget, origin, token), json={"text": text, **body})


def _events(text):
    return [json.loads(line[len("data: "):]) for line in text.splitlines()
            if line.startswith("data: ")]


# ── models and validation ────────────────────────────────────────────────────

def test_origins_are_normalized_and_checked():
    from widgets.models import WidgetValidationError, normalize_origin, validate_origins
    assert normalize_origin("https://Shop.Example:443/") == "https://shop.example"
    assert normalize_origin("https://shop.example:8443") == "https://shop.example:8443"
    assert normalize_origin("http://localhost:5500") == "http://localhost:5500"
    assert normalize_origin("http://127.0.0.1") == "http://127.0.0.1"
    for bad in ("http://shop.example", "https://shop.example/page", "ftp://x.y",
                "https://*.shop.example", "https://u:p@shop.example", "shop.example"):
        with pytest.raises(WidgetValidationError):
            normalize_origin(bad)
    assert validate_origins(["https://a.example", "https://A.example/", ""],
                            multi_mode=True) == ["https://a.example"]
    with pytest.raises(WidgetValidationError):
        validate_origins(["*"], multi_mode=True)
    assert validate_origins(["*"], multi_mode=False) == ["*"]


def test_accent_language_and_limits_are_bounded():
    from widgets.models import (WidgetValidationError, validate_accent, validate_language,
                                validate_limits)
    assert validate_accent("Teal") == "teal"
    with pytest.raises(WidgetValidationError):
        validate_accent("#ff0000")
    assert validate_language("DE") == "de"
    with pytest.raises(WidgetValidationError):
        validate_language("fr")
    limits = validate_limits({"messages_per_minute": 3}, base={"max_attachments": 1})
    assert limits["messages_per_minute"] == 3 and limits["max_attachments"] == 1
    for bad in ({"messages_per_minute": 0}, {"attachment_max_bytes": 50 * 1024 * 1024},
                {"surprise": 1}):
        with pytest.raises(WidgetValidationError):
            validate_limits(bad)


# ── management ───────────────────────────────────────────────────────────────

def test_create_list_update_snippet_and_audit(single, agent, client):
    widget = _create(client, agent)
    assert widget["widget_id"].startswith("wgt_")
    assert widget["public_key"].startswith("ahw_")
    assert widget["owner_id"] == "local"
    assert widget["allowed_origins"] == [SITE, "http://localhost:3000"]
    assert widget["agent_name"] == "Shop Helper"
    assert widget["limits"]["messages_per_minute"] == 6

    listed = client.get("/api/widgets", params={"workspace": "default"}).json()
    assert [w["widget_id"] for w in listed] == [widget["widget_id"]]

    snippet = client.get(f"/api/widgets/{widget['widget_id']}/snippet").json()
    assert snippet["snippet"] == (
        f'<script src="http://testserver/widget.js" data-widget="{widget["widget_id"]}" '
        f'data-key="{widget["public_key"]}" async></script>')

    patched = client.patch(f"/api/widgets/{widget['widget_id']}",
                           json={"accent": "teal", "limits": {"messages_per_minute": 2}})
    assert patched.status_code == 200, patched.text
    assert patched.json()["accent"] == "teal"
    assert patched.json()["limits"]["messages_per_minute"] == 2
    assert patched.json()["limits"]["max_attachments"] == 3

    bad = client.patch(f"/api/widgets/{widget['widget_id']}", json={"accent": "#fff"})
    assert bad.status_code == 400
    bad = client.post("/api/widgets", json={"name": "x", "agent_id": "no-such-agent"})
    assert bad.status_code == 400

    from common import db
    actions = [r["action"] for r in db.get_conn().execute(
        "SELECT action FROM audit_log WHERE object_type = 'widget' ORDER BY id").fetchall()]
    assert actions == ["widget.create", "widget.update"]


def test_rotating_the_key_cuts_off_the_old_snippet(single, agent, client):
    widget = _create(client, agent)
    old = dict(widget)
    assert client.get(f"/api/widgets/public/{widget['widget_id']}/config",
                      headers=_pub(old)).status_code == 200
    rotated = client.post(f"/api/widgets/{widget['widget_id']}/rotate-key").json()
    assert rotated["public_key"] != old["public_key"]
    refused = client.get(f"/api/widgets/public/{widget['widget_id']}/config", headers=_pub(old))
    assert refused.status_code == 401 and refused.json()["code"] == "bad_key"
    assert client.get(f"/api/widgets/public/{widget['widget_id']}/config",
                      headers=_pub(rotated)).status_code == 200


def test_delete_removes_threads_and_messages(single, agent, client, pipeline):
    widget = _create(client, agent)
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    assert _send(client, widget, token, thread_id).status_code == 200
    assert client.delete(f"/api/widgets/{widget['widget_id']}").json() == {"deleted": True}
    from common import db
    for table in ("widgets", "widget_threads", "widget_messages"):
        assert db.get_conn().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# ── the public guard ─────────────────────────────────────────────────────────

def test_config_needs_key_and_an_allowed_origin(single, agent, client):
    widget = _create(client, agent)
    url = f"/api/widgets/public/{widget['widget_id']}/config"
    ok = client.get(url, headers=_pub(widget))
    assert ok.status_code == 200, ok.text
    config = ok.json()
    assert config["title"] == "Ask us" and config["greeting"] == "Hi! How can we help?"
    assert config["agent_name"] == "Shop Helper"
    # Nothing about where the widget lives or who owns it.
    for secret in ("workspace", "owner_id", "public_key", "allowed_origins", "agent_id"):
        assert secret not in config

    no_origin = client.get(url, headers=_pub(widget, origin=None))
    assert no_origin.status_code == 403 and no_origin.json()["code"] == "origin_not_allowed"
    wrong = client.get(url, headers=_pub(widget, origin="https://evil.example"))
    assert wrong.status_code == 403 and wrong.json()["code"] == "origin_not_allowed"
    no_key = client.get(url, headers={"Origin": SITE})
    assert no_key.status_code == 401 and no_key.json()["code"] == "bad_key"
    missing = client.get("/api/widgets/public/wgt_0000000000000000/config", headers=_pub(widget))
    assert missing.status_code == 401

    client.patch(f"/api/widgets/{widget['widget_id']}", json={"enabled": False})
    off = client.get(url, headers=_pub(widget))
    assert off.status_code == 403 and off.json()["code"] == "widget_disabled"


def test_public_paths_are_open_in_multi_mode(multi, agent, client):
    """No hub credential on the visitor's side, while management needs one."""
    admin = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert admin.status_code == 200, admin.text
    auth = {"Authorization": f"Bearer {admin.json()['token']}"}
    created = client.post("/api/widgets", headers=auth, json={
        "workspace": "default", "name": "w", "agent_id": agent.id, "allowed_origins": [SITE]})
    assert created.status_code == 200, created.text
    widget = created.json()
    assert client.get("/api/widgets", params={"workspace": "default"}).status_code == 401
    assert client.get(f"/api/widgets/public/{widget['widget_id']}/config",
                      headers=_pub(widget)).status_code == 200
    # The wildcard is refused in multi mode.
    wild = client.post("/api/widgets", headers=auth, json={
        "name": "w2", "agent_id": agent.id, "allowed_origins": ["*"]})
    assert wild.status_code == 400


def test_widget_script_is_served_with_cache_headers(single, client):
    response = client.get("/widget.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "max-age=" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "AgentsHubWidget" in response.text
    etag = response.headers["etag"]
    again = client.get("/widget.js", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert client.get("/api/widgets/public/widget.js").status_code == 200


# ── CORS ─────────────────────────────────────────────────────────────────────

def test_preflight_is_answered_per_widget_without_credentials(single, agent, client):
    widget = _create(client, agent)
    url = f"/api/widgets/public/{widget['widget_id']}/threads"
    ok = client.options(url, headers={
        "Origin": SITE, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-widget-key,x-visitor-token"})
    assert ok.status_code == 204
    assert ok.headers["access-control-allow-origin"] == SITE
    assert "x-visitor-token" in ok.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in ok.headers

    refused = client.options(url, headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert refused.status_code == 403
    assert "access-control-allow-origin" not in refused.headers


def test_responses_carry_the_widget_origin_and_never_credentials(single, agent, client):
    widget = _create(client, agent)
    url = f"/api/widgets/public/{widget['widget_id']}/config"
    # localhost:3000 is also one of the dashboard's development origins, for
    # which the global CORS setup allows credentials: the widget's own answer
    # replaces that.
    response = client.get(url, headers=_pub(widget, origin="http://localhost:3000"))
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers
    wrong = client.get(url, headers=_pub(widget, origin="https://evil.example"))
    assert "access-control-allow-origin" not in wrong.headers


def test_global_cors_is_unchanged_for_the_dashboard(single, client):
    response = client.options("/api/agents", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-credentials"] == "true"
    refused = client.options("/api/agents", headers={
        "Origin": SITE, "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in refused.headers


# ── visitors ─────────────────────────────────────────────────────────────────

def test_visitor_tokens_are_signed_bound_and_renewable():
    from widgets import visitor
    minted = visitor.mint_visitor("wgt_aaaa")
    vid = visitor.verify_visitor(minted["visitor_token"], "wgt_aaaa")
    assert vid and vid.startswith("vis_") and vid == minted["visitor_id"]
    # Bound to its widget.
    assert visitor.verify_visitor(minted["visitor_token"], "wgt_bbbb") is None
    # Tampered.
    body, _, mac = minted["visitor_token"].rpartition(".")
    forged = base64.urlsafe_b64encode(json.dumps(
        {"k": "visitor", "w": "wgt_aaaa", "v": "vis_other", "exp": time.time() + 999}).encode()
    ).rstrip(b"=").decode() + "." + mac
    assert visitor.verify_visitor(forged, "wgt_aaaa") is None
    # Expired.
    old = visitor.mint_visitor("wgt_aaaa", ttl_seconds=-5)
    assert visitor.verify_visitor(old["visitor_token"], "wgt_aaaa") is None
    # A preview ticket is not a visitor token and the other way round.
    ticket = visitor.mint_preview("wgt_aaaa", minted_by="local")["ticket"]
    assert visitor.verify_visitor(ticket, "wgt_aaaa") is None
    assert visitor.verify_preview(minted["visitor_token"], "wgt_aaaa") is False
    assert visitor.verify_preview(ticket, "wgt_aaaa") is True


def test_minting_with_a_valid_token_keeps_the_visitor(single, agent, client):
    widget = _create(client, agent)
    token = _visitor(client, widget)
    from widgets.visitor import verify_visitor
    renewed = client.post(f"/api/widgets/public/{widget['widget_id']}/visitor",
                          headers=_pub(widget, token=token)).json()
    assert verify_visitor(renewed["visitor_token"], widget["widget_id"]) == \
        verify_visitor(token, widget["widget_id"])
    bad = client.get(f"/api/widgets/public/{widget['widget_id']}/threads",
                     headers=_pub(widget, token="nonsense.token"))
    assert bad.status_code == 401 and bad.json()["code"] == "visitor_token_invalid"


def test_a_visitor_never_reaches_another_visitors_thread(single, agent, client, pipeline):
    widget = _create(client, agent)
    alice, bob = _visitor(client, widget), _visitor(client, widget)
    alice_thread = _thread(client, widget, alice)
    assert _send(client, widget, alice, alice_thread).status_code == 200

    base = f"/api/widgets/public/{widget['widget_id']}/threads"
    assert client.get(base, headers=_pub(widget, token=bob)).json() == {"threads": []}
    for method in ("get", "delete"):
        response = getattr(client, method)(f"{base}/{alice_thread}", headers=_pub(widget, token=bob))
        assert response.status_code == 404 and response.json()["code"] == "thread_not_found"
    posted = _send(client, widget, bob, alice_thread)
    assert posted.status_code == 404
    # A second widget's visitor cannot use the thread id either.
    other = _create(client, agent, name="Other")
    stranger = _visitor(client, other)
    assert client.get(f"/api/widgets/public/{other['widget_id']}/threads/{alice_thread}",
                      headers=_pub(other, token=stranger)).status_code == 404
    # Alice still has it.
    mine = client.get(f"{base}/{alice_thread}", headers=_pub(widget, token=alice)).json()
    assert [m["role"] for m in mine["messages"]] == ["user", "assistant"]


def test_visitor_delete_hides_the_thread_but_the_owner_still_sees_it(single, agent, client,
                                                                     pipeline):
    widget = _create(client, agent)
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    _send(client, widget, token, thread_id)
    base = f"/api/widgets/public/{widget['widget_id']}/threads"
    assert client.delete(f"{base}/{thread_id}", headers=_pub(widget, token=token)).status_code == 200
    assert client.get(base, headers=_pub(widget, token=token)).json() == {"threads": []}
    owner_view = client.get(f"/api/widgets/{widget['widget_id']}/threads").json()
    assert owner_view[0]["thread_id"] == thread_id
    assert owner_view[0]["visitor_deleted_at"]
    # The owner deletes for real.
    assert client.delete(f"/api/widgets/{widget['widget_id']}/threads/{thread_id}").status_code == 200
    assert client.get(f"/api/widgets/{widget['widget_id']}/threads").json() == []


# ── the turn ─────────────────────────────────────────────────────────────────

def test_a_message_streams_only_visitor_safe_events(single, agent, client, pipeline):
    widget = _create(client, agent)
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    response = _send(client, widget, token, thread_id)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [e["type"] for e in events] == ["meta", "tool_start", "tool_end", "token", "token", "done"]
    assert events[0]["run_id"] == "run-1" and events[0]["thread_id"] == thread_id
    assert events[1] == {"type": "tool_start", "tool": "search_memory"}
    assert events[2] == {"type": "tool_end", "tool": "search_memory"}
    done = events[-1]
    assert done["ok"] is True and done["response"] == "Hello there [1]"
    assert done["citations"] == [{"n": 1, "title": "guide.md › Setup", "snippet": "Install it first"}]
    assert done["title"] == "Where is my order?"
    raw = response.text
    for leak in ("private thought", "secret input", "secret output", "/srv/hub", "pool-9",
                 "file_abc", "entities", "inner_tool", "usage"):
        assert leak not in raw

    request = pipeline["requests"][0]
    assert request.agent_id == agent.id and request.workspace == "default"
    assert request.source == "widget" and request.conversation_id == thread_id
    assert request.history == []

    # The thread keeps both sides, and the owner sees the run behind the reply.
    owner = client.get(f"/api/widgets/{widget['widget_id']}/threads/{thread_id}").json()
    user_msg, reply = owner["messages"]
    assert user_msg["role"] == "user" and user_msg["text"] == "Where is my order?"
    assert reply["run_id"] == "run-1" and reply["tokens"] == 120 and reply["status"] == "ok"

    # The next turn carries the history.
    _send(client, widget, token, thread_id, text="And when?")
    history = [(h.role, h.content) for h in pipeline["requests"][1].history]
    assert history == [("user", "Where is my order?"), ("agent", "Hello there [1]")]


def test_a_failed_turn_says_failed_without_the_error_text(single, agent, client, pipeline):
    pipeline["turn"] = lambda request: [
        {"type": "meta", "run_id": "run-x"},
        {"type": "error", "source": "llm", "error": "Traceback: key sk-live-123 rejected"},
        {"type": "done", "ok": False, "response": "Error: key sk-live-123 rejected",
         "error": "key sk-live-123 rejected", "run_id": "run-x"},
    ]
    widget = _create(client, agent)
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    response = _send(client, widget, token, thread_id)
    events = _events(response.text)
    assert events[-1]["type"] == "done" and events[-1]["ok"] is False
    assert events[-1]["error"] == "failed"
    assert "sk-live" not in response.text and "Traceback" not in response.text


def test_a_handoff_moves_the_thread_to_the_next_agent(single, agent, client, pipeline):
    def turn(request):
        if request.agent_id == "shop-helper":
            return [
                {"type": "meta", "run_id": "run-a"},
                {"type": "token", "token": "Let me pass you on."},
                {"type": "handoff", "from_agent_id": "shop-helper", "to_agent_id": "shop-billing",
                 "to_agent_name": "Billing Desk", "reason": "billing question",
                 "history_filter": "last_user", "run_id": "run-a", "next_run_id": "run-b",
                 "from_response": "Let me pass you on."},
                {"type": "meta", "run_id": "run-b"},
                {"type": "token", "token": "Billing here."},
                {"type": "done", "ok": True, "response": "Billing here.", "run_id": "run-b",
                 "agent_id": "shop-billing", "usage": {"total_tokens": 10}},
            ]
        return [{"type": "meta", "run_id": "run-c"},
                {"type": "done", "ok": True, "response": "Still billing.", "run_id": "run-c",
                 "agent_id": request.agent_id}]

    pipeline["turn"] = turn
    widget = _create(client, agent)
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    events = _events(_send(client, widget, token, thread_id, text="A refund please").text)
    handoff = next(e for e in events if e["type"] == "handoff")
    assert handoff == {"type": "handoff", "to_agent_name": "Billing Desk",
                       "from_response": "Let me pass you on."}
    assert "billing question" not in json.dumps(events)

    _send(client, widget, token, thread_id, text="Thanks")
    assert pipeline["requests"][1].agent_id == "shop-billing"

    visitor_view = client.get(f"/api/widgets/public/{widget['widget_id']}/threads/{thread_id}",
                              headers=_pub(widget, token=token)).json()
    first_reply = visitor_view["messages"][1]
    assert first_reply["text"] == "Let me pass you on."
    assert first_reply["handoff"] == {"to_agent_name": "Billing Desk"}
    assert visitor_view["thread"]["agent_name"] == "Billing Desk"


def test_messages_per_minute_are_limited_per_visitor(single, agent, client, pipeline):
    widget = _create(client, agent, limits={"messages_per_minute": 2})
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    assert _send(client, widget, token, thread_id).status_code == 200
    assert _send(client, widget, token, thread_id).status_code == 200
    third = _send(client, widget, token, thread_id)
    assert third.status_code == 429 and third.json()["code"] == "rate_limited"
    assert int(third.headers["retry-after"]) >= 1
    # Another visitor is not affected.
    other = _visitor(client, widget)
    assert _send(client, widget, other, _thread(client, widget, other)).status_code == 200


def test_the_daily_token_cap_stops_the_widget(single, agent, client, pipeline):
    widget = _create(client, agent, limits={"tokens_per_day": 100})
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    assert _send(client, widget, token, thread_id).status_code == 200  # 120 tokens used
    capped = _send(client, widget, token, thread_id)
    assert capped.status_code == 429 and capped.json()["code"] == "daily_limit"


def test_attachments_are_checked_against_the_widget_limits(single, agent, client, pipeline):
    widget = _create(client, agent, limits={"attachment_max_bytes": 64, "max_attachments": 2})
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)

    def att(name, data, mime=None):
        return {"name": name, "mime_type": mime, "data_b64": base64.b64encode(data).decode()}

    too_big = _send(client, widget, token, thread_id, attachments=[att("big.txt", b"x" * 65)])
    assert too_big.status_code == 413 and too_big.json()["code"] == "attachment_too_large"
    too_many = _send(client, widget, token, thread_id,
                     attachments=[att(f"{i}.txt", b"x") for i in range(3)])
    assert too_many.status_code == 400 and too_many.json()["code"] == "too_many_attachments"

    ok = _send(client, widget, token, thread_id, text="",
               attachments=[att("notes.txt", b"order 42", "text/plain"),
                            att("scan.png", b"\x89PNG\r\n", "image/png")])
    assert ok.status_code == 200, ok.text
    sent = pipeline["requests"][-1].attachments
    assert sent[0].filename == "notes.txt" and sent[0].content == "order 42"
    assert sent[1].content_b64 and sent[1].store_to_workspace is True
    owner = client.get(f"/api/widgets/{widget['widget_id']}/threads/{thread_id}").json()
    assert owner["messages"][0]["attachments"] == ["notes.txt", "scan.png"]

    off = _create(client, agent, name="No files", limits={"max_attachments": 0})
    t2 = _visitor(client, off)
    refused = _send(client, off, t2, _thread(client, off, t2), attachments=[att("a.txt", b"x")])
    assert refused.status_code == 400 and refused.json()["code"] == "attachments_disabled"


def test_a_message_that_is_too_long_or_empty_is_refused(single, agent, client, pipeline):
    widget = _create(client, agent)
    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    long = _send(client, widget, token, thread_id, text="x" * 9000)
    assert long.status_code == 400 and long.json()["code"] == "message_too_long"
    empty = _send(client, widget, token, thread_id, text="   ")
    assert empty.status_code == 400 and empty.json()["code"] == "empty_message"
    assert pipeline["requests"] == []


# ── preview and owner checks ─────────────────────────────────────────────────

def test_the_preview_ticket_lets_the_hub_origin_in(single, agent, client, pipeline):
    widget = _create(client, agent, enabled=False)
    url = f"/api/widgets/public/{widget['widget_id']}/config"
    hub = "http://localhost:5173"  # the dashboard's development origin
    assert client.get(url, headers=_pub(widget, origin=hub)).status_code == 403
    ticket = client.post(f"/api/widgets/{widget['widget_id']}/preview").json()["ticket"]
    previewed = client.get(url, headers=_pub(widget, origin=hub, **{"X-Widget-Preview": ticket}))
    assert previewed.status_code == 200 and previewed.json()["preview"] is True
    # Same origin (no Origin header on a same-origin GET) is the hub too.
    same = client.get(url, headers=_pub(widget, origin=None, **{"X-Widget-Preview": ticket}))
    assert same.status_code == 200
    # Not a way in from anywhere else.
    elsewhere = client.get(url, headers=_pub(widget, origin="https://evil.example",
                                             **{"X-Widget-Preview": ticket}))
    assert elsewhere.status_code == 403
    # Threads opened from the preview are flagged for the owner.
    token = client.post(f"/api/widgets/public/{widget['widget_id']}/visitor",
                        headers=_pub(widget, origin=hub, **{"X-Widget-Preview": ticket})).json()
    thread = client.post(f"/api/widgets/public/{widget['widget_id']}/threads", json={},
                         headers=_pub(widget, origin=hub, token=token["visitor_token"],
                                      **{"X-Widget-Preview": ticket})).json()
    owner = client.get(f"/api/widgets/{widget['widget_id']}/threads").json()
    assert owner[0]["thread_id"] == thread["thread_id"] and owner[0]["preview"] is True


def test_multi_mode_needs_an_editor_and_an_owner_who_still_can(multi, agent, client, pipeline):
    from common import identity
    from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
    create_workspace_folder("shop")
    payload = {"workspace": "shop", "name": "Shop", "agent_id": agent.id,
               "allowed_origins": [SITE]}
    # A new workspace runs only what its allowed_agents list names.
    assert client.post("/api/widgets", json=payload).status_code == 401
    allowed = list(get_workspace_metadata("shop").get("allowed_agents") or [])
    update_workspace_metadata("shop", {"allowed_agents": allowed + [agent.id]})
    admin = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    admin_auth = {"Authorization": f"Bearer {admin.json()['token']}"}
    editor = identity.create_user("ed", PASSWORD)
    viewer = identity.create_user("vi", PASSWORD)
    identity.set_member("shop", editor["id"], "editor")
    identity.set_member("shop", viewer["id"], "viewer")

    def login(name):
        token = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
        return {"Authorization": f"Bearer {token.json()['token']}"}

    ed, vi = login("ed"), login("vi")
    assert client.post("/api/widgets", headers=vi, json=payload).status_code == 403
    other_agent = client.post("/api/widgets", headers=ed, json={**payload, "agent_id": "shop-billing"})
    assert other_agent.status_code == 400 and "not available" in other_agent.json()["detail"]
    created = client.post("/api/widgets", headers=ed, json=payload)
    assert created.status_code == 200, created.text
    widget = created.json()
    assert widget["owner_id"] == editor["id"]
    # A viewer reads it, cannot change it.
    assert client.get(f"/api/widgets/{widget['widget_id']}", headers=vi).status_code == 200
    assert client.post(f"/api/widgets/{widget['widget_id']}/rotate-key",
                       headers=vi).status_code == 403

    token = _visitor(client, widget)
    thread_id = _thread(client, widget, token)
    assert _send(client, widget, token, thread_id).status_code == 200

    # The owner is demoted: the widget stops answering on their behalf.
    identity.set_member("shop", editor["id"], "viewer")
    refused = _send(client, widget, token, thread_id)
    assert refused.status_code == 403 and refused.json()["code"] == "widget_unavailable"
    listed = client.get("/api/widgets", headers=admin_auth, params={"workspace": "shop"}).json()
    assert listed[0]["owner_ok"] is False
