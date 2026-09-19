"""
Installing the terminal client: the command on PATH, and the shell hook.

Two separate things, and both are about names. `pip install -e .` must put only
``agents_hub`` in site-packages, because this repository's own top-level names
(``agents``, ``tasks``, ``tools``) belong to other distributions too. The shell
hook must land in the startup file exactly once, however many times it is run.
"""
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import main as cli


runner = CliRunner()
REPO = Path(cli.__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("SHELL", raising=False)
    return tmp_path


class TestTheInstalledPackage:
    def test_the_launcher_package_points_at_the_checkout(self):
        import agents_hub

        assert agents_hub.PROJECT_ROOT == REPO
        assert (agents_hub.PROJECT_ROOT / "cli" / "main.py").exists()
        assert callable(agents_hub.main)

    def test_only_the_launcher_is_declared_for_installation(self):
        """The service's modules stay out of site-packages: `agents` is the
        OpenAI SDK's name, and shadowing it either way is a trap."""
        import tomllib

        config = tomllib.loads((REPO / "pyproject.toml").read_text())
        assert config["tool"]["setuptools"]["packages"] == ["agents_hub"]
        assert "py-modules" not in config["tool"]["setuptools"]

    def test_both_command_names_are_installed(self):
        import tomllib

        config = tomllib.loads((REPO / "pyproject.toml").read_text())
        assert config["project"]["scripts"] == {
            "ah": "agents_hub:main",
            "agents-hub": "agents_hub:main",
        }
        # The shell function is named `ah` too, and finds the script it wraps by
        # this list — short name first.
        assert cli._CONSOLE_SCRIPTS[0] == "ah"

    def test_the_installer_is_runnable(self):
        import subprocess

        script = REPO / "install.sh"
        assert script.exists() and os.access(script, os.X_OK)
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


class TestWhereTheHookGoes:
    def test_each_shell_gets_its_own_startup_file(self, home):
        assert cli._rc_file("zsh") == home / ".zshrc"
        assert cli._rc_file("fish") == home / ".config" / "fish" / "config.fish"

    def test_bash_follows_what_this_machine_has(self, home):
        # A Mac with only .bash_profile: login shells never read .bashrc there.
        (home / ".bash_profile").write_text("# mine\n")
        assert cli._rc_file("bash") == home / ".bash_profile"

        (home / ".bashrc").write_text("# mine\n")
        assert cli._rc_file("bash") == home / ".bashrc"


class TestWritingTheBlock:
    def test_installing_appends_between_markers(self, home):
        rc = home / ".zshrc"
        rc.write_text("export EDITOR=vim\n")
        result = runner.invoke(cli.app, ["shell-init", "zsh", "--install"])

        assert result.exit_code == 0
        body = rc.read_text()
        assert body.startswith("export EDITOR=vim")
        assert body.count(cli._BEGIN_MARK) == 1
        assert "ah() {" in body

    def test_installing_twice_rewrites_rather_than_stacks(self, home):
        rc = home / ".zshrc"
        runner.invoke(cli.app, ["shell-init", "zsh", "--install"])
        runner.invoke(cli.app, ["shell-init", "zsh", "--install", "--name", "hub"])

        body = rc.read_text()
        assert body.count(cli._BEGIN_MARK) == 1
        assert "hub() {" in body
        assert "ah() {" not in body

    def test_what_was_already_there_survives(self, home):
        rc = home / ".zshrc"
        rc.write_text("before\n")
        runner.invoke(cli.app, ["shell-init", "zsh", "--install"])
        rc.write_text(rc.read_text() + "after\n")
        runner.invoke(cli.app, ["shell-init", "zsh", "--install"])

        body = rc.read_text().splitlines()
        assert body[0] == "before"
        assert body[-1] == "after"

    def test_uninstalling_removes_only_the_block(self, home):
        rc = home / ".zshrc"
        rc.write_text("before\n")
        runner.invoke(cli.app, ["shell-init", "zsh", "--install"])
        runner.invoke(cli.app, ["shell-init", "zsh", "--uninstall"])

        assert rc.read_text() == "before\n"

    def test_uninstalling_nothing_is_not_an_error(self, home):
        (home / ".zshrc").write_text("before\n")
        result = runner.invoke(cli.app, ["shell-init", "zsh", "--uninstall"])
        assert result.exit_code == 0
        assert (home / ".zshrc").read_text() == "before\n"

    def test_a_missing_startup_file_is_created(self, home):
        result = runner.invoke(cli.app, ["shell-init", "fish", "--install"])
        assert result.exit_code == 0
        assert (home / ".config" / "fish" / "config.fish").read_text().count("function ah") == 1

    def test_an_explicit_file_wins(self, home):
        elsewhere = home / "custom.rc"
        runner.invoke(cli.app, ["shell-init", "zsh", "--install", "--rc-file", str(elsewhere)])
        assert "ah() {" in elsewhere.read_text()
        assert not (home / ".zshrc").exists()

    def test_printing_alone_touches_nothing(self, home):
        result = runner.invoke(cli.app, ["shell-init", "zsh"])
        assert "ah() {" in result.stdout
        assert not (home / ".zshrc").exists()


class TestWhatTheHookCalls:
    def test_the_installed_command_is_preferred(self, monkeypatch, tmp_path):
        script = tmp_path / "agents-hub"
        script.write_text("#!/bin/sh\n")
        monkeypatch.setattr(cli.sys, "argv", [str(script), "shell-init"])
        assert cli._launcher() == f"'{script}'"

    def test_otherwise_it_falls_back_to_this_file(self, monkeypatch):
        import shutil

        monkeypatch.setattr(cli.sys, "argv", ["main.py", "shell-init"])
        monkeypatch.setattr(shutil, "which", lambda name: None)
        launcher = cli._launcher()
        assert str(REPO / "cli" / "main.py") in launcher
        assert cli.sys.executable in launcher


class FakeProc:
    """A child that has already finished, so `up`'s watch loop runs once."""

    def __init__(self, code=0):
        self.code = code
        self.terminated = False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


@pytest.fixture
def launched(monkeypatch):
    """Record what `up` would start, without starting or building anything."""
    calls = []

    def fake_popen(cmd, cwd=None, env=None):
        calls.append({"cmd": cmd, "cwd": Path(cwd) if cwd else None, "env": env or {}})
        return FakeProc()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_dist_is_stale", lambda folder: False)
    return calls


@pytest.fixture
def with_npm(monkeypatch):
    """A machine that has npm and a dashboard whose dependencies are installed."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(Path, "exists", lambda self: True)


class TestUp:
    """One command for the two servers that used to need two terminals."""

    def test_the_backend_runs_on_the_port_it_was_given(self, launched):
        runner.invoke(cli.app, ["up", "--no-frontend", "--backend-port", "9001"])

        assert len(launched) == 1
        backend = launched[0]
        assert backend["cmd"][1:4] == ["-m", "uvicorn", "main:app"]
        assert "9001" in backend["cmd"]
        assert backend["cwd"] == REPO / "dashboard" / "backend"

    def test_production_is_the_default(self, launched, with_npm):
        result = runner.invoke(cli.app, ["up"])

        backend, dashboard = launched
        assert "--reload" not in backend["cmd"]
        # The built bundle, not the dev server.
        assert dashboard["cmd"][:3] == ["npm", "run", "preview"]
        assert "production" in result.stdout

    def test_dev_swaps_both_halves(self, launched, with_npm):
        result = runner.invoke(cli.app, ["up", "--dev"])

        backend, dashboard = launched
        assert "--reload" in backend["cmd"]
        assert dashboard["cmd"][:3] == ["npm", "run", "dev"]
        assert "development" in result.stdout

    def test_the_dashboard_is_pointed_at_this_backend(self, launched, with_npm):
        runner.invoke(cli.app, ["up", "--backend-port", "9001", "--frontend-port", "9002"])

        dashboard = launched[1]
        assert "9002" in dashboard["cmd"]
        assert dashboard["cwd"] == REPO / "dashboard" / "frontend"
        # Same-origin /api in the browser, forwarded to wherever the API landed.
        assert dashboard["env"]["VITE_API_PROXY"] == "http://localhost:9001"

    def test_a_stale_bundle_is_rebuilt_first(self, launched, with_npm, monkeypatch):
        ran = []
        monkeypatch.setattr(cli, "_dist_is_stale", lambda folder: True)
        monkeypatch.setattr(cli.subprocess, "run", lambda cmd, cwd=None: ran.append(cmd) or FakeProc())

        runner.invoke(cli.app, ["up"])
        assert ran == [["npm", "run", "build"]]

    def test_the_dev_server_needs_no_bundle(self, launched, with_npm, monkeypatch):
        monkeypatch.setattr(cli, "_dist_is_stale", lambda folder: True)
        monkeypatch.setattr(cli.subprocess, "run",
                            lambda *a, **k: pytest.fail("--dev must not build the bundle"))

        result = runner.invoke(cli.app, ["up", "--dev"])
        assert result.exit_code == 0

    def test_without_npm_the_backend_still_runs(self, launched, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: None)
        result = runner.invoke(cli.app, ["up"])

        assert len(launched) == 1
        assert "npm" in result.stdout


class TestBundleStaleness:
    """When to rebuild: cheap to get wrong in the safe direction, expensive in
    the other — a stale dashboard looks like a change that did nothing."""

    def build(self, tmp_path, built_first: bool):
        frontend = tmp_path / "frontend"
        (frontend / "src").mkdir(parents=True)
        (frontend / "dist").mkdir()
        source = frontend / "src" / "App.jsx"
        index = frontend / "dist" / "index.html"

        if built_first:
            source.write_text("x")
            index.write_text("y")
            os.utime(index, (source.stat().st_mtime + 10,) * 2)
        else:
            index.write_text("y")
            source.write_text("x")
            os.utime(source, (index.stat().st_mtime + 10,) * 2)
        return frontend

    def test_no_bundle_at_all_is_stale(self, tmp_path):
        (tmp_path / "frontend").mkdir()
        assert cli._dist_is_stale(tmp_path / "frontend") is True

    def test_a_bundle_newer_than_its_sources_is_current(self, tmp_path):
        assert cli._dist_is_stale(self.build(tmp_path, built_first=True)) is False

    def test_an_edited_source_makes_it_stale(self, tmp_path):
        assert cli._dist_is_stale(self.build(tmp_path, built_first=False)) is True
