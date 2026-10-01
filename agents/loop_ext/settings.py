"""
Workspace settings of the agent loop's extensions, with environment defaults.

The loop's policies are switched and tuned in one place per workspace,
``get_workspace_metadata(ws)["settings"]["loop"]``:

- ``compaction`` (bool): fold and clear the context of a long run
  (agents/loop_ext/compaction.py). Env default ``AGENTS_HUB_LOOP_COMPACTION``, on.
- ``compaction_fraction`` (float): share of the model's context window a run
  may fill before it is compacted. Env ``AGENTS_HUB_LOOP_COMPACTION_FRACTION``, 0.7.
- ``compaction_keep`` (int): how many of the newest tool results always stay
  verbatim. Env ``AGENTS_HUB_LOOP_COMPACTION_KEEP``, 3.
- ``tool_search_threshold`` (int): above this many tools an agent sees a short
  list and ``search_tools`` (agents/loop_ext/tool_search.py). Env
  ``AGENTS_HUB_TOOL_SEARCH_THRESHOLD``, 30.
- ``native`` (bool): use Anthropic's own context editing and deferred tool
  loading where the model supports them. Env ``AGENTS_HUB_LOOP_NATIVE``, on.
- ``strict_tools`` (bool): read by the structured-output extension.
- ``view_focus`` (bool): a view agent sees the tools of the view kind it is on
  (agents/loop_ext/view_focus.py). Env ``AGENTS_HUB_LOOP_VIEW_FOCUS``, on.

Any other key resolves the same way, with ``AGENTS_HUB_LOOP_<KEY>`` as its
environment default. The workspace block is read once per agent build: a built
agent is cached and serves many runs, and its extensions are configured when it
is built, so a changed setting applies to the next build (the workspace
metadata is part of the build fingerprint in agents/agent_cache.py, so a saved
change rebuilds the agent on its next run).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: Environment variables whose names do not follow ``AGENTS_HUB_LOOP_<KEY>``.
ENV_NAMES: Dict[str, str] = {
    "tool_search_threshold": "AGENTS_HUB_TOOL_SEARCH_THRESHOLD",
}

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

# Attribute on the built agent that holds the block read for its current build.
_CACHE_ATTR = "_loop_settings_cache"


def env_name(key: str) -> str:
    """The environment variable that supplies the default for *key*."""
    return ENV_NAMES.get(key) or f"AGENTS_HUB_LOOP_{key.upper()}"


def _workspace_block(workspace: Optional[str]) -> Dict[str, Any]:
    """``settings.loop`` of the agent's workspace, or ``{}``.

    An agent is built with its operating path (``<workspace>/<project>`` for a
    task), so the name is recovered the way the tool policy recovers it.
    """
    try:
        from tools.approval import workspace_name
        ws = workspace_name(workspace)
        if not ws:
            return {}
        from workspace import get_workspace_metadata
        settings = get_workspace_metadata(ws).get("settings") or {}
        block = settings.get("loop") if isinstance(settings, dict) else None
        return dict(block) if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001 - unreadable settings mean the defaults, never a failed build
        log.debug("loop settings: workspace block unreadable for %r", workspace, exc_info=True)
        return {}


def loop_settings(agent: Any) -> Dict[str, Any]:
    """The workspace ``settings.loop`` block for *agent*, read once per build.

    Kept on the agent keyed by its chat model: the extensions of one build all
    read the same block (one workspace lookup instead of one per extension),
    and a rebuild, which makes a new model, reads it again.
    """
    llm_key = id(getattr(agent, "_llm", None))
    cached = getattr(agent, _CACHE_ATTR, None)
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == llm_key:
        return cached[1]
    block = _workspace_block(getattr(agent, "workspace", None))
    try:
        setattr(agent, _CACHE_ATTR, (llm_key, block))
    except (AttributeError, TypeError, ValueError):
        # An object that refuses attributes just reads the block again next time.
        log.debug("loop settings: cannot cache on %r", type(agent).__name__)
    return block


class _Unset(Exception):
    """A value that does not convert to the wanted type."""


def _convert(value: Any, default: Any) -> Any:
    """*value* in the type of *default*; raises :class:`_Unset` when it does
    not convert."""
    if default is None:
        return value
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise _Unset(value)
    if isinstance(default, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise _Unset(value) from exc
        if isinstance(value, bool) or number != number:  # a bool or NaN is no number here
            raise _Unset(value)
        return int(number) if isinstance(default, int) else number
    if isinstance(default, str):
        return str(value)
    return value


def env_setting(key: str, default: Any = None) -> Any:
    """The environment default for *key* (no workspace), typed like *default*."""
    raw = os.environ.get(env_name(key))
    if raw is None or not str(raw).strip():
        return default
    try:
        return _convert(raw, default)
    except _Unset:
        return default


def loop_setting(agent: Any, key: str, default: Any = None) -> Any:
    """One loop setting for *agent*: the workspace value, else the environment
    default, else *default*; converted to the type of *default* when one is
    given (a value that does not convert counts as unset)."""
    value = loop_settings(agent).get(key)
    if value is not None:
        try:
            return _convert(value, default)
        except _Unset:
            pass
    return env_setting(key, default)


# ── Workspace-level access, for callers that already have a workspace name ──
#
# ``_workspace_block``/``loop_setting`` above resolve an *agent*'s operating
# path to a workspace name first (``tools.approval.workspace_name``), because
# that is all an agent carries. The settings API route
# (dashboard/backend/routes/agent_loop_settings.py) already has the plain
# workspace name, so it reads the block directly and reuses the same
# conversion rules to report the effective value; neither function above is
# changed by this.

#: Every key the ``settings.loop`` workspace block may hold, with the default
#: ``loop_setting`` falls back to and, for a bounded numeric setting, its
#: allowed range. Kept next to ``ENV_NAMES`` as the one place that lists the
#: keys this module understands; the API route validates PUT payloads against
#: it and builds GET's ``effective``/``defaults`` from it.
LOOP_SETTINGS: Dict[str, Dict[str, Any]] = {
    "compaction": {"default": True},
    "compaction_fraction": {"default": 0.7, "min": 0.05, "max": 0.95},
    "compaction_keep": {"default": 3, "min": 1},
    "tool_search_threshold": {"default": 30, "min": 1},
    "native": {"default": True},
    "strict_tools": {"default": False},
    "view_focus": {"default": True},
}


def workspace_loop_block(name: Optional[str]) -> Dict[str, Any]:
    """``settings.loop`` of the workspace named *name*, or ``{}``.

    *name* is a plain workspace name, not an agent's operating path (see
    ``_workspace_block`` above for that case)."""
    if not name:
        return {}
    try:
        from workspace import get_workspace_metadata
        settings = get_workspace_metadata(name).get("settings") or {}
        block = settings.get("loop") if isinstance(settings, dict) else None
        return dict(block) if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001 - unreadable settings read back as empty, never a crash
        log.debug("loop settings: workspace block unreadable for %r", name, exc_info=True)
        return {}


def effective_loop_setting(name: Optional[str], key: str) -> Any:
    """The value ``loop_setting`` would give an agent of workspace *name* for
    *key*: the workspace value (converted to its default's type), else the
    environment default, else :data:`LOOP_SETTINGS`'s default for *key*."""
    default = LOOP_SETTINGS.get(key, {}).get("default")
    value = workspace_loop_block(name).get(key)
    if value is not None:
        try:
            return _convert(value, default)
        except _Unset:
            pass
    return env_setting(key, default)


__all__ = [
    "ENV_NAMES", "LOOP_SETTINGS", "effective_loop_setting", "env_name", "env_setting",
    "loop_setting", "loop_settings", "workspace_loop_block",
]
