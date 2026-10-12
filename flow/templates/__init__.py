"""Flow templates: a ready flow to start from in the Create flow dialog.

The flow counterpart of agent templates (``agents/templates/``): where an
agent template is one agent, a flow template is a small pipeline of the
hub's system agents (researcher, writer, verifier, screener, ...) with a task
written for every step, so it runs as soon as it is created. Every template
also carries six example requests per interface language, copied into the
flow as its ``examples``; the flow's chat shows them while it is empty
(``GET /api/flows/{id}/examples``).

One file per template, ``flow/templates/<id>.yaml``::

    id: research_report            # must match the file name
    name: Research report
    description: ...
    category: Research
    icon: search                   # one plain word, the picker maps it
    examples: {en: [...], ru: [...], de: [...]}
    nodes:
      - key: research              # unique within the template
        agent: researcher          # a registered agent id
        label: {en: Research, ru: Исследование, de: Recherche}
        task: |                    # the step's own task (data.nodeTask)
          ...
    edges:
      - {from: research, to: write}

A node with no incoming edge receives the person's message; every other node
receives its predecessors' output only (flow/engine.py build_agent_input), so
first steps are told to restate the request at the top of what they hand on.
Creating a flow from a template is ``POST /api/flow-templates/{id}/create``
(dashboard/backend/routes/flow_templates.py); see docs/flow-templates.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger(__name__)

#: ``flow/templates/<id>.yaml`` files live next to this one.
TEMPLATES_ROOT = Path(__file__).resolve().parent

#: Interface languages every template gives examples (and labels) in.
LANGUAGES = ("en", "ru", "de")

#: Example requests per language a shipped template carries.
EXAMPLES_PER_LANGUAGE = 6

#: Horizontal and vertical spacing of the generated canvas layout.
_COLUMN_WIDTH = 240
_ROW_HEIGHT = 150


class TemplateError(ValueError):
    """A template file that cannot be read."""


@dataclass(frozen=True)
class TemplateNode:
    key: str
    agent: str
    label: Dict[str, str]
    task: str
    description: str = ""


@dataclass(frozen=True)
class FlowTemplate:
    id: str
    name: str
    description: str
    nodes: List[TemplateNode]
    edges: List[tuple]
    category: str = "General"
    icon: str = "workflow"
    examples: Dict[str, List[str]] = field(default_factory=dict)

    def agents(self) -> List[str]:
        """The agents the template's steps run, in step order, once each."""
        out: List[str] = []
        for node in self.nodes:
            if node.agent not in out:
                out.append(node.agent)
        return out

    def to_dict(self, lang: str = "en") -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "agents": self.agents(),
            "steps": [
                {"key": n.key, "agent": n.agent, "label": _pick(n.label, lang)}
                for n in self.nodes
            ],
            "edges": [{"from": s, "to": t} for s, t in self.edges],
            "examples": examples_for(self.examples, lang),
        }


def _pick(by_lang: Dict[str, str], lang: str) -> str:
    return by_lang.get(lang) or by_lang.get("en") or next(iter(by_lang.values()), "")


def _str_list(value: Any) -> List[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def normalize_examples(value: Any) -> Dict[str, List[str]]:
    """A flow's ``examples`` as ``{lang: [text, ...]}``.

    Accepts the stored dict, or a plain list (the same requests for every
    language, as a hand-written YAML or an API caller may give). Empty
    languages are dropped.
    """
    if isinstance(value, list):
        items = _str_list(value)
        return {"en": items} if items else {}
    if not isinstance(value, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for lang, items in value.items():
        cleaned = _str_list(items if isinstance(items, list) else [items])
        if cleaned:
            out[str(lang).strip().lower()] = cleaned
    return out


def examples_for(value: Any, lang: str) -> List[str]:
    """The examples in ``lang``, else English, else the first language set."""
    by_lang = normalize_examples(value)
    if not by_lang:
        return []
    lang = (lang or "en").split("-")[0].lower()
    return by_lang.get(lang) or by_lang.get("en") or next(iter(by_lang.values()))


def _label(value: Any, fallback: str) -> Dict[str, str]:
    if isinstance(value, dict):
        out = {str(k): str(v).strip() for k, v in value.items() if str(v or "").strip()}
        return out or {"en": fallback}
    text = str(value or "").strip()
    return {"en": text or fallback}


def _read(path: Path) -> FlowTemplate:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise TemplateError(f"{path}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"{path}: must be a mapping")
    missing = [f for f in ("id", "name", "description", "nodes") if not data.get(f)]
    if missing:
        raise TemplateError(f"{path}: missing {', '.join(missing)}")
    template_id = str(data["id"]).strip()
    if template_id != path.stem:
        raise TemplateError(f"{path}: id '{template_id}' must match the file name '{path.stem}'")

    nodes: List[TemplateNode] = []
    for i, raw in enumerate(data.get("nodes") or []):
        if not isinstance(raw, dict):
            raise TemplateError(f"{path}: node {i} must be a mapping")
        key = str(raw.get("key") or "").strip()
        agent = str(raw.get("agent") or "").strip()
        if not key or not agent:
            raise TemplateError(f"{path}: node {i} needs a key and an agent")
        if any(n.key == key for n in nodes):
            raise TemplateError(f"{path}: duplicate node key '{key}'")
        nodes.append(TemplateNode(
            key=key,
            agent=agent,
            label=_label(raw.get("label"), key),
            task=str(raw.get("task") or "").strip(),
            description=" ".join(str(raw.get("description") or "").split()),
        ))

    keys = {n.key for n in nodes}
    edges: List[tuple] = []
    for raw in data.get("edges") or []:
        if not isinstance(raw, dict):
            raise TemplateError(f"{path}: an edge must be a mapping of from/to")
        src, dst = str(raw.get("from") or ""), str(raw.get("to") or "")
        if src not in keys or dst not in keys:
            raise TemplateError(f"{path}: edge {src} -> {dst} names an unknown step")
        edges.append((src, dst))

    return FlowTemplate(
        id=template_id,
        name=str(data["name"]).strip(),
        description=" ".join(str(data["description"]).split()),
        nodes=nodes,
        edges=edges,
        category=str(data.get("category") or "General").strip(),
        icon=str(data.get("icon") or "workflow").strip() or "workflow",
        examples=normalize_examples(data.get("examples")),
    )


def list_templates() -> List[FlowTemplate]:
    """Every template, in the order the files sort. A file that does not
    read is logged and skipped, so one bad template never empties the list."""
    out: List[FlowTemplate] = []
    for path in sorted(TEMPLATES_ROOT.glob("*.yaml")):
        try:
            out.append(_read(path))
        except TemplateError:
            log.warning("flow templates: skipping %s", path.name, exc_info=True)
    return out


def get_template(template_id: str) -> Optional[FlowTemplate]:
    """One template by id, or None when there is no such file."""
    template_id = (template_id or "").strip()
    if not template_id or "/" in template_id or "\\" in template_id or ".." in template_id:
        return None
    path = TEMPLATES_ROOT / f"{template_id}.yaml"
    if not path.is_file():
        return None
    return _read(path)


def _layout(template: FlowTemplate) -> Dict[str, Dict[str, int]]:
    """Left to right by depth (longest path from a first step), steps of the
    same depth stacked and centred on the row of the deepest column."""
    preds: Dict[str, List[str]] = {n.key: [] for n in template.nodes}
    for src, dst in template.edges:
        preds[dst].append(src)
    depth: Dict[str, int] = {}

    def depth_of(key: str, seen: frozenset = frozenset()) -> int:
        if key in depth:
            return depth[key]
        if key in seen:  # a cycle; validation reports it
            return 0
        d = max((depth_of(p, seen | {key}) + 1 for p in preds[key]), default=0)
        depth[key] = d
        return d

    columns: Dict[int, List[str]] = {}
    for node in template.nodes:
        columns.setdefault(depth_of(node.key), []).append(node.key)
    tallest = max((len(c) for c in columns.values()), default=1)
    positions: Dict[str, Dict[str, int]] = {}
    for col, keys in columns.items():
        offset = (tallest - len(keys)) * _ROW_HEIGHT // 2
        for row, key in enumerate(keys):
            positions[key] = {"x": 80 + col * _COLUMN_WIDTH, "y": 100 + offset + row * _ROW_HEIGHT}
    return positions


def build_flow(template: FlowTemplate, flow_id: str, *, lang: str = "en") -> Dict[str, Any]:
    """The combined flow document (the shape ``flow.store`` persists) for a
    new flow made from ``template``: step labels in ``lang``, each step's
    task as its ``nodeTask``, the examples in every language. Name,
    description, workspace and timestamps are the caller's to set."""
    positions = _layout(template)
    ids = {n.key: f"node-{n.key}" for n in template.nodes}
    nodes = [{
        "id": ids[n.key],
        "type": "flowNode",
        "position": positions[n.key],
        "data": {
            "label": _pick(n.label, lang),
            "agent_id": n.agent,
            "category": "agent",
            "description": n.description,
            "nodeTask": n.task,
        },
    } for n in template.nodes]
    edges = [{
        "id": f"edge-{src}-{dst}",
        "source": ids[src],
        "target": ids[dst],
        "type": "smoothstep",
    } for src, dst in template.edges]
    return {
        "id": flow_id,
        "name": template.name,
        "description": template.description,
        "nodes": nodes,
        "edges": edges,
        "examples": {k: list(v) for k, v in template.examples.items()},
        "template": template.id,
    }


def shipped_agent_ids() -> set:
    """Ids of the System agents the hub ships (``bootstrap/agents.json``):
    what a template may run, since ``ensure_agents`` can register any of
    them on demand."""
    import json
    from common.bootstrap import BOOTSTRAP_AGENTS_FILE
    try:
        raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(a.get("id")) for a in raw.get("agents") or [] if isinstance(a, dict) and a.get("id")}


def ensure_agents(template: FlowTemplate) -> List[str]:
    """Register every System agent the template runs that is missing from
    this hub (``common.bootstrap.ensure_system_agent``), so a flow made from
    it runs at once. Returns the agents still missing afterwards."""
    from common.bootstrap import ensure_system_agent
    return [a for a in template.agents() if not ensure_system_agent(a)]


def validate_template(template: FlowTemplate) -> List[str]:
    """Problems beyond what reading checks: an agent that is not registered,
    a graph the engine's preflight rejects, or examples that are missing,
    short or repeated in a language. Empty means the template is sound."""
    problems: List[str] = []
    from agents.registry import get_agent
    from flow.validate import FlowValidationError, validate_flow

    shipped = shipped_agent_ids()
    for agent in template.agents():
        if agent not in shipped and get_agent(agent) is None:
            problems.append(f"{template.id}: unknown agent '{agent}'")
    for node in template.nodes:
        if not node.task:
            problems.append(f"{template.id}: step '{node.key}' has no task")
    try:
        validate_flow(build_flow(template, f"template-{template.id}"))
    except FlowValidationError as exc:
        problems.extend(f"{template.id}: {e}" for e in exc.errors)
    for lang in LANGUAGES:
        items = template.examples.get(lang) or []
        if len(items) != EXAMPLES_PER_LANGUAGE or len(set(items)) != len(items):
            problems.append(
                f"{template.id}: needs {EXAMPLES_PER_LANGUAGE} distinct examples in '{lang}'"
            )
        for node in template.nodes:
            if lang not in node.label:
                problems.append(f"{template.id}: step '{node.key}' has no '{lang}' label")
    return problems


__all__ = [
    "FlowTemplate", "TemplateNode", "TemplateError", "TEMPLATES_ROOT", "LANGUAGES",
    "build_flow", "ensure_agents", "examples_for", "get_template", "list_templates",
    "normalize_examples", "shipped_agent_ids", "validate_template",
]
