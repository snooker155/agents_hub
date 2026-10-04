"""A file of the workspace folder is named by its registry id in an address
(files/service.py): the folder listing hands out the ids, the file routes of
routes/workspaces.py take ``file_id`` (``path`` stays for old links and for
folders), a path nothing registered yet gets an id on request, a delete
tombstones the records, and a chat link to a file the agent wrote carries
the id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from common.paths import WORKSPACES_ROOT  # noqa: E402
from files import service  # noqa: E402

WS = "ids"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.delenv(service.MAX_FILE_MB_ENV, raising=False)
    monkeypatch.delenv(service.MAX_WORKSPACE_MB_ENV, raising=False)


@pytest.fixture
def root() -> Path:
    folder = WORKSPACES_ROOT / WS
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import workspaces

    app = FastAPI()
    app.include_router(workspaces.router)
    return TestClient(app)


def _write(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── service ──────────────────────────────────────────────────────────────────

def test_folder_ids_and_path_of_round_trip(root):
    _write(root, "notes/a.md", "# A")
    rec = service.register_path(WS, "notes/a.md")
    upload = service.create_file(WS, "b.txt", b"b")
    assert service.folder_ids(WS) == {"notes/a.md": rec["file_id"]}
    assert service.folder_path_of(rec["file_id"], WS) == "notes/a.md"
    # A stored upload is not a file of the folder, and an id is bound to its workspace.
    assert service.folder_path_of(upload["file_id"], WS) is None
    assert service.folder_path_of(rec["file_id"], "other") is None
    assert service.folder_path_of("file_0000000000000000", WS) is None


def test_ensure_folder_record_registers_once_and_skips_hidden(root):
    _write(root, "report.md")
    first = service.ensure_folder_record(WS, "report.md")
    assert service.ensure_folder_record(WS, "report.md")["file_id"] == first["file_id"]
    _write(root, ".views/v.json")
    with pytest.raises(service.FileError):
        service.ensure_folder_record(WS, ".views/v.json")


def test_unregister_tree_tombstones_what_was_under_the_folder(root):
    _write(root, "out/a.txt")
    _write(root, "out/deep/b.txt")
    _write(root, "outside.txt")
    for rel in ("out/a.txt", "out/deep/b.txt", "outside.txt"):
        service.register_path(WS, rel)
    assert service.unregister_tree(WS, "out") == 2
    assert set(service.folder_ids(WS)) == {"outside.txt"}


# ── routes ───────────────────────────────────────────────────────────────────

def test_listing_carries_the_ids_and_content_reads_by_id(root, client):
    _write(root, "docs/plan.md", "# Plan")
    _write(root, "loose.txt", "never registered")
    rec = service.register_path(WS, "docs/plan.md")

    body = client.get(f"/api/workspaces/{WS}/files").json()
    assert body["ids"] == {"docs/plan.md": rec["file_id"]}
    assert "loose.txt" in body["files"]

    resp = client.get(f"/api/workspaces/{WS}/file-content", params={"file_id": rec["file_id"]})
    assert resp.status_code == 200
    assert resp.json()["path"] == "docs/plan.md" and resp.json()["content"] == "# Plan"
    raw = client.get(f"/api/workspaces/{WS}/file-raw", params={"file_id": rec["file_id"]})
    assert raw.status_code == 200 and raw.content == b"# Plan"
    # Old links with a path keep working.
    assert client.get(f"/api/workspaces/{WS}/file-content", params={"path": "loose.txt"}).status_code == 200


def test_an_unknown_or_foreign_id_is_404_and_nothing_is_400(root, client):
    other = WORKSPACES_ROOT / "elsewhere"
    other.mkdir(parents=True, exist_ok=True)
    _write(other, "secret.txt", "s")
    foreign = service.register_path("elsewhere", "secret.txt")
    for fid in (foreign["file_id"], "file_0000000000000000", "../secret.txt"):
        resp = client.get(f"/api/workspaces/{WS}/file-content", params={"file_id": fid})
        assert resp.status_code == 404, fid
    assert client.get(f"/api/workspaces/{WS}/file-content").status_code == 400


def test_file_id_route_registers_a_path_on_request(root, client):
    _write(root, "made-by-hand.csv", "a,b")
    resp = client.get(f"/api/workspaces/{WS}/file-id", params={"path": "made-by-hand.csv"})
    assert resp.status_code == 200
    fid = resp.json()["file_id"]
    assert service.folder_path_of(fid, WS) == "made-by-hand.csv"
    assert client.get(f"/api/workspaces/{WS}/file-id", params={"path": "missing.csv"}).status_code == 404
    _write(root, ".hidden/x.txt")
    assert client.get(f"/api/workspaces/{WS}/file-id", params={"path": ".hidden/x.txt"}).status_code == 400
    assert client.get(f"/api/workspaces/{WS}/file-id", params={"path": "../x"}).status_code == 400


def test_upload_answers_with_the_id(root, client):
    resp = client.post(f"/api/workspaces/{WS}/files/upload",
                       files={"file": ("up.txt", b"hello", "text/plain")}, data={"path": "in"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "in/up.txt"
    assert service.folder_path_of(body["file_id"], WS) == "in/up.txt"


def test_delete_by_id_tombstones_the_record(root, client):
    _write(root, "gone.txt")
    rec = service.register_path(WS, "gone.txt")
    resp = client.delete(f"/api/workspaces/{WS}/files", params={"file_id": rec["file_id"]})
    assert resp.status_code == 200 and resp.json()["path"] == "gone.txt"
    assert not (root / "gone.txt").exists()
    assert service.get_file(rec["file_id"]) is None
    assert client.get(f"/api/workspaces/{WS}/file-content",
                      params={"file_id": rec["file_id"]}).status_code == 404


def test_delete_of_a_folder_tombstones_its_files(root, client):
    _write(root, "tmp/a.txt")
    rec = service.register_path(WS, "tmp/a.txt")
    resp = client.delete(f"/api/workspaces/{WS}/files", params={"path": "tmp"})
    assert resp.status_code == 200 and resp.json()["type"] == "directory"
    assert service.get_file(rec["file_id"]) is None


# ── chat links ───────────────────────────────────────────────────────────────

def test_a_chat_link_to_a_registered_file_carries_its_id(root):
    from common.entity_links import entity_payloads

    _write(root, "src/api.py", "print(1)")
    rec = service.register_path(WS, "src/api.py")
    record = {"kind": "file", "id": "src/api.py", "action": "updated", "label": "src/api.py",
              "meta": {"workspace": WS}}
    item = entity_payloads([record])[0]
    assert item["url"] == f"/workspaces/{WS}?tab=files&file={rec['file_id']}"
    assert item["title"] == "src/api.py"


# ── memory pool files ────────────────────────────────────────────────────────

@pytest.fixture
def memory_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.memory as memory_routes

    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")
    app = FastAPI()
    app.include_router(memory_routes.router)
    return TestClient(app)


def test_pool_files_are_listed_indexed_and_deleted_by_id(root, memory_client):
    from memory.models import SharedMemory
    from memory.store import MemoryStore

    mem = MemoryStore().add(SharedMemory(name="kb", workspace=WS))
    up = memory_client.post(f"/api/shared-memory/{mem.id}/files/upload", data={"workspace": WS},
                            files={"file": ("leave.md", b"# Leave\n\n30 days a year.\n", "text/markdown")})
    assert up.status_code == 200
    fid = up.json()["file_id"]
    assert service.folder_path_of(fid, WS) == "knowledge/leave.md"
    _write(root, "knowledge/older.md", "# Older\n")  # put there before the registry followed it

    listed = {f["filename"]: f for f in memory_client.get(
        f"/api/shared-memory/{mem.id}/files", params={"workspace": WS}).json()["files"]}
    assert listed["leave.md"]["file_id"] == fid
    assert listed["older.md"]["file_id"]

    resp = memory_client.post(f"/api/shared-memory/{mem.id}/files/{fid}/index", params={"workspace": WS})
    assert resp.status_code == 200, resp.text
    assert resp.json()["filename"] == "leave.md"
    assert memory_client.delete(f"/api/shared-memory/{mem.id}/files/{fid}/index").json()["filename"] == "leave.md"
    # An id of a file outside the knowledge folder names nothing here.
    _write(root, "notes.md")
    other = service.register_path(WS, "notes.md")
    assert memory_client.post(f"/api/shared-memory/{mem.id}/files/{other['file_id']}/index",
                              params={"workspace": WS}).status_code == 404

    gone = memory_client.delete(f"/api/shared-memory/{mem.id}/files/{fid}", params={"workspace": WS})
    assert gone.status_code == 200 and gone.json()["deleted"] == "leave.md"
    assert not (root / "knowledge" / "leave.md").exists()
    assert service.get_file(fid) is None
    # A name from an old client still works.
    assert memory_client.delete(f"/api/shared-memory/{mem.id}/files/older.md",
                                params={"workspace": WS}).status_code == 200
