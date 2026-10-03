"""``ah apply`` as a command: exit codes, the lock next to the files, the
confirmation before deletes, ``--export``. The hub is the planner tests'
fake, put where ``cli.main.hub()`` finds its backend."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from test_declarative_planner import ENV, FakeHub


@pytest.fixture
def fake(monkeypatch):
    from cli import main as cli_main
    hub = FakeHub()
    monkeypatch.setattr(cli_main, "_backend", SimpleNamespace(request=hub))
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    return hub


def _run(*args):
    from cli import main as cli_main
    return CliRunner().invoke(cli_main.app, ["apply", *args, "--workspace", "default"])


def test_dry_run_apply_and_reapply(tmp_path, fake):
    (tmp_path / "hub.yaml").write_text(ENV.format(description="Sandbox."))
    result = _run(str(tmp_path), "--dry-run")
    assert result.exit_code == 0, result.output
    assert "create" in result.output and not fake.envs
    assert not (tmp_path / "ah.lock").exists()

    result = _run(str(tmp_path))
    assert result.exit_code == 0, result.output
    lock = json.loads((tmp_path / "ah.lock").read_text())
    assert list(lock["resources"]) == ["environment/box"]

    result = _run(str(tmp_path), "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["plan"]["counts"] == {"unchanged": 1}


def test_invalid_files_exit_non_zero_with_file_and_line(tmp_path, fake):
    (tmp_path / "hub.yaml").write_text("kind: environment\nid: box\nmood: calm\n")
    result = _run(str(tmp_path))
    assert result.exit_code == 1
    assert "hub.yaml:3: unknown environment field 'mood'" in result.output
    assert not fake.writes


def test_drift_exits_non_zero_until_forced(tmp_path, fake):
    (tmp_path / "hub.yaml").write_text(ENV.format(description="Sandbox."))
    assert _run(str(tmp_path)).exit_code == 0
    env_id = next(iter(fake.envs))
    fake._update(env_id, {"description": "By hand."})
    result = _run(str(tmp_path))
    assert result.exit_code == 1 and "drift" in result.output and "--force" in result.output
    assert fake.envs[env_id]["description"] == "By hand."
    assert _run(str(tmp_path), "--force").exit_code == 0
    assert fake.envs[env_id]["description"] == "Sandbox."


def test_prune_asks_before_deleting(tmp_path, fake):
    (tmp_path / "hub.yaml").write_text(ENV.format(description="Sandbox."))
    assert _run(str(tmp_path)).exit_code == 0
    (tmp_path / "hub.yaml").write_text("")
    result = _run(str(tmp_path), "--prune")
    assert result.exit_code == 1 and "--yes" in result.output
    assert fake.envs
    assert _run(str(tmp_path), "--prune", "--yes").exit_code == 0
    assert not fake.envs
    assert json.loads((tmp_path / "ah.lock").read_text())["resources"] == {}


def test_export_writes_files_and_a_lock_that_owns_them(tmp_path, fake):
    env_id = fake("POST", "/api/environments", json={"name": "Shared Box", "description": "Theirs."})["id"]
    out = tmp_path / "repo"
    result = _run("--export", f"environment:{env_id}", "--out", str(out))
    assert result.exit_code == 0, result.output
    text = (out / "environments" / "shared-box.yaml").read_text()
    assert "name: Shared Box" in text and "description: Theirs." in text
    result = _run(str(out), "--json", "--dry-run")
    assert json.loads(result.output)["plan"]["counts"] == {"unchanged": 1}
