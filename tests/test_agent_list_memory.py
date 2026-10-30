"""
The agents list tells the chat's agent picker what each agent remembers
with in the workspace asked about: its memory pools by name (read-only
marked) and whether personal memory is on for it there.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))


def test_memory_pools_by_name_and_personal_switch():
    from memory import personal
    from memory.models import SharedMemory
    from memory.store import MemoryStore
    from routes.agents.listing import _memory_annotator
    from workspace.storage import create_workspace_folder
    create_workspace_folder("acme")
    notes = MemoryStore().add(SharedMemory(name="Team notes", workspace="acme"))
    docs = MemoryStore().add(SharedMemory(name="Product docs", workspace="acme"))
    personal.set_agent("writer", "acme", True)

    annotate = _memory_annotator("acme")
    writer = annotate({"id": "writer", "memory_type": "shared",
                       "memory_data": [str(notes.id), {"id": str(docs.id), "read_only": True}]})
    assert writer["type"] == "shared" and writer["personal"] is True
    assert [(p["name"], p["read_only"]) for p in writer["pools"]] == [
        ("Team notes", False), ("Product docs", True)]

    plain = annotate({"id": "reader", "memory_type": "none"})
    assert plain == {"type": "none", "pools": [], "personal": False}

    # The workspace switch off turns every agent's personal memory off.
    personal.set_workspace_enabled("acme", False)
    assert _memory_annotator("acme")({"id": "writer", "memory_type": "none"})["personal"] is False
