"""
Agents a person made in a workspace, with their prompt files.

An agent is two things kept apart: a record in the registry
(agents/registry.py) and markdown under ``definitions/<id>/``
(agents/prompt_assembly.py). The portable document joins them, so one change
to either is one change to the record. Both are written through their own
modules: the registry's save runs the capability guard, the isolation and
scope checks, version history and the review gate, and a refusal comes back
as an :class:`AdapterError` carrying the reason instead of a half-written
agent.

Only agents of the workspace that are the person's own work travel. The
product's agents (``system``, shipped under agents/definitions/) exist on
every hub already, and an id they or another workspace hold is "taken": the
other end's agent must not overwrite them. What stays local: who owns the
copy, its review and publication state (a decision made per hub), the host
port mapping and the system flags. Secrets are declared by name in
``secrets`` and their values never leave the secret store; the remote agent's
descriptor is scrubbed of anything token-shaped as a second line.

An agent that shares a definition folder with another one (``definition_id``
differing from the id) keeps that link and syncs no prompt files, because the
folder is not its own to write.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents import prompt_assembly as pa
from agents import registry
from hubsync.adapters.base import Adapter, AdapterError, register

#: Record fields that describe this hub's copy, not the agent.
_LOCAL_FIELDS = ("id", "owner_workspace", "owner_user", "shared", "system", "user_modified",
                 "review_status", "review_note", "reviewed_by", "reviewed_at")

#: Keys of the remote descriptor that would hold a credential. The descriptor
#: names an environment variable for its token (auth_token_env), so this finds
#: nothing in a record written through the UI; it is for a hand-edited one.
_SECRET_KEYS = ("token", "api_key", "apikey", "password", "secret", "authorization", "bearer")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if not (isinstance(v, str) and any(s in k.lower() for s in _SECRET_KEYS)
                        and not k.lower().endswith("_env"))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _own_definition(spec: registry.AgentSpec) -> bool:
    return not spec.definition_id or spec.definition_id == spec.id


def _syncable(spec: Optional[registry.AgentSpec], workspace: str) -> bool:
    return bool(spec) and spec.owner_workspace == workspace and not spec.system


def _read_examples_raw(agent_id: str) -> str:
    path = pa.part_path(agent_id, pa.EXAMPLES_FILE)
    return path.read_text(encoding="utf-8") if path else ""


class AgentAdapter(Adapter):
    kind = "agent"
    order = 10
    label = "Agents"
    volatile = ()

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        return sorted(s.id for s in registry.list_agents_raw()
                      if _syncable(s, workspace) and (s.owner_user or owner) == owner)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        spec = registry.get_agent_raw(record_id)
        if not _syncable(spec, workspace):
            return None
        doc = {k: v for k, v in spec.to_dict().items() if k not in _LOCAL_FIELDS}
        if doc.get("remote"):
            doc["remote"] = _scrub(doc["remote"])
        if _own_definition(spec):
            doc["prompts"] = {
                "instructions": pa.read_instructions(spec.id),
                "capabilities": pa.read_capabilities(spec.id),
                "usage": pa.read_usage(spec.id),
                "examples": _read_examples_raw(spec.id),
            }
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        existing = registry.get_agent_raw(record_id)
        if existing is not None:
            if existing.system or existing.owner_workspace != workspace:
                raise AdapterError(f"agent id '{record_id}' is taken here by another agent")
            if (existing.owner_user or owner) != owner:
                raise AdapterError(f"agent id '{record_id}' belongs to someone else here")
        elif pa.is_system_definition(record_id):
            raise AdapterError(f"agent id '{record_id}' is taken here by a product agent")
        prompts = doc.get("prompts") or {}
        record = {k: v for k, v in doc.items() if k not in _LOCAL_FIELDS and k != "prompts"}
        record.update(id=record_id, owner_workspace=workspace, owner_user=owner)
        if existing is not None:
            # Publication and review are decided per hub and stay as they are.
            kept = {"shared": existing.shared, "review_status": existing.review_status,
                    "review_note": existing.review_note, "reviewed_by": existing.reviewed_by,
                    "reviewed_at": existing.reviewed_at}
            record.update({k: v for k, v in kept.items() if v is not None})
        try:
            spec = registry._validate_agent_dict(record)
            registry.save_raw(spec, actor=owner, note="sync")
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - a guard or isolation refusal has its own types
            if exc.__class__.__module__.split(".")[0] in ("agents", "common", "tools"):
                raise AdapterError(str(exc)) from exc
            raise
        if _own_definition(spec):
            self._write_prompts(spec.id, prompts)
        return spec.id

    @staticmethod
    def _write_prompts(agent_id: str, prompts: Dict[str, Any]) -> None:
        writers = {pa.INSTRUCTIONS_FILE: pa.write_instructions,
                   pa.CAPABILITIES_FILE: pa.write_capabilities,
                   pa.USAGE_FILE: pa.write_usage}
        for filename, write in writers.items():
            text = str(prompts.get(filename.rsplit(".", 1)[0]) or "")
            if text.strip():
                write(agent_id, text)
            else:
                pa.clear_part(agent_id, filename)
        examples = str(prompts.get("examples") or "")
        if examples.strip():
            folder = pa.agent_dir(agent_id)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / pa.EXAMPLES_FILE).write_text(examples, encoding="utf-8")
        else:
            pa.clear_part(agent_id, pa.EXAMPLES_FILE)

    def delete(self, workspace: str, record_id: str) -> None:
        spec = registry.get_agent_raw(record_id)
        if not _syncable(spec, workspace):
            return
        try:
            registry.remove_agent(record_id)
        except ValueError as exc:  # other agents extend it
            raise AdapterError(str(exc)) from exc
        if _own_definition(spec):
            pa.delete_definition(spec.id)


register(AgentAdapter())
