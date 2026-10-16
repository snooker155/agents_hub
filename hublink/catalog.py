"""
Catalog only: the linked hub as a library.

A person runs this hub with their own model keys and uses the hub they are
linked to (hublink/link.py) only to browse its agents, skills, flows, teams
and loops and to install copies into a local workspace. Nothing else flows:
no models, no data, no mirror. The remote end is the marketplace routes of
the same codebase (routes/marketplace.py).

Why copies carry provenance: an installed item remembers where it came from
(``hub_catalog_installs``: remote url and id, the hash of the remote
definition it was made from, the hash of what was written here), so the
Updates tab can say "the remote changed this" and show a diff before
anything is overwritten.

Why definitions are reduced to a *portable form* per kind: it is what is
compared, diffed and written. Model choices (provider, model, grader) are
dropped on purpose, since an installed copy runs on the models of this hub,
and secrets never appear in a definition at all (they are names in the
remote's tools, never values). Ids that point at other items (the agents of a
flow, the flow of a loop) are remapped to the local ids of their copies,
because a dependency whose id is taken here by something else is installed
under a free id.

Installs go through the same save paths as the UI (the agent create route
with its capability guard, the skill create route with the safety review,
``flow_store.save_flow`` with its review gate), so a guard that refuses an
install here refuses it with the same message.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from common.docstore import DocStore
from hublink import store
from hublink.remote import Remote, RemoteError

log = logging.getLogger(__name__)

KINDS = ("agents", "skills", "flows", "teams", "loops")

#: Kinds the remote serves to an administrator's key only (teams and loops
#: carry no publishing flag there, routes/marketplace.py). The link's key is
#: hub-wide, so what it reads is the linking administrator's view; only this
#: hub's administrators get to browse or install these, and the same holds for
#: a flow the remote has not published (a loop's flow).
ADMIN_KINDS = ("teams", "loops")

ADMIN_ONLY = "Only an administrator can browse or install teams and loops from the linked hub."

_installs = DocStore("hub_catalog_installs")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: Keys of a team or loop definition that are this hub's own business.
_TEAM_DROP = ("team_id", "workspace", "created_at", "updated_at", "leader_display_name",
              "entry_display_name", "default_provider", "default_model")
_MEMBER_DROP = ("provider", "model")
_LOOP_DROP = ("loop_id", "workspace", "created_at", "updated_at", "evaluator_provider",
              "evaluator_model", "grader")


class CatalogError(Exception):
    """Something the person can act on. ``status`` is the HTTP status to use;
    ``detail`` may carry structure (a plan with conflicts)."""

    def __init__(self, message: str, *, status: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail if detail is not None else message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the link and the remote ──────────────────────────────────────────────────

def enabled() -> bool:
    doc = store.get()
    return bool(doc and doc.get("catalog"))


def set_enabled(value: bool) -> Dict[str, Any]:
    doc = store.update(catalog=bool(value))
    if doc is None:
        raise CatalogError("Link a hub first (Settings, Remote hub).", status=409)
    return {"enabled": bool(doc.get("catalog"))}


def _remote() -> Remote:
    doc = store.get()
    if not doc:
        raise CatalogError("No hub is linked. Link one in Settings, Remote hub.", status=409)
    if not doc.get("catalog"):
        raise CatalogError("Catalog mode is off. Turn it on in Settings, Remote hub.", status=409)
    return Remote(doc["url"], doc.get("api_key") or "")


def _remote_url() -> str:
    doc = store.get() or {}
    return str(doc.get("url") or "")


def _get(path: str) -> Any:
    try:
        return _remote().get_json(path)
    except RemoteError as exc:
        status = 404 if exc.status == 404 else 502
        raise CatalogError(f"The linked hub answered: {exc}", status=status) from exc


# ── portable forms ───────────────────────────────────────────────────────────

def digest(portable: Any) -> str:
    raw = json.dumps(portable, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def render_text(portable: Dict[str, Any]) -> str:
    """A readable text of a portable form, for the detail view and the diff:
    short values on one line each, long text and structures in sections."""
    head: List[str] = []
    sections: List[str] = []
    for key, value in portable.items():
        if value in ("", None, [], {}):
            continue
        if isinstance(value, str) and "\n" not in value and len(value) < 120:
            head.append(f"{key}: {value}")
        elif isinstance(value, (bool, int, float)) or value is None:
            head.append(f"{key}: {json.dumps(value)}")
        elif isinstance(value, str):
            sections.append(f"## {key}\n{value.rstrip()}\n")
        else:
            dumped = yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip()
            sections.append(f"## {key}\n{dumped}\n")
    return "\n".join(head) + ("\n\n" + "\n".join(sections) if sections else "\n")


def _known_tools() -> set:
    try:
        from tools.registry import get_all_tools
        return {t.id for t in get_all_tools()}
    except Exception:  # noqa: BLE001 - without the tool list nothing is dropped
        log.debug("catalog: tool list unavailable", exc_info=True)
        return set()


def _remote_agent_portable(raw: Dict[str, Any]) -> Dict[str, Any]:
    definition = raw.get("definition") or {}
    return {
        "name": raw.get("name") or raw.get("id"),
        "description": raw.get("description") or "",
        "domain": raw.get("domain") or "general",
        "tools": [t.get("id") for t in raw.get("tools") or [] if isinstance(t, dict) and t.get("id")],
        "commands": list(raw.get("commands") or []),
        "instructions": (definition.get("instructions") or "").strip(),
        "capabilities": (definition.get("capabilities") or "").strip(),
        "usage": (definition.get("usage") or "").strip(),
    }


def _local_agent_portable(agent_id: str) -> Optional[Dict[str, Any]]:
    from agents import prompt_assembly, registry
    from agents.agent_factory import get_factory

    spec = registry.get_agent(agent_id)
    if spec is None:
        return None
    defs = get_factory().definitions_dir
    def_id = spec.def_id()
    return {
        "name": spec.name,
        "description": spec.description or "",
        "domain": spec.domain or "general",
        "tools": list(spec.tools or []),
        "commands": list(spec.commands or []),
        "instructions": prompt_assembly.read_instructions(def_id, definitions_dir=defs).strip(),
        "capabilities": prompt_assembly.read_capabilities(def_id, definitions_dir=defs).strip(),
        "usage": prompt_assembly.read_usage(def_id, definitions_dir=defs).strip(),
    }


def _skill_portable(p: Any) -> Dict[str, Any]:
    get = (lambda k, d=None: p.get(k, d)) if isinstance(p, dict) else (lambda k, d=None: getattr(p, k, d))
    return {
        "name": get("name") or "",
        "description": get("description") or "",
        "steps": list(get("steps") or []),
        "body": (get("body") or "").strip(),
        "tags": list(get("tags") or []),
        "license": get("license") or "",
    }


def _strip_team(d: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in d.items() if k not in _TEAM_DROP}
    out["members"] = [{k: v for k, v in (m or {}).items() if k not in _MEMBER_DROP}
                      for m in d.get("members") or []]
    return out


#: Flow metadata that belongs to one hub's copy: timestamps, publishing and
#: review state, ownership, and the webhook secret (never carried anywhere).
_FLOW_DROP = ("id", "task_id", "created_at", "updated_at", "shared", "workspace", "review_status",
              "review_note", "reviewed_by", "reviewed_at", "owner_user", "origin_flow_id", "running",
              "webhook_secret")


def _strip_flow(d: Any) -> Dict[str, Any]:
    return {k: v for k, v in (d or {}).items() if k not in _FLOW_DROP} if isinstance(d, dict) else {}


def _strip_loop(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k not in _LOOP_DROP}


def _local_portable(kind: str, local_id: str) -> Optional[Dict[str, Any]]:
    if kind == "agents":
        return _local_agent_portable(local_id)
    if kind == "skills":
        from memory.procedural import find_procedure
        p = find_procedure(local_id)
        return _skill_portable(p) if p else None
    if kind == "flows":
        from flow import store as flow_store
        text = flow_store.export_flow_yaml(local_id)
        return _strip_flow(yaml.safe_load(text)) if text else None
    if kind == "teams":
        from teams import store as teams_store
        t = teams_store.get_team(local_id)
        return _strip_team(t.to_dict()) if t else None
    if kind == "loops":
        from loops import store as loops_store
        lp = loops_store.get_loop(local_id)
        return _strip_loop(lp.to_dict()) if lp else None
    return None


def _local_workspace(kind: str, local_id: str) -> Optional[str]:
    """The workspace a local item lives in (None when it has none)."""
    if kind == "skills":
        from memory.procedural import find_procedure
        p = find_procedure(local_id)
        return p.workspace if p else None
    if kind == "flows":
        from flow import store as flow_store
        f = flow_store.get_flow(local_id)
        return (f or {}).get("workspace")
    if kind == "teams":
        from teams import store as teams_store
        t = teams_store.get_team(local_id)
        return t.workspace if t else None
    if kind == "loops":
        from loops import store as loops_store
        lp = loops_store.get_loop(local_id)
        return lp.workspace if lp else None
    if kind == "agents":
        from agents import registry
        s = registry.get_agent(local_id)
        return s.owner_workspace if s else None
    return None


def _ws_name(workspace: Optional[str]) -> str:
    """A workspace as a name: no workspace at all is the default one (an item
    saved into default carries ``None``)."""
    return (workspace or "").strip() or "default"


def owner_workspace(kind: str, local_id: str, fallback: Optional[str] = None) -> str:
    """The workspace that owns a local copy now, which is what rights are
    checked against and what an update writes to; the provenance row's
    workspace only when the copy is gone."""
    if _local_exists(kind, local_id):
        return _ws_name(_local_workspace(kind, local_id))
    return _ws_name(fallback)


def _usable_in(spec: Any, workspace: str) -> bool:
    """Whether a local agent may be made runnable in this workspace: the rule
    of adding an agent to a workspace (routes/workspaces.py), so an install
    never reaches what that route refuses."""
    from workspace import is_system_agent
    if is_system_agent(spec.id):
        return True
    if getattr(spec, "default_workspace_only", False) and workspace != "default":
        return False
    owner = spec.owner_workspace
    return not owner or bool(spec.shared) or owner == workspace


def _local_exists(kind: str, local_id: str) -> bool:
    if kind == "agents":
        from agents import registry
        return registry.get_agent(local_id) is not None
    if kind == "flows":
        from flow import store as flow_store
        return flow_store.get_flow(local_id) is not None
    if kind == "teams":
        from teams import store as teams_store
        return teams_store.get_team(local_id) is not None
    if kind == "loops":
        from loops import store as loops_store
        return loops_store.get_loop(local_id) is not None
    if kind == "skills":
        from memory.procedural import find_procedure
        return find_procedure(local_id) is not None
    return False


# ── remote items ─────────────────────────────────────────────────────────────

def _ref(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": a.get("id"), "name": a.get("name") or a.get("id"), "system": bool(a.get("system")),
            "available": bool(a.get("on_marketplace")) or bool(a.get("system"))}


def _card(kind: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if kind == "agents":
        meta = {"domain": raw.get("domain"), "tools": raw.get("tools") or []}
    elif kind == "skills":
        meta = {"steps_count": raw.get("steps_count"), "tags": raw.get("tags") or []}
    elif kind == "flows":
        meta = {"nodes_count": raw.get("nodes_count")}
    elif kind == "teams":
        meta = {"members_count": raw.get("members_count"), "mode": raw.get("mode")}
    elif kind == "loops":
        meta = {"flow": raw.get("flow")}
    return {
        "kind": kind,
        "id": str(raw.get("id")),
        "name": raw.get("name") or str(raw.get("id")),
        "description": raw.get("description") or "",
        "author": raw.get("owner_workspace") or "",
        "updated_at": raw.get("updated_at"),
        "agents": [_ref(a) for a in raw.get("agents") or []],
        "meta": meta,
    }


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise CatalogError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.", status=404)


def _check_admin(kind: str, admin: bool) -> None:
    if kind in ADMIN_KINDS and not admin:
        raise CatalogError(ADMIN_ONLY, status=403)


def browse(kind: str, q: str = "", *, admin: bool = True) -> List[Dict[str, Any]]:
    """What the linked hub offers of one kind, as cards, each marked with the
    local copies already made from it."""
    _check_kind(kind)
    _check_admin(kind, admin)
    rows = _get(f"/api/marketplace/{kind}")
    if not isinstance(rows, list):
        rows = []
    needle = (q or "").strip().lower()
    url = _remote_url()
    installed: Dict[str, List[str]] = {}
    for rec in _installs.values():
        if isinstance(rec, dict) and rec.get("kind") == kind and rec.get("remote_url") == url:
            installed.setdefault(str(rec.get("remote_id")), []).append(rec.get("local_id"))
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        card = _card(kind, raw)
        if needle and needle not in f"{card['name']} {card['description']} {card['author']}".lower():
            continue
        card["installed_as"] = installed.get(card["id"], [])
        out.append(card)
    return out


def item(kind: str, remote_id: str, *, admin: bool = True) -> Dict[str, Any]:
    """One remote definition in full: the card, the portable form (what an
    install writes), its text for reading, its hash, and what it depends on.
    ``admin`` False keeps out what only this hub's administrators may read:
    teams, loops and flows the remote has not published."""
    _check_kind(kind)
    _check_admin(kind, admin)
    rid = str(remote_id)
    from urllib.parse import quote
    if kind == "skills":
        rows = _get("/api/marketplace/skills") or []
        raw = next((r for r in rows if isinstance(r, dict) and str(r.get("id")) == rid), None)
        if raw is None:
            raise CatalogError("Skill not found on the linked hub.", status=404)
        portable = _skill_portable(raw)
        extra: Dict[str, Any] = {"safety": raw.get("safety"), "resources": raw.get("resources") or []}
        agents: List[Dict[str, Any]] = []
    else:
        raw = _get(f"/api/marketplace/{kind}/{quote(rid, safe='')}")
        extra, agents = {}, [_ref(a) for a in raw.get("agents") or []]
        if kind == "agents":
            portable = _remote_agent_portable(raw)
            extra = {"skill_ids": [str(s.get("id")) for s in raw.get("skills") or [] if isinstance(s, dict)]}
        elif kind == "flows":
            if not admin and not raw.get("published"):
                raise CatalogError("Flow not found on the linked hub.", status=404)
            portable = _strip_flow(yaml.safe_load(raw.get("yaml") or ""))
        elif kind == "teams":
            portable = _strip_team(raw.get("definition") or {})
        else:  # loops
            portable = _strip_loop(raw.get("definition") or {})
            extra = {"flow": raw.get("flow")}
    card = _card(kind, {**raw, "id": rid})
    return {
        **card,
        "agents": agents or card["agents"],
        "portable": portable,
        "text": render_text(portable),
        "hash": digest(portable),
        **extra,
    }


# ── provenance ───────────────────────────────────────────────────────────────

def _key(kind: str, local_id: str) -> str:
    return f"{kind}:{local_id}"


def provenance(kind: str, local_id: str) -> Optional[Dict[str, Any]]:
    rec = _installs.get(_key(kind, local_id))
    return dict(rec) if isinstance(rec, dict) else None


def _origins(kind: str, remote_id: str) -> List[Dict[str, Any]]:
    """Every local copy still here made from this remote item, in any
    workspace."""
    url = _remote_url()
    out = []
    for rec in _installs.values():
        if not isinstance(rec, dict) or rec.get("kind") != kind:
            continue
        if rec.get("remote_url") != url or str(rec.get("remote_id")) != str(remote_id):
            continue
        if _local_exists(kind, rec["local_id"]):
            out.append(dict(rec))
    return out


def _find_origin(kind: str, remote_id: str, workspace: str) -> Optional[Dict[str, Any]]:
    """The local copy made from this remote item that the target workspace
    owns. Only that copy is updated by an install: agent ids are global, but
    a copy another workspace owns is that workspace's to change, and the
    person installing may have no rights there at all."""
    for rec in _origins(kind, remote_id):
        if owner_workspace(kind, rec["local_id"]) == workspace:
            return rec
    return None


def _record(kind: str, local_id: str, workspace: str, remote_id: str, remote_hash: str,
            deps: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    prev = provenance(kind, local_id) or {}
    local = _local_portable(kind, local_id)
    rec = {
        "kind": kind, "local_id": local_id, "workspace": workspace,
        "remote_url": _remote_url(), "remote_id": str(remote_id),
        "remote_hash": remote_hash, "local_hash": digest(local) if local is not None else "",
        "deps": deps, "installed_at": prev.get("installed_at") or _now(),
        "updated_at": _now(),
    }
    _installs.put(_key(kind, local_id), rec)
    return rec


def forget(kind: str, local_id: str) -> bool:
    return _installs.delete(_key(kind, local_id))


# ── plans ────────────────────────────────────────────────────────────────────

def _valid_id(value: str) -> str:
    value = (value or "").strip()
    if not _ID_RE.match(value):
        raise CatalogError("An id uses letters, digits, dots, dashes and underscores (up to 64).")
    return value


def _free_id(kind: str, base: str, taken: set) -> str:
    candidate = f"{base}-hub"
    n = 2
    while candidate in taken or _local_exists(kind, candidate):
        candidate = f"{base}-hub-{n}"
        n += 1
    return candidate


def _workspace_checked(workspace: str) -> str:
    from workspace import get_workspace_folder
    ws = (workspace or "").strip() or "default"
    if ws != "default" and get_workspace_folder(ws) is None:
        raise CatalogError(f"Workspace '{ws}' not found.", status=404)
    return ws


def _skill_name_taken(workspace: str, name: str, agent_id: str = "") -> bool:
    from memory.procedural import ProcedureStore
    low = name.strip().lower()
    return any(p.agent_id == agent_id and p.name.strip().lower() == low
               for p in ProcedureStore(workspace).load())


class _Plan:
    """Resolves one install into an ordered list of steps, dependencies first."""

    def __init__(self, workspace: str, root_new_id: Optional[str], admin: bool = True) -> None:
        self.workspace = workspace
        self.admin = admin
        self.root_new_id = (root_new_id or "").strip() or None
        self.steps: List[Dict[str, Any]] = []
        self.taken: set = set()
        self.seen: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _step(self, **kw: Any) -> Dict[str, Any]:
        step = {"local_id": None, "suggested_id": None, "reason": "", "tools_dropped": [],
                "root": False, **kw}
        self.steps.append(step)
        self.seen[(step["kind"], str(step["remote_id"]))] = step
        return step

    def resolve(self, kind: str, remote_id: str, *, root: bool = False,
                known: Optional[Dict[str, Any]] = None, agent_id: str = "") -> Dict[str, Any]:
        rid = str(remote_id)
        if (kind, rid) in self.seen:
            return self.seen[(kind, rid)]
        if kind == "agents":
            return self._agent(rid, root, known)
        if kind == "skills":
            return self._skill(rid, root, agent_id)
        return self._plain(kind, rid, root)

    # -- agents --

    def _agent(self, rid: str, root: bool, known: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        from agents import registry
        from workspace import is_system_agent
        ref = known or {}
        if not root and (ref.get("system") or is_system_agent(rid)):
            return self._step(kind="agents", remote_id=rid, name=ref.get("name") or rid,
                              action="system", local_id=rid,
                              reason="A system agent every hub has; it is used as it is.")
        origin = _find_origin("agents", rid, self.workspace)
        if origin:
            return self._step(kind="agents", remote_id=rid, name=ref.get("name") or rid,
                              action="update" if root else "exists", local_id=origin["local_id"],
                              reason="Already installed from this hub." + (
                                  " It will be updated." if root else ""), root=root)
        if not root:
            # A copy another workspace owns serves as a dependency only when
            # that workspace shared it (or it is global); it is never written.
            for rec in _origins("agents", rid):
                spec = registry.get_agent(rec["local_id"])
                if spec is not None and _usable_in(spec, self.workspace):
                    return self._step(kind="agents", remote_id=rid, name=ref.get("name") or rid,
                                      action="exists", local_id=rec["local_id"],
                                      reason="Already installed from this hub and shared here.")
        remote = item("agents", rid) if (root or ref.get("available", True)) else None
        name = (remote or {}).get("name") or ref.get("name") or rid
        if remote is None:
            same = registry.get_agent(rid)
            if same is not None and _usable_in(same, self.workspace):
                return self._step(kind="agents", remote_id=rid, name=name, action="exists",
                                  local_id=rid, reason="Not shared by the hub; the agent of the same id here is used.")
            if same is not None:
                return self._step(kind="agents", remote_id=rid, name=name, action="missing",
                                  reason=f"The hub does not share this agent, and the agent '{rid}' here belongs "
                                         f"to workspace '{same.owner_workspace}' and is not shared.")
            return self._step(kind="agents", remote_id=rid, name=name, action="missing",
                              reason="The hub does not share this agent and none with this id exists here.")
        dropped = [t for t in remote["portable"]["tools"] if _known_tools() and t not in _known_tools()]
        want = self.root_new_id if root and self.root_new_id else rid
        step_kw = dict(kind="agents", remote_id=rid, name=name, tools_dropped=dropped, root=root)
        if want != rid:
            want = _valid_id(want)
        if registry.get_agent(want) is not None or want in self.taken:
            suggested = _free_id("agents", rid, self.taken)
            if root and not self.root_new_id:
                return self._step(**step_kw, action="conflict", suggested_id=suggested,
                                  reason=f"An agent with the id '{rid}' already exists here. Pick another id.")
            if root:
                raise CatalogError(f"The id '{want}' is taken here too.", status=409)
            self.taken.add(suggested)
            step = self._step(**step_kw, action="rename", local_id=suggested,
                              reason=f"The id '{rid}' is taken here by another agent; installed as '{suggested}'.")
        else:
            self.taken.add(want)
            step = self._step(**step_kw, action="install", local_id=want)
        self._agent_skills(remote, step)
        return step

    def _agent_skills(self, remote: Dict[str, Any], agent_step: Dict[str, Any]) -> None:
        wanted = remote.get("skill_ids") or []
        if not wanted:
            return
        try:
            shared = {str(r.get("id")) for r in (_get("/api/marketplace/skills") or []) if isinstance(r, dict)}
        except CatalogError:
            return
        for sid in wanted:
            if sid in shared:
                self.resolve("skills", sid, agent_id=agent_step["local_id"])
                self.seen[("skills", sid)]["for_agent"] = agent_step["local_id"]

    # -- skills --

    def _skill(self, rid: str, root: bool, agent_id: str) -> Dict[str, Any]:
        remote = item("skills", rid)
        name = remote["name"]
        origin = _find_origin("skills", rid, self.workspace)
        kw = dict(kind="skills", remote_id=rid, name=name, root=root)
        if origin and root:
            return self._step(**kw, action="update", local_id=origin["local_id"],
                              reason="Already installed from this hub. It will be updated.")
        if origin:
            return self._step(**kw, action="exists", local_id=origin["local_id"],
                              reason="Already installed from this hub.")
        wanted = self.root_new_id if root and self.root_new_id else name
        if _skill_name_taken(self.workspace, wanted, agent_id if not root else ""):
            if root and not self.root_new_id:
                return self._step(**kw, action="conflict", suggested_id=f"{name} (hub)",
                                  reason=f"A skill named '{name}' already exists here. Pick another name.")
            if not root:
                return self._step(**kw, action="exists", local_id=None,
                                  reason=f"A skill named '{name}' already exists here; left alone.")
            raise CatalogError(f"A skill named '{wanted}' already exists here.", status=409)
        return self._step(**kw, action="install", new_name=wanted if wanted != name else None)

    # -- flows, teams, loops --

    def _plain(self, kind: str, rid: str, root: bool) -> Dict[str, Any]:
        remote = item(kind, rid, admin=self.admin)
        name = remote["name"]
        origin = _find_origin(kind, rid, self.workspace)
        kw = dict(kind=kind, remote_id=rid, name=name, root=root)
        if origin:
            step = self._step(**kw, action="update" if root else "exists", local_id=origin["local_id"],
                              reason="Already installed from this hub." + (" It will be updated." if root else ""))
            self._deps(kind, remote, root)
            return step
        want = self.root_new_id if root and self.root_new_id else rid
        if want != rid:
            want = _valid_id(want)
        if _local_exists(kind, want) or want in self.taken:
            suggested = _free_id(kind, rid, self.taken)
            if root and not self.root_new_id:
                step = self._step(**kw, action="conflict", suggested_id=suggested,
                                  reason=f"The id '{rid}' is taken here by another {kind[:-1]}. Pick another id.")
                self._deps(kind, remote, root)
                return step
            if root:
                raise CatalogError(f"The id '{want}' is taken here too.", status=409)
            self.taken.add(suggested)
            step = self._step(**kw, action="rename", local_id=suggested,
                              reason=f"The id '{rid}' is taken here; installed as '{suggested}'.")
        else:
            self.taken.add(want)
            step = self._step(**kw, action="install", local_id=want)
        self._deps(kind, remote, root)
        return step

    def _deps(self, kind: str, remote: Dict[str, Any], root: bool) -> None:
        """Dependencies of a flow, team or loop are resolved before the item
        itself is written; the step order puts them first."""
        mine = self.steps.pop()
        self.seen.pop((mine["kind"], str(mine["remote_id"])), None)
        if kind == "loops":
            flow = remote.get("flow") or {}
            if flow.get("id"):
                if not flow.get("on_marketplace"):
                    self._step(kind="flows", remote_id=flow["id"], name=flow.get("name") or flow["id"],
                               action="missing", reason="The hub does not serve this loop's flow.")
                else:
                    self.resolve("flows", flow["id"])
        for a in remote.get("agents") or []:
            if a.get("id"):
                self.resolve("agents", a["id"], known=a)
        self.steps.append(mine)
        self.seen[(mine["kind"], str(mine["remote_id"]))] = mine


def _build_plan(kind: str, remote_id: str, workspace: str, new_id: Optional[str],
                admin: bool = True) -> Tuple[_Plan, str]:
    ws = _workspace_checked(workspace)
    plan = _Plan(ws, new_id, admin)
    plan.resolve(kind, remote_id, root=True)
    return plan, ws


def plan_install(kind: str, remote_id: str, workspace: str, new_id: Optional[str] = None,
                 *, admin: bool = True) -> Dict[str, Any]:
    """What an install would do, nothing written: the steps in order (what is
    installed, updated, renamed, already there, or missing), the conflicts the
    person has to settle, and whether it can go ahead."""
    _check_kind(kind)
    _check_admin(kind, admin)
    plan, ws = _build_plan(kind, remote_id, workspace, new_id, admin)
    blocked = [s["reason"] and f"{s['name']}: {s['reason']}" for s in plan.steps if s["action"] == "missing"]
    conflicts = [s for s in plan.steps if s["action"] == "conflict"]
    root = next((s for s in plan.steps if s["root"]), plan.steps[-1])
    return {
        "kind": kind, "remote_id": str(remote_id), "workspace": ws, "name": root["name"],
        "steps": plan.steps, "blocked": blocked, "conflicts": conflicts,
        "can_install": not blocked and not conflicts,
    }


# ── writing ──────────────────────────────────────────────────────────────────

def _remap_strings(value: Any, mapping: Dict[str, str]) -> Any:
    """Replace ids in a definition. Flow YAML names its nodes by the agent id
    (``writer``, ``writer#2``), so the edges and the entry point carry the id
    too: a string equal to an id, or to ``id#N``, is replaced."""
    if not mapping:
        return value
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        head, sep, tail = value.partition("#")
        if sep and head in mapping and tail.isdigit():
            return f"{mapping[head]}#{tail}"
        return value
    if isinstance(value, list):
        return [_remap_strings(v, mapping) for v in value]
    if isinstance(value, dict):
        return {(_remap_strings(k, mapping) if isinstance(k, str) else k): _remap_strings(v, mapping)
                for k, v in value.items()}
    return value


def _idmap(steps: List[Dict[str, Any]], kind: str) -> Dict[str, str]:
    return {str(s["remote_id"]): s["local_id"] for s in steps
            if s["kind"] == kind and s.get("local_id") and s["local_id"] != str(s["remote_id"])}


def _for_local(kind: str, portable: Dict[str, Any], agents: Dict[str, str],
               flows: Dict[str, str]) -> Dict[str, Any]:
    """The remote's portable form as it is written here: agent and flow ids
    remapped, tools this hub does not have left out."""
    out = json.loads(json.dumps(portable))
    if kind == "agents":
        known = _known_tools()
        if known:
            out["tools"] = [t for t in out.get("tools") or [] if t in known]
    elif kind == "flows":
        out = _remap_strings(out, agents)
    elif kind == "teams":
        for m in out.get("members") or []:
            m["agent_id"] = agents.get(m.get("agent_id"), m.get("agent_id"))
        for f in ("leader_agent_id", "entry_agent_id"):
            if out.get(f):
                out[f] = agents.get(out[f], out[f])
    elif kind == "loops":
        if out.get("flow_id"):
            out["flow_id"] = flows.get(out["flow_id"], out["flow_id"])
        if out.get("evaluator_agent_id"):
            out["evaluator_agent_id"] = agents.get(out["evaluator_agent_id"], out["evaluator_agent_id"])
    return out


def _allow_agents(workspace: str, agent_ids: List[str]) -> None:
    """Make the agents of an installed flow, team or loop runnable in the
    workspace, the way adding a marketplace flow does."""
    from agents import registry
    from workspace import get_workspace_metadata, is_system_agent, update_workspace_metadata
    ids = []
    for a in agent_ids:
        if not a or is_system_agent(a):
            continue
        spec = registry.get_agent(a)
        # The plan already stops on such an agent; this keeps the rule here
        # too, so no path through this function widens another workspace's.
        if spec is not None and not _usable_in(spec, workspace):
            log.warning("catalog: agent %s is not shared with %s; not allowed there", a, workspace)
            continue
        ids.append(a)
    if not ids:
        return
    allowed = list(get_workspace_metadata(workspace).get("allowed_agents") or [])
    added = [a for a in ids if a not in allowed]
    if added:
        update_workspace_metadata(workspace, {"allowed_agents": allowed + added})


def _http(exc: Exception) -> CatalogError:
    """A route's HTTPException as a catalog error with the same words."""
    status = getattr(exc, "status_code", 400)
    detail = getattr(exc, "detail", str(exc))
    if isinstance(detail, dict):
        detail = detail.get("message") or json.dumps(detail, default=str)
    return CatalogError(str(detail), status=int(status))


async def _write_agent(local_id: str, ws: str, portable: Dict[str, Any], request: Any, *, create: bool) -> None:
    import dataclasses

    from agents import prompt_assembly, registry
    from agents.agent_factory import get_factory
    from fastapi import HTTPException
    from models import AgentCreateCustom
    from routes.agents.lifecycle import create_custom_agent

    defs = get_factory().definitions_dir
    commands = list(portable.get("commands") or [])
    if create:
        try:
            await create_custom_agent(AgentCreateCustom(
                id=local_id, name=portable.get("name") or local_id,
                description=portable.get("description") or "", domain=portable.get("domain") or "general",
                system_prompt=portable.get("instructions") or "(no instructions)",
                tools=list(portable.get("tools") or []),
                workspace=None if ws == "default" else ws), request)
        except HTTPException as exc:
            raise _http(exc) from exc
        except ValueError as exc:
            raise CatalogError(str(exc), status=409) from exc
        spec = registry.get_agent(local_id)
        changed = bool(commands)
    else:
        spec = registry.get_agent(local_id)
        if spec is None:
            raise CatalogError(f"Agent '{local_id}' is gone.", status=404)
        spec = dataclasses.replace(spec, name=portable.get("name") or spec.name,
                                   description=portable.get("description") or "",
                                   domain=portable.get("domain") or spec.domain,
                                   tools=list(portable.get("tools") or []))
        changed = True
    if spec is not None and changed:
        # Saved before the prompts are touched: the capability guard may refuse
        # the tool set, and a refusal must leave the copy as it was.
        try:
            registry.add_agent(dataclasses.replace(spec, commands=commands), note="hub catalog")
        except ValueError as exc:  # CapabilityViolation is a ValueError
            raise CatalogError(str(exc), status=409) from exc
    if not create:
        prompt_assembly.write_instructions(local_id, portable.get("instructions") or "(no instructions)",
                                           definitions_dir=defs)
    for fn, part in ((prompt_assembly.write_capabilities, "capabilities"), (prompt_assembly.write_usage, "usage")):
        if portable.get(part):
            fn(local_id, portable[part], definitions_dir=defs)


def _write_skill(ws: str, portable: Dict[str, Any], agent_id: str, *, name: Optional[str],
                 local_id: Optional[str]) -> str:
    from memory import skill_versions
    from memory.procedural import ProcedureStore, find_procedure
    from memory.skill_review import review_skill

    safety = review_skill(description=portable["description"], body=portable["body"], resources=[],
                          skill_dir=None, declared_license=portable.get("license") or "")
    if local_id:
        p = find_procedure(local_id)
        if p is None:
            raise CatalogError("The installed skill is gone.", status=404)
        p.name = name or portable["name"]
        p.description = portable["description"]
        p.steps = list(portable["steps"])
        p.body = portable["body"]
        p.tags = list(portable["tags"])
        p.license = portable.get("license") or ""
        p.safety = safety
        p.touch()
        ProcedureStore(p.workspace).update(p, version_op=skill_versions.OP_ORIGIN,
                                           version_note="updated from the linked hub")
        return str(p.id)
    from fastapi import HTTPException
    from models import SkillCreate
    from routes.skills import _create_skill
    try:
        created = _create_skill(
            SkillCreate(workspace=ws, name=name or portable["name"], description=portable["description"],
                        body=portable["body"], steps=list(portable["steps"]), tags=list(portable["tags"]),
                        agent_id=agent_id),
            version_op=skill_versions.OP_INSTALL, license=portable.get("license") or "", safety=safety)
    except HTTPException as exc:
        raise _http(exc) from exc
    return str(created["id"])


def _write_flow(local_id: str, ws: str, name: str, portable: Dict[str, Any], *, update: bool) -> None:
    from flow import store as flow_store
    from workspace import get_workspace_metadata, update_workspace_metadata

    text = yaml.safe_dump(portable, sort_keys=False, allow_unicode=True)
    try:
        flow = flow_store.import_flow_yaml(text, local_id)
    except flow_store.FlowParseError as exc:
        raise CatalogError(f"The flow does not fit this hub: {exc}", status=422) from exc
    except ValueError as exc:
        raise CatalogError(str(exc), status=400) from exc
    prev = flow_store.get_flow(local_id) if update else None
    now = _now()
    flow["name"] = name
    flow["workspace"] = prev.get("workspace") if prev else (None if ws == "default" else ws)
    flow["shared"] = bool((prev or {}).get("shared", False))
    flow["created_at"] = (prev or {}).get("created_at") or now
    flow["updated_at"] = now
    if prev and prev.get("owner_user") is not None:
        flow["owner_user"] = prev["owner_user"]
    flow_store.save_flow(flow)
    allowed = list(get_workspace_metadata(ws).get("allowed_flows") or [])
    if local_id not in allowed:
        update_workspace_metadata(ws, {"allowed_flows": allowed + [local_id]})


def _write_team(local_id: str, ws: str, portable: Dict[str, Any], *, update: bool) -> None:
    from teams import store as teams_store
    from teams.models import Team

    prev = teams_store.get_team(local_id) if update else None
    team = Team.from_dict({**portable, "team_id": local_id,
                           "workspace": prev.workspace if prev else (None if ws == "default" else ws),
                           "created_at": prev.created_at if prev else _now()})
    # The roster keeps this hub's own model picks on an update.
    if prev:
        picks = {m.agent_id: m for m in prev.members}
        for m in team.members:
            old = picks.get(m.agent_id)
            if old:
                m.provider, m.model = old.provider, old.model
        team.default_provider, team.default_model = prev.default_provider, prev.default_model
    teams_store.save_team(team)


def _write_loop(local_id: str, ws: str, portable: Dict[str, Any], *, update: bool) -> None:
    from loops import store as loops_store
    from loops.models import Loop

    prev = loops_store.get_loop(local_id) if update else None
    loop = Loop.from_dict({**portable, "loop_id": local_id,
                           "workspace": prev.workspace if prev else (None if ws == "default" else ws),
                           "created_at": prev.created_at if prev else _now()})
    if prev:
        loop.evaluator_provider, loop.evaluator_model, loop.grader = (
            prev.evaluator_provider, prev.evaluator_model, prev.grader)
    loops_store.save_loop(loop)


def _agents_of(kind: str, portable: Dict[str, Any]) -> List[str]:
    if kind == "flows":
        from flow import store as flow_store
        return flow_store.flow_agent_ids({"nodes": portable.get("nodes") or []})
    if kind == "teams":
        ids = [m.get("agent_id") for m in portable.get("members") or []]
        return [i for i in ids + [portable.get("leader_agent_id"), portable.get("entry_agent_id")] if i]
    if kind == "loops":
        return [portable["evaluator_agent_id"]] if portable.get("evaluator_agent_id") else []
    return []


async def install(kind: str, remote_id: str, workspace: str, new_id: Optional[str] = None,
                  request: Any = None, *, admin: bool = True) -> Dict[str, Any]:
    """Install an item and what it needs, dependencies first. A conflict on
    the item's own id or a missing dependency stops it before anything is
    written; a dependency already installed from this hub is left alone (it
    is updated from the Updates tab, with a diff)."""
    _check_kind(kind)
    _check_admin(kind, admin)
    plan, ws = _build_plan(kind, remote_id, workspace, new_id, admin)
    blocked = [s for s in plan.steps if s["action"] == "missing"]
    if blocked:
        raise CatalogError("Cannot install: " + "; ".join(f"{s['name']} ({s['reason']})" for s in blocked),
                           status=409, detail={"plan": {"steps": plan.steps}, "error": "blocked"})
    conflicts = [s for s in plan.steps if s["action"] == "conflict"]
    if conflicts:
        raise CatalogError(conflicts[0]["reason"], status=409,
                           detail={"error": "conflict", "plan": {"steps": plan.steps},
                                   "suggested_id": conflicts[0].get("suggested_id")})
    return await _execute(plan, ws, request)


async def _execute(plan: _Plan, ws: str, request: Any) -> Dict[str, Any]:
    agents = _idmap(plan.steps, "agents")
    flows = _idmap(plan.steps, "flows")
    deps: Dict[str, Dict[str, str]] = {"agents": agents, "flows": flows}
    done: List[Dict[str, Any]] = []
    for step in plan.steps:
        act = step["action"]
        if act in ("system", "exists"):
            done.append(step)
            continue
        kind, rid = step["kind"], step["remote_id"]
        remote = item(kind, rid)
        portable = _for_local(kind, remote["portable"], agents, flows)
        is_update = act == "update"
        local_id = step["local_id"]
        if kind == "agents":
            await _write_agent(local_id, ws, portable, request, create=not is_update)
        elif kind == "skills":
            agent_id = step.get("for_agent") or ""
            local_id = _write_skill(ws, portable, agent_id, name=step.get("new_name"),
                                    local_id=local_id if is_update else None)
            step["local_id"] = local_id
        elif kind == "flows":
            _write_flow(local_id, ws, portable_name(remote), portable, update=is_update)
        elif kind == "teams":
            _write_team(local_id, ws, portable, update=is_update)
        elif kind == "loops":
            _write_loop(local_id, ws, portable, update=is_update)
        if kind in ("flows", "teams", "loops"):
            _allow_agents(ws, [agents.get(a, a) for a in _agents_of(kind, remote["portable"])])
        step["done"] = True
        _record(kind, local_id, ws, rid, remote["hash"], deps if kind != "agents" else {})
        done.append(step)
    root = next((s for s in plan.steps if s["root"]), plan.steps[-1])
    return {"ok": True, "kind": root["kind"], "local_id": root["local_id"], "workspace": ws, "steps": done}


def portable_name(remote: Dict[str, Any]) -> str:
    return remote.get("name") or remote.get("id")


# ── updates and diffs ────────────────────────────────────────────────────────

def installed(workspace: Optional[str] = None,
              visible: Optional[Callable[[Optional[str]], bool]] = None) -> List[Dict[str, Any]]:
    """Install records of the current link, of one workspace when named, and
    only those whose copy lives where ``visible`` allows when it is given."""
    url = _remote_url()
    out = []
    for rec in _installs.values():
        if not isinstance(rec, dict) or rec.get("remote_url") != url:
            continue
        if workspace and rec.get("workspace") != workspace:
            continue
        if visible is not None and not visible(item_workspace(rec)):
            continue
        out.append(dict(rec))
    return out


def item_workspace(rec: Dict[str, Any]) -> Optional[str]:
    """Where an installed copy lives now, as stored on the item (``None`` for
    a global one, which every account sees), or the record's workspace when
    the copy is gone. Visibility is checked against this."""
    if _local_exists(rec["kind"], rec["local_id"]):
        return _local_workspace(rec["kind"], rec["local_id"])
    return rec.get("workspace")


def _state(rec: Dict[str, Any], remote: Optional[Dict[str, Any]], error: str = "") -> Dict[str, Any]:
    local = _local_portable(rec["kind"], rec["local_id"])
    modified = local is not None and digest(local) != rec.get("local_hash")
    return {
        "kind": rec["kind"], "local_id": rec["local_id"], "remote_id": rec["remote_id"],
        "workspace": rec.get("workspace"), "installed_at": rec.get("installed_at"),
        "updated_at": rec.get("updated_at"),
        "name": (remote or {}).get("name") or rec["local_id"],
        "update_available": bool(remote) and remote["hash"] != rec.get("remote_hash"),
        "locally_modified": modified, "local_missing": local is None,
        "remote_missing": remote is None and not error, "error": error,
    }


def updates(workspace: Optional[str] = None,
            visible: Optional[Callable[[Optional[str]], bool]] = None) -> List[Dict[str, Any]]:
    """Every installed item with whether the hub's version has moved on."""
    _remote()
    out = []
    for rec in installed(workspace, visible):
        try:
            remote = item(rec["kind"], rec["remote_id"])
            out.append(_state(rec, remote))
        except CatalogError as exc:
            out.append(_state(rec, None, "" if exc.status == 404 else str(exc)))
    out.sort(key=lambda s: (not s["update_available"], s["name"].lower()))
    return out


def _rec_or_404(kind: str, local_id: str) -> Dict[str, Any]:
    _check_kind(kind)
    rec = provenance(kind, local_id)
    if not rec:
        raise CatalogError("This item was not installed from the linked hub.", status=404)
    return rec


def diff(kind: str, local_id: str, *, admin: bool = True) -> Dict[str, Any]:
    """The unified diff from what is here now to what an update would write."""
    rec = _rec_or_404(kind, local_id)
    remote = item(kind, rec["remote_id"], admin=admin)
    deps = rec.get("deps") or {}
    wanted = _for_local(kind, remote["portable"], deps.get("agents") or {}, deps.get("flows") or {})
    local = _local_portable(kind, local_id)
    if local is None:
        raise CatalogError("The local copy is gone.", status=404)
    a, b = render_text(local), render_text(wanted)
    lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), "installed here", "linked hub",
                                      lineterm="", n=3))
    return {**_state(rec, remote), "diff": "\n".join(lines), "local": a, "remote": b}


async def update(kind: str, local_id: str, request: Any = None, *, admin: bool = True) -> Dict[str, Any]:
    """Overwrite the local copy with the hub's current version (the diff shows
    what is replaced; a skill keeps its history, an agent its version log).
    The plan is made for the workspace that owns the copy now, so the record
    follows the copy and never the workspace someone else installed from."""
    rec = _rec_or_404(kind, local_id)
    _check_admin(kind, admin)
    plan, ws = _build_plan(kind, rec["remote_id"], owner_workspace(kind, local_id, rec.get("workspace")),
                           None, admin)
    root = next((s for s in plan.steps if s["root"]), None)
    if root is None or root["action"] != "update" or root["local_id"] != local_id:
        raise CatalogError("The local copy cannot be matched to the hub's item.", status=409)
    blocked = [s for s in plan.steps if s["action"] == "missing"]
    if blocked:
        raise CatalogError("Cannot update: " + "; ".join(s["name"] for s in blocked), status=409)
    return await _execute(plan, ws, request)


__all__ = ["ADMIN_KINDS", "KINDS", "CatalogError", "browse", "diff", "digest", "enabled", "forget",
           "installed", "install", "item", "item_workspace", "owner_workspace", "plan_install",
           "provenance", "render_text", "set_enabled", "update", "updates"]
