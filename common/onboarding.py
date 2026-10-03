"""
What is set up and what is still missing: the hub's state as a newcomer sees it.

The Help panel's agent (``support``, see ``routes/help_chat.py``) answers "what
should I do next?", and that question has an answer only against the install in
front of the user: no model provider means nothing can run, an install with
agents but no chat yet wants a first conversation, and so on. This module reads
that state once per turn, server side, from the same stores the pages read, and
renders it as a compact block for the prompt.

It mirrors what the first-run checklist in the dashboard ticks
(``components/docs/OnboardingChecklist.jsx``: providers, workspaces, agents, a
first chat) and adds what a newcomer reaches next: models enabled on the Models
page, custom agents, tasks, channels, connections and watchers.

Two properties:

* **Never raises.** Every item is read on its own; one that fails reads as
  ``None`` ("could not tell") rather than taking the turn down. A help panel
  that breaks on a half-configured install fails the one person it is for.
* **Counts and names, never values.** A provider is "key set", never the key; a
  channel is "configured", never its token. The block lands in a prompt.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: Custom agent names listed by name; past this the block says how many more.
MAX_NAMED_AGENTS = 6


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - an item that cannot be read is "unknown", not a failed turn
        log.debug("onboarding snapshot item failed", exc_info=True)
        return None


def _scope(workspace: Optional[str]) -> Optional[str]:
    """The workspace filter the stores take: ``default`` means every workspace."""
    ws = (workspace or "").strip()
    return None if not ws or ws == "default" else ws


def _providers() -> Dict[str, Any]:
    from common.config import settings

    keys = [name for name, value in (
        ("openai", settings.openai_api_key),
        ("anthropic", settings.anthropic_api_key),
        ("google", settings.google_api_key),
    ) if value]
    local = [name for name, value in (
        ("ollama", getattr(settings, "ollama_model", "")),
        ("lmstudio", getattr(settings, "lmstudio_model", "")),
    ) if value]
    return {"default_provider": settings.default_provider or "",
            "keys_set": keys, "local_models": local}


def _enabled_models() -> List[str]:
    from tools.delegation import enabled_models

    return [m["id"] for m in enabled_models()]


def _workspace_default_model(workspace: Optional[str]) -> Optional[str]:
    from workspace.storage import get_workspace_default_model_config, get_workspace_metadata

    if not workspace:
        return None
    cfg = get_workspace_default_model_config(get_workspace_metadata(workspace))
    if not (cfg.get("provider") or cfg.get("model")):
        return None
    return "/".join(x for x in (cfg.get("provider"), cfg.get("model")) if x)


def _agents() -> Dict[str, Any]:
    from agents.registry import load_all_raw

    records = [a for a in load_all_raw() if isinstance(a, dict)]
    custom = [str(a.get("name") or a.get("id")) for a in records if not a.get("system")]
    return {"total": len(records), "custom": len(custom), "custom_names": custom[:MAX_NAMED_AGENTS]}


def _workspaces() -> int:
    from workspace.storage import list_workspace_folders

    return len(list_workspace_folders())


def _chats(workspace: Optional[str]) -> int:
    from common import chat_store

    return int(chat_store.list_chats(workspace=_scope(workspace), limit=1)["total"])


def _tasks(workspace: Optional[str]) -> int:
    from tasks.service import list_tasks_page

    _, total = list_tasks_page(workspace=_scope(workspace), limit=1)
    return int(total)


def _channels() -> Dict[str, List[str]]:
    from connectors.channels import registry as channels

    configured, enabled = [], []
    for spec in channels.all_channels():
        try:
            if spec.store.is_configured(*spec.service.required_fields):
                configured.append(spec.name)
            if spec.store.is_enabled():
                enabled.append(spec.name)
        except Exception:  # noqa: BLE001 - one broken channel store must not hide the rest
            log.debug("channel %s unreadable", spec.name, exc_info=True)
    return {"configured": configured, "enabled": enabled}


def _connections(workspace: Optional[str]) -> int:
    from connections import store

    return len(store.list_connections(_scope(workspace)))


def _databases(workspace: Optional[str]) -> int:
    from connectors.databases.store import list_connections

    return len(list_connections(_scope(workspace)))


def _watchers(workspace: Optional[str]) -> int:
    from watchers.service import list_watchers

    return len(list_watchers(_scope(workspace)))


def _mcp_servers(workspace: Optional[str]) -> int:
    from mcp_client.store import list_servers

    return len(list_servers(workspace or "default"))


def _accounts() -> List[str]:
    """Credential connectors with saved credentials (Google, Microsoft, Jira,
    Notion...). Databases are counted separately and always read configured
    on their own, so they are left out here."""
    from connectors import credentials

    return [spec.name for spec in credentials.all_specs()
            if spec.name != "databases" and spec.is_configured()]


def _skills(workspace: Optional[str]) -> int:
    from memory.procedural import ProcedureStore

    return len(ProcedureStore(workspace or "default").load())


def hub_snapshot(workspace: Optional[str] = None, *, tour_done: Optional[bool] = None) -> Dict[str, Any]:
    """Everything the Help agent is told about the install. Never raises.

    ``tour_done`` comes from the browser (the tour marks itself done in local
    storage, which the server cannot see); ``None`` means it was not said.
    """
    return {
        "workspace": workspace or None,
        "providers": _safe(_providers),
        "enabled_models": _safe(_enabled_models),
        "workspace_default_model": _safe(lambda: _workspace_default_model(workspace)),
        "agents": _safe(_agents),
        "workspaces": _safe(_workspaces),
        "chats": _safe(lambda: _chats(workspace)),
        "tasks": _safe(lambda: _tasks(workspace)),
        "channels": _safe(_channels),
        "connections": _safe(lambda: _connections(workspace)),
        "databases": _safe(lambda: _databases(workspace)),
        "watchers": _safe(lambda: _watchers(workspace)),
        "mcp_servers": _safe(lambda: _mcp_servers(workspace)),
        "accounts": _safe(_accounts),
        "skills": _safe(lambda: _skills(workspace)),
        "tour_done": tour_done,
    }


def missing_steps(snap: Dict[str, Any]) -> List[str]:
    """The first-run steps still open, most blocking first, as short phrases."""
    out: List[str] = []
    providers = snap.get("providers")
    if providers is not None and not (providers.get("keys_set") or providers.get("local_models")):
        out.append("no model provider is configured (no API key, no local model): nothing can run yet")
    if snap.get("enabled_models") == []:
        out.append("no model is enabled on the Models page")
    if snap.get("chats") == 0:
        out.append("no chat yet: the user has not talked to an agent")
    agents = snap.get("agents")
    if agents is not None and agents.get("custom") == 0:
        out.append("no custom agent yet: only the shipped system agents exist")
    if snap.get("tasks") == 0:
        out.append("no task yet in this workspace")
    channels = snap.get("channels")
    if channels is not None and not channels.get("configured"):
        out.append("no channel connected (Slack, Telegram, Discord, Teams, mail)")
    if snap.get("tour_done") is False:
        out.append("the welcome tour has not been taken")
    return out


def _fmt(value: Any, unknown: str = "unknown") -> str:
    return unknown if value is None else str(value)


def render_snapshot(snap: Dict[str, Any]) -> List[str]:
    """The snapshot as prompt lines: what is configured, then what is missing."""
    providers = snap.get("providers")
    agents = snap.get("agents")
    channels = snap.get("channels")
    models = snap.get("enabled_models")

    lines: List[str] = []
    if providers is None:
        lines.append("- Providers: unknown")
    else:
        lines.append(
            f"- Providers: default {providers.get('default_provider') or 'not set'}; "
            f"API keys set for {', '.join(providers.get('keys_set') or []) or 'none'}; "
            f"local models set for {', '.join(providers.get('local_models') or []) or 'none'}")
    if models is None:
        lines.append("- Enabled models: unknown")
    else:
        shown = ", ".join(models[:8]) + (f" and {len(models) - 8} more" if len(models) > 8 else "")
        lines.append(f"- Enabled models ({len(models)}): {shown or 'none'}")
    if snap.get("workspace_default_model"):
        lines.append(f"- This workspace's default model: {snap['workspace_default_model']}")
    if agents is None:
        lines.append("- Agents: unknown")
    else:
        names = ", ".join(agents.get("custom_names") or [])
        more = agents["custom"] - len(agents.get("custom_names") or [])
        lines.append(
            f"- Agents: {agents['total']} in total, {agents['custom']} custom"
            + (f" ({names}{f' and {more} more' if more > 0 else ''})" if names else ""))
    lines.append(f"- Workspaces: {_fmt(snap.get('workspaces'))}")
    lines.append(f"- Chats in this workspace: {_fmt(snap.get('chats'))}")
    lines.append(f"- Tasks in this workspace: {_fmt(snap.get('tasks'))}")
    if channels is None:
        lines.append("- Channels: unknown")
    else:
        lines.append(
            f"- Channels configured: {', '.join(channels.get('configured') or []) or 'none'}; "
            f"enabled: {', '.join(channels.get('enabled') or []) or 'none'}")
    lines.append(f"- Connections (inbound data feeds): {_fmt(snap.get('connections'))}")
    lines.append(f"- Database connections: {_fmt(snap.get('databases'))}")
    lines.append(f"- Watchers: {_fmt(snap.get('watchers'))}")
    lines.append(f"- MCP servers in this workspace: {_fmt(snap.get('mcp_servers'))}")
    accounts = snap.get("accounts")
    lines.append("- Connected accounts and services: "
                 + ("unknown" if accounts is None else (", ".join(accounts) or "none")))
    lines.append(f"- Skills in this workspace: {_fmt(snap.get('skills'))}")
    if snap.get("tour_done") is not None:
        lines.append(f"- Welcome tour taken: {'yes' if snap['tour_done'] else 'no'}")

    missing = missing_steps(snap)
    lines += ["", "Still missing:" if missing else "Still missing: nothing from the first-run list."]
    lines += [f"- {m}" for m in missing]
    return lines


__all__ = ["hub_snapshot", "render_snapshot", "missing_steps"]
