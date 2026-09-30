-- 0011: rag_chunks, the source of truth for a pool's indexed chunk text
-- (dashboard/backend/rag/chunk_store.py, dashboard/backend/rag/chunking.py,
-- dashboard/backend/rag/bm25.py).
--
-- The vector store (chroma, pinecone, qdrant) has only ever held ids and
-- vectors, nothing readable back out. This table is what keyword search
-- reads, what a reindex compares content_hash against to skip an unchanged
-- file, and what a deletion clears alongside the vectors. One row per chunk
-- of one indexed file, keyed the same way the vector store keys its own
-- points: (file_id, chunk_index), so the two stay in step.
CREATE TABLE IF NOT EXISTS rag_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_id      TEXT NOT NULL,
    file_id      TEXT NOT NULL,        -- {pool_id}::{filename}, matches the vector store
    filename     TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    heading_path TEXT,                 -- JSON list of headings this chunk sits under
    content_hash TEXT NOT NULL,        -- sha256 of the whole file at index time, shared by every chunk of one version
    char_start   INTEGER NOT NULL,
    char_end     INTEGER NOT NULL,
    created_at   TEXT,
    UNIQUE (file_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_pool ON rag_chunks(pool_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_file ON rag_chunks(file_id);
