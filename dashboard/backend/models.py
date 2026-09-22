"""
Pydantic models for API request/response validation.
"""
from datetime import datetime
from pydantic import BaseModel, model_validator
from typing import List, Optional, Dict, Any


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    workspace_name: Optional[str] = None
    workspace: Optional[str] = None
    project: Optional[str] = None
    should_decompose: bool = False
    parent_id: Optional[str] = None
    priority: Optional[str] = None
    project_id: Optional[str] = None
    # Task IDs or keys (e.g. DEMO-12) that must complete before this task runs
    depends: Optional[List[str]] = None
    # Optional deadline, ISO 8601. Naive values are assumed UTC.
    due_at: Optional[datetime] = None


class TaskWorkspaceUpdate(BaseModel):
    workspace_name: Optional[str] = None
    workspace: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    project_id: Optional[str] = None
    project: Optional[str] = None
    # Replace the task's dependency list (task IDs or keys); [] clears it
    depends: Optional[List[str]] = None
    # Optional deadline, ISO 8601. Naive values are assumed UTC. Send null to clear it.
    due_at: Optional[datetime] = None


class AgentCreateCustom(BaseModel):
    id: str
    name: str
    description: str = ""
    domain: str = "general"
    system_prompt: Optional[str] = None
    tools: List[str] = ["read_file", "write_file", "list_files"]
    capacity: int = 1
    # Workspace the agent is created in. When set (and not 'default'), the agent
    # is owned by that workspace and only visible there until it is shared.
    workspace: Optional[str] = None
    # When set, reuse an existing definition folder instead of authoring a new
    # one. system_prompt is then ignored and write_instructions is skipped.
    definition_id: Optional[str] = None


class AgentCloneToWorkspace(BaseModel):
    # Target workspace the new record is bound to (owner_workspace).
    workspace: str
    # Optional explicit id for the new record; defaults to "<def_id>@<workspace>".
    new_id: Optional[str] = None


class AgentSharingUpdate(BaseModel):
    shared: bool


class AgentDescriptionUpdate(BaseModel):
    description: str


class AgentMemoryUpdate(BaseModel):
    memory_type: str
    memory_data: Any = None
    # Workspace the assignment applies to (memory is per-workspace; defaults
    # to 'default'). Outside the agent's home workspace the assignment is
    # stored as a workspace metadata override, not on the agent record.
    workspace: Optional[str] = None


class AgentSkillsConfigUpdate(BaseModel):
    skills_enabled: bool


class AgentEpisodicConfigUpdate(BaseModel):
    # Tri-state: null = auto (off for local providers), True = on, False = off.
    episodic_write_enabled: Optional[bool] = None


class AgentResponseFormatUpdate(BaseModel):
    # Structured response the agent may emit: "none" | "buttons" | "telegram".
    response_format: str = "none"


class AgentClarifyGateUpdate(BaseModel):
    # When True, the agent asks clarifying questions before executing if it lacks
    # enough information, instead of proceeding on assumptions.
    clarify_gate: bool = False


class AgentSelfDelegationUpdate(BaseModel):
    # When True, the agent may target itself in run_agent_tool / assign_agent_tool.
    # Off by default because a self-run recurses the same agent.
    allow_self_delegation: bool = False


class AgentSkillCreate(BaseModel):
    workspace: str
    name: str
    description: str
    steps: List[str]
    tags: List[str] = []


# ── Skills catalog (routes/skills.py) ─────────────────────────────────────────

class SkillCreate(BaseModel):
    workspace: str
    name: str
    description: str
    steps: List[str]
    tags: List[str] = []
    # Empty means a catalog entry: it lives in the workspace but is not attached
    # to any agent, so nothing injects it until it is installed onto one.
    agent_id: str = ""


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[List[str]] = None
    tags: Optional[List[str]] = None


class SkillSharingUpdate(BaseModel):
    # Publish to (or withdraw from) the global skills catalog.
    shared: bool = False


class SkillInstall(BaseModel):
    # Copy this skill into ``workspace``; attach it to ``agent_id`` when given.
    workspace: str
    agent_id: str = ""


class AgentToolsUpdate(BaseModel):
    tools: List[str] = []


class AgentDelegatesUpdate(BaseModel):
    # Empty list = no restriction (delegate to any agent in the workspace).
    delegates: List[str] = []


class AgentReasoningUpdate(BaseModel):
    think_enabled: Optional[bool] = None  # add the think scratchpad tool
    think_mode: Optional[str] = None      # standard | deep | analytical (tool prompt)
    thinking_level: Optional[str] = None  # off | low | medium | high (native model reasoning)
    plan_enabled: Optional[bool] = None   # add the plan tool
    plan_format: Optional[str] = None     # structured | bullet | numbered | freeform


class AgentModelUpdate(BaseModel):
    provider: Optional[str] = None       # openai | anthropic | google | ollama | lmstudio | inherit
    model: Optional[str] = None
    api_key: Optional[str] = None        # per-agent key override; None = keep existing
    base_url: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    clear_temperature: bool = False       # explicitly remove temperature override
    clear_max_tokens: bool = False        # explicitly remove max_tokens override
    clear_api_key: bool = False           # explicitly remove api_key override


class AgentAssign(BaseModel):
    agent_id: str
    params: Optional[Dict[str, Any]] = None
    require_approval: bool = False


class TaskAnswer(BaseModel):
    # The user's answer to a task that is paused in the awaiting_input state.
    answer: str


class DecomposeRequest(BaseModel):
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    verbose: bool = True


class WorkspaceCreate(BaseModel):
    name: Optional[str] = None


class WorkspaceAttach(BaseModel):
    """Register a directory outside the state root as a workspace, in place."""
    # Absolute path as the *backend process* sees it. In a container that is a
    # path inside the container, not on the host.
    path: str
    # Optional, and only as an assertion: an attached workspace is named after
    # the folder it points at, so a different name is rejected rather than
    # silently ignored.
    name: Optional[str] = None


class WorkspaceAgentAction(BaseModel):
    agent_id: str


class WorkspaceFlowAction(BaseModel):
    flow_id: str


class RunAgentRequest(BaseModel):
    agent: str  # pm, ba, sd, tl, be, fe, ops, qa, or 'graph'
    action: str | None = None
    description: str | None = None
    workspace: Optional[str] = None


class UserInputRequest(BaseModel):
    answers: Dict[str, str]
    workspace: str


class MemoryCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "text"
    workspace: Optional[str] = None


class MemoryNoteAdd(BaseModel):
    title: str
    content: str


class MemoryNoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class MemoryStructuredSlotUpsert(BaseModel):
    data: Dict[str, Any]


class OrchestratorSettings(BaseModel):
    enabled: bool = False
    assignment_mode: str = "manual"  # "live" = auto-run on assignment, "manual" = requires approval
    followup_mode: str = "single"  # "continuous" = re-invoke orchestrator after agent finishes, "single" = one pass only
    wait_for_completion: bool = False  # True = poll until agent finishes; False = fire-and-forget (start and stop)
    execution_mode: str = "subprocess"  # "subprocess" = spawn immediately, "node" = delegate to worker node
    max_retries: int = 0  # auto-retry a failed worker run up to N times before blocking the task (0 = off)

    @model_validator(mode="after")
    def _exclusive_modes(self) -> "OrchestratorSettings":
        if self.wait_for_completion and self.followup_mode == "continuous":
            raise ValueError(
                "wait_for_completion and followup_mode=continuous are mutually exclusive: "
                "the orchestrator cannot both wait inline and trigger a follow-up continuation."
            )
        return self


class SessionCreate(BaseModel):
    title: str
    description: str = ""
    agent_id: str
    workspace: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class SessionContextCreate(BaseModel):
    """Create a new session context (process-level) and start its first agent run."""
    title: str
    description: str = ""
    agent_id: str
    workspace: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


# Chat models live in the ``chat`` core package so the pipelines import without
# the backend on sys.path; re-exported here for the route layer / other backend
# consumers that do ``from models import ChatRequest``.
from chat.models import ChatHistoryMessage, ChatAttachment, ChatReference, ChatRequest  # noqa: E402,F401


class ToolSourceUpdate(BaseModel):
    source_code: str


class RepoConfigUpdate(BaseModel):
    type: Optional[str] = None
    url: Optional[str] = None
    branch: Optional[str] = None
    local_path: Optional[str] = None


class FrontendConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    port: Optional[int] = None
    dev_command: Optional[str] = None
    build_dir: Optional[str] = None
    url: Optional[str] = None


class BackendConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    port: Optional[int] = None
    start_command: Optional[str] = None
    swagger_path: Optional[str] = None
    base_url: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    type: Optional[str] = "general"
    workspace: str
    tags: Optional[List[str]] = []
    repo: Optional[Dict[str, Any]] = None
    frontend: Optional[Dict[str, Any]] = None
    backend: Optional[Dict[str, Any]] = None


class ProjectAttach(BaseModel):
    """Register a directory as a project inside a workspace, without copying it."""
    workspace: str
    # Absolute path as the *backend process* sees it (in a container: a path
    # inside the container).
    path: str
    # Defaults to the directory's own name. The project folder inside the
    # workspace is always named after the directory, so the link, the project
    # name and repo.local_path agree.
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = "code"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = None
    tags: Optional[List[str]] = None
    repo: Optional[Dict[str, Any]] = None
    frontend: Optional[Dict[str, Any]] = None
    backend: Optional[Dict[str, Any]] = None


class ProjectImportFromRepo(BaseModel):
    provider: str                      # "github" | "gitlab"
    remote_id: str                     # "owner/repo" / GitLab path_with_namespace
    workspace: str
    name: Optional[str] = None         # defaults to the repo name
    branch: Optional[str] = None       # defaults to the repo default branch
    import_issues: bool = True


class ProjectConnectRepo(BaseModel):
    provider: str
    remote_id: str
    branch: Optional[str] = None
    import_issues: bool = True


class ProjectApiRequest(BaseModel):
    method: str = "GET"
    path: str = "/"
    headers: Optional[Dict[str, str]] = None
    body: Optional[Any] = None
    base_url: Optional[str] = None


class ProjectGraphSave(BaseModel):
    """Hand-edited project structure graph (React-Flow nodes/edges)."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []


class ProjectGraphChat(BaseModel):
    """One interactive build message for a project graph view."""
    message: str


class ProjectTasksChat(BaseModel):
    """One planner-chat turn. Empty message = the default 'generate from graphs'."""
    message: Optional[str] = None
