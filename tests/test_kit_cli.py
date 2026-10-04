"""``ah kit``: list, show, install (cli/commands/kit.py), driven against the
real app in process the same way the ``ah apply`` CLI tests exercise the
engine, through ``cli.main.hub().request``.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    from agents import prompt_assembly
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    return defs


@pytest.fixture
def fake(monkeypatch, tmp_path):
    from cli import main as cli_main
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    from fastapi.testclient import TestClient
    from cli.backend import BackendError
    from dashboard.backend.main import app

    client = TestClient(app)

    def request(method, path, *, params=None, json=None):
        r = client.request(method, path, params=params, json=json)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise BackendError(f"{r.status_code}: {detail}")
        return r.json() if r.content else None

    monkeypatch.setattr(cli_main, "_backend", SimpleNamespace(request=request))
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    # ah.lock-style files for ``ah kit install`` default to the state dir;
    # point it at a throwaway one so the test never touches the real .agents_hub.
    monkeypatch.setenv("AGENTS_HUB_ROOT", str(tmp_path / "hubroot"))
    return tmp_path


def _run(*args):
    from cli import main as cli_main
    return CliRunner().invoke(cli_main.app, ["kit", *args])


def test_kit_list_shows_the_shipped_kits(fake):
    result = _run("list")
    assert result.exit_code == 0, result.output
    assert "support" in result.output
    assert "finance" in result.output
    assert "recruiting" in result.output


def test_kit_list_json(fake):
    result = _run("list", "--json")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert {r["id"] for r in rows} >= {"support", "finance", "recruiting"}


def test_kit_show_unknown_id(fake):
    result = _run("show", "no-such-kit")
    assert result.exit_code == 1
    assert "Unknown kit" in result.output


def test_kit_show_lists_resources(fake):
    result = _run("show", "support")
    assert result.exit_code == 0, result.output
    assert "agent/support_triage" in result.output
    assert "agent/support_resolver" in result.output


def test_kit_install_dry_run(fake):
    ws = f"kit-cli-{uuid.uuid4().hex[:8]}"
    result = _run("install", "finance", "--workspace", ws, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "create" in result.output


def test_kit_install_then_reinstall_unchanged(fake):
    ws = f"kit-cli-{uuid.uuid4().hex[:8]}"
    result = _run("install", "recruiting", "--workspace", ws)
    assert result.exit_code == 0, result.output
    assert "Installed 'recruiting'" in result.output

    result2 = _run("install", "recruiting", "--workspace", ws, "--json")
    assert result2.exit_code == 0, result2.output
    payload = json.loads(result2.output)
    assert set(c["action"] for c in payload["plan"]["changes"]) == {"unchanged"}
