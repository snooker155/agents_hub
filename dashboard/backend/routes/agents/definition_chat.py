"""The agent definition chat."""
import json

from fastapi import APIRouter, HTTPException
from typing import Any, Dict, List

from agents import registry
from agents import prompt_assembly
from tools.registry import get_all_tools
from chat.entity_chat import EntityChatSpec
from chat.entity_chat_router import EntityChatRoute, build_entity_chat_router




router = APIRouter(prefix="/api/agents", tags=["agents"])

# ── The definition chat ──────────────────────────────────────────────────────
#
# Same shape as the loop, team, scenario and world build chats, with one
# difference in kind: the entity under edit is an agent's own definition. That
# makes the warning below load-bearing — an agent editing a *system* agent is
# detaching it from the shipped seed, and the user should hear that before it
# happens rather than discover it at the next upgrade.

DEFINITION_AGENT_ID = "agent_creator"
DEFINITION_CHAT_KIND = "agentdef"


def _definition_state(agent_id: str) -> Dict[str, Any]:
    """What the agent is editing: the record's editable fields plus the three
    markdown files that become the system prompt."""
    spec = registry.get_agent(agent_id)
    if spec is None:
        return {}
    return {
        "agent_id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "domain": spec.domain,
        "system": spec.system,
        "user_modified": spec.user_modified,
        "tools": list(spec.tools or []),
        "delegates": list(spec.delegates or []),
        "provider": spec.provider,
        "model": spec.model,
        "temperature": spec.temperature,
        "reasoning": dict(spec.reasoning or {}),
        "skills_enabled": spec.skills_enabled,
        "instructions": prompt_assembly.read_instructions(spec.def_id()),
        "capabilities": prompt_assembly.read_capabilities(spec.def_id()),
        "usage": prompt_assembly.read_usage(spec.def_id()),
    }


def _tool_catalog() -> List[Dict[str, str]]:
    """Every tool that could be granted, with what it costs in capability terms.

    The capability flags are in here because the guard will refuse a bad
    combination at save time, and an agent that knows why beforehand can propose
    a workable set instead of discovering the refusal.
    """
    return [
        {"id": t.id, "name": t.name, "category": t.category,
         "description": t.description,
         "grants": ", ".join(sorted(t._grants)) or "nothing"}
        for t in get_all_tools()
    ]


def _definition_chat_prompt(agent_id: str, history: List[dict], user_message: str) -> str:
    """One turn's prompt: the agent under edit, the tools it could hold, the talk."""
    from chat.entity_chat import transcript_block

    state = _definition_state(agent_id)
    parts = [
        "You are editing ONE agent's definition in this platform. The user is "
        "looking at its page: every change you make with modify_agent_tool "
        "appears in the panels beside this chat.",
        "",
        f"Agent under edit: {state.get('name')} (agent_id: {agent_id})",
        "",
        "=== Current definition ===",
        json.dumps(state, ensure_ascii=False, indent=2),
        "",
        "=== Tools that could be granted ===",
        json.dumps(_tool_catalog(), ensure_ascii=False, indent=2),
        "",
        "Rules for this conversation:",
        f"- Apply every change to agent_id '{agent_id}' with modify_agent_tool. "
        "Never create a second agent unless the user explicitly asks for one.",
        "- `system_prompt` APPENDS to instructions.md by default. For a rewrite "
        "pass replace_system_prompt=true. Know which one you are doing and say so.",
        "- instructions.md, capabilities.md and usage.md are all concatenated "
        "into the system prompt. So capabilities.md must describe tools the "
        "agent actually holds: a tool named there that it does not have is a "
        "promise the runtime cannot keep, and the agent will try the call and "
        "fail. If you change the tool list, update capabilities.md in the same "
        "turn.",
        "- Granting tools can be refused. An agent that can read private data, "
        "ingest untrusted text AND send data outside is blocked outright. Check "
        "the grants column above before proposing a set, and if the job really "
        "needs all three, propose splitting it across two agents instead.",
        "- When the user only asks a question, answer it without changing anything.",
        "- Finish with one short paragraph: what you changed and what the agent "
        "will now do differently.",
    ]
    if state.get("system") and not state.get("user_modified"):
        parts.insert(4, (
            "!! This is a SYSTEM agent. It ships with the product and is kept in "
            "sync with what the product ships. The first edit marks it as the "
            "operator's and it stops receiving those updates, permanently. Say "
            "this plainly and get a clear yes before your first modify call in "
            "this conversation. A question about the agent is not a yes."
        ))
    talk = transcript_block(history[:-1])
    if talk:
        parts += ["", "=== Conversation so far ===", talk]
    parts += ["", "=== The user's latest message ===", user_message]
    return "\n".join(parts)


def _load_definition_chat(request):
    """Path param only: the agent under edit, 404 when it does not exist."""
    from types import SimpleNamespace

    agent_id = request.path_params["agent_id"]
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return SimpleNamespace(entity_id=agent_id, workspace=None, agent_spec=spec)


def _load_definition_send(request, body):
    from common.bootstrap import ensure_system_agent

    ctx = _load_definition_chat(request)
    if not ensure_system_agent(DEFINITION_AGENT_ID):
        raise HTTPException(status_code=503,
                            detail=f"The '{DEFINITION_AGENT_ID}' agent is not registered")
    ctx.workspace = request.query_params.get("workspace")
    ctx.before = _definition_state(ctx.entity_id)
    return ctx


def _definition_summarize(ctx):
    def _summarize() -> str:
        after = _definition_state(ctx.entity_id)
        if not after:
            return "The agent is gone."
        before = ctx.before
        if after == before:
            return ""
        bits = []
        if after["instructions"] != before["instructions"]:
            bits.append("rewrote its instructions")
        if after["capabilities"] != before["capabilities"]:
            bits.append("updated what it says it can do")
        if after["usage"] != before["usage"]:
            bits.append("updated when to use it")
        if after["tools"] != before["tools"]:
            was, now = len(before["tools"]), len(after["tools"])
            bits.append(f"changed its tools ({was} → {now})")
        if after["model"] != before["model"] or after["provider"] != before["provider"]:
            bits.append("changed its model")
        if after["description"] != before["description"]:
            bits.append("rewrote its description")
        return ("Done — " + ", ".join(bits) + ".") if bits else "Done — the agent was updated."
    return _summarize


def _definition_context_setup(ctx):
    if ctx.workspace:
        from common.workspace_context import _workspace_ctx
        _workspace_ctx.set(ctx.workspace)


async def _definition_post_turn(queue, ctx):
    after = registry.get_agent(ctx.entity_id)
    if after:
        await queue.put({"type": "agentdef", "agent": _definition_state(ctx.entity_id)})


router.include_router(build_entity_chat_router(EntityChatRoute(
    kind=DEFINITION_CHAT_KIND,
    path="/{agent_id}/definition/chat",
    load=_load_definition_chat,
    load_for_send=_load_definition_send,
    prompt=lambda ctx, history, msg: _definition_chat_prompt(ctx.entity_id, history, msg),
    spec=lambda ctx: EntityChatSpec(
        kind=DEFINITION_CHAT_KIND, agent_id=DEFINITION_AGENT_ID,
        title=f"{ctx.agent_spec.name} · definition", workspace=ctx.workspace,
    ),
    summarize=_definition_summarize,
    context_setup=_definition_context_setup,
    post_turn=_definition_post_turn,
)))


