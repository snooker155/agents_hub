"""
One headless Blender process, and the client that talks to it.

A daemon is a long-lived ``blender --background`` running ``host/main.py``,
reachable over a Unix socket. It holds the scene between commands, which is the
whole reason it stays up: rebuilding a twenty-command object from scratch for
each command would make the twenty-first cost twenty-one.

State that matters lives in the caller's command log, not here. This process is
a **cache of the last revision**, and everything about its lifecycle follows
from that: it may be evicted when idle, killed when it hangs, and replaced by a
replay of the log. Nothing is lost when one dies except time.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from connectors.blender import store

HOST_SCRIPT = Path(__file__).resolve().parent / "host" / "main.py"

READY_PREFIX = "BLENDER_GEOM_READY"


class DaemonError(RuntimeError):
    """The engine could not be reached, started, or kept alive."""


class CommandError(RuntimeError):
    """The engine rejected a command. ``kind`` is 'protocol' (the caller's
    arguments are wrong and a corrected retry can work) or 'engine' (the
    operation itself failed)."""

    def __init__(self, message: str, kind: str = "engine"):
        super().__init__(message)
        self.kind = kind


class BlenderDaemon:
    """A single engine process, addressed by ``key``.

    One key is one scene. Commands are serialized by a lock: ``bpy`` is neither
    reentrant nor thread-safe, and the host answers one frame at a time anyway.
    """

    def __init__(self, key: str, *, binary: str = "", idle_timeout: Optional[int] = None,
                 startup_timeout: Optional[int] = None, command_timeout: Optional[int] = None):
        cfg = store.load()
        self.key = key
        self.binary = binary or store.binary_path()
        self.idle_timeout = int(idle_timeout if idle_timeout is not None else cfg["idle_timeout_s"])
        self.startup_timeout = float(startup_timeout if startup_timeout is not None
                                     else cfg["startup_timeout_s"])
        self.command_timeout = float(command_timeout if command_timeout is not None
                                     else cfg["command_timeout_s"])

        self.socket_path = _socket_path()
        self.log_path = str(store.runtime_dir() / f"{_safe(key)}.log")

        self.proc: Optional[subprocess.Popen] = None
        #: Set when this object drives an engine another process started. The
        #: socket is the same; what differs is that we must not reap a process
        #: we do not own, and liveness comes from the pid rather than Popen.
        self.pid: Optional[int] = None
        self.owner_pid: Optional[int] = None
        self.version = ""
        self.started_at = 0.0
        self.last_used = 0.0
        self.commands = 0
        self.revision = 0
        self._sock: Optional[socket.socket] = None
        self._file = None
        self._lock = threading.RLock()
        self._pump: Optional[threading.Thread] = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.is_alive():
            return
        if not self.binary or not Path(self.binary).exists():
            raise DaemonError(
                "no Blender binary configured. Set the path on the Blender connector "
                f"page (tried: {self.binary or 'nothing'})")
        if not HOST_SCRIPT.exists():  # pragma: no cover - packaging accident
            raise DaemonError(f"host script missing at {HOST_SCRIPT}")

        argv = [self.binary, "--background", "--factory-startup",
                "--python", str(HOST_SCRIPT), "--",
                "--socket", self.socket_path, "--idle-timeout", str(self.idle_timeout)]
        log = open(self.log_path, "ab", buffering=0)
        log.write(b"\n=== %s %s ===\n" % (time.strftime("%Y-%m-%dT%H:%M:%S").encode(),
                                          self.key.encode()))
        try:
            self.proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=log, text=False,
                start_new_session=True)
        except OSError as exc:
            log.close()
            raise DaemonError(f"could not start Blender: {exc}")

        try:
            self.version = self._await_ready()
        except Exception:
            self.stop()
            log.close()
            raise
        # Blender keeps writing to stdout (render progress, operator chatter).
        # Nobody reads it after startup, so drain it into the log or the pipe
        # fills and the process wedges mid-render.
        self._pump = threading.Thread(target=self._drain_stdout, args=(log,), daemon=True)
        self._pump.start()

        self._connect()
        self.started_at = time.time()
        self.last_used = self.started_at
        self.pid = self.proc.pid
        self.owner_pid = os.getpid()

    def _await_ready(self) -> str:
        """Wait for the host's ready line rather than polling the socket file.

        A socket that does not exist yet and a Blender that failed to start look
        identical from the outside; the ready line tells them apart, and a dead
        process is reported as dead instead of as a timeout.
        """
        deadline = time.time() + self.startup_timeout
        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise DaemonError(
                        f"Blender exited during startup (code {self.proc.returncode}); "
                        f"see {self.log_path}")
                for _ in sel.select(timeout=0.5):
                    line = self.proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", "replace").strip()
                    if text.startswith(READY_PREFIX):
                        return text[len(READY_PREFIX):].strip()
            raise DaemonError(
                f"Blender did not become ready within {self.startup_timeout:.0f}s; "
                f"see {self.log_path}")
        finally:
            sel.close()

    def _drain_stdout(self, log) -> None:
        try:
            for line in iter(self.proc.stdout.readline, b""):
                log.write(line)
        except Exception:
            pass
        finally:
            try:
                log.close()
            except Exception:
                pass

    def _connect(self) -> None:
        deadline = time.time() + 10.0
        last: Optional[Exception] = None
        while time.time() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self.socket_path)
                self._sock = sock
                self._file = sock.makefile("rwb")
                return
            except OSError as exc:
                last = exc
                time.sleep(0.1)
        raise DaemonError(f"could not connect to the engine socket: {last}")

    @classmethod
    def attach(cls, record: Dict[str, Any]) -> "BlenderDaemon":
        """Drive an engine started by another process, from its registry record."""
        daemon = cls(str(record.get("key") or "attached"))
        daemon.socket_path = str(record.get("socket") or "")
        daemon.log_path = str(record.get("log") or daemon.log_path)
        daemon.pid = int(record.get("pid") or 0) or None
        daemon.owner_pid = int(record.get("owner_pid") or 0) or None
        daemon.version = str(record.get("version") or "")
        daemon.started_at = float(record.get("started_at") or time.time())
        daemon.last_used = time.time()
        daemon._connect()
        return daemon

    @property
    def owned(self) -> bool:
        """True when this process started the engine and may reap it."""
        return self.proc is not None

    def is_alive(self) -> bool:
        if self._sock is None:
            return False
        if self.proc is not None:
            return self.proc.poll() is None
        return _pid_alive(self.pid)

    def stop(self, *, graceful: bool = True) -> None:
        with self._lock:
            if self._file is not None and graceful and self.is_alive():
                try:
                    self._send({"id": "bye", "cmd": "shutdown"})
                    self._sock.settimeout(5.0)
                    self._file.readline()
                except Exception:
                    pass
            for closer in (self._file, self._sock):
                try:
                    closer and closer.close()
                except Exception:
                    pass
            self._file = self._sock = None
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            elif self.proc is None and _pid_alive(self.pid):
                # Someone else's engine: ask it to go, but never escalate to
                # SIGKILL on a process this code did not start.
                try:
                    os.kill(self.pid, signal.SIGTERM)
                except OSError:
                    pass
            if self.owned:
                try:
                    os.unlink(self.socket_path)
                except OSError:
                    pass

    # ── commands ─────────────────────────────────────────────────────────────

    def call(self, cmd: str, args: Optional[Dict[str, Any]] = None,
             *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Run one command and return its ``result``.

        Raises :class:`CommandError` for a rejected command and
        :class:`DaemonError` when the engine itself is gone. A timeout kills the
        process: a wedged geometry operator does not recover, and the caller can
        rebuild the scene from its log.
        """
        with self._lock:
            if not self.is_alive():
                self.start()
            frame = {"id": uuid.uuid4().hex[:12], "cmd": cmd, "args": args or {}}
            self._sock.settimeout(timeout or self.command_timeout)
            try:
                self._send(frame)
                line = self._file.readline()
            except socket.timeout:
                self.stop(graceful=False)
                raise DaemonError(
                    f"'{cmd}' exceeded {timeout or self.command_timeout:.0f}s and the engine "
                    "was killed; the scene can be rebuilt by replaying its command log")
            except OSError as exc:
                self.stop(graceful=False)
                raise DaemonError(f"lost the engine while running '{cmd}': {exc}")
            if not line:
                self.stop(graceful=False)
                raise DaemonError(f"the engine closed the connection during '{cmd}'; "
                                  f"see {self.log_path}")

            try:
                resp = json.loads(line.decode("utf-8"))
            except Exception as exc:
                raise DaemonError(f"unreadable response from the engine: {exc}")

            self.last_used = time.time()
            if not resp.get("ok"):
                raise CommandError(str(resp.get("error") or "command failed"),
                                   str(resp.get("error_kind") or "engine"))
            self.commands += 1
            result = resp.get("result") or {}
            if isinstance(result, dict) and isinstance(result.get("revision"), int):
                self.revision = result["revision"]
            if isinstance(result, dict):
                result.setdefault("_ms", resp.get("ms"))
            return result

    def _send(self, frame: Dict[str, Any]) -> None:
        self._file.write((json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8"))
        self._file.flush()

    # ── reporting ────────────────────────────────────────────────────────────

    def record(self) -> Dict[str, Any]:
        """The registry entry another process needs to find and drive this engine."""
        return {"key": self.key, "pid": self.pid, "socket": self.socket_path,
                "log": self.log_path, "version": self.version,
                "started_at": self.started_at, "owner_pid": self.owner_pid}

    def info(self) -> Dict[str, Any]:
        """What the connector page shows for this daemon."""
        alive = self.is_alive()
        return {
            "key": self.key,
            "alive": alive,
            "owned": self.owned,
            "pid": self.pid,
            "version": self.version,
            "revision": self.revision,
            "commands": self.commands,
            "uptime_s": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "idle_s": round(time.time() - self.last_used, 1) if self.last_used else None,
            "socket": self.socket_path,
            "log": self.log_path,
            "rss_bytes": _rss(self.pid) if alive and self.pid else None,
        }


#: Unix socket paths are capped by the kernel (104 bytes on macOS, 108 on
#: Linux) — short enough that a socket under the state root breaks as soon as
#: the project lives a few directories deep. The socket is a transient handle,
#: not state, so it goes somewhere reliably short and the log stays with the
#: rest of the connector's files.
SOCKET_DIR = Path("/tmp/agents-hub-blender") if os.name == "posix" else None
MAX_SOCKET_PATH = 100


def _socket_path() -> str:
    base = SOCKET_DIR or store.runtime_dir()
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    path = str(base / f"{uuid.uuid4().hex[:10]}.sock")
    if len(path) > MAX_SOCKET_PATH:  # pragma: no cover - only on exotic layouts
        raise DaemonError(f"socket path is too long for this platform: {path}")
    return path


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # someone else's process, but it exists
        return True
    return True


def _safe(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(key))[:64] or "default"


def _rss(pid: int) -> Optional[int]:
    """Resident memory, so an operator can see a daemon that is growing.

    ``ps`` rather than psutil: it is one call, it is everywhere, and a missing
    number is not worth a dependency.
    """
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        value = (out.stdout or "").strip()
        return int(value) * 1024 if value.isdigit() else None
    except Exception:
        return None


__all__ = ["BlenderDaemon", "DaemonError", "CommandError", "_pid_alive"]
