"""common/state_sync.py: a hub in Docker with its state on a volume and the
checkout's .agents_hub follow each other. The state is copied toward the side
that did not change since the last sync; when both changed nothing is
overwritten; the folder's database is never opened while it has a -wal or
-shm file (a hub on the host has it open)."""
import sqlite3
from pathlib import Path

import pytest

from common import state_sync


def _db(root: Path, *rows: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(root / "agents_hub.db"))
    conn.execute("create table if not exists t(x)")
    for row in rows:
        conn.execute("insert into t values (?)", (row,))
    conn.commit()
    conn.close()


def _rows(root: Path) -> list:
    conn = sqlite3.connect(str(root / "agents_hub.db"))
    try:
        return [r[0] for r in conn.execute("select x from t order by rowid")]
    finally:
        conn.close()


@pytest.fixture
def sides(tmp_path, monkeypatch):
    volume = tmp_path / "volume"
    folder = tmp_path / "checkout" / ".agents_hub"
    volume.mkdir()
    folder.parent.mkdir()
    monkeypatch.setattr("common.paths.AGENTS_HUB_ROOT", volume)
    monkeypatch.setenv("AGENTS_HUB_STATE_VOLUME", "agents_hub_state")
    monkeypatch.setenv(state_sync.SYNC_DIR_ENV, str(folder))
    monkeypatch.setattr(state_sync, "_stop_agent_containers", lambda: 0)
    return volume, folder


def _start():
    assert state_sync.import_on_start() == 0


def _stop():
    state_sync.export_on_shutdown()


def test_first_start_takes_the_folder_into_an_empty_volume(sides):
    volume, folder = sides
    _db(folder, "local")
    (folder / "workspaces" / "w").mkdir(parents=True)
    (folder / "workspaces" / "w" / "a.txt").write_text("hi")
    _start()
    assert _rows(volume) == ["local"]
    assert (volume / "workspaces" / "w" / "a.txt").read_text() == "hi"
    assert state_sync._read_marker()["exported"] is False  # the hub runs now


def test_a_stop_exports_and_keeps_the_previous_folder(sides):
    volume, folder = sides
    _db(folder, "local")
    _start()
    _db(volume, "docker")
    _stop()
    assert _rows(folder) == ["local", "docker"]
    assert _rows(folder.parent / ".agents_hub.prev") == ["local"]
    assert not (folder / state_sync.MARKER).exists()
    assert state_sync._read_marker()["exported"] is True


def test_an_unchanged_folder_is_not_imported_again(sides):
    volume, folder = sides
    _db(folder, "local")
    _start()
    _db(volume, "docker")
    _stop()
    _start()
    assert _rows(volume) == ["local", "docker"]


def test_work_done_on_the_host_between_runs_comes_in(sides):
    volume, folder = sides
    _db(folder, "local")
    _start()
    _stop()
    _db(folder, "host work")
    _start()
    assert _rows(volume) == ["local", "host work"]


def test_when_both_changed_nothing_is_overwritten(sides):
    volume, folder = sides
    _db(folder, "local")
    _start()
    _db(volume, "docker")
    _db(folder, "host work")          # a hub on the host ran at the same time
    _stop()                            # refused: the folder changed too
    assert _rows(folder) == ["local", "host work"]
    _start()                           # refused: the volume was not exported
    assert _rows(volume) == ["local", "docker"]


def test_a_folder_in_use_is_never_opened(sides, monkeypatch):
    volume, folder = sides
    _db(folder, "local")
    (folder / "agents_hub.db-wal").write_bytes(b"")
    opened = []
    monkeypatch.setattr(state_sync, "_backup_db", lambda src, dst: opened.append(src))
    _start()
    _stop()
    assert opened == []
    assert not (volume / "agents_hub.db").exists()


def test_never_synced_sides_with_two_databases_need_a_choice(sides):
    volume, folder = sides
    _db(folder, "local")
    _db(volume, "docker")
    _start()
    assert _rows(volume) == ["docker"]
    done, why = state_sync.export_state(folder)
    assert not done and "--force" in why
    done, _ = state_sync.export_state(folder, force=True)
    assert done and _rows(folder) == ["docker"]


def test_an_import_never_replaces_an_open_volume_database(sides):
    volume, folder = sides
    _db(folder, "local")
    _db(volume, "docker")
    (volume / "agents_hub.db-shm").write_bytes(b"")
    done, why = state_sync.import_state(folder, force=True)
    assert not done and "open" in why
    assert _rows(volume) == ["docker"]


def test_a_manual_export_while_running_is_only_a_snapshot(sides):
    volume, folder = sides
    _db(folder, "local")
    _start()
    _db(volume, "docker")
    done, _ = state_sync.export_state(folder)
    assert done and state_sync._read_marker()["exported"] is False


def test_sync_is_off_without_a_volume(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_HUB_STATE_VOLUME", raising=False)
    monkeypatch.setenv(state_sync.SYNC_DIR_ENV, str(tmp_path))
    assert state_sync.sync_dir() is None


def test_every_start_says_how_the_volume_and_the_folder_relate(sides, capsys):
    _start()
    out = capsys.readouterr().out
    assert "agents_hub_state" in out and ".agents_hub.prev" in out and "docker compose stop" in out
