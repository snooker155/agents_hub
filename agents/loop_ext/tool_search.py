"""
Loop extension: tool search (see agents/loop_ext/__init__.py).

A model offered a hundred tools reads a hundred schemas on every step and
picks worse among them than among ten. Above a threshold (``tool_search`` on
the agent, or the workspace's ``loop.tool_search_threshold``, 30 by default)
the model is shown a short list instead: the core tools the agent's prompt
relies on, plus ``search_tools``, whose description says how many more tools
exist and in which categories. A search ranks the hidden ("deferred") tools
by name, description, category and argument names, answers with the best
matches (name, description, arguments) and loads them: from the next model
call on they are bound like the core tools. Every tool stays in the executor,
so a deferred tool the model calls by name without loading it still runs.

Core tools, always visible:

- ``ask_user``: the only way to reach the person; a model that has to search
  for it first would guess instead of asking;
- the reasoning tools (``think``, ``plan`` and the plan store,
  tools.approval.REASONING_TOOL_NAMES): the reasoning guidance in the prompt
  tells the model to use them unprompted;
- the delegation tools the prompt's delegation sections name
  (``delegate_task_tool``, ``run_agent_tool``, ``list_agents_tool``,
  ``list_models_tool``) and the two task tools a delegating agent needs
  without thinking about it (``create_task``, ``get_task_result``);
- ``handoff_to_agent`` (tools/handoff.py), for the same reason as the
  delegation tools: hidden behind a search while ``run_agent_tool`` is in
  view, the model delegates when the user asked to be handed over;
- any tool the agent's own system prompt names (up to
  ``PROMPT_CORE_LIMIT``): instructions that say "use read_file" should not
  send the model searching for read_file;
- ``search_tools`` itself.

On Anthropic models that support it, the tools array of every request holds
every tool, the non-core ones marked ``defer_loading: true`` (see
agents/loop_ext/anthropic_native.py), and the result of ``search_tools`` is
sent as ``tool_reference`` blocks that the API expands into the loaded tools'
definitions. The array is then the same on every request of the run, which
keeps the provider's prompt cache and does not edit the history of a model
that binds its thinking to the conversation. When a fold of the run's context
(agents/loop_ext/compaction.py) takes an old search result away, the tools it
had loaded are declared without ``defer_loading`` from then on.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from agents.agent_loop import LoopExtension, LoopState, current_state

log = logging.getLogger(__name__)

SEARCH_TOOL_NAME = "search_tools"

#: Tools that stay visible whatever the count (module docstring).
CORE_TOOLS = frozenset({
    "ask_user",
    SEARCH_TOOL_NAME,
    "delegate_task_tool",
    "run_agent_tool",
    "list_agents_tool",
    "list_models_tool",
    "create_task",
    "get_task_result",
    "handoff_to_agent",
})

DEFAULT_THRESHOLD = 30
#: How many tools named in the system prompt are kept visible at most.
PROMPT_CORE_LIMIT = 12
#: Results of one search, default and ceiling.
DEFAULT_LIMIT = 5
MAX_LIMIT = 10
#: Characters of a description and of an argument listing in a search answer.
DESCRIPTION_CHARS = 600
ARGUMENTS_CHARS = 1500

#: First line of a search answer that loaded tools; the names after it are
#: what the Anthropic path turns into ``tool_reference`` blocks.
LOADED_PREFIX = "Loaded tools: "

_STOP = frozenset({
    "a", "an", "and", "any", "are", "by", "can", "do", "for", "from", "i", "in",
    "is", "it", "me", "my", "of", "on", "or", "some", "that", "the", "this", "to",
    "tool", "tools", "use", "using", "want", "with", "need",
})


def _tokens(text: str) -> Set[str]:
    out: Set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if raw in _STOP:
            continue
        if len(raw) > 3 and raw.endswith("s") and not raw.endswith("ss"):
            raw = raw[:-1]
        out.add(raw)
    return out


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


@dataclass
class _Entry:
    """One deferred tool as the search sees it."""

    name: str
    category: str
    description: str
    parameters: Dict[str, Any]
    name_tokens: Set[str] = field(default_factory=set)
    category_tokens: Set[str] = field(default_factory=set)
    description_tokens: Set[str] = field(default_factory=set)
    argument_tokens: Set[str] = field(default_factory=set)

    def score(self, query: str, tokens: Set[str]) -> float:
        if not tokens and not query:
            return 0.0
        total = 0.0
        q = query.strip().lower()
        if q and q == self.name.lower():
            total += 10
        for t in tokens:
            if t in self.name_tokens:
                total += 3
            elif len(t) >= 4 and t in self.name.lower():
                total += 1.5
            if t in self.category_tokens:
                total += 2
            if t in self.description_tokens:
                total += 1
            if t in self.argument_tokens:
                total += 0.5
        if len(q) >= 6 and q in self.description.lower():
            total += 2
        return total


def _category_of(name: str) -> str:
    try:
        from tools.registry import get_tool_by_id
        spec = get_tool_by_id(name)
    except Exception:  # noqa: BLE001 - a catalog that cannot load leaves tools uncategorised
        spec = None
    return str(getattr(spec, "category", "") or "other")


def _openai_schema(tool: Any) -> Dict[str, Any]:
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool
        return convert_to_openai_tool(tool).get("function") or {}
    except Exception:  # noqa: BLE001 - a tool without a readable schema is described by name only
        return {"name": _tool_name(tool), "description": str(getattr(tool, "description", "") or "")}


def _first_paragraph(text: str) -> str:
    text = (text or "").strip()
    para = re.split(r"\n\s*\n", text, maxsplit=1)[0].strip()
    return para[:DESCRIPTION_CHARS]


def _entry(tool: Any) -> _Entry:
    schema = _openai_schema(tool)
    name = _tool_name(tool)
    parameters = schema.get("parameters") or {}
    description = _first_paragraph(str(schema.get("description") or getattr(tool, "description", "") or ""))
    category = _category_of(name)
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    arg_text = " ".join(
        f"{k} {(v or {}).get('description') or ''}" for k, v in (props or {}).items()
        if isinstance(v, dict) or v is None)
    return _Entry(
        name=name, category=category, description=description, parameters=parameters,
        name_tokens=_tokens(name.replace("_", " ")),
        category_tokens=_tokens(category.replace("_", " ")),
        description_tokens=_tokens(description),
        argument_tokens=_tokens(arg_text),
    )


def _arguments_text(parameters: Dict[str, Any]) -> str:
    props = parameters.get("properties") if isinstance(parameters, dict) else None
    if not props:
        return "none"
    compact = {}
    for key, value in props.items():
        if not isinstance(value, dict):
            continue
        item = {k: value[k] for k in ("type", "description", "enum", "items", "default") if k in value}
        compact[key] = item
    text = json.dumps(compact, ensure_ascii=False, default=str)
    if len(text) > ARGUMENTS_CHARS:
        text = text[:ARGUMENTS_CHARS] + " ...}"
    required = parameters.get("required") or []
    return text + (f"; required: {', '.join(map(str, required))}" if required else "")


def loaded_names(text: Any) -> List[str]:
    """The tool names a ``search_tools`` answer loaded, from its first line."""
    if not isinstance(text, str) or not text.startswith(LOADED_PREFIX):
        return []
    first = text.split("\n", 1)[0][len(LOADED_PREFIX):]
    return [n.strip() for n in first.split(",") if n.strip()]


class ToolSearchExtension(LoopExtension):
    """Short tool list plus ``search_tools`` (module docstring)."""

    name = "tool_search"

    def __init__(self, *, core: Set[str], entries: Sequence[_Entry],
                 tool_chars: Dict[str, int], native_llm: Any = None,
                 anthropic_tools: Optional[List[Dict[str, Any]]] = None) -> None:
        self.core = set(core) | {SEARCH_TOOL_NAME}
        self.entries = list(entries)
        self.deferred = {e.name for e in self.entries}
        self.tool_chars = dict(tool_chars)
        #: The Anthropic model the deferred-loading request goes to, when it applies.
        self.native_llm = native_llm
        self.anthropic_tools = list(anthropic_tools or [])

    # -- the search itself --

    def categories(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for e in self.entries:
            counts[e.category] = counts.get(e.category, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> List[_Entry]:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))
        tokens = _tokens(query)
        scored = [(e.score(query or "", tokens), e) for e in self.entries]
        ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda p: (-p[0], p[1].name))
        return [e for _s, e in ranked[:limit]]

    def render(self, query: str, found: List[_Entry]) -> str:
        if not found:
            cats = ", ".join(f"{c} ({n})" for c, n in self.categories().items())
            return (f"No tool matched \"{query}\". Categories you can search: {cats}. "
                    "Try other words, a tool name or a category name.")
        parts = [LOADED_PREFIX + ", ".join(e.name for e in found),
                 "These tools can be called from your next step on."]
        for e in found:
            parts.append(f"\n## {e.name} ({e.category})\n{e.description or '(no description)'}\n"
                         f"Arguments: {_arguments_text(e.parameters)}")
        return "\n".join(parts)

    def run_search(self, query: str, limit: int = DEFAULT_LIMIT) -> str:
        """What ``search_tools`` does: rank, load, answer."""
        found = self.search(query, limit)
        state = current_state()
        if state is not None:
            for e in found:
                if e.name not in state.loaded_tools:
                    state.loaded_tools.append(e.name)
        return self.render(query, found)

    def description(self) -> str:
        cats = ", ".join(f"{c} ({n})" for c, n in self.categories().items())
        return (
            "Find and load tools that are not shown to you yet. Besides the tools you "
            f"can see, this agent has {len(self.entries)} more, in these categories: "
            f"{cats}. Call search_tools with a few words describing what you need (a "
            "tool name or a category name works too); the tools it finds can be called "
            "from your next step on. Search again whenever you need a capability you "
            "do not see."
        )

    # -- the hooks --

    def _state(self, state: LoopState) -> Dict[str, Any]:
        ts = state.scratch.get("tool_search")
        if not isinstance(ts, dict):
            ts = {"undeferred": []}
            state.scratch["tool_search"] = ts
        return ts

    def select_tools(self, state: LoopState, tools: List[Any]) -> List[Any]:
        loaded = set(state.loaded_tools)
        selected = [t for t in tools if _tool_name(t) in self.core or _tool_name(t) in loaded]
        # For the compaction estimate: the schemas this call actually carries.
        self._state(state)["bound_chars"] = sum(self.tool_chars.get(_tool_name(t), 0) for t in selected)
        return selected

    def shape_messages(self, state: LoopState, inputs: Dict[str, Any],
                       scratchpad: List[Any]) -> List[Any]:
        """On the Anthropic path, note loaded tools whose search result is no
        longer in the trail (a fold took it): they are declared undeferred."""
        if self.native_llm is None or not state.loaded_tools:
            return scratchpad
        referenced: Set[str] = set()
        for m in scratchpad:
            if getattr(m, "type", "") == "tool" and self._is_search_result(m):
                referenced.update(loaded_names(m.content))
        ts = self._state(state)
        undeferred = ts.setdefault("undeferred", [])
        for name in state.loaded_tools:
            if name in self.deferred and name not in referenced and name not in undeferred:
                undeferred.append(name)
        return scratchpad

    def model_kwargs(self, state: LoopState, llm: Any) -> Dict[str, Any]:
        """The whole tool set with deferred loading, on the agent's own
        Anthropic model only (a fallback model gets the short list)."""
        if self.native_llm is None or llm is not self.native_llm or not self.anthropic_tools:
            return {}
        undeferred = set(self._state(state).get("undeferred") or [])
        tools = []
        for spec in self.anthropic_tools:
            if spec.get("name") in undeferred:
                spec = {k: v for k, v in spec.items() if k != "defer_loading"}
            tools.append(spec)
        return {"tools": tools}

    def wrap_model(self, state: LoopState, bound: Any, rebind: Any) -> Any:
        """On the Anthropic path, send search answers as ``tool_reference``
        blocks. Wraps the agent's own model only: a fallback bound later by
        ``rebind`` reads the plain text answer."""
        if self.native_llm is None:
            return bound
        from langchain_core.runnables import RunnableLambda
        return RunnableLambda(self.to_references, name="tool_references") | bound

    @staticmethod
    def _is_search_result(message: Any) -> bool:
        extra = getattr(message, "additional_kwargs", None) or {}
        return str(extra.get("name") or getattr(message, "name", None) or "") == SEARCH_TOOL_NAME

    def to_references(self, prompt: Any) -> List[Any]:
        messages = prompt.to_messages() if hasattr(prompt, "to_messages") else list(prompt)
        out = []
        for m in messages:
            if getattr(m, "type", "") == "tool" and self._is_search_result(m):
                names = [n for n in loaded_names(m.content) if n in self.deferred]
                if names:
                    m = m.model_copy(update={"content": [
                        {"type": "tool_reference", "tool_name": n} for n in names]})
            out.append(m)
        return out


# ── building ──────────────────────────────────────────────────────────────────

def _threshold(agent: Any) -> int:
    from agents.loop_ext.settings import loop_setting
    value = int(loop_setting(agent, "tool_search_threshold", DEFAULT_THRESHOLD))
    return value if value > 0 else DEFAULT_THRESHOLD


def core_names(agent: Any, names: Sequence[str]) -> Set[str]:
    """The core tools of *agent* among *names* (module docstring)."""
    try:
        from tools.approval import REASONING_TOOL_NAMES as reasoning
    except ImportError:
        reasoning = frozenset()
    core = {n for n in names if n in CORE_TOOLS or n in reasoning}
    prompt = str(getattr(agent, "system_prompt", "") or "")
    if prompt:
        mentioned = []
        for n in names:
            if n in core:
                continue
            match = re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", prompt)
            if match:
                mentioned.append((match.start(), n))
        core.update(n for _pos, n in sorted(mentioned)[:PROMPT_CORE_LIMIT])
    return core


def _native_llm(agent: Any) -> Any:
    from agents.loop_ext import anthropic_native as an
    from agents.loop_ext.settings import loop_setting
    llm = getattr(agent, "_llm", None)
    if llm is None or not an.is_anthropic_client(llm):
        return None
    model = an.model_id(llm) or str(getattr(agent, "model", "") or "")
    if not an.supports_tool_reference(model) or not loop_setting(agent, "native", True):
        return None
    return llm


def _anthropic_tools(tools: Sequence[Any], core: Set[str]) -> List[Dict[str, Any]]:
    """Every tool in Anthropic's own shape, the non-core ones deferred.

    A dict already in that shape passes through ``bind_tools`` of the
    installed client with its extra keys, which is how ``defer_loading``
    reaches the request.
    """
    from langchain_anthropic.chat_models import convert_to_anthropic_tool
    out = []
    for t in tools:
        spec = dict(convert_to_anthropic_tool(t))
        if _tool_name(t) not in core:
            spec["defer_loading"] = True
        out.append(spec)
    return out


def _search_tool(ext: ToolSearchExtension) -> Any:
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class SearchToolsInput(BaseModel):
        query: str = Field(description="A few words describing the capability you need, "
                                       "a tool name or a category name.")
        limit: int = Field(default=DEFAULT_LIMIT,
                           description=f"How many tools to load, 1 to {MAX_LIMIT}.")

    def search_tools(query: str, limit: int = DEFAULT_LIMIT) -> str:
        return ext.run_search(query, limit)

    return StructuredTool.from_function(
        func=search_tools,
        name=SEARCH_TOOL_NAME,
        description=ext.description(),
        args_schema=SearchToolsInput,
        metadata={"hub_tool_search": True},
    )


def extension_for(agent: Any) -> Optional[ToolSearchExtension]:
    """Tool search for *agent*, or None when it has few enough tools (or the
    agent switched it off). Appends ``search_tools`` to the agent's tools."""
    tools = list(getattr(agent, "_tools", None) or [])
    # A rebuild of the same agent finds its own search tool from last time.
    own = [t for t in tools if _tool_name(t) == SEARCH_TOOL_NAME
           and (getattr(t, "metadata", None) or {}).get("hub_tool_search")]
    if any(_tool_name(t) == SEARCH_TOOL_NAME for t in tools if t not in own):
        log.info("tool_search: %s already has a tool named %s; tool search is off",
                 getattr(agent, "agent_id", "?"), SEARCH_TOOL_NAME)
        return None
    tools = [t for t in tools if t not in own]

    flag = getattr(getattr(agent, "spec", None), "tool_search", None)
    if flag is False:
        return None
    if flag is None and len(tools) <= _threshold(agent):
        return None

    names = [_tool_name(t) for t in tools]
    core = core_names(agent, names)
    deferred = [t for t in tools if _tool_name(t) not in core]
    if not deferred:
        return None

    entries = [_entry(t) for t in deferred]
    tool_chars = {}
    for t in tools:
        tool_chars[_tool_name(t)] = len(json.dumps(_openai_schema(t), default=str))
    native = _native_llm(agent)
    ext = ToolSearchExtension(core=core, entries=entries, tool_chars=tool_chars, native_llm=native)
    search = _search_tool(ext)
    tool_chars[SEARCH_TOOL_NAME] = len(json.dumps(_openai_schema(search), default=str))
    ext.tool_chars = tool_chars

    # A new list rather than an append: the list the agent was built with
    # may be shared, and the executor is built from ``agent._tools`` after
    # the extensions load, so it picks this one up.
    agent._tools = [*tools, search]
    if native is not None:
        try:
            ext.anthropic_tools = _anthropic_tools([*tools, search], ext.core)
        except Exception:  # noqa: BLE001 - no deferred declaration means the client-side list
            log.warning("tool_search: Anthropic tool declarations failed", exc_info=True)
            ext.native_llm = None
    return ext


__all__ = [
    "CORE_TOOLS",
    "LOADED_PREFIX",
    "SEARCH_TOOL_NAME",
    "ToolSearchExtension",
    "core_names",
    "extension_for",
    "loaded_names",
]
