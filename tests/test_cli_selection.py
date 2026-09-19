"""
How the CLI decides which workspace and project a command acts on.

Two mechanisms meet here. The working directory selects on its own, because a
workspace that links to a directory *is* that directory. Shell integration
exports a selection into the session, the way `conda activate` does, and that
export wins — an explicit activation should beat wherever you happen to stand.

Precedence, narrowest first:  --workspace  >  AGENTS_HUB_WORKSPACE  >  cwd  >  file
"""
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import main as cli


runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_selection(tmp_path, monkeypatch):
    """A throwaway state root, config home, and cwd, with no detection cached."""
    from common import paths

    root = tmp_path / "state"
    workspaces = root / "workspaces"
    workspaces.mkdir(parents=True)
    monkeypatch.setattr(paths, "WORKSPACES_ROOT", workspaces)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in ("AGENTS_HUB_WORKSPACE", "AGENTS_HUB_PROJECT", "AGENTS_HUB_AUTO_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    monkeypatch.setattr(cli, "_detected", None)
    yield workspaces
    cli._detected = None


def attach(workspaces: Path, name: str, target: Path) -> Path:
    """Register ``target`` as an attached workspace, as `workspace init` does."""
    target.mkdir(parents=True, exist_ok=True)
    link = workspaces / name
    link.symlink_to(target, target_is_directory=True)
    return link


def store(selection: dict) -> None:
    path = cli._state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(selection))


class TestTheWorkingDirectorySelects:
    def test_standing_in_an_attached_workspace_selects_it(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        assert cli._active_workspace() == "myapp"

    def test_a_subdirectory_counts_too(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        deep = tmp_path / "code" / "myapp" / "src" / "api"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert cli._active_workspace() == "myapp"

    def test_an_unrelated_directory_selects_nothing(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        elsewhere = tmp_path / "somewhere else"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert cli._active_workspace() is None

    def test_the_most_specific_match_wins(self, isolated_selection, tmp_path, monkeypatch):
        """A project inside a workspace beats the workspace containing it."""
        ws = isolated_selection / "dev"
        ws.mkdir()
        project_dir = tmp_path / "code" / "backend"
        project_dir.mkdir(parents=True)
        (ws / "backend").symlink_to(project_dir, target_is_directory=True)

        monkeypatch.chdir(project_dir)
        assert cli._detect_from_cwd() == ("dev", "backend")

    def test_a_dangling_link_does_not_stop_the_command(self, isolated_selection, tmp_path, monkeypatch):
        (isolated_selection / "gone").symlink_to(tmp_path / "never_existed", target_is_directory=True)
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        assert cli._active_workspace() == "myapp"

    def test_no_state_root_at_all_is_survivable(self, tmp_path, monkeypatch):
        from common import paths
        monkeypatch.setattr(paths, "WORKSPACES_ROOT", tmp_path / "nothing here")
        monkeypatch.setattr(cli, "_detected", None)
        assert cli._detect_from_cwd() == (None, None)


class TestPrecedence:
    def test_the_flag_beats_everything(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        monkeypatch.setenv("AGENTS_HUB_WORKSPACE", "from_env")
        store({"workspace": "from_file"})
        assert cli._active_workspace("explicit") == "explicit"

    def test_an_activated_shell_beats_the_directory(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        monkeypatch.setenv("AGENTS_HUB_WORKSPACE", "from_env")
        assert cli._active_workspace() == "from_env"

    def test_the_directory_beats_the_stored_file(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        store({"workspace": "from_file"})
        assert cli._active_workspace() == "myapp"
        assert "working directory" in cli._workspace_source()

    def test_the_file_still_applies_away_from_any_workspace(self, isolated_selection):
        store({"workspace": "from_file"})
        assert cli._active_workspace() == "from_file"
        assert cli._workspace_source() == str(cli._state_file())

    @pytest.mark.parametrize("value", ["0", "false", "off", "no"])
    def test_detection_can_be_turned_off(self, isolated_selection, tmp_path, monkeypatch, value):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        monkeypatch.setenv("AGENTS_HUB_AUTO_WORKSPACE", value)
        store({"workspace": "from_file"})
        assert cli._active_workspace() == "from_file"


class TestProjectSelection:
    def test_the_directory_finds_the_project_it_holds(self, isolated_selection, tmp_path, monkeypatch):
        ws = isolated_selection / "dev"
        ws.mkdir()
        project_dir = tmp_path / "code" / "backend"
        project_dir.mkdir(parents=True)
        (ws / "backend").symlink_to(project_dir, target_is_directory=True)
        monkeypatch.setattr(cli, "_detected_project_id", lambda w, f: "proj-1" if (w, f) == ("dev", "backend") else None)

        monkeypatch.chdir(project_dir)
        assert cli._active_project() == "proj-1"

    def test_a_project_from_another_workspace_does_not_leak(self, isolated_selection, tmp_path, monkeypatch):
        """The env var names a different workspace, so the directory's project is wrong."""
        ws = isolated_selection / "dev"
        ws.mkdir()
        project_dir = tmp_path / "code" / "backend"
        project_dir.mkdir(parents=True)
        (ws / "backend").symlink_to(project_dir, target_is_directory=True)
        monkeypatch.setattr(cli, "_detected_project_id", lambda w, f: "proj-1")
        monkeypatch.setenv("AGENTS_HUB_WORKSPACE", "somewhere_else")

        monkeypatch.chdir(project_dir)
        assert cli._active_project() is None

    def test_a_stored_project_is_ignored_outside_its_workspace(self, isolated_selection, tmp_path, monkeypatch):
        attach(isolated_selection, "myapp", tmp_path / "code" / "myapp")
        store({"workspace": "dev", "project": "proj-1"})
        monkeypatch.chdir(tmp_path / "code" / "myapp")
        assert cli._active_workspace() == "myapp"
        assert cli._active_project() is None

    def test_a_stored_project_applies_within_its_workspace(self, isolated_selection):
        store({"workspace": "dev", "project": "proj-1"})
        assert cli._active_project() == "proj-1"


class TestShellIntegration:
    """`shell-export` publishes a selection into a shell; `shell-init` prints the
    wrapper that calls it. Both are eval'd, so their output must be plain."""

    def test_export_reads_the_file_not_the_environment(self, isolated_selection, monkeypatch):
        # Honouring the variable it sets would make the first activation permanent.
        store({"workspace": "from_file"})
        monkeypatch.setenv("AGENTS_HUB_WORKSPACE", "from_env")
        out = runner.invoke(cli.app, ["shell-export", "zsh"]).stdout
        assert "export AGENTS_HUB_WORKSPACE='from_file'" in out
        assert "from_env" not in out

    def test_an_empty_selection_unsets(self, isolated_selection):
        out = runner.invoke(cli.app, ["shell-export", "bash"]).stdout
        assert "unset AGENTS_HUB_WORKSPACE" in out
        assert "unset AGENTS_HUB_PROJECT" in out

    def test_fish_speaks_fish(self, isolated_selection):
        store({"workspace": "dev", "project": "proj-1"})
        out = runner.invoke(cli.app, ["shell-export", "fish"]).stdout
        assert "set -gx AGENTS_HUB_WORKSPACE 'dev'" in out
        assert "set -gx AGENTS_HUB_PROJECT 'proj-1'" in out

    def test_a_quote_in_a_name_cannot_break_out(self, isolated_selection):
        store({"workspace": "it's here"})
        out = runner.invoke(cli.app, ["shell-export", "zsh"]).stdout.strip()
        assert out.splitlines()[0] == """export AGENTS_HUB_WORKSPACE='it'\\''s here'"""

    def test_export_output_has_no_markup(self, isolated_selection):
        store({"workspace": "dev"})
        out = runner.invoke(cli.app, ["shell-export", "zsh"]).stdout
        assert "\x1b[" not in out

    @pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
    def test_init_prints_a_function_that_resyncs(self, shell):
        out = runner.invoke(cli.app, ["shell-init", shell]).stdout
        assert "shell-export" in out
        # Every command that writes the selection has to trigger the resync, or
        # the shell keeps a stale export.
        for command in cli._SELECTION_COMMANDS:
            assert command in out

    def test_init_names_the_function(self):
        out = runner.invoke(cli.app, ["shell-init", "zsh", "--name", "hub"]).stdout
        assert "hub() {" in out

    def test_init_refuses_a_shell_it_cannot_write(self):
        result = runner.invoke(cli.app, ["shell-init", "tcsh"])
        assert result.exit_code == 1

    def test_init_falls_back_to_a_known_shell(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/bin/tcsh")
        assert cli._guess_shell() == "zsh"
        monkeypatch.setenv("SHELL", "/opt/homebrew/bin/fish")
        assert cli._guess_shell() == "fish"

    def test_the_generated_paths_are_absolute(self):
        out = runner.invoke(cli.app, ["shell-init", "zsh"]).stdout
        assert str(Path(cli.__file__).resolve()) in out
        assert os.sep in out
