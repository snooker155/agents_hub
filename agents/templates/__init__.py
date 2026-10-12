"""Agent templates: one ready agent to start from in the create wizard.

Modelled on the starter agents Anthropic offers for Claude Managed Agents
(an SRE incident responder, a teammate in the team chat, an issue to pull
request agent, a data analyst, invoice processing, contract review): each is
a careful prompt, a tool set that passes the capability guard, the
connectors it works best with, a default outcome rubric and a short "what to
do next" note. A kit (``kits/``) is a team of agents installed as a bundle;
a template is a single agent the person names and adjusts before creating.

One file per template, ``agents/templates/<id>.md``: YAML frontmatter, then
the system prompt as the body. Frontmatter fields: ``id`` (must match the
file name), ``name``, ``description``, ``category``, ``icon`` (one plain
word), ``tools``, ``connectors: {required: [...], optional: [...]}`` naming
connectors and channels the way kits do, ``outcome`` (``rubric``,
``max_iterations``, ``threshold``: an agent default outcome,
docs/outcomes.md), ``starters`` (example first messages) and
``next_steps``. Creating an agent from one is ``POST
/api/agent-templates/{id}/create`` (dashboard/backend/routes/agent_templates.py);
see docs/agent-templates.md.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from declarative.parser import split_frontmatter

log = logging.getLogger(__name__)

#: ``agents/templates/<id>.md`` files live next to this one.
TEMPLATES_ROOT = Path(__file__).resolve().parent

#: Frontmatter fields every template must set.
REQUIRED_FIELDS = ("id", "name", "description", "tools")


class TemplateError(ValueError):
    """A template file that cannot be read."""


@dataclass(frozen=True)
class AgentTemplate:
    id: str
    name: str
    description: str
    system_prompt: str
    tools: List[str]
    category: str = "General"
    icon: str = "sparkles"
    domain: str = "general"
    connectors_required: List[str] = field(default_factory=list)
    connectors_optional: List[str] = field(default_factory=list)
    outcome: Optional[Dict[str, Any]] = None
    starters: List[str] = field(default_factory=list)
    next_steps: str = ""

    def to_dict(self, *, with_prompt: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "icon": self.icon,
            "domain": self.domain,
            "tools": list(self.tools),
            "connectors": {"required": list(self.connectors_required),
                           "optional": list(self.connectors_optional)},
            "outcome": dict(self.outcome) if self.outcome else None,
            "starters": list(self.starters),
            "next_steps": self.next_steps,
        }
        if with_prompt:
            out["system_prompt"] = self.system_prompt
        return out


def _str_list(value: Any) -> List[str]:
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _read(path: Path) -> AgentTemplate:
    split = split_frontmatter(path.read_text(encoding="utf-8"))
    if split is None:
        raise TemplateError(f"{path}: no frontmatter")
    head, body, _ = split
    try:
        data = yaml.safe_load(head) or {}
    except yaml.YAMLError as exc:
        raise TemplateError(f"{path}: invalid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"{path}: frontmatter must be a mapping")
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        raise TemplateError(f"{path}: missing {', '.join(missing)}")
    template_id = str(data["id"]).strip()
    if template_id != path.stem:
        raise TemplateError(f"{path}: id '{template_id}' must match the file name '{path.stem}'")
    if not body.strip():
        raise TemplateError(f"{path}: the body (system prompt) is empty")
    connectors = data.get("connectors") or {}
    if not isinstance(connectors, dict):
        raise TemplateError(f"{path}: connectors must be a mapping of required/optional")
    outcome = data.get("outcome")
    if outcome is not None and not isinstance(outcome, dict):
        raise TemplateError(f"{path}: outcome must be a mapping")
    return AgentTemplate(
        id=template_id,
        name=str(data["name"]).strip(),
        description=" ".join(str(data["description"]).split()),
        system_prompt=body.strip() + "\n",
        tools=_str_list(data.get("tools")),
        category=str(data.get("category") or "General").strip(),
        icon=str(data.get("icon") or "sparkles").strip() or "sparkles",
        domain=str(data.get("domain") or "general").strip() or "general",
        connectors_required=_str_list(connectors.get("required")),
        connectors_optional=_str_list(connectors.get("optional")),
        outcome=dict(outcome) if outcome else None,
        starters=_str_list(data.get("starters")),
        next_steps=" ".join(str(data.get("next_steps") or "").split()),
    )


def list_templates() -> List[AgentTemplate]:
    """Every template, in the order the files sort. A file that does not
    read is logged and skipped, so one bad template never empties the list."""
    out: List[AgentTemplate] = []
    for path in sorted(TEMPLATES_ROOT.glob("*.md")):
        try:
            out.append(_read(path))
        except TemplateError:
            log.warning("agent templates: skipping %s", path.name, exc_info=True)
    return out


def get_template(template_id: str) -> Optional[AgentTemplate]:
    """One template by id, or None when there is no such file."""
    template_id = (template_id or "").strip()
    if not template_id or "/" in template_id or "\\" in template_id or ".." in template_id:
        return None
    path = TEMPLATES_ROOT / f"{template_id}.md"
    if not path.is_file():
        return None
    return _read(path)


def free_agent_id(base: str, taken) -> str:
    """``base``, or ``base_2``, ``base_3``... when an agent already has it.
    ``taken`` is a predicate: does an agent with this id exist."""
    if not taken(base):
        return base
    n = 2
    while taken(f"{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def validate_template(template: AgentTemplate) -> List[str]:
    """Problems beyond what reading checks: an unknown tool, an unknown
    connector, a tool set the capability guard would refuse, or an outcome
    the task outcome rules reject. Empty means the template is sound."""
    problems: List[str] = []
    import kits
    from tools.capabilities import check_combination
    from tasks.outcome import OutcomeError, normalize_outcome

    tool_ids = kits.known_tool_ids()
    for tool in template.tools:
        if tool not in tool_ids:
            problems.append(f"{template.id}: unknown tool '{tool}'")
    names = kits.known_connector_names()
    for name in template.connectors_required + template.connectors_optional:
        if name not in names:
            problems.append(f"{template.id}: unknown connector or channel '{name}'")
    violation = check_combination(template.tools)
    if violation is not None and violation.blocking:
        problems.append(f"{template.id}: tool set forms '{violation.rule_id}'")
    if template.outcome:
        try:
            normalize_outcome(dict(template.outcome))
        except OutcomeError as exc:
            problems.append(f"{template.id}: outcome {exc}")
    return problems


__all__ = [
    "AgentTemplate", "TemplateError", "TEMPLATES_ROOT", "list_templates",
    "get_template", "free_agent_id", "validate_template",
]
