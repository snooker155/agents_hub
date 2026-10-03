"""The four kinds a declarative file may hold, and how each meets the hub.

A kind knows its fields: how to check a declared value, how to put a declared
and an observed value into one canonical shape so the two compare, which
fields can only be set when the resource is created, and which REST route
writes each one. It reads and writes the hub only through ``ctx.request``,
the ``request(method, path, params=None, json=None)`` callable ``ah`` passes
(``cli.backend``'s ``hub().request``), so the same code drives a hub in this
process or one across the network, and never imports a service module.

Field writes go through the routes the dashboard uses for the same edit
(``/api/agents/{id}/tools``, ``/api/environments/{id}``, ``/api/plan/jobs/{id}``,
``/api/shared-memory/{id}/blocks/{name}``), so every check those routes make
(the capability guard, handoff targets, catalog models, cron syntax) applies
to a file exactly as it does to a click.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from declarative.errors import Problem
from declarative.parser import Resource

#: What a reference to a resource this apply has yet to create resolves to
#: while planning.
PENDING = "(known after apply)"


def is_not_found(exc: BaseException) -> bool:
    """Whether a failed request means "no such record". ``cli.backend`` puts
    the status in front of the detail in process (``404: Agent not found``)
    and only the detail over HTTP, so both are recognised, and so is an
    exception that carries a ``status_code``."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return int(status) == 404
    text = str(exc).strip().lower()
    return text.startswith("404") or "not found" in text


class Context:
    """One plan or apply: the transport, the default workspace and the hub ids
    known so far (from the lock, then from what this apply creates)."""

    def __init__(self, request: Callable[..., Any], workspace: Optional[str] = None,
                 bundle: Any = None, lock: Any = None) -> None:
        self.request = request
        self.workspace = (workspace or "").strip() or None
        self.bundle = bundle
        self.ids: Dict[str, str] = {}
        if lock is not None:
            for address in lock:
                entry = lock.get(address) or {}
                if entry.get("id"):
                    self.ids[address] = entry["id"]
        self._exists: Dict[Tuple[str, str], bool] = {}

    def call(self, method: str, path: str, params: Optional[dict] = None,
             json: Optional[dict] = None) -> Any:
        return self.request(method, path, params=params, json=json)

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        """GET, with None for a record that does not exist."""
        try:
            return self.call("GET", path, params=params)
        except Exception as exc:  # noqa: BLE001 - the transport's own error type is not known here
            if is_not_found(exc):
                return None
            raise

    def declared(self, kind: str, key: Any) -> bool:
        return self.bundle is not None and self.bundle.get(kind, str(key)) is not None

    def resolve(self, kind: str, value: Any) -> Any:
        """A reference by declared key, as the hub id it stands for. An agent's
        key is its hub id; a value the bundle does not declare is taken as a
        hub id already."""
        if value in (None, ""):
            return value
        if kind == "agent" or not self.declared(kind, value):
            return str(value)
        return self.ids.get(f"{kind}/{value}", PENDING)

    def exists(self, kind: str, value: Any) -> bool:
        key = (kind, str(value))
        if key not in self._exists:
            self._exists[key] = KINDS[kind].exists(self, str(value))
        return self._exists[key]

    def default_workspace(self, res: Resource) -> Optional[str]:
        ws = res.spec.get("workspace")
        return str(ws).strip() if ws not in (None, "") else self.workspace


# ---------------------------------------------------------------------------
# Field checks and canonical forms
# ---------------------------------------------------------------------------


def _text(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _str_or_none(v: Any) -> Optional[str]:
    text = _text(v)
    return text or None


def _str_list(v: Any) -> List[str]:
    out: List[str] = []
    for item in v or []:
        text = _text(item)
        if text and text not in out:
            out.append(text)
    return out


def _sorted_list(v: Any) -> List[str]:
    return sorted(_str_list(v))


def _bool(v: Any) -> bool:
    return bool(v)


def _tri(v: Any) -> Optional[bool]:
    return None if v is None or v == "auto" else bool(v)


def _workspace(v: Any) -> str:
    return _text(v) or "default"


def _iso(v: Any) -> Optional[str]:
    """An instant as a UTC ISO string, whatever the input spelled."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        except ValueError:
            return str(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _ident(v: Any) -> Any:
    return v


def want_str(v: Any) -> Optional[str]:
    return None if isinstance(v, (str, int, float)) and not isinstance(v, bool) else "must be a string"


def want_text(v: Any) -> Optional[str]:
    return None if isinstance(v, str) or v is None else "must be text"


def want_bool(v: Any) -> Optional[str]:
    return None if isinstance(v, bool) else "must be true or false"


def want_tri(v: Any) -> Optional[str]:
    return None if v is None or isinstance(v, bool) or v == "auto" else "must be true, false or auto"


def want_int(v: Any) -> Optional[str]:
    return None if isinstance(v, int) and not isinstance(v, bool) else "must be a whole number"


def want_num(v: Any) -> Optional[str]:
    return None if v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) else "must be a number"


def want_list(v: Any) -> Optional[str]:
    if not isinstance(v, list) or not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in v):
        return "must be a list of names"
    return None


def want_mapping(keys: Optional[Tuple[str, ...]] = None) -> Callable[[Any], Optional[str]]:
    def check(v: Any) -> Optional[str]:
        if not isinstance(v, dict):
            return "must be a mapping"
        if keys is not None:
            unknown = sorted(set(v) - set(keys))
            if unknown:
                return f"unknown key {', '.join(unknown)} (known: {', '.join(keys)})"
        return None
    return check


@dataclass(frozen=True)
class Field:
    """One declared field of a kind.

    ``norm`` maps a declared value (references already resolved) and an
    observed one to the same canonical shape. ``partial`` mappings manage only
    the keys a file names, leaving the rest of the hub's mapping alone.
    ``ref`` names the kind a value refers to (a key or a list of keys).
    """
    name: str
    check: Callable[[Any], Optional[str]] = _ident
    norm: Callable[[Any], Any] = _ident
    create_only: bool = False
    partial: bool = False
    ref: Optional[str] = None


def narrow(field: Field, declared: Any, observed: Any) -> Any:
    """The part of an observed value a declaration manages."""
    if field.partial and isinstance(declared, dict):
        source = observed if isinstance(observed, dict) else {}
        return {k: source.get(k) for k in declared}
    return observed


def _resolve_item(ctx: Context, kind: str, value: Any) -> Any:
    """One reference, or a ``{pool: key, read_only: ...}`` entry holding one."""
    if isinstance(value, dict):
        out = dict(value)
        slot = "pool" if "pool" in value else "id"
        out[slot] = ctx.resolve(kind, value.get(slot))
        return out
    if isinstance(value, str) and kind == "memory_pool" and value == "none":
        return value
    return ctx.resolve(kind, value)


class Kind:
    """What every kind provides; the subclasses fill it in."""

    name = ""
    fields: Tuple[Field, ...] = ()
    #: Fields filled in from the declared id when the file leaves them out.
    key_defaults: Tuple[str, ...] = ()

    @property
    def field_map(self) -> Dict[str, Field]:
        return {f.name: f for f in self.fields}

    # ---- files ----

    def validate(self, res: Resource) -> List[Problem]:
        problems: List[Problem] = []
        fmap = self.field_map
        for name, value in res.spec.items():
            f = fmap.get(name)
            if f is None:
                known = ", ".join(sorted(fmap))
                problems.append(res.problem(f"unknown {self.name} field '{name}' (known: {known})", name))
                continue
            message = f.check(value) if f.check is not _ident else None
            if message:
                problems.append(res.problem(f"{name} {message}", name))
        problems.extend(self.validate_more(res))
        return problems

    def validate_more(self, res: Resource) -> List[Problem]:
        return []

    def refs(self, res: Resource) -> List[Tuple[str, str, Any]]:
        out: List[Tuple[str, str, Any]] = []
        for f in self.fields:
            if f.ref is None or f.name not in res.spec:
                continue
            value = res.spec[f.name]
            if f.name == "memory" and not isinstance(value, list):
                continue  # memory: none
            for item in (value if isinstance(value, list) else [value]):
                if isinstance(item, dict):
                    item = item.get("pool", item.get("id"))
                out.append((f.name, f.ref, item))
        return out

    def declared(self, res: Resource) -> Dict[str, Any]:
        """The declared spec, with the defaults the id supplies."""
        spec = dict(res.spec)
        for name in self.key_defaults:
            spec.setdefault(name, res.key)
        return spec

    def desired(self, ctx: Context, res: Resource) -> Dict[str, Any]:
        """Declared fields in canonical form, references resolved to hub ids,
        in field (write) order."""
        spec = self.declared(res)
        out: Dict[str, Any] = {}
        for f in self.fields:
            if f.name not in spec:
                continue
            value = spec[f.name]
            if f.ref is not None:
                value = ([_resolve_item(ctx, f.ref, v) for v in value] if isinstance(value, list)
                         else _resolve_item(ctx, f.ref, value))
            out[f.name] = f.norm(value)
        return out

    # ---- hub ----

    def exists(self, ctx: Context, hub_id: str) -> bool:
        return self.observe(ctx, hub_id, None) is not None

    def observe(self, ctx: Context, hub_id: str, res: Optional[Resource]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def view(self, name: str, declared: Any, observed: Any) -> Any:
        """The part of an observed value the declared one is compared with."""
        return narrow(self.field_map[name], declared, observed)

    def find_existing(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> Optional[str]:
        """A hub record this resource would collide with when created, for a
        resource the lock does not know yet."""
        return None

    def blocked(self, observed: Dict[str, Any], changed: List[str]) -> Optional[str]:
        """Why the hub record cannot take these changes, if it cannot."""
        return None

    def create(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> str:
        raise NotImplementedError

    def write_groups(self, names: List[str]) -> List[List[str]]:
        """Changed fields in the batches one request writes."""
        return [[n] for n in names]

    def write(self, ctx: Context, hub_id: str, res: Resource, values: Dict[str, Any],
              observed: Dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, ctx: Context, hub_id: str) -> None:
        raise NotImplementedError

    def version(self, ctx: Context, hub_id: str, observed: Dict[str, Any]) -> Any:
        return observed.get("_version")


def _q(value: str) -> str:
    from urllib.parse import quote
    return quote(str(value), safe="")


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------

MODEL_KEYS = ("provider", "model", "base_url", "temperature", "max_tokens")
REASONING_KEYS = ("think_enabled", "think_mode", "thinking_level", "plan_enabled", "plan_format")
LOOP_KEYS = ("fallback_models", "advisor_model", "output_schema", "max_concurrent_delegates",
             "tool_search", "compaction")
WEB_KEYS = ("allowed_domains", "blocked_domains")
SKILL_KEYS = ("name", "description", "steps", "body", "tags")


def _norm_model(v: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, val in (v or {}).items():
        if k == "provider":
            out[k] = _text(val) or "inherit"
        elif k in ("model", "base_url"):
            out[k] = _text(val)
        else:
            out[k] = val
    return out


def _check_memory(v: Any) -> Optional[str]:
    if v in (None, "none"):
        return None
    if not isinstance(v, list):
        return "must be a list of memory pools (a pool id, or {pool: id, read_only: true})"
    for item in v:
        if isinstance(item, dict):
            if not item.get("pool") or set(item) - {"pool", "read_only"}:
                return "entries are a pool id or {pool: id, read_only: true}"
        elif not isinstance(item, str) or not item.strip():
            return "entries are a pool id or {pool: id, read_only: true}"
    return None


def _norm_memory(v: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in (v if isinstance(v, list) else []):
        if isinstance(item, dict):
            pid, ro = item.get("id") or item.get("pool"), bool(item.get("read_only"))
        else:
            pid, ro = item, False
        if pid and all(e["id"] != str(pid) for e in out):
            out.append({"id": str(pid), "read_only": ro})
    return out


def _observed_memory(detail: Dict[str, Any]) -> List[Dict[str, Any]]:
    if (detail.get("memory_type") or "none") != "shared":
        return []
    data = detail.get("memory_data")
    return _norm_memory(data if isinstance(data, list) else [data] if data else [])


def _check_skills(v: Any) -> Optional[str]:
    if not isinstance(v, list):
        return "must be a list of skills"
    names = set()
    for item in v:
        if not isinstance(item, dict) or not _text(item.get("name")):
            return "entries are mappings with at least a name"
        unknown = set(item) - set(SKILL_KEYS)
        if unknown:
            return f"entry '{item.get('name')}' has unknown key {', '.join(sorted(unknown))}"
        if not item.get("steps") and not _text(item.get("body")):
            return f"entry '{item.get('name')}' needs steps or a body"
        key = _text(item["name"]).lower()
        if key in names:
            return f"entry '{item['name']}' is listed twice"
        names.add(key)
    return None


def _skill_canon(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "description": _text(item.get("description")),
        "steps": [s for s in (_text(x) for x in item.get("steps") or []) if s],
        "body": _text(item.get("body")),
        "tags": [t for t in (_text(x) for x in item.get("tags") or []) if t],
    }


def _norm_skills(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict):
        return v
    return {_text(item["name"]): _skill_canon(item) for item in v or []}


def _check_outcome(v: Any) -> Optional[str]:
    """``outcome:`` is the same shape as a task's (tasks/outcome.py):
    ``rubric``, ``max_iterations``, ``grader``, ``threshold``. Absent or
    empty clears the agent's default outcome."""
    if v in (None, {}):
        return None
    if not isinstance(v, dict):
        return "must be a mapping with rubric, max_iterations, grader, threshold"
    from tasks.outcome import OutcomeError, normalize_outcome
    try:
        normalize_outcome(v)
    except OutcomeError as exc:
        return str(exc)
    return None


def _norm_outcome(v: Any) -> Optional[Dict[str, Any]]:
    if not v:
        return None
    from tasks.outcome import normalize_outcome
    return normalize_outcome(v)


class AgentKind(Kind):
    name = "agent"
    key_defaults = ("name",)
    fields = (
        Field("name", want_str, _text),
        Field("domain", want_str, _text),
        Field("capacity", want_int, int),
        Field("workspace", want_str, _workspace, create_only=True),
        Field("capability_override", want_bool, _bool),
        Field("tools", want_list, _sorted_list),
        Field("model", want_mapping(MODEL_KEYS), _norm_model, partial=True),
        Field("reasoning", want_mapping(REASONING_KEYS), dict, partial=True),
        Field("memory", _check_memory, _norm_memory, ref="memory_pool"),
        Field("delegates", want_list, _sorted_list, ref="agent"),
        Field("handoffs", want_list, _sorted_list, ref="agent"),
        Field("handoff_history", want_str, _text),
        Field("description", want_text, _text),
        Field("instructions", want_text, _text),
        Field("capabilities", want_text, _text),
        Field("usage", want_text, _text),
        Field("skills", _check_skills, _norm_skills, partial=True),
        Field("skills_enabled", want_bool, _bool),
        Field("episodic_write", want_tri, _tri),
        Field("response_format", want_str, _text),
        Field("clarify_gate", want_bool, _bool),
        Field("allow_self_delegation", want_bool, _bool),
        Field("web_domains", want_mapping(WEB_KEYS), lambda v: {k: _str_list(x) for k, x in (v or {}).items()},
              partial=True),
        Field("loop", want_mapping(LOOP_KEYS), dict, partial=True),
        Field("tool_policy", want_mapping(), dict),
        Field("guardrails", want_list, _sorted_list),
        Field("secrets", want_list, _sorted_list),
        Field("shared", want_bool, _bool),
        Field("proactive", want_mapping(), dict, partial=True),
        Field("outcome", _check_outcome, _norm_outcome),
    )

    #: Values a fresh agent has, left out of an exported file.
    DEFAULTS: Dict[str, Any] = {
        "domain": "general", "capacity": 1, "workspace": "default", "capability_override": False,
        "model": {"provider": "inherit", "model": "", "base_url": "", "temperature": None, "max_tokens": None},
        "reasoning": {}, "memory": [], "delegates": [], "handoffs": [], "handoff_history": "full",
        "description": "", "capabilities": "", "usage": "", "skills": {}, "skills_enabled": False,
        "episodic_write": None, "response_format": "none", "clarify_gate": False,
        "allow_self_delegation": False, "web_domains": {"allowed_domains": [], "blocked_domains": []},
        "loop": {"fallback_models": [], "advisor_model": None, "output_schema": None,
                 "max_concurrent_delegates": 6, "tool_search": None, "compaction": None},
        "tool_policy": {}, "guardrails": [], "secrets": [], "shared": False, "outcome": None,
    }

    def validate_more(self, res: Resource) -> List[Problem]:
        problems: List[Problem] = []
        if not _text(res.spec.get("instructions")):
            problems.append(res.problem("an agent needs instructions (the markdown body)"))
        model = res.spec.get("model")
        if isinstance(model, dict) and model.get("model") and not model.get("provider"):
            problems.append(res.problem("model: name a provider with the model", "model"))
        reasoning = res.spec.get("reasoning")
        if isinstance(reasoning, dict) and any(v is None for v in reasoning.values()):
            problems.append(res.problem("reasoning: a key left empty cannot be cleared; remove it", "reasoning"))
        if "/" in res.key or not res.key.strip():
            problems.append(res.problem(f"'{res.key}' is not a usable agent id", "id"))
        return problems

    # ---- reading ----

    def observe(self, ctx: Context, hub_id: str, res: Optional[Resource]) -> Optional[Dict[str, Any]]:
        detail = ctx.get(f"/api/agents/{_q(hub_id)}")
        if detail is None:
            return None
        wanted = set(self.declared(res)) if res is not None else set(self.field_map)
        owner = detail.get("owner_workspace") or None
        if owner and "memory" in wanted:
            # Read in the home workspace: elsewhere the route answers the
            # workspace's own memory override instead of the record's.
            detail = ctx.get(f"/api/agents/{_q(hub_id)}", params={"workspace": owner}) or detail
        out: Dict[str, Any] = {
            "name": detail.get("name") or "",
            "domain": detail.get("domain") or "general",
            "capacity": int(detail.get("capacity") or 1),
            "workspace": _workspace(owner),
            "capability_override": bool(detail.get("capability_override")),
            "tools": _sorted_list(detail.get("tools")),
            "model": {
                "provider": detail.get("provider") or "inherit",
                "model": detail.get("model") or "",
                "base_url": detail.get("base_url") or "",
                "temperature": detail.get("temperature"),
                "max_tokens": detail.get("max_tokens"),
            },
            "reasoning": dict(detail.get("reasoning") or {}),
            "memory": _observed_memory(detail),
            "delegates": _sorted_list(detail.get("delegates")),
            "handoffs": _sorted_list(detail.get("handoffs")),
            "handoff_history": detail.get("handoff_history") or "full",
            "description": _text(detail.get("description")),
            "skills_enabled": bool(detail.get("skills_enabled")),
            "episodic_write": detail.get("episodic_write_enabled"),
            "response_format": detail.get("response_format") or "none",
            "clarify_gate": bool(detail.get("clarify_gate")),
            "allow_self_delegation": bool(detail.get("allow_self_delegation")),
            "web_domains": {"allowed_domains": list(detail.get("allowed_domains") or []),
                            "blocked_domains": list(detail.get("blocked_domains") or [])},
            "loop": {
                "fallback_models": list(detail.get("fallback_models") or []),
                "advisor_model": detail.get("advisor_model") or None,
                "output_schema": detail.get("output_schema") or None,
                "max_concurrent_delegates": int(detail.get("max_concurrent_delegates") or 6),
                "tool_search": detail.get("tool_search"),
                "compaction": detail.get("compaction"),
            },
            "tool_policy": dict(detail.get("tool_policy") or {}),
            "guardrails": _sorted_list(detail.get("guardrails")),
            "secrets": _sorted_list(detail.get("secrets")),
            "shared": bool(detail.get("shared")),
            "_system": bool(detail.get("system")),
        }
        if wanted & {"instructions", "capabilities", "usage"}:
            definition = ctx.get(f"/api/agents/{_q(hub_id)}/definition") or {}
            for part in ("instructions", "capabilities", "usage"):
                out[part] = _text(definition.get(part))
        if "proactive" in wanted:
            status = ctx.get(f"/api/agents/{_q(hub_id)}/proactive") or {}
            profile = dict(status.get("profile") or {})
            profile.pop("job_id", None)
            out["proactive"] = profile
        if "skills" in wanted:
            out["skills"], out["_skill_ids"] = self._skills(ctx, hub_id, out["workspace"])
        if "outcome" in wanted:
            data = ctx.get(f"/api/agents/{_q(hub_id)}/default-outcome") or {}
            out["outcome"] = _norm_outcome(data.get("default_outcome"))
        return out

    @staticmethod
    def _skills(ctx: Context, hub_id: str, workspace: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
        rows = ctx.get("/api/skills", params={"workspace": workspace, "agent_id": hub_id}) or []
        skills: Dict[str, Any] = {}
        ids: Dict[str, str] = {}
        for row in rows:
            if row.get("repo"):
                continue  # a repository skill: read only, the repository owns it
            name = _text(row.get("name"))
            skills[name] = _skill_canon(row)
            ids[name] = str(row.get("id"))
        return skills, ids

    def version(self, ctx: Context, hub_id: str, observed: Dict[str, Any]) -> Any:
        versions = ctx.get(f"/api/agents/{_q(hub_id)}/versions") or {}
        return max((v.get("version") or 0 for v in versions.get("versions") or []), default=None)

    def find_existing(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> Optional[str]:
        return res.key if ctx.get(f"/api/agents/{_q(res.key)}") is not None else None

    def exists(self, ctx: Context, hub_id: str) -> bool:
        return ctx.get(f"/api/agents/{_q(hub_id)}") is not None

    # ---- writing ----

    def create(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> str:
        spec = self.declared(res)
        body: Dict[str, Any] = {
            "id": res.key,
            "name": desired.get("name") or res.key,
            "description": desired.get("description", ""),
            "domain": desired.get("domain") or "general",
            "system_prompt": spec.get("instructions") or "",
            "capacity": desired.get("capacity", 1),
            "workspace": ctx.default_workspace(res),
            # Handoff targets may be agents this same apply has yet to
            # create; they are written once every agent exists.
            "handoffs": [],
        }
        if "tools" in desired and not desired.get("capability_override"):
            body["tools"] = desired["tools"]
        elif "tools" in desired:
            body["tools"] = []  # the override goes on first, then the tools
        ctx.call("POST", "/api/agents/create", json=body)
        return res.key

    def write(self, ctx: Context, hub_id: str, res: Resource, values: Dict[str, Any],
              observed: Dict[str, Any]) -> None:
        base = f"/api/agents/{_q(hub_id)}"
        home = observed.get("workspace") or "default"
        for name, value in values.items():
            if name == "capability_override":
                ctx.call("POST", f"{base}/capability-override", json={"capability_override": value})
            elif name == "tools":
                ctx.call("POST", f"{base}/tools", json={"tools": value})
            elif name == "model":
                body: Dict[str, Any] = {}
                for k, v in value.items():
                    if k in ("temperature", "max_tokens") and v is None:
                        body[f"clear_{k}"] = True
                    else:
                        body[k] = v
                ctx.call("POST", f"{base}/model", json=body)
            elif name == "reasoning":
                ctx.call("POST", f"{base}/reasoning", json=value)
            elif name == "memory":
                if value:
                    data: Any = [({"id": e["id"], "read_only": True} if e["read_only"] else e["id"])
                                 for e in value]
                    ctx.call("POST", f"{base}/memory",
                             json={"memory_type": "shared", "memory_data": data, "workspace": home})
                else:
                    ctx.call("DELETE", f"{base}/memory", params={"workspace": home})
            elif name == "delegates":
                ctx.call("POST", f"{base}/delegates", json={"delegates": value})
            elif name in ("handoffs", "handoff_history"):
                ctx.call("POST", f"{base}/handoffs", json={name: value})
            elif name == "description":
                ctx.call("PUT", f"{base}/description", json={"description": value})
            elif name in ("name", "domain", "capacity"):
                ctx.call("PUT", f"{base}/identity", json={name: value})
            elif name in ("instructions", "capabilities", "usage"):
                ctx.call("PUT", f"{base}/definition", json={name: value})
            elif name == "skills":
                self._write_skills(ctx, hub_id, res, value, observed, home)
            elif name == "skills_enabled":
                ctx.call("POST", f"{base}/skills-config", json={"skills_enabled": value})
            elif name == "episodic_write":
                ctx.call("POST", f"{base}/episodic-config", json={"episodic_write_enabled": value})
            elif name == "response_format":
                ctx.call("POST", f"{base}/response-format", json={"response_format": value})
            elif name == "clarify_gate":
                ctx.call("POST", f"{base}/clarify-gate", json={"clarify_gate": value})
            elif name == "allow_self_delegation":
                ctx.call("POST", f"{base}/self-delegation", json={"allow_self_delegation": value})
            elif name == "web_domains":
                ctx.call("PUT", f"{base}/web-domains", json=value)
            elif name == "loop":
                ctx.call("PUT", f"{base}/loop-settings", json=value)
            elif name == "tool_policy":
                ctx.call("PUT", f"{base}/tool-policy", json={"tool_policy": value})
            elif name == "guardrails":
                ctx.call("PUT", f"{base}/guardrails", json={"guardrails": value})
            elif name == "secrets":
                ctx.call("PUT", f"{base}/secrets", json={"secrets": value})
            elif name == "shared":
                ctx.call("POST", f"{base}/sharing", json={"shared": value})
            elif name == "proactive":
                ctx.call("PUT", f"{base}/proactive", json=value)
            elif name == "outcome":
                ctx.call("PUT", f"{base}/default-outcome", json=value or {})
            else:  # pragma: no cover - create-only fields never reach a write
                raise ValueError(f"{name} cannot be changed on an existing agent")

    def _write_skills(self, ctx: Context, hub_id: str, res: Resource, value: Dict[str, Any],
                      observed: Dict[str, Any], home: str) -> None:
        current = observed.get("skills") or {}
        ids = observed.get("_skill_ids") or {}
        if not ids and current:
            current, ids = self._skills(ctx, hub_id, home)
        for name, skill in value.items():
            if name not in ids:
                ctx.call("POST", "/api/skills", json={"name": name, "workspace": home,
                                                      "agent_id": hub_id, **skill})
            elif current.get(name) != skill:
                ctx.call("PATCH", f"/api/skills/{_q(ids[name])}", json=skill)

    def blocked(self, observed: Dict[str, Any], changed: List[str]) -> Optional[str]:
        if observed.get("_system") and changed:
            return "a system agent ships with the hub; declare a new id instead of editing it"
        return None

    def delete(self, ctx: Context, hub_id: str) -> None:
        ctx.call("DELETE", f"/api/agents/{_q(hub_id)}")


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------

NETWORK_KEYS = ("type", "allowed_hosts", "allow_package_managers")
LIMIT_KEYS = ("memory", "cpus", "pids_limit")


def _check_env_vars(v: Any) -> Optional[str]:
    if not isinstance(v, dict) or not all(isinstance(k, str) for k in v):
        return "must be a mapping of NAME: value"
    return None


class EnvironmentKind(Kind):
    name = "environment"
    key_defaults = ("name",)
    fields = (
        Field("name", want_str, _text),
        Field("workspace", want_str, _str_or_none),
        Field("description", want_text, _text),
        Field("mode", want_str, _text),
        Field("image", lambda v: None if v is None else want_str(v), _str_or_none),
        Field("packages", want_list, _str_list),
        Field("network", want_mapping(NETWORK_KEYS), dict, partial=True),
        Field("limits", want_mapping(LIMIT_KEYS), dict, partial=True),
        Field("size", lambda v: None if v is None else want_str(v), _str_or_none),
        Field("env", _check_env_vars, lambda v: {str(k): str(x) for k, x in (v or {}).items()}),
        Field("sandbox_provider", want_str, _text),
        Field("is_default", want_bool, _bool),
    )
    DEFAULTS: Dict[str, Any] = {
        "workspace": None, "description": "", "mode": "inherit", "image": None, "packages": [],
        "network": {"type": "unrestricted", "allowed_hosts": [], "allow_package_managers": False},
        "limits": {"memory": None, "cpus": None, "pids_limit": None}, "size": None, "env": {},
        "sandbox_provider": "inherit", "is_default": False,
    }

    @staticmethod
    def _canon(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": _text(row.get("name")),
            "workspace": _str_or_none(row.get("workspace")),
            "description": _text(row.get("description")),
            "mode": row.get("mode") or "inherit",
            "image": _str_or_none(row.get("image")),
            "packages": list(row.get("packages") or []),
            "network": dict(row.get("network") or {}),
            "limits": dict(row.get("limits") or {}),
            "size": _str_or_none(row.get("size")),
            "env": dict(row.get("env") or {}),
            "sandbox_provider": row.get("sandbox_provider") or "inherit",
            "is_default": bool(row.get("is_default")),
            "_version": row.get("updated_at"),
            "_archived": bool(row.get("archived_at")),
        }

    def observe(self, ctx: Context, hub_id: str, res: Optional[Resource]) -> Optional[Dict[str, Any]]:
        row = ctx.get(f"/api/environments/{_q(hub_id)}")
        return self._canon(row) if row is not None else None

    def find_existing(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> Optional[str]:
        ws = desired.get("workspace", _str_or_none(ctx.default_workspace(res)))
        rows = ctx.get("/api/environments", params={"workspace": ws, "include_archived": True}
                       if ws else {"include_archived": True}) or []
        for row in rows:
            if _text(row.get("name")) == desired.get("name") and _str_or_none(row.get("workspace")) == ws:
                return str(row.get("id"))
        return None

    def blocked(self, observed: Dict[str, Any], changed: List[str]) -> Optional[str]:
        if observed.get("_archived") and changed:
            return "the environment is archived in the hub and read only"
        return None

    def create(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> str:
        body = {k: v for k, v in desired.items()}
        body.setdefault("workspace", ctx.default_workspace(res))
        row = ctx.call("POST", "/api/environments", json=body)
        return str(row["id"])

    def write_groups(self, names: List[str]) -> List[List[str]]:
        return [names] if names else []

    def write(self, ctx: Context, hub_id: str, res: Resource, values: Dict[str, Any],
              observed: Dict[str, Any]) -> None:
        ctx.call("PATCH", f"/api/environments/{_q(hub_id)}", json=dict(values))

    def delete(self, ctx: Context, hub_id: str) -> None:
        ctx.call("DELETE", f"/api/environments/{_q(hub_id)}")


# ---------------------------------------------------------------------------
# memory_pool
# ---------------------------------------------------------------------------

BLOCK_KEYS = ("value", "limit_chars", "description", "read_only")


def _check_blocks(v: Any) -> Optional[str]:
    if not isinstance(v, dict):
        return "must be a mapping of block name to its text (or {value, limit_chars, description, read_only})"
    for name, block in v.items():
        if isinstance(block, dict):
            unknown = set(block) - set(BLOCK_KEYS)
            if unknown:
                return f"block '{name}' has unknown key {', '.join(sorted(unknown))}"
        elif not isinstance(block, str):
            return f"block '{name}' must be text or a mapping"
    return None


def _norm_blocks(v: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, block in (v or {}).items():
        block = {"value": block} if isinstance(block, str) else dict(block)
        if "value" in block:
            block["value"] = _text(block["value"])
        out[str(name)] = block
    return out


class MemoryPoolKind(Kind):
    name = "memory_pool"
    key_defaults = ("name",)
    fields = (
        Field("name", want_str, _text, create_only=True),
        Field("description", want_text, _text, create_only=True),
        Field("type", want_str, _text, create_only=True),
        Field("workspace", want_str, _str_or_none, create_only=True),
        Field("blocks", _check_blocks, _norm_blocks, partial=True),
    )

    def observe(self, ctx: Context, hub_id: str, res: Optional[Resource]) -> Optional[Dict[str, Any]]:
        row = ctx.get(f"/api/shared-memory/{_q(hub_id)}")
        if row is None:
            return None
        blocks = {}
        for b in row.get("blocks") or []:
            blocks[str(b.get("name"))] = {"value": _text(b.get("value")), "limit_chars": b.get("limit_chars"),
                                          "description": b.get("description") or "",
                                          "read_only": bool(b.get("read_only"))}
        return {
            "name": _text(row.get("name")),
            "description": _text(row.get("description")),
            "type": row.get("type") or "text",
            "workspace": _str_or_none(row.get("workspace")),
            "blocks": blocks,
            "_version": row.get("updated_at"),
        }

    def view(self, name: str, declared: Any, observed: Any) -> Any:
        if name != "blocks":
            return super().view(name, declared, observed)
        # Each block manages only the keys the file gives it; block names
        # match without regard to case, as the hub looks them up.
        current = {k.lower(): v for k, v in (observed or {}).items()}
        out: Dict[str, Any] = {}
        for block, spec in (declared or {}).items():
            have = current.get(block.lower())
            out[block] = {k: have.get(k) for k in spec} if have is not None else None
        return out

    def find_existing(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> Optional[str]:
        ws = desired.get("workspace", _str_or_none(ctx.default_workspace(res)))
        rows = ctx.get("/api/shared-memory", params={"workspace": ws} if ws else None) or []
        for row in rows:
            if (_text(row.get("name")) == desired.get("name") and _str_or_none(row.get("workspace")) == ws
                    and (row.get("kind") or "shared") == "shared"):
                return str(row.get("id"))
        return None

    def create(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> str:
        body = {"name": desired["name"], "description": desired.get("description", ""),
                "type": desired.get("type") or "text",
                "workspace": desired.get("workspace", ctx.default_workspace(res))}
        row = ctx.call("POST", "/api/shared-memory", json=body)
        return str(row["id"])

    def write(self, ctx: Context, hub_id: str, res: Resource, values: Dict[str, Any],
              observed: Dict[str, Any]) -> None:
        current = self.view("blocks", values.get("blocks"), observed.get("blocks"))
        for name, block in (values.get("blocks") or {}).items():
            if current.get(name) != block:
                ctx.call("PUT", f"/api/shared-memory/{_q(hub_id)}/blocks/{_q(name)}", json=block)

    def delete(self, ctx: Context, hub_id: str) -> None:
        ctx.call("DELETE", f"/api/shared-memory/{_q(hub_id)}")


# ---------------------------------------------------------------------------
# deployment (a scheduled plan job)
# ---------------------------------------------------------------------------

JOB_KINDS = ("agent_task", "notification", "flow", "memory_consolidate")
RECURRENCES = ("none", "hourly", "daily", "weekly", "cron")
#: Declared name -> the job field it is stored as.
HUB_NAMES = {"job": "kind", "agent": "agent_id", "environment": "environment_id",
             "memory_pools": "memory_pool_ids", "consolidate_pool": "consolidate_pool_id"}
FINAL_STATUSES = ("fired", "cancelled", "failed")


class DeploymentKind(Kind):
    name = "deployment"
    key_defaults = ("title",)
    fields = (
        Field("title", want_str, _text),
        Field("job", want_str, _text, create_only=True),
        Field("workspace", want_str, _str_or_none, create_only=True),
        Field("flow_id", want_str, _text, create_only=True),
        Field("seed", want_mapping(), dict, create_only=True),
        Field("max_concurrent", want_int, int, create_only=True),
        Field("agent", want_str, _text, ref="agent"),
        Field("message", want_text, _text),
        Field("recurrence", want_str, _text),
        Field("cron", want_str, _text),
        Field("timezone", want_str, lambda v: _text(v) or "UTC"),
        Field("run_at", lambda v: None if isinstance(v, (str, datetime)) else "must be an ISO date and time",
              _iso),
        Field("catch_up", want_bool, _bool),
        Field("channels", want_list, _str_list),
        Field("environment", lambda v: None if v is None else want_str(v), _str_or_none, ref="environment"),
        Field("budget_usd", want_num, _ident),
        Field("agent_version", lambda v: None if v is None else want_int(v), _ident),
        Field("auto_pause_after", want_int, int),
        Field("project_id", lambda v: None if v is None else want_str(v), _str_or_none),
        Field("file_ids", want_list, _str_list),
        Field("secrets", want_list, _str_list),
        Field("memory_pools", want_list, _str_list, ref="memory_pool"),
        Field("memory_access", lambda v: None if v is None else want_str(v), _str_or_none),
        Field("consolidate_pool", want_str, _str_or_none, ref="memory_pool"),
        Field("consolidate_session_limit", lambda v: None if v is None else want_int(v), _ident),
        Field("paused", want_bool, _bool),
    )
    DEFAULTS: Dict[str, Any] = {
        "job": "agent_task", "workspace": None, "flow_id": "", "seed": {}, "max_concurrent": 1,
        "message": "", "recurrence": "none", "cron": "", "timezone": "UTC", "catch_up": False,
        "channels": ["dashboard"], "environment": None, "budget_usd": None, "agent_version": None,
        "auto_pause_after": 3, "project_id": None, "file_ids": [], "secrets": [], "memory_pools": [],
        "memory_access": None, "consolidate_pool": None, "consolidate_session_limit": None, "paused": False,
    }

    def declared(self, res: Resource) -> Dict[str, Any]:
        spec = super().declared(res)
        if "recurrence" not in spec:
            spec["recurrence"] = "cron" if spec.get("cron") else "none"
        if spec.get("recurrence") != "none":
            # A recurring job's run_at is only where it starts: the hub moves
            # it on every firing, so it is sent on create and never compared.
            spec.pop("run_at", None)
        return spec

    def validate_more(self, res: Resource) -> List[Problem]:
        problems: List[Problem] = []
        spec = self.declared(res)
        job = spec.get("job", "agent_task")
        if job not in JOB_KINDS:
            problems.append(res.problem(f"job must be one of {', '.join(JOB_KINDS)}", "job"))
        if spec.get("recurrence") not in RECURRENCES:
            problems.append(res.problem(f"recurrence must be one of {', '.join(RECURRENCES)}", "recurrence"))
        if spec.get("recurrence") == "cron" and not spec.get("cron"):
            problems.append(res.problem("recurrence cron needs a cron: expression", "recurrence"))
        if spec.get("recurrence") not in ("cron",) and not res.spec.get("run_at"):
            problems.append(res.problem("run_at is required unless cron: sets the schedule", "id"))
        if job == "agent_task" and not spec.get("agent"):
            problems.append(res.problem("an agent_task deployment names the agent: it runs", "id"))
        if job == "flow" and not spec.get("flow_id"):
            problems.append(res.problem("a flow deployment needs flow_id", "job"))
        if job == "memory_consolidate" and not spec.get("consolidate_pool"):
            problems.append(res.problem("a memory_consolidate deployment needs consolidate_pool", "job"))
        return problems

    def observe(self, ctx: Context, hub_id: str, res: Optional[Resource]) -> Optional[Dict[str, Any]]:
        row = ctx.get(f"/api/plan/jobs/{_q(hub_id)}")
        if row is None:
            return None
        out = {
            "title": _text(row.get("title")),
            "job": row.get("kind") or "agent_task",
            "workspace": _str_or_none(row.get("workspace")),
            "flow_id": _text(row.get("flow_id")),
            "seed": dict(row.get("seed") or {}),
            "max_concurrent": int(row.get("max_concurrent") or 1),
            "agent": _text(row.get("agent_id")),
            "message": _text(row.get("message")),
            "recurrence": row.get("recurrence") or "none",
            "cron": _text(row.get("cron")),
            "timezone": _text(row.get("timezone")) or "UTC",
            "run_at": _iso(row.get("run_at")),
            "catch_up": bool(row.get("catch_up")),
            "channels": list(row.get("channels") or []),
            "environment": _str_or_none(row.get("environment_id")),
            "budget_usd": row.get("budget_usd"),
            "agent_version": row.get("agent_version"),
            "auto_pause_after": int(row.get("auto_pause_after") if row.get("auto_pause_after") is not None else 3),
            "project_id": _str_or_none(row.get("project_id")),
            "file_ids": list(row.get("file_ids") or []),
            "secrets": list(row.get("secrets") or []),
            "memory_pools": list(row.get("memory_pool_ids") or []),
            "memory_access": _str_or_none(row.get("memory_access")),
            "consolidate_pool": _str_or_none(row.get("consolidate_pool_id")),
            "consolidate_session_limit": row.get("consolidate_session_limit"),
            "paused": row.get("status") == "paused",
            "_status": row.get("status"),
            "_version": row.get("updated_at"),
        }
        return out

    def blocked(self, observed: Dict[str, Any], changed: List[str]) -> Optional[str]:
        status = observed.get("_status")
        if status in FINAL_STATUSES and changed:
            return (f"the job is {status} in the hub and cannot be edited; remove its entry from "
                    "the lock to create a new one")
        return None

    @staticmethod
    def _first_run(ctx: Context, values: Dict[str, Any], declared_run_at: Optional[str]) -> Optional[str]:
        """Where a recurring job starts: the declared run_at while it is still
        ahead, else the cron's next fire as the hub computes it."""
        if declared_run_at and _iso(declared_run_at) > datetime.now(timezone.utc).isoformat():
            return _iso(declared_run_at)
        if values.get("recurrence") == "cron" and values.get("cron"):
            params = {"cron": values["cron"], "count": 1}
            if values.get("timezone"):
                params["timezone"] = values["timezone"]
            preview = ctx.get("/api/plan/cron/preview", params=params) or {}
            if preview.get("valid") is False:
                raise ValueError(f"cron '{values['cron']}': {preview.get('error')}")
            runs = preview.get("upcoming_runs_at") or []
            if runs:
                return _iso(runs[0])
            try:
                from croniter import croniter
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(values.get("timezone") or "UTC")
                nxt = croniter(values["cron"], datetime.now(tz)).get_next(datetime)
                return _iso(nxt)
            except ImportError:
                pass
        return _iso(declared_run_at) if declared_run_at else None

    @staticmethod
    def _hub_body(values: Dict[str, Any]) -> Dict[str, Any]:
        return {HUB_NAMES.get(k, k): v for k, v in values.items() if k != "paused"}

    def create(self, ctx: Context, res: Resource, desired: Dict[str, Any]) -> str:
        values = {**{k: v for k, v in desired.items()}}
        values.setdefault("job", "agent_task")
        values.setdefault("workspace", ctx.default_workspace(res))
        run_at = self._first_run(ctx, values, res.spec.get("run_at"))
        values.pop("run_at", None)
        body = self._hub_body(values)
        if run_at:
            body["run_at"] = run_at
        else:
            body["delay_minutes"] = 1
        row = ctx.call("POST", "/api/plan/jobs", json=body)
        if desired.get("paused"):
            ctx.call("POST", f"/api/plan/jobs/{_q(row['id'])}/pause")
        return str(row["id"])

    def write_groups(self, names: List[str]) -> List[List[str]]:
        body = [n for n in names if n != "paused"]
        return ([body] if body else []) + ([["paused"]] if "paused" in names else [])

    def write(self, ctx: Context, hub_id: str, res: Resource, values: Dict[str, Any],
              observed: Dict[str, Any]) -> None:
        if "paused" in values:
            verb = "pause" if values["paused"] else "resume"
            ctx.call("POST", f"/api/plan/jobs/{_q(hub_id)}/{verb}")
            return
        body = self._hub_body(values)
        if {"cron", "timezone", "recurrence"} & set(values) and "run_at" not in values:
            merged = {k: values.get(k, observed.get(k)) for k in ("recurrence", "cron", "timezone")}
            run_at = self._first_run(ctx, merged, res.spec.get("run_at"))
            if run_at:
                body["run_at"] = run_at
        ctx.call("PATCH", f"/api/plan/jobs/{_q(hub_id)}", json=body)

    def delete(self, ctx: Context, hub_id: str) -> None:
        ctx.call("DELETE", f"/api/plan/jobs/{_q(hub_id)}")


KINDS: Dict[str, Kind] = {k.name: k for k in (MemoryPoolKind(), EnvironmentKind(), AgentKind(),
                                               DeploymentKind())}
#: Create and update order: what a later kind refers to exists by then.
#: Deletes run in the reverse order.
KIND_ORDER = ("memory_pool", "environment", "agent", "deployment")


__all__ = ["KINDS", "KIND_ORDER", "Kind", "Field", "Context", "PENDING", "is_not_found", "narrow"]
