"""
cli/rest_main.py: the entry point for the REST-only PyInstaller build
(scripts/build_cli_binary.sh). Its one job is refusing to run at all unless
AGENTS_HUB_URL is set, before cli.main (and so cli.backend.DirectBackend,
whose methods import the service one call at a time) is ever imported, so a
binary built without the service's dependencies never gets near them.

--help is the deliberate exception: it must work with nothing configured, so
someone handed this binary with no setup at all can still find out what it
needs. See cli/rest_main.py's own docstring and docs/cli.md.
"""
from __future__ import annotations

import pytest

from cli import rest_main


def test_refuses_to_run_without_agents_hub_url(monkeypatch, capsys):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["ah", "agent", "list"])
    with pytest.raises(SystemExit) as excinfo:
        rest_main.main()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "AGENTS_HUB_URL" in err
    assert "not set" in err
    assert "export AGENTS_HUB_URL=" in err
    assert "Traceback" not in err


def test_a_blank_agents_hub_url_is_also_refused(monkeypatch, capsys):
    """An exported-but-empty variable is a very easy way to get a confusing
    failure (the same reasoning as cli/backend.py's blank-URL handling)."""
    monkeypatch.setenv("AGENTS_HUB_URL", "   ")
    monkeypatch.setattr("sys.argv", ["ah", "agent", "list"])
    with pytest.raises(SystemExit) as excinfo:
        rest_main.main()
    assert excinfo.value.code == 1
    assert "AGENTS_HUB_URL" in capsys.readouterr().err


def test_help_works_with_nothing_configured(monkeypatch, capsys):
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["ah", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        rest_main.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "AGENTS_HUB_URL is not set" not in out
    assert "Usage" in out


def test_no_arguments_also_shows_help_not_the_url_error(monkeypatch, capsys):
    """typer's no_args_is_help turns a bare `ah` into help too; the guard must
    recognise that case as well, not just an explicit --help."""
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["ah"])
    with pytest.raises(SystemExit):
        rest_main.main()
    out = capsys.readouterr().out
    assert "AGENTS_HUB_URL is not set" not in out


def test_a_configured_url_skips_the_refusal(monkeypatch, capsys):
    """With AGENTS_HUB_URL set, main() falls through to cli.main.app(): still
    exercised here as --help, so no real network call happens in this test
    (an unreachable URL's connection error is covered by cli/backend.py's own
    tests, and by the cli-binary CI job against the built executable)."""
    monkeypatch.setenv("AGENTS_HUB_URL", "http://127.0.0.1:1")
    monkeypatch.setattr("sys.argv", ["ah", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        rest_main.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "AGENTS_HUB_URL is not set" not in out
    assert "Usage" in out
