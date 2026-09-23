"""Deletion and reindexing, through the routes (dashboard/backend/routes/memory.py):

``POST /api/shared-memory/{id}/files/{filename}/index`` indexes a file,
``POST /api/shared-memory/{id}/rag/reindex`` re-chunks and re-embeds one file
or a whole pool, skipping a file whose content has not changed unless
``force=true``, and ``DELETE .../files/{filename}`` removes a file and its
index entirely. These tests exercise all three against the real chunk store
(``rag_chunks``), the way ``tests/test_memory_stores.py`` and
``tests/test_record_access.py`` exercise the rest of the memory routes: no
vector store configured, so this is the BM25-only path end to end, and the
one behaviour the audit called out by name — an edited file that shrinks
leaves no stale chunk behind.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from rag.chunk_store import pool_chunks  # noqa: E402
from common.paths import workspace_knowledge_dir  # noqa: E402

WORKSPACE = "default"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture(autouse=True)
def _no_vector_store(monkeypatch):
    """Importing dashboard.backend.main loads the repo's own .env, which
    defaults to RAG_VECTOR_DB=chroma / RAG_EMBEDDING_PROVIDER=sentence-
    transformers. These tests exercise the chunk-store/BM25 path that works
    with neither installed, so force both off regardless of what .env says."""
    monkeypatch.setenv("RAG_VECTOR_DB", "none")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "none")


@pytest.fixture
def pool_id(client):
    resp = client.post("/api/shared-memory", json={"name": "rag test pool", "workspace": WORKSPACE})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _write_knowledge_file(name: str, content: str) -> Path:
    kdir = workspace_knowledge_dir(WORKSPACE)
    path = kdir / name
    path.write_text(content, encoding="utf-8")
    return path


def _index(client, pool_id, filename):
    resp = client.post(
        f"/api/shared-memory/{pool_id}/files/{filename}/index",
        params={"workspace": WORKSPACE},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _list_files(client, pool_id):
    resp = client.get(f"/api/shared-memory/{pool_id}/files", params={"workspace": WORKSPACE})
    assert resp.status_code == 200, resp.text
    return {f["filename"]: f for f in resp.json()["files"]}


def _reindex(client, pool_id, **params):
    resp = client.post(f"/api/shared-memory/{pool_id}/rag/reindex",
                        params={"workspace": WORKSPACE, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── indexing ─────────────────────────────────────────────────────────────────

def test_indexing_a_file_writes_chunk_rows(client, pool_id):
    _write_knowledge_file("guide.md", "# Guide\n\nSome content about widgets and gadgets.\n")
    result = _index(client, pool_id, "guide.md")
    assert result["chunks"] >= 1
    assert result["status"] == "indexed"

    stored = pool_chunks(str(pool_id))
    assert len(stored) == result["chunks"]
    assert stored[0]["filename"] == "guide.md"


def test_the_file_list_reports_chunk_count_and_content_hash(client, pool_id):
    _write_knowledge_file("notes.md", "Some notes about falcons and kestrels.\n")
    _index(client, pool_id, "notes.md")

    files = _list_files(client, pool_id)
    assert files["notes.md"]["status"] == "indexed"
    assert files["notes.md"]["chunks"] >= 1
    assert files["notes.md"]["content_hash"]


# ── reindexing: unchanged files are skipped ──────────────────────────────────

def test_reindexing_an_unchanged_file_is_skipped(client, pool_id):
    _write_knowledge_file("stable.md", "Content that never changes.\n")
    _index(client, pool_id, "stable.md")

    result = _reindex(client, pool_id, filename="stable.md")
    assert result["results"][0]["ok"] is True
    assert result["results"][0]["skipped"] is True


def test_force_reindexes_even_an_unchanged_file(client, pool_id):
    _write_knowledge_file("stable2.md", "Content that never changes either.\n")
    _index(client, pool_id, "stable2.md")

    result = _reindex(client, pool_id, filename="stable2.md", force="true")
    assert result["results"][0]["ok"] is True
    assert result["results"][0]["skipped"] is False


def test_an_edited_file_is_reindexed_and_the_hash_changes(client, pool_id):
    _write_knowledge_file("edit.md", "Version one of the document.\n")
    _index(client, pool_id, "edit.md")
    before = _list_files(client, pool_id)["edit.md"]["content_hash"]

    _write_knowledge_file("edit.md", "Version two of the document, now longer than before.\n")
    result = _reindex(client, pool_id, filename="edit.md")
    assert result["results"][0]["ok"] is True
    assert result["results"][0]["skipped"] is False

    after = _list_files(client, pool_id)["edit.md"]["content_hash"]
    assert after != before


# ── the shrinking-file guarantee ─────────────────────────────────────────────

def test_a_shrinking_file_leaves_no_stale_chunk_behind(client, pool_id):
    long_text = "\n\n".join(f"Paragraph {i} with some unique filler content." for i in range(20))
    _write_knowledge_file("shrink.md", long_text)
    first = _index(client, pool_id, "shrink.md")
    assert first["chunks"] > 1

    _write_knowledge_file("shrink.md", "Just one short paragraph now.\n")
    result = _reindex(client, pool_id, filename="shrink.md")
    new_count = result["results"][0]["chunks"]
    assert new_count < first["chunks"]

    stored = pool_chunks(str(pool_id))
    assert len(stored) == new_count
    assert all(c["filename"] == "shrink.md" for c in stored)


# ── whole-pool reindex ───────────────────────────────────────────────────────

def test_reindexing_the_whole_pool_covers_every_indexed_file(client, pool_id):
    _write_knowledge_file("a.md", "Content of file a.\n")
    _write_knowledge_file("b.md", "Content of file b.\n")
    _index(client, pool_id, "a.md")
    _index(client, pool_id, "b.md")

    result = _reindex(client, pool_id)
    names = {r["filename"] for r in result["results"]}
    assert names == {"a.md", "b.md"}
    assert all(r["ok"] for r in result["results"])


# ── deletion ─────────────────────────────────────────────────────────────────

def test_deleting_a_file_removes_its_chunks(client, pool_id):
    _write_knowledge_file("gone.md", "This file will be deleted.\n")
    _index(client, pool_id, "gone.md")
    assert len(pool_chunks(str(pool_id))) >= 1

    resp = client.delete(f"/api/shared-memory/{pool_id}/files/gone.md", params={"workspace": WORKSPACE})
    assert resp.status_code == 200, resp.text

    stored = pool_chunks(str(pool_id))
    assert all(c["filename"] != "gone.md" for c in stored)


def test_deindexing_a_file_removes_its_chunks_but_keeps_it_on_disk(client, pool_id):
    path = _write_knowledge_file("keep.md", "This file stays on disk.\n")
    _index(client, pool_id, "keep.md")

    resp = client.delete(f"/api/shared-memory/{pool_id}/files/keep.md/index")
    assert resp.status_code == 200, resp.text

    assert path.exists()
    stored = pool_chunks(str(pool_id))
    assert all(c["filename"] != "keep.md" for c in stored)
