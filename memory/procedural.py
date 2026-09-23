from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional, Sequence
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from common import db
from common.docstore import DocStore
from common.paths import PROCEDURES_FILE as _PROCEDURES_FILE
from common.paths import WORKSPACES_ROOT, ensure_agents_hub_root


# ── Model ─────────────────────────────────────────────────────────────────────

class Procedure(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    # "When to use this" — matched against task instructions to surface relevant skills
    description: str
    steps: List[str]
    tags: List[str] = Field(default_factory=list)
    source: Literal["user", "agent"] = "user"
    # The agent this skill is attached to. Empty means it is a catalog entry in
    # its workspace — visible in the Skills page and installable onto an agent,
    # but not injected into anyone's prompt until it is.
    agent_id: str = ""
    workspace: str                 # which workspace this procedure lives in
    # Published to the global skills catalog, mirroring AgentSpec.shared: off by
    # default (a skill belongs to the workspace that authored it), on once the
    # author publishes it, at which point any workspace may install a copy.
    shared: bool = False
    # Set on an installed copy, pointing at the published skill it came from.
    origin_skill_id: Optional[str] = None
    # None for user-authored; tracked for agent-discovered procedures over time
    success_rate: Optional[float] = None
    use_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        object.__setattr__(self, "updated_at", datetime.now(timezone.utc))


# ── Store ─────────────────────────────────────────────────────────────────────

def _model_to_dict(procedure: Procedure) -> dict:
    # JSON mode: enums as their values, datetimes as ISO strings, UUIDs as
    # strings, exactly what the JSON files used to hold.
    return procedure.model_dump(mode="json")


def _record_key(rec: Any) -> Optional[str]:
    return str(rec.get("id")) if isinstance(rec, dict) and rec.get("id") else None


_LEGACY_MIGRATED = False


def _migrate_legacy_files() -> None:
    """One-shot: merge any per-workspace procedures.json files directly into
    the single 'procedures' collection.

    Each old file lives at .agents_hub/workspaces/<ws>/procedures.json and is a
    list of Procedure records. Records already present (by id, or already
    imported from the legacy single-file procedures.json) are skipped; the
    rest are put into the store, then each legacy file is renamed to
    procedures.json.migrated.bak so a second boot doesn't re-import them.
    """
    global _LEGACY_MIGRATED
    if _LEGACY_MIGRATED:
        return
    _LEGACY_MIGRATED = True

    if not WORKSPACES_ROOT.exists():
        return

    legacy_files = list(WORKSPACES_ROOT.glob("*/procedures.json"))
    if not legacy_files:
        return

    ensure_agents_hub_root()

    docs_store = DocStore("procedures", legacy_file=_PROCEDURES_FILE, legacy_key=_record_key)
    with db.transaction():
        # Reading .keys() imports the legacy single-file procedures.json first
        # (if it still exists), so per-workspace records are only added when
        # not already covered by that import.
        existing_ids = set(docs_store.keys())
        for legacy in legacy_files:
            try:
                text = legacy.read_text(encoding="utf-8")
                records = json.loads(text) if text.strip() else []
            except Exception:
                records = []
            if not isinstance(records, list):
                continue
            workspace_name = legacy.parent.name
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                # Old records may not carry `workspace` — backfill from folder name.
                rec.setdefault("workspace", workspace_name)
                rid = str(rec.get("id") or "")
                if rid and rid in existing_ids:
                    continue
                store_key = rid or str(uuid4())
                existing_ids.add(store_key)
                docs_store.put(store_key, rec)

            try:
                legacy.rename(legacy.with_suffix(legacy.suffix + ".migrated.bak"))
            except Exception:
                pass


class ProcedureStore:
    """Store for Procedure objects.

    All procedures live in one collection ('procedures') in the ``documents``
    table. The `workspace` argument is retained on the constructor so call
    sites stay unchanged — it filters records on read and stamps the
    `workspace` field on writes.
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.path = _PROCEDURES_FILE
        ensure_agents_hub_root()
        _migrate_legacy_files()
        self.docs = DocStore("procedures", legacy_file=self.path, legacy_key=_record_key)

    # ── unfiltered I/O (operates on the whole collection) ───────────────────

    def _load_all(self) -> List[Procedure]:
        out: List[Procedure] = []
        for obj in self.docs.values():
            try:
                out.append(Procedure(**obj))
            except Exception:
                continue
        return out

    # ── workspace-scoped API ────────────────────────────────────────────────

    def load(self, timeout: float = 10.0) -> List[Procedure]:
        """Return procedures belonging to this workspace."""
        return [p for p in self._load_all() if p.workspace == self.workspace]

    def save(self, procedures: Sequence[Procedure], timeout: float = 10.0) -> None:
        """Replace this workspace's procedures with the given list (other workspaces untouched)."""
        with self.docs.transaction():
            others = {
                k: v for k, v in self.docs.all().items()
                if not (isinstance(v, dict) and v.get("workspace") == self.workspace)
            }
            stamped: dict = {}
            for p in procedures:
                if p.workspace != self.workspace:
                    p = p.model_copy(update={"workspace": self.workspace})
                stamped[str(p.id)] = _model_to_dict(p)
            self.docs.replace_all({**others, **stamped})

    def get(self, procedure_id: UUID | str, timeout: float = 10.0) -> Optional[Procedure]:
        doc = self.docs.get(str(procedure_id))
        if doc is None:
            return None
        try:
            p = Procedure(**doc)
        except Exception:
            return None
        return p if p.workspace == self.workspace else None

    def add(self, procedure: Procedure, timeout: float = 10.0) -> Procedure:
        if procedure.workspace != self.workspace:
            procedure = procedure.model_copy(update={"workspace": self.workspace})
        self.docs.put(str(procedure.id), _model_to_dict(procedure))
        return procedure

    def update(self, procedure: Procedure, timeout: float = 10.0) -> bool:
        pid = str(procedure.id)
        if procedure.workspace != self.workspace:
            procedure = procedure.model_copy(update={"workspace": self.workspace})
        with self.docs.transaction():
            existing = self.docs.get(pid)
            if not isinstance(existing, dict) or existing.get("workspace") != self.workspace:
                return False
            self.docs.put(pid, _model_to_dict(procedure))
            return True

    def delete(self, procedure_id: UUID | str, timeout: float = 10.0) -> bool:
        pid = str(procedure_id)
        with self.docs.transaction():
            existing = self.docs.get(pid)
            if not isinstance(existing, dict) or existing.get("workspace") != self.workspace:
                return False
            return self.docs.delete(pid)


def all_procedures() -> List[Procedure]:
    """Every procedure in every workspace, newest store state.

    The per-workspace ``ProcedureStore`` deliberately filters on read; the
    global skills catalog is the one caller that needs the unfiltered view.
    """
    return ProcedureStore("default")._load_all()


def find_procedure(procedure_id: UUID | str) -> Optional[Procedure]:
    """Look a procedure up by id across all workspaces."""
    pid = str(procedure_id)
    for p in all_procedures():
        if str(p.id) == pid:
            return p
    return None


# ── Relevance matching ────────────────────────────────────────────────────────

def _score_relevance(procedure: Procedure, query: str) -> float:
    """Keyword overlap score. No vector store needed — procedures are few and named precisely."""
    query_words = set(query.lower().split())
    candidate = f"{procedure.name} {procedure.description} {' '.join(procedure.tags)}".lower()
    if not query_words:
        return 0.0
    return len(query_words & set(candidate.split())) / len(query_words)


def find_relevant_procedures(
    query: str,
    agent_id: str,
    workspace: str,
    top_k: int = 3,
) -> List[Procedure]:
    """Return top-k procedures for this agent+workspace most relevant to the query."""
    store = ProcedureStore(workspace)
    agent_procedures = [p for p in store.load() if p.agent_id == agent_id]
    scored = sorted(
        [(p, _score_relevance(p, query)) for p in agent_procedures],
        key=lambda x: x[1],
        reverse=True,
    )
    return [p for p, score in scored[:top_k] if score > 0.0]


def inject_skills_catalog(agent_id: str, workspace: str, system_prompt: str) -> str:
    """Append a skills catalog (name + description) to the system prompt.

    This gives the agent a persistent overview of all available skills so it can
    recognise when one applies, without bloating every instruction with full steps.
    Returns the system prompt unchanged when no skills exist.
    """
    try:
        store = ProcedureStore(workspace)
        procedures = [p for p in store.load() if p.agent_id == agent_id]
        if not procedures:
            return system_prompt
        lines = ["\n\n## Available Skills\n",
                 "The following skills are available to you. "
                 "When the task matches a skill, its full steps will be provided automatically. "
                 "Use `get_skill` with the skill's name to fetch its steps.\n"]
        for p in procedures:
            lines.append(f"- **{p.name}**: {p.description}")
        return system_prompt + "\n".join(lines) + "\n"
    except Exception:
        return system_prompt


# ── Scoped tool factory ───────────────────────────────────────────────────────

def create_skills_tools(agent_id: str, workspace: str) -> List[Any]:
    """Create skill tools scoped to a specific agent+workspace pair.

    Returns three StructuredTool instances with agent_id and workspace baked in,
    so agents cannot read or write skills belonging to other agents or workspaces.
    """

    # list_skills ──────────────────────────────────────────────────────────────

    class ListSkillsInput(BaseModel):
        tags: Optional[str] = Field(None, description="Comma-separated tags to filter by (optional)")
        query: Optional[str] = Field(None, description="Natural-language query to rank results by relevance")

    def _list_skills(tags: Optional[str] = None, query: Optional[str] = None) -> str:
        try:
            store = ProcedureStore(workspace)
            procedures = [p for p in store.load() if p.agent_id == agent_id]

            if tags:
                tag_set = {t.strip().lower() for t in tags.split(",")}
                procedures = [p for p in procedures if tag_set & {t.lower() for t in p.tags}]

            if query:
                procedures = sorted(procedures, key=lambda p: _score_relevance(p, query), reverse=True)

            result = [
                {
                    "name": p.name,
                    "description": p.description,
                    "tags": p.tags,
                    "source": p.source,
                    "use_count": p.use_count,
                }
                for p in procedures
            ]
            return json.dumps({"ok": True, "skills": result, "count": len(result)})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"list_skills failed: {e}"})

    list_skills_tool = StructuredTool.from_function(
        name="list_skills",
        description=(
            "List your available skills (reusable step-by-step procedures for this workspace). "
            "Filter by tags or rank by a natural-language query. "
            "Use this before starting a task to discover what procedures already exist."
        ),
        func=_list_skills,
        args_schema=ListSkillsInput,
    )

    # get_skill ────────────────────────────────────────────────────────────────

    class GetSkillInput(BaseModel):
        # Tolerant on purpose: some models call get_skill with a missing/empty
        # argument. Accepting an optional value lets us return a helpful JSON
        # error from the function body instead of raising a Pydantic
        # ValidationError that bubbles up and aborts the run.
        name: Optional[str] = Field(
            None, description="Name of the skill to retrieve (as listed in your system prompt)"
        )

    def _get_skill(name: Optional[str] = None) -> str:
        try:
            if not name or not str(name).strip():
                available = [
                    p.name for p in ProcedureStore(workspace).load() if p.agent_id == agent_id
                ]
                return json.dumps({
                    "ok": False,
                    "error": "get_skill requires a non-empty 'name' argument.",
                    "available": available,
                })
            store = ProcedureStore(workspace)
            agent_procedures = [p for p in store.load() if p.agent_id == agent_id]
            target = name.strip().lower()
            matches = [p for p in agent_procedures if p.name.strip().lower() == target]
            if not matches:
                available = [p.name for p in agent_procedures]
                return json.dumps({
                    "ok": False,
                    "error": f"Skill not found: {name!r}",
                    "available": available,
                })
            if len(matches) > 1:
                return json.dumps({
                    "ok": False,
                    "error": f"Multiple skills named {name!r} — name collision in this workspace.",
                    "candidates": [
                        {"name": p.name, "description": p.description, "tags": p.tags}
                        for p in matches
                    ],
                })
            p = matches[0]
            p.use_count += 1
            p.touch()
            store.update(p)

            return json.dumps({
                "ok": True,
                "name": p.name,
                "description": p.description,
                "steps": p.steps,
                "tags": p.tags,
                "source": p.source,
            })
        except Exception as e:
            return json.dumps({"ok": False, "error": f"get_skill failed: {e}"})

    get_skill_tool = StructuredTool.from_function(
        name="get_skill",
        description=(
            "Retrieve the full step-by-step instructions for a skill by name. "
            "Skill names are listed in your system prompt under 'Available Skills'."
        ),
        func=_get_skill,
        args_schema=GetSkillInput,
    )

    # create_skill ─────────────────────────────────────────────────────────────

    class CreateSkillInput(BaseModel):
        name: str = Field(..., description="Short name for this skill")
        description: str = Field(..., description="When to use this skill — matched against future task instructions")
        steps: List[str] = Field(..., description="Ordered list of steps that make up this skill")
        tags: Optional[str] = Field(None, description="Comma-separated tags, e.g. 'debugging,python,api'")

    def _create_skill(name: str, description: str, steps: List[str], tags: Optional[str] = None) -> str:
        try:
            store = ProcedureStore(workspace)
            target = name.strip().lower()
            for p in store.load():
                if p.agent_id == agent_id and p.name.strip().lower() == target:
                    return json.dumps({
                        "ok": False,
                        "error": f"A skill named {name!r} already exists. Pick a different name or update the existing one.",
                    })
            tag_list = [t.strip() for t in tags.split(",")] if tags else []
            procedure = Procedure(
                name=name,
                description=description,
                steps=steps,
                tags=tag_list,
                source="agent",
                agent_id=agent_id,
                workspace=workspace,
            )
            store.add(procedure)
            return json.dumps({"ok": True, "name": name})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"create_skill failed: {e}"})

    create_skill_tool = StructuredTool.from_function(
        name="create_skill",
        description=(
            "Create and save a new reusable skill. "
            "Use this when you discover a sequence of steps that worked well and could apply to similar future tasks. "
            "Write the description as 'when to use this' so it gets injected automatically on matching future instructions."
        ),
        func=_create_skill,
        args_schema=CreateSkillInput,
    )

    return [list_skills_tool, get_skill_tool, create_skill_tool]
