"""common/docstore.py: named JSON collections in the documents table, and the
one-time import of the JSON file each collection used to be."""
from __future__ import annotations

import json
import threading

import pytest

from common import db
from common.docstore import DocStore


def test_put_get_delete_and_insertion_order():
    s = DocStore("t_things")
    s.put("b", {"n": 2})
    s.put("a", {"n": 1})
    s.put("c", {"n": 3})
    assert s.get("a") == {"n": 1}
    assert s.get("missing") is None
    assert list(s.all()) == ["b", "a", "c"]
    s.put("b", {"n": 22})              # a rewrite keeps its place
    assert list(s.all()) == ["b", "a", "c"]
    assert s.all()["b"] == {"n": 22}
    assert s.delete("a") is True and s.delete("a") is False
    assert s.keys() == ["b", "c"] and s.count() == 2
    assert s.exists("c") and not s.exists("a")


def test_replace_all_keeps_existing_places_and_drops_the_rest():
    s = DocStore("t_replace")
    s.put("x", 1)
    s.put("y", 2)
    s.replace_all({"z": 3, "y": 20})
    assert s.all() == {"y": 20, "z": 3}
    assert list(s.all()) == ["y", "z"]
    assert s.clear() == 2 and s.count() == 0


def test_stores_are_isolated_by_name():
    a, b = DocStore("t_a"), DocStore("t_b")
    a.put("k", "a")
    b.put("k", "b")
    assert a.get("k") == "a" and b.get("k") == "b"
    assert a.keys() == ["k"] and b.keys() == ["k"]


def test_signature_changes_on_every_write():
    s = DocStore("t_sig")
    before = s.signature()
    s.put("k", 1)
    after = s.signature()
    assert before != after
    assert after.startswith("1:")


def test_a_transaction_makes_read_modify_write_atomic_across_threads():
    s = DocStore("t_counter")
    s.put("n", 0)
    errors = []

    def bump():
        try:
            for _ in range(20):
                with s.transaction():
                    s.put("n", int(s.get("n")) + 1)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert s.get("n") == 80


def test_a_legacy_list_file_is_imported_once_and_renamed(tmp_path):
    path = tmp_path / "things.json"
    path.write_text(json.dumps([{"id": "one", "v": 1}, {"id": "two", "v": 2}, {"v": 3}]),
                    encoding="utf-8")
    s = DocStore("t_legacy_list", legacy_file=path, legacy_key=lambda d: d.get("id"))
    assert s.all() == {"one": {"id": "one", "v": 1}, "two": {"id": "two", "v": 2}, "2": {"v": 3}}
    assert not path.exists() and (tmp_path / "things.json.migrated").exists()

    # A second store over the same name never re-imports, even if the file
    # comes back: the rows already there win and the file is left alone.
    path.write_text(json.dumps([{"id": "three"}]), encoding="utf-8")
    again = DocStore("t_legacy_list", legacy_file=path, legacy_key=lambda d: d.get("id"))
    assert set(again.keys()) == {"one", "two", "2"}
    assert path.exists()


def test_a_legacy_dict_file_is_keyed_by_its_own_keys(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"ws-a": {"x": 1}, "ws-b": {"x": 2}}), encoding="utf-8")
    s = DocStore("t_legacy_dict", legacy_file=path)
    assert s.get("ws-b") == {"x": 2}
    assert (tmp_path / "map.json.migrated").exists()


def test_an_unreadable_legacy_file_is_left_in_place(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    s = DocStore("t_legacy_broken", legacy_file=path)
    assert s.count() == 0
    assert path.exists()
    assert "unreadable" in capsys.readouterr().out


def test_import_legacy_by_hand_loads_an_empty_store_only(tmp_path):
    src = tmp_path / "custom.json"
    src.write_text("{}", encoding="utf-8")
    s = DocStore("t_manual")
    assert s.import_legacy({"state": {"a": 1}}, src) == 1
    assert s.get("state") == {"a": 1}
    assert (tmp_path / "custom.json.migrated").exists()
    assert s.import_legacy({"state": {"a": 2}}, None) == 0
    assert s.get("state") == {"a": 1}


def test_the_legacy_check_runs_again_after_the_database_is_reopened(tmp_path, reopen_db):
    path = tmp_path / "later.json"
    s = DocStore("t_reopen", legacy_file=path)
    assert s.count() == 0            # checked: no file yet
    path.write_text(json.dumps({"k": 1}), encoding="utf-8")
    assert s.count() == 0            # same opening: not checked again
    reopen_db(tmp_path / "other.db")
    assert s.get("k") == 1           # a new opening looks at the file again


@pytest.mark.parametrize("value", [None, 0, "", [], {"nested": [1, {"a": None}]}])
def test_any_json_value_round_trips(value):
    s = DocStore("t_values")
    s.put("v", value)
    assert s.get("v") == value if value is not None else s.get("v") is None
    assert "v" in s.all()


def test_documents_table_is_in_the_migration_ledger():
    conn = db.get_conn()
    row = conn.execute("SELECT name FROM schema_migrations WHERE version = 6").fetchone()
    assert row is not None and row["name"] == "documents"
