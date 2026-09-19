"""
Chat request/response models.

The Pydantic models for a chat exchange. They live in the ``chat`` core package
(rather than the dashboard's backend ``models`` module) so the chat pipelines can
be imported without the dashboard backend on ``sys.path``. The backend ``models``
module re-exports them for the route layer and other backend consumers.
"""
from typing import List, Optional

from pydantic import BaseModel, model_validator


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


class ChatReference(BaseModel):
    """A service entity the user attached to a message from the composer.

    The browser sends only the pointer (``kind`` + ``id``); the server renders
    the record into ``content`` at request time (see ``chat.references``), so a
    stale or deleted entity cannot be smuggled in as prompt text and the payload
    stays small no matter how large the entity is.
    """
    kind: str
    id: str
    label: str = ""
    # Filled in server-side by ``resolve_references``; anything a client sends
    # here is overwritten.
    content: str = ""


class ChatRequest(BaseModel):
    agent_id: Optional[str] = None
    flow_id: Optional[str] = None
    # A team target: the message is handed to a roster of agents that talk to
    # each other (teams/), rather than to one agent or a flow's DAG.
    team_id: Optional[str] = None
    message: str
    workspace: Optional[str] = None
    project_id: Optional[str] = None
    history: List[ChatHistoryMessage] = []
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    attachments: List[ChatAttachment] = []
    # Hub entities attached alongside the files — a task, view, project,
    # scenario, loop, flow, team, agent or scheduled job the user picked in the
    # composer. Rendered into the prompt for this turn only.
    references: List[ChatReference] = []
    # Where this exchange originated, recorded as the run's ``message_origin`` so
    # the Messages list can distinguish e.g. Telegram runs from web chat. None
    # defaults to "chat" (the web Chat page) at run-record time.
    source: Optional[str] = None
    # For the multiplexed transport (POST /api/chat/stream-sse): the caller's SSE
    # client id, so the server can deliver this run's events over the browser's
    # single existing /api/stream connection instead of a dedicated stream.
    client_id: Optional[str] = None
    # The Visualization Studio binds a conversation to one live view. When set,
    # the pipeline exposes it to the view mutation tools (via current_view_id)
    # and injects a compact scene-context note so the agent knows what it edits.
    view_id: Optional[str] = None
    # Message addressed to an existing agent instance rather than to the agent
    # in general: the run is recorded against that live copy, so writing to a
    # copy that already finished continues *its* thread instead of forking a new
    # one. Set by the instances route; the web Chat page leaves it empty.
    instance_id: Optional[str] = None

    @model_validator(mode="after")
    def _require_target(self):
        if not self.agent_id and not self.flow_id and not self.team_id:
            raise ValueError("One of agent_id, flow_id or team_id must be provided")
        return self
