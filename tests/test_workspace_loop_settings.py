"""
``GET/PUT /api/workspaces/{name}/loop-settings`` (dashboard/backend/routes/
agent_loop_settings.py): the workspace ``settings.loop`` block the agent
loop's extensions read through ``agents.loop_ext.settings.loop_setting``
(compaction, tool search, Anthropic native features, strict tool schemas).

GET reports the stored block plus what each key resolves to once the
environment and the built-in defaults are folded in, the same order
``loop_setting`` uses for a built agent; PUT validates a partial update,
lets ``null`` remove a key, and keeps every other ``settings`` key (the tool
policy, the agent mode, ...) untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata


@pytest.fixture
def ws():
    """A workspace of this test's own, so tests never share one another's
    loop settings block."""
    name = f"loop-ws-{uuid4().hex[:8]}"
    create_workspace_folder(name)
    return name


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_loop_settings as loop_settings_routes

    app = FastAPI()
    app.include_router(loop_settings_routes.router)
    return TestClient(app)


DEFAULTS = {
    "compaction": True,
    "compaction_fraction": 0.7,
    "compaction_keep": 3,
    "tool_search_threshold": 30,
    "native": True,
    "strict_tools": False,
    "view_focus": True,
    "tool_output_spill_chars": 20000,
    "advisor_max_calls": 5,
    "advisor_max_answer_chars": 4000,
}

ENV_NAMES = {
    "compaction": "AGENTS_HUB_LOOP_COMPACTION",
    "compaction_fraction": "AGENTS_HUB_LOOP_COMPACTION_FRACTION",
    "compaction_keep": "AGENTS_HUB_LOOP_COMPACTION_KEEP",
    "tool_search_threshold": "AGENTS_HUB_TOOL_SEARCH_THRESHOLD",
    "native": "AGENTS_HUB_LOOP_NATIVE",
    "strict_tools": "AGENTS_HUB_LOOP_STRICT_TOOLS",
    "view_focus": "AGENTS_HUB_LOOP_VIEW_FOCUS",
    "tool_output_spill_chars": "AGENTS_HUB_LOOP_TOOL_OUTPUT_SPILL_CHARS",
    "advisor_max_calls": "AGENTS_HUB_LOOP_ADVISOR_MAX_CALLS",
    "advisor_max_answer_chars": "AGENTS_HUB_LOOP_ADVISOR_MAX_ANSWER_CHARS",
}


def _get(client, ws):
    resp = client.get(f"/api/workspaces/{ws}/loop-settings")
    assert resp.status_code == 200
    return resp.json()


def test_get_defaults_with_nothing_set(client, ws):
    body = _get(client, ws)
    assert body["settings"] == {}
    assert body["effective"] == DEFAULTS
    assert body["defaults"] == DEFAULTS
    assert body["env"] == ENV_NAMES


def test_get_env_override_wins_over_default(client, ws, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_TOOL_SEARCH_THRESHOLD", "12")
    monkeypatch.setenv("AGENTS_HUB_LOOP_COMPACTION", "off")
    body = _get(client, ws)
    assert body["effective"]["tool_search_threshold"] == 12
    assert body["effective"]["compaction"] is False
    # Untouched keys still show their built-in default.
    assert body["effective"]["native"] is True


def test_get_workspace_value_wins_over_env(client, ws, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_TOOL_SEARCH_THRESHOLD", "12")
    update_workspace_metadata(ws, {"settings": {"loop": {"tool_search_threshold": 50}}})
    body = _get(client, ws)
    assert body["settings"] == {"tool_search_threshold": 50}
    assert body["effective"]["tool_search_threshold"] == 50


def test_put_partial_update_round_trips(client, ws):
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["settings"] == {"compaction": False}
    assert body["effective"]["compaction"] is False
    assert body["effective"]["native"] is True  # untouched, still the default


def test_put_keeps_other_loop_keys(client, ws):
    client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": False, "native": False})
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction_keep": 5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings"] == {"compaction": False, "native": False, "compaction_keep": 5}


def test_put_keeps_other_settings_keys(client, ws):
    """A save here must not clobber the tool policy or the agent mode, which
    live in the same ``settings`` block but are owned by other routes."""
    update_workspace_metadata(ws, {"settings": {"agent_mode": "docker", "tool_policy": {"*": "auto"}}})
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": False})
    assert resp.status_code == 200, resp.text
    settings = get_workspace_metadata(ws).get("settings") or {}
    assert settings["agent_mode"] == "docker"
    assert settings["tool_policy"] == {"*": "auto"}
    assert settings["loop"] == {"compaction": False}


def test_put_null_resets_a_key(client, ws):
    client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": False, "native": False})
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": None})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["settings"] == {"native": False}
    assert body["effective"]["compaction"] is True  # back to the built-in default


def test_put_null_on_every_key_drops_the_loop_block(client, ws):
    client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": False})
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={"compaction": None})
    assert resp.status_code == 200, resp.text
    settings = get_workspace_metadata(ws).get("settings") or {}
    assert "loop" not in settings


@pytest.mark.parametrize("key, value", [
    ("compaction", "yes"),
    ("compaction_fraction", 0.0),
    ("compaction_fraction", 1.0),
    ("compaction_fraction", "0.5"),
    ("compaction_keep", 0),
    ("tool_search_threshold", 0),
    ("native", 1),
    ("strict_tools", "on"),
])
def test_put_rejects_bad_values(client, ws, key, value):
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={key: value})
    assert resp.status_code == 400
    assert key in resp.json()["detail"]


def test_put_rejects_unknown_key(client, ws):
    resp = client.put(f"/api/workspaces/{ws}/loop-settings", json={"not_a_loop_key": True})
    assert resp.status_code == 400
    assert "not_a_loop_key" in resp.json()["detail"]


def test_put_missing_workspace_is_404(client):
    resp = client.put("/api/workspaces/does-not-exist-loop/loop-settings", json={"compaction": False})
    assert resp.status_code == 404


def test_an_agent_built_in_this_workspace_sees_the_saved_value(client, ws):
    """The value the route writes is the one ``loop_setting`` hands a built
    agent: the same path the compaction and tool-search extensions read at
    build time."""
    from agents.loop_ext.settings import loop_setting

    resp = client.put(f"/api/workspaces/{ws}/loop-settings",
                      json={"compaction_fraction": 0.4, "tool_search_threshold": 12})
    assert resp.status_code == 200, resp.text

    agent = SimpleNamespace(workspace=ws, _llm=object())
    assert loop_setting(agent, "compaction_fraction", 0.7) == 0.4
    assert loop_setting(agent, "tool_search_threshold", 30) == 12
    # A key never set on this workspace still falls back to its default.
    assert loop_setting(agent, "native", True) is True
