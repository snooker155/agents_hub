"""
Remote state (common/remote_state.py): an agent process with no database calls
marked store functions on the hub, inside the workspace its run token was
minted for, and fails loudly on any store access nobody marked.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import UUID

import pytest
from pydantic import BaseModel

from common import db, remote_state, run_tokens
from common.remote_state import ScopeError, hub_call
from tasks.models import TaskStatus


# ── functions the tests mark ─────────────────────────────────────────────────

class Box(BaseModel):
    name: str
    size: int = 0


@dataclasses.dataclass
class Pair:
    left: int
    right: str


@hub_call("workspace")
def ws_echo(workspace: Optional[str] = None, value: int = 0):
    return {"workspace": workspace, "value": value}


@hub_call("task:task_id")
def task_title(task_id: str):
    from uuid import UUID
    from tasks.service import get_task
    found = get_task(UUID(task_id))
    return found.title if found else None


@hub_call("global")
def shapes():
    return {"when": datetime(2026, 10, 30, 12, tzinfo=timezone.utc), "day": date(2026, 10, 30),
            "pair": (1, "a"), "tags": {"x"}, "raw": b"\x00\x01", "path": Path("/tmp/x"),
            "box": Box(name="b", size=3), "dc": Pair(2, "z"), 7: "int key",
            "id": UUID("12345678-1234-5678-1234-567812345678"), "money": Decimal("1.50"),
            "status": TaskStatus.resolved}


@hub_call("global")
def fails(kind: str):
    if kind == "value":
        raise ValueError("bad value")
    if kind == "key":
        raise KeyError("missing")
    raise ScopeError("own error type")


@hub_call("global")
def opens_db():
    return db.get_conn().execute("SELECT 1").fetchone()[0]


@hub_call("global")
def returns_object():
    return object()


@hub_call("global")
def not_listed():  # its module is left out of REMOTE_MODULES in one test
    return 1


@pytest.fixture
def remote(monkeypatch):
    """This test's process as an agent container: no database, store calls go
    through the hub's dispatch (in process, JSON on the wire) with a run token
    of workspace ``acme``."""
    run_tokens._cache.clear()
    top = __name__.split(".", 1)[0]
    monkeypatch.setattr(remote_state, "REMOTE_MODULES", (__name__,))
    monkeypatch.setattr(remote_state, "_OWN_PACKAGES", remote_state._OWN_PACKAGES | {top})
    state = {"token": run_tokens.mint(run_id="run-remote", workspace="acme")}

    def transport(payload):
        wire = json.loads(json.dumps(payload))
        with remote_state.hub_side():
            row = run_tokens.resolve(state["token"])
            try:
                body = remote_state.dispatch(row, wire)
            except ScopeError as exc:
                raise ScopeError(str(exc))
        return json.loads(json.dumps(body))

    monkeypatch.setattr(remote_state, "_transport", transport)
    monkeypatch.setenv(remote_state.ENV, "1")
    yield state
    run_tokens._cache.clear()


# ── the process without a database ───────────────────────────────────────────

def test_a_remote_process_cannot_open_the_database(remote):
    with pytest.raises(remote_state.RemoteStateError, match="hub_call"):
        db.get_conn()
    with pytest.raises(remote_state.RemoteStateError):
        with db.transaction():
            pass
    # The hub side of a call opens it as usual.
    assert opens_db() == 1


def test_marked_functions_run_locally_outside_a_container():
    assert ws_echo("acme", 2) == {"workspace": "acme", "value": 2}
    assert opens_db() == 1


# ── scope ────────────────────────────────────────────────────────────────────

def test_workspace_scope_fills_in_and_refuses_another(remote):
    assert ws_echo(value=1) == {"workspace": "acme", "value": 1}
    assert ws_echo("acme") == {"workspace": "acme", "value": 0}
    with pytest.raises(ScopeError, match="not this run's workspace"):
        ws_echo("other")
    with pytest.raises(ScopeError):
        ws_echo("default")


def test_record_scope_follows_the_record_to_its_workspace(remote, monkeypatch):
    from tasks.service import create_task
    with remote_state.hub_side():
        mine = create_task("mine", workspace="acme")
        theirs = create_task("theirs", workspace="other")
    assert task_title(str(mine.id)) == "mine"
    with pytest.raises(ScopeError, match="not in this run's workspace"):
        task_title(str(theirs.id))
    # A task that does not exist reveals nothing: the function finds nothing.
    assert task_title("00000000-0000-0000-0000-000000000000") is None


def test_default_workspace_token_reaches_only_default_records(remote):
    from tasks.service import create_task
    with remote_state.hub_side():
        remote["token"] = run_tokens.mint(run_id="run-default", workspace=None)
        plain = create_task("plain")
        scoped = create_task("scoped", workspace="acme")
    assert task_title(str(plain.id)) == "plain"
    with pytest.raises(ScopeError):
        task_title(str(scoped.id))
    assert ws_echo() == {"workspace": None, "value": 0}


def test_a_bad_scope_fails_at_import():
    with pytest.raises(ValueError):
        hub_call("task")
    with pytest.raises(ValueError):
        hub_call(":x")


# ── values and errors ────────────────────────────────────────────────────────

def test_values_come_back_as_what_they_were(remote):
    got = shapes()
    assert got["when"] == datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    assert got["day"] == date(2026, 10, 30)
    assert got["pair"] == (1, "a") and got["tags"] == {"x"} and got["raw"] == b"\x00\x01"
    assert got["path"] == Path("/tmp/x")
    assert got["box"] == Box(name="b", size=3) and got["dc"] == Pair(2, "z")
    assert got[7] == "int key"
    assert got["id"] == UUID("12345678-1234-5678-1234-567812345678")
    assert got["money"] == Decimal("1.50") and got["status"] is TaskStatus.resolved


def test_exceptions_come_back_as_themselves(remote):
    with pytest.raises(ValueError, match="bad value"):
        fails("value")
    with pytest.raises(KeyError):
        fails("key")
    with pytest.raises(ScopeError, match="own error type"):
        fails("scope")
    with pytest.raises(TypeError, match="cannot send"):
        returns_object()


def test_only_listed_modules_and_marked_functions_run(remote, monkeypatch):
    monkeypatch.setattr(remote_state, "REMOTE_MODULES", ())
    with pytest.raises(LookupError):
        not_listed()


def test_classes_outside_the_codebase_are_never_rebuilt():
    with pytest.raises(ValueError, match="refusing"):
        remote_state.decode({"__rs": "model", "c": "subprocess:Popen", "v": {}})
    with pytest.raises(ValueError, match="refusing"):
        remote_state.decode({"__rs": "dataclass", "c": "os:stat_result", "v": {}})
    # An exception type from outside comes back as a plain RemoteCallError.
    err = remote_state._rebuild_error({"type": "subprocess:CalledProcessError", "message": "x"})
    assert isinstance(err, remote_state.RemoteCallError)


# ── through the app ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    top = __name__.split(".", 1)[0]
    monkeypatch.setattr(remote_state, "REMOTE_MODULES", (__name__,))
    monkeypatch.setattr(remote_state, "_OWN_PACKAGES", remote_state._OWN_PACKAGES | {top})
    run_tokens._cache.clear()
    return TestClient(app)


@pytest.mark.parametrize("mode", ["single", "multi"])
def test_the_route_needs_a_run_token_and_checks_scope(monkeypatch, client, mode):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", mode, raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    token = run_tokens.mint(run_id="run-route", workspace="acme")
    headers = {"Authorization": f"Bearer {token}"}
    key = f"{__name__}:ws_echo"

    ok = client.post("/api/run-state/call", json={"fn": key, "kwargs": {"value": 3}}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"ok": True, "result": {"workspace": "acme", "value": 3}}

    other = client.post("/api/run-state/call", json={"fn": key, "args": ["other"]}, headers=headers)
    assert other.status_code == 403
    missing = client.post("/api/run-state/call", json={"fn": "os:system", "args": ["true"]},
                          headers=headers)
    assert missing.status_code == 404
    bad = client.post("/api/run-state/call", json={"fn": key, "kwargs": {"nope": 1}}, headers=headers)
    assert bad.status_code == 400
    # No run token, no call: not even the single-mode operator.
    assert client.post("/api/run-state/call", json={"fn": key}).status_code in (401, 403)
