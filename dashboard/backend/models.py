"""
Pydantic models for API request/response validation.
"""
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


class AgentCreateCustom(BaseModel):
    id: str
    name: str
    description: str = ""
    domain: str = "general"
    system_prompt: str
    tools: List[str] = ["read_file", "write_file", "list_files"]
    capacity: int = 1
    # Workspace the agent is created in. When set (and not 'default'), the agent
    # is owned by that workspace and only visible there until it is shared.
    workspace: Optional[str] = None


class AgentSharingUpdate(BaseModel):
    shared: bool


class AgentMemoryUpdate(BaseModel):
    memory_type: str
    memory_data: Any = None


class AgentSkillsConfigUpdate(BaseModel):
    skills_enabled: bool


class AgentSkillCreate(BaseModel):
    workspace: str
    name: str
    description: str
    steps: List[str]
    tags: List[str] = []


class AgentToolsUpdate(BaseModel):
    tools: List[str] = []


class AgentReasoningUpdate(BaseModel):
    think_enabled: Optional[bool] = None  # add the think tool
    think_mode: Optional[str] = None      # standard | deep | analytical
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


class DecomposeRequest(BaseModel):
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    verbose: bool = True


class WorkspaceCreate(BaseModel):
    name: Optional[str] = None


class WorkspaceAgentAction(BaseModel):
    agent_id: str


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


class ChatHistoryMessage(BaseModel):
    role: str  # "user" or "agent"
    content: str


class ChatAttachment(BaseModel):
    filename: str
    content: str = ""
    # Optional base64-encoded binary payload. When set, the materializer writes
    # bytes to disk instead of UTF-8 text — used for Telegram photo/document
    # uploads and any future binary web uploads.
    content_b64: Optional[str] = None
    mime_type: Optional[str] = None
    store_to_workspace: bool = False
    stored_workspace_path: Optional[str] = None


class ChatRequest(BaseModel):
    agent_id: Optional[str] = None
    flow_id: Optional[str] = None
    message: str
    workspace: Optional[str] = None
    project_id: Optional[str] = None
    history: List[ChatHistoryMessage] = []
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    attachments: List[ChatAttachment] = []

    @model_validator(mode="after")
    def _require_target(self):
        if not self.agent_id and not self.flow_id:
            raise ValueError("Either agent_id or flow_id must be provided")
        return self


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


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = None
    tags: Optional[List[str]] = None
    repo: Optional[Dict[str, Any]] = None
    frontend: Optional[Dict[str, Any]] = None
    backend: Optional[Dict[str, Any]] = None


class ProjectApiRequest(BaseModel):
    method: str = "GET"
    path: str = "/"
    headers: Optional[Dict[str, str]] = None
    body: Optional[Any] = None
    base_url: Optional[str] = None
