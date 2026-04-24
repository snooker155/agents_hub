"""
Shell execution tool — lets agents run commands inside their workspace and
see stdout/stderr/exit-code so they can verify their own output and iterate.
"""
from __future__ import annotations

import subprocess
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field


_MAX_OUTPUT = 20_000  # chars — keeps context window manageable


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
    import os

    workspace = os.environ.get("AGENT_WORKSPACE") or os.getcwd()
    timeout = min(int(timeout or 30), 120)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
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
