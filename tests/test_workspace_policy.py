"""The workspace tool policy: the approval gate flag and the hook config.

``GET/PUT /api/workspaces/{name}/policy`` is the one place an operator edits
both, so what it stores has to be exactly what the agent process reads back:
``tools.approval.approval_gate_enabled`` for the gate and
``agents.hooks.load_hooks`` for the hooks. These tests round-trip through the
route and then read the value the way the run does, rather than asserting on
the metadata shape twice.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from workspace import create_workspace_folder


@pytest.fixture
def ws():
    """A workspace of this test's own.

    Workspace metadata lives under one state root for the whole session, so a
    shared name would carry the previous test's policy into the next one.
    """
    name = f"policy-ws-{uuid4().hex[:8]}"
    create_workspace_folder(name)
    return name


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import workspaces as workspace_routes

    app = FastAPI()
    app.include_router(workspace_routes.router)
    return TestClient(app)


def _policy(client, ws):
    resp = client.get(f"/api/workspaces/{ws}/policy")
    assert resp.status_code == 200
    return resp.json()


def test_policy_defaults_to_off_and_no_hooks(client, ws):
    assert _policy(client, ws) == {"require_tool_approval": False, "hooks": {}}


def test_policy_round_trips(client, ws):
    payload = {
        "require_tool_approval": True,
        "hooks": {
            "PreToolUse": [
                {"matcher": "run_shell", "type": "command",
                 "command": "./scripts/check.sh", "timeout": 10},
            ],
            "PostToolUse": [
                {"matcher": ".*", "type": "http", "url": "https://example.test/audit"},
            ],
        },
    }
    resp = client.put(f"/api/workspaces/{ws}/policy", json=payload)
    assert resp.status_code == 200, resp.text

    stored = _policy(client, ws)
    assert stored["require_tool_approval"] is True
    assert stored["hooks"]["PreToolUse"][0]["command"] == "./scripts/check.sh"
    assert stored["hooks"]["PostToolUse"][0]["url"] == "https://example.test/audit"


def test_policy_leaves_other_settings_alone(client, ws):
    from workspace import get_workspace_metadata, update_workspace_metadata

    update_workspace_metadata(ws, {"settings": {"agent_mode": "docker"}})
    client.put(f"/api/workspaces/{ws}/policy", json={"require_tool_approval": True})
    settings = get_workspace_metadata(ws).get("settings") or {}
    assert settings["agent_mode"] == "docker"
    assert settings["require_tool_approval"] is True


@pytest.mark.parametrize("entry, expected", [
    ({"matcher": "run_shell", "type": "shout", "command": "x"}, "PreToolUse[0]"),
    ({"matcher": "run_shell", "type": "command"}, "PreToolUse[0]"),
    ({"matcher": "run_shell", "type": "http"}, "PreToolUse[0]"),
    ({"matcher": 42, "type": "command", "command": "x"}, "PreToolUse[0]"),
    ({"matcher": "x", "type": "command", "command": "x", "timeout": "soon"}, "PreToolUse[0]"),
])
def test_invalid_hook_entry_is_rejected_with_its_index(client, ws, entry, expected):
    resp = client.put(
        f"/api/workspaces/{ws}/policy",
        json={"hooks": {"PreToolUse": [entry]}},
    )
    assert resp.status_code == 400
    assert expected in resp.json()["detail"]


def test_bad_index_is_the_entry_that_is_wrong(client, ws):
    good = {"matcher": "a", "type": "command", "command": "ok.sh"}
    resp = client.put(
        f"/api/workspaces/{ws}/policy",
        json={"hooks": {"PreToolUse": [good, good, {"type": "http"}]}},
    )
    assert resp.status_code == 400
    assert "PreToolUse[2]" in resp.json()["detail"]


def test_unknown_event_is_rejected(client, ws):
    resp = client.put(
        f"/api/workspaces/{ws}/policy",
        json={"hooks": {"WheneverYouLike": []}},
    )
    assert resp.status_code == 400


def test_approval_gate_reads_the_saved_flag(client, ws):
    """The flag the route writes is the one the gate reads at run time."""
    from tools import approval

    assert approval.approval_gate_enabled(ws) is False
    client.put(f"/api/workspaces/{ws}/policy", json={"require_tool_approval": True})
    assert approval.approval_gate_enabled(ws) is True
    client.put(f"/api/workspaces/{ws}/policy", json={"require_tool_approval": False})
    assert approval.approval_gate_enabled(ws) is False


def test_hooks_are_loaded_by_the_agent_hook_loader(client, ws):
    from agents import hooks

    client.put(f"/api/workspaces/{ws}/policy", json={"hooks": {
        "PreToolUse": [{"matcher": "run_shell", "type": "command", "command": "./gate.sh"}],
    }})
    loaded = hooks.load_hooks(ws)
    assert loaded[hooks.PRE_TOOL_USE][0]["command"] == "./gate.sh"
