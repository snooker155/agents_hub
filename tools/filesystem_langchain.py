"""
LangChain tool wrappers for filesystem tools.

Provides structured, LangChain-compatible tools for file operations.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from pathlib import Path

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from tools.filesystem import (
    read_file as _read_file,
    write_file as _write_file,
    list_files as _list_files,
    search_text as _search_text,
)
from swe_agent.config import SweAgentConfig, DEFAULT_IGNORE


# -------------------- I/O Schemas --------------------

class ReadFileInput(BaseModel):
    path: str = Field(..., description="Workspace-relative or absolute path to read")


class ReadFileOutput(BaseModel):
    path: str
    content: str


class WriteFileInput(BaseModel):
    path: str = Field(..., description="Target file path (workspace-relative or absolute)")
    content: str = Field(..., description="UTF-8 text content to write")
    create_dirs: bool = Field(True, description="Create missing parent directories")


class WriteFileOutput(BaseModel):
    path: str = Field(..., description="Workspace-relative path of the written file")


class ListFilesInput(BaseModel):
    glob: str = Field("**/*", description="Glob pattern relative to workspace")
    ignore: Optional[List[str]] = Field(
        default=None,
        description="Optional list of path patterns to ignore (e.g. ['.git', '.venv'])",
    )


class ListFilesOutput(BaseModel):
    files: List[str]


class SearchMatch(BaseModel):
    path: str
    line: int
    match: str


class SearchTextInput(BaseModel):
    pattern: str = Field(..., description="Regex pattern (Python re syntax)")
    file_glob: str = Field(..., description="Glob of files to search, e.g. '**/*.py'")


class SearchTextOutput(BaseModel):
    results: List[SearchMatch]


class FileOp(BaseModel):
    path: str
    op: str


class ApplyUnifiedDiffInput(BaseModel):
    diff_text: str = Field(..., description="Unified diff text (e.g., from git diff)")


class ApplyUnifiedDiffOutput(BaseModel):
    applied: bool
    files: List[FileOp]


class CreateFileInput(BaseModel):
    path: str = Field(..., description="Path to create under the workspace")
    content: str = Field("", description="Initial file content")


class CreateFileOutput(BaseModel):
    path: str


class ReadMemoryInput(BaseModel):
    memory_id: str = Field(..., description="ID of the shared memory pool")
    file_name: Optional[str] = Field(None, description="Optional specific file name within the memory pool")

# -------------------- Tool factory --------------------

def create_filesystem_tools(workspace: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> List[Any]:
    """Create filesystem tools bound to a workspace.
    
    Args:
        workspace: Filesystem workspace root path
        config: Optional config dict with max_read_bytes, ignore_globs, etc.
    
    Returns:
        List of LangChain tool objects
    """
    ws_path = Path(workspace).resolve() if workspace else None
    
    c = config or {}
    cfg = SweAgentConfig(
        workspace_root=ws_path,
        max_read_bytes=c.get("max_read_bytes", 1_000_000),
        ignore_globs=c.get("ignore_globs", list(DEFAULT_IGNORE)),
        allow_delete=c.get("allow_delete", True),
        binary_threshold=c.get("binary_threshold", 4096),
    )

    def _read_impl(path: str) -> str:
        try:
            content = _read_file(path, workspace=ws_path, config=cfg)
            return ReadFileOutput(path=path, content=content).model_dump_json(ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"read_file failed: {e}", "path": path}, ensure_ascii=False)

    read_tool = StructuredTool.from_function(
        name="read_file",
        description="Read a UTF-8 text file and return JSON with {path, content}.",
        func=_read_impl,
        args_schema=ReadFileInput,
    )

    def _write_impl(path: str, content: str, create_dirs: bool = True) -> str:
        try:
            rel = _write_file(path, content, create_dirs=create_dirs, workspace=ws_path)
            return WriteFileOutput(path=rel).model_dump_json(ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"write_file failed: {e}", "path": path}, ensure_ascii=False)

    write_tool = StructuredTool.from_function(
        name="write_file",
        description="Write UTF-8 text to a file. Returns JSON with {path}.",
        func=_write_impl,
        args_schema=WriteFileInput,
    )

    def _list_impl(glob: str = "**/*", ignore: Optional[List[str]] = None) -> str:
        try:
            files = _list_files(glob, ignore=ignore, workspace=ws_path, config=cfg)
            return ListFilesOutput(files=files).model_dump_json(ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"list_files failed: {e}"}, ensure_ascii=False)

    list_tool = StructuredTool.from_function(
        name="list_files",
        description="List files matching a glob. Returns JSON with {files: [...]}.",
        func=_list_impl,
        args_schema=ListFilesInput,
    )

    def _search_impl(pattern: str, file_glob: str) -> str:
        try:
            raw = _search_text(pattern, file_glob, workspace=ws_path, config=cfg)
            results = [SearchMatch(**hit) for hit in raw]
            return SearchTextOutput(results=results).model_dump_json(ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"search_text failed: {e}"}, ensure_ascii=False)

    search_tool = StructuredTool.from_function(
        name="search_text",
        description="Search regex pattern across files. Returns JSON with {results}.",
        func=_search_impl,
        args_schema=SearchTextInput,
    )

    def _apply_impl(diff_text: str) -> str:
        try:
            result = _apply_unified_diff(diff_text, workspace=str(ws_path) if ws_path else None, config=cfg)
            files = [FileOp(**fo) for fo in (result.get("files") or [])]
            out = ApplyUnifiedDiffOutput(applied=bool(result.get("applied")), files=files)
            return out.model_dump_json(ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"apply_unified_diff failed: {e}"}, ensure_ascii=False)

    apply_tool = StructuredTool.from_function(
        name="apply_unified_diff",
        description="Apply a unified diff to files under the workspace and return JSON with applied and files[]",
        func=_apply_impl,
        args_schema=ApplyUnifiedDiffInput,
    )

    def _create_impl(path: str, content: str = "") -> str:
        try:
            rel = _create_file(path, content, workspace=str(ws_path) if ws_path else None)
            return CreateFileOutput(path=rel).model_dump_json(ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"create_file failed: {e}", "path": path}, ensure_ascii=False)

    create_tool = StructuredTool.from_function(
        name="create_file",
        description="Create a new file under the workspace. Returns JSON with {path}",
        func=_create_impl,
        args_schema=CreateFileInput,
    )

    def _read_memory_impl(memory_id: str, file_name: Optional[str] = None) -> str:
        try:
            from tasks.storage import MemoryStore
            store = MemoryStore()
            mem = store.get(memory_id)
            if not mem:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {memory_id}"})

            # Log usage for visibility
            print(f"[SWE][{cfg.workspace_root.name if cfg.workspace_root else 'agent'}] Using memory: {memory_id}")

            if file_name:
                for f in mem.files:
                    if f["name"] == file_name:
                        return json.dumps({"ok": True, "memory_id": memory_id, "file_name": file_name, "content": f["content"]})
                return json.dumps({"ok": False, "error": f"File {file_name} not found in memory pool {memory_id}"})

            return json.dumps({"ok": True, "memory_id": memory_id, "files": [f["name"] for f in mem.files], "description": mem.description})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"read_memory failed: {e}"})

    read_memory_tool = StructuredTool.from_function(
        name="read_memory",
        description="Access shared memory pools. Returns file list or specific file content.",
        func=_read_memory_impl,
        args_schema=ReadMemoryInput,
    )

    return [read_tool, write_tool, list_tool, search_tool, apply_tool, create_tool, read_memory_tool]


__all__ = ["create_filesystem_tools"]
