"""Tool builders for core memory block tools, bound to one memory binding."""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from .store import MemoryStore

log = logging.getLogger(__name__)
from .tool_support import (
    _persist,
)


def build_block_tools(ctx):
    _block_writable_ids = ctx._block_writable_ids
    _pool_name = ctx._pool_name
    _refuse_write = ctx._refuse_write
    _ro_ids = ctx._ro_ids
    multi = ctx.multi
    personal_extra = ctx.personal_extra
    pool_ids = ctx.pool_ids

    # -----------------------------------------------------------------------
    # core memory blocks — the always-in-context layer
    # -----------------------------------------------------------------------

    def _find_block(name: str):
        """Locate a block by name across the attached pools, primary first.

        Returns (pool_id, memory, block) or (None, None, None).
        """
        store = MemoryStore()
        for pid in pool_ids:
            mem = store.get(pid)
            if not mem:
                continue
            block = mem.get_block(name)
            if block is not None:
                return pid, mem, block
        return None, None, None

    def _block_names() -> list:
        store = MemoryStore()
        names: list = []
        for pid in pool_ids:
            mem = store.get(pid)
            if mem:
                names.extend(b.name for b in (mem.blocks or []))
        return names

    def _block_result(pid, block, **extra) -> str:
        payload = {
            "ok": True,
            "name": block.name,
            "value": block.value,
            "chars": len(block.value or ""),
            "limit_chars": block.limit_chars,
            "read_only": block.read_only,
            "description": block.description,
        }
        if multi:
            payload["pool"] = _pool_name(str(pid))
        payload.update(extra)
        return json.dumps(payload)

    class _BlockReadInput(BaseModel):
        name: str = Field(..., description="Name of the block to read, e.g. 'persona' or 'user'.")

    def _block_read_impl(name: str) -> str:
        try:
            pid, _mem, block = _find_block(name)
            if block is None:
                return json.dumps({
                    "ok": False,
                    "error": f"No memory block named {name!r}",
                    "available": _block_names(),
                })
            return _block_result(pid, block)
        except Exception as e:  # noqa: BLE001 - the tool returns the error to the model as a string
            return json.dumps({"ok": False, "error": f"memory_block_read failed: {e}"})

    memory_block_read_tool = StructuredTool.from_function(
        name="memory_block_read",
        description=(
            "Read one core memory block in full. The blocks are already rendered into your "
            "system prompt, so you only need this when a block was truncated there (it says so) "
            "or when you want to confirm the exact text before editing it."
        ),
        func=_block_read_impl,
        args_schema=_BlockReadInput,
    )

    class _BlockReplaceInput(BaseModel):
        name: str = Field(..., description="Name of the block to edit.")
        old: str = Field(..., description="Exact text to find in the block. Must occur exactly once.")
        new: str = Field(..., description="Text to put in its place. Pass an empty string to delete the old text.")

    def _block_replace_impl(name: str, old: str, new: str) -> str:
        try:
            pid, mem, block = _find_block(name)
            if block is None:
                return json.dumps({
                    "ok": False,
                    "error": f"No memory block named {name!r}",
                    "available": _block_names(),
                })
            if pid in _ro_ids:
                return _refuse_write(pid)
            if pid not in _block_writable_ids:
                return json.dumps({
                    "ok": False,
                    "error": f"Block {name!r} is in pool {_pool_name(pid)!r}, which is attached "
                             "read-only context here; block edits only reach the primary pool"
                             + (" or the personal pool" if personal_extra else "") + ".",
                })
            if block.read_only:
                return json.dumps({"ok": False, "error": f"Block {block.name!r} is read-only"})
            occurrences = (block.value or "").count(old)
            if occurrences == 0:
                return json.dumps({
                    "ok": False,
                    "error": f"`old` text not found in block {block.name!r} — read it first and copy the text exactly",
                })
            if occurrences > 1:
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"`old` occurs {occurrences} times in block {block.name!r}; "
                        "include enough surrounding text to make it unique"
                    ),
                })
            updated = (block.value or "").replace(old, new, 1)
            if len(updated) > block.limit_chars:
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"Replacement would take block {block.name!r} to {len(updated)} chars, "
                        f"{len(updated) - block.limit_chars} over its {block.limit_chars} char limit. "
                        "Shorten the new text or remove something else first."
                    ),
                    "over_by": len(updated) - block.limit_chars,
                })
            block.value = updated
            _persist(MemoryStore(), mem)
            return _block_result(pid, block, mode="replaced")
        except Exception as e:  # noqa: BLE001 - the tool returns the error to the model as a string
            return json.dumps({"ok": False, "error": f"memory_block_replace failed: {e}"})

    memory_block_replace_tool = StructuredTool.from_function(
        name="memory_block_replace",
        description=(
            "Replace a piece of text inside a core memory block. Use it to correct a fact that "
            "changed rather than appending a contradiction: `old` must appear exactly once, and "
            "an empty `new` deletes it. The edit is refused when it would push the block past "
            "its character limit, and the error says by how much."
        ),
        func=_block_replace_impl,
        args_schema=_BlockReplaceInput,
    )

    class _BlockAppendInput(BaseModel):
        name: str = Field(..., description="Name of the block to append to.")
        text: str = Field(..., description="Text to add at the end of the block, on its own line.")

    def _block_append_impl(name: str, text: str) -> str:
        try:
            pid, mem, block = _find_block(name)
            if block is None:
                return json.dumps({
                    "ok": False,
                    "error": f"No memory block named {name!r}",
                    "available": _block_names(),
                })
            if pid in _ro_ids:
                return _refuse_write(pid)
            if pid not in _block_writable_ids:
                return json.dumps({
                    "ok": False,
                    "error": f"Block {name!r} is in pool {_pool_name(pid)!r}, which is attached "
                             "read-only context here; block edits only reach the primary pool"
                             + (" or the personal pool" if personal_extra else "") + ".",
                })
            if block.read_only:
                return json.dumps({"ok": False, "error": f"Block {block.name!r} is read-only"})
            current = block.value or ""
            addition = text if not current else ("\n" + text)
            updated = current + addition
            if len(updated) > block.limit_chars:
                return json.dumps({
                    "ok": False,
                    "error": (
                        f"Appending would take block {block.name!r} to {len(updated)} chars, "
                        f"{len(updated) - block.limit_chars} over its {block.limit_chars} char limit. "
                        "Shorten the text, or use memory_block_replace to drop something stale first."
                    ),
                    "over_by": len(updated) - block.limit_chars,
                })
            block.value = updated
            _persist(MemoryStore(), mem)
            return _block_result(pid, block, mode="appended")
        except Exception as e:  # noqa: BLE001 - the tool returns the error to the model as a string
            return json.dumps({"ok": False, "error": f"memory_block_append failed: {e}"})

    memory_block_append_tool = StructuredTool.from_function(
        name="memory_block_append",
        description=(
            "Add a line to a core memory block. Blocks are always in your context, so keep them "
            "to durable facts: who the user is, how they want you to work, what the standing "
            "constraints are. Everything else belongs in a slot, a note or an episode. The "
            "append is refused when it would push the block past its character limit."
        ),
        func=_block_append_impl,
        args_schema=_BlockAppendInput,
    )

    return memory_block_read_tool, memory_block_replace_tool, memory_block_append_tool
