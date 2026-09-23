"""`ah secrets`: keygen, and list/set/delete over both CLI backends.

Direct mode runs common.secrets in this process against the test database;
the HTTP path is checked for the requests it would make, since a server per
test would only be testing FastAPI (the routes have their own tests).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import cli.main as cli

runner = CliRunner()


@pytest.fixture
def direct(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "cli test passphrase", raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)
    monkeypatch.setattr(cli, "_backend", SimpleNamespace(kind="direct"))


def test_keygen_prints_a_usable_fernet_key():
    from cryptography.fernet import Fernet
    out = runner.invoke(cli.app, ["secrets", "keygen"])
    assert out.exit_code == 0
    Fernet(out.stdout.strip().encode())  # raises if it is not a key


def test_direct_set_list_delete(direct):
    set_ = runner.invoke(cli.app, ["secrets", "set", "API_KEY", "--value", "sk-cli-value-123",
                                   "--workspace", "ws", "--agent", "a1"])
    assert set_.exit_code == 0, set_.stdout
    listed = runner.invoke(cli.app, ["secrets", "list", "--workspace", "ws", "--json"])
    assert listed.exit_code == 0
    assert "API_KEY" in listed.stdout and "a1" in listed.stdout
    assert "sk-cli-value-123" not in listed.stdout
    from common import secrets
    assert secrets.get_secret("ws", "API_KEY", agent_id="a1") == "sk-cli-value-123"
    gone = runner.invoke(cli.app, ["secrets", "delete", "API_KEY", "--workspace", "ws",
                                   "--agent", "a1"])
    assert gone.exit_code == 0
    assert secrets.list_secrets("ws") == []
    again = runner.invoke(cli.app, ["secrets", "delete", "API_KEY", "--workspace", "ws",
                                    "--agent", "a1"])
    assert again.exit_code == 1


def test_direct_value_from_stdin(direct):
    out = runner.invoke(cli.app, ["secrets", "set", "FROM_STDIN", "--value", "-", "--workspace", "ws"],
                        input="piped-value-long\n")
    assert out.exit_code == 0, out.stdout
    from common import secrets
    assert secrets.get_secret("ws", "FROM_STDIN") == "piped-value-long"


def test_direct_refusal_is_a_clean_error(direct, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "", raising=False)
    out = runner.invoke(cli.app, ["secrets", "set", "X", "--value", "v", "--workspace", "ws"])
    assert out.exit_code == 1
    assert "keygen" in out.stdout
    bad = runner.invoke(cli.app, ["secrets", "set", "lower", "--value", "v", "--workspace", "ws"])
    assert bad.exit_code == 1


def test_http_uses_the_routes(monkeypatch):
    calls = []

    def request(method, path, *, params=None, json=None, timeout=None):
        calls.append((method, path, params, json))
        return [] if method == "GET" else {}

    monkeypatch.setattr(cli, "_backend", SimpleNamespace(kind="http", _request=request))
    runner.invoke(cli.app, ["secrets", "set", "T", "--value", "v", "--workspace", "ws",
                            "--user", "u1"])
    runner.invoke(cli.app, ["secrets", "list", "--workspace", "ws"])
    runner.invoke(cli.app, ["secrets", "delete", "T", "--workspace", "ws", "--user", "u1"])
    assert calls == [
        ("PUT", "/api/workspaces/ws/secrets/T", None,
         {"value": "v", "agent_id": None, "user_id": "u1"}),
        ("GET", "/api/workspaces/ws/secrets", None, None),
        ("DELETE", "/api/workspaces/ws/secrets/T", {"user_id": "u1"}, None),
    ]
