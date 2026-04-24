"""
Agent registry loader.

Loads and validates available agents from the shared `.agents_hub/agents.json`
module and exposes a small API:
- list_agents() -> list[AgentSpec]
- get_agent(agent_id: str) -> AgentSpec | None

Validation rules:
- JSON must contain object with key "agents": [ ... ]
- Each agent must provide: id, name, type, entrypoint
- Flat execution fields: system_prompt (str), temperature (float|None),
  max_tokens (int|None), api_key (str|None), verbose (bool), streaming (bool)
- tools is a list of strings (defaults to [])
- Agent IDs must be unique
- entrypoint must be in the form "module.sub:attr" (importable)

The loader caches results and will auto-reload if the file mtime changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import json
from common.paths import AGENTS_FILE


# -------------------- Data models --------------------

@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    type: str
    entrypoint: str
    description: str = ""
    domain: str = "general"
    default_params: Dict[str, Any] = field(default_factory=dict)
    tools: List[str] = field(default_factory=list)
    commands: List[Dict[str, Any]] = field(default_factory=list)
    capacity: int = 1
    is_remote: bool = False
    agent_url: Optional[str] = None
    original_id: Optional[str] = None
    memory_type: str = "none"
    memory_data: Any = None
    default_workspace_only: bool = False
    # Model configuration — explicit per-agent overrides; None means inherit from workspace/global
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    # Execution parameters — flat top-level fields (replaces default_params)
    system_prompt: str = ""
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
    # Reasoning capability settings — keyed by tool id (e.g. "think", "plan")
    reasoning: Dict[str, Any] = field(default_factory=dict)

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
            "is_remote": self.is_remote,
            "agent_url": self.agent_url,
            "original_id": self.original_id,
            "memory_type": self.memory_type,
            "memory_data": self.memory_data,
            "default_workspace_only": self.default_workspace_only,
        }
        # Only write model/provider fields when explicitly set to keep JSON clean
        if self.provider is not None:
            d["provider"] = self.provider
        if self.model is not None:
            d["model"] = self.model
        if self.base_url is not None:
            d["base_url"] = self.base_url
        # Only write execution fields when non-default to keep JSON clean
        # api_key is intentionally omitted from to_dict() output (sensitive)
        if self.system_prompt:
            d["system_prompt"] = self.system_prompt
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
        if self.is_default_chat_agent:
            d["is_default_chat_agent"] = True
        if self.reasoning:
            d["reasoning"] = dict(self.reasoning)
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
    return AGENTS_FILE


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

    is_remote = bool(ad.get("is_remote", False))
    agent_url = ad.get("agent_url")
    original_id = ad.get("original_id")
    memory_type = ad.get("memory_type", "none")
    memory_data = ad.get("memory_data")
    default_workspace_only = bool(ad.get("default_workspace_only", False))
    provider = ad.get("provider") or None
    model = ad.get("model") or None
    base_url = ad.get("base_url") or None

    # Flat execution fields — read from top-level first, fall back to legacy default_params
    system_prompt = ad.get("system_prompt") or legacy_dp.get("system_prompt") or ""
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
    reasoning = ad.get("reasoning") or {}
    if not isinstance(reasoning, dict):
        reasoning = {}

    # Validate entrypoint shape early (if not remote)
    if not is_remote:
        _split_entrypoint(ad["entrypoint"])  # raises if malformed

    description = ad.get("description", "")
    domain = ad.get("domain", "general")

    return AgentSpec(
        id=ad["id"].strip(),
        name=ad["name"].strip(),
        type=ad["type"].strip(),
        entrypoint=ad["entrypoint"].strip(),
        description=description,
        domain=domain,
        default_params={},
        tools=tools,
        commands=commands,
        capacity=capacity,
        is_remote=is_remote,
        agent_url=agent_url,
        original_id=original_id,
        memory_type=memory_type,
        memory_data=memory_data,
        default_workspace_only=default_workspace_only,
        provider=provider,
        model=model,
        base_url=base_url,
        system_prompt=system_prompt,
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
        reasoning=reasoning,
    )


def _load_file_raw(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Agents config not found at {path}. Ensure 'agents.json' exists."
        ) from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in agents config at {path}: {e}") from e


def _load_agents_from_disk() -> List[AgentSpec]:
    path = _config_path()
    data = _load_file_raw(path)

    if not isinstance(data, dict):
        raise ValueError(f"Agents config root must be an object: {path}")

    raw_agents = data.get("agents")
    if not isinstance(raw_agents, list):
        raise ValueError(f"Agents config must contain a list under 'agents': {path}")

    specs: List[AgentSpec] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_agents):
        if not isinstance(item, dict):
            raise ValueError(f"Agent entry at index {idx} must be an object, got {type(item).__name__}")
        spec = _validate_agent_dict(item)
        if spec.id in seen:
            raise ValueError(f"Duplicate agent id '{spec.id}' in agents config")
        seen.add(spec.id)
        specs.append(spec)

    return specs


def _maybe_reload() -> List[AgentSpec]:
    path = _config_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        # Trigger downstream error on actual load
        mtime = None

    cached_mtime = _REGISTRY_CACHE.get("mtime")
    agents = _REGISTRY_CACHE.get("agents")

    if agents is not None and mtime == cached_mtime:
        return agents  # type: ignore[return-value]

    specs = _load_agents_from_disk()
    _REGISTRY_CACHE["mtime"] = mtime
    _REGISTRY_CACHE["agents"] = specs
    return specs


# -------------------- Public API --------------------

def list_agents() -> List[AgentSpec]:
    """Return the list of available AgentSpec objects (validated)."""
    return list(_maybe_reload())


def get_agent(agent_id: str) -> Optional[AgentSpec]:
    """Return a specific agent by id or None if not found."""
    if not agent_id:
        return None
    for spec in _maybe_reload():
        if spec.id == agent_id:
            return spec
    return None


def add_agent(spec: AgentSpec) -> None:
    """Persist a new agent spec to agents.json."""
    path = _config_path()
    try:
        data = _load_file_raw(path)
    except FileNotFoundError:
        data = {"agents": []}

    if not isinstance(data, dict) or "agents" not in data:
        data = {"agents": []}

    # Check for duplicates (update if exists)
    found = False
    for i, a in enumerate(data["agents"]):
        if a.get("id") == spec.id:
            data["agents"][i] = spec.to_dict()
            found = True
            break

    if not found:
        data["agents"].append(spec.to_dict())

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Force reload on next access
    _REGISTRY_CACHE["mtime"] = None


def remove_agent(agent_id: str) -> bool:
    """Remove an agent from agents.json by id. Returns True if found and removed."""
    path = _config_path()
    try:
        data = _load_file_raw(path)
    except FileNotFoundError:
        return False

    if not isinstance(data, dict) or "agents" not in data:
        return False

    original_len = len(data["agents"])
    data["agents"] = [a for a in data["agents"] if a.get("id") != agent_id]

    if len(data["agents"]) == original_len:
        return False  # not found

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Force reload on next access
    _REGISTRY_CACHE["mtime"] = None
    return True


def set_default_chat_agent(agent_id: str) -> None:
    """Mark agent_id as the default chat agent, clearing the flag from all others."""
    path = _config_path()
    try:
        data = _load_file_raw(path)
    except FileNotFoundError:
        raise ValueError("agents.json not found")

    if not isinstance(data, dict) or "agents" not in data:
        raise ValueError("Invalid agents.json")

    found = False
    for a in data["agents"]:
        if a.get("id") == agent_id:
            a["is_default_chat_agent"] = True
            found = True
        else:
            a.pop("is_default_chat_agent", None)

    if not found:
        raise ValueError(f"Agent '{agent_id}' not found")

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _REGISTRY_CACHE["mtime"] = None


def clear_default_chat_agent() -> None:
    """Clear the default chat agent flag from all agents."""
    path = _config_path()
    try:
        data = _load_file_raw(path)
    except FileNotFoundError:
        return

    if not isinstance(data, dict) or "agents" not in data:
        return

    for a in data["agents"]:
        a.pop("is_default_chat_agent", None)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _REGISTRY_CACHE["mtime"] = None
