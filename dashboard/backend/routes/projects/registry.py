"""The project registry chat."""
from ._common import (_project_to_dict, store)
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tasks import service as tasks_service
from workspace import project_folder_name
from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router


router = APIRouter(prefix="/api/projects", tags=["projects"])

# and its "entity" is the workspace, not a project row.
#
# Until this existed, project_manager was a system agent with no caller at all.

REGISTRY_AGENT_ID = "project_manager"
REGISTRY_CHAT_KIND = "projects"


class RegistryChatIn(BaseModel):
    message: str = ""
    #: Accepted in the body too, but the query parameter wins — the shared SSE
    #: client posts only ``{message}``, so the query string is how every caller
    #: actually gets the workspace across.
    workspace: Optional[str] = None


def _registry_chat_id(workspace: Optional[str]) -> str:
    """One conversation per workspace: the registry is workspace-scoped, and a
    single global thread would mix unrelated efforts."""
    return (workspace or "default").strip() or "default"


def _registry_state(workspace: str) -> dict:
    """The projects in this workspace, with their task counts."""
    all_tasks = tasks_service.list_tasks()
    out = []
    for p in store().list():
        if p.workspace != workspace:
            continue
        d = _project_to_dict(p)
        d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == p.id)
        d["folder"] = project_folder_name(p.name)
        out.append(d)
    return {"workspace": workspace, "projects": out}


def _registry_chat_prompt(workspace: str, history: list, user_message: str) -> str:
    """One turn's prompt: the workspace's registry, then the talk."""
    from chat.entity_chat import transcript_block

    state = _registry_state(workspace)
    parts = [
        "You are managing the PROJECT REGISTRY of one workspace in this "
        "platform. The user is looking at the project list: every change you "
        "make with your tools appears there.",
        "",
        f"Workspace: {workspace}",
        "",
        "=== Projects in this workspace ===",
        json.dumps(state["projects"], ensure_ascii=False, indent=2, default=str),
        "",
        "Rules for this conversation:",
        f"- Create projects in workspace '{workspace}' unless the user names a "
        "different one.",
        "- A project is a folder plus a record. Creating one does not analyse "
        "any code and does not plan any work: the Architect Agent builds the "
        "structure graph from the project's own page, and the Planner turns "
        "that into tasks. Say which of those comes next instead of pretending "
        "to have done it.",
        "- Deleting a project removes the record, not the folder. Say so before "
        "deleting, and never delete one the user has not named.",
        "- A project carrying tasks is not an empty record. Report the count "
        "before proposing to retire it.",
        "- When the user only asks a question, answer it without changing "
        "anything.",
        "- Finish with one short paragraph: what exists now, and what the next "
        "step is.",
    ]
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _load_registry_chat(request):
    from types import SimpleNamespace

    workspace = _registry_chat_id(request.query_params.get("workspace"))
    return SimpleNamespace(entity_id=workspace, workspace=workspace)


def _load_registry_send(request, body):
    from common.bootstrap import ensure_system_agent

    if not ensure_system_agent(REGISTRY_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{REGISTRY_AGENT_ID}' agent is not registered")
    from types import SimpleNamespace

    # Accepted in the body too, but the query parameter wins — the shared SSE
    # client posts only ``{message}``, so the query string is how every caller
    # actually gets the workspace across.
    workspace = _registry_chat_id(request.query_params.get("workspace") or body.get("workspace"))
    ctx = SimpleNamespace(entity_id=workspace, workspace=workspace)
    ctx.before = _registry_state(workspace)
    return ctx


def _registry_summarize(ctx):
    def _summarize() -> str:
        after = _registry_state(ctx.workspace)
        before = ctx.before
        if after == before:
            return ""
        was = {p["id"] for p in before["projects"]}
        now = {p["id"] for p in after["projects"]}
        bits = []
        if now - was:
            bits.append(f"created {len(now - was)} project(s)")
        if was - now:
            bits.append(f"removed {len(was - now)} project(s)")
        if not bits:
            bits.append("updated a project")
        return "Done — " + ", ".join(bits) + "."
    return _summarize


def _registry_context_setup(ctx):
    from common.workspace_context import _workspace_ctx

    # The registry tools resolve the workspace from this ContextVar, so a
    # project lands where the user is looking.
    _workspace_ctx.set(ctx.workspace)


async def _registry_post_turn(queue, ctx):
    await queue.put({"type": "projects", "projects": _registry_state(ctx.workspace)["projects"]})


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=REGISTRY_CHAT_KIND,
    path="/registry/chat",
    load=_load_registry_chat,
    load_for_send=_load_registry_send,
    prompt=lambda ctx, history, msg: _registry_chat_prompt(ctx.workspace, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=REGISTRY_CHAT_KIND, agent_id=REGISTRY_AGENT_ID,
        title=f"{ctx.workspace} · projects", workspace=ctx.workspace,
    ),
    summarize=_registry_summarize,
    context_setup=_registry_context_setup,
    post_turn=_registry_post_turn,
)))

