"""
Shell execution tool — lets agents run commands inside their workspace and
see stdout/stderr/exit-code so they can verify their own output and iterate.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field


_MAX_OUTPUT = 20_000  # chars — keeps context window manageable

# Variable names ending in one of these are treated as secrets regardless of
# provider, so a new integration's key is scrubbed without needing a code change.
_SECRET_NAME_SUFFIXES = ("_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD")

# Specific names that do not follow the suffix convention above but still
# hold credentials the command's own environment must never see.
_SECRET_NAMES = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "AGENTS_HUB_API_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
})


def scrubbed_env(base: Optional[Mapping[str, str]] = None) -> dict:
    """Build a subprocess environment with provider keys and other secrets removed.

    Starts from ``base`` (the current process environment when omitted) and
    drops any variable whose name ends with ``_API_KEY``, ``_SECRET``,
    ``_TOKEN`` or ``_PASSWORD``, or matches a short explicit list of known
    credential names. Everything else passes through unchanged, so ordinary
    variables a command needs (PATH, HOME, locale, PYTHON*, VIRTUAL_ENV,
    CONDA_*, AGENT_WORKSPACE, non-secret AGENTS_HUB_* settings, and so on)
    still reach the command. This is what an agent-run shell command should
    see: it can run `env` without reading out the backend's provider keys.
    """
    source = base if base is not None else os.environ
    result: dict = {}
    for name, value in source.items():
        upper = name.upper()
        if name in _SECRET_NAMES:
            continue
        if any(upper.endswith(suffix) for suffix in _SECRET_NAME_SUFFIXES):
            continue
        result[name] = value
    return result


def _shell_allowlist() -> Optional[tuple]:
    """Return the allowed command prefixes when the allowlist is enabled, else None.

    Enabling is opt-in and off by default (so existing agent shell usage is
    unchanged): a workspace can turn it on via its ``settings.shell_allowlist_enabled``,
    or it can be forced globally via ``SHELL_ALLOWLIST_ENABLED``. When on, only
    commands whose first word is in ``settings.allow_shell`` may run.
    """
    try:
        from common.config import settings
        enabled = bool(settings.shell_allowlist_enabled)
        if not enabled:
            from common.workspace_context import resolve_active_workspace
            ws = resolve_active_workspace()
            if ws:
                try:
                    from workspace import get_workspace_metadata
                    enabled = bool((get_workspace_metadata(ws).get("settings") or {}).get("shell_allowlist_enabled"))
                except Exception:
                    enabled = False
        if enabled:
            return tuple(settings.allow_shell)
    except Exception:
        pass
    return None


def _command_allowed(command: str, allowlist: tuple) -> bool:
    """True when the command's first token matches an allowed prefix."""
    import shlex
    try:
        first = (shlex.split(command)[0] if command.strip() else "")
    except Exception:
        first = command.strip().split()[0] if command.strip() else ""
    # Compare on the executable's basename so '/usr/bin/python' matches 'python'.
    import os
    first_base = os.path.basename(first)
    return first in allowlist or first_base in allowlist


def _resolve_shell_cwd() -> str:
    """Resolve the directory a shell command should run in.

    Every launcher sets ``AGENT_WORKSPACE`` to the workspace *name*, not a path,
    so the old ``os.environ["AGENT_WORKSPACE"] or os.getcwd()`` either pointed at
    a bare name (invalid cwd → the command errored) or, for in-process chat runs
    with no such env var, fell back to the backend's own working directory —
    the Agents Hub repo itself. This resolves the real operating directory:

    1. Subprocess launchers (task / continuation runs) already start with cwd set
       to the exact workspace/project folder — trust it when it lies under the
       workspaces root (never the application repo).
    2. In-process runs (chat, Telegram) share the backend cwd, so derive the
       directory from the active workspace/project context vars instead.
    3. Fall back to the workspaces root — anywhere but the application's own repo.
    """
    try:
        from workspace import WORKSPACES_ROOT
        root = Path(WORKSPACES_ROOT).resolve()
        cwd = Path.cwd().resolve()
        if cwd == root or root in cwd.parents:
            return str(cwd)
    except Exception:
        pass

    try:
        from common.workspace_context import resolve_active_workspace, resolve_active_project
        ws_name = resolve_active_workspace()
        if ws_name:
            from workspace import resolve_project_root, project_folder_name
            proj_folder = None
            proj_id = resolve_active_project()
            if proj_id:
                try:
                    from projects.storage import ProjectStore
                    from common.paths import PROJECTS_FILE
                    obj = ProjectStore(PROJECTS_FILE).get(str(proj_id))
                    if obj and getattr(obj, "name", None):
                        proj_folder = project_folder_name(obj.name)
                except Exception:
                    proj_folder = None
            return str(resolve_project_root(ws_name, proj_folder))
    except Exception:
        pass

    try:
        from workspace import WORKSPACES_ROOT
        Path(WORKSPACES_ROOT).mkdir(parents=True, exist_ok=True)
        return str(WORKSPACES_ROOT)
    except Exception:
        import os
        return os.getcwd()


class RunShellInput(BaseModel):
    command: str = Field(..., description="Shell command to run inside the workspace")
    timeout: Optional[int] = Field(
        30, description="Timeout in seconds (default 30, max 120)"
    )


@tool("run_shell", args_schema=RunShellInput)
def run_shell(command: str, timeout: Optional[int] = 30) -> str:
    """Run a shell command inside the agent workspace and return its output.

    Use this to verify your work:
    - Run tests: 'pytest tests/ -v'
    - Check syntax: 'python -m py_compile myfile.py'
    - Install deps: 'pip install -r requirements.txt'
    - Run linters: 'flake8 src/'
    - Execute scripts: 'python main.py'

    Returns stdout, stderr, and exit code so you can detect failures and fix them.
    Always check the exit_code: 0 = success, non-zero = failure.
    """
    allowlist = _shell_allowlist()
    if allowlist is not None and not _command_allowed(command, allowlist):
        return (
            f"exit_code: -1\nerror: command not permitted by the shell allowlist. "
            f"Allowed commands: {', '.join(allowlist)}"
        )

    workspace = _resolve_shell_cwd()
    timeout = min(int(timeout or 30), 120)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=scrubbed_env(),
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Truncate if too long
        if len(stdout) > _MAX_OUTPUT:
            stdout = stdout[:_MAX_OUTPUT] + "\n...[truncated]"
        if len(stderr) > _MAX_OUTPUT:
            stderr = stderr[:_MAX_OUTPUT] + "\n...[truncated]"

        parts = [f"exit_code: {proc.returncode}"]
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")

        return "\n".join(parts)

    except subprocess.TimeoutExpired:
        return f"exit_code: -1\nerror: command timed out after {timeout}s"
    except Exception as exc:
        return f"exit_code: -1\nerror: {exc}"
