"""
Per-run overrides: one object that changes how a single run's agent is built.

A run may differ from its agent's record without the record changing: a
script asks for a cheaper model, an eval tries a narrower tool set, a
proactive tick puts its outbound tools on "ask" and wants a schema for its
answer. Before this module each of those was a flag of its own
(``--tool-policy``, ``--output-schema``, ``--provider``/``--model``). They are
now keys of one ``overrides`` object, accepted the same way by a task launch
(``params.overrides``, agents/agent_launcher.py), a chat turn and ``/v1``
(``ChatRequest.overrides``, chat/models.py), and the command line
(``ah agent run --overrides``, ``runtime.agent_run --overrides``). The old
flags still work: :func:`fold_legacy` turns them into keys of the object.

Keys (anything else is refused, ``OverrideError``):

``model``, ``provider``
    The model of this run, over the agent's own and the workspace cascade.
``system``
    Replaces the agent's own instructions (the assembled ``instructions.md``,
    ``capabilities.md`` and ``usage.md``). What the hub adds around them
    (workspace instructions, memory, tool guidance) is still added.
``system_append``
    Appended to the agent's instructions (or to ``system`` when both are given).
``tools``
    A list that replaces the record's tool list, or ``{"add": [...],
    "remove": [...]}`` that edits it. Ids are checked against the catalog.
``skills``
    ``true`` or ``false`` turns the agent's skills on or off for this run; a
    list of skill names turns them on and lists only those in the catalog.
``mcp``
    MCP server ids: the run gets ``mcp:<id>`` for each, instead of whatever
    MCP servers the record names.
``tool_policy``
    Tool policy entries merged over the agent's own (tools/permission_policy.py).
``output_schema``
    A JSON Schema the final answer of this run must match
    (agents/loop_ext/structured.py).
``max_concurrent_delegates``
    How many of this run's delegated subtasks (``delegate_task_tool``,
    tools/delegation.py) may be running at once, 1..32, over the agent's own
    default (6). Reaches a container run as an environment variable the same
    way the delegation depth does.

The object is normalised (:func:`normalize`) into one canonical shape, which
is what travels to the build as ``run_overrides`` and what the agent build
cache keys on (agents/agent_cache.py), so an overridden build never shares a
cache entry with the plain one. The capability guard sees the run's tool set:
:func:`guard_tools` refuses a combination the record does not already form,
before the run starts and again at build time (agents/agent_factory.py).
The run record keeps what was asked for under ``overrides`` (:func:`record_view`).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

#: Every key the object accepts.
KEYS = ("model", "provider", "system", "system_append", "tools", "skills", "mcp",
        "tool_policy", "output_schema", "max_concurrent_delegates")

#: Range accepted for ``max_concurrent_delegates`` (agent field and override alike).
MAX_CONCURRENT_DELEGATES_MIN = 1
MAX_CONCURRENT_DELEGATES_MAX = 32

#: Keys of a ``tools`` edit given as an object.
TOOL_EDIT_KEYS = ("add", "remove")

#: Group aliases a tool list may name (agents/agent_factory.py
#: ``_create_tools``), accepted beside the catalog's ids.
TOOL_GROUP_ALIASES = (
    "filesystem", "task_management", "agent_coordination", "agent_management",
    "agent_flows", "flow_management", "scenario_management", "world_management",
    "team_management", "loop_management", "project_management", "entity_runs",
    "schedule_management", "service_ops", "docs", "geometry", "evals",
)

#: A system text longer than this is refused; the run record keeps at most
#: ``RECORD_TEXT_CHARS`` of it.
MAX_TEXT_CHARS = 200_000
RECORD_TEXT_CHARS = 4_000

_SERVER_ID = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")


class OverrideError(ValueError):
    """An overrides object that cannot be used: unknown key, wrong type,
    unknown tool, invalid schema. Routes answer it with 400."""


# ── parsing and validation ───────────────────────────────────────────────────

def _strings(value: Any, key: str) -> List[str]:
    if not isinstance(value, (list, tuple)):
        raise OverrideError(f"overrides.{key} must be a list of strings")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise OverrideError(f"overrides.{key} must be a list of non-empty strings")
        if item.strip() not in out:
            out.append(item.strip())
    return out


def _text(value: Any, key: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise OverrideError(f"overrides.{key} must be a string")
    if not allow_empty and not value.strip():
        raise OverrideError(f"overrides.{key} must not be empty")
    if len(value) > MAX_TEXT_CHARS:
        raise OverrideError(f"overrides.{key} is longer than {MAX_TEXT_CHARS} characters")
    return value


def known_tool_ids() -> set:
    """The tool ids an override may name: the catalog's and the group aliases."""
    from tools.registry import get_all_tools
    return {t.id for t in get_all_tools()} | set(TOOL_GROUP_ALIASES)


def _is_mcp(tool_id: str) -> bool:
    return tool_id.startswith("mcp:") or tool_id.startswith("mcp__")


def _check_tools(ids: Iterable[str], key: str) -> None:
    known = known_tool_ids()
    unknown = sorted(t for t in ids if t not in known and not _is_mcp(t))
    if unknown:
        raise OverrideError(f"overrides.{key}: unknown tool id(s): {', '.join(unknown)}")


def _tool_policy(value: Any) -> Dict[str, str]:
    from agents.registry import TOOL_POLICY_MODES
    if not isinstance(value, dict):
        raise OverrideError("overrides.tool_policy must be an object of tool id to mode")
    out: Dict[str, str] = {}
    for key, mode in value.items():
        k, m = str(key or "").strip(), str(mode or "").strip().lower()
        if not k:
            raise OverrideError("overrides.tool_policy has an empty tool id")
        if m not in TOOL_POLICY_MODES:
            raise OverrideError(f"overrides.tool_policy['{k}']: mode must be one of "
                                f"{', '.join(TOOL_POLICY_MODES)}")
        out[k] = m
    return dict(sorted(out.items()))


def _max_concurrent_delegates(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OverrideError("overrides.max_concurrent_delegates must be a whole number")
    number = int(value)
    if number != value or not (MAX_CONCURRENT_DELEGATES_MIN <= number <= MAX_CONCURRENT_DELEGATES_MAX):
        raise OverrideError(
            f"overrides.max_concurrent_delegates must be a whole number between "
            f"{MAX_CONCURRENT_DELEGATES_MIN} and {MAX_CONCURRENT_DELEGATES_MAX}")
    return number


def _output_schema(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise OverrideError("overrides.output_schema must be a non-empty JSON object")
    import jsonschema
    try:
        validator_cls = jsonschema.validators.validator_for(
            value, default=jsonschema.Draft202012Validator)
        validator_cls.check_schema(value)
    except jsonschema.exceptions.SchemaError as exc:
        raise OverrideError(f"overrides.output_schema is not a valid JSON Schema: {exc.message}") from exc
    return value


def parse(raw: Any) -> Dict[str, Any]:
    """``raw`` as a dict: a dict as is, a JSON string decoded, None as ``{}``."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise OverrideError(f"overrides is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise OverrideError("overrides must be a JSON object")
    return raw


def normalize(raw: Any, *, check_tool_ids: bool = True) -> Dict[str, Any]:
    """The canonical form of an overrides object. Raises :class:`OverrideError`.

    Keys whose value is None are dropped (an absent override). The result has
    sorted keys and de-duplicated lists, so two requests that mean the same
    override produce the same object (and the same build cache key).
    """
    data = parse(raw)
    unknown = sorted(str(k) for k in data if k not in KEYS)
    if unknown:
        raise OverrideError(f"unknown override key(s): {', '.join(unknown)}; "
                            f"allowed: {', '.join(KEYS)}")
    out: Dict[str, Any] = {}
    for key in ("model", "provider"):
        value = data.get(key)
        if value is not None:
            out[key] = _text(value, key, allow_empty=False).strip()
    if data.get("system") is not None:
        out["system"] = _text(data["system"], "system", allow_empty=False)
    if data.get("system_append") is not None:
        text = _text(data["system_append"], "system_append", allow_empty=True)
        if text.strip():
            out["system_append"] = text
    tools = data.get("tools")
    if tools is not None:
        if isinstance(tools, dict):
            bad = sorted(str(k) for k in tools if k not in TOOL_EDIT_KEYS)
            if bad:
                raise OverrideError(f"overrides.tools: unknown key(s) {', '.join(bad)}; "
                                    "use a list, or an object with add and remove")
            edit = {k: sorted(_strings(tools[k], f"tools.{k}")) for k in TOOL_EDIT_KEYS
                    if tools.get(k) is not None}
            if check_tool_ids and edit.get("add"):
                _check_tools(edit["add"], "tools.add")
            out["tools"] = edit
        else:
            ids = _strings(tools, "tools")
            if check_tool_ids:
                _check_tools(ids, "tools")
            out["tools"] = ids
    skills = data.get("skills")
    if skills is not None:
        if isinstance(skills, bool):
            out["skills"] = skills
        else:
            out["skills"] = sorted(_strings(skills, "skills"))
    mcp = data.get("mcp")
    if mcp is not None:
        servers = _strings(mcp, "mcp")
        bad = [s for s in servers if not _SERVER_ID.match(s)]
        if bad:
            raise OverrideError(f"overrides.mcp: not a server id: {', '.join(bad)}")
        out["mcp"] = sorted(servers)
    if data.get("tool_policy") is not None:
        policy = _tool_policy(data["tool_policy"])
        if policy:
            out["tool_policy"] = policy
    if data.get("output_schema") is not None:
        out["output_schema"] = _output_schema(data["output_schema"])
    if data.get("max_concurrent_delegates") is not None:
        out["max_concurrent_delegates"] = _max_concurrent_delegates(data["max_concurrent_delegates"])
    return dict(sorted(out.items()))


def fold_legacy(raw: Any, *, tool_policy: Any = None, output_schema: Any = None,
                check_tool_ids: bool = True) -> Dict[str, Any]:
    """The overrides object with the old separate flags folded in.

    ``--tool-policy`` and ``--output-schema`` (and the ``tool_policy`` and
    ``output_schema`` launch params a proactive tick sends) are aliases of
    the object's keys of the same name; a key the object already carries
    wins over the flag. Raises :class:`OverrideError`.
    """
    data = dict(parse(raw))
    if tool_policy not in (None, "", {}) and data.get("tool_policy") is None:
        data["tool_policy"] = parse(tool_policy) if isinstance(tool_policy, str) else tool_policy
    if output_schema not in (None, "", {}) and data.get("output_schema") is None:
        data["output_schema"] = parse(output_schema) if isinstance(output_schema, str) else output_schema
    return normalize(data, check_tool_ids=check_tool_ids)


# ── applying ─────────────────────────────────────────────────────────────────

def changes_tools(overrides: Dict[str, Any]) -> bool:
    return bool(overrides) and ("tools" in overrides or "mcp" in overrides)


def effective_tools(record_tools: Iterable[str], overrides: Dict[str, Any]) -> List[str]:
    """The run's definition level tool list: the record's, edited by
    ``tools`` and ``mcp``. Equal to the record's when neither is given."""
    tools = [str(t) for t in (record_tools or [])]
    spec = (overrides or {}).get("tools")
    if isinstance(spec, list):
        tools = list(spec)
    elif isinstance(spec, dict):
        removed = set(spec.get("remove") or [])
        tools = [t for t in tools if t not in removed]
        for tool_id in spec.get("add") or []:
            if tool_id not in tools:
                tools.append(tool_id)
    servers = (overrides or {}).get("mcp")
    if servers is not None:
        tools = [t for t in tools if not _is_mcp(t)] + [f"mcp:{s}" for s in servers]
    return tools


def model_params(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """The ``create_agent`` config keys for the run's model."""
    return {k: overrides[k] for k in ("provider", "model") if (overrides or {}).get(k)}


def apply_to_definition(definition: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of ``definition`` (``AgentFactory.load_definition``'s shape)
    with the run's instructions and tool list."""
    if not overrides:
        return definition
    out = dict(definition)
    if overrides.get("system") is not None:
        out["system_prompt"] = overrides["system"]
    if overrides.get("system_append"):
        base = str(out.get("system_prompt") or "")
        out["system_prompt"] = (base + "\n\n---\n\n" + overrides["system_append"]) if base else overrides["system_append"]
    if changes_tools(overrides):
        out["tools"] = effective_tools(out.get("tools") or [], overrides)
    return out


def apply_to_spec(spec: Any, overrides: Dict[str, Any]) -> Any:
    """The spec the build reads its loop settings from, with the run's tool
    policy (merged over the record's), answer schema and skills switch."""
    if spec is None or not overrides:
        return spec
    from dataclasses import replace
    changes: Dict[str, Any] = {}
    if overrides.get("tool_policy"):
        changes["tool_policy"] = {**dict(getattr(spec, "tool_policy", None) or {}),
                                  **overrides["tool_policy"]}
    if overrides.get("output_schema"):
        changes["output_schema"] = dict(overrides["output_schema"])
    skills = overrides.get("skills")
    if skills is not None:
        changes["skills_enabled"] = bool(skills) if isinstance(skills, bool) else True
    return replace(spec, **changes) if changes else spec


def skills_catalog(agent_id: str, workspace: str, system_prompt: str, names: List[str]) -> str:
    """The skills catalog for a run whose ``skills`` override names the
    skills to list: the same lines memory.procedural.inject_skills_catalog
    writes, for the named skills only (by name or id)."""
    try:
        from memory.procedural import ProcedureStore, _visible_procedures
        from memory.skill_versions import effective_content
        wanted = {str(n).strip().lower() for n in names}
        # Own skills and those inherited from the agent's parents (extends).
        procedures = [p for p in _visible_procedures(ProcedureStore(workspace), agent_id)
                      if (str(p.name).lower() in wanted or str(getattr(p, "id", "")).lower() in wanted)]
        if not procedures:
            return system_prompt
        lines = ["\n\n## Available Skills\n",
                 "The following skills are available to you. "
                 "When the task matches a skill, its full steps will be provided automatically. "
                 "Use `get_skill` with the skill's name to fetch its steps.\n"]
        for p in procedures:
            lines.append(f"- **{p.name}**: {effective_content(p)['description']}")
        return system_prompt + "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001 - a store hiccup leaves the catalog out, never the run
        log.debug("run overrides: skills catalog for %s not built", agent_id, exc_info=True)
        return system_prompt


def guard_tools(agent_id: str, tools: List[str], spec: Any) -> None:
    """Refuse a run tool set that forms a capability combination the record
    does not already form. Raises ``agents.capability_guard.CapabilityViolation``.

    Judged like a save of the record with these tools (``enforce_agent_tools``),
    with the record's own tools as the previous state: a combination the record
    already holds (by grandfathering or its ``capability_override``) stays as
    it is, a new one is refused in block mode whatever the override says,
    because the override was granted for the record, not for every run of it.
    """
    from agents.capability_guard import enforce_agent_tools
    enforce_agent_tools(
        agent_id, list(tools),
        previous_tools=list(getattr(spec, "tools", None) or []) if spec is not None else None,
        override=False,
        delegates=list(getattr(spec, "delegates", None) or []) if spec is not None else None,
    )


def validate_for_agent(agent_id: str, raw: Any, *, tool_policy: Any = None,
                       output_schema: Any = None) -> Dict[str, Any]:
    """Normalise ``raw`` for a run of ``agent_id`` and check its tool set
    against the capability guard. What a route calls before it starts a run.

    Raises :class:`OverrideError` (400) or ``CapabilityViolation`` (409).
    """
    overrides = fold_legacy(raw, tool_policy=tool_policy, output_schema=output_schema)
    if changes_tools(overrides):
        from agents.registry import get_agent
        spec = get_agent(agent_id)
        if spec is not None:
            guard_tools(agent_id, effective_tools(spec.tools or [], overrides), spec)
    return overrides


def effective_max_concurrent_delegates(agent_spec: Any, overrides: Optional[Dict[str, Any]]) -> int:
    """How many delegated subtasks one run of ``agent_spec`` may have running
    at once: this run's own override when set, else the agent's own field,
    else 6. What ``agent_launcher`` puts in the run's environment for
    ``delegate_task_tool`` to enforce (agents/agent_launcher.py)."""
    value = (overrides or {}).get("max_concurrent_delegates")
    if value is None:
        value = getattr(agent_spec, "max_concurrent_delegates", None) if agent_spec is not None else None
    try:
        number = int(value) if value is not None else 6
    except (TypeError, ValueError):
        number = 6
    return min(MAX_CONCURRENT_DELEGATES_MAX, max(MAX_CONCURRENT_DELEGATES_MIN, number))


def record_view(overrides: Dict[str, Any]) -> Dict[str, Any]:
    """What the run record keeps (``run.overrides``): the object itself, the
    system texts clipped so a long prompt does not bloat every run listing."""
    out: Dict[str, Any] = {}
    for key, value in (overrides or {}).items():
        if key in ("system", "system_append") and isinstance(value, str) and len(value) > RECORD_TEXT_CHARS:
            out[key] = value[:RECORD_TEXT_CHARS] + f"\n...[{len(value) - RECORD_TEXT_CHARS} more characters]"
        else:
            out[key] = value
    return out


def build_kwargs(overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``create_agent`` keyword arguments for these overrides (empty when none)."""
    return {"run_overrides": dict(overrides)} if overrides else {}


__all__ = [
    "KEYS", "OverrideError", "normalize", "fold_legacy", "parse", "effective_tools",
    "apply_to_definition", "apply_to_spec", "model_params", "guard_tools",
    "validate_for_agent", "record_view", "build_kwargs", "changes_tools", "skills_catalog",
    "effective_max_concurrent_delegates", "MAX_CONCURRENT_DELEGATES_MIN", "MAX_CONCURRENT_DELEGATES_MAX",
]
