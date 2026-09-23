"""
`ah auth`: whoami and personal API keys.

Direct mode has no named user (it *is* the local operator), so the keys
commands need an explicit ``--user`` there, exercised against the real
``DirectBackend`` over the throwaway database ``tests/conftest.py`` points
every test at. The HTTP path is exercised against a fake backend standing in
for a running service: real ``requests`` calls belong to
``tests/test_cli_backend.py``'s structural checks, not here.
"""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from cli import main as cli
from cli.backend import BackendError

runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_backend(monkeypatch):
    """Every test picks its own backend; never leak one into the next."""
    monkeypatch.setattr(cli, "_backend", None)
    yield
    cli._backend = None


def _key_id_from(output: str) -> str:
    match = re.search(r"id (\S+),", output)
    assert match, f"no key id in output:\n{output}"
    return match.group(1)


# ── direct mode: no named user ───────────────────────────────────────────────

def test_direct_whoami_is_the_local_operator(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    result = runner.invoke(cli.app, ["auth", "whoami"])
    assert result.exit_code == 0, result.output
    assert "local" in result.output
    assert "admin" in result.output


def test_direct_keys_without_user_explains_the_requirement(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    result = runner.invoke(cli.app, ["auth", "keys", "list"])
    assert result.exit_code == 1
    assert "--user" in result.output
    assert "AGENTS_HUB_URL" in result.output


def test_direct_keys_create_list_revoke_for_a_named_user(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    from common import identity
    identity.create_user("alice", "hunter2-but-longer", role="member")

    created = runner.invoke(
        cli.app, ["auth", "keys", "create", "--name", "laptop", "--user", "alice"])
    assert created.exit_code == 0, created.output
    assert "New API key" in created.output
    assert "ahk_" in created.output
    key_id = _key_id_from(created.output)

    listed = runner.invoke(cli.app, ["auth", "keys", "list", "--user", "alice"])
    assert listed.exit_code == 0, listed.output
    assert key_id in listed.output
    assert "laptop" in listed.output

    revoked = runner.invoke(cli.app, ["auth", "keys", "revoke", key_id, "--user", "alice"])
    assert revoked.exit_code == 0, revoked.output
    assert "revoked" in revoked.output

    from common import api_keys
    user = identity.get_user_by_username("alice")
    assert api_keys.list_keys(user["id"]) == []


def test_direct_keys_for_an_unknown_user_fails_clearly(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    result = runner.invoke(cli.app, ["auth", "keys", "list", "--user", "nobody"])
    assert result.exit_code == 1
    assert "no such user" in result.output


def test_a_scoped_direct_key_records_its_workspaces(monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    from common import identity
    identity.create_user("bob", "hunter2-but-longer")

    result = runner.invoke(cli.app, [
        "auth", "keys", "create", "--user", "bob", "--name", "scoped",
        "--workspace", "alpha", "--workspace", "beta", "--expires-days", "30",
    ])
    assert result.exit_code == 0, result.output
    assert "alpha, beta" in result.output


# ── HTTP mode: a fake backend stands in for a running service ───────────────

class FakeHttpBackend:
    """Just enough of HttpBackend's shape to drive the CLI commands."""

    kind = "http"

    def __init__(self):
        self.calls = []

    def whoami(self):
        self.calls.append(("whoami",))
        return {"id": "u1", "username": "alice", "role": "member",
                "via": "api_key", "mode": "multi"}

    def list_api_keys(self, username=None):
        self.calls.append(("list", username))
        return [{"id": "k1", "name": "ci", "hint": "wxyz", "workspaces": None,
                 "expires_at": None, "last_used_at": None}]

    def create_api_key(self, username=None, name="", workspaces=None, expires_in_days=None):
        self.calls.append(("create", username, name, workspaces, expires_in_days))
        return {"id": "k2", "name": name, "key": "ahk_fakehttpsecret",
                "workspaces": workspaces, "expires_at": None}

    def revoke_api_key(self, key_id, username=None):
        self.calls.append(("revoke", key_id, username))
        return {"revoked": True}


@pytest.fixture
def fake_http(monkeypatch):
    backend = FakeHttpBackend()
    monkeypatch.setattr(cli, "_backend", backend)
    return backend


def test_http_whoami_reports_the_authenticated_principal(fake_http):
    result = runner.invoke(cli.app, ["auth", "whoami"])
    assert result.exit_code == 0, result.output
    assert "alice" in result.output
    assert "api_key" in result.output


def test_http_keys_create_goes_through_rest(fake_http):
    result = runner.invoke(cli.app, ["auth", "keys", "create", "--name", "ci"])
    assert result.exit_code == 0, result.output
    assert "ahk_fakehttpsecret" in result.output
    assert fake_http.calls == [("create", None, "ci", None, None)]


def test_http_keys_ignores_user_but_warns(fake_http):
    result = runner.invoke(cli.app, ["auth", "keys", "list", "--user", "somebody"])
    assert result.exit_code == 0, result.output
    assert "ignored" in result.output
    assert "ci" in result.output  # the fake's canned key, listed anyway


def test_http_keys_revoke(fake_http):
    result = runner.invoke(cli.app, ["auth", "keys", "revoke", "k1"])
    assert result.exit_code == 0, result.output
    assert "revoked" in result.output
    assert fake_http.calls == [("revoke", "k1", None)]


def test_the_direct_backend_error_message_is_reused(monkeypatch):
    """Both call sites (list/create/revoke) share the exact same explanation,
    so a user sees one consistent message whichever they hit first."""
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    from cli.backend import DirectBackend
    backend = DirectBackend()
    with pytest.raises(BackendError, match="AGENTS_HUB_URL"):
        backend.list_api_keys(None)
    with pytest.raises(BackendError, match="AGENTS_HUB_URL"):
        backend.create_api_key(None, "x")
    with pytest.raises(BackendError, match="AGENTS_HUB_URL"):
        backend.revoke_api_key("k1", None)
