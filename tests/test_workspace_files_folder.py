"""Workspace files that live in the workspace folder (files/service.py,
"Files in the workspace folder" in docs/files.md).

A path an agent writes gets a record whose content is the file itself, kept
in step as the file changes and tombstoned when it goes; the filesystem tools
register what they write; an index of a whole folder registers the rest with
a source by folder, skips what the registry never follows and drops the
records of gone files; and the HTTP index answers with the summary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common.paths import AGENTS_HUB_ROOT, WORKSPACES_ROOT  # noqa: E402
from files import service  # noqa: E402


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.delenv(service.MAX_FILE_MB_ENV, raising=False)
    monkeypatch.delenv(service.MAX_WORKSPACE_MB_ENV, raising=False)


def _folder(name: str) -> Path:
    root = WORKSPACES_ROOT / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── one path ─────────────────────────────────────────────────────────────────

def test_a_folder_file_is_registered_without_a_copy_and_read_back():
    root = _folder("plain")
    _write(root, "research/report.md", "# Report\nfindings")
    rec = service.register_path("plain", "research/report.md", created_by="agent-1",
                                meta={"agent_id": "agent-1", "run_id": "r1"})
    assert rec["source"] == "agent" and rec["name"] == "report.md"
    assert rec["mime_type"] == "text/markdown" and rec["size"] == len("# Report\nfindings")
    assert rec["meta"]["path"] == "research/report.md" and rec["meta"]["run_id"] == "r1"
    # The content is the file in the folder: no copy under files/.
    assert service.local_path(rec["file_id"]) == root / "research" / "report.md"
    assert not (AGENTS_HUB_ROOT / service.FILES_DIR / "plain").exists()
    assert service.read_bytes(rec["file_id"]) == b"# Report\nfindings"
    assert service.extract_text(rec["file_id"]) == "# Report\nfindings"
    assert [f["file_id"] for f in service.list_files("plain")] == [rec["file_id"]]
    assert service.file_at_path("plain", "research/report.md")["file_id"] == rec["file_id"]


def test_rewriting_the_file_updates_the_same_record():
    root = _folder("rewrite")
    _write(root, "notes.txt", "v1")
    first = service.register_path("rewrite", "notes.txt", meta={"run_id": "r1"})
    again = service.register_path("rewrite", "notes.txt", meta={"run_id": "r1"})
    assert again == first  # unchanged content, unchanged record
    _write(root, "notes.txt", "version two")
    second = service.register_path("rewrite", "notes.txt", meta={"run_id": "r2"})
    assert second["file_id"] == first["file_id"]
    assert second["size"] == len("version two") and second["sha256"] != first["sha256"]
    assert second["meta"] == {"path": "notes.txt", "run_id": "r2"}
    assert len(service.list_files("rewrite")) == 1
    assert service.read_bytes(first["file_id"]) == b"version two"


def test_unregister_tombstones_without_touching_the_folder():
    root = _folder("gone")
    _write(root, "a.txt", "a")
    rec = service.register_path("gone", "a.txt")
    (root / "a.txt").unlink()
    assert service.unregister_path("gone", "a.txt") is True
    assert service.unregister_path("gone", "a.txt") is False
    assert service.get_file(rec["file_id"]) is None
    assert service.get_file(rec["file_id"], include_deleted=True)["deleted_at"]


def test_deleting_a_folder_file_from_the_registry_deletes_it_in_the_folder():
    root = _folder("delete")
    _write(root, "out/result.csv", "a,b\n1,2")
    rec = service.register_path("delete", "out/result.csv")
    assert service.delete_file(rec["file_id"]) is True
    assert not (root / "out" / "result.csv").exists()
    # The agent's folder stays, even empty: it is the agent's working tree.
    assert (root / "out").is_dir()


def test_a_path_must_stay_inside_the_workspace_and_be_a_regular_file():
    root = _folder("bounds")
    _write(root, "ok.txt", "x")
    for bad in ("../ok.txt", "/etc/passwd", "", "a/../../ok.txt"):
        with pytest.raises(service.FileError):
            service.register_path("bounds", bad)
    with pytest.raises(service.FileError):
        service.register_path("bounds", "missing.txt")
    (root / "sub").mkdir()
    with pytest.raises(service.FileError):
        service.register_path("bounds", "sub")
    assert service.register_path("bounds", "./ok.txt")["meta"]["path"] == "ok.txt"


def test_the_limits_apply_to_folder_files_too(monkeypatch):
    root = _folder("limits")
    _write(root, "big.txt", "x" * 300)
    monkeypatch.setenv(service.MAX_FILE_MB_ENV, "0.0001")  # about 104 bytes
    with pytest.raises(service.FileTooLarge):
        service.register_path("limits", "big.txt")


def test_what_the_registry_never_follows():
    assert service.is_indexable("research/report.md")
    for hidden in (".logs/run.log", "a/.hidden", "node_modules/x.js", "__pycache__/a.pyc",
                   ".git/HEAD", "build/out.bin", "../x", "/abs"):
        assert not service.is_indexable(hidden), hidden


# ── the tools ────────────────────────────────────────────────────────────────

def test_the_filesystem_tools_register_what_they_write(monkeypatch):
    from tools.filesystem_langchain import create_filesystem_tools
    from common.agent_context import current_agent_id

    root = _folder("toolsws")
    (root / "proj").mkdir()
    tools = {t.name: t for t in create_filesystem_tools(str(root / "proj"))}
    token = current_agent_id.set("writer")
    try:
        tools["write_file"].invoke({"path": "docs/plan.md", "content": "# Plan"})
        tools["create_file"].invoke({"path": "main.py", "content": "print(1)\n"})
        tools["write_file"].invoke({"path": ".secrets/token", "content": "nope"})
    finally:
        current_agent_id.reset(token)

    files = {f["meta"]["path"]: f for f in service.list_files("toolsws")}
    assert set(files) == {"proj/docs/plan.md", "proj/main.py"}
    plan = files["proj/docs/plan.md"]
    assert plan["source"] == "agent" and plan["created_by"] == "writer"
    assert plan["meta"]["agent_id"] == "writer"
    assert service.read_bytes(plan["file_id"]) == b"# Plan"

    tools["write_file"].invoke({"path": "docs/plan.md", "content": "# Plan v2"})
    assert service.get_file(plan["file_id"])["size"] == len("# Plan v2")
    assert len(service.list_files("toolsws")) == 2

    tools["delete_file"].invoke({"path": "main.py"})
    assert [f["meta"]["path"] for f in service.list_files("toolsws")] == ["proj/docs/plan.md"]


def test_a_dir_outside_the_workspaces_root_registers_nothing(tmp_path):
    from tools.filesystem_langchain import create_filesystem_tools
    tools = {t.name: t for t in create_filesystem_tools(str(tmp_path))}
    tools["write_file"].invoke({"path": "x.txt", "content": "x"})
    assert service.list_files(tmp_path.name) == []


# ── the index ────────────────────────────────────────────────────────────────

def test_index_registers_the_folder_with_a_source_by_folder_and_skips_hidden_and_caches():
    root = _folder("indexed")
    _write(root, "report.md", "r")
    _write(root, "plots/one/plot.md", "p")
    _write(root, "knowledge/guide.pdf", "not really a pdf")
    _write(root, "chat_uploads/2026_x_sample.txt", "s")
    _write(root, "task_files/brief.txt", "b")
    _write(root, ".logs/run.log", "hidden")
    _write(root, ".progress.json", "{}")
    _write(root, "node_modules/lib/index.js", "js")
    _write(root, "__pycache__/a.pyc", "pyc")
    (root / "link.txt").symlink_to(root / "report.md")

    summary = service.index_workspace("indexed", created_by="u1")
    assert summary["added"] == 5 and summary["updated"] == 0 and summary["removed"] == 0
    assert summary["skipped"] == []
    by_path = {f["meta"]["path"]: f for f in service.list_files("indexed")}
    assert set(by_path) == {"report.md", "plots/one/plot.md", "knowledge/guide.pdf",
                            "chat_uploads/2026_x_sample.txt", "task_files/brief.txt"}
    assert by_path["report.md"]["source"] == "agent"
    assert by_path["knowledge/guide.pdf"]["source"] == "memory"
    assert by_path["chat_uploads/2026_x_sample.txt"]["source"] == "chat"
    assert by_path["task_files/brief.txt"]["source"] == "task"
    assert all(f["created_by"] == "u1" for f in by_path.values())


def test_index_is_idempotent_updates_changes_and_drops_gone_files():
    root = _folder("again")
    _write(root, "a.txt", "a")
    _write(root, "b.txt", "b")
    first = service.index_workspace("again")
    assert (first["added"], first["unchanged"]) == (2, 0)
    ids = {f["meta"]["path"]: f["file_id"] for f in service.list_files("again")}

    second = service.index_workspace("again")
    assert (second["added"], second["updated"], second["unchanged"], second["removed"]) == (0, 0, 2, 0)

    _write(root, "a.txt", "a changed")
    (root / "b.txt").unlink()
    _write(root, "c.txt", "c")
    third = service.index_workspace("again")
    assert (third["added"], third["updated"], third["unchanged"], third["removed"]) == (1, 1, 0, 1)
    live = {f["meta"]["path"]: f["file_id"] for f in service.list_files("again")}
    assert live["a.txt"] == ids["a.txt"] and "b.txt" not in live and "c.txt" in live
    assert service.get_file(ids["b.txt"], include_deleted=True)["deleted_at"]


def test_index_leaves_uploaded_files_alone_and_reports_what_it_skips(monkeypatch):
    root = _folder("mixed")
    upload = service.create_file("mixed", "uploaded.txt", b"uploaded bytes")
    _write(root, "small.txt", "s")
    _write(root, "large.txt", "x" * 300)
    monkeypatch.setenv(service.MAX_FILE_MB_ENV, "0.0001")
    summary = service.index_workspace("mixed")
    assert summary["added"] == 1 and summary["removed"] == 0
    assert [s["path"] for s in summary["skipped"]] == ["large.txt"]
    assert "limit" in summary["skipped"][0]["reason"]
    assert service.get_file(upload["file_id"]) is not None


def test_index_needs_a_folder():
    with pytest.raises(service.FileError):
        service.index_workspace("no-such-folder-here")


# ── over HTTP ────────────────────────────────────────────────────────────────

def test_index_over_http_answers_the_summary_and_leaves_an_audit_row():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.files as files_routes
    from common import audit

    root = _folder("httpws")
    _write(root, "answer.md", "42")
    app = FastAPI()
    app.include_router(files_routes.router)
    api = TestClient(app)
    resp = api.post("/api/files/index", params={"workspace": "httpws"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace"] == "httpws" and body["added"] == 1 and body["skipped"] == []
    listing = api.get("/api/files", params={"workspace": "httpws"}).json()
    assert [f["meta"]["path"] for f in listing["files"]] == ["answer.md"]
    fid = listing["files"][0]["file_id"]
    assert api.get(f"/api/files/{fid}/text").json()["text"] == "42"
    assert "file.index" in {row["action"] for row in audit.query(action="file.")["items"]}
    assert api.post("/api/files/index", params={"workspace": "nowhere-at-all"}).status_code == 400
    assert api.post("/api/files/index").status_code == 422
