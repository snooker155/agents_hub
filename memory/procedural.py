from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, List, Literal, Optional, Sequence
from uuid import UUID, uuid4

from filelock import FileLock
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from common.paths import WORKSPACES_ROOT


# ── Model ─────────────────────────────────────────────────────────────────────

class Procedure(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    # "When to use this" — matched against task instructions to surface relevant skills
    description: str
    steps: List[str]
    tags: List[str] = Field(default_factory=list)
    source: Literal["user", "agent"] = "user"
    agent_id: str                  # which agent owns this procedure
    workspace: str                 # which workspace this procedure lives in
    # None for user-authored; tracked for agent-discovered procedures over time
    success_rate: Optional[float] = None
    use_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        object.__setattr__(self, "updated_at", datetime.now(timezone.utc))


# ── Store ─────────────────────────────────────────────────────────────────────

def _json_default(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o)!r} is not JSON serializable")


def _workspace_procedures_path(workspace: str) -> Path:
    return WORKSPACES_ROOT / workspace / "procedures.json"


class ProcedureStore:
    """File-based store for Procedure objects, scoped to a workspace."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.path = _workspace_procedures_path(workspace)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write([])

    def load(self, timeout: float = 10.0) -> List[Procedure]:
        with FileLock(str(self.lock_path), timeout=timeout):
            return self._load_unlocked()

    def _load_unlocked(self) -> List[Procedure]:
        try:
            text = self.path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            return [Procedure(**obj) for obj in json.loads(text)]
        except Exception:
            return []

    def save(self, procedures: Sequence[Procedure], timeout: float = 10.0) -> None:
        with FileLock(str(self.lock_path), timeout=timeout):
            self._atomic_write([p.model_dump() for p in procedures])

    def get(self, procedure_id: UUID | str, timeout: float = 10.0) -> Optional[Procedure]:
        pid = str(procedure_id)
        for p in self.load(timeout=timeout):
            if str(p.id) == pid:
                return p
        return None

    def add(self, procedure: Procedure, timeout: float = 10.0) -> Procedure:
        with FileLock(str(self.lock_path), timeout=timeout):
            procedures = self._load_unlocked()
            procedures.append(procedure)
            self._atomic_write([p.model_dump() for p in procedures])
        return procedure

    def update(self, procedure: Procedure, timeout: float = 10.0) -> bool:
        pid = str(procedure.id)
        with FileLock(str(self.lock_path), timeout=timeout):
            procedures = self._load_unlocked()
            for i, p in enumerate(procedures):
                if str(p.id) == pid:
                    procedures[i] = procedure
                    self._atomic_write([p.model_dump() for p in procedures])
                    return True
        return False

    def delete(self, procedure_id: UUID | str, timeout: float = 10.0) -> bool:
        pid = str(procedure_id)
        with FileLock(str(self.lock_path), timeout=timeout):
            procedures = self._load_unlocked()
            new_list = [p for p in procedures if str(p.id) != pid]
            if len(new_list) == len(procedures):
                return False
            self._atomic_write([p.model_dump() for p in new_list])
            return True

    def _atomic_write(self, payload: Iterable[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(list(payload), ensure_ascii=False, indent=2, default=_json_default)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)


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


_SKILL_INQUIRY_WORDS = {"skill", "skills", "procedure", "procedures"}


def is_skills_inquiry(query: str) -> bool:
    """Return True if the query is asking about what skills the agent has."""
    return bool(_SKILL_INQUIRY_WORDS & set(query.lower().split()))


def _format_procedures(procedures: List[Procedure], header: str) -> str:
    lines = [f"## {header}\n"]
    for p in procedures:
        lines.append(f"**{p.name}** (ID: `{p.id}`)")
        lines.append(f"_{p.description}_")
        for i, step in enumerate(p.steps, 1):
            lines.append(f"  {i}. {step}")
        lines.append("")
    return "\n".join(lines) + "\n\n"


SKILLS_RELEVANCE_THRESHOLD = 0.15


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


def inject_procedural_context(agent_id: str, workspace: str, instruction: str) -> str:
    """Return a block of matching skill steps to prepend to the instruction.

    Only skills whose name/description/tags are relevant to the instruction
    (score above threshold) have their full steps injected. The catalog is
    already in the system prompt, so we only add the actionable detail here.
    """
    try:
        store = ProcedureStore(workspace)
        agent_procedures = [p for p in store.load() if p.agent_id == agent_id]

        if not agent_procedures:
            return ""

        if is_skills_inquiry(instruction):
            # For explicit skill queries, show the full listing with steps
            return _format_procedures(agent_procedures, "Your Skills")

        matched = [
            p for p in agent_procedures
            if _score_relevance(p, instruction) >= SKILLS_RELEVANCE_THRESHOLD
        ]
        if not matched:
            return ""

        return _format_procedures(matched, "Relevant Skills for This Task")
    except Exception:
        return ""


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
        name: str = Field(..., description="Name of the skill to retrieve (as listed in your system prompt)")

    def _get_skill(name: str) -> str:
        try:
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
