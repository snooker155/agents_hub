"""Regression tests for the review of the sync engine (hubsync/), one or more
per finding, each named after what it guards:

* the pass actor is fixed at setup, so "Sync now" and the background pass
  see the same records (#2, #17);
* sync turns the whole one-way mirror off and the link store keeps it off
  (#1, #19);
* a synced watcher cannot reach the operator's Google account past the
  admin gate, and keeps its approval stamp (#3);
* a second device's empty personal pool merges instead of wiping the first
  device's notes (#4);
* a kind that cannot be listed pushes no deletions (#5);
* two vault records never map to one local record (#6);
* one record that fails while pulling does not stall the space (#7);
* an encrypted space refuses plain records, other keys, and envelopes from
  another space; only version 2 envelopes open (#14);
* a refused apply keeps the last good exchange, so local edits still travel
  and later overwrites keep them as conflicts (#16);
* a flow deleted in one synced workspace stays in the others (#18);
* non-administrators see a trimmed sync status (#32);
* kinds with no time of their own are timed by when this end saw the edit,
  offline too (#33);
* an unused remote key can be replaced, a used one cannot (#31).
"""
from __future__ import annotations

import base64
import gzip
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common import identity
from common.config import settings
from hublink import mirror as hub_mirror
from hublink import remote as hub_remote
from hublink import store as link_store
from hubsync import client as sync_client
from hubsync import crypto, engine, state, vault
from hubsync.adapters import base

PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def _drop_new_workspaces():
    """Remove the workspace folders this test made. They live on disk under
    the suite's one state root and outlive the per-test database, so a
    leftover "team" turned a later test's "create it when missing" path
    (test_setup_link.py) into a silent skip."""
    from workspace import storage
    # "default" is made once per process and every later test expects it.
    before = {p.name for p in storage.list_workspace_folders()} | {"default"}
    yield
    for path in storage.list_workspace_folders():
        if path.name not in before:
            storage.delete_workspace_folder(path.name)


class MemAdapter(base.Adapter):
    """Records keyed by (workspace, id); ``owner`` filters when a record names one."""
    kind = "memtest"
    order = 1

    def __init__(self):
        self.data = {}
        self.refuse = set()
        self.refuse_delete = set()
        self.list_fails = 0
        self.dedupe = False
        self.owners_seen = []

    def list_ids(self, workspace, owner):
        self.owners_seen.append(owner)
        if self.list_fails:
            self.list_fails -= 1
            raise RuntimeError("store down")
        return sorted(i for (w, i), d in self.data.items()
                      if w == workspace and d.get("owner", owner) == owner)

    def read(self, workspace, record_id):
        doc = self.data.get((workspace, record_id))
        return dict(doc) if doc is not None else None

    def apply(self, workspace, record_id, doc, *, owner):
        if record_id in self.refuse:
            raise base.AdapterError("refused here")
        if self.dedupe:
            for (w, i), d in self.data.items():
                if w == workspace and i != record_id and d.get("body") == doc.get("body"):
                    return i  # a store that dedupes equal content, as files do
        self.data[(workspace, record_id)] = dict(doc)
        return record_id

    def delete(self, workspace, record_id):
        if record_id in self.refuse_delete:
            raise base.AdapterError("other agents extend it")
        self.data.pop((workspace, record_id), None)


@pytest.fixture
def mem(monkeypatch):
    adapter = MemAdapter()
    monkeypatch.setattr(base, "_REGISTRY", {"memtest": adapter})
    monkeypatch.setattr(base, "_loaded", True)
    vault.ensure_space("u1", "space", encrypted=False, key_id=None)
    return adapter


def _pass(device, workspace, *, key=None, cursor=0, space="space", transport=None):
    spec = engine.Spec(workspace=workspace, space=space,
                       transport=transport or engine.LocalTransport("u1", space),
                       state_owner=device, actor="u1", key=key, cursor=cursor)
    return engine.run(spec)


# ── the HTTP fixture: both ends in one app, as in test_sync_http.py ──────────

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


# ── #2, #17: the actor is fixed at setup ─────────────────────────────────────

def test_sync_now_and_the_background_pass_sync_the_same_records(hub):
    token = identity.set_current_user("alice")
    try:
        # The administrator sets sync up from a request, then clicks Sync now.
        sync_client.configure(enabled=True, encrypted=False,
                              mappings=[{"workspace": "laptop", "space": "remote-ws"}])
        hub.data[("laptop", "c1")] = {"owner": "alice", "body": "alice's chat"}
        assert sync_client.run_pass()["pushed"] == 1
    finally:
        identity.reset_current_user(token)
    # The background pass has no request at all.
    out = sync_client.run_pass()
    assert out["errors"] == []
    assert vault.get_record("local", "remote-ws", "memtest", "c1")["deleted"] is False
    assert set(hub.owners_seen) == {"alice"}
    assert sync_client.config()["actor"] == "alice"


def test_a_later_save_by_another_admin_keeps_the_actor(hub):
    token = identity.set_current_user("alice")
    try:
        sync_client.configure(enabled=True, encrypted=False,
                              mappings=[{"workspace": "laptop", "space": "remote-ws"}])
    finally:
        identity.reset_current_user(token)
    token = identity.set_current_user("bob")
    try:
        sync_client.configure(enabled=True, encrypted=False,
                              mappings=[{"workspace": "laptop", "space": "remote-ws"}])
    finally:
        identity.reset_current_user(token)
    assert sync_client.config()["actor"] == "alice"


# ── #1, #19: the mirror goes off as a whole and stays off ────────────────────

def test_turning_sync_on_turns_the_run_mirror_off_and_keeps_it_off(hub):
    assert link_store.mirror_enabled("runs")  # the link's default
    hub_mirror.enqueue("run", "queued-before")
    sync_client.configure(enabled=True, encrypted=False, mappings=[{"workspace": "laptop", "space": "remote-ws"}])
    doc = link_store.get()
    assert not any(doc["mirror"].values())
    assert hub_mirror.outbox_stats()["pending"] == 0
    for kind in link_store.MIRROR_KINDS:
        assert not link_store.mirror_enabled(kind)
    # Saving the Remote hub form posts the switches it loaded: they stay off.
    link_store.update(mirror={"runs": True, "chats": True, "memory": True})
    assert not any(link_store.get()["mirror"].values())
    link_store.put({**link_store.get(), "mirror": {"runs": True, "chats": True, "memory": False}})
    assert not any(link_store.get()["mirror"].values())
    hub_mirror.on_run_finished({"status": "running"}, {"status": "completed", "run_id": "r1"})
    hub_mirror.on_chat_saved("chat-1")
    assert hub_mirror.outbox_stats()["pending"] == 0


def test_a_run_queued_while_sync_is_on_is_dropped_not_sent(hub, monkeypatch):
    sync_client.configure(enabled=True, encrypted=False, mappings=[{"workspace": "laptop", "space": "remote-ws"}])
    # A raw switch left on by an older record still sends nothing.
    link_store._store.put(link_store.KEY, {**link_store.get(), "mirror": {"runs": True}})
    hub_mirror.enqueue("run", "r1")
    sent = []
    monkeypatch.setitem(hub_mirror._SENDERS, "run", lambda doc, ids: sent.extend(ids) or {})
    out = hub_mirror.drain(force=True)
    assert sent == [] and out["skipped"] == 1


# ── #3: watchers and the operator's Google account ───────────────────────────

def test_a_synced_google_watcher_from_a_non_admin_is_refused(monkeypatch):
    from watchers.store import store
    from workspace.storage import create_workspace_folder
    from hubsync.adapters.base import AdapterError
    create_workspace_folder("ws-w")
    monkeypatch.setattr(identity, "current_mode", lambda: "multi")
    member = identity.create_user("member1", PASSWORD, role="member")["id"]
    doc = {"name": "Inbox", "kind": "imap", "interval_seconds": 300, "auto_pause_after": 5,
           "config": {"use_google": True, "username": ""}}
    ad = base.get("watcher")
    with pytest.raises(AdapterError):
        ad.apply("ws-w", "w-google", doc, owner=member)
    assert store.get("w-google") is None
    # A plain password watcher from the same person lands.
    plain = {**doc, "config": {"host": "imap.example.com", "username": "me", "password_secret": "PW"}}
    assert ad.apply("ws-w", "w-plain", plain, owner=member) == "w-plain"


def test_a_config_change_keeps_the_google_approval_stamp():
    from watchers import kinds, service
    from watchers.store import store
    from workspace.storage import create_workspace_folder
    create_workspace_folder("ws-w")
    w = service.create("ws-w", {"kind": "imap", "name": "Inbox",
                                "config": {"use_google": True, "username": "", "folder": "INBOX"}})
    store.update(w.id, state={kinds.GOOGLE_FROM: "default", "uid": 7})
    ad = base.get("watcher")
    doc = ad.read("ws-w", w.id)
    doc["config"] = {**doc["config"], "folder": "Archive"}
    ad.apply("ws-w", w.id, doc, owner="local")  # the local operator: every role
    after = store.get(w.id)
    assert after.state.get(kinds.GOOGLE_FROM) == kinds.google_source("ws-w")
    assert "uid" not in after.state


# ── #4: a second device's empty personal pool ────────────────────────────────

@pytest.fixture
def memory_only(monkeypatch):
    from hubsync.adapters import memory as memory_adapter
    monkeypatch.setattr(base, "_REGISTRY", {"memory": memory_adapter.MemoryAdapter()})
    monkeypatch.setattr(base, "_loaded", True)
    vault.ensure_space("u1", "space", encrypted=False, key_id=None)


def _mem_pass(device, workspace, cursor=0):
    spec = engine.Spec(workspace=workspace, space="space", transport=engine.LocalTransport("u1", "space"),
                       state_owner=device, actor="local", cursor=cursor)
    return engine.run(spec)


def test_a_new_devices_empty_pool_does_not_wipe_the_first_devices_notes(memory_only):
    from memory import personal
    from memory.store import MemoryStore
    pool = personal.ensure_pool("local", "laptop")
    pool.notes.append({"id": "n1", "title": "t", "content": "likes tea", "created_at": "2026-01-01"})
    MemoryStore().add(pool)
    laptop = _mem_pass("laptop", "laptop")
    assert laptop.pushed == 1
    personal.ensure_pool("local", "phone")  # opened once on the phone, empty
    _mem_pass("phone", "phone")
    _mem_pass("laptop", "laptop", cursor=laptop.cursor)
    laptop_notes = MemoryStore().get(personal.pool_id("local", "laptop")).notes
    phone_notes = MemoryStore().get(personal.pool_id("local", "phone")).notes
    assert [n["content"] for n in laptop_notes] == ["likes tea"]
    assert [n["content"] for n in phone_notes] == ["likes tea"]


def test_two_devices_notes_meet_as_a_merge(memory_only):
    from memory import personal
    from memory.store import MemoryStore
    a = personal.ensure_pool("local", "laptop")
    a.notes.append({"id": "n1", "content": "from the laptop"})
    MemoryStore().add(a)
    laptop = _mem_pass("laptop", "laptop")
    b = personal.ensure_pool("local", "phone")
    b.notes.append({"id": "n2", "content": "from the phone"})
    MemoryStore().add(b)
    _mem_pass("phone", "phone")
    _mem_pass("laptop", "laptop", cursor=laptop.cursor)
    for ws in ("laptop", "phone"):
        notes = MemoryStore().get(personal.pool_id("local", ws)).notes
        assert sorted(n["content"] for n in notes) == ["from the laptop", "from the phone"]


def test_an_unmergeable_first_meeting_keeps_the_travelling_version_and_the_local_one(mem):
    mem.data[("laptop", "x")] = {"body": "laptop's", "updated_at": "2026-01-01T00:00:00"}
    _pass("laptop", "laptop")
    # The phone's copy is newer by time, but was never exchanged.
    mem.data[("phone", "x")] = {"body": "phone's", "updated_at": "2026-10-01T00:00:00"}
    r = _pass("phone", "phone")
    assert mem.data[("phone", "x")]["body"] == "laptop's"
    lost = state.conflicts("phone")
    assert r.conflicts == 1 and lost[0]["kept"] == "remote"
    assert state.conflict_doc(lost[0]["conflict_id"])["lost_doc"]["body"] == "phone's"


# ── #5: a failed listing ─────────────────────────────────────────────────────

def test_a_failed_listing_pushes_no_deletions(mem):
    mem.data[("laptop", "n1")] = {"body": "keep me"}
    c = _pass("laptop", "laptop").cursor
    mem.list_fails = 1
    r = _pass("laptop", "laptop", cursor=c)
    assert r.errors and r.deleted_there == 0
    assert vault.get_record("u1", "space", "memtest", "n1")["deleted"] is False


# ── #6: one local record, one sync id ────────────────────────────────────────

def test_two_records_on_one_local_record_do_not_push_a_deletion(mem):
    mem.data[("laptop", "p1")] = {"body": "same bytes"}
    mem.data[("laptop", "u1")] = {"body": "same bytes"}
    c_laptop = _pass("laptop", "laptop").cursor
    mem.dedupe = True
    r = _pass("phone", "phone")
    assert r.conflicts == 1
    again = _pass("phone", "phone", cursor=r.cursor)
    assert again.deleted_there == 0
    for rid in ("p1", "u1"):
        assert vault.get_record("u1", "space", "memtest", rid)["deleted"] is False
    _pass("laptop", "laptop", cursor=c_laptop)
    assert ("laptop", "p1") in mem.data and ("laptop", "u1") in mem.data


def test_records_for_local_sees_every_mapping():
    state.put("dev", "space", "memtest", "b", local_id="x", hash_="h", version=1)
    state.put("dev", "space", "memtest", "x", local_id="x", hash_="h", version=1)
    assert state.records_for_local("dev", "space", "memtest", "x") == ["x", "b"]
    assert state.record_for_local("dev", "space", "memtest", "x") == "x"


# ── #7: one failing record does not stall the space ──────────────────────────

def test_a_refused_delete_does_not_stall_the_space(mem):
    mem.data[("laptop", "base")] = {"body": "base"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    del mem.data[("laptop", "base")]
    _pass("laptop", "laptop", cursor=c_laptop)
    mem.refuse_delete.add("base")
    mem.data[("phone", "new")] = {"body": "made on the phone"}
    r = _pass("phone", "phone", cursor=c_phone)  # used to raise here, every pass
    assert r.errors and r.pushed == 1 and r.cursor > c_phone
    assert ("phone", "base") in mem.data
    again = _pass("phone", "phone", cursor=r.cursor)
    assert again.pushed == 0 and again.errors == []


def test_an_error_reading_a_record_while_pulling_is_reported(mem, monkeypatch):
    mem.data[("laptop", "n1")] = {"body": "one"}
    mem.data[("laptop", "n2")] = {"body": "two"}
    _pass("laptop", "laptop")
    real_read = mem.read

    def broken(workspace, record_id):
        if workspace == "phone" and record_id == "n1":
            raise OSError("disk hiccup")
        return real_read(workspace, record_id)
    monkeypatch.setattr(mem, "read", broken)
    r = _pass("phone", "phone")
    assert any("n1" in e for e in r.errors)
    assert mem.data[("phone", "n2")]["body"] == "two"


# ── #14: what an encrypted space accepts ─────────────────────────────────────

class FeedTransport(engine.Transport):
    """A remote that serves what the test hands it."""

    def __init__(self, records):
        self.records = records

    def changes(self, since):
        return {"records": self.records if not since else [], "next": 1, "more": False}

    def push(self, records):
        return [{"status": "ok", "version": 1} for _ in records]


def _key():
    return crypto.derive_key("correct horse battery", crypto.new_salt(), 1000)


def _plain(doc):
    return base64.b64encode(gzip.compress(json.dumps({"data": doc}).encode())).decode()


def test_an_encrypted_space_refuses_a_plain_record_from_the_server(mem):
    key = _key()
    feed = FeedTransport([{"kind": "memtest", "record_id": "evil", "version": 1, "encrypted": False,
                           "payload": _plain({"body": "injected instructions"})}])
    r = _pass("laptop", "laptop", key=key, transport=feed)
    assert r.errors and ("laptop", "evil") not in mem.data


def _sealed_v1(key, doc, kind, record_id):
    nonce = os.urandom(12)
    packed = gzip.compress(json.dumps(doc).encode())
    sealed = AESGCM(key).encrypt(nonce, packed, f"agents-hub/v1|{kind}|{record_id}".encode())
    return json.dumps({"v": 1, "alg": crypto.ALG, "kid": crypto.key_id(key),
                       "n": base64.b64encode(nonce).decode(), "c": base64.b64encode(sealed).decode()})


def test_an_encrypted_space_refuses_a_record_under_another_key(mem):
    key = _key()
    payload = crypto.encode_record({"data": {"body": "x"}}, kind="memtest", record_id="r", key=key,
                                   space="space")
    feed = FeedTransport([{"kind": "memtest", "record_id": "r", "version": 1, "encrypted": True,
                           "key_id": "someone-elses", "payload": payload}])
    r = _pass("laptop", "laptop", key=key, transport=feed)
    assert r.errors and ("laptop", "r") not in mem.data


def test_an_envelope_cannot_move_to_another_space(mem):
    key = _key()
    payload = crypto.encode_record({"data": {"body": "from work"}}, kind="memtest", record_id="r", key=key,
                                   space="work")
    feed = FeedTransport([{"kind": "memtest", "record_id": "r", "version": 1, "encrypted": True,
                           "key_id": crypto.key_id(key), "payload": payload}])
    r = _pass("laptop", "laptop", key=key, transport=feed, space="personal")
    assert r.errors and ("laptop", "r") not in mem.data
    ok = _pass("tablet", "tablet", key=key, transport=FeedTransport(feed.records), space="work")
    assert ok.errors == [] and mem.data[("tablet", "r")]["body"] == "from work"


def test_a_version_1_envelope_is_refused():
    """hubsync was never released, so no version 1 data exists, and a v1
    envelope binds no space: opening one would let a record move between
    spaces sealed with the same key."""
    key = _key()
    payload = _sealed_v1(key, {"data": {"body": "old"}}, "memtest", "r")
    with pytest.raises(crypto.CryptoError):
        crypto.decode_record(payload, kind="memtest", record_id="r", encrypted=True, key=key, space="any")
    assert json.loads(crypto.encode_record({}, kind="k", record_id="r", key=key, space="s"))["v"] == 2


# ── #16: a refused apply keeps the last good exchange ────────────────────────

def test_an_edit_after_a_refused_apply_still_travels(mem):
    mem.data[("laptop", "x")] = {"body": "v1", "updated_at": "2026-10-01T10:00:00"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    mem.data[("laptop", "x")] = {"body": "v2", "updated_at": "2026-10-01T11:00:00"}
    c_laptop = _pass("laptop", "laptop", cursor=c_laptop).cursor
    mem.refuse.add("x")
    r = _pass("phone", "phone", cursor=c_phone)
    assert r.errors and mem.data[("phone", "x")]["body"] == "v1"
    c_phone = r.cursor
    mem.refuse.clear()
    mem.data[("phone", "x")] = {"body": "phone edit", "updated_at": "2026-10-01T12:00:00"}
    for _ in range(2):
        c_phone = _pass("phone", "phone", cursor=c_phone).cursor
    _pass("laptop", "laptop", cursor=c_laptop)
    assert mem.data[("laptop", "x")]["body"] == "phone edit"


def test_a_newer_version_after_a_refusal_keeps_the_local_edit_as_the_loser(mem):
    mem.data[("laptop", "x")] = {"body": "v1", "updated_at": "2026-10-01T10:00:00"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    mem.data[("laptop", "x")] = {"body": "v2", "updated_at": "2026-10-01T11:00:00"}
    c_laptop = _pass("laptop", "laptop", cursor=c_laptop).cursor
    mem.refuse.add("x")
    c_phone = _pass("phone", "phone", cursor=c_phone).cursor
    mem.refuse.clear()
    mem.data[("phone", "x")] = {"body": "phone edit", "updated_at": "2026-10-01T12:00:00"}
    # The laptop moves on before the phone's next pass.
    mem.data[("laptop", "x")] = {"body": "v3", "updated_at": "2026-10-01T13:00:00"}
    _pass("laptop", "laptop", cursor=c_laptop)
    r = _pass("phone", "phone", cursor=c_phone)
    assert mem.data[("phone", "x")]["body"] == "v3"
    lost = [state.conflict_doc(c["conflict_id"])["lost_doc"] for c in state.conflicts("phone")]
    assert r.conflicts >= 1 and {"body": "phone edit", "updated_at": "2026-10-01T12:00:00"} in lost
    assert all((d or {}).get("body") != "v1" for d in lost)


# ── #18: flows are shared between workspaces ─────────────────────────────────

def test_a_flow_deleted_in_one_synced_workspace_stays_in_the_others():
    from flow import store as flow_store
    from workspace.storage import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
    for ws in ("w1", "w2"):
        create_workspace_folder(ws)
    # Owned by w1: a global flow is never deleted by a sync, since every
    # workspace without a list sees it (test_sync_residuals.py).
    flow_store.save_flow({"id": "shared-flow", "name": "Shared", "workspace": "w1", "nodes": [], "edges": []})
    update_workspace_metadata("w1", {"allowed_flows": ["shared-flow"]})
    update_workspace_metadata("w2", {"allowed_flows": ["shared-flow"]})
    base.get("flow").delete("w1", "shared-flow")
    assert flow_store.get_flow("shared-flow") is not None
    assert "shared-flow" not in get_workspace_metadata("w1")["allowed_flows"]
    assert get_workspace_metadata("w2")["allowed_flows"] == ["shared-flow"]
    base.get("flow").delete("w2", "shared-flow")
    assert flow_store.get_flow("shared-flow") is None


def test_a_flow_owned_here_and_listed_elsewhere_moves_to_that_workspace():
    from flow import store as flow_store
    from workspace.storage import create_workspace_folder, update_workspace_metadata
    for ws in ("w1", "w2"):
        create_workspace_folder(ws)
    flow_store.save_flow({"id": "owned", "name": "Owned", "workspace": "w1", "nodes": [], "edges": []})
    update_workspace_metadata("w2", {"allowed_flows": ["owned"]})
    base.get("flow").delete("w1", "owned")
    assert flow_store.get_flow("owned")["workspace"] == "w2"
    assert "owned" not in base.get("flow").list_ids("w1", "local")


# ── #32: what a non-administrator sees ───────────────────────────────────────

def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_member_sees_only_their_workspaces_and_no_error_text(client, monkeypatch):
    from hubsync import state as sync_state
    from workspace.storage import create_workspace_folder
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)
    for ws in ("team", "secret-ws"):
        create_workspace_folder(ws)
    admin = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert admin.status_code == 200, admin.text
    admin_headers = _bearer(admin.json()["token"])
    bob = identity.create_user("bob", PASSWORD, role="member")["id"]
    identity.set_member("team", bob, "editor")
    bob_headers = _bearer(client.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
                          .json()["token"])
    link_store.put({**link_store.blank(), "url": "http://remote.test", "api_key": "k", "name": "remote",
                    "sync": {"enabled": True, "encrypted": False, "actor": "root",
                             "mappings": [{"workspace": "team", "space": "team"},
                                          {"workspace": "secret-ws", "space": "secret-ws"}],
                             "last_result": {"pushed": 2, "errors": ["memtest r-secret: boom in secret-ws"]},
                             "last_error": "secret-ws: boom"}})
    sync_state.add_conflict("", "secret-ws", "memtest", "r-secret", kept="local", lost_doc=None)
    sync_state.add_conflict("", "team", "memtest", "r-team", kept="local", lost_doc=None)

    seen = client.get("/api/hublink/sync", headers=bob_headers).json()
    assert "secret-ws" not in json.dumps(seen) and "boom" not in json.dumps(seen)
    assert [m["workspace"] for m in seen["mappings"]] == ["team"]
    assert seen["has_error"] is True and seen["last_error"] == "" and seen["conflicts"] == 1
    assert seen["last_result"] == {"pushed": 2, "error_count": 1}
    rows = client.get("/api/hublink/sync/conflicts", headers=bob_headers).json()["conflicts"]
    assert [c["space"] for c in rows] == ["team"]

    full = client.get("/api/hublink/sync", headers=admin_headers).json()
    assert len(full["mappings"]) == 2 and full["last_error"] == "secret-ws: boom"
    assert len(client.get("/api/hublink/sync/conflicts", headers=admin_headers).json()["conflicts"]) == 2


# ── #33: kinds with no time of their own ─────────────────────────────────────

def test_a_later_local_edit_of_a_timeless_kind_wins_over_an_earlier_remote_one(mem):
    mem.data[("laptop", "a")] = {"body": "base"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    mem.data[("phone", "a")] = {"body": "phone, Sunday"}
    _pass("phone", "phone", cursor=c_phone)
    mem.data[("laptop", "a")] = {"body": "laptop, Monday"}
    r = _pass("laptop", "laptop", cursor=c_laptop)
    assert mem.data[("laptop", "a")]["body"] == "laptop, Monday"
    assert r.conflicts == 1


class Offline(engine.Transport):
    def changes(self, since):
        raise ConnectionError("the remote is out of reach")

    def push(self, records):
        raise ConnectionError("the remote is out of reach")


def test_an_edit_made_offline_is_timed_when_it_was_made(mem):
    mem.data[("laptop", "a")] = {"body": "base"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    mem.data[("laptop", "a")] = {"body": "laptop, offline Monday"}
    with pytest.raises(ConnectionError):
        _pass("laptop", "laptop", cursor=c_laptop, transport=Offline())
    mem.data[("phone", "a")] = {"body": "phone, Tuesday"}
    _pass("phone", "phone", cursor=c_phone)
    _pass("laptop", "laptop", cursor=c_laptop)  # back online on Wednesday
    assert mem.data[("laptop", "a")]["body"] == "phone, Tuesday"
    lost = state.conflicts("laptop")
    assert state.conflict_doc(lost[0]["conflict_id"])["lost_doc"]["body"] == "laptop, offline Monday"


# ── #31: replacing an unused key ─────────────────────────────────────────────

def test_an_unused_remote_key_can_be_replaced_on_explicit_ask(hub, client):
    first = sync_client.setup_key(generate=True)
    with pytest.raises(sync_client.SyncConfigError) as refused:
        sync_client.setup_key(generate=True)
    assert refused.value.status == 409
    second = sync_client.setup_key(generate=True, replace_unused=True)
    assert second["key_id"] != first["key_id"] and second["generated_passphrase"]
    keys = client.get("/api/sync/keys").json()["keys"]
    assert [k["key_id"] for k in keys] == [second["key_id"]]
    assert sync_client.config()["key"]["key_id"] == second["key_id"]


def test_a_key_that_sealed_records_cannot_be_replaced(hub, client):
    made = sync_client.setup_key(generate=True)
    sync_client.configure(enabled=True, encrypted=True, mappings=[{"workspace": "laptop", "space": "remote-enc"}])
    hub.data[("laptop", "d1")] = {"body": "private"}
    sync_client.run_pass()
    with pytest.raises(sync_client.SyncConfigError) as refused:
        sync_client.setup_key(generate=True, replace_unused=True)
    assert refused.value.status == 409
    assert client.delete(f"/api/sync/keys/{made['key_id']}").status_code == 409
    assert [k["key_id"] for k in client.get("/api/sync/keys").json()["keys"]] == [made["key_id"]]
    assert client.delete("/api/sync/keys/nope").status_code == 404


def test_deleting_a_key_drops_its_empty_encrypted_spaces():
    vault.put_key("o1", key_id="k1", kdf=crypto.KDF, salt="c2FsdA==", iterations=100_000, verifier="v")
    vault.ensure_space("o1", "empty-enc", encrypted=True, key_id="k1")
    assert vault.delete_key("o1", "k1") is True
    assert vault.get_key("o1", "k1") is None and vault.get_space("o1", "empty-enc") is None
