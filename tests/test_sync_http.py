"""Sync over HTTP, both ends in one app (hubsync/client.py, hubsync/materialize.py,
routes/sync.py): the client pushes a workspace to a space on the "remote"
(this same app behind a TestClient, as in test_hublink.py), the remote
materializes plain records into its own workspace and sends edits made there
back, and an encrypted space stays opaque: never materialized, the key's
public half and the counts available to the browser, a second device unlocks
only with the right passphrase."""
from __future__ import annotations

import pytest

from common.config import settings
from hublink import remote as hub_remote
from hublink import store as link_store
from hubsync import client as sync_client
from hubsync import crypto, materialize, vault
from hubsync.adapters import base


class MemAdapter(base.Adapter):
    kind = "memtest"
    order = 1

    def __init__(self):
        self.data = {}

    def list_ids(self, workspace, owner):
        return sorted(i for (w, i) in self.data if w == workspace)

    def read(self, workspace, record_id):
        doc = self.data.get((workspace, record_id))
        return dict(doc) if doc is not None else None

    def apply(self, workspace, record_id, doc, *, owner):
        self.data[(workspace, record_id)] = dict(doc)
        return record_id

    def delete(self, workspace, record_id):
        self.data.pop((workspace, record_id), None)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def hub(client, monkeypatch):
    def transport(method, url, *, headers, body, timeout, stream):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        resp = client.request(method, path, headers=headers, content=body)
        return hub_remote.Response(resp.status_code, dict(resp.headers), content=resp.content)
    monkeypatch.setattr(hub_remote, "transport", transport)
    monkeypatch.setattr(settings, "secret_key", crypto.generate_passphrase(), raising=False)
    monkeypatch.setattr(crypto, "ITERATIONS", 100_000)
    adapter = MemAdapter()
    monkeypatch.setattr(base, "_REGISTRY", {"memtest": adapter})
    monkeypatch.setattr(base, "_loaded", True)
    from workspace.storage import create_workspace_folder
    for name in ("laptop", "remote-ws", "remote-enc"):
        create_workspace_folder(name)
    link_store.put({**link_store.blank(), "url": "http://remote.test", "api_key": "", "name": "remote"})
    return adapter


def test_plain_sync_materializes_and_brings_remote_edits_back(hub, client):
    hub.data[("laptop", "n1")] = {"title": "Plan", "body": "draft", "updated_at": "2026-10-01T10:00:00"}
    sync_client.configure(enabled=True, encrypted=False, mappings=[{"workspace": "laptop", "space": "remote-ws"}])
    assert sync_client.run_pass()["pushed"] == 1
    materialize.run_all()
    assert hub.data[("remote-ws", "n1")]["body"] == "draft"
    # Edited on the remote's site, then back on the laptop.
    hub.data[("remote-ws", "n1")] = {"title": "Plan", "body": "edited there", "updated_at": "2026-10-01T11:00:00"}
    materialize.run_all()
    out = sync_client.run_pass()
    assert out["applied"] == 1 and hub.data[("laptop", "n1")]["body"] == "edited there"
    status = client.get("/api/hublink/sync").json()
    assert status["enabled"] and not status["encrypted"] and status["last_result"]["applied"] == 1


def test_encrypted_space_stays_opaque_and_unlocks_with_the_right_passphrase(hub, client):
    made = sync_client.setup_key(generate=True)
    passphrase = made["generated_passphrase"]
    assert passphrase and "sealed" not in str(client.get("/api/hublink/sync").json())
    sync_client.configure(enabled=True, encrypted=True, mappings=[{"workspace": "laptop", "space": "remote-enc"}])
    hub.data[("laptop", "d1")] = {"title": "Diary", "body": "private"}
    sync_client.run_pass()
    rec = vault.get_record("local", "remote-enc", "memtest", "d1")
    assert rec["encrypted"] and "private" not in rec["payload"] and "Diary" not in rec["payload"]
    materialize.run_all()
    assert ("remote-enc", "d1") not in hub.data

    here = client.get("/api/sync/here", params={"workspace": "remote-enc"}).json()
    assert here["synced"] and here["space"]["encrypted"] and here["space"]["counts"]["encrypted"] == 1
    key = crypto.derive_key(passphrase, here["key"]["salt"], here["key"]["iterations"])
    assert crypto.check_verifier(key, here["key"]["verifier"])
    full = client.get("/api/sync/spaces/remote-enc/records/memtest/d1").json()
    doc = crypto.decode_record(full["payload"], kind="memtest", record_id="d1", encrypted=True, key=key,
                               space="remote-enc")
    assert doc["data"]["body"] == "private" and doc["title"] == "Diary"

    # A second device of the same person: the wrong passphrase is refused.
    sync_client.forget_key()
    with pytest.raises(sync_client.SyncConfigError):
        sync_client.setup_key(passphrase="not the passphrase at all")
    assert sync_client.setup_key(passphrase=passphrase)["key_id"] == here["key"]["key_id"]


def test_a_space_cannot_switch_sealing_with_records_in_it(hub, client):
    sync_client.configure(enabled=True, encrypted=False, mappings=[{"workspace": "laptop", "space": "remote-ws"}])
    hub.data[("laptop", "n1")] = {"body": "plain"}
    sync_client.run_pass()
    sync_client.setup_key(generate=True)
    resp = client.put("/api/sync/spaces/remote-ws", json={"encrypted": True,
                                                           "key_id": sync_client.config()["key"]["key_id"]})
    assert resp.status_code == 409


def test_an_unknown_workspace_is_refused(hub, client):
    assert client.put("/api/sync/spaces/nowhere", json={"encrypted": False}).status_code == 404


def test_encryption_is_chosen_per_workspace(hub, client):
    """One workspace sealed, another in clear, in the same pass; a locked key
    holds back only the sealed one; the workspace route changes one mapping."""
    from workspace.storage import create_workspace_folder
    create_workspace_folder("notes")
    sync_client.setup_key(generate=True)
    sync_client.configure(enabled=True, mappings=[
        {"workspace": "laptop", "space": "remote-enc", "encrypted": True},
        {"workspace": "notes", "space": "remote-ws", "encrypted": False},
    ])
    hub.data[("laptop", "d1")] = {"title": "Diary", "body": "private"}
    hub.data[("notes", "n1")] = {"title": "Plan", "body": "open"}
    sync_client.run_pass()
    assert vault.get_record("local", "remote-enc", "memtest", "d1")["encrypted"]
    plain = vault.get_record("local", "remote-ws", "memtest", "n1")
    assert not plain["encrypted"]
    opened = crypto.decode_record(plain["payload"], kind="memtest", record_id="n1", encrypted=False, key=None,
                                  space="remote-ws")
    assert opened["data"]["body"] == "open"
    status = client.get("/api/hublink/sync").json()
    assert status["encrypted"] is True
    assert {m["workspace"]: m["encrypted"] for m in status["mappings"]} == {"laptop": True, "notes": False}

    # The key forgotten: sync stops, the choice per workspace stays.
    sync_client.forget_key()
    after = sync_client.status()
    assert not after["enabled"] and {m["workspace"]: m["encrypted"] for m in after["mappings"]} == {
        "laptop": True, "notes": False}

    # One workspace from its own Settings tab: the other mapping is untouched.
    out = client.put("/api/hublink/sync/workspaces/laptop", json={"synced": False})
    assert out.status_code == 200, out.text
    assert [m["workspace"] for m in out.json()["mappings"]] == ["notes"]
    out = client.put("/api/hublink/sync/workspaces/laptop",
                     json={"synced": True, "space": "remote-enc", "encrypted": True})
    assert {m["workspace"]: (m["space"], m["encrypted"]) for m in out.json()["mappings"]} == {
        "notes": ("remote-ws", False), "laptop": ("remote-enc", True)}


def test_a_mapping_saved_before_the_per_workspace_choice_keeps_the_old_flag(hub):
    link_store.update(sync={"enabled": False, "encrypted": True,
                            "mappings": [{"workspace": "laptop", "space": "remote-enc"}]})
    assert sync_client.mappings() == [{"workspace": "laptop", "space": "remote-enc", "encrypted": True}]


def test_the_workspace_route_needs_a_link(client):
    link_store.clear()
    out = client.put("/api/hublink/sync/workspaces/laptop", json={"synced": True})
    assert out.status_code == 409
