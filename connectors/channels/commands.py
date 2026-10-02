"""
Text commands a user can type in any channel.

The same set Telegram answers, transport-free: a channel hands the message
text and the chat key to :func:`handle_command` and sends back whatever
string it returns (``None`` means "not a command, run it as a message").

The one rule that matters is kept here too: ``/workspace`` is read-only. A
chat gets a workspace when an operator binds it from the dashboard
(``POST /api/channels/<name>/bindings``), never by asking the bot, so a user
who finds the bot can neither talk to it (the allowlist) nor grant
themselves a workspace (this file).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from agents import registry
from common.session_broker import notify_change

from .store import ChannelStore

HELP_TEXT = (
    "Agents Hub bot\n"
    "\n"
    "Setup order: an operator binds this chat to a workspace from the "
    "dashboard, then you pick an agent OR a flow allowed in that workspace.\n"
    "\n"
    "Commands:\n"
    "/workspace — show this chat's workspace (set by the operator, not here)\n"
    "/workspaces — list available workspaces\n"
    "/agent [id] — bind this chat to an agent in the current workspace\n"
    "/agents — list agents allowed in the current workspace\n"
    "/flow [id] — bind this chat to a flow in the current workspace\n"
    "/flows — list flows available in the current workspace\n"
    "/reset — start a fresh conversation (keeps workspace + target)\n"
    "/status — show current binding\n"
    "/help — show this help\n"
    "\n"
    "Binding an agent and binding a flow are mutually exclusive — the latest "
    "one wins. After binding, send any text and the bound agent/flow will reply."
)

#: Prefixes a command may start with. Slack and Discord reserve "/" for their
#: own slash commands, so "!" works everywhere.
COMMAND_PREFIXES = ("/", "!")

_COMMANDS = {"start", "help", "workspaces", "workspace", "agents", "agent",
             "flows", "flow", "reset", "status"}


def workspace_names() -> list[str]:
    try:
        from workspace import list_workspace_folders
        return sorted(p.name for p in list_workspace_folders())
    except Exception:  # noqa: BLE001 - a listing failure reads as "none"
        return []


def allowed_agent_ids_for(workspace: str) -> Optional[list[str]]:
    """The workspace's allowed_agents list, or None if unrestricted."""
    try:
        from workspace import get_workspace_metadata
        meta = get_workspace_metadata(workspace) or {}
        allowed = meta.get("allowed_agents")
        if isinstance(allowed, list) and allowed:
            return [str(a) for a in allowed]
    except Exception:  # noqa: BLE001
        pass
    return None


def agents_visible_in(workspace: str) -> list[Any]:
    specs = registry.list_agents()
    allowed = allowed_agent_ids_for(workspace)
    if allowed is None:
        return specs
    allowed_set = set(allowed)
    return [s for s in specs if s.id in allowed_set]


def _load_flows() -> list[dict[str, Any]]:
    try:
        from flow import store as flow_store
        return flow_store.list_flows()
    except Exception:  # noqa: BLE001
        return []


def flows_visible_in(workspace: str) -> list[dict[str, Any]]:
    return [f for f in _load_flows() if not f.get("workspace") or f.get("workspace") == workspace]


def get_flow(flow_id: str) -> Optional[dict[str, Any]]:
    for f in _load_flows():
        if f.get("id") == flow_id:
            return f
    return None


def is_command(text: str) -> bool:
    t = (text or "").strip()
    if not t or t[0] not in COMMAND_PREFIXES:
        return False
    word = t[1:].split(None, 1)[0].split("@", 1)[0].lower() if len(t) > 1 else ""
    return word in _COMMANDS


def unbound_reply(binding: Optional[dict[str, Any]], chat_key: str) -> str:
    """What to say to a chat that has no complete binding yet."""
    if not binding or not binding.get("workspace"):
        return (
            f"This chat isn't bound to a workspace yet. Ask the operator to bind chat "
            f"`{chat_key}` to one from the dashboard, then pick an agent with "
            "/agent <id> or a flow with /flow <id>. /help for details."
        )
    return (
        f"Workspace `{binding['workspace']}` is set, but no agent or flow yet. "
        "Use /agent <id> (/agents to list) or /flow <id> (/flows to list)."
    )


def handle_command(store: ChannelStore, chat_key: str, text: str, *,
                   title: Optional[str] = None) -> Optional[str]:
    """Answer a command, or return None when ``text`` is not one."""
    if not is_command(text):
        return None
    chat_key = str(chat_key)
    cmd, _, args = text.strip().partition(" ")
    cmd = cmd[1:].split("@", 1)[0].lower()
    args = args.strip()
    binding = store.get_binding(chat_key) or {}
    workspace = binding.get("workspace")

    if cmd == "start":
        if workspace and (binding.get("agent_id") or binding.get("flow_id")):
            if binding.get("flow_id"):
                flow = get_flow(binding["flow_id"])
                target = f"flow: `{(flow or {}).get('name') or binding['flow_id']}`"
            else:
                target = f"agent: `{binding['agent_id']}`"
            return f"Ready. Workspace: `{workspace}`, {target}. Send a message, or /help for commands."
        return unbound_reply(binding, chat_key)

    if cmd == "help":
        return HELP_TEXT

    if cmd == "workspaces":
        names = workspace_names()
        if not names:
            return "No workspaces found."
        return "Workspaces:\n" + "\n".join(f"• {w}" for w in names)

    if cmd == "workspace":
        if not args:
            return f"Current workspace: {workspace or '(not set)'}. Ask the operator to change it."
        return (
            f"This chat's workspace is set by the operator, not by chat. Ask them to bind "
            f"chat `{chat_key}` to workspace `{args}` from the dashboard."
        )

    if cmd == "agents":
        if not workspace:
            return "This chat has no workspace yet. Ask the operator to bind it from the dashboard."
        visible = agents_visible_in(workspace)
        if not visible:
            return f"No agents authorised in workspace `{workspace}`."
        lines = "\n".join(f"• {a.id} — {a.name}" for a in visible)
        return f"Agents in `{workspace}`:\n{lines}"

    if cmd == "agent":
        if not workspace:
            return "This chat has no workspace yet. Ask the operator to bind it from the dashboard."
        if not args:
            return f"Current agent: {binding.get('agent_id') or '(not set)'}. Use /agent <id> to change it."
        spec = registry.get_agent(args)
        if not spec:
            return f"Unknown agent: {args}"
        allowed = allowed_agent_ids_for(workspace)
        if allowed is not None and args not in allowed:
            return (f"Agent `{args}` isn't authorised in workspace `{workspace}`.\n"
                    f"Allowed: {', '.join(allowed) or '(none)'}")
        store.upsert_binding(
            chat_key=chat_key, agent_id=args, flow_id=None, workspace=workspace,
            conversation_id=binding.get("conversation_id") or str(uuid.uuid4()),
            title=title or binding.get("title"),
        )
        notify_change(f"channel_{store.name}", chat_key=chat_key)
        return f"Bound to agent `{args}` in workspace `{workspace}`. Send a message to start."

    if cmd == "flows":
        if not workspace:
            return "This chat has no workspace yet. Ask the operator to bind it from the dashboard."
        visible = flows_visible_in(workspace)
        if not visible:
            return f"No flows available in workspace `{workspace}`."
        lines = "\n".join(f"• {f.get('id')} — {f.get('name') or ''}" for f in visible)
        return f"Flows in `{workspace}`:\n{lines}"

    if cmd == "flow":
        if not workspace:
            return "This chat has no workspace yet. Ask the operator to bind it from the dashboard."
        if not args:
            return f"Current flow: {binding.get('flow_id') or '(not set)'}. Use /flow <id> to change it."
        flow = get_flow(args)
        if not flow:
            return f"Unknown flow: {args}"
        if flow.get("workspace") and flow.get("workspace") != workspace:
            return f"Flow `{args}` belongs to another workspace."
        store.upsert_binding(
            chat_key=chat_key, agent_id="", flow_id=args, workspace=workspace,
            conversation_id=binding.get("conversation_id") or str(uuid.uuid4()),
            title=title or binding.get("title"),
        )
        notify_change(f"channel_{store.name}", chat_key=chat_key)
        return f"Bound to flow `{flow.get('name') or args}` in workspace `{workspace}`. Send a message to start."

    if cmd == "reset":
        if not binding:
            return "Nothing to reset: this chat has no binding yet."
        store.reset_conversation(chat_key, str(uuid.uuid4()))
        notify_change(f"channel_{store.name}", chat_key=chat_key)
        return "Conversation reset. The next message starts a fresh one."

    if cmd == "status":
        if not binding:
            return "No binding. Ask the operator to bind this chat to a workspace."
        target = (f"flow `{binding['flow_id']}`" if binding.get("flow_id")
                  else f"agent `{binding['agent_id']}`" if binding.get("agent_id") else "no agent or flow")
        return (f"Workspace: {workspace or '(not set)'}\nTarget: {target}\n"
                f"Conversation: {binding.get('conversation_id') or '(none yet)'}")

    return None


__all__ = ["HELP_TEXT", "COMMAND_PREFIXES", "handle_command", "is_command", "unbound_reply",
           "workspace_names", "agents_visible_in", "flows_visible_in", "allowed_agent_ids_for"]
