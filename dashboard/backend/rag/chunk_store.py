"""
The chunk store: the source of truth for what a pool's indexed files contain.

The vector store (``vector_store.py``) has only ever held ids and vectors —
there was nowhere durable holding the chunk text itself, its structure
(headings, character offsets) or a way to tell whether a file had actually
changed since it was last indexed. ``rag_chunks`` (migration 0011) is that
place: one row per chunk of one indexed file, keyed by ``(file_id,
chunk_index)`` the same way the vector store keys its own points, so the two
stay in step. Keyword search (``bm25.py``) reads only from here.

``content_hash`` is a sha256 of the whole file at index time, the same value
for every chunk of one version — it is what lets a reindex skip a file that
has not changed instead of re-chunking and re-embedding it for nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from common import db

from .chunking import Chunk


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_content_hash(file_id: str) -> Optional[str]:
    """The content_hash stored for *file_id*'s chunks, or None if it has none
    (never indexed, or indexed before this table existed and not reindexed
    since)."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT content_hash FROM rag_chunks WHERE file_id = ? LIMIT 1", (file_id,)
    ).fetchone()
    return row["content_hash"] if row else None


def replace_file_chunks(
    pool_id: str,
    filename: str,
    file_id: str,
    chunks: Sequence[Chunk],
    content_hash: str,
) -> int:
    """Atomically swap *file_id*'s chunk rows for *chunks*.

    Delete-then-insert inside one transaction, so a concurrent keyword search
    never reads a mix of the old chunk set and the new one — the vector side
    gets the same guarantee from its deterministic per-index ids (see
    ``vector_store.chunk_id``) and its own tail-drop on upsert. Returns the
    number of rows written.
    """
    now = _now()
    with db.transaction() as conn:
        conn.execute("DELETE FROM rag_chunks WHERE file_id = ?", (file_id,))
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO rag_chunks (pool_id, file_id, filename, chunk_index, text, "
                "heading_path, content_hash, char_start, char_end, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pool_id, file_id, filename, i, chunk.text,
                    db.dumps(list(chunk.heading_path or [])), content_hash,
                    int(chunk.char_start), int(chunk.char_end), now,
                ),
            )
    return len(chunks)


def delete_file_chunks(file_id: str) -> int:
    """Remove every chunk of one indexed file. Returns the number removed."""
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM rag_chunks WHERE file_id = ?", (file_id,))
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def delete_pool_chunks(pool_id: str) -> int:
    """Remove every chunk a pool ever indexed. Returns the number removed."""
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM rag_chunks WHERE pool_id = ?", (pool_id,))
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def get_chunk(file_id: str, chunk_index: int) -> Optional[dict]:
    """One chunk row, or None. Used to enrich a vector-only hybrid-search hit
    (heading_path, filename) that BM25 did not also return."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT file_id, filename, chunk_index, text, heading_path, content_hash, "
        "char_start, char_end FROM rag_chunks WHERE file_id = ? AND chunk_index = ?",
        (file_id, int(chunk_index)),
    ).fetchone()
    return _row_to_dict(row) if row else None


def pool_chunks(pool_id: str) -> List[dict]:
    """Every chunk of every file a pool has indexed, in file/index order —
    the corpus ``bm25.py`` builds its index over."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT file_id, filename, chunk_index, text, heading_path, content_hash, "
        "char_start, char_end FROM rag_chunks WHERE pool_id = ? "
        "ORDER BY file_id, chunk_index",
        (pool_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def file_chunk_count(file_id: str) -> int:
    conn = db.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM rag_chunks WHERE file_id = ?", (file_id,)
    ).fetchone()
    return int(row["c"]) if row else 0


def pool_file_index(pool_id: str) -> Dict[str, dict]:
    """``{filename: {"chunks": n, "content_hash": h}}`` for every file this
    pool currently has chunk rows for — what the file-list endpoint reports
    alongside the workspace-knowledge-dir bookkeeping."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT filename, content_hash, COUNT(*) AS c FROM rag_chunks "
        "WHERE pool_id = ? GROUP BY filename, content_hash",
        (pool_id,),
    ).fetchall()
    return {
        r["filename"]: {"chunks": int(r["c"]), "content_hash": r["content_hash"]}
        for r in rows
    }


def _row_to_dict(row) -> dict:
    return {
        "file_id": row["file_id"],
        "filename": row["filename"],
        "chunk_index": int(row["chunk_index"]),
        "text": row["text"],
        "heading_path": db.loads(row["heading_path"], []),
        "content_hash": row["content_hash"],
        "char_start": int(row["char_start"]),
        "char_end": int(row["char_end"]),
    }
