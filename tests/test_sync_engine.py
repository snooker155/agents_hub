"""The sync engine (hubsync/engine.py) against the real vault (hubsync/vault.py),
with an in-memory adapter so the decisions are tested apart from any store:
two devices of one person exchange through one space, edits travel both
ways without echo, deletions travel, conflicts keep the loser, encryption
keeps the vault blind, and a record that cannot be written here is neither
retried forever nor mistaken for a local deletion."""
from __future__ import annotations

import pytest

from hubsync import crypto, engine, state, vault
from hubsync.adapters import base


class MemAdapter(base.Adapter):
    kind = "memtest"
    order = 1

    def __init__(self):
        self.data = {}          # (workspace, id) -> doc
        self.refuse = set()

    def list_ids(self, workspace, owner):
        return sorted(i for (w, i) in self.data if w == workspace)

    def read(self, workspace, record_id):
        doc = self.data.get((workspace, record_id))
        return dict(doc) if doc is not None else None

    def apply(self, workspace, record_id, doc, *, owner):
        if record_id in self.refuse:
            raise base.AdapterError("id taken")
        self.data[(workspace, record_id)] = dict(doc)
        return record_id

    def delete(self, workspace, record_id):
        self.data.pop((workspace, record_id), None)


@pytest.fixture
def mem(monkeypatch):
    adapter = MemAdapter()
    monkeypatch.setattr(base, "_REGISTRY", {"memtest": adapter})
    monkeypatch.setattr(base, "_loaded", True)
    vault.ensure_space("u1", "space", encrypted=False, key_id=None)
    return adapter


def _pass(device, workspace, key=None, cursor=0):
    spec = engine.Spec(workspace=workspace, space="space", transport=engine.LocalTransport("u1", "space"),
                       state_owner=device, actor="u1", key=key, cursor=cursor)
    return engine.run(spec)


def test_a_record_travels_and_does_not_echo(mem):
    mem.data[("laptop", "n1")] = {"title": "first", "body": "hello", "updated_at": "2026-10-01T10:00:00"}
    r1 = _pass("laptop", "laptop")
    assert r1.pushed == 1
    r2 = _pass("phone", "phone")
    assert r2.applied == 1 and mem.data[("phone", "n1")]["body"] == "hello"
    # Nothing changed: neither side pushes again.
    assert _pass("laptop", "laptop", cursor=r1.cursor).pushed == 0
    assert _pass("phone", "phone", cursor=r2.cursor).pushed == 0


def test_edits_and_deletions_travel_both_ways(mem):
    mem.data[("laptop", "n1")] = {"body": "v1", "updated_at": "2026-10-01T10:00:00"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    mem.data[("phone", "n1")] = {"body": "v2 from phone", "updated_at": "2026-10-01T11:00:00"}
    c_phone = _pass("phone", "phone", cursor=c_phone).cursor
    r = _pass("laptop", "laptop", cursor=c_laptop)
    assert r.applied == 1 and mem.data[("laptop", "n1")]["body"] == "v2 from phone"
    c_laptop = r.cursor
    del mem.data[("laptop", "n1")]
    assert _pass("laptop", "laptop", cursor=c_laptop).deleted_there == 1
    assert _pass("phone", "phone", cursor=c_phone).deleted_here == 1
    assert ("phone", "n1") not in mem.data


def test_a_conflict_keeps_the_newer_side_and_the_loser(mem):
    mem.data[("laptop", "n1")] = {"body": "base", "updated_at": "2026-10-01T10:00:00"}
    c_laptop = _pass("laptop", "laptop").cursor
    c_phone = _pass("phone", "phone").cursor
    mem.data[("laptop", "n1")] = {"body": "laptop edit", "updated_at": "2026-10-01T12:00:00"}
    mem.data[("phone", "n1")] = {"body": "phone edit", "updated_at": "2026-10-01T11:00:00"}
    _pass("phone", "phone", cursor=c_phone)          # phone pushes first
    r = _pass("laptop", "laptop", cursor=c_laptop)   # laptop is newer: keeps its edit
    assert mem.data[("laptop", "n1")]["body"] == "laptop edit"
    assert r.conflicts == 1
    lost = state.conflicts("laptop")
    assert lost and lost[0]["kept"] == "local"
    assert state.conflict_doc(lost[0]["conflict_id"])["lost_doc"]["body"] == "phone edit"
    # The next laptop pass pushes its edit on top; the phone then takes it.
    _pass("laptop", "laptop", cursor=r.cursor)
    _pass("phone", "phone")
    assert mem.data[("phone", "n1")]["body"] == "laptop edit"


def test_encrypted_space_keeps_the_vault_blind(mem):
    salt = crypto.new_salt()
    key = crypto.derive_key("correct horse", salt, 1000)
    vault.delete_space("u1", "space")
    vault.ensure_space("u1", "space", encrypted=True, key_id=crypto.key_id(key))
    mem.data[("laptop", "n1")] = {"title": "Diary", "body": "secret text"}
    _pass("laptop", "laptop", key=key)
    stored = vault.get_record("u1", "space", "memtest", "n1")
    assert stored["encrypted"] and "secret" not in stored["payload"] and "Diary" not in stored["payload"]
    _pass("phone", "phone", key=key)
    assert mem.data[("phone", "n1")]["body"] == "secret text"
    wrong = crypto.derive_key("wrong", salt, 1000)
    r = _pass("tablet", "tablet", key=wrong)
    assert r.errors and ("tablet", "n1") not in mem.data
    # A plain record cannot land in an encrypted space.
    with pytest.raises(vault.VaultError):
        vault.push("u1", "space", [{"kind": "memtest", "record_id": "x", "payload": "e30=", "encrypted": False}])


def test_a_refused_record_is_not_retried_or_deleted(mem):
    mem.data[("laptop", "agent-x")] = {"body": "mine"}
    _pass("laptop", "laptop")
    mem.refuse.add("agent-x")
    r = _pass("phone", "phone")
    assert r.errors and ("phone", "agent-x") not in mem.data
    again = _pass("phone", "phone", cursor=r.cursor)
    assert again.pushed == 0 and again.deleted_there == 0
    assert vault.get_record("u1", "space", "memtest", "agent-x")["deleted"] is False


def test_a_record_refused_for_a_missing_dependency_lands_on_the_retry(mem):
    mem.data[("laptop", "child")] = {"extends": "parent"}
    mem.data[("laptop", "parent")] = {"base": True}
    _pass("laptop", "laptop")
    real_apply = mem.apply

    def needs_parent(workspace, record_id, doc, *, owner):
        if doc.get("extends") and (workspace, doc["extends"]) not in mem.data:
            raise base.AdapterError("parent missing")
        return real_apply(workspace, record_id, doc, owner=owner)

    mem.apply = needs_parent
    r = _pass("phone", "phone")
    assert ("phone", "child") in mem.data and ("phone", "parent") in mem.data
    assert r.errors == []
