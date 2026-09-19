"""run_shell tests: opt-in allowlist enforcement and safe cwd resolution."""
from tools import shell
from tools.shell import run_shell


def test_allowlist_disabled_by_default_runs_command():
    out = run_shell.invoke({"command": "echo hello", "timeout": 5})
    assert "exit_code: 0" in out
    assert "hello" in out


def test_allowlist_blocks_disallowed_command(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "shell_allowlist_enabled", True)
    monkeypatch.setattr(settings, "allow_shell", ("echo",))

    # Disallowed command is refused *before* execution.
    out = run_shell.invoke({"command": "ls -la /", "timeout": 5})
    assert "not permitted" in out
    assert "exit_code: -1" in out

    # Allowed command still runs.
    ok = run_shell.invoke({"command": "echo hi", "timeout": 5})
    assert "exit_code: 0" in ok


def test_command_allowed_matches_basename():
    assert shell._command_allowed("/usr/bin/python script.py", ("python",)) is True
    assert shell._command_allowed("pytest -q", ("python", "pytest")) is True
    assert shell._command_allowed("rm -rf x", ("python", "pytest")) is False


def test_shell_cwd_is_never_the_repo_root():
    # Whatever the resolution outcome, it must land under the workspaces root,
    # never the application's own working directory.
    from workspace import WORKSPACES_ROOT
    from pathlib import Path
    cwd = Path(shell._resolve_shell_cwd()).resolve()
    root = Path(WORKSPACES_ROOT).resolve()
    assert cwd == root or root in cwd.parents
