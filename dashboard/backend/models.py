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
    tools: List[str] = ["remote"]
    capabilities: Optional[List[str]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_tools(cls, data):
        if isinstance(data, dict) and "tools" not in data and "capabilities" in data:
            caps = data.get("capabilities")
            if isinstance(caps, list):
                data["tools"] = caps
        return data


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


class AgentToolsUpdate(BaseModel):
    tools: List[str] = []


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


class MemoryFileAdd(BaseModel):
    name: str
    content: str


class MemoryFileUpdate(BaseModel):
    content: str


class MemoryFileProcess(BaseModel):
    chunk_size: int = 500
    overlap: int = 50


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
