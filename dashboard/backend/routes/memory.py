"""
Shared memory related API routes.
"""
from fastapi import APIRouter, HTTPException
from uuid import UUID

from tasks.storage import MemoryStore
from tasks.models import SharedMemory
from models import MemoryCreate, MemoryFileAdd


router = APIRouter(prefix="/api/shared-memory", tags=["memory"])


@router.get("")
async def list_shared_memory():
    store = MemoryStore()
    memories = store.load()
    return [m.model_dump() if hasattr(m, "model_dump") else m.dict() for m in memories]


@router.post("")
async def create_shared_memory(data: MemoryCreate):
    store = MemoryStore()
    mem = SharedMemory(name=data.name, description=data.description, type=data.type)
    store.add(mem)
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()


@router.get("/{memory_id}")
async def get_shared_memory(memory_id: UUID):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()


@router.delete("/{memory_id}")
async def delete_shared_memory(memory_id: UUID):
    store = MemoryStore()
    found = store.delete(memory_id)
    if not found:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"message": "Memory deleted"}


@router.post("/{memory_id}/files")
async def add_memory_file(memory_id: UUID, data: MemoryFileAdd):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    mem.files.append({"name": data.name, "content": data.content})
    mem.touch()

    # Update in store
    memories = store.load()
    for i, m in enumerate(memories):
        if m.id == mem.id:
            memories[i] = mem
            break
    store.save(memories)
    return mem.model_dump() if hasattr(mem, "model_dump") else mem.dict()
