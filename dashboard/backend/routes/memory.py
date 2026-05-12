"""
Shared memory related API routes.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
from uuid import UUID

from memory.store import MemoryStore
from memory.models import SharedMemory
from models import (
    MemoryCreate,
    MemoryNoteAdd, MemoryNoteUpdate,
    MemoryStructuredSlotUpsert,
)
from rag import get_rag_status, ingest_file
from common.paths import workspace_knowledge_dir


router = APIRouter(prefix="/api/shared-memory", tags=["memory"])


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

    # Clean up the per-pool episodes file so deleted pools don't leave orphaned data.
    try:
        from common.paths import pool_episodes_file
        ep_path = pool_episodes_file(str(memory_id))
        if ep_path.exists():
            ep_path.unlink()
        lock_path = ep_path.with_suffix(ep_path.suffix + ".lock")
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass

    # Same for the per-pool graph file.
    try:
        from common.paths import pool_graph_file
        g_path = pool_graph_file(str(memory_id))
        if g_path.exists():
            g_path.unlink()
        lock_path = g_path.with_suffix(g_path.suffix + ".lock")
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass

    return {"message": "Memory deleted"}


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@router.post("/{memory_id}/notes")
async def add_note(memory_id: UUID, data: MemoryNoteAdd):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    if data.title.startswith("journal:"):
        raise HTTPException(status_code=403, detail="Journal entries are read-only and cannot be created manually")
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
            if note.get("title", "").startswith("journal:"):
                raise HTTPException(status_code=403, detail="Journal entries are read-only")
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
    note = next((n for n in mem.notes if n.get("id") == note_id), None)
    if note and note.get("title", "").startswith("journal:"):
        raise HTTPException(status_code=403, detail="Journal entries are read-only")
    mem.notes = [n for n in mem.notes if n.get("id") != note_id]
    return _mem_dump(_persist_mem(store, mem))


# ---------------------------------------------------------------------------
# Structured slots
# ---------------------------------------------------------------------------

@router.get("/{memory_id}/structured")
async def list_structured_slots(memory_id: UUID):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    return {"slots": list(mem.structured_data.keys())}


@router.put("/{memory_id}/structured/{slot}")
async def upsert_structured_slot(memory_id: UUID, slot: str, data: MemoryStructuredSlotUpsert):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    mem.structured_data[slot] = data.data
    return _mem_dump(_persist_mem(store, mem))


@router.delete("/{memory_id}/structured/{slot}")
async def delete_structured_slot(memory_id: UUID, slot: str):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    if slot not in mem.structured_data:
        raise HTTPException(status_code=404, detail=f"Slot '{slot}' not found")
    del mem.structured_data[slot]
    return _mem_dump(_persist_mem(store, mem))


# ---------------------------------------------------------------------------
# Knowledge files — workspace-scoped uploads, pool-scoped indexing
# ---------------------------------------------------------------------------

def _rag_file_entry(mem: SharedMemory, filename: str) -> Optional[dict]:
    return next((f for f in mem.rag_files if f.get("filename") == filename), None)


@router.get("/{memory_id}/files")
async def list_rag_files(memory_id: UUID, workspace: str):
    """List all files in workspace knowledge dir, annotated with this pool's index status."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")

    kdir = workspace_knowledge_dir(workspace)
    disk_files = sorted(
        [p.name for p in kdir.iterdir() if p.is_file()],
        key=str.lower,
    ) if kdir.exists() else []

    indexed = {f["filename"]: f for f in mem.rag_files}
    result = []
    for name in disk_files:
        entry = indexed.get(name)
        result.append({
            "filename": name,
            "workspace": workspace,
            "status": entry["status"] if entry else "pending",
            "indexed_at": entry.get("indexed_at") if entry else None,
            "chunks": entry.get("chunks", 0) if entry else 0,
        })
    return {"files": result}


@router.post("/{memory_id}/files/upload")
async def upload_knowledge_file(
    memory_id: UUID,
    workspace: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a file to the workspace knowledge directory."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")

    kdir = workspace_knowledge_dir(workspace)
    dest = kdir / file.filename
    content = await file.read()
    dest.write_bytes(content)

    return {"filename": file.filename, "workspace": workspace, "status": "pending"}


@router.post("/{memory_id}/files/{filename}/index")
async def index_knowledge_file(memory_id: UUID, filename: str, workspace: str):
    """Chunk and embed a workspace knowledge file into this pool's vector store."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")

    kdir = workspace_knowledge_dir(workspace)
    path = kdir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in workspace knowledge dir")

    ok, err, chunk_count = ingest_file(path, str(memory_id))
    if not ok:
        raise HTTPException(status_code=500, detail=err)

    now = datetime.now(timezone.utc).isoformat()
    existing = _rag_file_entry(mem, filename)
    if existing:
        existing.update({"status": "indexed", "indexed_at": now, "chunks": chunk_count, "workspace": workspace})
    else:
        mem.rag_files.append({
            "filename": filename,
            "workspace": workspace,
            "status": "indexed",
            "indexed_at": now,
            "chunks": chunk_count,
        })
    _persist_mem(store, mem)
    return {"filename": filename, "status": "indexed", "chunks": chunk_count}


@router.delete("/{memory_id}/files/{filename}/index")
async def deindex_knowledge_file(memory_id: UUID, filename: str):
    """Remove a file's index entry from this pool (does not delete the file from disk)."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")

    mem.rag_files = [f for f in mem.rag_files if f.get("filename") != filename]
    _persist_mem(store, mem)
    return {"filename": filename, "status": "pending"}


@router.delete("/{memory_id}/files/{filename}")
async def delete_knowledge_file(memory_id: UUID, filename: str, workspace: str):
    """Delete a file from disk and remove it from this pool's index."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")

    kdir = workspace_knowledge_dir(workspace)
    path = kdir / filename
    if path.exists():
        path.unlink()

    mem.rag_files = [f for f in mem.rag_files if f.get("filename") != filename]
    _persist_mem(store, mem)
    return {"deleted": filename}


# ---------------------------------------------------------------------------
# Episodes — discrete event log scoped to a pool
# ---------------------------------------------------------------------------

@router.get("/{memory_id}/episodes")
async def list_episodes(
    memory_id: UUID,
    kind: Optional[str] = None,
    outcome: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
):
    """List recent episodes for a memory pool, optionally filtered."""
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")

    from memory.episodic import EpisodeStore
    eps = EpisodeStore(str(memory_id)).query(
        query=query,
        kind=kind,
        outcome=outcome,
        limit=max(1, min(int(limit), 200)),
    )
    return {
        "count": len(eps),
        "episodes": [e.model_dump() for e in eps],
    }


@router.get("/{memory_id}/episodes/stats")
async def episodes_stats(memory_id: UUID):
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.episodic import EpisodeStore
    return EpisodeStore(str(memory_id)).stats()


@router.delete("/{memory_id}/episodes/{episode_id}")
async def delete_episode(memory_id: UUID, episode_id: UUID):
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.episodic import EpisodeStore
    if not EpisodeStore(str(memory_id)).delete(episode_id):
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"deleted": str(episode_id)}


# ---------------------------------------------------------------------------
# Graph — knowledge graph scoped to a pool
# ---------------------------------------------------------------------------

@router.get("/{memory_id}/graph")
async def get_graph(memory_id: UUID):
    """Return the full graph for visualization (nodes + edges)."""
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.graph import GraphStore
    nodes, edges = GraphStore(str(memory_id)).load()
    return {
        "nodes": [n.model_dump() for n in nodes],
        "edges": [e.model_dump() for e in edges],
    }


@router.get("/{memory_id}/graph/stats")
async def graph_stats(memory_id: UUID):
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.graph import GraphStore
    return GraphStore(str(memory_id)).stats()


@router.post("/{memory_id}/graph/link")
async def graph_link(memory_id: UUID, payload: dict):
    """Create or merge an edge between two entities.

    Body: {"source": {"type", "name", "properties"?},
           "target": {"type", "name", "properties"?},
           "relation": str, "edge_properties"?: {}, "weight"?: float}
    """
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.graph import GraphStore
    src = payload.get("source") or {}
    tgt = payload.get("target") or {}
    relation = payload.get("relation")
    if not src.get("type") or not src.get("name") or not tgt.get("type") or not tgt.get("name") or not relation:
        raise HTTPException(status_code=400, detail="source/target {type,name} and relation are required")
    g = GraphStore(str(memory_id))
    src_node, src_mode = g.upsert_node(src["type"], src["name"], src.get("properties"))
    tgt_node, tgt_mode = g.upsert_node(tgt["type"], tgt["name"], tgt.get("properties"))
    edge, edge_mode = g.add_edge(
        src_node.id,
        tgt_node.id,
        relation,
        properties=payload.get("edge_properties"),
        weight=payload.get("weight"),
    )
    return {
        "source": {**src_node.model_dump(), "mode": src_mode},
        "target": {**tgt_node.model_dump(), "mode": tgt_mode},
        "edge": {**edge.model_dump(), "mode": edge_mode},
    }


@router.delete("/{memory_id}/graph/nodes/{node_id}")
async def delete_graph_node(memory_id: UUID, node_id: UUID):
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.graph import GraphStore
    if not GraphStore(str(memory_id)).delete_node(node_id):
        raise HTTPException(status_code=404, detail="Node not found")
    return {"deleted": str(node_id)}


@router.delete("/{memory_id}/graph/edges/{edge_id}")
async def delete_graph_edge(memory_id: UUID, edge_id: UUID):
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.graph import GraphStore
    if not GraphStore(str(memory_id)).delete_edge(edge_id):
        raise HTTPException(status_code=404, detail="Edge not found")
    return {"deleted": str(edge_id)}


@router.post("/{memory_id}/graph/extract")
async def graph_extract(memory_id: UUID, payload: dict):
    """Run LLM-based extraction over a chunk of text and persist the triples.

    Body: {"text": str}. Synchronous; returns counts.
    """
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    text = (payload or {}).get("text") or ""
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    from memory.graph_extract import extract_and_persist
    return extract_and_persist(str(memory_id), text)
