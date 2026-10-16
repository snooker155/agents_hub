"""Residuals of the sync review (hubsync/, docs/hub-sync.md), one block per item:

* only version 2 envelopes are written and read, and the browser's test
  vector holds nothing else (hubsync was never released with version 1, and a
  version 1 envelope does not bind its space);
* a synced flow lands owned by its workspace, never global, and a deletion
  from the other side never removes a flow another workspace can see (#18);
* a key that already exists answers a 409 whose detail says whether it can
  be replaced, and replacing an unused one works end to end (#31);
* ``Adapter.merge`` is a real hook with a default, which the engine calls;
* linking while sync is on creates no mirror connection on the remote;
* an upload updated to bytes another held file has keeps its old record
  until the new mapping stands, so it is never pushed as a deletion (#6).
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import os
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.config import settings
from hublink import remote as hub_remote
from hublink import store as link_store
from hubsync import client as sync_client
from hubsync import crypto, engine, state, vault
from hubsync.adapters import base
from hubsync.adapters.base import AdapterError

VECTOR = Path(__file__).resolve().parents[1] / "dashboard/frontend/src/lib/__tests__/fixtures/syncVector.json"


@pytest.fixture(autouse=True)
def _drop_new_workspaces():
    """Workspace folders live under the suite's one state root and outlive
    the per-test database; leaving them would change what later tests see.
    ``default`` stays even when a test made it: the hub creates it once per
    process, so removing it would break every later test that relies on it."""
    from workspace import storage
    before = {p.name for p in storage.list_workspace_folders()} | {"default"}
    yield
    for path in storage.list_workspace_folders():
        if path.name not in before:
            storage.delete_workspace_folder(path.name)


def _key():
    return crypto.derive_key("correct horse battery", crypto.new_salt(), 1000)


# ── envelopes: version 2 only ────────────────────────────────────────────────

def _sealed_v1(key, doc, kind, record_id):
    nonce = os.urandom(12)
    packed = gzip.compress(json.dumps(doc).encode())
    sealed = AESGCM(key).encrypt(nonce, packed, f"agents-hub/v1|{kind}|{record_id}".encode())
    return json.dumps({"v": 1, "alg": crypto.ALG, "kid": crypto.key_id(key),
                       "n": base64.b64encode(nonce).decode(), "c": base64.b64encode(sealed).decode()})


def test_a_version_1_envelope_is_refused():
    key = _key()
    payload = _sealed_v1(key, {"data": {"body": "old"}}, "memtest", "r")
    with pytest.raises(crypto.CryptoError, match="unknown envelope format"):
        crypto.decode_record(payload, kind="memtest", record_id="r", encrypted=True, key=key, space="any")
    assert not hasattr(crypto, "READABLE_VERSIONS")


def test_only_version_2_is_written_and_an_unknown_version_is_refused():
    key = _key()
    payload = crypto.encode_record({"data": {"x": 1}}, kind="k", record_id="r", key=key, space="s")
    envelope = json.loads(payload)
    assert envelope["v"] == 2
    assert crypto.decode_record(payload, kind="k", record_id="r", encrypted=True, key=key,
                                space="s") == {"data": {"x": 1}}
    for version in (1, 3, None):
        with pytest.raises(crypto.CryptoError):
            crypto.open_(key, {**envelope, "v": version}, crypto.record_aad("k", "r", "s"))
    with pytest.raises(crypto.CryptoError):
        crypto.decode_record("[1, 2]", kind="k", record_id="r", encrypted=True, key=key, space="s")
    with pytest.raises(crypto.CryptoError):
        crypto.record_aad("k", "r")  # no space, no record


def test_the_browser_vector_is_version_2_and_opens_here():
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    assert not [k for k in vector if k.endswith("_v1")]
    assert json.loads(vector["verifier"])["v"] == 2 and json.loads(vector["payload_encrypted"])["v"] == 2
    key = crypto.derive_key(vector["passphrase"], vector["salt"], vector["iterations"])
    assert crypto.key_id(key) == vector["key_id"] and crypto.check_verifier(key, vector["verifier"])
    doc = crypto.decode_record(vector["payload_encrypted"], kind=vector["kind"], record_id=vector["record_id"],
                               encrypted=True, key=key, space=vector["space"])
    assert doc == vector["doc"]
    with pytest.raises(crypto.CryptoError):
        crypto.decode_record(vector["payload_encrypted"], kind=vector["kind"], record_id=vector["record_id"],
                             encrypted=True, key=key, space="another space")


# ── #18: flows stay in the workspace they were synced into ───────────────────

def _flows_seen(workspace):
    from dashboard.backend.routes import flows as flow_routes
    page = asyncio.run(flow_routes.list_flows(workspace=workspace))
    return {f["id"] if isinstance(f, dict) else f.id for f in page}


def _flow_doc(name="Synced"):
    return {"name": name, "description": "", "nodes": [], "edges": []}


@pytest.fixture
def flow_spaces():
    from workspace import create_workspace_folder
    names = [f"fl-{uuid.uuid4().hex[:6]}" for _ in range(3)]
    for name in names:
        create_workspace_folder(name)
    return names


def test_a_synced_flow_is_owned_by_its_workspace_not_global(flow_spaces):
    from flow import store as flow_store
    from workspace.storage import get_workspace_metadata
    here, other, _ = flow_spaces
    fid = f"flow-{uuid.uuid4().hex[:8]}"
    ad = base.get("flow")
    assert ad.apply(here, fid, _flow_doc(), owner="local") == fid
    assert flow_store.get_flow(fid)["workspace"] == here
    assert fid in _flows_seen(here) and fid not in _flows_seen(other)
    # A workspace with no list keeps having none: a list would hide every
    # global flow it saw.
    assert get_workspace_metadata(here).get("allowed_flows") is None
    assert ad.list_ids(here, "local") == [fid] and ad.list_ids(other, "local") == []


def test_a_synced_flow_joins_the_list_of_a_workspace_that_has_one(flow_spaces):
    from flow import store as flow_store
    from workspace.storage import get_workspace_metadata, update_workspace_metadata
    here, other, _ = flow_spaces
    update_workspace_metadata(here, {"allowed_flows": []})
    fid = f"flow-{uuid.uuid4().hex[:8]}"
    base.get("flow").apply(here, fid, _flow_doc(), owner="local")
    assert get_workspace_metadata(here)["allowed_flows"] == [fid]
    assert flow_store.get_flow(fid)["workspace"] == here and fid not in _flows_seen(other)


def test_deleting_a_global_flow_only_unlists_it(flow_spaces):
    from flow import store as flow_store
    from workspace.storage import get_workspace_metadata, update_workspace_metadata
    here, other, _ = flow_spaces
    fid = f"flow-{uuid.uuid4().hex[:8]}"
    flow_store.save_flow({"id": fid, "workspace": None, "owner_user": "local", **_flow_doc("Global")})
    update_workspace_metadata(here, {"allowed_flows": [fid]})
    assert fid in _flows_seen(other)  # every workspace without a list sees it
    base.get("flow").delete(here, fid)
    assert flow_store.get_flow(fid) is not None and fid in _flows_seen(other)
    assert get_workspace_metadata(here)["allowed_flows"] == []


def test_deleting_a_flow_another_workspace_owns_leaves_it(flow_spaces):
    from flow import store as flow_store
    from workspace.storage import get_workspace_metadata, update_workspace_metadata
    here, other, _ = flow_spaces
    fid = f"flow-{uuid.uuid4().hex[:8]}"
    flow_store.save_flow({"id": fid, "workspace": other, "owner_user": "local", **_flow_doc("Theirs")})
    update_workspace_metadata(here, {"allowed_flows": [fid]})
    base.get("flow").delete(here, fid)
    assert flow_store.get_flow(fid)["workspace"] == other
    assert fid not in get_workspace_metadata(here)["allowed_flows"]


def test_a_flow_this_workspace_held_alone_is_deleted(flow_spaces):
    from flow import store as flow_store
    here, _, _ = flow_spaces
    fid = f"flow-{uuid.uuid4().hex[:8]}"
    ad = base.get("flow")
    ad.apply(here, fid, _flow_doc(), owner="local")
    ad.delete(here, fid)
    assert flow_store.get_flow(fid) is None


def test_a_flow_owned_here_and_listed_elsewhere_is_handed_over(flow_spaces):
    from flow import store as flow_store
    from workspace.storage import update_workspace_metadata
    here, other, _ = flow_spaces
    fid = f"flow-{uuid.uuid4().hex[:8]}"
    ad = base.get("flow")
    ad.apply(here, fid, _flow_doc(), owner="local")
    update_workspace_metadata(other, {"allowed_flows": [fid]})
    ad.delete(here, fid)
    assert flow_store.get_flow(fid)["workspace"] == other
    assert fid not in ad.list_ids(here, "local")


def test_a_sync_does_not_rewrite_a_flow_this_workspace_does_not_hold(flow_spaces):
    from flow import store as flow_store
    here, other, _ = flow_spaces
    ad = base.get("flow")
    global_id = f"flow-{uuid.uuid4().hex[:8]}"
    flow_store.save_flow({"id": global_id, "workspace": None, "owner_user": "local", **_flow_doc("Global")})
    theirs = f"flow-{uuid.uuid4().hex[:8]}"
    flow_store.save_flow({"id": theirs, "workspace": other, "owner_user": "local", **_flow_doc("Theirs")})
    for fid, name in ((global_id, "Global"), (theirs, "Theirs")):
        with pytest.raises(AdapterError):
            ad.apply(here, fid, _flow_doc("Overwritten"), owner="local")
        assert flow_store.get_flow(fid)["name"] == name


# ── #31: a key that exists, and replacing an unused one ──────────────────────

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
    """Both ends in one app, as in test_sync_http.py."""
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
    for name in ("laptop", "remote-enc"):
        create_workspace_folder(name)
    link_store.put({**link_store.blank(), "url": "http://remote.test", "api_key": "", "name": "remote"})
    return adapter


def test_the_vault_counts_records_per_key():
    vault.put_key("o1", key_id="k1", kdf=crypto.KDF, salt="c2FsdA==", iterations=100_000, verifier="v")
    vault.put_key("o1", key_id="k2", kdf=crypto.KDF, salt="c2FsdA==", iterations=100_000, verifier="v")
    vault.ensure_space("o1", "s1", encrypted=True, key_id="k1")
    vault.push("o1", "s1", [{"kind": "k", "record_id": r, "encrypted": True, "key_id": "k1", "payload": "x"}
                            for r in ("a", "b")])
    vault.push("o1", "s1", [{"kind": "k", "record_id": "b", "base_version": 1, "deleted": True}])
    assert {k["key_id"]: k["records"] for k in vault.list_keys("o1")} == {"k1": 1, "k2": 0}
    assert vault.list_keys("someone else") == []


def test_an_unused_key_answers_a_structured_409_and_is_replaced(hub, client):
    first = client.post("/api/hublink/sync/key", json={"generate": True})
    assert first.status_code == 200 and first.json()["generated_passphrase"]
    first_id = first.json()["key_id"]
    assert client.get("/api/sync/keys").json()["keys"][0]["records"] == 0

    again = client.post("/api/hublink/sync/key", json={"generate": True})
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert detail["code"] == "key_exists" and detail["replace_unused"] is True
    assert isinstance(detail["message"], str) and detail["message"]

    replaced = client.post("/api/hublink/sync/key", json={"generate": True, "replace_unused": True})
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["generated_passphrase"] and body["key_id"] != first_id
    assert [k["key_id"] for k in client.get("/api/sync/keys").json()["keys"]] == [body["key_id"]]
    # The new passphrase opens the new key, shown once and never again.
    assert client.get("/api/hublink/sync").json().get("generated_passphrase") is None
    sync_client.forget_key()
    assert sync_client.setup_key(passphrase=body["generated_passphrase"])["key_id"] == body["key_id"]


def test_a_key_that_sealed_records_is_not_offered_for_replacement(hub, client):
    made = sync_client.setup_key(generate=True)
    sync_client.configure(enabled=True, encrypted=True, mappings=[{"workspace": "laptop", "space": "remote-enc"}])
    hub.data[("laptop", "d1")] = {"body": "private"}
    sync_client.run_pass()
    assert client.get("/api/sync/keys").json()["keys"][0]["records"] == 1

    again = client.post("/api/hublink/sync/key", json={"generate": True})
    assert again.status_code == 409
    assert again.json()["detail"] == {"message": again.json()["detail"]["message"], "code": "key_exists",
                                      "replace_unused": False}
    refused = client.post("/api/hublink/sync/key", json={"generate": True, "replace_unused": True})
    assert refused.status_code == 409 and isinstance(refused.json()["detail"], str)
    assert [k["key_id"] for k in client.get("/api/sync/keys").json()["keys"]] == [made["key_id"]]
    wrong = client.post("/api/hublink/sync/key", json={"passphrase": "not the passphrase at all"})
    assert wrong.status_code in (403, 409) and isinstance(wrong.json()["detail"], str)


def test_a_key_count_the_remote_does_not_report_reads_as_used():
    assert sync_client._unused([{"key_id": "k", "records": 0}]) is True
    assert sync_client._unused([{"key_id": "k"}]) is False
    assert sync_client._unused([{"key_id": "k", "records": 0}, {"key_id": "j", "records": 2}]) is False
    assert sync_client._unused([{"key_id": "k", "records": False}]) is False
    assert sync_client._unused([]) is False


# ── the merge hook ───────────────────────────────────────────────────────────

class Merging(MemAdapter):
    def __init__(self):
        super().__init__()
        self.merged = 0

    def merge(self, local, incoming):
        self.merged += 1
        return {"notes": sorted(set(local.get("notes", [])) | set(incoming.get("notes", [])))}


def _pass(device, workspace, cursor=0):
    spec = engine.Spec(workspace=workspace, space="space", transport=engine.LocalTransport("u1", "space"),
                       state_owner=device, actor="u1", cursor=cursor)
    return engine.run(spec)


def _with_adapter(monkeypatch, adapter):
    monkeypatch.setattr(base, "_REGISTRY", {adapter.kind: adapter})
    monkeypatch.setattr(base, "_loaded", True)
    vault.ensure_space("u1", "space", encrypted=False, key_id=None)
    return adapter


def test_the_base_adapter_merges_nothing_and_replaces_nothing():
    plain = MemAdapter()
    assert plain.merge({"a": 1}, {"b": 2}) is None
    assert plain.replaced("ws", "old", "new", {}) is None


def test_the_engine_calls_the_merge_method_on_a_first_meeting(monkeypatch):
    ad = _with_adapter(monkeypatch, Merging())
    ad.data[("laptop", "pool")] = {"notes": ["tea"]}
    _pass("laptop", "laptop")
    ad.data[("phone", "pool")] = {"notes": ["coffee"]}
    r = _pass("phone", "phone")
    assert ad.merged == 1 and r.conflicts == 0
    assert ad.data[("phone", "pool")] == {"notes": ["coffee", "tea"]}


def test_an_adapter_without_merge_keeps_the_local_copy_as_a_conflict(monkeypatch):
    ad = _with_adapter(monkeypatch, MemAdapter())
    ad.data[("laptop", "x")] = {"body": "laptop's"}
    _pass("laptop", "laptop")
    ad.data[("phone", "x")] = {"body": "phone's"}
    r = _pass("phone", "phone")
    assert r.conflicts == 1 and ad.data[("phone", "x")] == {"body": "laptop's"}
    assert state.conflict_doc(state.conflicts("phone")[0]["conflict_id"])["lost_doc"] == {"body": "phone's"}


# ── linking while sync is on ─────────────────────────────────────────────────

@pytest.fixture
def loopback(client, monkeypatch):
    def transport(method, url, *, headers, body, timeout, stream):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        resp = client.request(method, path, headers=headers, content=body)
        return hub_remote.Response(resp.status_code, dict(resp.headers), content=resp.content)
    monkeypatch.setattr(hub_remote, "transport", transport)
    from hublink import runtime
    monkeypatch.setattr(runtime, "refresh", lambda: None)
    monkeypatch.setattr(runtime, "stop", lambda: None)
    monkeypatch.setattr(runtime, "hello_now", lambda: False)
    return client


def _link(client, mirror):
    # No agents: the MCP registration needs a default workspace this test
    # does not make, and it has nothing to do with the mirror.
    return client.put("/api/hublink", json={"url": "http://remote.test", "api_key": "ahk_x", "name": "laptop",
                                            "use_agents": False, "mirror": mirror})


def test_linking_while_sync_is_on_creates_no_mirror_connection(loopback):
    client = loopback
    link_store.put({**link_store.blank(), "url": "http://remote.test", "api_key": "ahk_x", "name": "laptop",
                    "sync": {"enabled": True, "mappings": []}})
    resp = _link(client, {"runs": True, "chats": True, "memory": True})
    assert resp.status_code == 200, resp.text
    assert not (link_store.get().get("connection") or {}).get("id")
    assert client.get("/api/connections").json()["connections"] == []
    assert not any((link_store.get().get("mirror") or {}).values())


def test_linking_with_sync_off_still_makes_the_mirror_connection(loopback):
    resp = _link(loopback, {"runs": True})
    assert resp.status_code == 200, resp.text
    assert link_store.get()["connection"]["id"] == "node-laptop"


# ── #6: an upload updated to bytes another held file has ─────────────────────

@pytest.fixture
def files_only(monkeypatch):
    from hubsync.adapters import file as file_adapter
    from workspace import create_workspace_folder
    for name in ("res-laptop", "res-phone"):
        create_workspace_folder(name)
    monkeypatch.setattr(base, "_REGISTRY", {"file": file_adapter.FileAdapter()})
    monkeypatch.setattr(base, "_loaded", True)
    vault.ensure_space("u1", "space", encrypted=False, key_id=None)
    return base.get("file")


def _phone_file(record_id):
    row = state.rows("phone", "space")[("file", record_id)]
    return row["local_id"]


def _update_in_vault(ad, record_id, data, *, name):
    """What the laptop pushes when it updates the upload ``record_id``."""
    import hashlib
    current = vault.get_record("u1", "space", "file", record_id)
    doc = {"name": name, "mime_type": "text/plain", "sha256": hashlib.sha256(data).hexdigest(),
           "size": len(data), "source": "upload", "path": None, "meta": {},
           "content_b64": base64.b64encode(data).decode("ascii")}
    payload = crypto.encode_record({"kind": "file", "id": record_id, "title": name, "data": doc}, kind="file",
                                   record_id=record_id)
    out = vault.push("u1", "space", [{"kind": "file", "record_id": record_id, "base_version": current["version"],
                                      "modified_at": "2030-01-01T00:00:00+00:00", "payload": payload}])
    assert out[0]["status"] == "ok"


def test_an_upload_updated_to_another_files_bytes_is_not_pushed_as_deleted(files_only):
    from files import service
    a = service.create_file("res-laptop", "a.txt", b"one", created_by="u1")
    b = service.create_file("res-laptop", "b.txt", b"two", created_by="u1")
    _pass("laptop", "res-laptop")
    r = _pass("phone", "res-phone")
    phone_a, phone_b = _phone_file(a["file_id"]), _phone_file(b["file_id"])
    assert service.read_bytes(phone_a) == b"one" and service.read_bytes(phone_b) == b"two"

    _update_in_vault(files_only, a["file_id"], b"two", name="a.txt")
    again = _pass("phone", "res-phone", cursor=r.cursor)
    assert again.conflicts == 1 and again.deleted_there == 0
    # The old upload is still there, as it was, and still carries its id.
    assert service.get_file(phone_a) is not None and service.read_bytes(phone_a) == b"one"
    assert _phone_file(a["file_id"]) == phone_a
    later = _pass("phone", "res-phone", cursor=again.cursor)
    assert later.deleted_there == 0
    for rid in (a["file_id"], b["file_id"]):
        assert vault.get_record("u1", "space", "file", rid)["deleted"] is False


def test_an_upload_updated_to_new_bytes_replaces_the_old_record(files_only):
    from files import service
    a = service.create_file("res-laptop", "a.txt", b"one", created_by="u1")
    _pass("laptop", "res-laptop")
    r = _pass("phone", "res-phone")
    old = _phone_file(a["file_id"])
    _update_in_vault(files_only, a["file_id"], b"three", name="a.txt")
    again = _pass("phone", "res-phone", cursor=r.cursor)
    new = _phone_file(a["file_id"])
    assert again.errors == [] and new != old
    assert service.get_file(old) is None and service.read_bytes(new) == b"three"
    assert files_only.list_ids("res-phone", "u1") == [new]
    assert _pass("phone", "res-phone", cursor=again.cursor).deleted_there == 0
