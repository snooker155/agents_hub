"""Writing hub records out as declarative files: ``ah apply --export``.

The output is the format :mod:`declarative.parser` reads, so a user starts a
repository from what the hub already holds: an agent becomes
``agents/<id>.md`` (plus ``<id>.capabilities.md`` / ``<id>.usage.md`` when it
has them), an environment, a memory pool and a deployment a YAML file each.
Values a fresh record has anyway are left out, so a file says what is
particular about its resource. A reference between two exported records is
written by declared key; one to a record not exported keeps its hub id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from declarative.kinds import KINDS, AgentKind, Context, DeploymentKind, EnvironmentKind

#: Spellings ``--export`` accepts in front of a hub id.
KIND_ALIASES = {
    "agent": "agent", "environment": "environment", "env": "environment",
    "memory_pool": "memory_pool", "pool": "memory_pool", "memory": "memory_pool",
    "deployment": "deployment", "job": "deployment",
}
#: Folder each kind is written into.
FOLDERS = {"agent": "agents", "environment": "environments", "memory_pool": "memory",
           "deployment": "deployments"}
#: The proactive profile of an agent nobody configured (proactive/profile.py).
PROACTIVE_DEFAULTS: Dict[str, Any] = {
    "enabled": False, "interval_minutes": 60, "cron": "", "timezone": "UTC",
    "quiet_hours": {"from": "", "to": ""}, "daily_budget_usd": 0.0, "max_runs_per_day": 0,
    "tick_budget_usd": None, "environment_id": None, "brief": "", "triggers": [],
    "notify": ["dashboard"], "workspace": None, "auto_pause_after": 3,
}


@dataclass
class Exported:
    """What an export wrote: the main file of each resource and the hub id
    behind each declared address."""
    files: List[Path] = field(default_factory=list)
    ids: Dict[str, str] = field(default_factory=dict)


def parse_ref(raw: str) -> Tuple[str, str]:
    """``"researcher"`` is an agent; ``"environment:<id>"`` (or ``env:``,
    ``pool:``, ``memory_pool:``, ``deployment:``, ``job:``) names another kind."""
    text = str(raw).strip()
    if ":" in text:
        head, _, rest = text.partition(":")
        kind = KIND_ALIASES.get(head.strip().lower())
        if kind is not None and rest.strip():
            return kind, rest.strip()
    return "agent", text


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return value or "resource"


def _dump_yaml(data: Dict[str, Any]) -> str:
    import yaml

    class Dumper(yaml.SafeDumper):
        pass

    def text(dumper, value):
        style = "|" if "\n" in value else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    Dumper.add_representer(str, text)
    return yaml.dump(data, Dumper=Dumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=100)


def _trim(value: Any, default: Any) -> Any:
    """``value`` without what equals ``default``; None when nothing is left."""
    if isinstance(value, dict) and isinstance(default, dict):
        out = {k: v for k, v in value.items() if k not in default or default[k] != v}
        return out or None
    return None if value == default else value


def _agent_file(observed: Dict[str, Any], hub_id: str, key_of: Callable[[str, str], str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {"kind": "agent", "id": hub_id}
    if observed.get("name") and observed["name"] != hub_id:
        data["name"] = observed["name"]
    order = ["description"] + [f.name for f in AgentKind.fields if f.name != "description"]
    for name in order:
        if name in ("name", "instructions", "capabilities", "usage") or name not in observed:
            continue
        value = observed[name]
        if name == "memory":
            value = [({"pool": key_of("memory_pool", e["id"]), "read_only": True} if e["read_only"]
                      else key_of("memory_pool", e["id"])) for e in value]
        elif name == "skills":
            value = [{"name": n, **{k: v for k, v in s.items() if v}} for n, s in sorted(value.items())]
        elif name == "model":
            value = (_trim(value, AgentKind.DEFAULTS["model"]) or None
                     if value.get("provider") != "inherit" else None)
        elif name == "proactive":
            value = _trim(value, PROACTIVE_DEFAULTS) if value.get("enabled") else None
        if name in AgentKind.DEFAULTS and name not in ("model", "proactive", "memory", "skills"):
            value = _trim(value, AgentKind.DEFAULTS[name])
        if value in (None, [], {}, ""):
            continue
        data[name] = value
    return data


def _environment_file(observed: Dict[str, Any], key: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"kind": "environment", "id": key}
    if observed.get("name") != key:
        data["name"] = observed.get("name")
    for f in EnvironmentKind.fields:
        if f.name == "name":
            continue
        value = _trim(observed.get(f.name), EnvironmentKind.DEFAULTS.get(f.name))
        if value not in (None, [], {}, ""):
            data[f.name] = value
    return data


def _pool_file(observed: Dict[str, Any], key: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"kind": "memory_pool", "id": key}
    if observed.get("name") != key:
        data["name"] = observed.get("name")
    for name, default in (("description", ""), ("type", "text"), ("workspace", None)):
        if observed.get(name) not in (default, None, ""):
            data[name] = observed[name]
    blocks = {n: b["value"] for n, b in (observed.get("blocks") or {}).items() if b and b.get("value")}
    if blocks:
        data["blocks"] = blocks
    return data


def _deployment_file(observed: Dict[str, Any], key: str, key_of: Callable[[str, str], str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {"kind": "deployment", "id": key}
    if observed.get("title") != key:
        data["title"] = observed.get("title")
    recurring = observed.get("recurrence") != "none"
    for f in DeploymentKind.fields:
        name = f.name
        if name == "title":
            continue
        value = observed.get(name)
        if name == "recurrence" and value == ("cron" if observed.get("cron") else "none"):
            continue
        if name == "run_at":
            if observed.get("recurrence") == "cron":
                continue
            data[name] = value
            continue
        if name in ("agent", "environment", "consolidate_pool") and value:
            value = key_of(f.ref or "", value)
        elif name == "memory_pools":
            value = [key_of("memory_pool", v) for v in value or []]
        value = _trim(value, DeploymentKind.DEFAULTS.get(name))
        if value in (None, [], {}, ""):
            continue
        data[name] = value
    if not recurring and "run_at" not in data:
        data["run_at"] = observed.get("run_at")
    return data


def export(request: Callable[..., Any], refs: List[str], out_dir: Path, *,
           workspace: Optional[str] = None) -> Exported:
    """Write each referenced hub record into ``out_dir``. Raises ValueError
    naming a reference the hub does not have; writes nothing in that case."""
    ctx = Context(request, workspace)
    wanted = [parse_ref(r) for r in refs]
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    missing: List[str] = []
    for kind, hub_id in wanted:
        if (kind, hub_id) in seen:
            continue
        observed = KINDS[kind].observe(ctx, hub_id, None)
        if observed is None:
            missing.append(f"{kind} '{hub_id}'")
        else:
            seen[(kind, hub_id)] = observed
    if missing:
        raise ValueError("not in the hub: " + ", ".join(missing))

    keys: Dict[Tuple[str, str], str] = {}
    taken: Dict[str, set] = {}
    for (kind, hub_id), observed in seen.items():
        if kind == "agent":
            key = hub_id
        else:
            key = slug(observed.get("title") if kind == "deployment" else observed.get("name"))
        base, n = key, 2
        while key in taken.setdefault(kind, set()):
            key, n = f"{base}-{n}", n + 1
        taken[kind].add(key)
        keys[(kind, hub_id)] = key

    def key_of(kind: str, hub_id: str) -> str:
        return keys.get((kind, hub_id), hub_id)

    out = Exported()
    out_dir = Path(out_dir)
    for (kind, hub_id), observed in seen.items():
        key = keys[(kind, hub_id)]
        folder = out_dir / FOLDERS[kind]
        folder.mkdir(parents=True, exist_ok=True)
        if kind == "agent":
            front = _agent_file(observed, hub_id, key_of)
            path = folder / f"{key}.md"
            body = observed.get("instructions") or ""
            path.write_text(f"---\n{_dump_yaml(front)}---\n\n{body}\n", encoding="utf-8")
            for part in ("capabilities", "usage"):
                part_path = folder / f"{key}.{part}.md"
                if observed.get(part):
                    part_path.write_text(observed[part] + "\n", encoding="utf-8")
                elif part_path.exists():
                    part_path.unlink()
        else:
            if kind == "environment":
                data = _environment_file(observed, key)
            elif kind == "memory_pool":
                data = _pool_file(observed, key)
            else:
                data = _deployment_file(observed, key, key_of)
            path = folder / f"{key}.yaml"
            path.write_text(_dump_yaml(data), encoding="utf-8")
        out.files.append(path)
        out.ids[f"{kind}/{key}"] = hub_id
    return out


__all__ = ["export", "Exported", "parse_ref", "slug", "KIND_ALIASES"]
