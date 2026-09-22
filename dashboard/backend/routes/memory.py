"""
Shared memory related API routes.
"""
from datetime import datetime, timezone

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from uuid import UUID

from memory.store import MemoryStore
from memory.models import SharedMemory
from models import (
    MemoryCreate,
    MemoryNoteAdd, MemoryNoteUpdate,
    MemoryStructuredSlotUpsert,
)
from rag import get_rag_status, ingest_file, delete_file_vectors, delete_pool_vectors
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
    return mem.model_dump()


def _unlink_graph_mirror(memory_id: UUID, node_type: str, name: str) -> None:
    """Delete the graph mirror node (`slot`/`note`) that `remember` created for a
    slot or note. Mirrors the `forget` tool so UI deletes don't leave orphans.
    Best-effort: a graph problem must not break the slot/note deletion itself.
    """
    try:
        from memory.graph import GraphStore
        gstore = GraphStore(str(memory_id))
        node = gstore.get_node(node_type, name)
        if node is not None:
            gstore.delete_node(node.id)
    except Exception:
        pass


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


# ── The Memory Agent's chat ──────────────────────────────────────────────────
#
# Page-level rather than per-pool, because the agent's pool is resolved from the
# workspace: a chat pinned to the row you clicked would be lying about which
# pool the tools actually write to. The prompt names the resolved pool, and the
# agent is told to say which one it is working with.

MEMORY_AGENT_ID = "memory_extractor"
MEMORY_CHAT_KIND = "memory"


class MemoryChatIn(BaseModel):
    message: str = ""


def _memory_chat_id(workspace: Optional[str], memory_id: Optional[str] = None) -> str:
    """One conversation per pool, falling back to one per workspace.

    Keyed on the pool because the conversation is *about* that pool: a question
    asked while pool A is open should not come back under pool B. With no pool
    open there is nothing to be about yet, so the workspace holds the thread.
    """
    if memory_id:
        return f"pool:{str(memory_id).strip()}"
    return f"ws:{(workspace or 'default').strip() or 'default'}"


def _pool_card(memory_id: Optional[str]) -> List[Dict[str, Any]]:
    """The pool the page has open, as the prompt wants to see it."""
    if not memory_id:
        return []
    mem = MemoryStore().get(str(memory_id))
    return [{
        "memory_id": str(memory_id),
        "name": getattr(mem, "name", "") if mem else "",
        "description": getattr(mem, "description", "") if mem else "",
        "exists": mem is not None,
        "source": "open on the page",
    }]


def _bound_pools(workspace: Optional[str]) -> List[Dict[str, Any]]:
    """The pools the agent's tools are bound to when no pool is open.

    This is the record-and-workspace assignment, which is what the agent gets
    everywhere else in the product.
    """
    from agents.registry import get_agent
    from memory.binding import effective_memory_pools

    # Best effort: an unreadable registry means "no binding to report", not a
    # failed page. The prompt already handles the empty case by telling the
    # agent to say so rather than pretend.
    try:
        spec = get_agent(MEMORY_AGENT_ID)
        pool_ids = effective_memory_pools(spec, workspace) if spec else []
    except Exception:
        return []
    store = MemoryStore()
    out: List[Dict[str, Any]] = []
    for pool_id in pool_ids:
        mem = store.get(pool_id)
        out.append({
            "memory_id": str(pool_id),
            "name": getattr(mem, "name", "") if mem else "",
            "description": getattr(mem, "description", "") if mem else "",
            "exists": mem is not None,
        })
    return out


def _memory_chat_prompt(workspace: str, pools: List[Dict[str, Any]],
                        history: List[dict], user_message: str) -> str:
    """One turn's prompt: which pool is bound, then the talk."""
    from chat.entity_chat import transcript_block

    parts = [
        "You are on the Memory page of this platform. The user is looking at the "
        "pools in this workspace.",
        "",
        f"Workspace: {workspace}",
        "",
        "=== The pool(s) your tools are bound to ===",
        json.dumps(pools, ensure_ascii=False, indent=2),
        "",
        "Rules for this conversation:",
        "- Name the pool you are working with in your first answer. The page "
        "lists several; your tools read and write the one above, and the user "
        "cannot tell which from the conversation alone.",
        "- When a pool is open on the page, your tools are bound to THAT pool "
        "for this turn, whatever the agent's configured assignment says. The "
        "question is being asked about what is on screen.",
        "- A question about what is known is answered by reading. Do not run "
        "extraction to answer a question: it would store the question.",
        "- Before proposing an extraction, search for what the pool already "
        "holds. The same fact stored three ways is how a pool degrades.",
        "- You cannot open files. If the user points at a document rather than "
        "pasting it, say so and ask for the text.",
        "- Editing and deleting existing entries happens on the page, not "
        "through you. Say that instead of trying.",
    ]
    if not pools:
        parts.insert(6, (
            "!! No pool is assigned to you in this workspace, so your memory "
            "tools have nothing to work on. Say exactly that: the user needs to "
            "assign one from the agent's Memory tab. Do not pretend to read or "
            "store anything."
        ))
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


@router.get("/chat")
async def get_memory_chat(workspace: Optional[str] = Query(None),
                          memory_id: Optional[str] = Query(None)):
    """The Memory Agent's transcript for the open pool, plus the replay trace."""
    from common.entity_chat_store import entity_chat_store

    chat_id = _memory_chat_id(workspace, memory_id)
    chat_store = entity_chat_store()
    return {
        "messages": chat_store.get_messages(MEMORY_CHAT_KIND, chat_id),
        "trace": chat_store.get_trace(MEMORY_CHAT_KIND, chat_id),
        # What the session picker needs to reach this chat's history
        # (routes/entity_chats.py); the browser never builds the key itself.
        "chat_ref": {"kind": MEMORY_CHAT_KIND, "id": chat_id},
        "pools": _pool_card(memory_id) or _bound_pools(workspace),
    }


@router.delete("/chat")
async def clear_memory_chat(workspace: Optional[str] = Query(None),
                            memory_id: Optional[str] = Query(None)):
    """Clear the transcript and start a fresh session. No memory is touched."""
    from common.entity_chat_store import entity_chat_store

    epoch = entity_chat_store().clear(MEMORY_CHAT_KIND,
                                      _memory_chat_id(workspace, memory_id),
                                      new_session=True)
    return {"cleared": True, "session_epoch": epoch}


@router.post("/chat")
async def chat_memory(payload: MemoryChatIn,
                      workspace: Optional[str] = Query(None),
                      memory_id: Optional[str] = Query(None)):
    """Run one turn of the Memory Agent chat (SSE).

    Streams the agent's ``tool_*`` / ``thinking`` / ``token`` events, then a
    ``memory`` event so the page can refresh what the pool holds, the final
    ``message`` and ``done``.
    """
    from chat.entity_chat import (
        EntityChatSpec, RecordingQueue, SSE_HEADERS, guarded, relay_queue,
        run_entity_chat_turn, spawn_detached, sse,
    )
    from common.bootstrap import ensure_system_agent

    if not ensure_system_agent(MEMORY_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{MEMORY_AGENT_ID}' agent is not registered")
    user_message = (payload.message or "").strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    ws = (workspace or "default").strip() or "default"
    chat_id = _memory_chat_id(workspace, memory_id)
    pools = _pool_card(memory_id) or _bound_pools(workspace)

    spec = EntityChatSpec(
        kind=MEMORY_CHAT_KIND,
        agent_id=MEMORY_AGENT_ID,
        title=f"{pools[0]['name'] or ws if pools else ws} · memory",
        workspace=ws,
        # Bind the agent to the pool the user has open, so the question is
        # answered from what they are looking at rather than from whatever the
        # record happens to be assigned. Reaches the agent cache key, so two
        # pools are two cached agents.
        agent_overrides={"memory_pool": memory_id} if memory_id else {},
    )

    async def run_turn(queue: asyncio.Queue):
        from common.workspace_context import _workspace_ctx

        # The pool binding is resolved per workspace, so this ContextVar decides
        # which pool the agent's tools write to.
        _workspace_ctx.set(ws)

        await run_entity_chat_turn(
            queue, spec, chat_id, user_message,
            lambda history: _memory_chat_prompt(ws, pools, history, user_message),
        )
        await queue.put({"type": "memory", "pools": pools})

    async def event_stream():
        queue = RecordingQueue()
        yield sse({"type": "meta", "kind": MEMORY_CHAT_KIND, "id": chat_id})
        worker = spawn_detached(guarded(run_turn, queue))
        async for frame in relay_queue(queue):
            yield frame
        await worker

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.post("/chat/stop")
async def stop_memory_chat(workspace: Optional[str] = Query(None),
                           memory_id: Optional[str] = Query(None)):
    """Stop the in-flight Memory Agent turn for this pool."""
    from chat.entity_chat import cancel_entity_runs

    cancelled = cancel_entity_runs(MEMORY_CHAT_KIND,
                                   _memory_chat_id(workspace, memory_id))
    return {"stopped": cancelled > 0, "cancelled": cancelled}


# Declared before the ``/{memory_id}`` routes below: "chat" is a literal path,
# and a catch-all declared first would read it as a memory id (the same reason
# ``/rag-config`` sits above them).
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

    # And the pool's vectors: a deleted pool whose chunks stay in the store
    # keeps answering searches from a pool that no longer exists.
    try:
        delete_pool_vectors(str(memory_id))
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
    result = _mem_dump(_persist_mem(store, mem))
    if note and note.get("title"):
        _unlink_graph_mirror(memory_id, "note", note["title"])
    return result


# ---------------------------------------------------------------------------
# Core memory blocks — the always-in-context layer
# ---------------------------------------------------------------------------

class MemoryBlockUpsert(BaseModel):
    """Body of a block PUT. Unset fields keep their current value."""

    value: Optional[str] = None
    limit_chars: Optional[int] = None
    description: Optional[str] = None
    read_only: Optional[bool] = None


@router.get("/{memory_id}/blocks")
async def list_memory_blocks(memory_id: UUID):
    """The pool's blocks, exactly as they are rendered into a system prompt."""
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    return {"blocks": [b.model_dump() for b in mem.blocks]}


@router.put("/{memory_id}/blocks/{name}")
async def upsert_memory_block(memory_id: UUID, name: str, data: MemoryBlockUpsert):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")

    block = mem.get_block(name)
    if block is not None and block.read_only and data.read_only is not False:
        raise HTTPException(status_code=403, detail=f"Block '{block.name}' is read-only")

    limit = data.limit_chars if data.limit_chars is not None else (
        block.limit_chars if block else None
    )
    if data.value is not None and limit is not None and len(data.value) > limit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Value is {len(data.value)} chars, {len(data.value) - limit} over the "
                f"block's {limit} char limit"
            ),
        )

    mem.upsert_block(
        name,
        data.value,
        limit_chars=data.limit_chars,
        description=data.description,
        read_only=data.read_only,
    )
    return _mem_dump(_persist_mem(store, mem))


@router.delete("/{memory_id}/blocks/{name}")
async def delete_memory_block(memory_id: UUID, name: str):
    store = MemoryStore()
    mem = store.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory pool not found")
    block = mem.get_block(name)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block '{name}' not found")
    if block.read_only:
        raise HTTPException(status_code=403, detail=f"Block '{block.name}' is read-only")
    mem.blocks = [b for b in mem.blocks if b.name != block.name]
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
    result = _mem_dump(_persist_mem(store, mem))
    _unlink_graph_mirror(memory_id, "slot", slot)
    return result


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
    # De-indexing means the chunks go too: leaving them made "pending" a lie,
    # the file kept answering searches with no entry saying why.
    ok, err, meta = delete_file_vectors(str(memory_id), filename)
    return {"filename": filename, "status": "pending", "vectors_deleted": meta if ok else None,
            "vector_error": err or None}


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
    ok, err, meta = delete_file_vectors(str(memory_id), filename)
    return {"deleted": filename, "vectors_deleted": meta if ok else None,
            "vector_error": err or None}


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


@router.post("/{memory_id}/graph/prune")
async def prune_graph_mirrors(memory_id: UUID, dry_run: bool = False):
    """Remove `slot`/`note` mirror nodes whose backing slot/note no longer exists.

    Reconciles the graph after slot/note deletes. Typed entity nodes are never
    touched. Pass `?dry_run=true` to preview what would be removed.
    """
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.maintenance import prune_orphan_mirrors
    return prune_orphan_mirrors(str(memory_id), dry_run=dry_run)


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


@router.post("/{memory_id}/graph/merge-slots")
async def graph_merge_slots(memory_id: UUID):
    """Merge generic `slot`-typed mirror nodes into same-name typed entity nodes.

    Cleans up the duplication produced when a slot mirror and an extraction
    triple created two nodes for the same entity (e.g. ("slot", x) + ("item", x)).
    Edges are re-pointed and properties combined.
    """
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    from memory.graph import merge_slot_duplicates
    return merge_slot_duplicates(str(memory_id))


@router.post("/{memory_id}/graph/merge-nodes")
async def graph_merge_nodes(memory_id: UUID, payload: dict):
    """Merge one node into another (for semantic duplicates the automatic
    slot-merge can't match by name, e.g. 'team_member_bob' vs 'bob').

    Body: {"keep_id": "<node uuid>", "drop_id": "<node uuid>"}.
    Properties are combined (keep wins on conflict), edges re-pointed, the
    dropped node removed.
    """
    store = MemoryStore()
    if not store.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory pool not found")
    keep_id, drop_id = payload.get("keep_id"), payload.get("drop_id")
    if not keep_id or not drop_id:
        raise HTTPException(status_code=400, detail="keep_id and drop_id are required")
    if str(keep_id) == str(drop_id):
        raise HTTPException(status_code=400, detail="keep_id and drop_id must differ")
    from memory.graph import GraphStore
    kept = GraphStore(str(memory_id)).merge_nodes(keep_id, drop_id)
    if kept is None:
        raise HTTPException(status_code=404, detail="One or both nodes not found")
    return {"kept": kept.model_dump()}


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
