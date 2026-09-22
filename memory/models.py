from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# Default character budget for a core memory block. Blocks are rendered into
# every system prompt, so the limit is what keeps an always-in-context layer
# from crowding out the conversation.
DEFAULT_BLOCK_LIMIT = 2000


class MemoryBlock(BaseModel):
    """A named, always-in-context block of free text (Letta style core memory).

    Unlike a structured slot (a dict-shaped record fetched through a tool), a
    block is plain text rendered into the system prompt at build time and
    capped at ``limit_chars``, so the agent sees it without any tool call.
    """

    name: str
    value: str = ""
    limit_chars: int = DEFAULT_BLOCK_LIMIT
    description: str = ""
    read_only: bool = False


def default_blocks() -> List["MemoryBlock"]:
    """The two blocks a pool is seeded with when it has none."""
    return [
        MemoryBlock(
            name="persona",
            value="",
            description=(
                "Who you are in this pool: your role, your tone, the standing "
                "instructions you have accepted. Keep it short and current."
            ),
        ),
        MemoryBlock(
            name="user",
            value="",
            description=(
                "Facts about the user you are working with: name, role, "
                "preferences, what they are working on."
            ),
        ),
    ]


class SharedMemory(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    type: str = "text"
    description: str = ""
    workspace: Optional[str] = None
    blocks: List[MemoryBlock] = Field(default_factory=default_blocks)  # always-in-context core memory
    notes: List[Dict[str, Any]] = Field(default_factory=list)   # [{id, title, content, created_at}]
    kv_pairs: List[Dict[str, Any]] = Field(default_factory=list) # legacy — migrated on load
    structured_data: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # slot -> data dict
    rag_files: List[Dict[str, Any]] = Field(default_factory=list) # [{filename, workspace, status, indexed_at, chunks}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context: Any) -> None:
        """Migrate legacy formats into the flat slot -> data dict on load."""
        # Seed the core memory blocks for pools written before blocks existed.
        if not self.blocks:
            self.blocks = default_blocks()
        # legacy kv_pairs list
        if self.kv_pairs:
            for kv in self.kv_pairs:
                key = kv.get("key")
                if key and key not in self.structured_data:
                    self.structured_data[key] = {"value": kv.get("value", "")}
            self.kv_pairs = []
        # legacy {schema_type, data} wrapper — unwrap to just the data dict
        for slot, val in list(self.structured_data.items()):
            if isinstance(val, dict) and "data" in val and "schema_type" in val:
                self.structured_data[slot] = val["data"]

    def touch(self) -> None:
        """Update the updated_at timestamp to now (UTC)."""
        try:
            object.__setattr__(self, "updated_at", datetime.now(timezone.utc))
        except Exception:
            setattr(self, "updated_at", datetime.now(timezone.utc))

    # ── Core memory blocks ───────────────────────────────────────────────────

    def get_block(self, name: str) -> Optional[MemoryBlock]:
        """Return the block called *name* (case-insensitive) or None."""
        key = (name or "").strip().lower()
        for block in self.blocks:
            if block.name.strip().lower() == key:
                return block
        return None

    def upsert_block(
        self,
        name: str,
        value: Optional[str] = None,
        *,
        limit_chars: Optional[int] = None,
        description: Optional[str] = None,
        read_only: Optional[bool] = None,
    ) -> MemoryBlock:
        """Create or update a block by name, leaving unspecified fields alone."""
        block = self.get_block(name)
        if block is None:
            block = MemoryBlock(name=(name or "").strip())
            self.blocks.append(block)
        if value is not None:
            block.value = value
        if limit_chars is not None:
            block.limit_chars = max(1, int(limit_chars))
        if description is not None:
            block.description = description
        if read_only is not None:
            block.read_only = bool(read_only)
        return block
