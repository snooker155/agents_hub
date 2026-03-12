"""
Pydantic models for API request/response validation.
"""
from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    workspace_name: Optional[str] = None
    workspace: Optional[str] = None
    should_decompose: bool = False


class TaskWorkspaceUpdate(BaseModel):
    workspace_name: Optional[str] = None
    workspace: Optional[str] = None


class AgentClone(BaseModel):
    original_id: str
    new_id: str
    new_name: str


class AgentConnect(BaseModel):
    id: str
    name: str
    description: str = ""
    domain: str = "general"
    agent_url: str
    capacity: int = 1
    capabilities: List[str] = ["remote"]


class AgentCreateCustom(BaseModel):
    id: str
    name: str
    description: str = ""
    domain: str = "general"
    system_prompt: str
    tools: List[str] = ["read_file", "write_file", "list_files"]
    capacity: int = 1


class AgentMemoryUpdate(BaseModel):
    memory_type: str
    memory_data: Any = None


class AgentAssign(BaseModel):
    agent_id: str
    params: Optional[Dict[str, Any]] = None


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


class MemoryFileAdd(BaseModel):
    name: str
    content: str


class YamlManifest(BaseModel):
    yaml: str


class OrchestratorSettings(BaseModel):
    enabled: bool = False


class SessionCreate(BaseModel):
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
    content: str
    store_to_workspace: bool = False
    stored_workspace_path: Optional[str] = None


class ChatRequest(BaseModel):
    agent_id: str
    message: str
    workspace: Optional[str] = None
    history: List[ChatHistoryMessage] = []
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    attachments: List[ChatAttachment] = []


class ToolSourceUpdate(BaseModel):
    source_code: str
