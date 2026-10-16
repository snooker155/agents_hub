"""``hub_lookup`` for the Vault and Catalog pages (chat/lookup_kinds/catalog.py):
the kinds ``synced`` and ``catalog_install``.

What is promised: ``synced`` lists the caller's own sync spaces on this hub
(never another person's, never one in a workspace the lookup does not read)
with their counts, and this hub's own sync status when it is linked;
``catalog_install`` lists what was installed from the linked hub, from the
provenance store alone. Neither ever returns a sync payload, the sync key,
its id, the link's API key or the remote's error text, and neither calls the
remote.
"""
from __future__ import annotations

import base64
import json

import pytest

from chat import lookup
from common import identity
from hublink import catalog as hub_catalog
from hublink import remote as hub_remote
from hublink import store as link_store
from hubsync import vault

PASSWORD = "hunter2-but-longer"
API_KEY = "ahk_LINKED_HUB_SECRET_KEY"
PAYLOAD_TEXT = "MY PRIVATE DIARY ENTRY"
PAYLOAD = base64.b64encode(json.dumps({"title": PAYLOAD_TEXT}).encode()).decode()
SEALED = "gAAAA_SEALED_SYNC_KEY"
KEY_ID = "kid-0123456789abcdef"
REMOTE_ERROR = "remote said: token ahk_LINKED_HUB_SECRET_KEY rejected"


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


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def hub(monkeypatch):
    from workspace import create_workspace_folder
    for ws in ("default", "team", "other"):
        create_workspace_folder(ws)

    def no_network(*args, **kwargs):
        raise AssertionError("a lookup must not call the linked hub")
    monkeypatch.setattr(hub_remote, "transport", no_network)


@pytest.fixture
def linked(hub):
    link_store.put({
        **link_store.blank(), "url": "https://user:pw@remote.test", "api_key": API_KEY, "name": "office",
        "catalog": True,
        "sync": {"enabled": True, "encrypted": True,
                 "key": {"key_id": KEY_ID, "salt": "c2FsdA==", "iterations": 1000, "sealed": SEALED},
                 "mappings": [{"workspace": "team", "space": "team"},
                              {"workspace": "other", "space": "other"}],
                 "last_pass_at": "2026-10-15T09:00:00+00:00",
                 "last_result": {"pushed": 3, "applied": 1, "note": "free text"},
                 "last_error": REMOTE_ERROR},
    })
    yield
    for rec in hub_catalog.installed():
        hub_catalog.forget(rec["kind"], rec["local_id"])


def _space(owner, name, *, encrypted=False, records=2):
    vault.ensure_space(owner, name, encrypted=encrypted, key_id=KEY_ID if encrypted else None)
    vault.push(owner, name, [
        {"kind": "memory", "record_id": f"r{i}", "payload": PAYLOAD, "encrypted": encrypted,
         "key_id": KEY_ID if encrypted else None}
        for i in range(records)
    ])


def _install(kind, local_id, workspace, remote_id="x"):
    hub_catalog._installs.put(f"{kind}:{local_id}", {
        "kind": kind, "local_id": local_id, "workspace": workspace,
        "remote_url": link_store.get()["url"], "remote_id": remote_id,
        "remote_hash": "rh", "local_hash": "lh", "deps": {"agents": {remote_id: local_id}},
        "installed_at": "2026-10-14T08:00:00+00:00", "updated_at": "2026-10-15T08:00:00+00:00",
    })


def _no_secrets(out):
    text = json.dumps(out)
    for secret in (API_KEY, PAYLOAD, PAYLOAD_TEXT, SEALED, KEY_ID, REMOTE_ERROR, "user:pw", "free text"):
        assert secret not in text, secret


# ── coverage ─────────────────────────────────────────────────────────────────

def test_the_vault_and_catalog_pages_are_answered():
    pages = lookup.covered_pages()
    assert pages["/vault"] == "synced" and pages["/catalog"] == "catalog_install"
    assert not lookup.KINDS["synced"].admin and not lookup.KINDS["catalog_install"].admin


# ── synced ───────────────────────────────────────────────────────────────────

def test_synced_lists_this_hubs_status_and_the_spaces(single, linked):
    _space("local", "team", records=3)
    _space("local", "other", encrypted=True, records=1)
    out = lookup.lookup("synced", workspace="all")
    ids = [r["id"] for r in out["items"]]
    assert ids[0] == "this-hub/client" and set(ids[1:]) == {"team", "other"}
    client = out["items"][0]
    assert "office" in client["label"] and "on" in client["subtitle"] and "failing" in client["subtitle"]
    team = next(r for r in out["items"] if r["id"] == "team")
    assert team["url"] == "/vault?space=team" and "3 records" in team["subtitle"]
    _no_secrets(out)

    card = lookup.lookup("synced", entity_id="other", workspace="all")
    fields = card["fields"]
    assert card["url"] == "/vault?space=other"
    assert fields["encrypted"] and fields["key_set"] and fields["records"] == 1
    assert fields["encrypted_records"] == 1 and fields["by_kind"] == {"memory": 1} and fields["last_push_at"]
    _no_secrets(card)

    status = lookup.lookup("sync", entity_id="this-hub/client", workspace="all")["fields"]
    assert status["enabled"] and status["encrypted"] and status["key_set"] and not status["key_unlocked"]
    assert status["remote"] == "https://remote.test" and status["has_error"] and status["open_conflicts"] == 0
    assert status["last_result"] == {"pushed": 3, "applied": 1}
    assert {m["workspace"] for m in status["mappings"]} == {"team", "other"}
    _no_secrets(status)


def test_synced_stays_in_the_lookups_workspace(single, linked):
    _space("local", "team")
    _space("local", "other")
    out = lookup.lookup("synced", current="team", cross_workspace=False)
    assert [r["id"] for r in out["items"]] == ["this-hub/client", "team"]
    status = lookup.lookup("synced", entity_id="this-hub/client", current="team", cross_workspace=False)
    assert [m["workspace"] for m in status["fields"]["mappings"]] == ["team"]
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("synced", entity_id="other", current="team", cross_workspace=False)


def test_synced_without_a_link_lists_spaces_only(single, hub):
    _space("local", "team")
    out = lookup.lookup("synced", workspace="team")
    assert [r["id"] for r in out["items"]] == ["team"]
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("synced", entity_id="this-hub/client", workspace="team")


def test_synced_shows_only_the_callers_own_spaces(multi, hub):
    bob = identity.create_user("bob", PASSWORD, role="member")["id"]
    alice = identity.create_user("alice", PASSWORD, role="member")["id"]
    for user in (bob, alice):
        identity.set_member("team", user, "editor")
    _space(alice, "team", records=5)
    assert lookup.lookup("synced", workspace="team", user_id=bob)["items"] == []
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("synced", entity_id="team", workspace="team", user_id=bob)
    _space(bob, "team", records=1)
    card = lookup.lookup("synced", entity_id="team", workspace="team", user_id=bob)
    assert card["fields"]["records"] == 1
    # A space named after a workspace bob cannot see stays out, even his own.
    _space(bob, "other")
    assert [r["id"] for r in lookup.lookup("synced", workspace="all", user_id=bob)["items"]] == ["team"]


# ── catalog_install ──────────────────────────────────────────────────────────

def test_catalog_installs_list_and_card_from_provenance(single, linked):
    _install("agents", "writer-2", "team", remote_id="writer")
    _install("skills", "summarize", "other", remote_id="sk-9")
    out = lookup.lookup("catalog_install", workspace="all")
    assert {r["id"] for r in out["items"]} == {"agents:writer-2", "skills:summarize"}
    assert all(r["url"] == "/catalog" for r in out["items"])
    assert lookup.lookup("catalog", workspace="team")["count"] == 1
    _no_secrets(out)

    card = lookup.lookup("catalog_install", entity_id="agents:writer-2", workspace="all")
    fields = card["fields"]
    assert card["url"] == "/catalog" and fields["remote_id"] == "writer" and fields["workspace"] == "team"
    assert fields["remote"] == "https://remote.test" and fields["local_missing"]
    assert fields["dependencies"] == {"agents": {"writer": "writer-2"}}
    _no_secrets(card)
    # A bare local id works when it names one item.
    assert lookup.lookup("catalog_install", entity_id="summarize", workspace="all")["fields"]["kind"] == "skills"


def test_catalog_installs_respect_the_workspace(single, linked):
    _install("skills", "summarize", "other")
    assert lookup.lookup("catalog_install", current="team", cross_workspace=False)["items"] == []
    with pytest.raises(lookup.LookupError_):
        lookup.lookup("catalog_install", entity_id="skills:summarize", current="team", cross_workspace=False)
