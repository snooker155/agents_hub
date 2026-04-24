"""
Shared memory related API routes.
"""
import asyncio
import json
import re
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from typing import Optional
from uuid import UUID

from memory.store import MemoryStore
from memory.models import SharedMemory
from models import (
    MemoryCreate, MemoryFileAdd, MemoryFileUpdate, MemoryFileProcess,
    MemoryNoteAdd, MemoryNoteUpdate, MemoryKVAdd, MemoryKVUpdate,
)
from rag import process_rag, get_rag_status
from rag.service import process_rag_with_progress


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


@router.get("/{memory_id}/files/{file_name}/process-stream")
async def process_memory_file_rag_stream(
    memory_id: UUID,
    file_name: str,
    chunk_size: int = 500,
    overlap: int = 50,
):
    """Stream RAG processing progress as Server-Sent Events."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    found_file = next((f for f in mem.files if _file_name(f) == file_name), None)
    if not found_file:
        raise HTTPException(status_code=404, detail="File not found")

    content = found_file.get("content", "") if isinstance(found_file, dict) else found_file["content"]
    chunks = _chunk_text(content, chunk_size=chunk_size, overlap=overlap)
    file_id = f"{str(memory_id)}::{file_name}"

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()

    def _on_event(event: dict) -> None:
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    def _run_processing() -> None:
        try:
            process_rag_with_progress(chunks, file_id, _on_event)
        except Exception as exc:
            _on_event({"type": "error", "message": str(exc)})

    thread = threading.Thread(target=_run_processing, daemon=True)
    thread.start()

    async def _generate():
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=300)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Processing timed out'})}\n\n"
                break

            yield f"data: {json.dumps(event)}\n\n"

            if event.get("type") in ("done", "error"):
                # Persist result to memory store
                rag_ok = event["type"] == "done"
                rag_err = event.get("message") if not rag_ok else None
                rag_meta = {k: v for k, v in event.items() if k != "type"}

                # Reload store to get latest state (avoid races with other writes)
                fresh_store = MemoryStore()
                fresh_mem = fresh_store.get(memory_id)
                if fresh_mem:
                    for i, f in enumerate(fresh_mem.files):
                        if _file_name(f) == file_name:
                            base = f if isinstance(f, dict) else {"name": file_name, "content": content}
                            fresh_mem.files[i] = {
                                **base,
                                "rag_status": "indexed" if rag_ok else "failed",
                                "rag_chunks": len(chunks),
                                "rag_chunk_size": chunk_size,
                                "rag_overlap": overlap,
                                "rag_processed_at": datetime.now(timezone.utc).isoformat(),
                                "rag_error": rag_err or None,
                                **rag_meta,
                            }
                            break
                    _persist_mem(fresh_store, fresh_mem)
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@router.post("/{memory_id}/notes")
async def add_note(memory_id: UUID, data: MemoryNoteAdd):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from uuid import uuid4
    note = {
        "id": str(uuid4()),
        "title": data.title,
        "content": data.content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    mem.notes.append(note)
    return _mem_dump(_persist_mem(store, mem))


@router.put("/{memory_id}/notes/{note_id}")
async def update_note(memory_id: UUID, note_id: str, data: MemoryNoteUpdate):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    for note in mem.notes:
        if note.get("id") == note_id:
            if data.title is not None:
                note["title"] = data.title
            if data.content is not None:
                note["content"] = data.content
            return _mem_dump(_persist_mem(store, mem))
    raise HTTPException(status_code=404, detail="Note not found")


@router.delete("/{memory_id}/notes/{note_id}")
async def delete_note(memory_id: UUID, note_id: str):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    mem.notes = [n for n in mem.notes if n.get("id") != note_id]
    return _mem_dump(_persist_mem(store, mem))


# ---------------------------------------------------------------------------
# Key-Value pairs
# ---------------------------------------------------------------------------

@router.post("/{memory_id}/kv")
async def add_kv(memory_id: UUID, data: MemoryKVAdd):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    if any(kv["key"] == data.key for kv in mem.kv_pairs):
        raise HTTPException(status_code=409, detail=f"Key '{data.key}' already exists")
    mem.kv_pairs.append({"key": data.key, "value": data.value, "description": data.description})
    return _mem_dump(_persist_mem(store, mem))


@router.put("/{memory_id}/kv/{key}")
async def update_kv(memory_id: UUID, key: str, data: MemoryKVUpdate):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    for kv in mem.kv_pairs:
        if kv["key"] == key:
            if data.value is not None:
                kv["value"] = data.value
            if data.description is not None:
                kv["description"] = data.description
            return _mem_dump(_persist_mem(store, mem))
    raise HTTPException(status_code=404, detail="Key not found")


@router.delete("/{memory_id}/kv/{key}")
async def delete_kv(memory_id: UUID, key: str):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    mem.kv_pairs = [kv for kv in mem.kv_pairs if kv["key"] != key]
    return _mem_dump(_persist_mem(store, mem))
