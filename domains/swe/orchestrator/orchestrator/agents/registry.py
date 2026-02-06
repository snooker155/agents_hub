"""
Agent registry loader.

Loads and validates available agents from `agents.json` located next to this
module and exposes a small API:
- list_agents() -> list[AgentSpec]
- get_agent(agent_id: str) -> AgentSpec | None

Validation rules:
- JSON must contain object with key "agents": [ ... ]
- Each agent must provide: id, name, type, entrypoint
- default_params is a dict (defaults to {})
- capabilities is a list of strings (defaults to [])
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


# -------------------- Data models --------------------

@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    type: str
    entrypoint: str
    default_params: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    capacity: int = 1
    is_remote: bool = False
    agent_url: Optional[str] = None
    original_id: Optional[str] = None
    memory_type: str = "none"
    memory_data: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "entrypoint": self.entrypoint,
            "default_params": dict(self.default_params) if self.default_params else {},
            "capabilities": list(self.capabilities) if self.capabilities else [],
            "capacity": self.capacity,
            "is_remote": self.is_remote,
            "agent_url": self.agent_url,
            "original_id": self.original_id,
            "memory_type": self.memory_type,
            "memory_data": self.memory_data,
        }

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
    return Path(__file__).with_name("agents.json")


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
    default_params = ad.get("default_params")
    if default_params is None:
        default_params = {}
    if not isinstance(default_params, dict):
        raise ValueError(
            f"Agent default_params must be an object (dict): id={ad.get('id')}"
        )

    capabilities = ad.get("capabilities")
    if capabilities is None:
        capabilities = []
    if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities):
        raise ValueError(
            f"Agent capabilities must be a list of strings: id={ad.get('id')}"
        )

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

    # Validate entrypoint shape early (if not remote)
    if not is_remote:
        _split_entrypoint(ad["entrypoint"])  # raises if malformed

    return AgentSpec(
        id=ad["id"].strip(),
        name=ad["name"].strip(),
        type=ad["type"].strip(),
        entrypoint=ad["entrypoint"].strip(),
        default_params=default_params,
        capabilities=capabilities,
        capacity=capacity,
        is_remote=is_remote,
        agent_url=agent_url,
        original_id=original_id,
        memory_type=memory_type,
        memory_data=memory_data,
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
    """Remove an agent by id from agents.json. Returns True if found."""
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
        return False

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Force reload on next access
    _REGISTRY_CACHE["mtime"] = None
    return True


__all__ = ["AgentSpec", "list_agents", "get_agent", "add_agent", "remove_agent"]
