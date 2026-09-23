"""In-process runs bind the secret scope (docs/secrets.md).

A subprocess run receives its secrets through the environment the launcher
builds. A chat turn, a decomposer thread and a delegated worker run inside
the backend process, so they enter ``common.secrets.activate`` instead: a
tool that asks ``common.secrets.get(name)`` then sees the same allowlist a
subprocess would. These tests only check that the scope is bound at the
right moment, with the right agent and the right person.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from chat.models import ChatRequest
from chat.send import send_chat_message


def _fake_agent_spec():
    return SimpleNamespace(tools=[], model_overrides=lambda: {})


def test_a_chat_turn_runs_under_the_agents_secret_scope(monkeypatch):
    import agents.agent_invoke as agent_invoke
    import agents.registry as registry
    import chat.send as send_mod
    from common import identity, secrets

    monkeypatch.setattr(registry, "get_agent", lambda agent_id: _fake_agent_spec())
    seen = {}

    def _create(agent_id, workspace=None, **kw):
        seen["scope"] = secrets.active_scope()
        return SimpleNamespace(provider="prov", model="mdl")

    def _invoke(*a, **kw):
        seen["invoke_scope"] = secrets.active_scope()
        return SimpleNamespace(result=SimpleNamespace(ok=True, agent_output="Hi", error=None))

    monkeypatch.setattr(send_mod, "create_agent", _create)
    monkeypatch.setattr(agent_invoke, "invoke_agent", _invoke)

    token = identity.set_current_user("user-42")
    try:
        out = asyncio.run(send_chat_message(ChatRequest(agent_id="test_agent", message="hello")))
    finally:
        identity.reset_current_user(token)
    assert out["ok"] is True
    # Both the build and the invocation ran inside the scope, for this agent
    # and this person; the scope is gone once the turn is over.
    assert seen["scope"][1:] == ("test_agent", "user-42")
    assert seen["invoke_scope"] == seen["scope"]
    assert secrets.active_scope() is None


def test_the_scope_hands_out_only_declared_names(monkeypatch):
    """The round trip: a declared name resolves inside the scope, an
    undeclared one and any name outside the scope resolve to nothing."""
    from common import secrets
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "test-passphrase", raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)
    monkeypatch.setattr(secrets, "allowed_for_agent",
                        lambda agent_id: {"GITHUB_TOKEN"} if agent_id == "bot" else set())
    secrets.set_secret("w1", "GITHUB_TOKEN", "ghp_secret")
    secrets.set_secret("w1", "SLACK_TOKEN", "xoxb-secret")

    assert secrets.get("GITHUB_TOKEN") is None
    with secrets.activate("w1", "bot", "user-1"):
        assert secrets.get("GITHUB_TOKEN") == "ghp_secret"
        assert secrets.get("SLACK_TOKEN") is None
    with secrets.activate("w1", "other", "user-1"):
        assert secrets.get("GITHUB_TOKEN") is None
    assert secrets.get("GITHUB_TOKEN") is None
