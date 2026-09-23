"""
Sandboxed code execution: run a Python, Node or Bash snippet in a throwaway
container instead of a shell in the agent's working directory.

``run_shell`` runs a command on the hub's host, in the workspace, with network
access: fine for building and testing a project the agent owns, wrong for a
snippet whose content came from somewhere else. ``run_code`` writes the snippet
to a temporary directory and runs it with

    docker run --rm --network none --read-only --cap-drop ALL
               --security-opt no-new-privileges --user 65534:65534
               --memory <limit> --cpus <limit> --pids-limit <n>
               -v <tempdir>:/code:ro -w /code <image> <interpreter> /code/main.<ext>

so the snippet has no network, no writable filesystem beyond a small /tmp, no
host environment and no workspace. ``mount_workspace=True`` adds the run's
workspace read-only at /work, for analysing files without being able to change
them.

When docker is unavailable, ``CODE_RUNNER_FALLBACK=local`` runs the snippet as
a plain subprocess in a temporary directory with ``scrubbed_env`` and the same
timeout. That has none of the isolation above and is an explicit opt-in;
without it the tool returns an error. Settings: common/config.py
(``code_runner_*``); docs: docs/tools-and-capabilities.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.shell import _MAX_OUTPUT, scrubbed_env

DEFAULT_IMAGES: Dict[str, str] = {
    "python": "python:3.12-slim",
    "node": "node:20-slim",
    "bash": "bash:5",
}

# language -> (file name, interpreter in the container, local interpreter candidates)
_LANGS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "python": ("main.py", "python", ("python3", "python")),
    "node": ("main.js", "node", ("node",)),
    "bash": ("main.sh", "bash", ("bash",)),
}

_CONTAINER_PREFIX = "agents-hub-code-"
_DOCKER_CHECK_TTL = 60.0
_docker_cache: Dict[str, float] = {}


# ── Settings ─────────────────────────────────────────────────────────────────

def _settings():
    from common.config import settings
    return settings


def parse_images(raw: str) -> Dict[str, str]:
    """``CODE_RUNNER_IMAGES`` as JSON or ``lang=image,lang=image``, over the defaults."""
    images = dict(DEFAULT_IMAGES)
    raw = (raw or "").strip()
    if not raw:
        return images
    parsed: Dict[str, str] = {}
    if raw.startswith("{"):
        try:
            parsed = {str(k): str(v) for k, v in json.loads(raw).items()}
        except Exception:
            parsed = {}
    else:
        for part in raw.split(","):
            lang, sep, image = part.partition("=")
            if sep and lang.strip() and image.strip():
                parsed[lang.strip()] = image.strip()
    for lang, image in parsed.items():
        if lang in _LANGS and image:
            images[lang] = image
    return images


def docker_available() -> bool:
    """True when a docker CLI is on PATH and its daemon answers. Cached briefly."""
    now = time.monotonic()
    if now - _docker_cache.get("checked", 0.0) < _DOCKER_CHECK_TTL:
        return bool(_docker_cache.get("ok"))
    ok = False
    if shutil.which("docker"):
        try:
            proc = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                                  capture_output=True, text=True, timeout=5)
            ok = proc.returncode == 0 and bool(proc.stdout.strip())
        except Exception:
            ok = False
    _docker_cache.update(checked=now, ok=1.0 if ok else 0.0)
    return ok


def _host_path(path: str) -> str:
    """The path as the docker daemon sees it (differs when the hub itself runs
    in a container; see managers.container_manager._host_path)."""
    try:
        from managers.container_manager import _host_path as translate
        return translate(path)
    except Exception:
        return str(Path(path).resolve())


def _scratch_root() -> Path:
    """Temporary directories live under the state directory rather than /tmp:
    when the hub runs in a container, only paths under the project root can be
    translated into host paths for the bind mount."""
    try:
        from common.paths import AGENTS_HUB_ROOT
        root = Path(AGENTS_HUB_ROOT) / "code_runs"
    except Exception:
        root = Path(tempfile.gettempdir()) / "agents_hub_code_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _workspace_dir() -> Optional[str]:
    """The run's workspace directory, only when it really is one (never the
    application's own repository)."""
    try:
        from tools.shell import _resolve_shell_cwd
        from workspace import WORKSPACES_ROOT
        path = Path(_resolve_shell_cwd()).resolve()
        root = Path(WORKSPACES_ROOT).resolve()
        if path == root or root in path.parents:
            return str(path)
    except Exception:
        pass
    return None


# ── Command construction ─────────────────────────────────────────────────────

def build_docker_command(
    *,
    language: str,
    code_dir: str,
    image: str,
    name: str,
    memory: str,
    cpus: str,
    pids_limit: int,
    workspace_dir: Optional[str] = None,
    interactive: bool = False,
) -> List[str]:
    """The full ``docker run`` argv for one snippet. Pure: no I/O."""
    file_name, interpreter, _ = _LANGS[language]
    cmd = [
        "docker", "run", "--rm", "--quiet",
        "--name", name,
        "--label", "agents_hub.kind=run_code",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--memory", str(memory),
        "--cpus", str(cpus),
        "--pids-limit", str(int(pids_limit)),
        "-e", "HOME=/tmp",
        "-v", f"{code_dir}:/code:ro",
    ]
    if workspace_dir:
        cmd += ["-v", f"{workspace_dir}:/work:ro"]
    if interactive:
        cmd.append("-i")
    cmd += ["-w", "/code", image, interpreter, f"/code/{file_name}"]
    return cmd


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + "\n...[truncated]"
    return text


def format_result(exit_code: int, duration_ms: int, runtime: str,
                  stdout: str = "", stderr: str = "", error: str = "") -> str:
    """Same shape as run_shell's result, plus timing and where it ran."""
    parts = [f"exit_code: {exit_code}", f"duration_ms: {duration_ms}", f"runtime: {runtime}"]
    if error:
        parts.append(f"error: {error}")
    stdout, stderr = _truncate(stdout or ""), _truncate(stderr or "")
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    return "\n".join(parts)


def _write_snippet(language: str, code: str) -> Path:
    code_dir = Path(tempfile.mkdtemp(prefix="run-", dir=_scratch_root()))
    # The container runs as nobody: the directory and file must be readable
    # by any user, which mkdtemp's 0700 is not.
    os.chmod(code_dir, 0o755)
    path = code_dir / _LANGS[language][0]
    path.write_text(code, encoding="utf-8")
    os.chmod(path, 0o644)
    return code_dir


# ── Runners ──────────────────────────────────────────────────────────────────

def _run_docker(language: str, code_dir: Path, timeout: int, stdin: Optional[str],
                mount_workspace: bool) -> str:
    s = _settings()
    image = parse_images(s.code_runner_images)[language]
    workspace = None
    if mount_workspace:
        ws = _workspace_dir()
        if ws is None:
            return format_result(-1, 0, f"docker ({image})",
                                 error="mount_workspace: this run has no workspace directory")
        workspace = _host_path(ws)
    name = f"{_CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
    cmd = build_docker_command(
        language=language, code_dir=_host_path(str(code_dir)), image=image, name=name,
        memory=s.code_runner_memory, cpus=s.code_runner_cpus,
        pids_limit=s.code_runner_pids_limit, workspace_dir=workspace,
        interactive=stdin is not None,
    )
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Killing the docker CLI does not stop the container; remove it by name.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return format_result(
            -1, int((time.monotonic() - started) * 1000), f"docker ({image})",
            stdout=_as_text(exc.stdout), stderr=_as_text(exc.stderr),
            error=f"timed out after {timeout}s",
        )
    duration = int((time.monotonic() - started) * 1000)
    stderr = proc.stderr or ""
    # 125: docker itself failed (image pull, bad flag), not the snippet.
    if proc.returncode == 125:
        return format_result(-1, duration, f"docker ({image})", stderr=stderr,
                             error="docker could not start the container")
    return format_result(proc.returncode, duration, f"docker ({image})",
                         stdout=proc.stdout or "", stderr=stderr)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _run_local(language: str, code_dir: Path, timeout: int, stdin: Optional[str],
               mount_workspace: bool) -> str:
    file_name, _, candidates = _LANGS[language]
    interpreter = next((shutil.which(c) for c in candidates if shutil.which(c)), None)
    if interpreter is None:
        return format_result(-1, 0, "local", error=f"no {language} interpreter on this host")
    env = scrubbed_env()
    env["HOME"] = str(code_dir)
    note = ""
    if mount_workspace:
        ws = _workspace_dir()
        if ws is None:
            return format_result(-1, 0, "local",
                                 error="mount_workspace: this run has no workspace directory")
        env["WORK"] = ws
        note = " (workspace at $WORK, not write-protected in local mode)"
    started = time.monotonic()
    try:
        proc = subprocess.run([interpreter, str(code_dir / file_name)], cwd=str(code_dir),
                              input=stdin, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        return format_result(-1, int((time.monotonic() - started) * 1000), "local" + note,
                             stdout=_as_text(exc.stdout), stderr=_as_text(exc.stderr),
                             error=f"timed out after {timeout}s")
    return format_result(proc.returncode, int((time.monotonic() - started) * 1000), "local" + note,
                         stdout=proc.stdout or "", stderr=proc.stderr or "")


# ── Tool ─────────────────────────────────────────────────────────────────────

class RunCodeInput(BaseModel):
    language: Literal["python", "node", "bash"] = Field(description="Language of the snippet.")
    code: str = Field(description="The program to run, as the full source of one file.")
    timeout: Optional[int] = Field(default=60, description="Timeout in seconds (default 60).")
    stdin: Optional[str] = Field(default=None, description="Text passed to the program on stdin.")
    mount_workspace: bool = Field(
        default=False,
        description="Mount the run's workspace read-only at /work so the code can read its files.")


@tool("run_code", args_schema=RunCodeInput)
def run_code(language: str, code: str, timeout: Optional[int] = 60,
             stdin: Optional[str] = None, mount_workspace: bool = False) -> str:
    """Run a Python, Node or Bash snippet in a throwaway sandbox and return its output.

    The sandbox has no network and cannot change anything outside itself. Use it
    for calculations, data transformation, parsing and quick experiments. Set
    mount_workspace to read the workspace's files (read-only, at /work). Returns
    exit_code, duration, stdout and stderr: 0 means success.
    """
    language = (language or "").strip().lower()
    if language not in _LANGS:
        return format_result(-1, 0, "none", error=f"unsupported language {language!r} "
                             "(expected python, node or bash)")
    s = _settings()
    limit = max(1, int(s.code_runner_max_timeout or 300))
    timeout = max(1, min(int(timeout or 60), limit))

    use_docker = docker_available()
    if not use_docker and str(s.code_runner_fallback or "").strip().lower() != "local":
        return format_result(-1, 0, "none", error=(
            "docker is not available on this host, so run_code cannot start its sandbox. "
            "An operator can set CODE_RUNNER_FALLBACK=local to run snippets as plain "
            "subprocesses instead (no isolation)."))

    code_dir = _write_snippet(language, code or "")
    try:
        if use_docker:
            return _run_docker(language, code_dir, timeout, stdin, mount_workspace)
        return _run_local(language, code_dir, timeout, stdin, mount_workspace)
    except Exception as exc:
        return format_result(-1, 0, "docker" if use_docker else "local", error=str(exc))
    finally:
        shutil.rmtree(code_dir, ignore_errors=True)


RUN_CODE_TOOLS = [run_code]

__all__ = ["run_code", "RUN_CODE_TOOLS", "build_docker_command", "parse_images",
           "docker_available", "format_result", "DEFAULT_IMAGES"]
