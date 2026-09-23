"""Personal API keys (common/api_keys.py): create, list, revoke, resolve.

Runs against whichever database ``tests/conftest.py`` points at (SQLite by
default, Postgres with ``AGENTS_HUB_TEST_DATABASE_URL``). Nothing here talks
HTTP; the routes that expose this are covered in
``tests/test_account_routes.py``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import api_keys, identity


@pytest.fixture
def user():
    return identity.create_user("alice", "hunter2-but-longer", role="member",
                                display_name="Alice")


def test_create_returns_the_key_once_and_a_hint(user):
    key, record = api_keys.create_key(user["id"], name="laptop")
    assert key.startswith(api_keys.KEY_PREFIX)
    assert record["name"] == "laptop"
    assert record["hint"] == key[-4:]
    assert record["workspaces"] is None
    assert record["revoked_at"] is None
    # The plaintext key is never in the record.
    assert "key" not in record
    assert "key_hash" not in record


def test_list_keys_is_scoped_to_the_owner(user):
    other = identity.create_user("bob", "hunter2-but-longer")
    api_keys.create_key(user["id"], name="mine")
    api_keys.create_key(other["id"], name="theirs")
    mine = api_keys.list_keys(user["id"])
    assert [k["name"] for k in mine] == ["mine"]


def test_resolve_finds_the_owner(user):
    key, record = api_keys.create_key(user["id"])
    found = api_keys.resolve(key)
    assert found is not None
    resolved_user, resolved_key = found
    assert resolved_user["id"] == user["id"]
    assert resolved_key["id"] == record["id"]


def test_resolve_rejects_garbage_and_wrong_keys(user):
    assert api_keys.resolve("not-a-key-at-all") is None
    key, _ = api_keys.create_key(user["id"])
    assert api_keys.resolve(key + "x") is None


def test_revoke_stops_it_resolving(user):
    key, record = api_keys.create_key(user["id"])
    assert api_keys.revoke_key(record["id"]) is True
    assert api_keys.resolve(key) is None
    # Revoking again reports nothing changed.
    assert api_keys.revoke_key(record["id"]) is False


def test_revoke_scoped_to_the_wrong_owner_does_nothing(user):
    other = identity.create_user("bob", "hunter2-but-longer")
    key, record = api_keys.create_key(user["id"])
    assert api_keys.revoke_key(record["id"], user_id=other["id"]) is False
    assert api_keys.resolve(key) is not None


def test_revoke_all_drops_every_live_key_of_one_user(user):
    api_keys.create_key(user["id"], name="a")
    api_keys.create_key(user["id"], name="b")
    other = identity.create_user("bob", "hunter2-but-longer")
    api_keys.create_key(other["id"], name="c")
    assert api_keys.revoke_all(user["id"]) == 2
    assert api_keys.list_keys(user["id"]) == []
    assert len(api_keys.list_keys(other["id"])) == 1


def test_expiry_is_honoured(user, monkeypatch):
    key, record = api_keys.create_key(user["id"], expires_in_days=1)
    assert record["expires_at"] is not None
    assert api_keys.resolve(key) is not None

    # Move "now" past the expiry.
    future = datetime.now(timezone.utc) + timedelta(days=2)
    monkeypatch.setattr(api_keys, "_now", lambda: future)
    assert api_keys.resolve(key) is None


def test_expires_in_days_zero_means_no_expiry(user):
    # Falsy like "not set": create_key only rejects a positive-but-invalid
    # value, i.e. a negative one.
    _, record = api_keys.create_key(user["id"], expires_in_days=0)
    assert record["expires_at"] is None


def test_a_negative_expiry_is_refused(user):
    with pytest.raises(ValueError):
        api_keys.create_key(user["id"], expires_in_days=-3)


def test_a_disabled_owner_loses_every_key(user):
    key, _ = api_keys.create_key(user["id"])
    identity.update_user(user["id"], disabled=True)
    assert api_keys.resolve(key) is None


def test_deleting_the_owner_removes_the_row_too(user):
    key, record = api_keys.create_key(user["id"])
    identity.delete_user(user["id"])
    assert api_keys.resolve(key) is None
    assert api_keys.get_key(record["id"]) is None


def test_no_such_user_refuses_to_cut_a_key():
    with pytest.raises(ValueError):
        api_keys.create_key("no-such-user")


def test_workspace_scope_is_stored_sorted_and_deduplicated(user):
    _, record = api_keys.create_key(user["id"], workspaces=["beta", "alpha", "alpha"])
    assert record["workspaces"] == ["alpha", "beta"]


def test_an_empty_workspace_list_means_unscoped(user):
    _, record = api_keys.create_key(user["id"], workspaces=[])
    assert record["workspaces"] is None


def test_last_used_is_touched_on_resolve(user):
    key, record = api_keys.create_key(user["id"])
    assert record["last_used_at"] is None
    found = api_keys.resolve(key)
    assert found is not None
    touched = api_keys.get_key(record["id"])
    assert touched["last_used_at"] is not None


def test_last_used_does_not_move_within_the_touch_interval(user, monkeypatch):
    key, record = api_keys.create_key(user["id"])
    api_keys.resolve(key)
    first = api_keys.get_key(record["id"])["last_used_at"]

    # Still inside TOUCH_INTERVAL: no write.
    api_keys.resolve(key)
    second = api_keys.get_key(record["id"])["last_used_at"]
    assert first == second

    # Past it: touched again.
    future = datetime.now(timezone.utc) + api_keys.TOUCH_INTERVAL + timedelta(seconds=5)
    monkeypatch.setattr(api_keys, "_now", lambda: future)
    api_keys.resolve(key)
    third = api_keys.get_key(record["id"])["last_used_at"]
    assert third != second


def test_looks_like_key_is_a_cheap_prefix_check():
    assert api_keys.looks_like_key("ahk_abc") is True
    assert api_keys.looks_like_key("Bearer ahk_abc") is False
    assert api_keys.looks_like_key("") is False
    assert api_keys.looks_like_key(None) is False
