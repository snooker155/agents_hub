"""Tool builders for the remember and forget tools, bound to one memory binding."""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from .store import MemoryStore

log = logging.getLogger(__name__)
from .tool_support import (
    JOURNAL_PREFIX, _persist,
)


def build_write_tools(ctx):
    _personal_field_description = ctx._personal_field_description
    _pool_name = ctx._pool_name
    _refuse_write = ctx._refuse_write
    _ro_ids = ctx._ro_ids
    _where = ctx._where
    _write_target = ctx._write_target
    multi = ctx.multi
    personal_extra = ctx.personal_extra
    pool_ids = ctx.pool_ids

    # -----------------------------------------------------------------------
    # remember — unified write
    # -----------------------------------------------------------------------

    class _RememberInput(BaseModel):
        slot: Optional[str] = Field(None, description="Slot name to store structured data (e.g. 'user_profile', 'api_version')")
        data: Optional[dict] = Field(None, description="Data dict for the slot. For a simple value use {\"value\": \"...\"}.")
        replace: bool = Field(
            False,
            description=(
                "If False (default), merge `data` into the existing slot — keys you pass overwrite "
                "matching keys, untouched keys are preserved. If True, replace the slot entirely."
            ),
        )
        note_title: Optional[str] = Field(None, description="Title of a free-text note to create or update")
        note_content: Optional[str] = Field(None, description="Content of the note")
        link_to_graph: bool = Field(
            True,
            description=(
                "If True (default), also mirror the slot as a graph node so `traverse` can reach it. "
                "Notes are mirrored too (as type='note') only when this is True. "
                "Set False to keep this write off the graph."
            ),
        )

    def _remember_impl(
        slot: Optional[str] = None,
        data: Optional[dict] = None,
        replace: bool = False,
        note_title: Optional[str] = None,
        note_content: Optional[str] = None,
        link_to_graph: bool = True,
        personal: bool = False,
    ) -> str:
        target = _write_target(personal)
        if target in _ro_ids:
            return _refuse_write(target)
        saved = []
        errors = []
        try:
            store = MemoryStore()
            mem = store.get(target)
            if not mem:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {target}"})

            if slot is not None:
                if data is None:
                    errors.append("data required when slot is provided")
                else:
                    existing = mem.structured_data.get(slot)
                    existing_keys = list(existing.keys()) if isinstance(existing, dict) else []
                    if replace or not isinstance(existing, dict):
                        merged = dict(data)
                        mode = "replaced" if existing is not None else "created"
                    else:
                        merged = {**existing, **data}
                        mode = "merged"
                    mem.structured_data[slot] = merged
                    saved.append({
                        "type": "structured",
                        "slot": slot,
                        "mode": mode,
                        "keys_before": existing_keys,
                        "keys_after": list(merged.keys()),
                        "added": [k for k in data.keys() if k not in existing_keys],
                        "overwritten": [k for k in data.keys() if k in existing_keys],
                    })

            if note_title is not None:
                if note_content is None:
                    errors.append("note_content required when note_title is provided")
                elif note_title.startswith(JOURNAL_PREFIX):
                    errors.append(f"note_title must not start with '{JOURNAL_PREFIX}' — journal entries are written automatically")
                else:
                    from datetime import datetime, timezone
                    from uuid import uuid4
                    existing_note = next((n for n in mem.notes if n.get("title") == note_title), None)
                    if existing_note:
                        existing_note["content"] = note_content
                    else:
                        mem.notes.append({
                            "id": str(uuid4()),
                            "title": note_title,
                            "content": note_content,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                    saved.append({"type": "note", "title": note_title})

            if not saved and not errors:
                return json.dumps({"ok": False, "error": "Provide at least one of: slot or note_title"})

            if saved:
                _persist(store, mem)

            # Bridge to the graph: mirror slots/notes as nodes for traversal.
            graph_links: list[dict] = []
            if link_to_graph and saved:
                try:
                    from memory.graph import GraphStore
                    gstore = GraphStore(target)
                    for entry in saved:
                        if entry.get("type") == "structured":
                            slot_name = entry["slot"]
                            slot_data = mem.structured_data.get(slot_name) or {}
                            props = {
                                k: v for k, v in slot_data.items()
                                if isinstance(v, (str, int, float, bool)) or v is None
                            }
                            # Attach to an existing same-name entity node when one
                            # exists, instead of creating a parallel ("slot", ...) twin.
                            twin = next((n for n in gstore.find_twins(slot_name) if n.type != "slot"), None)
                            if twin is not None:
                                node, mode = gstore.upsert_node(twin.type, twin.name, props)
                            else:
                                node, mode = gstore.upsert_node("slot", slot_name, props)
                            graph_links.append({"type": node.type, "name": slot_name, "node_id": str(node.id), "mode": mode})
                        elif entry.get("type") == "note":
                            note_name = entry["title"]
                            node, mode = gstore.upsert_node("note", note_name, {})
                            graph_links.append({"type": "note", "name": note_name, "node_id": str(node.id), "mode": mode})
                except Exception as ge:  # noqa: BLE001 - a failed graph bridge is reported in errors and the save stands
                    errors.append(f"graph bridge skipped: {ge}")

            out = {"ok": True, "saved": saved, "errors": errors, "graph_links": graph_links,
                   "memory": _where(mem)}
            if multi:
                out["pool"] = _pool_name(target)
            return json.dumps(out)

        except Exception as e:  # noqa: BLE001 - the tool returns the error to the model as a string
            return json.dumps({"ok": False, "error": f"remember failed: {e}"})

    class _RememberPersonalInput(_RememberInput):
        personal: bool = Field(False, description=_personal_field_description)

    remember_tool = StructuredTool.from_function(
        name="remember",
        description=(
            "Store information in shared memory. "
            "Slot semantics: a `slot` is a NAMED RECORD (a dict) — use slots for structured facts with named fields. "
            "For free-text content (a note, reminder, draft, idea — anything the user calls a 'note') "
            "use note_title+note_content instead of a slot. "
            "If the slot already exists, this call MERGES `data` into it — keys you pass are added/updated, untouched keys are preserved. "
            "Pick the SAME slot name when adding fields to an existing record (e.g. extending the `project` slot with a new `priority` field). "
            "Pick a NEW slot name only for an unrelated fact. "
            "Pass `replace=True` to overwrite the whole slot (rare; use only when previous content is wrong). "
            "Simple values can use {\"value\": \"...\"}. "
            "Both slot and note can be saved in one call. "
            "This tool cannot delete anything — to remove a slot or note, use `forget`. "
            "By default each saved slot/note is also mirrored as a graph node (type='slot' or 'note') "
            "so `link` and `traverse` can reach it. Set `link_to_graph=False` to skip the bridge."
            + (
                f" Multiple memory pools are attached; writes go to the primary pool ({_pool_name(pool_ids[0])})"
                + (", or to the user's personal memory with `personal=True`." if personal_extra else ".")
                if multi else ""
            )
        ),
        func=_remember_impl,
        args_schema=_RememberPersonalInput if personal_extra else _RememberInput,
    )

    # -----------------------------------------------------------------------
    # forget — delete a slot or note (and its graph mirror)
    # -----------------------------------------------------------------------

    class _ForgetInput(BaseModel):
        slot: Optional[str] = Field(None, description="Name of the structured slot to delete")
        note_title: Optional[str] = Field(None, description="Title of the free-text note to delete")
        file: Optional[str] = Field(
            None,
            description=(
                "Filename of an indexed knowledge file to drop from this pool: its index entry "
                "and its vectors both go, so it stops answering searches."
            ),
        )
        unlink_graph: bool = Field(
            True,
            description=(
                "If True (default), also delete the graph mirror node (type='slot' or 'note') "
                "created by `remember`, along with its edges. Typed entity nodes created via "
                "`link` are never touched."
            ),
        )

    def _forget_impl(
        slot: Optional[str] = None,
        note_title: Optional[str] = None,
        file: Optional[str] = None,
        unlink_graph: bool = True,
        personal: bool = False,
    ) -> str:
        target = _write_target(personal)
        if target in _ro_ids:
            return _refuse_write(target)
        deleted = []
        errors = []
        try:
            store = MemoryStore()
            mem = store.get(target)
            if not mem:
                return json.dumps({"ok": False, "error": f"Memory pool not found: {target}"})

            if slot is None and note_title is None and file is None:
                return json.dumps({"ok": False, "error": "Provide at least one of: slot, note_title or file"})

            if slot is not None:
                if slot in mem.structured_data:
                    del mem.structured_data[slot]
                    deleted.append({"type": "structured", "slot": slot})
                else:
                    errors.append(
                        f"Slot '{slot}' not found. Existing slots: "
                        f"{', '.join(mem.structured_data.keys()) or '(none)'}"
                    )

            if note_title is not None:
                if note_title.startswith(JOURNAL_PREFIX):
                    errors.append(
                        f"Notes titled '{JOURNAL_PREFIX}…' are the auto-managed journal and cannot be deleted"
                    )
                else:
                    before = len(mem.notes)
                    mem.notes = [n for n in mem.notes if n.get("title") != note_title]
                    if len(mem.notes) < before:
                        deleted.append({"type": "note", "title": note_title})
                    else:
                        plain = [
                            n["title"] for n in mem.notes
                            if not n.get("title", "").startswith(JOURNAL_PREFIX)
                        ]
                        errors.append(
                            f"Note '{note_title}' not found. Existing notes: "
                            f"{', '.join(plain) or '(none)'}"
                        )

            if file is not None:
                before_files = len(mem.rag_files)
                mem.rag_files = [f for f in mem.rag_files if f.get("filename") != file]
                indexed = len(mem.rag_files) < before_files
                # The vectors go whether or not the pool still had an index
                # entry: an entry lost to an earlier partial delete is exactly
                # the case where the chunks are still answering searches.
                from memory.rag_query import delete_rag_vectors
                vector_meta = delete_rag_vectors(str(target), file)
                if indexed:
                    deleted.append({"type": "file", "filename": file, "vectors": vector_meta})
                else:
                    errors.append(
                        f"File '{file}' was not indexed in this pool. Its vectors were removed anyway: {vector_meta}"
                    )

            if deleted:
                _persist(store, mem)

            # Remove the graph mirror nodes (only the 'slot'/'note'-typed twins
            # written by remember's bridge — never typed entity nodes).
            graph_unlinked: list[dict] = []
            if unlink_graph and deleted:
                try:
                    from memory.graph import GraphStore
                    gstore = GraphStore(target)
                    for entry in deleted:
                        if entry["type"] == "structured":
                            node = gstore.get_node("slot", entry["slot"])
                        elif entry["type"] == "note":
                            node = gstore.get_node("note", entry["title"])
                        else:
                            continue
                        if node is not None and gstore.delete_node(node.id):
                            graph_unlinked.append({"type": node.type, "name": node.name})
                except Exception as ge:  # noqa: BLE001 - a failed graph unlink is reported in errors and the delete stands
                    errors.append(f"graph unlink skipped: {ge}")

            return json.dumps({
                "ok": bool(deleted),
                "deleted": deleted,
                "errors": errors,
                "graph_unlinked": graph_unlinked,
                **({"memory": _where(mem)} if deleted else {}),
            })
        except Exception as e:  # noqa: BLE001 - the tool returns the error to the model as a string
            return json.dumps({"ok": False, "error": f"forget failed: {e}"})

    class _ForgetPersonalInput(_ForgetInput):
        personal: bool = Field(False, description="True to delete from the user's personal memory instead of your own pool.")

    forget_tool = StructuredTool.from_function(
        name="forget",
        description=(
            "Delete a structured slot, a free-text note, or an indexed file from shared memory. "
            "Pass `slot` to delete a slot, `note_title` to delete a note, `file` to drop an indexed "
            "document (its vectors go with it), or several in one call. "
            "Use when the user asks to remove, delete, or forget stored information, or to "
            "clean up after converting a slot into a note (or vice versa). "
            "The graph mirror node created by `remember` is removed too (set unlink_graph=False to keep it). "
            "Journal notes are auto-managed and cannot be deleted. "
            "Deletion is permanent — when the request is ambiguous, `recall` first to confirm what exists."
            + (
                f" Multiple memory pools are attached; deletes affect the primary pool ({_pool_name(pool_ids[0])})"
                + (", or the user's personal memory with `personal=True`." if personal_extra else " only.")
                if multi else ""
            )
        ),
        func=_forget_impl,
        args_schema=_ForgetPersonalInput if personal_extra else _ForgetInput,
    )

    return remember_tool, forget_tool
