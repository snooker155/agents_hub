"""Releases: the version source, and the database archive
taken before a schema migration (docs/deployment.md "Releases")."""
import asyncio
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── common/version.py ────────────────────────────────────────────────────────

def test_version_reads_pyproject(monkeypatch):
    from common import version
    monkeypatch.delenv("AGENTS_HUB_VERSION", raising=False)
    assert version.is_semver(version.app_version())


def test_baked_image_version_wins(monkeypatch):
    from common import version
    monkeypatch.setenv("AGENTS_HUB_VERSION", "v9.8.7")
    monkeypatch.setenv("AGENTS_HUB_GIT_SHA", "abc123def4567890")
    info = version.info()
    assert info["version"] == "9.8.7"
    assert info["git_sha"] == "abc123def4567890"
    assert info["git_short"] == "abc123def456"


def test_system_version_route(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_VERSION", "1.2.3")
    from dashboard.backend.routes.system import system_version
    body = asyncio.run(system_version())
    assert body["version"] == "1.2.3"
    assert body["schema_latest"] >= 24


# ── the archive before a migration ───────────────────────────────────────────

def _reopen_same_file(db):
    """Make the next get_conn() run the startup sequence again on the same file."""
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
    db._local = threading.local()
    db._schema_ready = False


@pytest.mark.sqlite_only
def test_pending_migration_archives_the_database_first(tmp_path, monkeypatch):
    from common import db, db_backup, migrations

    monkeypatch.setattr(db_backup, "AGENTS_HUB_ROOT", tmp_path / "root")
    conn = db.get_conn()
    conn.execute("INSERT INTO meta (key, value) VALUES ('app_version', '0.0.9') "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    latest = migrations.latest_version("sqlite")
    conn.execute(f"DELETE FROM {migrations.LEDGER_TABLE} WHERE version = ?", (latest,))

    _reopen_same_file(db)
    db.get_conn()

    archives = sorted((tmp_path / "root" / "backups").glob("pre-migrate_*.tar.gz"))
    assert len(archives) == 1
    report = db_backup.verify(archives[0])
    assert report["ok"], report["mismatches"]
    manifest = report["manifest"]
    assert manifest["reason"] == "pre-migrate"
    assert manifest["pending_migrations"] == [latest]
    assert manifest["app_version"] == "0.0.9"
    # The new build recorded itself as the one that last opened the database.
    from common.version import app_version
    row = db.get_conn().execute("SELECT value FROM meta WHERE key='app_version'").fetchone()
    assert row[0] == app_version()


@pytest.mark.sqlite_only
def test_nothing_pending_takes_no_archive(tmp_path, monkeypatch):
    from common import db, db_backup

    monkeypatch.setattr(db_backup, "AGENTS_HUB_ROOT", tmp_path / "root")
    db.get_conn()
    _reopen_same_file(db)
    db.get_conn()
    assert not (tmp_path / "root" / "backups").exists()


@pytest.mark.sqlite_only
def test_archive_can_be_turned_off_and_keeps_only_the_newest(tmp_path, monkeypatch):
    from common import db, db_backup, migrations

    monkeypatch.setattr(db_backup, "AGENTS_HUB_ROOT", tmp_path / "root")
    monkeypatch.setattr(db_backup, "PRE_MIGRATE_KEEP", 2)
    latest = migrations.latest_version("sqlite")

    def _drop_latest_and_reopen():
        db.get_conn().execute(f"DELETE FROM {migrations.LEDGER_TABLE} WHERE version = ?", (latest,))
        _reopen_same_file(db)
        db.get_conn()

    monkeypatch.setenv(db_backup.PRE_MIGRATE_ENV, "0")
    _drop_latest_and_reopen()
    assert not (tmp_path / "root" / "backups").exists()

    monkeypatch.delenv(db_backup.PRE_MIGRATE_ENV)
    stamps = iter(["20260101T000001Z", "20260101T000002Z", "20260101T000003Z"])
    monkeypatch.setattr(db_backup, "_timestamp", lambda: next(stamps))
    for _ in range(3):
        _drop_latest_and_reopen()
    names = sorted(p.name for p in (tmp_path / "root" / "backups").iterdir())
    assert [n.split("_")[1] for n in names] == ["20260101T000002Z", "20260101T000003Z"]
