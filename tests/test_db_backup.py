"""
Backup / restore / verify (common/db_backup.py): one archive round-trips the
database and the state directories beside it.

``AGENTS_HUB_ROOT`` is fixed for the whole test session (tests/conftest.py),
so these tests point ``common.paths.AGENTS_HUB_ROOT`` and the module-level
copy ``common.db_backup.AGENTS_HUB_ROOT`` at a throwaway directory of their
own, the same way the code itself reads that name (a plain attribute lookup
at call time, so monkeypatching the module's copy is enough — no need to
touch every other module that also imported it).
"""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import main as cli_main
from common import db_backup
from common import paths as paths_mod
from managers import run_manager as rm
from tasks.service import create_task

runner = CliRunner()


@pytest.fixture
def state_root(tmp_path, monkeypatch):
    """A throwaway AGENTS_HUB_ROOT, wired into both common.paths (what most of
    the app reads) and common.db_backup's own module-level copy."""
    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setattr(paths_mod, "AGENTS_HUB_ROOT", root)
    monkeypatch.setattr(db_backup, "AGENTS_HUB_ROOT", root)
    monkeypatch.delenv("AGENTS_HUB_URL", raising=False)
    return root


def _seed(state_root: Path) -> None:
    """One task, one run, and a few files under directories the archive
    should and should not carry (workspaces/ included; node_modules/ and
    __pycache__/ pruned wherever they appear)."""
    create_task("backup smoke test")
    rm.upsert_run({"run_id": "r1", "agent_id": "a", "status": "completed"})

    logs = state_root / "run_logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "agent_run_r1.log").write_text("hello\n", encoding="utf-8")

    ws = state_root / "workspaces" / "proj"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "notes.txt").write_text("kept\n", encoding="utf-8")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.pyc").write_bytes(b"junk")
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "index.js").write_text("junk", encoding="utf-8")


def test_backup_writes_manifest_and_archive(state_root, tmp_path):
    _seed(state_root)

    backups = tmp_path / "backups"
    backups.mkdir()
    report = db_backup.backup(backups, log=lambda *_: None)
    archive = Path(report["archive"])
    assert archive.exists()
    assert archive.name.startswith("agents_hub_backup_") and archive.name.endswith(".tar.gz")

    manifest = report["manifest"]
    assert manifest["counts"]["tasks"] == 1
    assert manifest["counts"]["runs"] == 1
    from common import db
    assert manifest["source"]["dialect"] == db.dialect()
    assert set(manifest["files"]) == {"run_logs", "workspaces"}


def test_backup_excludes_caches_and_node_modules(state_root, tmp_path):
    _seed(state_root)
    report = db_backup.backup(tmp_path / "backups", log=lambda *_: None)

    import tarfile
    with tarfile.open(report["archive"], "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("notes.txt") for n in names)
    assert not any("__pycache__" in n for n in names)
    assert not any("node_modules" in n for n in names)


def test_backup_without_files(state_root, tmp_path):
    _seed(state_root)
    report = db_backup.backup(tmp_path / "backups", include_files=False, log=lambda *_: None)
    assert report["manifest"]["files"] == []

    import tarfile
    with tarfile.open(report["archive"], "r:gz") as tar:
        names = tar.getnames()
    assert not any(n.startswith("files/") for n in names)


def test_restore_round_trip_into_empty_state(state_root, tmp_path, reopen_db):
    _seed(state_root)
    report = db_backup.backup(tmp_path / "backups", log=lambda *_: None)
    archive = report["archive"]

    # Lose everything: a brand new empty database, and an empty state root.
    reopen_db(tmp_path / "empty.db")
    for child in state_root.iterdir():
        import shutil
        shutil.rmtree(child) if child.is_dir() else child.unlink()

    restored = db_backup.restore(archive, log=lambda *_: None)
    assert restored["mismatches"] == {}
    assert restored["tables"]["tasks"]["target"] == 1
    assert restored["tables"]["runs"]["target"] == 1
    assert restored["files_restored"] >= 2  # the log file and notes.txt

    assert (state_root / "run_logs" / "agent_run_r1.log").read_text(encoding="utf-8") == "hello\n"
    assert (state_root / "workspaces" / "proj" / "notes.txt").read_text(encoding="utf-8") == "kept\n"
    assert not (state_root / "workspaces" / "proj" / "node_modules").exists()
    assert not (state_root / "workspaces" / "proj" / "__pycache__").exists()


def test_restore_refuses_a_busy_target_without_force(state_root, tmp_path):
    _seed(state_root)
    report = db_backup.backup(tmp_path / "backups", log=lambda *_: None)
    archive = report["archive"]

    # The configured database still holds the row(s) just backed up.
    with pytest.raises(RuntimeError, match="already holds rows"):
        db_backup.restore(archive)

    forced = db_backup.restore(archive, force=True, log=lambda *_: None)
    assert forced["mismatches"] == {}


def test_verify_matches_a_clean_archive(state_root, tmp_path):
    _seed(state_root)
    report = db_backup.backup(tmp_path / "backups", log=lambda *_: None)
    result = db_backup.verify(report["archive"])
    assert result["ok"] is True
    assert result["mismatches"] == {}


def test_verify_detects_a_tampered_manifest(state_root, tmp_path):
    _seed(state_root)
    report = db_backup.backup(tmp_path / "backups", log=lambda *_: None)

    import io
    import json
    import shutil
    import tarfile

    src = Path(report["archive"])
    tampered = src.with_name("tampered.tar.gz")
    shutil.copy(src, tampered)

    with tarfile.open(tampered, "r:gz") as tar:
        members = tar.getmembers()
        manifest = json.loads(tar.extractfile("manifest.json").read())
        payloads = {m.name: tar.extractfile(m).read() for m in members if m.isfile() and m.name != "manifest.json"}

    manifest["counts"]["runs"] = 999
    with tarfile.open(tampered, "w:gz") as tar:
        data = json.dumps(manifest).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for member in members:
            if member.isfile() and member.name != "manifest.json":
                data = payloads[member.name]
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            elif member.name != "manifest.json":
                tar.addfile(member)

    result = db_backup.verify(tampered)
    assert result["ok"] is False
    assert result["mismatches"]["runs"] == {"manifest": 999, "actual": 1}


def test_cli_backup_restore_verify_round_trip(state_root, tmp_path):
    _seed(state_root)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    result = runner.invoke(cli_main.app, ["db", "backup", "--to", str(backup_dir)])
    assert result.exit_code == 0, result.output
    archives = list(backup_dir.glob("*.tar.gz"))
    assert len(archives) == 1
    archive = archives[0]

    result = runner.invoke(cli_main.app, ["db", "verify", str(archive)])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output

    # Restoring over the same (non-empty) database without --force fails.
    result = runner.invoke(cli_main.app, ["db", "restore", str(archive)])
    assert result.exit_code == 1
    assert "--force" in result.output

    result = runner.invoke(cli_main.app, ["db", "restore", str(archive), "--force"])
    assert result.exit_code == 0, result.output
    assert "counts match the archive's manifest" in result.output


def test_cli_requires_direct_mode(state_root, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTS_HUB_URL", "http://example.invalid:9999")
    result = runner.invoke(cli_main.app, ["db", "backup", "--to", str(tmp_path / "b")])
    assert result.exit_code == 1
    assert "AGENTS_HUB_URL" in result.output
