"""The docker sandbox: today's ``run_code`` isolation, moved here unchanged
for network ``none``, plus a real fence for network ``limited``.

``none`` (the default, and the only mode ``run_code`` used before this
module existed) is exactly what it always was::

    docker run --rm --network none --read-only --cap-drop ALL
               --security-opt no-new-privileges --user 65534:65534
               --memory <limit> --cpus <limit> --pids-limit <n>
               -v <tempdir>:/code:ro -w /code <image> <interpreter> /code/main.<ext>

no network, no writable filesystem beyond a small ``/tmp``, no host
environment, no workspace unless asked for.

``unrestricted`` joins docker's own default ``bridge`` network: ordinary
outbound internet, the same as any other container on this host.

``limited`` is the enforced case: the container joins the internal, no
route out network (``managers.container_manager.EGRESS_NETWORK_NAME``,
created and fronted by an egress gateway on demand,
:func:`managers.container_manager.ensure_egress_gateway`) and gets
``HTTP_PROXY``/``HTTPS_PROXY`` pointing at the gateway with a token scoped to
the request's hosts (``environments/egress.py``). A process that ignores the
proxy variables has nowhere to go: the internal network has no default
route, unlike the ordinary ``agents-hub`` bridge a plain proxy-only fence
would leave open to a raw socket. When the egress proxy is disabled
(``AGENTS_HUB_EGRESS_PROXY`` unset) there is no way to enforce ``limited``
at the docker level, and this provider refuses the request rather than
running it unfenced — unlike an agent run container, which still needs
*some* network for its model calls and so falls back to the soft,
proxy-and-tool-level fence (environments/launch.py); a ``run_code`` snippet
needs nothing by default, so there is nothing to lose by refusing.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sandbox.base import (
    DEFAULT_IMAGES,
    LANGS,
    SandboxNetwork,
    SandboxProvider,
    SandboxRequest,
    SandboxResult,
    truncate_output,
)

log = logging.getLogger(__name__)

_CONTAINER_PREFIX = "agents-hub-code-"
_DOCKER_CHECK_TTL = 60.0
_docker_cache: Dict[str, float] = {}


def _settings():
    from common.config import settings
    return settings


def parse_images(raw: str) -> Dict[str, str]:
    """``CODE_RUNNER_IMAGES`` as JSON or ``lang=image,lang=image``, over the defaults."""
    import json
    images = dict(DEFAULT_IMAGES)
    raw = (raw or "").strip()
    if not raw:
        return images
    parsed: Dict[str, str] = {}
    if raw.startswith("{"):
        try:
            parsed = {str(k): str(v) for k, v in json.loads(raw).items()}
        except Exception:  # noqa: BLE001 - a malformed setting keeps the defaults
            parsed = {}
    else:
        for part in raw.split(","):
            lang, sep, image = part.partition("=")
            if sep and lang.strip() and image.strip():
                parsed[lang.strip()] = image.strip()
    for lang, image in parsed.items():
        if lang in LANGS and image:
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
        except Exception:  # noqa: BLE001 - no daemon reads as unavailable
            ok = False
    _docker_cache.update(checked=now, ok=1.0 if ok else 0.0)
    return ok


def _host_path(path: str) -> str:
    """The path as the docker daemon sees it (differs when the hub itself runs
    in a container; see managers.container_manager._host_path)."""
    try:
        from managers.container_manager import _host_path as translate
        return translate(path)
    except Exception:  # noqa: BLE001 - falls back to a plain resolve
        return str(Path(path).resolve())


def _scratch_root() -> Path:
    """Temporary directories live under the state directory rather than /tmp:
    when the hub runs in a container, only paths under the project root can be
    translated into host paths for the bind mount."""
    try:
        from common.paths import AGENTS_HUB_ROOT
        root = Path(AGENTS_HUB_ROOT) / "code_runs"
    except Exception:  # noqa: BLE001 - a bare environment without the app root
        root = Path(tempfile.gettempdir()) / "agents_hub_code_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_snippet(language: str, code: str, extra_files: Optional[Dict[str, str]] = None) -> Path:
    code_dir = Path(tempfile.mkdtemp(prefix="run-", dir=_scratch_root()))
    # The container runs as nobody: the directory and file must be readable
    # by any user, which mkdtemp's 0700 is not.
    os.chmod(code_dir, 0o755)
    path = code_dir / LANGS[language][0]
    path.write_text(code, encoding="utf-8")
    os.chmod(path, 0o644)
    for rel, content in (extra_files or {}).items():
        rel = str(rel or "").strip().lstrip("/")
        if not rel or ".." in Path(rel).parts:
            continue  # never let a snippet's own file list escape code_dir
        target = code_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o644)
    return code_dir


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


# ── command construction (pure) ──────────────────────────────────────────────

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
    network: str = "none",
    extra_env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """The full ``docker run`` argv for one snippet. Pure: no I/O.

    ``network`` defaults to ``"none"`` (today's only mode): the network name
    to join, or the literal ``"none"``/``"bridge"``. ``extra_env`` adds ``-e``
    flags beyond ``HOME`` (the egress proxy variables, for a ``limited``
    network); empty for ``none``/``unrestricted``.
    """
    file_name, interpreter, _ = LANGS[language]
    cmd = [
        "docker", "run", "--rm", "--quiet",
        "--name", name,
        "--label", "agents_hub.kind=run_code",
        "--network", network,
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--memory", str(memory),
        "--cpus", str(cpus),
        "--pids-limit", str(int(pids_limit)),
        "-e", "HOME=/tmp",
    ]
    for key, value in (extra_env or {}).items():
        if value:
            cmd += ["-e", f"{key}={value}"]
    cmd += ["-v", f"{code_dir}:/code:ro"]
    if workspace_dir:
        cmd += ["-v", f"{workspace_dir}:/work:ro"]
    if interactive:
        cmd.append("-i")
    cmd += ["-w", "/code", image, interpreter, f"/code/{file_name}"]
    return cmd


# ── network policy ───────────────────────────────────────────────────────────

def _resolve_network(net: SandboxNetwork, environment_id: Optional[str]) -> Tuple[str, Dict[str, str], str]:
    """(docker network name, extra ``-e`` vars, error). Error non-empty means
    refuse rather than run: the caller must not fall back to running unfenced."""
    if net.type == "unrestricted":
        return "bridge", {}, ""
    if net.type == "none":
        return "none", {}, ""
    # limited
    try:
        from environments import egress
    except Exception as exc:  # noqa: BLE001 - reported as a refusal
        return "none", {}, f"cannot enforce a limited network: {exc}"
    if not egress.enabled():
        return "none", {}, (
            "a limited network needs the egress proxy (AGENTS_HUB_EGRESS_PROXY=1); it is off, so "
            "run_code refuses this request rather than running the snippet with no network fence")
    from managers import container_manager as cm
    gateway = cm.ensure_egress_gateway()
    if not gateway:
        return "none", {}, ("the egress gateway container could not be started (see the sandbox "
                            "doctor check); run_code refuses a limited network rather than running "
                            "the snippet unfenced")
    token = egress.register(net.hosts, environment_id=environment_id, owner={"kind": "run_code"})
    url = f"http://{token}@{gateway}:{egress.port()}"
    extra_env = {
        "HTTP_PROXY": url, "HTTPS_PROXY": url, "http_proxy": url, "https_proxy": url,
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
    }
    return cm.EGRESS_NETWORK_NAME, extra_env, ""


# ── the provider ─────────────────────────────────────────────────────────────

class DockerProvider(SandboxProvider):
    name = "docker"

    def is_available(self) -> Tuple[bool, str]:
        if docker_available():
            return True, ""
        return False, "no docker daemon answers on this host (docker CLI missing, or its daemon is down)"

    def run(self, request: SandboxRequest) -> SandboxResult:
        language = (request.language or "").strip().lower()
        if language not in LANGS:
            return SandboxResult(exit_code=-1, provider=self.name,
                                 error=f"unsupported language {language!r} (expected python, node or bash)")
        ok, reason = self.is_available()
        if not ok:
            return SandboxResult(exit_code=-1, provider=self.name, error=(
                f"docker is not available on this host ({reason}), so run_code cannot start its "
                "sandbox. An operator can set CODE_RUNNER_FALLBACK=local to run snippets as plain "
                "subprocesses instead (no isolation)."))

        s = _settings()
        network_name, extra_env, net_error = _resolve_network(
            request.network or SandboxNetwork(), request.environment_id)
        if net_error:
            return SandboxResult(exit_code=-1, provider=self.name, error=net_error)

        image = request.image or parse_images(s.code_runner_images)[language]
        workspace_host = _host_path(request.workspace) if request.workspace else None
        code_dir = _write_snippet(language, request.code or "", request.extra_files)
        name = f"{_CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
        cmd = build_docker_command(
            language=language, code_dir=_host_path(str(code_dir)), image=image, name=name,
            memory=request.memory or s.code_runner_memory, cpus=request.cpus or s.code_runner_cpus,
            pids_limit=request.pids_limit or s.code_runner_pids_limit,
            workspace_dir=workspace_host, interactive=request.stdin is not None,
            network=network_name, extra_env=extra_env,
        )
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, input=request.stdin, capture_output=True, text=True,
                                  timeout=request.timeout)
        except subprocess.TimeoutExpired as exc:
            # Killing the docker CLI does not stop the container; remove it by name.
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            shutil.rmtree(code_dir, ignore_errors=True)
            out, out_trunc = truncate_output(_as_text(exc.stdout), _max_output())
            err, err_trunc = truncate_output(_as_text(exc.stderr), _max_output())
            return SandboxResult(
                exit_code=-1, stdout=out, stderr=err, truncated_stdout=out_trunc,
                truncated_stderr=err_trunc, duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True, provider=self.name, error=f"timed out after {request.timeout}s")
        except Exception as exc:  # noqa: BLE001 - reported as the result's error, never raised
            shutil.rmtree(code_dir, ignore_errors=True)
            return SandboxResult(exit_code=-1, provider=self.name, error=str(exc))
        shutil.rmtree(code_dir, ignore_errors=True)

        duration = int((time.monotonic() - started) * 1000)
        # 125: docker itself failed (image pull, bad flag), not the snippet.
        if proc.returncode == 125:
            err, err_trunc = truncate_output(proc.stderr or "", _max_output())
            return SandboxResult(exit_code=-1, stderr=err, truncated_stderr=err_trunc,
                                 duration_ms=duration, provider=self.name,
                                 error="docker could not start the container")
        out, out_trunc = truncate_output(proc.stdout or "", _max_output())
        err, err_trunc = truncate_output(proc.stderr or "", _max_output())
        return SandboxResult(exit_code=proc.returncode, stdout=out, stderr=err,
                             truncated_stdout=out_trunc, truncated_stderr=err_trunc,
                             duration_ms=duration, provider=self.name)


def _max_output() -> int:
    from tools.shell import _MAX_OUTPUT
    return _MAX_OUTPUT


__all__ = ["DockerProvider", "build_docker_command", "parse_images", "docker_available",
           "DEFAULT_IMAGES"]
