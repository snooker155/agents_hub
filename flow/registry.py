"""
Flow entity registry.

A *flow entity* is any typed unit that can become a node in a flow graph:
agents, processors, conditions, transforms, and any future category. This
registry mirrors ``agents/registry.py`` but federates three sources into one
catalog:

1. **Builtin (code-defined)** — files dropped under
   ``flow/entities/<category>/<name>.py`` that expose a module-level
   ``SPEC = FlowEntitySpec(...)`` plus a ``run(state, config, ctx)`` function.
   A developer adds a new entity simply by adding a file; the registry
   discovers it by scanning the folders. The subfolder name is the default
   ``category``.

2. **Agents (federated)** — every agent in ``agents.json`` is projected into a
   ``FlowEntitySpec`` with ``category="agent"``. Agents are NOT duplicated here;
   they remain owned by the agent registry. Creating an agent (via UI or the
   ``agent_creator`` agent) therefore surfaces it in the flow registry for free.

3. **User (data-defined)** — optional records in the ``flow_entities``
   document collection (:class:`common.docstore.DocStore`, one row per entity,
   keyed by id; same shape as ``agents.json``), for entities created through
   the UI or by an agent at runtime. An existing ``flow_entities.json`` is
   imported once, on first use.

Public API:
- list_entities() -> list[FlowEntitySpec]
- get_entity(entity_id) -> FlowEntitySpec | None
- list_groups() -> dict[category, list[FlowEntitySpec]]
- add_user_entity(spec) / remove_user_entity(entity_id)

The uniform node contract every entity callable honours is::

    run(state: FlowState, config: dict, ctx: RunContext) -> NodeResult

Agent entities adapt to the existing ``agent_factory.create_agent(...).run(...)``
path. Builtin entities run inline. Note: Phase 1 only builds the *catalog*;
non-agent node *execution* (dispatch via ``load_callable``) lands in a later
phase, but the resolution machinery is already provided here.
"""
from __future__ import annotations

import json
import pkgutil
from dataclasses import dataclass, field, replace
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from common import db
from common.docstore import DocStore
from common.paths import FLOW_ENTITIES_FILE

# Folder holding code-defined entities, scanned for subfolders (categories).
ENTITIES_DIR = Path(__file__).resolve().parent / "entities"
# Dotted package path used to import discovered modules.
ENTITIES_PKG = "flow.entities"

# User/agent-created entities, one row per entity keyed by id. FLOW_ENTITIES_FILE
# (a dict wrapper: {"entities": [...]}) is not shaped for DocStore's automatic
# import, so it is imported by hand — see _ensure_legacy_entities_imported.
_ENTITIES_STORE = DocStore("flow_entities")


# -------------------- Data model --------------------

@dataclass(frozen=True)
class FlowEntitySpec:
    id: str
    name: str
    category: str            # "agent" | "processor" | "condition" | "transform" | ...
    entrypoint: str          # "module.path:attribute" — resolved by load_callable()
    description: str = ""
    group: str = ""          # finer UI grouping within a category
    inputs: List[str] = field(default_factory=list)   # declared state-key contract
    outputs: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)  # params shown in inspector
    icon: str = ""
    # Provenance: where this spec came from. Drives editability in the UI
    # (only "user" entities can be removed via the API).
    source: str = "builtin"  # "builtin" | "agent" | "user"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "entrypoint": self.entrypoint,
            "description": self.description,
            "group": self.group,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "config_schema": dict(self.config_schema),
            "icon": self.icon,
            "source": self.source,
        }

    def load_callable(self) -> Callable[..., Any]:
        """Resolve ``entrypoint`` ("module:attr") to a Python callable."""
        mod_name, attr_name = _split_entrypoint(self.entrypoint)
        try:
            mod = import_module(mod_name)
        except Exception as e:  # pragma: no cover - import errors are environment-specific
            raise ValueError(
                f"Failed to import module '{mod_name}' for entity '{self.id}': {e}"
            ) from e
        try:
            fn = getattr(mod, attr_name)
        except Exception as e:
            raise ValueError(
                f"Module '{mod_name}' has no attribute '{attr_name}' for entity '{self.id}'"
            ) from e
        if not callable(fn):
            raise ValueError(f"Entrypoint '{self.entrypoint}' for entity '{self.id}' is not callable")
        return fn


# -------------------- Internals --------------------

def _split_entrypoint(entrypoint: str) -> tuple[str, str]:
    if ":" not in entrypoint:
        raise ValueError(
            f"Invalid entrypoint format '{entrypoint}'. Expected 'module.path:attribute'"
        )
    mod, attr = entrypoint.split(":", 1)
    mod, attr = mod.strip(), attr.strip()
    if not mod or not attr:
        raise ValueError(
            f"Invalid entrypoint format '{entrypoint}'. Expected 'module.path:attribute'"
        )
    return mod, attr


def _spec_from_dict(d: Dict[str, Any], *, source: str) -> FlowEntitySpec:
    for k in ("id", "name", "category", "entrypoint"):
        v = d.get(k)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Flow entity missing required field '{k}': {d}")
    _split_entrypoint(d["entrypoint"])  # validate shape early
    inputs = d.get("inputs") or []
    outputs = d.get("outputs") or []
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError(f"Flow entity inputs/outputs must be lists: id={d.get('id')}")
    cfg = d.get("config_schema") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return FlowEntitySpec(
        id=d["id"].strip(),
        name=d["name"].strip(),
        category=d["category"].strip(),
        entrypoint=d["entrypoint"].strip(),
        description=d.get("description", "") or "",
        group=d.get("group", "") or "",
        inputs=[str(x) for x in inputs],
        outputs=[str(x) for x in outputs],
        config_schema=cfg,
        icon=d.get("icon", "") or "",
        source=source,
    )


# Cache keyed on the inputs that can change at runtime: builtin-dir mtimes,
# the user store's signature, and the agents registry signature (see
# _maybe_reload).
_CACHE: Dict[str, Any] = {"key": None, "entities": None}


def _builtin_dir_signature() -> tuple:
    if not ENTITIES_DIR.exists():
        return ()
    sig: List[tuple] = []
    for p in sorted(ENTITIES_DIR.rglob("*.py")):
        try:
            sig.append((str(p), p.stat().st_mtime))
        except OSError:
            continue
    return tuple(sig)


def _discover_builtin() -> List[FlowEntitySpec]:
    """Import every module under flow_entities/<category>/ and read its SPEC.

    A broken entity file is skipped (not fatal) so one bad drop-in cannot take
    down the whole registry.
    """
    specs: List[FlowEntitySpec] = []
    if not ENTITIES_DIR.exists():
        return specs

    for category_dir in sorted(ENTITIES_DIR.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("_"):
            continue
        category = category_dir.name
        for mod_info in pkgutil.iter_modules([str(category_dir)]):
            if mod_info.name.startswith("_"):
                continue
            dotted = f"{ENTITIES_PKG}.{category_dir.name}.{mod_info.name}"
            try:
                mod = import_module(dotted)
            except Exception as e:  # noqa: BLE001 - skip bad entity, keep registry alive
                print(f"[flow_registry] skip {dotted}: import failed: {e}")
                continue
            spec = getattr(mod, "SPEC", None)
            if spec is None:
                continue
            try:
                if isinstance(spec, FlowEntitySpec):
                    # Default category to the folder name when unset.
                    norm = spec if spec.category else replace(spec, category=category)
                    specs.append(replace(norm, source="builtin"))
                elif isinstance(spec, dict):
                    spec.setdefault("category", category)
                    specs.append(_spec_from_dict(spec, source="builtin"))
                else:
                    print(f"[flow_registry] skip {dotted}: SPEC is not FlowEntitySpec/dict")
            except Exception as e:  # noqa: BLE001
                print(f"[flow_registry] skip {dotted}: invalid SPEC: {e}")
    return specs


def _agents_as_entities() -> List[FlowEntitySpec]:
    """Project every registered agent into a flow entity (category='agent')."""
    from agents import registry as agent_registry

    out: List[FlowEntitySpec] = []
    try:
        agents = agent_registry.list_agents()
    except Exception as e:  # noqa: BLE001 - never let a bad agents.json break the catalog
        print(f"[flow_registry] could not load agents: {e}")
        return out
    for a in agents:
        out.append(
            FlowEntitySpec(
                id=a.id,
                name=a.name,
                category="agent",
                entrypoint=a.entrypoint,
                description=a.description or "",
                group=a.domain or "general",
                inputs=[],
                outputs=[],
                config_schema={},
                source="agent",
            )
        )
    return out


def _agents_signature() -> tuple:
    try:
        from agents import registry as agent_registry
        return tuple(sorted(a.id for a in agent_registry.list_agents()))
    except Exception:
        return ()


_legacy_entities_imported_for: Optional[str] = None


def _ensure_legacy_entities_imported() -> None:
    """Import an existing flow_entities.json into the store, at most once per
    database (mirrors common.docstore.DocStore._ensure_imported's own marker:
    a fresh/rewritten database makes this run again).

    FLOW_ENTITIES_FILE is a dict wrapper (``{"entities": [...]}``), so it is
    read and keyed by hand rather than left to DocStore's automatic
    ``legacy_file`` import, which only understands a plain list or dict file.
    """
    global _legacy_entities_imported_for
    db.get_conn()
    marker = f"{db._generation}:{db.dialect()}:{db.DB_FILE}:{db.database_url()}"
    if _legacy_entities_imported_for == marker:
        return
    _legacy_entities_imported_for = marker
    path = FLOW_ENTITIES_FILE
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[flow_registry] invalid {path.name}: {e}")
        return
    raw = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return
    docs: Dict[str, Any] = {}
    for item in raw:
        if isinstance(item, dict) and item.get("id"):
            docs[str(item["id"])] = item
    _ENTITIES_STORE.import_legacy(docs, source=path)


def _load_user_entities() -> List[FlowEntitySpec]:
    """Load data-defined entities from the 'flow_entities' store (optional)."""
    _ensure_legacy_entities_imported()
    specs: List[FlowEntitySpec] = []
    for item in _ENTITIES_STORE.values():
        if not isinstance(item, dict):
            continue
        try:
            specs.append(_spec_from_dict(item, source="user"))
        except Exception as e:  # noqa: BLE001
            print(f"[flow_registry] skip user entity: {e}")
    return specs


def _maybe_reload() -> List[FlowEntitySpec]:
    _ensure_legacy_entities_imported()
    # Cache keyed on the inputs that can change at runtime: builtin-dir mtimes,
    # the user store's signature, and the agents registry signature.
    key = (_builtin_dir_signature(), _ENTITIES_STORE.signature(), _agents_signature())
    if _CACHE["entities"] is not None and _CACHE["key"] == key:
        return _CACHE["entities"]  # type: ignore[return-value]

    # Merge sources; on id collision, later sources win in this order:
    # builtin < agent < user (user records can intentionally override).
    by_id: Dict[str, FlowEntitySpec] = {}
    for spec in _discover_builtin():
        by_id[spec.id] = spec
    for spec in _agents_as_entities():
        by_id[spec.id] = spec
    for spec in _load_user_entities():
        by_id[spec.id] = spec

    entities = list(by_id.values())
    _CACHE["key"] = key
    _CACHE["entities"] = entities
    return entities


# -------------------- Public API --------------------

def list_entities() -> List[FlowEntitySpec]:
    """Return all flow entities from every source (builtin, agents, user)."""
    return list(_maybe_reload())


def get_entity(entity_id: str) -> Optional[FlowEntitySpec]:
    if not entity_id:
        return None
    for spec in _maybe_reload():
        if spec.id == entity_id:
            return spec
    return None


def _agent_visible_in_workspace(agent_id: str, workspace: Optional[str]) -> bool:
    """Whether an agent entity should appear in a workspace's flow palette.

    Mirrors the agents-list route (``dashboard/backend/routes/agents.py``) so the
    flow palette shows exactly the agents that belong to the workspace:

    1. **Ownership** — system + shared agents show everywhere; a workspace-owned,
       non-shared agent shows only in its owning workspace; unowned (legacy)
       agents are globally available.
    2. **Allowlist** — for a non-default workspace whose metadata declares
       ``allowed_agents``, only listed agents are visible (plus system agents and
       agents owned by this workspace).
    3. **default_workspace_only** — such agents are hidden from non-default
       workspaces.

    Unknown agents (no registry record) default to visible. Non-agent entities
    are not workspace-scoped and never reach this helper.
    """
    try:
        from agents.registry import get_agent as _get_agent
        from workspace import system_agent_ids as _system_ids
    except Exception:
        return True
    spec = _get_agent(agent_id)
    if spec is None:
        return True

    is_system = getattr(spec, "system", False) or agent_id in _system_ids()
    owner = getattr(spec, "owner_workspace", None)

    # 1. Ownership visibility.
    if not (is_system or getattr(spec, "shared", False)):
        if owner and owner != (workspace or "default"):
            return False

    ws = workspace or "default"
    if ws == "default":
        return True

    # 2. allowed_agents allowlist (system + workspace-owned always pass).
    try:
        from workspace import get_workspace_metadata
        allowed = (get_workspace_metadata(ws) or {}).get("allowed_agents")
    except Exception:
        allowed = None
    if allowed is not None:
        if not (agent_id in allowed or is_system or owner == ws):
            return False

    # 3. Hide default-workspace-only agents from non-default workspaces.
    if getattr(spec, "default_workspace_only", False) and not is_system:
        return False

    return True


def list_groups(workspace: Optional[str] = None) -> Dict[str, List[FlowEntitySpec]]:
    """Return entities grouped by category, for the Registry menu UI.

    When ``workspace`` is given, the ``agent`` category is scoped to that
    workspace by ownership (see ``_agent_visible_in_workspace``); other
    categories are workspace-independent. This affects only the palette — flow
    execution resolves entities via list_entities/get_entity, which stay
    unfiltered so a flow can always run its own workspace's agents.
    """
    groups: Dict[str, List[FlowEntitySpec]] = {}
    for spec in _maybe_reload():
        if (
            workspace is not None
            and spec.category == "agent"
            and not _agent_visible_in_workspace(spec.id, workspace)
        ):
            continue
        groups.setdefault(spec.category, []).append(spec)
    for specs in groups.values():
        specs.sort(key=lambda s: (s.group, s.name.lower()))
    return groups


def add_user_entity(spec: FlowEntitySpec) -> None:
    """Persist a user/agent-created entity to the 'flow_entities' store (upsert by id)."""
    _ensure_legacy_entities_imported()
    record = replace(spec, source="user").to_dict()
    _ENTITIES_STORE.put(spec.id, record)
    _CACHE["key"] = None  # force reload


def remove_user_entity(entity_id: str) -> bool:
    """Remove a user-defined entity by id. Returns True if removed."""
    _ensure_legacy_entities_imported()
    removed = _ENTITIES_STORE.delete(entity_id)
    if removed:
        _CACHE["key"] = None  # force reload
    return removed
