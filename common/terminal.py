"""
A shell in a run's container or a service replica's container, for the
dashboard's terminal panel (docs/terminal.md).

Why the hub keeps the session, not the socket
---------------------------------------------
A WebSocket drops for dull reasons: a laptop lid, a proxy timeout, a tab
moved to another window that the browser throttles. A shell that died with
its socket would lose whatever was running in it, so the ``docker exec`` lives
in a :class:`TerminalSession` here, owned by this process, and a socket only
*attaches* to it. While nothing is attached the session waits a grace period
(``TERMINAL_GRACE_SECONDS``, 60 s by default), keeps reading the shell's
output into a ring buffer (``TERMINAL_BUFFER_BYTES``, 256 KB), and a
reconnect with the session id replays that buffer and carries on streaming.
A session nobody reattaches to in time is closed, and so is one with no input
and no output for ``TERMINAL_IDLE_SECONDS`` (15 minutes), attached or not.

The shell
---------
``docker exec -it <container> sh`` (managers/container_manager.py,
``exec_shell_argv``) run with a pseudo-terminal of our own as its stdin and
stdout: the Docker CLI then forwards raw bytes both ways and resizes the
container's pty when it gets SIGWINCH, which :meth:`PtyProcess.resize` sends
after setting the new size. The CLI rather than the SDK because every other
container call in the hub goes through it, so a terminal works wherever the
hub can start the container (a remote ``DOCKER_HOST`` included). Only a POSIX
host has ptys; elsewhere opening a terminal is refused with a message.

Who may open one
----------------
A shell reaches everything the run can: its files, its environment, its
network. So, in ``multi`` mode, an admin, or the run's owner (the chat's
owner, the person who filed the task; for a replica, whoever deployed the
service) who is also at least an editor of its workspace; a run whose owner
is not known is left to admins. Never the service credential: that is what
an agent's own process presents, and an agent does not get a shell in its own
sandbox through the dashboard. Outside ``multi`` the one operator may. A
user may hold ``TERMINAL_MAX_PER_USER`` (3) sessions at once, attached or
waiting out their grace period.

Every open, resume and close lands in the audit log (``terminal.open``,
``terminal.resume``, ``terminal.close``) with the container, the session and,
on close, why it ended and how many bytes went each way. Keystrokes are not
recorded: the log says who had a shell where and when, not what they typed.
"""
from __future__ import annotations

import atexit
import errno
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from common.auth import MULTI, WS_EDITOR, Principal, role_satisfies

log = logging.getLogger(__name__)

KINDS = ("run", "replica")

DEFAULT_GRACE_SECONDS = 60
DEFAULT_IDLE_SECONDS = 900
DEFAULT_BUFFER_BYTES = 256 * 1024
DEFAULT_MAX_PER_USER = 3

#: How often the reaper looks for sessions past their grace or idle limit.
REAP_INTERVAL_SECONDS = 5.0

#: One read from the shell at most; a burst larger than this arrives in
#: several frames.
READ_CHUNK = 64 * 1024

#: Statuses of a run that still has its container.
_ACTIVE_RUN_STATUSES = ("running", "stop", "pending", "queued")

#: Sentinels a session hands its attached sink besides output bytes.
EXITED = "exited"
TAKEN_OVER = "taken_over"


class TerminalRefused(Exception):
    """Why a terminal cannot be opened, for the person who asked: the route
    turns ``status`` into the HTTP status and ``detail`` into its message."""

    def __init__(self, detail: str, status: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _int_setting(key: str, default: int, minimum: int = 1) -> int:
    from common.config import live_setting
    try:
        return max(minimum, int(live_setting(key, str(default))))
    except (TypeError, ValueError):
        return default


def grace_seconds() -> int:
    return _int_setting("TERMINAL_GRACE_SECONDS", DEFAULT_GRACE_SECONDS, 0)


def idle_seconds() -> int:
    return _int_setting("TERMINAL_IDLE_SECONDS", DEFAULT_IDLE_SECONDS, 10)


def buffer_bytes() -> int:
    return _int_setting("TERMINAL_BUFFER_BYTES", DEFAULT_BUFFER_BYTES, 1024)


def max_per_user() -> int:
    return _int_setting("TERMINAL_MAX_PER_USER", DEFAULT_MAX_PER_USER, 1)


# ── Targets ──────────────────────────────────────────────────────────────────

@dataclass
class Target:
    """The container a terminal opens in, and what decides who may open it."""
    kind: str
    id: str
    container: str
    workspace: Optional[str] = None
    owner: Optional[str] = None
    label: str = ""


def _this_host() -> str:
    return socket.gethostname()


def _check_container(container: str, host: str, what: str) -> None:
    """Refuse a container on another host, or one the daemon no longer runs."""
    if host and host != _this_host():
        raise TerminalRefused(
            f"The {what}'s container runs on host {host}. A terminal opens only from "
            "the hub process on that host, which talks to its Docker daemon.")
    from managers.container_manager import container_status
    status = container_status(container)
    if status is None:
        raise TerminalRefused(f"The {what}'s container {container} no longer exists.", 410)
    if status != "running":
        raise TerminalRefused(f"The {what}'s container {container} is {status}, not running.")


def _chat_or_task_owner(run: Dict[str, Any]) -> Optional[str]:
    """The person a run belongs to: its conversation's owner for a chat turn,
    the person who filed its task otherwise. The same rule steering uses
    (dashboard/backend/routes/steering.py)."""
    try:
        if str(run.get("session_type") or "") == "chat":
            from common import chat_store
            conv_id = str(run.get("conversation_id") or run.get("task_id") or "")
            chat = chat_store.get_chat(conv_id) if conv_id else None
            return str((chat or {}).get("owner") or "") or None
        task_id = run.get("task_id")
        if not task_id:
            return None
        from uuid import UUID
        from tasks import service as tasks_service
        task = tasks_service.get_task(UUID(str(task_id)))
        if task is None:
            return None
        return str(getattr(task, "created_by_user", "") or "") or None
    except Exception:  # noqa: BLE001 - an unknown owner leaves the run to admins
        log.debug("terminal: owner lookup failed for %s", run.get("run_id"), exc_info=True)
        return None


def _run_target(run_id: str) -> Target:
    from managers.run_manager import get_run_by_id
    run = get_run_by_id(run_id)
    entity = False
    if run is None:
        from common import entity_runs
        run = entity_runs.get(run_id)
        entity = run is not None
    if run is None:
        raise TerminalRefused(f"Run {run_id} not found.", 404)
    container = str(run.get("container_name") or "")
    status = str(run.get("status") or "")
    if not container and status in ("pending", "queued"):
        raise TerminalRefused("This run has not started yet, so it has no container to open.")
    if not container:
        raise TerminalRefused(
            "This run executes in the hub's own process (local mode), not in a container, "
            "so there is no container to open a shell in.")
    if status not in _ACTIVE_RUN_STATUSES:
        raise TerminalRefused(
            f"This run has {status or 'ended'}; its container was removed when it finished.", 410)
    _check_container(container, str(run.get("host") or ""), "run")
    owner = None if entity else _chat_or_task_owner(run)
    return Target(kind="run", id=run_id, container=container,
                  workspace=run.get("workspace") or None, owner=owner,
                  label=str(run.get("title") or run.get("agent_id") or run_id))


def _replica_target(instance_id: str) -> Target:
    from instances import store as istore
    from services import replicas
    from services import store as sstore
    replica = replicas.get_replica(instance_id)
    if replica is None:
        raise TerminalRefused(f"Replica {instance_id} not found.", 404)
    container = str(replica.get("container_name") or "")
    if replica.get("carrier_mode") != "docker" or not container:
        raise TerminalRefused(
            "This replica runs as a process on the hub's host, not in a container, "
            "so there is no container to open a shell in.")
    if replica.get("state") not in istore.LIVE_STATES:
        raise TerminalRefused(
            f"This replica has {replica.get('state') or 'stopped'}; its container is gone.", 410)
    _check_container(container, str(replica.get("carrier_host") or ""), "replica")
    service = sstore.get(replica.get("service_id")) or {}
    return Target(kind="replica", id=instance_id, container=container,
                  workspace=replica.get("workspace") or service.get("workspace") or None,
                  owner=str(service.get("created_by") or "") or None,
                  label=str(replica.get("label") or instance_id))


def resolve_target(kind: str, target_id: str) -> Target:
    """The container behind ``kind``/``target_id``, or :class:`TerminalRefused`
    saying why there is none to open a shell in."""
    if kind not in KINDS:
        raise TerminalRefused(f"Unknown terminal target kind {kind!r}.", 404)
    if os.name != "posix":
        raise TerminalRefused("A terminal needs a POSIX host for its pseudo-terminal.", 501)
    return _run_target(target_id) if kind == "run" else _replica_target(target_id)


# ── Access ───────────────────────────────────────────────────────────────────

def authorize(principal: Optional[Principal], target: Target) -> None:
    """Raise :class:`TerminalRefused` (401/403) unless ``principal`` may open a
    shell in ``target`` (see the module docstring, "Who may open one")."""
    from common import access, identity
    if principal is None:
        raise TerminalRefused("Authentication required.", 401)
    if principal.kind == "service":
        raise TerminalRefused(
            "A terminal is opened by a person, not by an agent's or a run's own credential.", 403)
    if not principal.reaches(target.workspace):
        raise TerminalRefused("This API key does not reach the target's workspace.", 403)
    if identity.current_mode() != MULTI or principal.is_admin or principal.kind in ("local", "token"):
        return
    if not access.can_see_workspace(principal, target.workspace):
        raise TerminalRefused("This target's workspace is not visible to this account.", 403)
    if target.workspace:
        role = identity.membership_role(str(target.workspace), principal.id)
        if not role_satisfies(role, WS_EDITOR):
            raise TerminalRefused(
                "A terminal needs an editor's role in the target's workspace.", 403)
    what = "run" if target.kind == "run" else "service"
    if not target.owner:
        raise TerminalRefused(
            f"This {what} has no known owner, so only an admin may open a terminal in it.", 403)
    if not access.owner_or_admin(principal, target.owner):
        raise TerminalRefused(
            f"Only the {what}'s owner or an admin may open a terminal in it.", 403)


def principal_from_ticket(found: Dict[str, Any]) -> Optional[Principal]:
    """The principal a verified terminal ticket stands for, re-read so a
    demoted account does not keep the role it had when it minted it. The
    same resolution ``common.identity`` gives an auth ticket, plus the local
    operator (single mode mints terminal tickets too)."""
    from common import identity
    from common.auth import LOCAL_PRINCIPAL, TOKEN, TOKEN_PRINCIPAL
    kind = found.get("kind")
    mode = identity.current_mode()
    if kind == "local":
        return LOCAL_PRINCIPAL if mode == identity.SINGLE else None
    if kind == "token":
        return TOKEN_PRINCIPAL if mode == TOKEN else None
    if kind != "user" or mode != MULTI:
        return None
    user = identity.get_user(str(found.get("user") or ""))
    if user is None:
        return None
    scope = found.get("scope")
    return Principal(id=user["id"], username=user["username"], role=user["role"],
                     kind="user", via="ticket",
                     scope=tuple(scope) if scope is not None else None)


# ── The shell process ────────────────────────────────────────────────────────

class PtyProcess:
    """A command on a pseudo-terminal: what :class:`TerminalSession` reads and
    writes. Tests swap in a fake with the same four methods plus ``close``."""

    def __init__(self, argv: List[str], cols: int = 80, rows: int = 24):
        import pty
        master, slave = pty.openpty()
        self._fd = master
        self._set_size(slave, cols, rows)
        try:
            self._proc = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                start_new_session=True)
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._closed = False

    @staticmethod
    def _set_size(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(cols), 0, 0))

    def read(self, timeout: float) -> Optional[bytes]:
        """Output that arrived within ``timeout`` seconds: ``b""`` when none
        did, ``None`` once the command has exited and its output is drained."""
        import select
        if self._closed:
            return None
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not ready:
            return None if self._proc.poll() is not None else b""
        try:
            data = os.read(self._fd, READ_CHUNK)
        except OSError as exc:
            # Linux answers EIO once the last writer of the slave side is gone.
            if exc.errno in (errno.EIO, errno.EBADF):
                return None
            raise
        return data or None

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view and not self._closed:
            written = os.write(self._fd, view)
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        self._set_size(self._fd, cols, rows)
        # The CLI is not the foreground group of a controlling terminal here
        # (it has none), so the kernel will not signal it: say so ourselves.
        try:
            import signal
            os.kill(self._proc.pid, signal.SIGWINCH)
        except (OSError, AttributeError):
            pass

    def exit_code(self) -> Optional[int]:
        return self._proc.poll()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    log.warning("terminal: exec process %s did not exit", self._proc.pid)
        try:
            os.close(self._fd)
        except OSError:
            pass


def _spawn(target: Target, cols: int, rows: int):
    """Start the shell for ``target``. Tests replace this function."""
    from managers.container_manager import exec_shell_argv
    return PtyProcess(exec_shell_argv(target.container), cols=cols, rows=rows)


# ── Sessions ─────────────────────────────────────────────────────────────────

Sink = Callable[[Any], None]


@dataclass
class TerminalSession:
    """One shell, its output buffer and, at most, one attached socket.

    A sink is a non-blocking callable the session hands each chunk of output
    to (bytes), then :data:`EXITED` when the shell ends or :data:`TAKEN_OVER`
    when another socket attaches. It is called under the session's lock,
    which is what makes an attach's replay and the live stream after it
    neither overlap nor leave a gap.
    """
    id: str
    target: Target
    user_id: str
    username: str
    actor: Dict[str, Any]
    process: Any
    buffer_limit: int
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    detached_at: Optional[float] = None
    closed: bool = False
    close_reason: str = ""
    #: Why it ended, as a word the panel translates: ``exited``, ``closed``
    #: (by the user), ``grace``, ``idle`` or ``shutdown``.
    close_code: str = ""
    exit_code: Optional[int] = None
    bytes_in: int = 0
    bytes_out: int = 0
    buffer: bytearray = field(default_factory=bytearray)
    sink: Optional[Sink] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    _pump: Optional[threading.Thread] = None

    def start(self) -> None:
        self.detached_at = time.time()
        self._pump = threading.Thread(target=self._read_loop, name=f"terminal-{self.id[:8]}",
                                      daemon=True)
        self._pump.start()

    def _read_loop(self) -> None:
        while not self.closed:
            try:
                data = self.process.read(0.25)
            except Exception:  # noqa: BLE001 - a broken pty ends the session, not the server
                log.debug("terminal %s: read failed", self.id, exc_info=True)
                data = None
            if data is None:
                break
            if data:
                self._output(data)
        if not self.closed:
            manager().close(self, reason="the shell exited", code="exited")

    def _output(self, data: bytes) -> None:
        with self.lock:
            self.buffer.extend(data)
            excess = len(self.buffer) - self.buffer_limit
            if excess > 0:
                del self.buffer[:excess]
            self.bytes_out += len(data)
            self.last_activity = time.time()
            if self.sink is not None:
                _deliver(self.sink, bytes(data))

    def attach(self, sink: Sink) -> bytes:
        """Make ``sink`` the receiver of new output and return the buffer so
        far, to be replayed first. A sink already attached is told it was
        taken over."""
        with self.lock:
            previous, self.sink = self.sink, sink
            self.detached_at = None
            if previous is not None and previous is not sink:
                _deliver(previous, TAKEN_OVER)
            return bytes(self.buffer)

    def detach(self, sink: Sink) -> None:
        with self.lock:
            if self.sink is sink:
                self.sink = None
                self.detached_at = time.time()

    def write(self, data: bytes) -> None:
        if self.closed or not data:
            return
        self.bytes_in += len(data)
        self.last_activity = time.time()
        self.process.write(data)

    def resize(self, cols: int, rows: int) -> None:
        cols = max(2, min(int(cols), 1000))
        rows = max(1, min(int(rows), 500))
        if not self.closed:
            self.process.resize(cols, rows)

    def describe(self) -> Dict[str, Any]:
        return {
            "session_id": self.id, "kind": self.target.kind, "target_id": self.target.id,
            "container": self.target.container, "label": self.target.label,
            "user": self.username, "created_at": self.created_at,
            "attached": self.sink is not None, "detached_at": self.detached_at,
            "last_activity": self.last_activity,
        }


def _deliver(sink: Sink, item: Any) -> None:
    try:
        sink(item)
    except Exception:  # noqa: BLE001 - a socket that went away mid-send is the reaper's to notice
        log.debug("terminal: sink refused an item", exc_info=True)


class SessionLimit(TerminalRefused):
    def __init__(self, limit: int):
        super().__init__(
            f"You already have {limit} terminal session(s) open, the most one account may hold. "
            "Close one (or wait for a dropped one's grace period to pass) and try again.", 429)


class TerminalManager:
    """Every open session in this process, and the reaper that ends them."""

    def __init__(self) -> None:
        self._sessions: Dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # reads

    def get(self, session_id: Optional[str]) -> Optional[TerminalSession]:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(str(session_id))
        return session if session is not None and not session.closed else None

    def for_user(self, user_id: str) -> List[TerminalSession]:
        with self._lock:
            return [s for s in self._sessions.values() if s.user_id == user_id and not s.closed]

    def all(self) -> List[TerminalSession]:
        with self._lock:
            return [s for s in self._sessions.values() if not s.closed]

    def check_limit(self, user_id: str) -> None:
        limit = max_per_user()
        if len(self.for_user(user_id)) >= limit:
            raise SessionLimit(limit)

    # open and close

    def open(self, principal: Principal, target: Target, *, cols: int = 80, rows: int = 24,
             ip: Optional[str] = None) -> TerminalSession:
        from common import audit
        with self._lock:
            limit = max_per_user()
            mine = [s for s in self._sessions.values()
                    if s.user_id == principal.id and not s.closed]
            if len(mine) >= limit:
                raise SessionLimit(limit)
            session_id = secrets.token_urlsafe(18)
            # Reserve the slot before the (slow) spawn, so two sockets opened
            # at once cannot both slip under the limit.
            placeholder = TerminalSession(
                id=session_id, target=target, user_id=principal.id,
                username=principal.username or principal.id,
                actor=audit.actor_fields(principal), process=None,
                buffer_limit=buffer_bytes())
            self._sessions[session_id] = placeholder
        try:
            placeholder.process = _spawn(target, max(2, int(cols)), max(1, int(rows)))
        except TerminalRefused:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        except Exception as exc:  # noqa: BLE001 - any spawn failure is reported to the person as a refusal
            with self._lock:
                self._sessions.pop(session_id, None)
            raise TerminalRefused(f"Could not start a shell in {target.container}: {exc}", 502)
        placeholder.start()
        self._ensure_reaper()
        audit.record("terminal.open", principal=principal, object_type=target.kind,
                     object_id=target.id, workspace=target.workspace, ip=ip,
                     details={"session_id": session_id, "container": target.container})
        return placeholder

    def resumed(self, session: TerminalSession, principal: Principal,
                ip: Optional[str] = None) -> None:
        from common import audit
        audit.record("terminal.resume", principal=principal, object_type=session.target.kind,
                     object_id=session.target.id, workspace=session.target.workspace, ip=ip,
                     details={"session_id": session.id, "container": session.target.container})

    def close(self, session: TerminalSession, *, reason: str, code: str = "closed") -> bool:
        """End ``session`` (idempotent): stop the shell, tell an attached
        socket, write the ``terminal.close`` audit row. True when this call
        was the one that closed it."""
        from common import audit
        with session.lock:
            if session.closed:
                return False
            session.closed = True
            session.close_reason = reason
            session.close_code = code
        if session.process is not None:
            try:
                session.process.close()
            except Exception:  # noqa: BLE001 - the session is over whatever the process says
                log.debug("terminal %s: close failed", session.id, exc_info=True)
            try:
                session.exit_code = session.process.exit_code()
            except Exception:  # noqa: BLE001 - a fake or a vanished process may not say
                log.debug("terminal %s: no exit code", session.id, exc_info=True)
        with self._lock:
            self._sessions.pop(session.id, None)
        audit.record("terminal.close", actor=session.actor, object_type=session.target.kind,
                     object_id=session.target.id, workspace=session.target.workspace,
                     details={"session_id": session.id, "container": session.target.container,
                              "reason": reason, "why": code, "exit_code": session.exit_code,
                              "seconds": round(time.time() - session.created_at, 1),
                              "bytes_in": session.bytes_in, "bytes_out": session.bytes_out})
        # The socket hears last, so whatever it does next (a reconnect, a
        # read of the audit log) already sees the session gone.
        with session.lock:
            sink, session.sink = session.sink, None
        if sink is not None:
            _deliver(sink, EXITED)
        return True

    def close_all(self, reason: str = "the hub is shutting down") -> None:
        for session in self.all():
            self.close(session, reason=reason, code="shutdown")

    # the reaper

    def reap(self, now: Optional[float] = None) -> List[str]:
        """Close the sessions past their grace period (no socket attached) or
        idle limit. Returns the ids it closed."""
        now = time.time() if now is None else now
        grace, idle = grace_seconds(), idle_seconds()
        closed: List[str] = []
        for session in self.all():
            reason, code = "", ""
            if session.sink is None and session.detached_at is not None \
                    and now - session.detached_at > grace:
                reason, code = f"no one reattached within {grace} s", "grace"
            elif now - session.last_activity > idle:
                reason, code = f"idle for more than {idle} s", "idle"
            if reason and self.close(session, reason=reason, code=code):
                closed.append(session.id)
        return closed

    def _ensure_reaper(self) -> None:
        with self._lock:
            if self._reaper is not None and self._reaper.is_alive():
                return
            self._stop.clear()
            self._reaper = threading.Thread(target=self._reap_loop, name="terminal-reaper",
                                            daemon=True)
            self._reaper.start()

    def _reap_loop(self) -> None:
        while not self._stop.wait(REAP_INTERVAL_SECONDS):
            try:
                self.reap()
            except Exception:  # noqa: BLE001 - the reaper must outlive one bad pass
                log.warning("terminal: reaper pass failed", exc_info=True)
            with self._lock:
                if not self._sessions:
                    self._reaper = None
                    return


_MANAGER = TerminalManager()


def manager() -> TerminalManager:
    """The process-wide session registry."""
    return _MANAGER


@atexit.register
def _close_on_exit() -> None:  # pragma: no cover - interpreter shutdown
    try:
        _MANAGER.close_all()
    except Exception:  # noqa: BLE001 - nothing left to report to at exit
        log.debug("terminal: close at exit failed", exc_info=True)


__all__ = [
    "EXITED", "KINDS", "PtyProcess", "SessionLimit", "TAKEN_OVER", "Target",
    "TerminalManager", "TerminalRefused", "TerminalSession", "authorize", "buffer_bytes",
    "grace_seconds", "idle_seconds", "manager", "max_per_user", "principal_from_ticket",
    "resolve_target",
]
