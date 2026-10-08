"""
The hub's own model runtime, started and kept by the hub.

Without ``AGENTS_HUB_MODELS_URL`` (a runtime run elsewhere, such as the
compose service) the hub runs deploy/models/app.py itself on this host, so
local models need nothing installed by hand: no Ollama, no separate service
to start. Everything it needs lives under ``AGENTS_HUB_ROOT/models-runtime``:

* ``venv/``: the runtime's own Python environment, made from the backend's
  interpreter with deploy/models/requirements.txt. Speech engines installed
  from the Models page go here too, never into the backend's environment.
* ``token``: the shared token, made on first use (mode 0600).
* ``runtime.log``, ``runtime.pid``.
* ``stopped``: present after a person pressed Stop; the hub then leaves the
  runtime off until someone starts it again, across restarts of the hub.

Models are kept in ``AGENTS_HUB_ROOT/models`` (``AGENTS_HUB_MODELS_DIR``).

The runtime is its own process group, not a child that dies with the
backend: a reload of the backend (``--reload`` while editing, a release)
leaves loaded models loaded. The hub finds it again by its port and checks
it is the runtime it started (its code version and the token). A runtime
whose code changed under it is restarted when nothing is loaded, and marked
``stale`` otherwise, for the Restart button.

``AGENTS_HUB_MODELS_MANAGED=false`` turns all of this off.
"""
from __future__ import annotations

import hashlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT

log = logging.getLogger(__name__)

RUNTIME_SRC = PROJECT_ROOT / "deploy" / "models"
HOME = AGENTS_HUB_ROOT / "models-runtime"
VENV = HOME / "venv"
TOKEN_FILE = HOME / "token"
PID_FILE = HOME / "runtime.pid"
LOG_FILE = HOME / "runtime.log"
STOPPED_FILE = HOME / "stopped"
REQS_MARK = HOME / "requirements.sha"

#: Seconds the runtime may take to answer its health check after a start.
START_TIMEOUT = 60.0
#: Seconds a stop waits before it kills.
STOP_TIMEOUT = 15.0
#: A log past this size is moved aside at the next start.
LOG_ROTATE_BYTES = 5 * 1024 * 1024

#: What the runtime process gets from the hub's environment: what a Python
#: program and a download need, never the hub's keys.
_PASSED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE",
               "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
               "https_proxy", "http_proxy", "no_proxy", "HF_TOKEN", "HF_ENDPOINT", "LLAMA_SERVER_BIN")


def _settings() -> Any:
    from common.config import settings
    return settings


def active() -> bool:
    """Whether the hub runs the runtime itself: no external URL, not turned
    off, and the runtime's code is here to run."""
    s = _settings()
    return (bool(getattr(s, "models_managed", True))
            and not str(getattr(s, "models_url", "") or "").strip()
            and (RUNTIME_SRC / "app.py").is_file())


def port() -> int:
    return int(getattr(_settings(), "models_port", 8200) or 8200)


def url() -> str:
    return f"http://127.0.0.1:{port()}"


def models_dir() -> Path:
    raw = os.environ.get("AGENTS_HUB_MODELS_DIR")
    return Path(raw).expanduser() if raw else AGENTS_HUB_ROOT / "models"


def token() -> str:
    """The shared token, made on first use."""
    import secrets
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    HOME.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    tmp = TOKEN_FILE.with_name("token.tmp")
    tmp.write_text(value, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(TOKEN_FILE)
    return value


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def code_version() -> str:
    """The digest the runtime reports as ``version`` (deploy/models/app.py
    ``code_version``): the same five files, hashed the same way."""
    h = hashlib.sha1()
    for f in (RUNTIME_SRC / "app.py", RUNTIME_SRC / "speech_worker.py", RUNTIME_SRC / "openvoice_vc.py",
              PROJECT_ROOT / "providers" / "model_structure.py", RUNTIME_SRC / "voice_enhance.py"):
        try:
            h.update(f.resolve().read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


def log_tail(chars: int = 2000) -> str:
    try:
        with open(LOG_FILE, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - chars))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


# ── state ────────────────────────────────────────────────────────────────────

class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        #: stopped, preparing, starting, running, failed
        self.state = "stopped"
        self.message = ""
        self.stale = False


_st = _State()


def _set(state: str, message: str = "") -> None:
    _st.state, _st.message = state, message
    if state in ("failed",):
        log.warning("model runtime: %s", message)
    else:
        log.info("model runtime: %s %s", state, message)


def probe(timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    """The runtime's ``/healthz`` on our port, or None when nothing (or
    something else) answers there."""
    import httpx
    try:
        resp = httpx.get(f"{url()}/healthz", timeout=timeout)
        body = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return body if isinstance(body, dict) and "max_loaded" in body else None


def _token_accepted(timeout: float = 3.0) -> bool:
    import httpx
    try:
        resp = httpx.get(f"{url()}/jobs", headers={"Authorization": f"Bearer {token()}"}, timeout=timeout)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def user_stopped() -> bool:
    return STOPPED_FILE.exists()


def status() -> Dict[str, Any]:
    """What the Models page shows about the runtime the hub runs."""
    return {"managed": True, "state": _st.state, "message": _st.message, "stale": _st.stale,
            "stopped_by_user": user_stopped(), "url": url(), "home": str(HOME),
            "models_dir": str(models_dir()),
            "log_tail": log_tail() if _st.state in ("failed", "preparing", "starting") else ""}


# ── bringing it up ───────────────────────────────────────────────────────────

def _run_logged(cmd: list, *, timeout: float) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "ab") as fh:
        fh.write(f"\n$ {' '.join(str(c) for c in cmd)}\n".encode())
        fh.flush()
        result = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} {' '.join(str(c) for c in cmd[1:3])} "
                           f"exited with code {result.returncode}")


def prepare_venv() -> None:
    """Make the runtime's Python environment, or bring its packages up to
    deploy/models/requirements.txt when that file changed."""
    reqs = RUNTIME_SRC / "requirements.txt"
    want = hashlib.sha1(reqs.read_bytes()).hexdigest()
    have = REQS_MARK.read_text().strip() if REQS_MARK.is_file() else ""
    if venv_python().is_file() and have == want:
        return
    if not venv_python().is_file():
        _set("preparing", "creating the runtime's Python environment")
        _run_logged([sys.executable, "-m", "venv", str(VENV)], timeout=300)
    _set("preparing", "installing the runtime's packages")
    _run_logged([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                 "-r", str(reqs)], timeout=900)
    REQS_MARK.write_text(want)


def _spawn() -> int:
    env = {k: os.environ[k] for k in _PASSED_ENV if os.environ.get(k)}
    env.update({
        "MODELS_TOKEN": token(), "MODELS_DIR": str(models_dir()), "MODELS_PORT": str(port()),
        "MODELS_HOST": "127.0.0.1", "PYTHONUNBUFFERED": "1",
    })
    for k, v in os.environ.items():  # the runtime's own knobs pass through
        if k.startswith("MODELS_") and k not in env:
            env[k] = v
    models_dir().mkdir(parents=True, exist_ok=True)
    try:  # one previous log is kept
        if LOG_FILE.stat().st_size > LOG_ROTATE_BYTES:
            LOG_FILE.replace(LOG_FILE.with_name("runtime.log.1"))
    except OSError:
        pass
    with open(LOG_FILE, "ab") as fh:
        fh.write(f"\n--- starting the model runtime on port {port()} ---\n".encode())
        fh.flush()
        proc = subprocess.Popen([str(venv_python()), str(RUNTIME_SRC / "app.py")], cwd=str(RUNTIME_SRC),
                                env=env, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the runtime exited with code {proc.returncode}")
        if probe() is not None:
            return proc.pid
        time.sleep(0.5)
    raise RuntimeError(f"the runtime did not answer within {int(START_TIMEOUT)} s")


def _bring_up() -> None:
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with _file_lock():
            if probe() is None:
                prepare_venv()
                _set("starting", f"starting on port {port()}")
                _spawn()
        _after_up()
    except Exception as exc:  # noqa: BLE001 - the state carries why, the page shows it
        _set("failed", str(exc))


def _after_up() -> None:
    health = probe()
    if health is None:
        _set("failed", "the runtime stopped answering")
        return
    if not _token_accepted():
        _set("failed", f"another model runtime with another token runs on port {port()}; stop it, "
                       "or point AGENTS_HUB_MODELS_URL and AGENTS_HUB_MODELS_TOKEN at it")
        return
    _st.stale = health.get("version") not in (None, code_version())
    _set("running", "")
    try:
        from providers.local_models import ensure_hub_local_backend
        ensure_hub_local_backend()
    except Exception:  # noqa: BLE001 - the runtime runs; the provider is registered on the next read
        log.debug("could not register hub-local", exc_info=True)


class _file_lock:
    """One process at a time brings the runtime up (the API replica, a
    reloaded backend still shutting down)."""

    def __enter__(self) -> "_file_lock":
        self.fh = open(HOME / "start.lock", "w")
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except ImportError:  # Windows: no lock, one backend there anyway
            pass
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        except ImportError:
            pass
        self.fh.close()


def ensure(*, wait: bool = False, explicit: bool = False) -> Dict[str, Any]:
    """Make sure the runtime runs: a no-op when it answers, a start in a
    background thread when it does not. A runtime a person stopped stays
    stopped unless ``explicit`` (the Start button). ``wait`` blocks until the
    attempt ends (tests, the CLI)."""
    if not active():
        return {"managed": False}
    if explicit:
        STOPPED_FILE.unlink(missing_ok=True)
    with _st.lock:
        if _st.thread is not None and _st.thread.is_alive():
            thread = _st.thread
        else:
            thread = None
            health = probe()
            if health is not None:
                if _st.state != "running" or explicit:
                    _after_up()
                else:
                    _st.stale = health.get("version") not in (None, code_version())
                # The runtime's code changed: restart it while it holds nothing.
                if _st.state == "running" and _st.stale and not health.get("loaded"):
                    thread = _start_thread(restart=True)
            elif user_stopped():
                _set("stopped", "stopped from the Models page")
            else:
                thread = _start_thread()
    if wait and thread is not None:
        thread.join(START_TIMEOUT + 960)
    return status()


def _start_thread(*, restart: bool = False) -> threading.Thread:
    def run() -> None:
        if restart:
            _stop_process()
        _bring_up()

    _set("starting", "starting")
    t = threading.Thread(target=run, name="model-runtime-start", daemon=True)
    _st.thread = t
    t.start()
    return t


#: The longest the runtime may take to save its models' prompt cache slots
#: before a stop (a few GB of KV each).
SAVE_TIMEOUT = 60.0


def _save_cache() -> None:
    """Ask the runtime to save its models' prompt cache slots to disk. The
    signal below reaches its llama-servers at the same moment as the runtime,
    so its own save at shutdown would find them gone. Best effort: a runtime
    older than the cache answers 404, a hung one is stopped anyway."""
    import httpx
    try:
        httpx.post(f"{url()}/cache/save", headers={"Authorization": f"Bearer {token()}"},
                   timeout=SAVE_TIMEOUT)
    except httpx.HTTPError as exc:
        log.info("saving the runtime's prompt cache before the stop failed: %s", exc)


def _stop_process() -> bool:
    """Stop the runtime this hub started (its process group: the loaded
    llama-servers and speech workers go with it). Only a process that answers
    as the runtime on our port with the pid we recorded is signalled."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        pid = 0
    health = probe()
    if health is not None and health.get("pid") and int(health["pid"]) != pid:
        pid = int(health["pid"]) if _token_accepted() else 0
    if not pid:
        return False
    _save_cache()
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:  # not a group leader: a runtime someone started by hand
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
            return False
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline and probe(timeout=0.5) is not None:
        time.sleep(0.3)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    PID_FILE.unlink(missing_ok=True)
    return True


def stop() -> Dict[str, Any]:
    """Stop it and keep it stopped until someone starts it again."""
    if not active():
        return {"managed": False}
    HOME.mkdir(parents=True, exist_ok=True)
    STOPPED_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
    with _st.lock:
        _stop_process()
        _st.stale = False
        _set("stopped", "stopped from the Models page")
    return status()


def restart(*, wait: bool = False) -> Dict[str, Any]:
    if not active():
        return {"managed": False}
    STOPPED_FILE.unlink(missing_ok=True)
    with _st.lock:
        if _st.thread is not None and _st.thread.is_alive():
            return status()
        thread = _start_thread(restart=True)
    if wait:
        thread.join(START_TIMEOUT + 960)
    return status()


# ── keeping it up ────────────────────────────────────────────────────────────

#: Seconds between the watchdog's checks.
WATCH_SECONDS = 30.0
_watchdog: Optional[threading.Thread] = None


def start_watchdog() -> None:
    """Bring the runtime up now and check on it every ``WATCH_SECONDS``: a
    runtime that died is started again, unless a person stopped it."""
    global _watchdog
    if not active() or (_watchdog is not None and _watchdog.is_alive()):
        return

    def loop() -> None:
        while True:
            try:
                ensure()
            except Exception:  # noqa: BLE001 - the next check tries again
                log.debug("model runtime check failed", exc_info=True)
            time.sleep(WATCH_SECONDS)

    _watchdog = threading.Thread(target=loop, name="model-runtime-watchdog", daemon=True)
    _watchdog.start()


__all__ = ["active", "ensure", "restart", "start_watchdog", "status", "stop", "token", "url"]
