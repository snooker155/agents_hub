"""Run a workspace file from the dashboard: the Run of a file in the chat's
Artifacts panel and on the Artifacts page (route
``POST /api/views/code/run-file``, dashboard/backend/routes/views.py).

The file runs as itself, from its own folder: the working directory is the
file's folder, so relative paths and sibling modules resolve the way they do
when an agent runs it there, and Python's ``__file__`` / Node's
``__filename`` name the file. What runs is the editor's text when the person
changed it without saving, else the file as it is. The text goes in on stdin
and a small launcher per language runs it under the file's name, so an
unsaved edit runs without touching the folder.

Where it runs follows the workspace, the same rule ``run_shell`` keeps for a
chat turn (tools/shell.py):

- an isolated workspace (common/isolation.py): its no-network sandbox, the
  folder at ``/work``;
- execution mode docker (``common.config.agent_execution_mode``): a
  throwaway container of its own, the one ``run_shell`` uses outside an agent
  container (managers.container_manager.build_shell_command): the agents'
  base image (Node files: run_code's Node image), the agent network, the run
  limits, the folder read-write at ``/workspace``. No docker daemon means
  refused, never run on the host;
- local mode: the run_code sandbox (tools/run_code.run_snippet), the folder
  mounted read-only at ``$WORK``, exactly what a snippet's Run gets.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

# Extensions run as which language. ``.mjs`` is left out: an ES module cannot
# be compiled under a file name the way the Node launcher does.
RUN_LANG_BY_EXT = {
    ".py": "python",
    ".js": "node", ".cjs": "node",
    ".sh": "bash", ".bash": "bash",
}

CONTAINER_WORKSPACE = "/workspace"
ISOLATED_WORKSPACE = "/work"


class FileRunError(Exception):
    """A request that cannot be run at all; ``status`` is its HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def language_for(path: str) -> Optional[str]:
    """The run_code language a file runs as, by its extension, or None."""
    return RUN_LANG_BY_EXT.get(PurePosixPath(path or "").suffix.lower())


def launcher(language: str, base: str, rel_dir: str, name: str, *, base_from_env: bool = False) -> str:
    """The program that runs the text on stdin as ``<base>/<rel_dir>/<name>``,
    from that folder. ``base_from_env`` reads the base from ``$WORK`` (the
    run_code sandbox sets it to wherever it mounted the workspace)."""
    if language == "python":
        base_expr = "_os.environ['WORK']" if base_from_env else repr(base)
        return (
            "import os as _os, sys as _sys\n"
            f"_d = _os.path.join({base_expr}, {rel_dir!r})\n"
            f"_p = _os.path.join(_d, {name!r})\n"
            "_os.chdir(_d)\n"
            "_sys.path.insert(0, _d)\n"
            "_sys.argv = [_p]\n"
            "_src = _sys.stdin.read()\n"
            "_sys.stdin = open(_os.devnull)\n"
            "exec(compile(_src, _p, 'exec'), "
            "{'__name__': '__main__', '__file__': _p, '__builtins__': __builtins__})\n"
        )
    if language == "node":
        base_expr = "process.env.WORK" if base_from_env else json.dumps(base)
        return (
            "const _fs = require('fs'), _path = require('path'), _Module = require('module');\n"
            f"const _d = _path.join({base_expr}, {json.dumps(rel_dir)});\n"
            f"const _p = _path.join(_d, {json.dumps(name)});\n"
            "process.chdir(_d);\n"
            "const _src = _fs.readFileSync(0, 'utf8');\n"
            "const _m = new _Module(_p, null);\n"
            "_m.filename = _p;\n"
            "_m.paths = _Module._nodeModulePaths(_d);\n"
            "process.argv = [process.argv[0], _p];\n"
            # require.main === module holds for the file, as when node runs it.
            "process.mainModule = _m;\n"
            "_m._compile(_src.replace(/^#![^\\n]*/, ''), _p);\n"
        )
    if language == "bash":
        folder = f"\"$WORK\"/{shlex.quote(rel_dir)}" if base_from_env else shlex.quote(
            str(PurePosixPath(base) / rel_dir) if rel_dir else base)
        return f"cd -- {folder} || exit 1\nexec bash -s\n"
    raise ValueError(f"no launcher for {language!r}")


def _container_command(language: str, base: str, rel_dir: str, name: str) -> str:
    """The ``sh -c`` command of a container run: the launcher, its text on stdin."""
    code = launcher(language, base, rel_dir, name)
    if language == "python":
        return f"exec python -c {shlex.quote(code)}"
    if language == "node":
        return f"exec node -e {shlex.quote(code)}"
    return code


def _truncate(text: str) -> str:
    from tools.shell import _MAX_OUTPUT
    text = text or ""
    return text if len(text) <= _MAX_OUTPUT else text[:_MAX_OUTPUT] + "\n...[truncated]"


def _result(language: str, sandbox: str, *, exit_code: int = -1, stdout: str = "", stderr: str = "",
            duration_ms: int = 0, error: str = "") -> Dict[str, Any]:
    return {
        "ok": exit_code == 0 and not error,
        "exit_code": exit_code,
        "stdout": _truncate(stdout),
        "stderr": _truncate(stderr),
        "duration_ms": duration_ms,
        "language": language,
        "sandbox": sandbox,
        "error": error,
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _node_image() -> str:
    from common.config import settings
    from sandbox.docker import parse_images
    return parse_images(getattr(settings, "code_runner_images", "") or "")["node"]


def _base_image() -> str:
    """The agents' base image, as an agent container of no particular agent
    gets it (managers.container_manager.image_tag_for_agent without the
    per-agent layer)."""
    from common.config import live_setting
    from managers import container_manager as cm
    if cm.image_exists(cm.BASE_IMAGE):
        return cm.BASE_IMAGE
    return live_setting("AGENT_DOCKER_IMAGE", cm.BASE_IMAGE) or cm.BASE_IMAGE


def _run_docker(argv: list, name: str, source: str, timeout: int, language: str, sandbox: str) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, input=source, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Killing the docker CLI leaves the container running.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return _result(language, sandbox, stdout=_text(exc.stdout), stderr=_text(exc.stderr),
                       duration_ms=int((time.monotonic() - started) * 1000),
                       error=f"timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001 - reported as the result's error
        return _result(language, sandbox, error=str(exc))
    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode == 125:
        return _result(language, sandbox, stderr=proc.stderr or "", duration_ms=duration_ms, error=(
            "the file's container did not start: " + (proc.stderr or "").strip()[:2000]
            + " Build the base image on the Containers page if it is missing."))
    return _result(language, sandbox, exit_code=proc.returncode, stdout=proc.stdout or "",
                   stderr=proc.stderr or "", duration_ms=duration_ms)


def _run_in_container(folder: Path, language: str, rel_dir: str, name: str, source: str,
                      timeout: int) -> Dict[str, Any]:
    from managers import container_manager as cm
    from sandbox.docker import docker_available
    if not docker_available():
        return _result(language, "container", error=(
            "the execution mode is docker but no docker daemon answers on this host, "
            "so the file was not run."))
    container = f"agents-hub-file-{uuid.uuid4().hex[:12]}"
    argv = cm.build_shell_command(
        container_name=container, agent_id="", image=_node_image() if language == "node" else _base_image(),
        network=cm.agent_network(), workspace=str(folder),
        command=_container_command(language, CONTAINER_WORKSPACE, rel_dir, name), interactive=True,
    )
    return _run_docker(argv, container, source, timeout, language, "container")


def _run_isolated(folder: Path, language: str, rel_dir: str, name: str, source: str,
                  timeout: int) -> Dict[str, Any]:
    import os
    from common import isolation
    from sandbox.docker import _host_path, docker_available
    if not docker_available():
        return _result(language, "isolated", error=(
            "docker is not available, and an isolated workspace runs code only in its sandbox"))
    container = f"agents-hub-iso-{uuid.uuid4().hex[:12]}"
    user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
    argv = isolation.build_shell_command(
        _container_command(language, ISOLATED_WORKSPACE, rel_dir, name),
        workspace_dir=_host_path(str(folder)),
        image=_node_image() if language == "node" else isolation.sandbox_image(),
        name=container, user=user, interactive=True,
    )
    return _run_docker(argv, container, source, timeout, language, "isolated")


def _run_in_sandbox(folder: Path, language: str, rel_dir: str, name: str, source: str,
                    timeout: int) -> Dict[str, Any]:
    from tools import run_code as run_code_tool
    result = run_code_tool.run_snippet(
        language, launcher(language, "", rel_dir, name, base_from_env=True),
        timeout=timeout, stdin=source, mount_workspace=True, workspace=str(folder),
    )
    return _result(language, result.get("sandbox") or "unavailable", exit_code=result.get("exit_code", -1),
                   stdout=result.get("stdout", ""), stderr=result.get("stderr", ""),
                   duration_ms=result.get("duration_ms", 0), error=result.get("error", ""))


def run_mode(workspace: str) -> str:
    """``isolated``, ``container`` or ``sandbox``: where a file of
    ``workspace`` runs (see the module docstring)."""
    from common import isolation
    from common.config import agent_execution_mode
    if isolation.is_isolated(workspace):
        return "isolated"
    if agent_execution_mode(workspace) == "docker":
        return "container"
    return "sandbox"


def run_file(workspace: str, path: str, body: Optional[str] = None, *, timeout: int = 60) -> Dict[str, Any]:
    """Run the file at ``path`` of ``workspace`` (``body``: the text to run in
    its place, an unsaved edit). Raises :class:`FileRunError` for a request
    that names no runnable file of the workspace; anything that goes wrong
    once it runs comes back in the result's ``error``."""
    from common.config import settings
    from workspace import get_workspace_folder

    language = language_for(path)
    if language is None:
        raise FileRunError(400, f"{path!r} is not runnable here (only .py, .js/.cjs and .sh/.bash files)")
    folder = get_workspace_folder(workspace) if workspace else None
    if folder is None:
        raise FileRunError(404, "workspace folder not found")
    root = Path(folder).resolve()
    target = (root / (path or "").strip().lstrip("/")).resolve()
    try:
        rel = target.relative_to(root)
    except ValueError:
        raise FileRunError(400, "Invalid file path")
    if not target.is_file():
        raise FileRunError(404, "File not found")
    source = body if body is not None else target.read_text(encoding="utf-8", errors="replace")

    limit = max(1, int(getattr(settings, "code_runner_max_timeout", 0) or 300))
    timeout = max(1, min(int(timeout or 60), limit))
    rel_dir = rel.parent.as_posix()
    rel_dir = "" if rel_dir == "." else rel_dir
    runner = {"isolated": _run_isolated, "container": _run_in_container,
              "sandbox": _run_in_sandbox}[run_mode(workspace)]
    return runner(root, language, rel_dir, rel.name, source, timeout)


__all__ = ["FileRunError", "RUN_LANG_BY_EXT", "language_for", "launcher", "run_file", "run_mode"]
