"""The lock file (declarative/lock.py): stable bytes, entries that keep
their timestamp when nothing moved, and refusal of what it cannot read."""
from __future__ import annotations

import json

import pytest

from declarative import Lock, LockError
from declarative.lock import value_hash


def test_missing_file_is_an_empty_lock_bound_to_its_path(tmp_path):
    lock = Lock.load(tmp_path / "ah.lock")
    assert len(lock) == 0 and lock.path == tmp_path / "ah.lock"


def test_round_trip_is_sorted_and_stable(tmp_path):
    lock = Lock(workspace="team")
    lock.record("environment/b", kind="environment", key="b", hub_id="2",
                fields={"name": {"d": "x", "o": "x"}}, version="t1", source="b.yaml")
    lock.record("agent/a", kind="agent", key="a", hub_id="a", spec_hash="h",
                fields={"tools": {"d": "1", "o": "2"}}, version=3)
    path = lock.save(tmp_path / "ah.lock")
    text = path.read_text()
    data = json.loads(text)
    assert list(data["resources"]) == ["agent/a", "environment/b"]
    assert data["workspace"] == "team" and data["lock_version"] == 1
    again = Lock.load(path)
    assert again.hub_id("environment", "b") == "2" and again.get("agent/a")["version"] == 3
    again.save()
    assert path.read_text() == text


def test_applied_at_moves_only_when_the_entry_does():
    lock = Lock()
    first = lock.record("agent/a", kind="agent", key="a", hub_id="a", fields={"x": {"d": "1", "o": "1"}})
    stamp = first["applied_at"]
    lock.entries["agent/a"]["applied_at"] = "2000-01-01T00:00:00+00:00"
    same = lock.record("agent/a", kind="agent", key="a", hub_id="a", fields={"x": {"d": "1", "o": "1"}})
    assert same["applied_at"] == "2000-01-01T00:00:00+00:00"
    moved = lock.record("agent/a", kind="agent", key="a", hub_id="a", fields={"x": {"d": "2", "o": "2"}})
    assert moved["applied_at"] != "2000-01-01T00:00:00+00:00" and stamp


def test_refuses_what_it_cannot_read(tmp_path):
    bad = tmp_path / "bad.lock"
    bad.write_text("not json")
    with pytest.raises(LockError, match="not a lock file"):
        Lock.load(bad)
    newer = tmp_path / "newer.lock"
    newer.write_text(json.dumps({"lock_version": 99, "resources": {}}))
    with pytest.raises(LockError, match="newer ah"):
        Lock.load(newer)
    with pytest.raises(LockError, match="needs a path"):
        Lock().save()


def test_value_hash_ignores_key_order():
    assert value_hash({"a": 1, "b": [1, 2]}) == value_hash({"b": [1, 2], "a": 1})
    assert value_hash([1, 2]) != value_hash([2, 1])
