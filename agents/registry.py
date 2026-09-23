"""
Agent registry loader.

Loads and validates the agent registry (the ``agents`` document collection
in the database, common/docstore.py; an existing ``.agents_hub/agents.json``
is imported once and renamed ``.migrated``) and exposes a small API:
- list_agents() -> list[AgentSpec]
- get_agent(agent_id: str) -> AgentSpec | None

Validation rules:
- JSON must contain object with key "agents": [ ... ]
- Each agent must provide: id, name, type, entrypoint
- The system prompt lives in agents/definitions/<id>/instructions.md, NOT here
- type is "langchain" for agents this hub assembles and runs, or "remote" for an
  agent imported from its own repository and reached over HTTP; a remote record
  carries its endpoint, provenance and last readiness report under `remote`
  (see agents/importer/ and agents/remote_agent.py)
- Flat execution fields: temperature (float|None), max_tokens (int|None),
  api_key (str|None), verbose (bool), streaming (bool)
- tools is a list of strings (defaults to [])
- Agent IDs must be unique
- entrypoint must be in the form "module.sub:attr" (importable)

The loader caches results and reloads when the store's signature changes.
Inside a run container (``AGENTS_HUB_SNAPSHOT_DIR`` set, see
common/snapshot.py) it reads the snapshot the launcher wrote and refuses
writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import json
import logging
from common import snapshot
from common.docstore import DocStore
from common.paths import AGENTS_FILE

log = logging.getLogger(__name__)


# -------------------- Data models --------------------

def normalize_memory_pools(memory_type: str, memory_data: Any) -> List[str]:
    """Normalize a memory assignment to a list of pool ids, primary first.

    Accepts a single pool id (legacy) or a list of ids; strips and dedupes.
    Returns [] unless memory_type is 'shared' with at least one pool.
    """
    if memory_type != "shared" or not memory_data:
        return []
    raw = memory_data if isinstance(memory_data, (list, tuple)) else [memory_data]
    pools: List[str] = []
    for p in raw:
        pid = str(p).strip()
        if pid and pid not in pools:
            pools.append(pid)
    return pools


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    type: str
    entrypoint: str
    # definition_id: when set, the agent's prompt/markdown is loaded from
    # agents/definitions/<definition_id>/ instead of agents/definitions/<id>/.
    # This lets multiple records (e.g. one per workspace, each with its own
    # model/memory settings) share a single common definition. None means
    # "same as id" (the legacy 1:1 behaviour). Use def_id() to resolve.
    definition_id: Optional[str] = None
    description: str = ""
    domain: str = "general"
    default_params: Dict[str, Any] = field(default_factory=dict)
    tools: List[str] = field(default_factory=list)
    commands: List[Dict[str, Any]] = field(default_factory=list)
    capacity: int = 1
    memory_type: str = "none"
    memory_data: Any = None
    default_workspace_only: bool = False
    # Workspace ownership / visibility:
    # - owner_workspace: the workspace the agent was created in. When set and the
    #   agent is not shared, the agent is only visible in (and addable to) that
    #   workspace. None means the agent is not bound to any workspace (legacy /
    #   system / globally available agents).
    # - shared: when True, the agent is exposed across all workspaces regardless
    #   of owner_workspace (it can be added to any workspace).
    owner_workspace: Optional[str] = None
    shared: bool = False
    # Model configuration — explicit per-agent overrides; None means inherit from workspace/global
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    # Execution parameters — flat top-level fields (replaces default_params)
    # NB: system_prompt is intentionally NOT here. The prompt is sourced from
    # agents/definitions/<id>/instructions.md (+ capabilities.md, usage.md).
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    api_key: Optional[str] = None
    verbose: bool = False
    streaming: bool = False
    # Container HTTP exposure — when running in Docker, optionally expose an HTTP server
    http_expose: bool = False
    http_port: int = 8080       # port inside the container
    http_host_port: Optional[int] = None  # host-side port mapping (None = same as http_port)
    # Node type — determines how the node process runs when started
    # "worker"  : runs a polling loop consuming tasks from the queue (default)
    # "service" : runs an HTTP server only, no task polling
    node_type: str = "worker"
    # Chat default — if True, this agent is pre-selected when opening the Chat page
    is_default_chat_agent: bool = False
    # ── System vs. custom ────────────────────────────────────────────────────
    # An agent is one of exactly two things. A system agent is shipped in
    # bootstrap/agents.json and owned by the product: it is present in every
    # workspace, cannot be removed from one, and bootstrap keeps its tools and
    # description in sync with the seed on every start (see
    # common.bootstrap._sync_system_agents). Everything else is a custom agent,
    # owned by the operator, and bootstrap never touches it.
    #
    # This flag is the single source of truth for that split. It replaced both
    # the older `domain == "System"` convention (now purely thematic) and the
    # hardcoded id tuples that used to live in workspace/storage.py and
    # common/workspace_context.py.
    system: bool = False
    # Set on any system agent the operator has edited (through the dashboard or
    # the agent-management tools). Bootstrap's sync skips these records, so a
    # hand-tuned system agent is never silently reverted to the seed.
    user_modified: bool = False
    # When True: skills tools are auto-added and procedural context is injected at runtime
    skills_enabled: bool = False
    # Controls the episodic WRITE tool (record_episode) for agents with a shared
    # memory pool. Tri-state:
    #   None (default) — AUTO: on for cloud providers, off for local providers
    #     (ollama/lmstudio), whose smaller models tend to misfire on it.
    #   True  — always attach record_episode and teach its use.
    #   False — never attach it (other memory — recall/remember/recall_episodes/
    #     graph — and the automatic silent journal are unaffected).
    episodic_write_enabled: Optional[bool] = None
    # Delegation allowlist — agent ids this agent may delegate to / see via the
    # coordination tools (list_agents_tool, run_agent_tool, assign_agent_tool).
    # Empty list (default) means no restriction: every agent available in the
    # workspace is delegatable. When non-empty, only these ids (intersected with
    # workspace availability) are visible and runnable as delegation targets.
    delegates: List[str] = field(default_factory=list)
    # Reasoning capability settings — keyed by tool id (e.g. "think", "plan")
    reasoning: Dict[str, Any] = field(default_factory=dict)
    # Structured response format this agent may emit (rendered as buttons / a
    # Telegram inline keyboard). One of "none" (default), "buttons", "telegram".
    # When not "none", agent_factory injects a system-prompt snippet teaching the
    # <<<ui>>> block convention, the same way reasoning guidance is injected.
    response_format: str = "none"
    # Chat clarification gate. When True, agent_factory injects a system-prompt
    # snippet telling the agent to assess whether it has enough information before
    # producing deliverables and, if not, ask concise clarifying questions and
    # stop rather than proceeding on assumptions. Primarily meaningful in chat,
    # where the next turn replays history so the agent resumes once answered.
    clarify_gate: bool = False
    # Self-delegation. When False (default) an agent may not target itself in the
    # coordination tools (run_agent_tool / assign_agent_tool) — a self-run would
    # recurse the same agent. When True the agent is allowed to hand a sub-goal
    # back to itself; enforced in tools._delegation_blocked.
    allow_self_delegation: bool = False
    # Capability guard escape hatch. When True the operator has explicitly
    # accepted a tool combination that tools/capabilities.py would otherwise
    # block (see enforce_capabilities below). Under
    # settings.capability_override_requires_container the override is only
    # honoured at build time for container-isolated, no-network runs.
    capability_override: bool = False
    # Per-agent tweaks to the tool-approval gate (tools/approval.py). Both are
    # empty by default, so the agent follows the shared NEEDS_APPROVAL list:
    # ``approval_tools`` adds tool ids that this agent may not call unapproved,
    # ``approval_exempt`` removes ones it may, and the exemption wins.
    approval_tools: List[str] = field(default_factory=list)
    approval_exempt: List[str] = field(default_factory=list)
    # Workspace secrets (common/secrets.py) this agent may receive, by name.
    # Empty by default, and empty means none: a run is handed only the names
    # listed here, as environment variables. A non-empty list counts as
    # reading private data for the capability guard (tools/capabilities.py).
    secrets: List[str] = field(default_factory=list)
    # External-agent descriptor — empty for built-in agents. When ``type`` is
    # "remote" this holds everything needed to reach the agent over HTTP
    # (``url``/``run_path``/``health_path``/``timeout``/``auth_*``), the
    # provenance of the import (``repo_url``/``branch``/``commit``/``clone_path``)
    # and the last readiness report (``readiness``). Written by
    # ``agents.importer``, consumed by ``agents.remote_agent.RemoteAgent``.
    remote: Dict[str, Any] = field(default_factory=dict)

    def is_remote(self) -> bool:
        """Whether this record is an externally hosted (HTTP) agent.

        Remote agents are not built from ``agents/definitions`` + the internal
        tool registry; ``agent_factory`` hands them to ``RemoteAgent`` instead.
        """
        return self.type == "remote"

    def def_id(self) -> str:
        """Resolve the definition folder name for this agent.

        Returns ``definition_id`` when set (shared definition), otherwise the
        agent's own ``id`` (legacy 1:1 mapping). This is the single accessor
        used wherever the ``agents/definitions/<...>/`` folder is resolved.
        """
        return self.definition_id or self.id

    def memory_pools(self) -> List[str]:
        """Shared memory pool ids on this record, primary first.

        ``memory_data`` holds either a single pool id (legacy) or a list of
        ids. The first entry is the primary pool — all memory writes go there;
        the rest are read-only context. Returns [] when the record has no
        shared memory.

        NB: this is the record-level assignment, which applies in the agent's
        home workspace only. Runtime consumers should resolve the assignment
        for the workspace they operate in via memory.binding.effective_memory_pools.
        """
        return normalize_memory_pools(self.memory_type, self.memory_data)

    def model_overrides(self) -> Dict[str, Any]:
        """Per-agent model/provider overrides as a canonical kwargs dict.

        The single source of truth for the registry override cascade used by
        both in-process agent creation (``create_agent(**overrides)`` in the
        chat routes) and subprocess launches (``agent_launcher`` maps these
        onto ``AGENT_*`` env vars). Only set fields are included; ``provider``
        is skipped when it is the sentinel ``"inherit"``.
        """
        overrides: Dict[str, Any] = {}
        if self.provider and self.provider != "inherit":
            overrides["provider"] = self.provider
        if self.model:
            overrides["model"] = self.model
        if self.base_url:
            overrides["base_url"] = self.base_url
        if self.api_key:
            overrides["api_key"] = self.api_key
        if self.temperature is not None:
            overrides["temperature"] = self.temperature
        if self.max_tokens is not None:
            overrides["max_tokens"] = self.max_tokens
        return overrides

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "entrypoint": self.entrypoint,
            "description": self.description,
            "domain": self.domain,
            "tools": list(self.tools) if self.tools else [],
            "commands": list(self.commands) if self.commands else [],
            "capacity": self.capacity,
            "memory_type": self.memory_type,
            "memory_data": self.memory_data,
            "default_workspace_only": self.default_workspace_only,
            "shared": self.shared,
        }
        # Only write definition_id when it differs from id (shared definition).
        # Legacy 1:1 records stay clean and unchanged.
        if self.definition_id and self.definition_id != self.id:
            d["definition_id"] = self.definition_id
        # Only write owner_workspace when set to keep JSON clean and to treat
        # legacy agents (no owner) as globally available.
        if self.owner_workspace:
            d["owner_workspace"] = self.owner_workspace
        # Only write model/provider fields when explicitly set to keep JSON clean
        if self.provider is not None:
            d["provider"] = self.provider
        if self.model is not None:
            d["model"] = self.model
        if self.base_url is not None:
            d["base_url"] = self.base_url
        # Only write execution fields when non-default to keep JSON clean
        # api_key is intentionally omitted from to_dict() output (sensitive)
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.verbose:
            d["verbose"] = self.verbose
        if self.streaming:
            d["streaming"] = self.streaming
        if self.http_expose:
            d["http_expose"] = self.http_expose
            d["http_port"] = self.http_port
            if self.http_host_port is not None:
                d["http_host_port"] = self.http_host_port
        if self.node_type != "worker":
            d["node_type"] = self.node_type
        d["skills_enabled"] = self.skills_enabled
        # Only write the system fields on system records, so operator-created
        # agents keep a clean JSON shape.
        if self.system:
            d["system"] = True
            if self.user_modified:
                d["user_modified"] = True
        # Only write when explicitly set (True/False); omit when auto (None).
        if self.episodic_write_enabled is not None:
            d["episodic_write_enabled"] = self.episodic_write_enabled
        if self.reasoning:
            d["reasoning"] = dict(self.reasoning)
        # Only write when set, to keep default records clean.
        if self.response_format and self.response_format != "none":
            d["response_format"] = self.response_format
        # Only write when enabled, to keep default records clean.
        if self.clarify_gate:
            d["clarify_gate"] = self.clarify_gate
        # Only write when enabled, to keep default (self-block) records clean.
        if self.allow_self_delegation:
            d["allow_self_delegation"] = self.allow_self_delegation
        # Only write delegates when restricted, to keep unrestricted records clean.
        if self.delegates:
            d["delegates"] = list(self.delegates)
        # Only write the approval overrides when set, to keep default records clean.
        if self.approval_tools:
            d["approval_tools"] = list(self.approval_tools)
        if self.approval_exempt:
            d["approval_exempt"] = list(self.approval_exempt)
        if self.secrets:
            d["secrets"] = list(self.secrets)
        # Only write when the operator has accepted a blocked combination.
        if self.capability_override:
            d["capability_override"] = self.capability_override
        # Only write the external-agent descriptor when the record has one, so
        # built-in agents keep a clean JSON shape.
        if self.remote:
            d["remote"] = dict(self.remote)
        return d

    def load_callable(self) -> Callable[..., Any]:
        """Resolve entrypoint string to a Python callable.

        Expected format: "module.path:attribute". Raises ValueError if not importable.
        """
        mod_name, attr_name = _split_entrypoint(self.entrypoint)
        try:
            mod = import_module(mod_name)
        except Exception as e:
            raise ValueError(f"Failed to import module '{mod_name}' for agent '{self.id}': {e}") from e
        try:
            fn = getattr(mod, attr_name)
        except Exception as e:
            raise ValueError(
                f"Module '{mod_name}' does not provide attribute '{attr_name}' for agent '{self.id}'"
            ) from e
        if not callable(fn):
            raise ValueError(
                f"Entrypoint '{self.entrypoint}' for agent '{self.id}' is not callable"
            )
        return fn


# -------------------- Internals --------------------

_REGISTRY_CACHE: dict[str, Any] = {
    "mtime": None,
    "agents": None,  # type: ignore[assignment]
}


def _config_path() -> Path:
    """The legacy registry file (``agents.json``). The registry itself lives in
    the database now; the file is imported once on first use and renamed
    ``.migrated``. Kept for callers that still name it."""
    return AGENTS_FILE


# The registry: one document per agent, keyed by id, insertion order kept.
# add_agent/remove_agent run from the dashboard process as well as agent
# subprocesses (create_agent_tool / modify_agent_tool in
# tools/langchain_tools.py); the store's transaction serializes them across
# processes and hosts.
_agents_store = DocStore("agents")
_legacy_checked_for: Optional[int] = None


def _record_key(rec: Any) -> Optional[str]:
    return str(rec["id"]) if isinstance(rec, dict) and rec.get("id") else None


def _ensure_legacy_imported() -> None:
    """Import ``agents.json`` (``{"agents": [...]}``) once, keyed by id."""
    global _legacy_checked_for
    from common import db
    db.get_conn()
    if _legacy_checked_for == db._generation:
        return
    _legacy_checked_for = db._generation
    path = _config_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("agents.json is unreadable and was left in place: %s", exc)
        return
    items = raw.get("agents") if isinstance(raw, dict) else raw
    docs: Dict[str, Any] = {}
    for rec in (items or []):
        key = _record_key(rec)
        if key:
            docs[key] = rec
    _agents_store.import_legacy(docs, path)


def load_all_raw() -> List[Dict[str, Any]]:
    """Every agent record as stored (dicts, registry order). In a run
    container this is the snapshot the launcher wrote (common/snapshot.py)."""
    snap = snapshot.read_snapshot(snapshot.AGENTS_SNAPSHOT)
    if snap is not None:
        items = snap.get("agents") if isinstance(snap, dict) else snap
        return [rec for rec in (items or []) if isinstance(rec, dict)]
    _ensure_legacy_imported()
    return [rec for rec in _agents_store.values() if isinstance(rec, dict)]


def replace_all_raw(records: List[Dict[str, Any]]) -> None:
    """Make the registry exactly ``records`` (dicts with an ``id``), in that
    order. Bootstrap and tests use it; the API goes through add/remove."""
    if snapshot.in_snapshot_mode():
        snapshot.refuse_write("the agent registry")
    _ensure_legacy_imported()
    _agents_store.replace_all({str(r["id"]): r for r in records if isinstance(r, dict) and r.get("id")})
    _REGISTRY_CACHE["mtime"] = None


def export_snapshot() -> Dict[str, Any]:
    """The registry as the snapshot file for a run container holds it: the
    same ``{"agents": [...]}`` shape agents.json had."""
    return {"agents": load_all_raw()}


def _registry_signature() -> str:
    if snapshot.in_snapshot_mode():
        return "snapshot"
    _ensure_legacy_imported()
    return _agents_store.signature()


def _split_entrypoint(entrypoint: str) -> tuple[str, str]:
    if ":" not in entrypoint:
        raise ValueError(
            f"Invalid entrypoint format '{entrypoint}'. Expected 'module.path:attribute'"
        )
    mod, attr = entrypoint.split(":", 1)
    mod = mod.strip()
    attr = attr.strip()
    if not mod or not attr:
        raise ValueError(
            f"Invalid entrypoint format '{entrypoint}'. Expected 'module.path:attribute'"
        )
    return mod, attr


essential_fields = ("id", "name", "type", "entrypoint")


def _validate_agent_dict(ad: Dict[str, Any]) -> AgentSpec:
    # basic required fields
    for k in essential_fields:
        if k not in ad or not isinstance(ad[k], str) or not ad[k].strip():
            raise ValueError(f"Agent missing required field '{k}' or value is empty: {ad}")

    # normalize optional fields
    # Keep reading default_params for backward compatibility with old JSON files
    legacy_dp = ad.get("default_params")
    if legacy_dp is None:
        legacy_dp = {}
    if not isinstance(legacy_dp, dict):
        legacy_dp = {}

    tools = ad.get("tools")
    if tools is None:
        tools = []
    if not isinstance(tools, list) or not all(isinstance(x, str) for x in tools):
        raise ValueError(
            f"Agent tools must be a list of strings: id={ad.get('id')}"
        )

    commands = ad.get("commands", [])
    if not isinstance(commands, list):
        commands = []

    capacity = ad.get("capacity", 1)
    if not isinstance(capacity, int):
        try:
            capacity = int(capacity)
        except Exception:
            capacity = 1

    memory_type = ad.get("memory_type", "none")
    memory_data = ad.get("memory_data")
    default_workspace_only = bool(ad.get("default_workspace_only", False))
    definition_id = ad.get("definition_id") or None
    owner_workspace = ad.get("owner_workspace") or None
    shared = bool(ad.get("shared", False))
    provider = ad.get("provider") or None
    model = ad.get("model") or None
    base_url = ad.get("base_url") or None

    # Flat execution fields — read from top-level first, fall back to legacy default_params
    _raw_temperature = ad.get("temperature") if "temperature" in ad else legacy_dp.get("temperature")
    temperature: Optional[float] = float(_raw_temperature) if _raw_temperature is not None else None
    _raw_max_tokens = ad.get("max_tokens") if "max_tokens" in ad else legacy_dp.get("max_tokens")
    max_tokens: Optional[int] = int(_raw_max_tokens) if _raw_max_tokens is not None else None
    api_key = ad.get("api_key") or legacy_dp.get("api_key") or None
    verbose = bool(ad.get("verbose", legacy_dp.get("verbose", False)))
    streaming = bool(ad.get("streaming", legacy_dp.get("streaming", False)))
    http_expose = bool(ad.get("http_expose", False))
    _raw_http_port = ad.get("http_port", 8080)
    http_port: int = int(_raw_http_port) if _raw_http_port is not None else 8080
    _raw_http_host_port = ad.get("http_host_port")
    http_host_port: Optional[int] = int(_raw_http_host_port) if _raw_http_host_port is not None else None
    node_type = ad.get("node_type", "worker")
    if node_type not in ("worker", "service"):
        node_type = "worker"
    is_default_chat_agent = bool(ad.get("is_default_chat_agent", False))
    # Legacy seeds marked system agents with domain == "System". Keep reading it
    # so an install that predates the `system` flag still recognises its own
    # system agents on the first start after the upgrade.
    system = bool(ad.get("system", ad.get("domain") == "System"))
    user_modified = bool(ad.get("user_modified", False))
    skills_enabled = bool(ad.get("skills_enabled", False))
    _raw_epi = ad.get("episodic_write_enabled")
    episodic_write_enabled = bool(_raw_epi) if _raw_epi is not None else None
    reasoning = ad.get("reasoning") or {}
    if not isinstance(reasoning, dict):
        reasoning = {}

    response_format = ad.get("response_format") or "none"
    if response_format not in ("none", "buttons", "telegram"):
        response_format = "none"

    clarify_gate = bool(ad.get("clarify_gate", False))
    allow_self_delegation = bool(ad.get("allow_self_delegation", False))
    capability_override = bool(ad.get("capability_override", False))

    remote = ad.get("remote") or {}
    if not isinstance(remote, dict):
        remote = {}

    raw_delegates = ad.get("delegates")
    delegates: List[str] = []
    if isinstance(raw_delegates, (list, tuple)):
        for d in raw_delegates:
            did = str(d).strip()
            if did and did not in delegates:
                delegates.append(did)

    def _id_list(raw: Any) -> List[str]:
        """A clean, de-duplicated list of tool ids from whatever JSON holds."""
        out: List[str] = []
        if isinstance(raw, (list, tuple)):
            for item in raw:
                value = str(item).strip()
                if value and value not in out:
                    out.append(value)
        return out

    approval_tools = _id_list(ad.get("approval_tools"))
    approval_exempt = _id_list(ad.get("approval_exempt"))
    secrets = _id_list(ad.get("secrets"))

    # Validate entrypoint shape early
    _split_entrypoint(ad["entrypoint"])  # raises if malformed

    description = ad.get("description", "")
    domain = ad.get("domain", "general")

    return AgentSpec(
        id=ad["id"].strip(),
        definition_id=definition_id,
        name=ad["name"].strip(),
        type=ad["type"].strip(),
        entrypoint=ad["entrypoint"].strip(),
        description=description,
        domain=domain,
        default_params={},
        tools=tools,
        commands=commands,
        capacity=capacity,
        memory_type=memory_type,
        memory_data=memory_data,
        default_workspace_only=default_workspace_only,
        owner_workspace=owner_workspace,
        shared=shared,
        provider=provider,
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        verbose=verbose,
        streaming=streaming,
        http_expose=http_expose,
        http_port=http_port,
        http_host_port=http_host_port,
        node_type=node_type,
        is_default_chat_agent=is_default_chat_agent,
        system=system,
        user_modified=user_modified,
        skills_enabled=skills_enabled,
        episodic_write_enabled=episodic_write_enabled,
        reasoning=reasoning,
        response_format=response_format,
        clarify_gate=clarify_gate,
        allow_self_delegation=allow_self_delegation,
        capability_override=capability_override,
        delegates=delegates,
        approval_tools=approval_tools,
        approval_exempt=approval_exempt,
        secrets=secrets,
        remote=remote,
    )


def _load_agents_from_store() -> List[AgentSpec]:
    specs: List[AgentSpec] = []
    seen: set[str] = set()
    for idx, item in enumerate(load_all_raw()):
        if not isinstance(item, dict):
            raise ValueError(f"Agent entry at index {idx} must be an object, got {type(item).__name__}")
        spec = _validate_agent_dict(item)
        if spec.id in seen:
            raise ValueError(f"Duplicate agent id '{spec.id}' in the agent registry")
        seen.add(spec.id)
        specs.append(spec)
    return specs


def _maybe_reload() -> List[AgentSpec]:
    """The validated registry, rebuilt only when the store changed. The cache
    key is the store's signature (row count and latest write); ``mtime`` is
    the historical name of that slot and setting it to None still forces a
    reload, which callers and tests rely on."""
    key = _registry_signature()
    cached_key = _REGISTRY_CACHE.get("mtime")
    agents = _REGISTRY_CACHE.get("agents")
    if agents is not None and key == cached_key:
        return agents  # type: ignore[return-value]

    specs = _load_agents_from_store()
    _REGISTRY_CACHE["mtime"] = key
    _REGISTRY_CACHE["agents"] = specs
    return specs


# -------------------- Public API --------------------

def list_agents(limit: Optional[int] = None, offset: Optional[int] = None) -> List[AgentSpec]:
    """Return the list of available AgentSpec objects (validated).

    ``limit``/``offset`` page over the cached spec list already held in
    memory (the mtime-cached result of ``_maybe_reload``), so a page costs a
    slice, not a re-read of agents.json. With neither given, the full list is
    returned exactly as before. A caller that must filter before paging (e.g.
    the ``/api/agents`` route's workspace visibility rules) pages the filtered
    result itself instead; this is for a caller that wants a page of the raw
    registry.
    """
    specs = list(_maybe_reload())
    if limit is None and offset is None:
        return specs
    start = offset or 0
    return specs[start: start + limit] if limit is not None else specs[start:]


def count_agents() -> int:
    """Total number of registry agents, for a caller paging with
    :func:`list_agents` that needs ``total`` without holding the whole list."""
    return len(_maybe_reload())


def get_agent(agent_id: str) -> Optional[AgentSpec]:
    """Return a specific agent by id or None if not found."""
    if not agent_id:
        return None
    for spec in _maybe_reload():
        if spec.id == agent_id:
            return spec
    return None


def system_agent_ids() -> List[str]:
    """Ids of the product's own agents, in registry order.

    The single source of truth for the system/custom split. A broken or
    unreadable registry yields an empty list rather than raising: callers fall
    back to their own constants, so a bad agents.json cannot strip every
    workspace of its system agents at once.
    """
    try:
        specs = _maybe_reload()
    except Exception:
        return []
    return [spec.id for spec in specs if spec.system]


def add_agent(
    spec: AgentSpec,
    *,
    user_edit: bool = True,
    actor: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Persist a new agent spec to the registry.

    Save time is the capability guard's chokepoint: every write path (dashboard
    routes, ``create_agent_tool`` / ``modify_agent_tool``, bootstrap) lands here,
    so a tool set forming a blocked capability combination never reaches disk.
    Raises ``CapabilityViolation`` (a ``ValueError``) when it does — the routes'
    existing ValueError handlers turn that into a 400 with the offending
    capabilities named.

    ``actor``/``note`` are attributed to the version-history snapshot this call
    may take (see below); both are optional and go unused when nothing needs
    snapshotting.
    """
    from agents.capability_guard import enforce_agent_tools

    _prev = get_agent(spec.id)
    from tools.capabilities import secret_grant_ids

    # Declared secrets ride along as pseudo tool ids (``secrets:<NAME>``) that
    # grant reads_private, so holding a token closes the trifecta like any
    # private-data tool would.
    enforce_agent_tools(
        spec.id,
        list(spec.tools or []) + secret_grant_ids(spec.secrets),
        previous_tools=(list(_prev.tools or []) + secret_grant_ids(_prev.secrets)) if _prev else None,
        override=bool(spec.capability_override),
        # The spec being saved, not the (possibly stale-or-absent) registry
        # record: a brand-new agent, or one whose delegates list is being
        # narrowed in this very call, must be judged on the allowlist it is
        # about to have. See agents.capability_guard.check_agent_tools.
        delegates=list(spec.delegates or []),
    )

    # A system agent the operator edits stops tracking the seed: bootstrap's
    # sync skips records carrying this flag, so the edit survives every restart.
    # Bootstrap itself writes with user_edit=False and leaves the flag alone.
    if user_edit and spec.system and not spec.user_modified:
        import dataclasses as _dc
        spec = _dc.replace(spec, user_modified=True)

    # Snapshot whatever is currently stored into version history before this
    # call replaces it, so history never has a gap. Only fires when the agent
    # already exists and its stored definition differs from the last snapshot
    # on file; best-effort, never blocks a legitimate write (see
    # agents.versions.snapshot_if_changed).
    if _prev is not None:
        try:
            from agents.versions import snapshot_if_changed
            snapshot_if_changed(spec.id, next_spec=spec, actor=actor, note=note)
        except Exception:
            log.warning("could not snapshot version history for '%s'", spec.id, exc_info=True)

    if snapshot.in_snapshot_mode():
        snapshot.refuse_write("the agent registry")
    _ensure_legacy_imported()
    with _agents_store.transaction():
        _agents_store.put(spec.id, spec.to_dict())

    # Force reload on next access
    _REGISTRY_CACHE["mtime"] = None
    _notify_agents_changed(spec.id)


def remove_agent(agent_id: str) -> bool:
    """Remove an agent from the registry by id. Returns True if found and removed."""
    if snapshot.in_snapshot_mode():
        snapshot.refuse_write("the agent registry")
    _ensure_legacy_imported()
    if not _agents_store.delete(str(agent_id)):
        return False

    # Force reload on next access
    _REGISTRY_CACHE["mtime"] = None
    _notify_agents_changed(agent_id)
    return True


def _notify_agents_changed(agent_id: str | None = None) -> None:
    # Drop any cached build for the changed agent immediately. The build cache
    # also fingerprints the registry store's signature, so this is belt-and-
    # braces — an edit takes effect on the very next run.
    try:
        from agents.agent_cache import invalidate
        invalidate(agent_id)
    except Exception:
        pass
    try:
        from common.session_broker import notify_change
        notify_change("agents", agent_id=agent_id)
    except Exception:
        pass


def set_default_chat_agent(agent_id: str) -> None:
    """Deprecated: default chat agent is stored per workspace metadata."""
    raise ValueError("Default chat agent is stored in workspace metadata, not in the agent registry")


def clear_default_chat_agent() -> None:
    """Deprecated: default chat agent is stored per workspace metadata."""
    raise ValueError("Default chat agent is stored in workspace metadata, not in the agent registry")
