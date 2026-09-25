"""Sandboxed code execution: run a Python, Node or Bash snippet in a throwaway
sandbox instead of a shell in the agent's working directory.

``run_shell`` runs a command on the hub's host, in the workspace, with network
access: fine for building and testing a project the agent owns, wrong for a
snippet whose content came from somewhere else. ``run_code`` writes the
snippet and runs it through ``sandbox/registry.py``, which resolves a
provider (the run's environment first, then ``CODE_RUNNER_PROVIDER``, default
``docker``; see :func:`sandbox.registry.resolve`) and hands it a
``sandbox.base.SandboxRequest``. The default sandbox (docker, network "none")
has no network, no writable filesystem beyond a small ``/tmp``, no host
environment and no workspace; ``mount_workspace=True`` adds the run's
workspace read-only at ``/work``. A run whose *environment* sets network
``limited`` relaxes the network fence to that environment's own hosts instead
of a blanket refusal (unchanged for everything else: no environment, or one
that is ``unrestricted``/``none``, still gets the historical no-network
sandbox). See docs/sandboxes.md for the providers (docker, local, e2b,
modal) and what each one's network policy actually guarantees.

When docker is unavailable, ``CODE_RUNNER_FALLBACK=local`` runs the snippet as
a plain subprocess in a temporary directory with ``scrubbed_env`` and the same
timeout. That has none of the isolation above and is an explicit opt-in;
without it the tool returns an error. Settings: common/config.py
(``code_runner_*``); docs: docs/tools-and-capabilities.md, docs/sandboxes.md.

``run_snippet`` is the same sandbox as a plain function returning a structured
result instead of the tool's formatted-string shape — shared with the code
view's run route (dashboard/backend/routes/views.py, views/code.py), so a
snippet run from the Chat code panel gets exactly the isolation an agent's
``run_code`` call gets.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from sandbox.base import LANGS, SandboxNetwork, SandboxRequest, SandboxResult
from sandbox.docker import DEFAULT_IMAGES, build_docker_command, docker_available, parse_images
from tools.shell import _MAX_OUTPUT


# ── Settings ─────────────────────────────────────────────────────────────────

def _settings():
    from common.config import settings
    return settings


def _network_policy() -> SandboxNetwork:
    """The current process's environment network fence, if any: the same
    ``AGENTS_HUB_NETWORK``/``AGENTS_HUB_ALLOWED_HOSTS`` an agent run's
    environment sets (environments/launch.py), also read by tools/web.py.
    Defaults to ``none`` — unchanged from before ``sandbox/`` existed, a
    snippet gets no network unless its environment says ``limited``."""
    net = os.environ.get("AGENTS_HUB_NETWORK", "").strip().lower()
    if net == "limited":
        hosts = [h.strip() for h in os.environ.get("AGENTS_HUB_ALLOWED_HOSTS", "").split(",") if h.strip()]
        return SandboxNetwork(type="limited", hosts=hosts)
    return SandboxNetwork(type="none")


def _current_environment():
    """The Environment record this process's run belongs to
    (``AGENTS_HUB_ENVIRONMENT_ID``, set by environments/launch.py whenever
    any environment applies), or None: no environment, or it could not be
    loaded — fails open to the settings-level default provider, the same
    rule every other environment lookup in this codebase follows."""
    env_id = os.environ.get("AGENTS_HUB_ENVIRONMENT_ID", "").strip()
    if not env_id:
        return None
    try:
        from environments import service
        return service.get_environment(env_id)
    except Exception:  # noqa: BLE001 - falls back to the settings default
        return None


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
    except Exception:  # noqa: BLE001, S110 - no resolvable workspace
        pass
    return None


def _resolve_mount_workspace(
    mount_workspace: bool, workspace_override: Optional[str]
) -> Tuple[bool, Optional[str], Optional[str]]:
    """(ok, host workspace path or None, error message or None) for a mount request.

    ``workspace_override`` — when given, used as-is (the code view's run route
    passes the view's own workspace explicitly, since there is no in-process
    agent run to resolve ``_workspace_dir()`` from); otherwise falls back to
    the current run's workspace, exactly what the ``run_code`` tool always did.
    """
    if not mount_workspace:
        return True, None, None
    ws = workspace_override if workspace_override is not None else _workspace_dir()
    if ws is None:
        return False, None, "mount_workspace: this run has no workspace directory"
    return True, ws, None


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + "\n...[truncated]"
    return text


def format_result(exit_code: int, duration_ms: int, runtime: str,
                  stdout: str = "", stderr: str = "", error: str = "") -> str:
    """Same shape as run_shell's result, plus timing and which provider ran
    it (``runtime``: ``docker``, ``local``, ``e2b``, ``modal``, or ``none``
    when nothing ran at all)."""
    parts = [f"exit_code: {exit_code}", f"duration_ms: {duration_ms}", f"runtime: {runtime}"]
    if error:
        parts.append(f"error: {error}")
    stdout, stderr = _truncate(stdout or ""), _truncate(stderr or "")
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    return "\n".join(parts)


# ── The shared entry point ───────────────────────────────────────────────────

def _run_request(language: str, code: str, timeout: Optional[int], stdin: Optional[str],
                 mount_workspace: bool, workspace_override: Optional[str]) -> SandboxResult:
    """Build a ``SandboxRequest`` and hand it to the provider
    ``sandbox.registry.resolve`` picks. Never raises: any failure a provider
    cannot itself express becomes a ``SandboxResult`` with ``error`` set."""
    from sandbox import registry

    language = (language or "").strip().lower()
    if language not in LANGS:
        return SandboxResult(exit_code=-1, provider="none",
                             error=f"unsupported language {language!r} (expected python, node or bash)")

    s = _settings()
    limit = max(1, int(s.code_runner_max_timeout or 300))
    timeout = max(1, min(int(timeout or 60), limit))

    environment = _current_environment()
    try:
        provider_name = registry.resolve(environment, s)
        provider = registry.get_provider(provider_name)
    except Exception as exc:  # noqa: BLE001 - an unknown provider name is a configuration error, reported not raised
        return SandboxResult(exit_code=-1, provider="none", error=f"cannot resolve a sandbox provider: {exc}")

    # Nothing ran at all: reported as provider "none" (run_snippet's
    # "sandbox": "unavailable"), the same as an unsupported language, so a
    # caller like playground/environments/lab.py can tell "the sandbox never
    # started, nothing to charge for" apart from "it started and something
    # else went wrong" (a refused mount, a timeout, a bad exit — all of which
    # keep the resolved provider's name).
    ok, reason = provider.is_available()
    if not ok:
        hint = (" An operator can set CODE_RUNNER_FALLBACK=local to run snippets as plain "
               "subprocesses instead (no isolation)." if provider_name == "docker" else "")
        return SandboxResult(exit_code=-1, provider="none", error=(
            f"the {provider_name} sandbox provider is not available ({reason}), so run_code cannot "
            f"start its sandbox.{hint}"))

    network = _network_policy()
    # A snippet gets either the workspace or the network, never both: with a
    # limited network it can reach the environment's hosts, so it must not
    # also read the workspace's files, or it would be the whole trifecta in
    # one call. The capability guard classifies it the same way
    # (tools.capabilities.run_code_grants).
    if mount_workspace and network.type != "none":
        return SandboxResult(exit_code=-1, provider=provider_name, error=(
            "mount_workspace is not available here: this run's environment gives snippets "
            "network access (limited), and a snippet gets either the workspace or the network, "
            "not both."))

    ok, ws, err = _resolve_mount_workspace(mount_workspace, workspace_override)
    if not ok:
        return SandboxResult(exit_code=-1, provider=provider_name, error=err or "")

    request = SandboxRequest(
        language=language, code=code or "", timeout=timeout, stdin=stdin,
        memory=s.code_runner_memory, cpus=s.code_runner_cpus, pids_limit=s.code_runner_pids_limit,
        network=network, workspace=ws, environment_id=getattr(environment, "id", None),
    )
    try:
        return provider.run(request)
    except Exception as exc:  # noqa: BLE001 - a provider must not raise, but a bug in one must not crash the tool either
        return SandboxResult(exit_code=-1, provider=provider_name, error=str(exc))


def run_snippet(
    language: str,
    code: str,
    *,
    timeout: Optional[int] = 60,
    stdin: Optional[str] = None,
    mount_workspace: bool = False,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a snippet in the same sandbox the ``run_code`` tool uses; return a
    structured result instead of the tool's formatted-string shape.

    Shared by the ``run_code`` tool's underlying machinery and the code view's
    run route (dashboard/backend/routes/views.py, views/code.py), so a
    snippet run from the Chat code panel gets exactly the isolation an agent's
    ``run_code`` call gets. Returns ``{"ok", "exit_code", "duration_ms",
    "stdout", "stderr", "runtime", "language", "sandbox": "docker"|"local"|
    "e2b"|"modal"|"unavailable", "error"}``; never raises — any failure (bad
    language, no provider available, a timeout, a crashed interpreter) comes
    back as ``ok: False`` with ``error`` set.

    ``workspace`` overrides where ``mount_workspace`` mounts from: the route
    passes the view's own workspace directory explicitly, since there is no
    in-process agent run to resolve it from the way the tool does.
    """
    result = _run_request(language, code, timeout, stdin, mount_workspace, workspace)
    provider = (result.provider or "").strip()
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "runtime": provider or "none",
        "language": (language or "").strip().lower(),
        "sandbox": "unavailable" if provider in ("", "none") else provider,
        "error": result.error,
    }


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

    The sandbox has no network by default (an environment with a limited
    network relaxes that to its own allowed hosts) and cannot change anything
    outside itself. Use it for calculations, data transformation, parsing and
    quick experiments. Set mount_workspace to read the workspace's files
    (read-only, at /work). Returns exit_code, duration, which sandbox
    provider ran it, stdout and stderr: 0 means success.
    """
    result = _run_request(language, code, timeout, stdin, mount_workspace, None)
    return format_result(result.exit_code, result.duration_ms, result.provider or "none",
                         stdout=result.stdout, stderr=result.stderr, error=result.error)


RUN_CODE_TOOLS = [run_code]

__all__ = ["run_code", "RUN_CODE_TOOLS", "build_docker_command", "parse_images",
           "docker_available", "format_result", "DEFAULT_IMAGES", "run_snippet"]
