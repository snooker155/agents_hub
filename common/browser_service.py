"""The browser service, run and configured from the Settings page.

The browser tools talk to a separate service (deploy/browser/, headless
Chromium behind a token) at ``AGENTS_HUB_BROWSER_URL`` with
``AGENTS_HUB_BROWSER_TOKEN``. Both used to be .env-only settings that needed
a restart, and starting the service was a manual step. This module is the
"make it run" side of the Settings page's Browser section
(dashboard/backend/routes/browser.py, ``/api/browser/service``):

* :func:`status`: what is configured, whether the service answers, whether
  Chromium is installed for the hub's Playwright, whether docker can run the
  container, and what this hub itself has started.
* :func:`start_local` / :func:`stop_local`: the service as a subprocess of
  the hub's own Python (the one with Playwright installed), logging to
  ``.agents_hub/browser_service.log``; its pid is remembered in
  ``browser_service.json`` so a restarted hub finds it again.
* :func:`start_container` / :func:`stop_container`: the same service in
  docker, from the image built with deploy/browser/Dockerfile (built here
  when missing, as a job), published on loopback.
* :func:`install_chromium`: ``python -m playwright install chromium``, as a
  job whose log the page shows.

The two settings themselves are read live (``common.config.live_setting``)
by tools/browser.py, so a change on the page applies to the next call.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT

log = logging.getLogger(__name__)

SERVICE_DIR = PROJECT_ROOT / "deploy" / "browser"
STATE_FILE = AGENTS_HUB_ROOT / "browser_service.json"
LOG_FILE = AGENTS_HUB_ROOT / "browser_service.log"
CONTAINER_NAME = "agents-hub-browser"
IMAGE_TAG = "agents-hub-browser:latest"
DEFAULT_PORT = 3000
START_WAIT_SECONDS = 25
BUILD_TIMEOUT = int(os.environ.get("AGENTS_HUB_BROWSER_BUILD_TIMEOUT", "1800"))

MODES = ("local", "container")


class BrowserServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── settings ─────────────────────────────────────────────────────────────────

def setting(key: str) -> str:
    from common.config import live_setting
    return live_setting(key)


def configured_url() -> str:
    return setting("AGENTS_HUB_BROWSER_URL").rstrip("/")


def configured_token() -> str:
    return setting("AGENTS_HUB_BROWSER_TOKEN")


def configured_mode() -> str:
    mode = setting("AGENTS_HUB_BROWSER_MODE").lower()
    return mode if mode in MODES else "local"


def _port_of(url: str) -> int:
    from urllib.parse import urlsplit
    try:
        return int(urlsplit(url).port or DEFAULT_PORT)
    except (ValueError, TypeError):
        return DEFAULT_PORT


# ── the remembered state ─────────────────────────────────────────────────────

def _read_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(data: Dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        log.debug("could not write %s", STATE_FILE, exc_info=True)


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    pid = int(pid)
    # A child of this process that has exited is a zombie until reaped, and a
    # zombie still answers kill(pid, 0): reap it first, so "alive" means it.
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except ChildProcessError:
        pass  # not our child: only the signal check below applies
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ── probes ───────────────────────────────────────────────────────────────────

def healthz(url: str, timeout: float = 2.0) -> Dict[str, Any]:
    """``{reachable, sessions, max_sessions, error}`` for the service at ``url``."""
    if not url:
        return {"reachable": False, "error": "no url configured"}
    try:
        resp = httpx.get(f"{url.rstrip('/')}/healthz", timeout=timeout)
    except httpx.HTTPError as exc:
        return {"reachable": False, "error": exc.__class__.__name__}
    if resp.status_code != 200:
        return {"reachable": False, "error": f"healthz answered {resp.status_code}"}
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return {"reachable": True, "sessions": body.get("sessions"), "max_sessions": body.get("max_sessions")}


def docker_status() -> Dict[str, Any]:
    """Whether docker can run the container right now, and why not."""
    try:
        from sandbox.registry import get_provider
        ok, reason = get_provider("docker").is_available()
        return {"available": bool(ok), "reason": str(reason or "")}
    except Exception as exc:  # noqa: BLE001 - the page still loads without the probe
        return {"available": False, "reason": str(exc)}


def playwright_status() -> Dict[str, Any]:
    """Whether the hub's Python has Playwright, and Chromium for its version."""
    try:
        import playwright  # noqa: F401  # pyright: ignore[reportMissingImports] - optional
    except ImportError:
        return {"installed": False, "chromium": False, "reason": "the playwright package is not installed"}
    try:
        import playwright as pw  # pyright: ignore[reportMissingImports] - optional
        browsers = json.loads((Path(pw.__file__ or "").parent / "driver" / "package" / "browsers.json").read_text())
        revision = next(b["revision"] for b in browsers["browsers"] if b["name"] == "chromium")
    except Exception:  # noqa: BLE001 - an odd install: report as unknown rather than failing the page
        return {"installed": True, "chromium": None, "reason": "could not read the expected Chromium revision"}
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root:
        registry = Path(root)
    elif platform.system() == "Darwin":
        registry = Path.home() / "Library" / "Caches" / "ms-playwright"
    elif platform.system() == "Windows":
        registry = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    else:
        registry = Path.home() / ".cache" / "ms-playwright"
    present = (registry / f"chromium-{revision}").exists()
    return {"installed": True, "chromium": present, "revision": revision,
            "path": str(registry / f"chromium-{revision}")}


def _container_state() -> Dict[str, Any]:
    try:
        result = subprocess.run(["docker", "inspect", "--format", "{{.State.Status}}", CONTAINER_NAME],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {"exists": False, "running": False}
    if result.returncode != 0:
        return {"exists": False, "running": False}
    state = (result.stdout or "").strip()
    return {"exists": True, "running": state == "running", "state": state}


def _image_exists() -> bool:
    try:
        result = subprocess.run(["docker", "image", "inspect", IMAGE_TAG], capture_output=True, text=True,
                                timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def status() -> Dict[str, Any]:
    url, token, mode = configured_url(), configured_token(), configured_mode()
    state = _read_state()
    local_pid = state.get("pid")
    local = {"running": _pid_alive(local_pid), "pid": local_pid if _pid_alive(local_pid) else None,
             "port": state.get("port"), "log_file": str(LOG_FILE)}
    if not local["running"] and local_pid:
        state.pop("pid", None)
        _write_state(state)
    docker = docker_status()
    container = _container_state() if docker["available"] else {"exists": False, "running": False}
    return {
        "configured": bool(url and token),
        "url": url or None,
        "has_token": bool(token),
        "mode": mode,
        "service": healthz(url) if url else {"reachable": False, "error": "no url configured"},
        "local": local,
        "container": {**container, "name": CONTAINER_NAME, "image": IMAGE_TAG,
                      "image_built": _image_exists() if docker["available"] else False},
        "docker": docker,
        "playwright": playwright_status(),
        "python": sys.executable,
        "jobs": [j for j in _jobs_snapshot() if j["state"] == "running"],
    }


# ── jobs (install, build) ────────────────────────────────────────────────────

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _jobs_snapshot() -> List[Dict[str, Any]]:
    with _jobs_lock:
        return [dict(j) for j in _jobs.values()]


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _run_job(kind: str, cmd: List[str], *, cwd: Optional[Path] = None, timeout: int = BUILD_TIMEOUT,
             env: Optional[Dict[str, str]] = None, on_done=None) -> Dict[str, Any]:
    """Start ``cmd`` in a thread and return the job record; the page polls it."""
    for job in _jobs_snapshot():
        if job["kind"] == kind and job["state"] == "running":
            raise BrowserServiceError(f"a {kind} job is already running", 409)
    job_id = uuid.uuid4().hex[:12]
    record = {"id": job_id, "kind": kind, "state": "running", "log": "", "exit_code": None,
              "started_at": time.time(), "command": " ".join(cmd)}
    with _jobs_lock:
        _jobs[job_id] = record

    def worker() -> None:
        lines: List[str] = []
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            deadline = time.monotonic() + timeout
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                if len(lines) > 400:
                    del lines[: len(lines) - 400]
                with _jobs_lock:
                    _jobs[job_id]["log"] = "\n".join(lines)
                if time.monotonic() > deadline:
                    proc.kill()
                    lines.append(f"[timed out after {timeout}s]")
                    break
            code = proc.wait(timeout=30)
        except Exception as exc:  # noqa: BLE001 - reported on the job
            lines.append(f"[error: {exc}]")
            code = -1
        with _jobs_lock:
            _jobs[job_id].update({"state": "done" if code == 0 else "failed", "exit_code": code,
                                  "log": "\n".join(lines), "finished_at": time.time()})
        if code == 0 and on_done:
            try:
                on_done()
            except Exception:  # noqa: BLE001 - a follow-up that fails is logged, the job itself succeeded
                log.warning("browser service: follow-up after %s failed", kind, exc_info=True)

    threading.Thread(target=worker, name=f"browser-{kind}", daemon=True).start()
    return dict(record)


def install_chromium() -> Dict[str, Any]:
    if not playwright_status()["installed"]:
        raise BrowserServiceError("the playwright package is not installed in the hub's Python "
                                  "(pip install -r deploy/browser/requirements.txt)", 409)
    return _run_job("install_chromium", [sys.executable, "-m", "playwright", "install", "chromium"],
                    timeout=1800)


# ── local mode ───────────────────────────────────────────────────────────────

def start_local(token: str, port: int) -> Dict[str, Any]:
    if not token:
        raise BrowserServiceError("a token is required: set one (or generate it) first", 422)
    pw = playwright_status()
    if not pw["installed"]:
        raise BrowserServiceError("the playwright package is not installed in the hub's Python", 409)
    if pw["chromium"] is False:
        raise BrowserServiceError("Chromium is not installed for Playwright: install it first", 409)
    if not (SERVICE_DIR / "app.py").is_file():
        raise BrowserServiceError(f"the service is missing: {SERVICE_DIR / 'app.py'}", 500)
    state = _read_state()
    if _pid_alive(state.get("pid")):
        return {"already_running": True, "pid": state["pid"], "port": state.get("port")}
    env = dict(os.environ)
    env.update({"BROWSER_TOKEN": token, "BROWSER_PORT": str(int(port)), "BROWSER_HOST": "127.0.0.1",
                "PYTHONUNBUFFERED": "1"})
    # policy.py falls back to common.ssrf outside the image (where it is copied
    # in as hub_ssrf.py), so the repository root has to be importable.
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(PROJECT_ROOT), env.get("PYTHONPATH", "")) if p)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOG_FILE, "ab")  # noqa: SIM115 - handed to the child
    handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start on port {port}\n".encode())
    try:
        proc = subprocess.Popen([sys.executable, "app.py"], cwd=str(SERVICE_DIR), env=env, stdout=handle,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    finally:
        handle.close()
    _write_state({"pid": proc.pid, "port": int(port), "mode": "local", "started_at": time.time()})
    url = f"http://127.0.0.1:{int(port)}"
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _write_state({})
            raise BrowserServiceError("the service exited right after start: " + _log_tail(20), 500)
        if healthz(url, timeout=1.0).get("reachable"):
            return {"pid": proc.pid, "port": int(port), "url": url}
        time.sleep(0.5)
    return {"pid": proc.pid, "port": int(port), "url": url, "warning": "started, but healthz did not answer yet"}


def stop_local() -> bool:
    state = _read_state()
    pid = int(state.get("pid") or 0)
    if not _pid_alive(pid):
        _write_state({})
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    _write_state({})
    return True


def _log_tail(lines: int = 60) -> str:
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def log_tail(lines: int = 200) -> str:
    return _log_tail(lines)


# ── container mode ───────────────────────────────────────────────────────────

def _docker_run(token: str, port: int) -> Dict[str, Any]:
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, text=True, timeout=60)
    cmd = ["docker", "run", "-d", "--name", CONTAINER_NAME, "--restart", "unless-stopped",
           "-e", f"BROWSER_TOKEN={token}", "-p", f"127.0.0.1:{int(port)}:3000",
           "--shm-size", "1g", "--init", "--security-opt", "no-new-privileges:true",
           "--add-host", "host.docker.internal:host-gateway",
           "--label", "agents-hub.managed=true", IMAGE_TAG]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise BrowserServiceError("docker run failed: " + (result.stderr or result.stdout or "").strip()[-800:], 500)
    url = f"http://127.0.0.1:{int(port)}"
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if healthz(url, timeout=1.0).get("reachable"):
            return {"container": CONTAINER_NAME, "port": int(port), "url": url}
        time.sleep(0.5)
    return {"container": CONTAINER_NAME, "port": int(port), "url": url,
            "warning": "started, but healthz did not answer yet"}


def start_container(token: str, port: int) -> Dict[str, Any]:
    """Run the container, building the image first (as a job) when it is
    missing. Returns ``{job}`` while building, else the container's address."""
    if not token:
        raise BrowserServiceError("a token is required: set one (or generate it) first", 422)
    docker = docker_status()
    if not docker["available"]:
        raise BrowserServiceError("docker is not available: " + (docker["reason"] or "the daemon is not running"), 409)
    if not shutil.which("docker"):
        raise BrowserServiceError("the docker CLI is not installed", 409)
    if not _image_exists():
        job = _run_job("build_image",
                       ["docker", "build", "-f", str(SERVICE_DIR / "Dockerfile"), "-t", IMAGE_TAG, str(PROJECT_ROOT)],
                       cwd=PROJECT_ROOT, on_done=lambda: _docker_run(token, port))
        return {"job": job, "building": True}
    return _docker_run(token, port)


def stop_container() -> bool:
    try:
        result = subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, text=True,
                                timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserServiceError(f"could not stop the container: {exc}", 500)
    return result.returncode == 0


def container_logs(tail: int = 200) -> str:
    try:
        result = subprocess.run(["docker", "logs", "--tail", str(int(tail)), CONTAINER_NAME],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"[error fetching logs: {exc}]"
    return (result.stdout or "") + (result.stderr or "")


__all__ = [
    "BrowserServiceError", "CONTAINER_NAME", "DEFAULT_PORT", "IMAGE_TAG", "MODES", "configured_mode",
    "configured_token", "configured_url", "container_logs", "docker_status", "get_job", "healthz",
    "install_chromium", "log_tail", "playwright_status", "start_container", "start_local", "status",
    "stop_container", "stop_local",
]
