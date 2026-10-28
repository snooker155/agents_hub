"""
Skills a person or an agent wrote in a workspace.

A skill is a ``Procedure`` in the ``procedures`` collection
(memory/procedural.py), scoped by workspace and stamped with its author.
Skills imported from a project's ``.claude/skills`` folder (``source``
``repo``) come from git and are the folder's to bring back, so only
``user`` and ``agent`` skills travel.

The document is the content (memory/skill_versions.py ``CONTENT_FIELDS``)
plus the few fields that say what the skill is: ``license``, ``source``, the
agent it is attached to and when it was made. Everything that is bookkeeping
stays local: use count and success rate, the version number (the store counts
its own), pins, publication and review state, and the safety review. The
review is derived from the text, so :meth:`SkillAdapter.apply` runs it again
on what arrives instead of trusting a verdict computed elsewhere.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from hubsync.adapters.base import Adapter, AdapterError, register
from memory import skill_versions
from memory.procedural import Procedure, ProcedureStore
from memory.skill_review import review_skill

SYNCED_SOURCES = ("user", "agent")

#: Beyond the versioned content: what identifies the skill.
_EXTRA_FIELDS = ("license", "source", "agent_id", "created_at")


def _skill_id(record_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(record_id))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"hubsync:skill:{record_id}")


class SkillAdapter(Adapter):
    kind = "skill"
    order = 15
    label = "Skills"
    volatile = ("updated_at",)

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        return sorted(str(p.id) for p in ProcedureStore(workspace).load()
                      if p.source in SYNCED_SOURCES and (p.owner_user or owner) == owner)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        skill = ProcedureStore(workspace).get(record_id)
        if skill is None or skill.source not in SYNCED_SOURCES:
            return None
        data = skill.model_dump(mode="json")
        doc = {k: data[k] for k in (*skill_versions.CONTENT_FIELDS, *_EXTRA_FIELDS)}
        doc["updated_at"] = data["updated_at"]
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        source = doc.get("source") or "user"
        if source not in SYNCED_SOURCES:
            raise AdapterError(f"a '{source}' skill is not synced: it comes from its repository")
        store = ProcedureStore(workspace)
        pid = _skill_id(record_id)
        elsewhere = store.docs.get(str(pid))
        existing = store.get(pid)
        if existing is None and elsewhere is not None:
            raise AdapterError(f"skill id {record_id} belongs to another workspace here")
        if existing is not None and (existing.owner_user or owner) != owner:
            raise AdapterError(f"skill id {record_id} belongs to someone else here")
        fields = {k: doc[k] for k in (*skill_versions.CONTENT_FIELDS, "license", "agent_id") if k in doc}
        fields["source"] = source
        fields["safety"] = review_skill(
            description=str(fields.get("description") or ""), body=str(fields.get("body") or ""),
            resources=list(fields.get("resources") or []), declared_license=str(fields.get("license") or ""))
        try:
            if existing is None:
                created = {"created_at": doc["created_at"]} if doc.get("created_at") else {}
                store.add(Procedure(id=pid, workspace=workspace, owner_user=owner, **created, **fields))
            else:
                updated = existing.model_copy(update=fields)
                updated.touch()
                store.update(updated)
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        return str(pid)

    def delete(self, workspace: str, record_id: str) -> None:
        store = ProcedureStore(workspace)
        skill = store.get(record_id)
        if skill is not None and skill.source in SYNCED_SOURCES:
            store.delete(record_id)


register(SkillAdapter())
