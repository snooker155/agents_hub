"""`ah setup`, the Linked hub step (cli/onboard/link.py): each mode from an
answers file, applied to this checkout's database and to a stack through its
API.

The "other hub" is this same application behind a TestClient, as in
test_hublink.py: hublink.remote.transport is pointed at it, so the wizard's
probe, the link record's whoami and the sync key all land on the app that
holds the record. The CLI must never start the link's background threads,
so every local apply is checked for that too.
"""
from __future__ import annotations

import threading

import pytest
from rich.console import Console

from cli.onboard import link as L
from cli.onboard import probe as P
from cli.onboard import wizard as W
from cli.onboard.ui import AnswersAsker
from common.config import settings
from hublink import remote as hub_remote
from hublink import store


@pytest.fixture(autouse=True)
def _drop_new_workspaces():
    """Remove the workspace folders this test made. They live on disk under
    the suite's one state root and outlive the per-test database, so a
    leftover "team" turned a later test's "create it when missing" path
    (test_setup_link.py) into a silent skip."""
    from workspace import storage
    before = {p.name for p in storage.list_workspace_folders()}
    yield
    for path in storage.list_workspace_folders():
        if path.name not in before:
            storage.delete_workspace_folder(path.name)


def _asker(answers: dict) -> AnswersAsker:
    return AnswersAsker(Console(width=160, record=True), answers)


def _text(a: AnswersAsker) -> str:
    return a.console.export_text(clear=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def other_hub(client, monkeypatch):
    """Route hublink's outbound calls into the test app; a host named
    down.test does not answer."""
    def transport(method, url, *, headers, body, timeout, stream):
        if "://down.test" in url:
            raise hub_remote.RemoteError(f"cannot reach {url}")
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        resp = client.request(method, path, headers=headers, content=body)
        return hub_remote.Response(resp.status_code, dict(resp.headers), content=resp.content)
    monkeypatch.setattr(hub_remote, "transport", transport)
    from hubsync import crypto
    monkeypatch.setattr(settings, "secret_key", crypto.generate_passphrase(), raising=False)
    monkeypatch.setattr(crypto, "ITERATIONS", 100_000)
    from workspace.storage import create_workspace_folder
    create_workspace_folder("remote-ws")
    return client


@pytest.fixture
def started(monkeypatch):
    """Records any start of the link's or the sync's background parts."""
    calls: list = []
    from hublink import runtime
    from hubsync import client as sync_client
    monkeypatch.setattr(runtime, "start", lambda: calls.append("runtime.start"))
    monkeypatch.setattr(runtime, "refresh", lambda: calls.append("runtime.refresh"))
    monkeypatch.setattr(sync_client, "start", lambda: calls.append("hubsync.start"))
    return calls


def _no_threads(calls: list) -> None:
    assert calls == []
    names = {t.name for t in threading.enumerate()}
    assert not names & {"hublink-drain", "hubsync-client"}


def _local(answers: dict, **plan) -> tuple:
    plan_obj = W.Plan(shape="local", fresh=True, **plan)
    a = _asker({"link": answers})
    L.step_link(a, plan_obj, 8, W.STEPS)
    L.apply_local(a, plan_obj)
    return a, plan_obj


BASE = {"enabled": True, "url": "http://remote.test/", "api_key": "ahk_secret_key_1"}


def test_catalog_only_turns_the_catalog_on_and_sends_nothing(other_hub, started):
    a, plan = _local({**BASE, "mode": "catalog"})
    doc = store.get()
    assert doc["url"] == "http://remote.test" and doc["api_key"] == "ahk_secret_key_1"
    assert doc["catalog"] is True
    assert doc["mirror"] == {"runs": False, "chats": False, "memory": False}
    assert doc["use_models"] is False and doc["use_agents"] is False
    assert set(doc["remote"]) == {"mode", "principal", "personal_workspace"}  # the whoami cache
    assert not (doc.get("sync") or {}).get("enabled")
    from providers import registry
    assert registry.get_backend(store.BACKEND_ID) is None
    assert any("catalog only" in line for line in L.actions(plan))
    assert "ahk_secret_key_1" not in _text(a)
    _no_threads(started)


def test_two_way_sync_maps_workspaces_and_creates_the_missing_one(other_hub, started):
    from hubsync import client as sync_client
    from hubsync import vault
    from workspace.storage import get_workspace_folder
    a, plan = _local({**BASE, "mode": "sync", "sync": {"workspaces": ["laptop"],
                                                       "spaces": {"laptop": "remote-ws"}}})
    assert get_workspace_folder("laptop")
    cfg = sync_client.config()
    assert cfg["enabled"] is True and cfg["encrypted"] is False
    assert cfg["mappings"] == [{"workspace": "laptop", "space": "remote-ws"}]
    assert vault.get_space("local", "remote-ws")["encrypted"] in (False, 0)
    doc = store.get()
    assert doc["catalog"] is False and doc["mirror"]["chats"] is False
    assert any("laptop → remote-ws" in line and "readable there" in line for line in L.actions(plan))
    _no_threads(started)


def test_encrypted_sync_generates_the_passphrase_and_shows_it_once(other_hub, started):
    from hubsync import client as sync_client
    a, plan = _local({**BASE, "mode": "sync-e2e", "sync": {"workspaces": "laptop",
                                                           "spaces": {"laptop": "remote-ws"}}})
    [(label, passphrase)] = plan.generated
    assert label == "Sync passphrase" and len(passphrase) >= 12
    assert plan.generated_notes == [L.PASSPHRASE_WARNING]
    cfg = sync_client.config()
    assert cfg["enabled"] and cfg["encrypted"] and cfg["key"]["sealed"]
    assert passphrase not in cfg["key"]["sealed"]
    assert sync_client.unlocked_key() is not None
    assert passphrase not in _text(a)                     # not before the farewell
    W._farewell(a, plan, started=False)
    out = _text(a)
    assert out.count(passphrase) == 1 and "losing it and every" in out
    assert "ahk_secret_key_1" not in out
    _no_threads(started)


def test_a_second_device_types_the_passphrase_and_nothing_is_generated(other_hub, started):
    from hubsync import client as sync_client
    _, first = _local({**BASE, "mode": "sync-e2e", "sync": {"workspaces": ["laptop"],
                                                            "spaces": {"laptop": "remote-ws"}}})
    passphrase = first.generated[0][1]
    key_id = sync_client.config()["key"]["key_id"]
    sync_client.forget_key()
    store.clear()
    a, plan = _local({**BASE, "mode": "sync-e2e", "sync": {"workspaces": ["laptop"],
                                                           "spaces": {"laptop": "remote-ws"},
                                                           "passphrase": passphrase}})
    assert plan.generated == []
    cfg = sync_client.config()
    assert cfg["key"]["key_id"] == key_id and cfg["enabled"] and cfg["encrypted"]
    assert passphrase not in _text(a)
    _no_threads(started)


def test_connect_mode_wires_its_models_and_agents_and_the_run_mirror(other_hub, started):
    from mcp_client import store as mcp_store
    from providers import registry
    _local({**BASE, "mode": "connect", "name": "laptop"})
    doc = store.get()
    assert doc["use_models"] and doc["use_agents"] and doc["catalog"] is False
    assert registry.get_backend(store.BACKEND_ID)["base_url"] == "http://remote.test/v1"
    assert mcp_store.get_server("default", store.MCP_SERVER_ID) is not None
    assert doc["mirror"]["runs"] is True and doc["connection"]["id"] == "node-laptop"
    _no_threads(started)


def test_a_failed_probe_in_an_answers_file_warns_and_goes_on(other_hub, started):
    a, plan = _local({"enabled": True, "url": "http://down.test", "api_key": "k", "mode": "catalog"})
    assert plan.link["checked"] is False
    assert "Could not check it" in _text(a)
    assert any("not checked" in line for line in L.actions(plan))
    doc = store.get()
    assert doc["url"] == "http://down.test" and doc["catalog"] is True and doc["last_error"]
    _no_threads(started)


def test_kubernetes_leaves_the_link_to_settings():
    plan = W.Plan(shape="kubernetes", fresh=True)
    a = _asker({"link": {**BASE, "mode": "catalog"}})
    L.step_link(a, plan, 8, W.STEPS)
    assert plan.link == {} and L.actions(plan) == []
    assert "Settings, Remote hub" in _text(a)


def test_no_link_by_default_and_a_rerun_defaults_to_the_record(other_hub, started):
    plan = W.Plan(shape="local", fresh=True)
    L.step_link(_asker({}), plan, 8, W.STEPS)
    assert plan.link == {}
    _local({**BASE, "mode": "sync", "sync": {"workspaces": ["laptop"], "spaces": {"laptop": "remote-ws"}}})
    rerun = W.Plan(shape="local", fresh=False, current={})
    L.step_link(_asker({"link": {"enabled": True}}), rerun, 8, W.STEPS)
    assert rerun.link["url"] == "http://remote.test" and rerun.link["api_key"] == "ahk_secret_key_1"
    assert rerun.link["mode"] == "sync"
    assert rerun.link["sync"]["spaces"] == {"laptop": "remote-ws"}


# ---- a stack in Docker -------------------------------------------------------


@pytest.fixture
def stack(other_hub, monkeypatch):
    """The stack's API is the same test app; ah setup reaches it with requests."""
    import requests
    from hublink import runtime
    monkeypatch.setattr(runtime, "refresh", lambda: None)   # the stack's own, in this process
    calls: list = []

    def fake(method, url, headers=None, timeout=None, json=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        calls.append((method, path))
        return other_hub.request(method, path, headers=headers, json=json)
    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(P, "probe", lambda provider, api_key="", base_url="", timeout=10.0:
                        P.ProbeResult(False, error="could not connect"))
    monkeypatch.setattr(W, "_has_docker", lambda: True)
    monkeypatch.setattr(W, "apply_docker", lambda a, plan, start: start)
    return calls


DOCKER = {"shape": "docker", "database": "sqlite", "auth": "single", "providers": [], "start": True,
          "point_cli": False}


# A name no other test file uses: workspace folders outlive the per-test
# database, so "team" may be left on disk by any earlier file, and then the
# stack rightly skips creating it.
SYNC_WS = "setup-link-team"


def test_a_started_stack_gets_the_link_and_encrypted_sync_over_its_api(tmp_path, stack):
    from hubsync import client as sync_client
    from workspace.storage import get_workspace_folder
    assert get_workspace_folder(SYNC_WS) is None
    a = _asker({**DOCKER, "dir": str(tmp_path / "hub"), "link": {
        **BASE, "mode": "sync-e2e", "sync": {"workspaces": [SYNC_WS], "spaces": {SYNC_WS: "remote-ws"}}}})
    assert W.run(a, yes=True) == 0
    sent = set(stack)
    assert {("PUT", "/api/hublink"), ("PUT", "/api/catalog/settings"), ("POST", "/api/hublink/sync/key"),
            ("PUT", "/api/hublink/sync"), ("POST", "/api/workspaces")} <= sent
    assert get_workspace_folder(SYNC_WS)
    cfg = sync_client.config()
    assert cfg["enabled"] and cfg["encrypted"] and cfg["mappings"] == [{"workspace": SYNC_WS, "space": "remote-ws"}]
    assert store.get()["mirror"] == {"runs": False, "chats": False, "memory": False}
    out = _text(a)
    assert out.count("Sync passphrase:") == 1 and "losing it and every" in out
    assert "ahk_secret_key_1" not in out


def test_a_stack_that_already_has_the_workspace_is_not_asked_to_make_it_again(tmp_path, stack):
    """The other side of the same step: a workspace the stack already holds
    is looked up, never created twice (a second POST would answer 409)."""
    from hubsync import client as sync_client
    from workspace.storage import create_workspace_folder
    create_workspace_folder(SYNC_WS)
    a = _asker({**DOCKER, "dir": str(tmp_path / "hub"), "link": {
        **BASE, "mode": "sync", "sync": {"workspaces": [SYNC_WS], "spaces": {SYNC_WS: "remote-ws"}}}})
    assert W.run(a, yes=True) == 0
    assert ("GET", f"/api/workspaces/{SYNC_WS}") in stack and ("POST", "/api/workspaces") not in stack
    assert sync_client.config()["mappings"] == [{"workspace": SYNC_WS, "space": "remote-ws"}]


def test_a_stack_not_started_is_linked_on_a_later_run(tmp_path, stack):
    a = _asker({**DOCKER, "start": False, "dir": str(tmp_path / "hub"), "link": {**BASE, "mode": "catalog"}})
    assert W.run(a, yes=True) == 0
    assert stack == [] and store.get() is None
    assert "The linked hub is set once the stack runs" in _text(a)
