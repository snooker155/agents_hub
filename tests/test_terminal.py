"""The terminal into a run's or a replica's container (docs/terminal.md).

``common/terminal.py`` keeps the shell sessions, ``routes/terminal.py`` mints
the ticket and serves the socket. Docker is never called: ``_spawn`` is
replaced by a fake process that echoes its input, and the container's state
by a stub. One test drives a real pseudo-terminal with ``/bin/sh``.

Run: ``python -m pytest tests/test_terminal.py -q``
"""
from __future__ import annotations

import os
import queue
import socket
import sys
import time
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common import preview_tickets  # noqa: E402 - after the sys.path shim above
from common import terminal as term  # noqa: E402 - same

PASSWORD = "hunter2-but-longer"
CONTAINER = "agents-hub-run-abc123"


class FakeProcess:
    """A shell that echoes what it is sent, prefixed, and can be told to print
    or exit."""

    def __init__(self, cols=80, rows=24):
        self.out: "queue.Queue" = queue.Queue()
        self.written = []
        self.sizes = [(cols, rows)]
        self.closed = False
        self.code = None

    def read(self, timeout):
        if self.closed:
            return None
        try:
            return self.out.get(timeout=timeout)
        except queue.Empty:
            return b""

    def write(self, data):
        self.written.append(data)
        self.out.put(b"echo:" + data)

    def resize(self, cols, rows):
        self.sizes.append((cols, rows))

    def exit_code(self):
        return self.code

    def close(self):
        if not self.closed:
            self.closed = True
            self.out.put(None)

    def emit(self, data: bytes):
        self.out.put(data)

    def exit(self, code: int):
        self.code = code
        self.out.put(None)


def _wait(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def procs(monkeypatch):
    """A fresh session registry, a fake shell per open, and a running container."""
    from managers import container_manager
    spawned = []

    def fake_spawn(target, cols, rows):
        proc = FakeProcess(cols, rows)
        spawned.append(proc)
        return proc

    manager = term.TerminalManager()
    monkeypatch.setattr(term, "_MANAGER", manager)
    monkeypatch.setattr(term, "_spawn", fake_spawn)
    monkeypatch.setattr(container_manager, "container_status", lambda name: "running")
    yield spawned
    manager.close_all("test over")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


def _container_run(**fields) -> str:
    from managers import run_manager as rm
    from tasks import service as ts
    task = ts.create_task("terminal")
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(task.id), status=fields.pop("status", "running"),
                link_to_session=False)
    update = {"container_name": CONTAINER, "execution_mode": "docker"}
    update.update(fields)
    rm.update_run(rid, update)
    return rid


def _ticket(client, rid, session_id=None, headers=None):
    body = {"session_id": session_id} if session_id else {}
    response = client.post(f"/api/terminal/run/{rid}/ticket", json=body, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def _audit(action):
    from common import audit
    return audit.query(action=action)["items"]


# ── tickets ──────────────────────────────────────────────────────────────────

def _user(user_id="u1"):
    from common.auth import Principal
    return Principal(id=user_id, username="x", kind="user", via="session")


def test_a_terminal_ticket_opens_only_its_own_target_and_only_once():
    ticket = preview_tickets.mint_terminal(_user(), target_kind="run", target_id="r1")
    assert preview_tickets.verify_terminal(ticket, target_kind="run", target_id="r2") is None
    assert preview_tickets.verify_terminal(ticket, target_kind="replica", target_id="r1") is None
    found = preview_tickets.verify_terminal(ticket, target_kind="run", target_id="r1")
    assert found["user"] == "u1" and found["session"] is None
    assert preview_tickets.verify_terminal(ticket, target_kind="run", target_id="r1") is None


def test_terminal_and_auth_tickets_do_not_cross(monkeypatch):
    auth = preview_tickets.mint_auth(_user())
    assert preview_tickets.verify_terminal(auth, target_kind="run", target_id="r1") is None
    ticket = preview_tickets.mint_terminal(_user(), target_kind="run", target_id="r1",
                                           session_id="s1")
    assert preview_tickets.verify_auth(ticket) is None
    assert preview_tickets.verify_terminal(ticket, target_kind="run", target_id="r1")["session"] == "s1"
    late = preview_tickets.mint_terminal(_user(), target_kind="run", target_id="r1", ttl_seconds=1)
    later = time.time() + 120
    monkeypatch.setattr(preview_tickets.time, "time", lambda: later)
    assert preview_tickets.verify_terminal(late, target_kind="run", target_id="r1") is None


def test_the_socket_refuses_a_missing_or_foreign_ticket(procs, client):
    from starlette.websockets import WebSocketDisconnect
    rid = _container_run()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/api/terminal/run/{rid}/ws"):
            pass
    assert exc.value.code == 4401
    auth = preview_tickets.mint_auth(_user())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={auth}"):
            pass
    other = _container_run()
    ticket = _ticket(client, other)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={ticket}"):
            pass
    assert procs == []


# ── refusals ─────────────────────────────────────────────────────────────────

def test_a_local_run_is_refused_with_a_clear_message(procs, client):
    rid = _container_run(container_name=None, execution_mode="local")
    from managers import run_manager as rm
    rm.update_run(rid, {"container_name": ""})
    response = client.post(f"/api/terminal/run/{rid}/ticket", json={})
    assert response.status_code == 409
    assert "local mode" in response.json()["detail"]


def test_a_finished_run_says_its_container_is_gone(procs, client, monkeypatch):
    rid = _container_run(status="completed")
    response = client.post(f"/api/terminal/run/{rid}/ticket", json={})
    assert response.status_code == 410
    assert "removed" in response.json()["detail"]

    from managers import container_manager
    monkeypatch.setattr(container_manager, "container_status", lambda name: None)
    live = _container_run()
    response = client.post(f"/api/terminal/run/{live}/ticket", json={})
    assert response.status_code == 410 and "no longer exists" in response.json()["detail"]


def test_unknown_targets_and_other_hosts(procs, client):
    assert client.post("/api/terminal/run/nope/ticket", json={}).status_code == 404
    assert client.post("/api/terminal/pod/x/ticket", json={}).status_code == 404
    rid = _container_run(host="another-host.example")
    response = client.post(f"/api/terminal/run/{rid}/ticket", json={})
    assert response.status_code == 409 and "another-host.example" in response.json()["detail"]


# ── a session over the socket ────────────────────────────────────────────────

def test_open_type_resize_and_audit(procs, client):
    rid = _container_run()
    ticket = _ticket(client, rid)
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={ticket}&cols=100&rows=30") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "session" and hello["resumed"] is False
        assert hello["container"] == CONTAINER and hello["grace_seconds"] == 60
        proc = procs[0]
        assert proc.sizes[0] == (100, 30)
        ws.send_json({"type": "input", "data": "ls\r"})
        assert ws.receive_bytes() == b"echo:ls\r"
        ws.send_json({"type": "resize", "cols": 132, "rows": 40})
        assert _wait(lambda: proc.sizes[-1] == (132, 40))
        session_id = hello["session_id"]
    assert _wait(lambda: term.manager().get(session_id).sink is None)
    opened = _audit("terminal.open")
    assert opened and opened[0]["object_id"] == rid
    assert opened[0]["details"]["container"] == CONTAINER


def test_the_shell_exiting_is_reported_and_closes_the_session(procs, client):
    rid = _container_run()
    ticket = _ticket(client, rid)
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={ticket}") as ws:
        session_id = ws.receive_json()["session_id"]
        procs[0].exit(0)
        message = ws.receive_json()
        assert message["type"] == "exit" and message["code"] == 0
    assert term.manager().get(session_id) is None
    closed = _audit("terminal.close")
    assert closed and closed[0]["details"]["reason"] == "the shell exited"


def test_reconnect_replays_the_buffer_then_streams(procs, client):
    rid = _container_run()
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={_ticket(client, rid)}") as ws:
        session_id = ws.receive_json()["session_id"]
        procs[0].emit(b"hello ")
        assert ws.receive_bytes() == b"hello "
    session = term.manager().get(session_id)
    assert _wait(lambda: session.sink is None)
    # Output that arrives while nobody watches still lands in the buffer.
    procs[0].emit(b"while away")
    assert _wait(lambda: bytes(session.buffer).endswith(b"while away"))

    ticket = _ticket(client, rid, session_id=session_id)
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={ticket}&cols=90&rows=20") as ws:
        hello = ws.receive_json()
        assert hello["resumed"] is True and hello["session_id"] == session_id
        assert ws.receive_bytes() == b"hello while away"
        ws.send_json({"type": "input", "data": "pwd\r"})
        assert ws.receive_bytes() == b"echo:pwd\r"
    assert len(procs) == 1
    assert procs[0].sizes[-1] == (90, 20)
    assert _audit("terminal.resume")


def test_a_second_socket_takes_the_session_over(procs, client):
    rid = _container_run()
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={_ticket(client, rid)}") as first:
        session_id = first.receive_json()["session_id"]
        ticket = _ticket(client, rid, session_id=session_id)
        with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={ticket}") as second:
            assert second.receive_json()["resumed"] is True
            assert first.receive_json()["type"] == "taken_over"


def test_closing_from_the_panel_ends_the_session_at_once(procs, client):
    from starlette.websockets import WebSocketDisconnect
    rid = _container_run()
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={_ticket(client, rid)}") as ws:
        session_id = ws.receive_json()["session_id"]
        ws.send_json({"type": "close"})
        # Wait for the server's close frame: leaving the block cancels the
        # handler, which could otherwise land before it reads the message.
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert term.manager().get(session_id) is None
    assert procs[0].closed
    assert _audit("terminal.close")[0]["details"]["reason"] == "closed by the user"
    response = client.post(f"/api/terminal/run/{rid}/ticket", json={"session_id": session_id})
    assert response.status_code == 410


# ── grace, idle, limits, buffer ──────────────────────────────────────────────

def _open_direct(rid, user=None):
    from common.auth import LOCAL_PRINCIPAL
    target = term.resolve_target("run", rid)
    return term.manager().open(user or LOCAL_PRINCIPAL, target)


def test_a_dropped_session_waits_its_grace_period_then_closes(procs):
    rid = _container_run()
    session = _open_direct(rid)
    manager = term.manager()
    assert manager.reap(now=time.time() + 30) == []
    assert manager.reap(now=time.time() + 61) == [session.id]
    assert procs[0].closed and manager.get(session.id) is None
    row = _audit("terminal.close")[0]
    assert "reattached" in row["details"]["reason"]


def test_an_idle_session_closes_even_while_attached(procs, monkeypatch):
    monkeypatch.setenv("TERMINAL_IDLE_SECONDS", "120")
    rid = _container_run()
    session = _open_direct(rid)
    session.attach(lambda item: None)
    manager = term.manager()
    assert manager.reap(now=time.time() + 100) == []
    assert manager.reap(now=session.last_activity + 121) == [session.id]
    assert "idle" in _audit("terminal.close")[0]["details"]["reason"]


def test_sessions_per_user_are_limited(procs, client, monkeypatch):
    monkeypatch.setenv("TERMINAL_MAX_PER_USER", "2")
    rid = _container_run()
    _open_direct(rid)
    _open_direct(rid)
    response = client.post(f"/api/terminal/run/{rid}/ticket", json={})
    assert response.status_code == 429 and "2 terminal session" in response.json()["detail"]
    with pytest.raises(term.SessionLimit):
        _open_direct(rid)


def test_the_buffer_keeps_only_the_newest_bytes(procs, monkeypatch):
    monkeypatch.setenv("TERMINAL_BUFFER_BYTES", "1024")
    rid = _container_run()
    session = _open_direct(rid)
    procs[0].emit(b"a" * 1000)
    procs[0].emit(b"b" * 100)
    assert _wait(lambda: session.bytes_out == 1100)
    assert len(session.buffer) == 1024 and bytes(session.buffer).endswith(b"b" * 100)
    assert session.attach(lambda item: None) == bytes(session.buffer)


# ── who may open one ─────────────────────────────────────────────────────────

def test_authorize_in_multi_mode(multi):
    from common import identity
    from common.auth import Principal
    from common.identity import SERVICE_PRINCIPAL
    owner = identity.create_user("owner", PASSWORD)
    other = identity.create_user("other", PASSWORD)
    viewer = identity.create_user("viewer", PASSWORD)
    identity.set_member("ws1", owner["id"], "editor")
    identity.set_member("ws1", other["id"], "editor")
    identity.set_member("ws1", viewer["id"], "viewer")

    def p(user, role="member", scope=None):
        return Principal(id=user["id"], username=user["username"], role=role, kind="user",
                         scope=scope)

    target = term.Target(kind="run", id="r1", container="c", workspace="ws1", owner=owner["id"])
    term.authorize(p(owner), target)
    term.authorize(p(other, role="admin"), target)
    for who, code in ((p(other), 403), (p(viewer), 403), (SERVICE_PRINCIPAL, 403), (None, 401)):
        with pytest.raises(term.TerminalRefused) as exc:
            term.authorize(who, target)
        assert exc.value.status == code
    with pytest.raises(term.TerminalRefused):
        term.authorize(p(owner, scope=("ws2",)), target)
    # Owner unknown: admins only.
    orphan = term.Target(kind="run", id="r2", container="c", workspace="ws1", owner=None)
    with pytest.raises(term.TerminalRefused) as exc:
        term.authorize(p(owner), orphan)
    assert "only an admin" in exc.value.detail


def test_the_ticket_route_applies_the_rule_in_multi_mode(procs, client, multi, monkeypatch):
    from common import identity
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    admin = {"Authorization": f"Bearer {response.json()['token']}"}
    member = identity.create_user("member", PASSWORD)
    identity.set_member("default", member["id"], "editor")
    member_auth = {"Authorization": f"Bearer {identity.open_session(member['id'])['token']}"}
    rid = _container_run(workspace="default")
    monkeypatch.setattr(term, "_chat_or_task_owner", lambda run: "someone-else")
    refused = client.post(f"/api/terminal/run/{rid}/ticket", json={}, headers=member_auth)
    assert refused.status_code == 403 and "owner" in refused.json()["detail"]
    ticket = _ticket(client, rid, headers=admin)
    with client.websocket_connect(f"/api/terminal/run/{rid}/ws?ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "session"
    assert _audit("terminal.open")[0]["actor_name"] == "root"

    monkeypatch.setattr(term, "_chat_or_task_owner", lambda run: member["id"])
    assert client.post(f"/api/terminal/run/{rid}/ticket", json={},
                       headers=member_auth).status_code == 200


# ── replicas ─────────────────────────────────────────────────────────────────

def test_a_replica_target_and_its_refusals(procs, monkeypatch):
    from instances import carrier
    from instances import store as istore
    from services import store as sstore
    monkeypatch.setattr(carrier, "sync", lambda inst: inst)
    service = sstore.create(name="svc", agent_id="main-agent", workspace="default",
                            created_by="u-owner")
    docker = istore.create("main-agent", kind="resident", workspace="default", state="active",
                           service_id=service["service_id"], container_name="agents-hub-node-1",
                           carrier_mode="docker", carrier_host=socket.gethostname(), label="svc #1")
    target = term.resolve_target("replica", docker["instance_id"])
    assert target.container == "agents-hub-node-1" and target.owner == "u-owner"
    assert target.workspace == "default" and target.label == "svc #1"

    local = istore.create("main-agent", kind="resident", workspace="default", state="active",
                          service_id=service["service_id"], carrier_mode="local", pid=123)
    with pytest.raises(term.TerminalRefused) as exc:
        term.resolve_target("replica", local["instance_id"])
    assert "process on the hub's host" in exc.value.detail

    stopped = istore.create("main-agent", kind="resident", workspace="default", state="stopped",
                            service_id=service["service_id"], container_name="agents-hub-node-2",
                            carrier_mode="docker")
    with pytest.raises(term.TerminalRefused) as exc:
        term.resolve_target("replica", stopped["instance_id"])
    assert exc.value.status == 410

    loose = istore.create("main-agent", kind="resident", state="active",
                          container_name="agents-hub-node-3", carrier_mode="docker")
    with pytest.raises(term.TerminalRefused) as exc:
        term.resolve_target("replica", loose["instance_id"])
    assert exc.value.status == 404


# ── the real thing, minus Docker ─────────────────────────────────────────────

def test_exec_shell_argv_is_docker_exec_with_a_tty():
    from managers.container_manager import exec_shell_argv
    argv = exec_shell_argv("agents-hub-run-x")
    assert argv[:3] == ["docker", "exec", "-it"] and "agents-hub-run-x" in argv


@pytest.mark.skipif(os.name != "posix", reason="ptys are POSIX only")
def test_a_real_pty_round_trip():
    proc = term.PtyProcess(["/bin/sh", "-c", "printf ready; read line; printf got:$line; "
                                              "stty size"], cols=77, rows=21)
    seen = b""
    try:
        deadline = time.time() + 5
        while b"ready" not in seen and time.time() < deadline:
            seen += proc.read(0.2) or b""
        proc.write(b"abc\n")
        while time.time() < deadline:
            chunk = proc.read(0.2)
            if chunk is None:
                break
            seen += chunk
        assert b"got:abc" in seen
        assert b"21 77" in seen
    finally:
        proc.close()
    assert proc.read(0.1) is None
