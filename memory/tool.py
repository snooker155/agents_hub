from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from .store import MemoryStore


class ReadMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    file_name: Optional[str] = Field(None, description="Name of a specific file to read")
    note_title: Optional[str] = Field(None, description="Title of a specific note to read")
    kv_key: Optional[str] = Field(None, description="Key of a specific key-value pair to read")


def _read_memory_impl(
    memory_id: str,
    file_name: Optional[str] = None,
    note_title: Optional[str] = None,
    kv_key: Optional[str] = None,
) -> str:
    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        print(f"[memory] Using pool: {memory_id}")

        if file_name:
            for f in mem.files:
                if f["name"] == file_name:
                    return json.dumps({"ok": True, "type": "file", "memory_id": memory_id, "file_name": file_name, "content": f["content"]})
            return json.dumps({"ok": False, "error": f"File '{file_name}' not found in pool {memory_id}"})

        if note_title:
            for n in mem.notes:
                if n["title"] == note_title:
                    return json.dumps({"ok": True, "type": "note", "memory_id": memory_id, "title": n["title"], "content": n["content"]})
            return json.dumps({"ok": False, "error": f"Note '{note_title}' not found in pool {memory_id}"})

        if kv_key:
            for kv in mem.kv_pairs:
                if kv["key"] == kv_key:
                    return json.dumps({"ok": True, "type": "kv", "memory_id": memory_id, "key": kv["key"], "value": kv["value"], "description": kv.get("description", "")})
            return json.dumps({"ok": False, "error": f"Key '{kv_key}' not found in pool {memory_id}"})

        return json.dumps({
            "ok": True,
            "memory_id": memory_id,
            "description": mem.description,
            "files": [f["name"] for f in mem.files],
            "notes": [n["title"] for n in mem.notes],
            "kv_keys": [kv["key"] for kv in mem.kv_pairs],
        })
    except Exception as e:
        return json.dumps({"ok": False, "error": f"read_memory failed: {e}"})


read_memory_tool = StructuredTool.from_function(
    name="read_memory",
    description=(
        "Access a shared memory pool. Without extra args returns a summary listing files, notes, and kv_keys. "
        "Pass file_name to read a file, note_title to read a note, or kv_key to read a key-value pair."
    ),
    func=_read_memory_impl,
    args_schema=ReadMemoryInput,
)


class WriteMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    file_name: Optional[str] = Field(None, description="Name of the file to create or overwrite")
    file_content: Optional[str] = Field(None, description="Content to write to the file")
    note_title: Optional[str] = Field(None, description="Title of the note to create or update")
    note_content: Optional[str] = Field(None, description="Content of the note")
    kv_key: Optional[str] = Field(None, description="Key to create or update in the key-value store")
    kv_value: Optional[str] = Field(None, description="Value for the key-value pair")


def _write_memory_impl(
    memory_id: str,
    file_name: Optional[str] = None,
    file_content: Optional[str] = None,
    note_title: Optional[str] = None,
    note_content: Optional[str] = None,
    kv_key: Optional[str] = None,
    kv_value: Optional[str] = None,
) -> str:
    from datetime import datetime, timezone
    from uuid import uuid4

    try:
        store = MemoryStore()
        mem = store.get(memory_id)
        if not mem:
            return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

        if file_name is not None:
            if file_content is None:
                return json.dumps({"ok": False, "error": "file_content is required when writing a file"})
            entry = {
                "name": file_name,
                "content": file_content,
                "rag_status": "raw",
                "rag_chunks": 0,
                "size_bytes": len(file_content.encode("utf-8")),
                "added_at": datetime.now(timezone.utc).isoformat(),
            }
            mem.files = [f for f in mem.files if f.get("name") != file_name]
            mem.files.append(entry)
            _persist(store, mem)
            return json.dumps({"ok": True, "type": "file", "file_name": file_name})

        if note_title is not None:
            if note_content is None:
                return json.dumps({"ok": False, "error": "note_content is required when writing a note"})
            existing = next((n for n in mem.notes if n.get("title") == note_title), None)
            if existing:
                existing["content"] = note_content
            else:
                mem.notes.append({
                    "id": str(uuid4()),
                    "title": note_title,
                    "content": note_content,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            _persist(store, mem)
            return json.dumps({"ok": True, "type": "note", "title": note_title})

        if kv_key is not None:
            if kv_value is None:
                return json.dumps({"ok": False, "error": "kv_value is required when writing a key-value pair"})
            existing = next((kv for kv in mem.kv_pairs if kv.get("key") == kv_key), None)
            if existing:
                existing["value"] = kv_value
            else:
                mem.kv_pairs.append({"key": kv_key, "value": kv_value, "description": ""})
            _persist(store, mem)
            return json.dumps({"ok": True, "type": "kv", "key": kv_key})

        return json.dumps({"ok": False, "error": "Provide file_name, note_title, or kv_key to write"})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"write_memory failed: {e}"})


def _persist(store: MemoryStore, mem) -> None:
    mem.touch()
    memories = store.load()
    for i, m in enumerate(memories):
        if m.id == mem.id:
            memories[i] = mem
            break
    store.save(memories)


write_memory_tool = StructuredTool.from_function(
    name="write_memory",
    description=(
        "Write to a shared memory pool. "
        "Pass file_name + file_content to create/overwrite a file. "
        "Pass note_title + note_content to create/update a note. "
        "Pass kv_key + kv_value to create/update a key-value pair."
    ),
    func=_write_memory_impl,
    args_schema=WriteMemoryInput,
)


# ---------------------------------------------------------------------------
# search_memory — vector similarity search
# ---------------------------------------------------------------------------

class SearchMemoryInput(BaseModel):
    query: str = Field(..., description="Natural-language search query")
    memory_id: str = Field(..., description="ID of the shared memory pool to search")
    top_k: int = Field(default=5, description="Maximum number of chunks to return")


def _search_memory_impl(query: str, memory_id: str, top_k: int = 5) -> str:
    try:
        from memory.rag_query import search_rag, is_rag_configured
        if not is_rag_configured():
            return json.dumps({
                "ok": False,
                "error": "RAG is not configured. Set RAG_VECTOR_DB and RAG_EMBEDDING_PROVIDER in Settings.",
            })
        results = search_rag(query, memory_id, top_k=top_k)
        if not results:
            return json.dumps({"ok": True, "results": [], "note": "No relevant chunks found."})
        return json.dumps({"ok": True, "results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"search_memory failed: {e}"})


search_memory_tool = StructuredTool.from_function(
    name="search_memory",
    description=(
        "Semantic vector search over a shared memory pool. "
        "Returns the most relevant text chunks for the given query. "
        "Use this to retrieve context before answering questions or starting tasks."
    ),
    func=_search_memory_impl,
    args_schema=SearchMemoryInput,
)
