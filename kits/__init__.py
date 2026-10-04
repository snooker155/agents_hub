"""Industry agent kits: ready sets of agents for one line of work.

Modelled on Anthropic's Claude for Financial Services agent templates: a kit
names a few agents with careful prompts, the connectors they need and an
outcome rubric for each, so a workspace gets a working team in one step
instead of writing every agent by hand. Three ship with the hub:
``kits/support`` (customer support), ``kits/finance`` (finance operations and
analyst work) and ``kits/recruiting``.

A kit IS an ``ah apply`` bundle (``declarative/``, docs/apply.md) plus a
manifest, ``kit.yaml``: ``id`` (must match the folder name), ``name``,
``description``, ``industry``, ``icon`` (one plain word), ``version``,
``connectors: {required: [...], optional: [...]}`` naming connectors from
``GET /api/connectors`` and channels from ``GET /api/channels`` (mail, slack,
...), a ``rubrics`` summary and ``next_steps`` text shown after install.
Installing a kit is applying its bundle
(``dashboard/backend/routes/kits.py``, ``cli/commands/kit.py``); see
docs/kits.md.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from declarative import Bundle, load_bundle
from declarative.errors import ValidationError

log = logging.getLogger(__name__)

#: kits/<id>/ folders live next to this file.
KITS_ROOT = Path(__file__).resolve().parent

#: ``kit.yaml`` fields every kit must set.
REQUIRED_MANIFEST_FIELDS = ("id", "name", "description", "industry")

#: An agent's reference fields the install path rewrites along with its id.
_AGENT_REF_FIELDS = ("handoffs", "delegates")


class KitError(ValueError):
    """A kit manifest that cannot be read, or an unknown kit id."""


@dataclass(frozen=True)
class Kit:
    """One kit's manifest. ``path`` is the folder its bundle files live in."""
    id: str
    name: str
    description: str
    industry: str
    icon: str = "sparkles"
    version: str = "1.0.0"
    connectors_required: List[str] = field(default_factory=list)
    connectors_optional: List[str] = field(default_factory=list)
    rubrics: str = ""
    next_steps: str = ""
    path: Path = field(default=None, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "industry": self.industry,
            "icon": self.icon,
            "version": self.version,
            "connectors": {"required": list(self.connectors_required),
                           "optional": list(self.connectors_optional)},
            "rubrics": self.rubrics,
            "next_steps": self.next_steps,
        }


def _read_manifest(kit_dir: Path) -> Kit:
    manifest_path = kit_dir / "kit.yaml"
    if not manifest_path.exists():
        raise KitError(f"{kit_dir}: no kit.yaml")
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise KitError(f"{manifest_path}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise KitError(f"{manifest_path}: must be a mapping of fields")
    missing = [f for f in REQUIRED_MANIFEST_FIELDS if not str(data.get(f) or "").strip()]
    if missing:
        raise KitError(f"{manifest_path}: missing {', '.join(missing)}")
    kit_id = str(data["id"]).strip()
    if kit_id != kit_dir.name:
        raise KitError(f"{manifest_path}: id '{kit_id}' must match its folder name '{kit_dir.name}'")
    connectors = data.get("connectors") or {}
    if not isinstance(connectors, dict):
        raise KitError(f"{manifest_path}: connectors must be a mapping of required/optional")
    return Kit(
        id=kit_id,
        name=str(data["name"]).strip(),
        description=str(data["description"]).strip(),
        industry=str(data["industry"]).strip(),
        icon=str(data.get("icon") or "sparkles").strip() or "sparkles",
        version=str(data.get("version") or "1.0.0").strip() or "1.0.0",
        connectors_required=[str(c).strip() for c in (connectors.get("required") or []) if str(c).strip()],
        connectors_optional=[str(c).strip() for c in (connectors.get("optional") or []) if str(c).strip()],
        rubrics=str(data.get("rubrics") or "").strip(),
        next_steps=str(data.get("next_steps") or "").strip(),
        path=kit_dir,
    )


def list_kits() -> List[Kit]:
    """Every kit under ``kits/``, sorted by id. A folder with no ``kit.yaml``
    is not a kit and is skipped (so a helper folder never breaks the list)."""
    if not KITS_ROOT.exists():
        return []
    out: List[Kit] = []
    for child in sorted(KITS_ROOT.iterdir()):
        if child.is_dir() and (child / "kit.yaml").exists():
            out.append(_read_manifest(child))
    return out


def get_kit(kit_id: str) -> Optional[Kit]:
    """One kit by id, or None when it does not exist."""
    kit_id = (kit_id or "").strip()
    if not kit_id or "/" in kit_id or ".." in kit_id:
        return None
    kit_dir = KITS_ROOT / kit_id
    if not kit_dir.is_dir() or not (kit_dir / "kit.yaml").exists():
        return None
    return _read_manifest(kit_dir)


def load_kit_bundle(kit: Kit) -> Bundle:
    """The kit's agents, memory pool and deployment files as an apply
    :class:`~declarative.Bundle` (docs/apply.md). Raises
    :class:`declarative.errors.ValidationError` when a file is malformed."""
    bundle = load_bundle([str(kit.path)])
    _ensure_parent_agents(bundle)
    return bundle


def _ensure_parent_agents(bundle: Bundle) -> None:
    """Seed any system agent a kit agent ``extends:`` (declarative/kinds.py,
    docs/agent-inheritance.md) that this hub has not needed yet, the same
    on-demand way any other surface short of one system agent does
    (``common.bootstrap.ensure_system_agent``) — so installing a kit never
    depends on having opened something else first that happened to seed its
    parent. Additive and never raising: a kit extending an id that is not a
    shippable system agent is simply left as is, for the plan to report
    against the hub the normal way."""
    kit_ids = {r.key for r in bundle.by_kind("agent")}
    parents = set()
    for res in bundle.by_kind("agent"):
        extends = res.spec.get("extends")
        if isinstance(extends, str) and extends.strip():
            parent = extends.split("@", 1)[0].strip()
            if parent and parent not in kit_ids:
                parents.add(parent)
    if not parents:
        return
    try:
        from common.bootstrap import ensure_system_agent
    except Exception:  # noqa: BLE001 - a hub without this helper leaves it to the plan
        return
    for parent in parents:
        try:
            ensure_system_agent(parent)
        except Exception:  # noqa: BLE001 - best effort; a real problem surfaces on the plan instead
            log.debug("kits: could not seed parent agent %r on demand", parent, exc_info=True)


def namespaced_bundle(bundle: Bundle, workspace: Optional[str]) -> Bundle:
    """A copy of ``bundle`` with every agent id suffixed ``@<workspace>``.

    An agent's declared id is also its hub id (docs/apply.md): global across
    the whole hub, unlike an environment's or a memory pool's, which the
    apply engine already scopes by the ``workspace:`` field it writes on
    create. Installing the same kit into two workspaces would otherwise try
    to create the same agent id twice. A deployment's ``agent:`` reference
    and an agent's own ``handoffs``/``delegates`` are rewritten to match, so
    the kit's agents still find each other after the rename. Installing into
    the default workspace (or no workspace) keeps the kit's own ids, so a
    single-workspace hub reads exactly as the kit declares it.
    """
    ws = (workspace or "").strip()
    if not ws or ws == "default":
        return bundle
    agent_keys = {r.key for r in bundle.by_kind("agent")}

    def rename(value: Any) -> Any:
        return f"{value}@{ws}" if value in agent_keys else value

    out = Bundle(root=bundle.root, files=list(bundle.files))
    for res in bundle.resources:
        spec = dict(res.spec)
        key = res.key
        if res.kind == "agent":
            for name in _AGENT_REF_FIELDS:
                if isinstance(spec.get(name), list):
                    spec[name] = [rename(v) for v in spec[name]]
            key = rename(res.key)
        elif res.kind == "deployment" and spec.get("agent"):
            spec["agent"] = rename(spec["agent"])
        out.resources.append(dataclasses.replace(res, key=key, spec=spec))
    return out


def lock_path(kit_id: str, workspace: Optional[str]) -> Path:
    """Where a workspace's install of this kit keeps its lock
    (docs/apply.md, "The lock file"), so reinstalling updates instead of
    duplicating. One file per (kit, workspace) under the state directory."""
    from common.paths import AGENTS_HUB_ROOT
    ws = (workspace or "default").strip() or "default"
    folder = AGENTS_HUB_ROOT / "kits" / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{kit_id}__{ws}.json"


def known_tool_ids() -> set:
    from tools.registry import get_all_tools
    return {t.id for t in get_all_tools()}


def known_connector_names() -> set:
    from connectors import credentials
    from connectors.channels import registry as channels
    return ({s.name for s in credentials.all_specs()} | {s.name for s in channels.all_channels()})


def connector_status(name: str, workspace: Optional[str] = None) -> Optional[bool]:
    """Whether a connector or channel named in a manifest is configured, or
    None when the name is not a connector or a channel at all.

    ``databases`` has no credentials of its own (its generic config always
    reads as configured): for a kit it counts as configured once the
    workspace (any workspace when None) has at least one connection."""
    if name == "databases":
        try:
            from connectors.databases import store as db_store
            return bool(db_store.list_connections(workspace))
        except Exception:  # noqa: BLE001 - the probe must not break the kit listing
            log.debug("kits: could not list database connections", exc_info=True)
            return False
    # The connector in effect in ``workspace`` (connectors/channels/store.py):
    # its own definition, else the default workspace's.
    from connectors.channels.store import in_workspace
    try:
        from connectors import credentials
        spec = credentials.get(name)
        if spec is not None:
            return bool(in_workspace(workspace, spec.is_configured))
    except Exception:  # noqa: BLE001, S110 - a connector's own probe must not break the kit listing
        log.debug("kits: could not read connector status for %r", name, exc_info=True)
    try:
        from connectors.channels import registry as channels
        spec = channels.get(name)
        if spec is not None:
            return bool(in_workspace(workspace, spec.store.is_configured,
                                     *spec.service.required_fields))
    except Exception:  # noqa: BLE001, S110 - a channel's own probe must not break the kit listing
        log.debug("kits: could not read channel status for %r", name, exc_info=True)
    return None


def validate_kit(kit: Kit) -> List[str]:
    """Problems with a kit beyond what ``load_bundle`` already checks on its
    own: an agent naming a tool the hub does not have, or a manifest naming
    a connector or channel that does not exist. Empty means the kit is
    sound."""
    problems: List[str] = []
    try:
        bundle = load_kit_bundle(kit)
    except ValidationError as exc:
        return [str(p) for p in exc.problems]
    tool_ids = known_tool_ids()
    for res in bundle.by_kind("agent"):
        for tool in res.spec.get("tools") or []:
            if tool not in tool_ids:
                problems.append(f"{kit.id}: agent '{res.key}' names unknown tool '{tool}'")
    names = known_connector_names()
    for name in list(kit.connectors_required) + list(kit.connectors_optional):
        if name not in names:
            problems.append(f"{kit.id}: names unknown connector or channel '{name}'")
    return problems


__all__ = [
    "Kit", "KitError", "KITS_ROOT", "list_kits", "get_kit", "load_kit_bundle",
    "namespaced_bundle", "lock_path", "known_tool_ids", "known_connector_names",
    "connector_status", "validate_kit",
]
