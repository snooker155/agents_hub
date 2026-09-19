"""
Environments — the deterministic worlds agents act in.

An environment is a plain Python object, never an LLM. It validates, applies and
returns state. Adding a third environment must touch **zero frontend code**: an
environment declares its own parameter schema (mirroring ``ToolSpec.parameters``)
and names the renderer it wants, and the UI renders both generically.
"""
from typing import Any, Dict, List, Optional, Type

from playground.environments.base import Environment
from playground.worlds import is_custom_env, world_id_of

_REGISTRY: Dict[str, Type[Environment]] = {}


def register(cls: Type[Environment]) -> Type[Environment]:
    _REGISTRY[cls.env_id] = cls
    return cls


def create_environment(env_id: str, params: Optional[Dict[str, Any]] = None,
                       seed: int = 42) -> Optional[Environment]:
    """The environment a scenario names, or ``None`` if there is no such world.

    Two kinds resolve here. A shipped environment is a registered class. A
    ``custom:<world_id>`` is a world somebody authored: its spec is loaded and
    handed to the one interpreter, so the runner never learns the difference —
    it asks for an environment and gets an ``Environment``.
    """
    if is_custom_env(env_id):
        from playground import store
        from playground.environments.custom import CustomEnvironment
        spec = store.get_world(world_id_of(env_id))
        return CustomEnvironment(params or {}, seed=seed, spec=spec) if spec else None
    cls = _REGISTRY.get(env_id)
    return cls(params or {}, seed=seed) if cls else None


def get_environment_class(env_id: str) -> Optional[Type[Environment]]:
    return _REGISTRY.get(env_id)


def list_environments(workspace: Optional[str] = None,
                      include_custom: bool = True) -> List[Dict[str, Any]]:
    """Catalog for the setup UI: id, name, params, actions, renderer.

    Authored worlds are listed alongside the shipped ones, in the same shape,
    because "which worlds can I run a scenario in" has one answer and the
    picker should not be two lists. They are marked ``custom`` for the one
    reader that cares — the page that offers to edit them.

    A database that is not there yet (a unit test constructing environments,
    a first boot) must not take the catalogue down with it: the shipped
    environments are still the answer to most of the question.
    """
    envs = [cls.describe() for cls in _REGISTRY.values()]
    if include_custom:
        try:
            from playground import store
            from playground.environments.custom import describe_world
            envs += [describe_world(spec) for spec in store.list_worlds(workspace)]
        except Exception:
            pass
    return envs


# Importing the modules registers them. ``custom`` is deliberately not among
# them: it is not a world you can pick, it is the interpreter every authored
# world is run by, and it reaches the catalogue once per world that exists.
from playground.environments import market, social  # noqa: E402,F401

__all__ = [
    "Environment", "register", "create_environment", "get_environment_class",
    "list_environments",
]
