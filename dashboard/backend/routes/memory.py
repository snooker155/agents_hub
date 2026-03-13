"""
Shared memory related API routes.
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import Optional
from uuid import UUID

from tasks.storage import MemoryStore
from tasks.models import SharedMemory
from models import MemoryCreate, MemoryFileAdd, MemoryFileUpdate, MemoryFileProcess
from rag import process_rag, get_rag_status


router = APIRouter(prefix="/api/shared-memory", tags=["memory"])


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into semantic chunks by paragraph boundaries."""
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()] if text.strip() else []

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - overlap):
                    piece = para[i:i + chunk_size]
                    if piece.strip():
                        chunks.append(piece)
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks if chunks else ([text] if text.strip() else [])


def _persist_mem(store: MemoryStore, mem: SharedMemory) -> SharedMemory:
    """Save updated memory back to store."""
    mem.touch()
    memories = store.load()
    for i, m in enumerate(memories):
        if m.id == mem.id:
            memories[i] = mem
            break
    store.save(memories)
    return mem


def _mem_dump(mem: SharedMemory) -> dict:
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()


def _file_name(f) -> str:
    return f.get("name") if isinstance(f, dict) else f["name"]


# ---------------------------------------------------------------------------
# Pool CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_shared_memory(workspace: Optional[str] = None):
    store = MemoryStore()
    memories = store.load()
    if workspace:
        memories = [m for m in memories if (m.workspace or "") == workspace]
    return [_mem_dump(m) for m in memories]


@router.post("")
async def create_shared_memory(data: MemoryCreate):
    store = MemoryStore()
    mem = SharedMemory(name=data.name, description=data.description, type=data.type, workspace=data.workspace)
    store.add(mem)
    return _mem_dump(mem)


@router.get("/rag-config")
async def get_rag_config():
    """Return live RAG / vector-DB configuration status."""
    return get_rag_status()


@router.get("/rag-files")
async def get_all_rag_files():
    """Return all files that have been RAG-processed across every memory pool."""
    store = MemoryStore()
    result = []
    for mem in store.load():
        for f in mem.files:
            if isinstance(f, dict) and f.get("rag_status") not in (None, "raw"):
                result.append({**f, "memory_id": str(mem.id), "memory_name": mem.name})
    return result


@router.get("/{memory_id}")
async def get_shared_memory(memory_id: UUID):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _mem_dump(mem)


@router.delete("/{memory_id}")
async def delete_shared_memory(memory_id: UUID):
    store = MemoryStore()
    if not store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"message": "Memory deleted"}


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

@router.post("/{memory_id}/files")
async def add_memory_file(memory_id: UUID, data: MemoryFileAdd):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    mem.files.append({
        "name": data.name,
        "content": data.content,
        "rag_status": "raw",
        "rag_chunks": 0,
        "size_bytes": len(data.content.encode("utf-8")),
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    return _mem_dump(_persist_mem(store, mem))


@router.post("/{memory_id}/upload")
async def upload_memory_file(memory_id: UUID, file: UploadFile = File(...)):
    """Upload a UTF-8 text file into a memory pool."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    new_file = {
        "name": file.filename,
        "content": content,
        "rag_status": "raw",
        "rag_chunks": 0,
        "size_bytes": len(raw),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    # Replace if same name already exists
    mem.files = [f for f in mem.files if _file_name(f) != file.filename]
    mem.files.append(new_file)
    return _mem_dump(_persist_mem(store, mem))


@router.put("/{memory_id}/files/{file_name}")
async def update_memory_file(memory_id: UUID, file_name: str, data: MemoryFileUpdate):
    """Update the content of a file (resets RAG status)."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    found = False
    for i, f in enumerate(mem.files):
        if _file_name(f) == file_name:
            base = f if isinstance(f, dict) else {"name": file_name}
            mem.files[i] = {
                **base,
                "content": data.content,
                "size_bytes": len(data.content.encode("utf-8")),
                "rag_status": "raw",
                "rag_chunks": 0,
            }
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="File not found")
    return _mem_dump(_persist_mem(store, mem))


@router.delete("/{memory_id}/files/{file_name}")
async def delete_memory_file(memory_id: UUID, file_name: str):
    """Delete a specific file from a memory pool."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    new_files = [f for f in mem.files if _file_name(f) != file_name]
    if len(new_files) == len(mem.files):
        raise HTTPException(status_code=404, detail="File not found")

    mem.files = new_files
    _persist_mem(store, mem)
    return {"message": "File deleted"}


@router.post("/{memory_id}/files/{file_name}/process")
async def process_memory_file_rag(memory_id: UUID, file_name: str, data: MemoryFileProcess):
    """Chunk a file and mark it as RAG-indexed."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    found = False
    for i, f in enumerate(mem.files):
        if _file_name(f) == file_name:
            content = f.get("content") if isinstance(f, dict) else f["content"]
            chunks = _chunk_text(content, chunk_size=data.chunk_size, overlap=data.overlap)

            # Use a stable file_id for vector store deduplication
            file_id = f"{str(memory_id)}::{file_name}"
            rag_ok, rag_err, rag_meta = process_rag(chunks, file_id)

            base = f if isinstance(f, dict) else {"name": file_name, "content": content}
            mem.files[i] = {
                **base,
                "rag_status": "indexed" if rag_ok else "failed",
                "rag_chunks": len(chunks),
                "rag_chunk_size": data.chunk_size,
                "rag_overlap": data.overlap,
                "rag_processed_at": datetime.now(timezone.utc).isoformat(),
                "rag_error": rag_err or None,
                **rag_meta,
            }
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="File not found")
    return _mem_dump(_persist_mem(store, mem))
