"""Releases: the version source, scripts/release.py, and the database archive
taken before a schema migration (docs/deployment.md "Releases")."""
import asyncio
import importlib.util
import json
import shutil
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _release_module():
    spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


# ── scripts/release.py ───────────────────────────────────────────────────────

@pytest.mark.parametrize("current,spec,expected", [
    ("0.1.0", "patch", "0.1.1"),
    ("0.1.0", "minor", "0.2.0"),
    ("0.1.0", "major", "1.0.0"),
    ("0.3.0-rc.1", "patch", "0.3.0"),
    ("0.1.0", "v0.2.0-rc.1", "0.2.0-rc.1"),
])
def test_next_version(current, spec, expected):
    assert _release_module().next_version(current, spec) == expected


@pytest.mark.parametrize("spec", ["0.1.0", "0.0.9", "0.1.0-rc.1", "banana"])
def test_next_version_refuses_older_or_invalid(spec):
    rel = _release_module()
    with pytest.raises(rel.ReleaseError):
        rel.next_version("0.1.0", spec)


def test_changelog_release_renames_unreleased():
    rel = _release_module()
    text = "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- a thing\n\n## [0.1.0] - 2026-09-24\n\nold\n"
    out = rel.release_changelog(text, "0.2.0", "2026-10-01", [])
    assert "## [Unreleased]\n\n## [0.2.0] - 2026-10-01\n\n### Added\n\n- a thing" in out
    assert rel.section_body(out, "0.2.0") == "### Added\n\n- a thing"
    assert rel.section_body(out, "0.1.0") == "old"
    with pytest.raises(rel.ReleaseError):
        rel.release_changelog(out, "0.2.0", "2026-10-02", [])


def test_empty_unreleased_is_filled_from_commits():
    rel = _release_module()
    text = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-09-24\n\nold\n"
    out = rel.release_changelog(text, "0.1.1", "2026-10-01", ["Fix the widget", "Add a button"])
    assert rel.section_body(out, "0.1.1") == "### Changes\n\n- Fix the widget\n- Add a button"


def test_write_versions_moves_every_file_together(tmp_path, monkeypatch):
    rel = _release_module()
    paths = {
        "PYPROJECT": ROOT / "pyproject.toml",
        "PACKAGE_JSON": ROOT / "dashboard" / "frontend" / "package.json",
        "PACKAGE_LOCK": ROOT / "dashboard" / "frontend" / "package-lock.json",
        "CHART": ROOT / "deploy" / "helm" / "agents-hub" / "Chart.yaml",
        "README": ROOT / "README.md",
    }
    for name, src in paths.items():
        dst = tmp_path / src.name
        shutil.copy(src, dst)
        monkeypatch.setattr(rel, name, dst)

    assert len(set(rel.read_versions().values())) == 1, "the checkout's version files disagree"
    rel.write_versions("7.0.0-rc.2")
    assert set(rel.read_versions().values()) == {"7.0.0-rc.2"}
    lock = json.loads((tmp_path / "package-lock.json").read_text())
    assert lock["packages"][""]["version"] == "7.0.0-rc.2"
    # Only the package's own version moved, never a dependency's.
    before = json.loads(paths["PACKAGE_LOCK"].read_text())
    assert {k: v.get("version") for k, v in lock["packages"].items() if k} == \
        {k: v.get("version") for k, v in before["packages"].items() if k}


def test_check_flags_a_tag_that_does_not_match(capsys):
    rel = _release_module()
    assert rel.cmd_check(None) == 0
    assert rel.cmd_check("v99.0.0") == 1
    assert "does not match" in capsys.readouterr().err


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
