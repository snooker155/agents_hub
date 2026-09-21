"""
Chat context references — service entities the user attaches to a message.

The composer's paperclip is not only a file picker. Next to "a file from my
computer" the user can point at something the hub already holds — a task, a
view, a project, a playground scenario, a loop, a flow, a team, an agent, a
scheduled job — and have it folded into the prompt for that one turn.

Two halves live here:

* the **catalog** (:data:`KINDS`) — per kind: how to list candidates for the
  picker, how to render one into prompt text, where its page is, and which agent
  tool (if any) can load it. The dashboard's ``/api/context`` routes are a thin
  shell over this.
* the **prompt block** (:func:`build_reference_lines`) — the rendered entities as
  ``=== Attached <noun> ===`` sections, the same shape chat attachments use.

Why render server-side instead of letting the agent fetch: an explicit pick has
to work even when the agent has no tool for that kind — a playground scenario or
a loop has none at all, so "ask the agent to load it" is not available. When the
agent *does* have a loading tool, the rendered block still names it with the id,
so the agent can pull fresher or deeper detail on its own — the way it already
does for tasks today.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

# Per-entity and whole-message budgets for the rendered blocks. A reference is a
# pointer the user chose, not a data dump: past these the block is truncated and
# the agent is told to use the loading tool (or the linked page) for the rest.
MAX_REFERENCE_CHARS = 12_000
MAX_TOTAL_REFERENCE_CHARS = 60_000
MAX_REFERENCES = 10
# How many candidates one picker query returns before the user must narrow it.
LIST_LIMIT = 60
_BUDGET_NOTE = "\n...[truncated — attached context budget reached]"


# ── rendering helpers ────────────────────────────────────────────────────────

def _fields(pairs: Dict[str, Any]) -> List[str]:
    """``key: value`` lines, skipping anything empty — a reference block should
    not spend the agent's attention on ``priority: None``."""
    out: List[str] = []
    for key, value in pairs.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        out.append(f"{key}: {value}")
    return out


def _prose(title: str, body: Any, limit: int = 4000) -> List[str]:
    """A titled prose section, or nothing when the body is empty."""
    text = str(body or "").strip()
    if not text:
        return []
    if len(text) > limit:
        text = text[:limit] + "\n...[truncated]"
    return ["", f"{title}:", text]


def _json_block(title: str, value: Any, limit: int = 4000) -> List[str]:
    """A titled JSON section for structured specs (view spec, env params)."""
    if value in (None, "", [], {}):
        return []
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + "\n...[truncated]"
    return ["", f"{title}:", "```json", text, "```"]


def _matches(query: str, *values: Any) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    return any(q in str(v or "").lower() for v in values)


def _item(entity_id: str, label: str, subtitle: str = "", **meta) -> Dict[str, Any]:
    """One picker row. ``subtitle`` is the dimmed second line in the list."""
    return {"id": str(entity_id), "label": label or str(entity_id), "subtitle": subtitle, **meta}


# ── task ─────────────────────────────────────────────────────────────────────

def _list_tasks(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from tasks.service import list_tasks
    from common.workspace_context import filter_tasks_for_project, filter_tasks_for_workspace

    tasks = filter_tasks_for_project(
        filter_tasks_for_workspace(list_tasks(), workspace), project_id
    )
    tasks.sort(key=lambda t: getattr(t, "updated_at", None) or getattr(t, "created_at", None), reverse=True)
    out = []
    for task in tasks:
        key = getattr(task, "key", None) or ""
        if not _matches(query, task.title, key, task.description, task.id):
            continue
        status = getattr(getattr(task, "status", None), "value", str(getattr(task, "status", "")))
        out.append(_item(
            str(task.id),
            f"{key} {task.title}".strip() if key else task.title,
            status,
        ))
    return out


def _render_task(entity_id: str) -> Optional[Dict[str, str]]:
    from uuid import UUID
    from tasks.service import get_task, get_task_result, get_subtasks

    try:
        task = get_task(UUID(entity_id))
    except Exception:
        return None
    if task is None:
        return None

    status = getattr(getattr(task, "status", None), "value", str(getattr(task, "status", "")))
    lines = _fields({
        "id": str(task.id),
        "key": getattr(task, "key", None),
        "title": task.title,
        "status": status,
        "priority": getattr(task, "priority", None),
        "project_id": getattr(task, "project_id", None),
        "workspace": getattr(task, "workspace", None),
        "assigned_agent": getattr(task, "assigned_agent_type", None),
        "blocked_reason": getattr(task, "blocked_reason", None),
    })
    lines += _prose("Description", getattr(task, "description", ""))
    try:
        subtasks = get_subtasks(task.id)
    except Exception:
        subtasks = []
    if subtasks:
        lines += ["", "Subtasks:"]
        lines += [
            f"- [{getattr(getattr(s, 'status', None), 'value', '')}] {s.title} ({s.id})"
            for s in subtasks[:30]
        ]
    try:
        result = get_task_result(task.id)
    except Exception:
        result = None
    lines += _prose("Latest result", result)
    return {"title": task.title or f"Task {entity_id[:8]}", "body": "\n".join(lines)}


# ── view ─────────────────────────────────────────────────────────────────────

def _list_views(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from views.store import list_views

    out = []
    for row in list_views(workspace or None, limit=200):
        if not _matches(query, row.get("title"), row.get("summary"), row.get("kind"), row.get("view_id")):
            continue
        out.append(_item(
            row.get("view_id"),
            (row.get("title") or "").strip() or "Untitled view",
            " · ".join(x for x in [row.get("kind"), (row.get("summary") or "")[:80]] if x),
        ))
    return out


def _render_view(entity_id: str) -> Optional[Dict[str, str]]:
    from views.store import get_view

    env = get_view(entity_id)
    if not env:
        return None
    lines = _fields({
        "view_id": entity_id,
        "kind": env.get("kind"),
        "title": env.get("title"),
        "workspace": env.get("workspace"),
        "assets": env.get("assets"),
    })
    lines += _prose("Summary", env.get("summary"))
    lines += _json_block("Spec", env.get("spec"), limit=6000)
    lines += _json_block("Data", env.get("data"), limit=2000)
    lines += _json_block("Controls", env.get("controls"), limit=1500)
    return {"title": (env.get("title") or "").strip() or f"View {entity_id[:8]}", "body": "\n".join(lines)}


# ── project ──────────────────────────────────────────────────────────────────

def _project_store():
    from common.paths import PROJECTS_FILE
    from projects.storage import ProjectStore
    return ProjectStore(path=PROJECTS_FILE)


def _list_projects(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    out = []
    for project in _project_store().list():
        if workspace and project.workspace != workspace:
            continue
        if not _matches(query, project.name, project.description, project.id):
            continue
        status = getattr(getattr(project, "status", None), "value", "")
        ptype = getattr(getattr(project, "type", None), "value", "")
        out.append(_item(
            project.id, project.name,
            " · ".join(x for x in [ptype, status, project.workspace] if x),
        ))
    return out


def _render_project(entity_id: str) -> Optional[Dict[str, str]]:
    project = _project_store().get(entity_id)
    if project is None:
        return None
    repo = getattr(project, "repo", None)
    lines = _fields({
        "id": project.id,
        "name": project.name,
        "type": getattr(getattr(project, "type", None), "value", ""),
        "status": getattr(getattr(project, "status", None), "value", ""),
        "workspace": project.workspace,
        "tags": getattr(project, "tags", None),
        "repo": getattr(getattr(repo, "type", None), "value", "") if repo else "",
        "repo_url": getattr(repo, "url", None) if repo else None,
        "repo_branch": getattr(repo, "branch", None) if repo else None,
    })
    lines += _prose("Description", getattr(project, "description", ""))

    # A project's open work is the part an agent most often needs alongside it.
    try:
        from tasks.service import list_tasks
        from common.workspace_context import filter_tasks_for_project
        tasks = filter_tasks_for_project(list_tasks(), entity_id)
    except Exception:
        tasks = []
    if tasks:
        lines += ["", f"Tasks ({len(tasks)}):"]
        lines += [
            f"- [{getattr(getattr(t, 'status', None), 'value', '')}] "
            f"{(getattr(t, 'key', None) or '')} {t.title}".rstrip()
            for t in tasks[:40]
        ]
    return {"title": project.name or f"Project {entity_id[:8]}", "body": "\n".join(lines)}


# ── playground scenario ──────────────────────────────────────────────────────

def _list_scenarios(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from playground.store import list_scenarios

    out = []
    for scenario in list_scenarios(workspace or None):
        if not _matches(query, scenario.name, scenario.description, scenario.environment, scenario.scenario_id):
            continue
        out.append(_item(
            scenario.scenario_id,
            scenario.name or scenario.scenario_id,
            " · ".join(x for x in [scenario.environment, f"{len(scenario.roles)} roles"] if x),
        ))
    return out


def _render_scenario(entity_id: str) -> Optional[Dict[str, str]]:
    from playground.store import get_scenario, list_sim_runs

    scenario = get_scenario(entity_id)
    if scenario is None:
        return None
    lines = _fields({
        "scenario_id": scenario.scenario_id,
        "name": scenario.name,
        "environment": scenario.environment,
        "workspace": scenario.workspace,
        "activation": scenario.activation,
        "max_ticks": scenario.max_ticks,
        "seed": scenario.seed,
        "default_model": scenario.default_model,
        "default_provider": scenario.default_provider,
    })
    lines += _prose("Description", scenario.description)
    if scenario.roles:
        lines += ["", "Roles:"]
        for role in scenario.roles:
            lines.append(
                f"- {role.name or role.agent_id} ({role.role or 'no role'}) — "
                f"agent={role.agent_id or '-'}; goal={role.goal or '-'}"
            )
    lines += _json_block("Environment params", scenario.env_params, limit=2000)
    try:
        runs = list_sim_runs(scenario_id=entity_id, limit=5)
    except Exception:
        runs = []
    if runs:
        lines += ["", "Recent runs:"]
        lines += [
            f"- {r.sim_run_id} status={r.status} ticks={getattr(r, 'ticks_done', '')}"
            for r in runs
        ]
    return {"title": scenario.name or f"Scenario {entity_id[:8]}", "body": "\n".join(lines)}


# ── loop ─────────────────────────────────────────────────────────────────────

def _list_loops(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from loops.store import list_loops

    out = []
    for loop in list_loops(workspace or None):
        if not _matches(query, loop.name, loop.description, loop.exit_criterion, loop.loop_id):
            continue
        out.append(_item(
            loop.loop_id,
            loop.name or loop.loop_id,
            " · ".join(x for x in [f"flow={loop.flow_id}" if loop.flow_id else "",
                                   f"max {loop.max_iterations} iterations"] if x),
        ))
    return out


def _render_loop(entity_id: str) -> Optional[Dict[str, str]]:
    from loops.store import get_loop, list_runs

    loop = get_loop(entity_id)
    if loop is None:
        return None
    lines = _fields({
        "loop_id": loop.loop_id,
        "name": loop.name,
        "workspace": loop.workspace,
        "flow_id": loop.flow_id,
        "max_iterations": loop.max_iterations,
        "min_iterations": loop.min_iterations,
        "target_score": loop.target_score,
        "patience": loop.patience,
        "evaluator_mode": loop.evaluator_mode,
        "evaluator_agent_id": loop.evaluator_agent_id,
    })
    lines += _prose("Description", loop.description)
    lines += _prose("Exit criterion", loop.exit_criterion)
    try:
        runs = list_runs(loop_id=entity_id, limit=5)
    except Exception:
        runs = []
    if runs:
        lines += ["", "Recent runs:"]
        lines += [
            f"- {r.loop_run_id} status={r.status} iterations={r.iterations_done} "
            f"best_score={r.best_score} stop_reason={r.stop_reason or '-'}"
            for r in runs
        ]
    return {"title": loop.name or f"Loop {entity_id[:8]}", "body": "\n".join(lines)}


# ── flow ─────────────────────────────────────────────────────────────────────

def _list_flows(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from flow.store import list_flows

    # Visibility matches the /api/flows listing: an explicit ``allowed_flows``
    # allowlist on the workspace is authoritative, otherwise global + own flows.
    allowed = None
    if workspace:
        try:
            from workspace.storage import get_workspace_metadata
            allowed = get_workspace_metadata(workspace).get("allowed_flows")
        except Exception:
            allowed = None

    out = []
    for flow in list_flows():
        flow_id = flow.get("id") or flow.get("flow_id") or ""
        if allowed is not None:
            if flow_id not in allowed:
                continue
        elif workspace and flow.get("workspace") not in (None, "", workspace):
            continue
        if not _matches(query, flow.get("name"), flow.get("description"), flow_id):
            continue
        nodes = flow.get("nodes") or []
        out.append(_item(
            flow_id,
            (flow.get("name") or "").strip() or flow_id,
            f"{len(nodes)} nodes",
        ))
    return out


def _render_flow(entity_id: str) -> Optional[Dict[str, str]]:
    from flow.store import get_flow

    flow = get_flow(entity_id)
    if not flow:
        return None
    nodes = flow.get("nodes") or []
    edges = flow.get("edges") or []
    lines = _fields({
        "flow_id": entity_id,
        "name": flow.get("name"),
        "workspace": flow.get("workspace"),
        "nodes": len(nodes),
        "edges": len(edges),
    })
    lines += _prose("Description", flow.get("description"))
    if nodes:
        lines += ["", "Nodes:"]
        for node in nodes[:40]:
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            label = node.get("label") or data.get("label") or node.get("id")
            agent_id = node.get("agent_id") or data.get("agent_id") or ""
            lines.append(f"- {node.get('id')}: {label}" + (f" (agent={agent_id})" if agent_id else ""))
    if edges:
        lines += ["", "Edges:"]
        lines += [f"- {e.get('source')} → {e.get('target')}" for e in edges[:60]]
    return {"title": (flow.get("name") or "").strip() or f"Flow {entity_id[:8]}", "body": "\n".join(lines)}


# ── team ─────────────────────────────────────────────────────────────────────

def _list_teams(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from teams import store as team_store

    out = []
    for team in team_store.list_teams(workspace or None):
        if not _matches(query, team.name, team.description, team.team_id):
            continue
        out.append(_item(
            team.team_id, team.name or team.team_id,
            " · ".join(x for x in [team.mode, f"{len(team.members)} members"] if x),
        ))
    return out


def _render_team(entity_id: str) -> Optional[Dict[str, str]]:
    from teams import store as team_store

    team = team_store.get_team(entity_id)
    if team is None:
        return None
    lines = _fields({
        "team_id": team.team_id,
        "name": team.name,
        "mode": team.mode,
        "workspace": team.workspace,
        "leader": team.leader_agent_id,
        "max_rounds": team.max_rounds,
    })
    lines += _prose("Description", team.description)
    lines += _prose("Charter", team.charter)
    if team.members:
        lines += ["", "Members:"]
        lines += [
            f"- {m.display_name()} (agent={m.agent_id}) — {m.role or 'no role'}"
            for m in team.members
        ]
    return {"title": team.name or f"Team {entity_id[:8]}", "body": "\n".join(lines)}


# ── agent ────────────────────────────────────────────────────────────────────

def _list_agents(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from agents.registry import list_agents
    from common.workspace_context import filter_agents_for_workspace

    out = []
    for spec in filter_agents_for_workspace(list_agents(), workspace):
        if not _matches(query, spec.name, getattr(spec, "description", ""), spec.id):
            continue
        out.append(_item(
            spec.id,
            getattr(spec, "name", "") or spec.id,
            (getattr(spec, "description", "") or "")[:90],
        ))
    return out


def _render_agent(entity_id: str) -> Optional[Dict[str, str]]:
    from agents.registry import get_agent

    spec = get_agent(entity_id)
    if spec is None:
        return None
    lines = _fields({
        "agent_id": spec.id,
        "name": getattr(spec, "name", ""),
        "provider": getattr(spec, "provider", ""),
        "model": getattr(spec, "model", ""),
        "tools": list(getattr(spec, "tools", None) or []),
    })
    lines += _prose("Description", getattr(spec, "description", ""))
    return {"title": getattr(spec, "name", "") or entity_id, "body": "\n".join(lines)}


# ── scheduled job ────────────────────────────────────────────────────────────

def _list_jobs(workspace: Optional[str], project_id: Optional[str], query: str) -> List[Dict[str, Any]]:
    from plans.service import list_jobs

    out = []
    for job in list_jobs(workspace or None):
        if not _matches(query, job.title, str(job.id)):
            continue
        status = getattr(getattr(job, "status", None), "value", "")
        kind = getattr(getattr(job, "kind", None), "value", "")
        out.append(_item(str(job.id), job.title, " · ".join(x for x in [kind, status] if x)))
    return out


def _render_job(entity_id: str) -> Optional[Dict[str, str]]:
    from plans.service import get_job

    job = get_job(entity_id)
    if job is None:
        return None
    lines = _fields({
        "id": str(job.id),
        "title": job.title,
        "kind": getattr(getattr(job, "kind", None), "value", ""),
        "status": getattr(getattr(job, "status", None), "value", ""),
        "run_at": getattr(job, "run_at", None),
        "recurrence": getattr(getattr(job, "recurrence", None), "value", ""),
        "workspace": getattr(job, "workspace", None),
        "agent_id": getattr(job, "agent_id", None),
        "flow_id": getattr(job, "flow_id", None),
    })
    lines += _prose("Message", getattr(job, "message", ""))
    return {"title": job.title or f"Job {entity_id[:8]}", "body": "\n".join(lines)}


# ── the catalog ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RefKind:
    """One attachable entity kind.

    ``tools`` / ``tool_groups`` name the agent tools that can load this kind at
    runtime. They do not gate the attachment — a reference is always rendered
    into the prompt — they only decide whether the block also tells the agent
    how to fetch more (see :func:`build_reference_lines`).
    """
    kind: str
    noun: str
    icon: str
    list_fn: Callable[[Optional[str], Optional[str], str], List[Dict[str, Any]]]
    render_fn: Callable[[str], Optional[Dict[str, str]]]
    path_fn: Callable[[str], str]
    tools: tuple = ()
    tool_groups: tuple = ()
    # True when the listing already narrows by the selected workspace, so the
    # picker can say so instead of showing a misleadingly short list.
    workspace_scoped: bool = True


KINDS: Dict[str, RefKind] = {
    k.kind: k for k in [
        RefKind("task", "Task", "✅", _list_tasks, _render_task,
                lambda i: f"/tasks/{quote(i)}",
                tools=("get_task", "get_task_result"), tool_groups=("task_management",)),
        RefKind("view", "View", "📊", _list_views, _render_view,
                lambda i: f"/views/{quote(i)}",
                tools=("view_get",)),
        RefKind("project", "Project", "📁", _list_projects, _render_project,
                lambda i: f"/projects/{quote(i)}"),
        RefKind("scenario", "Scenario", "🎲", _list_scenarios, _render_scenario,
                lambda i: f"/playground/{quote(i)}"),
        RefKind("loop", "Loop", "🔁", _list_loops, _render_loop,
                lambda i: f"/loops?loop={quote(i)}"),
        RefKind("flow", "Flow", "🔀", _list_flows, _render_flow,
                lambda i: f"/flows/{quote(i)}",
                tools=("get_flow_tool",), tool_groups=("flow_management",)),
        RefKind("team", "Team", "👥", _list_teams, _render_team,
                lambda i: f"/teams/{quote(i)}"),
        RefKind("agent", "Agent", "🤖", _list_agents, _render_agent,
                lambda i: f"/agents/{quote(i)}",
                tools=("get_agent_tool",), tool_groups=("agent_management",)),
        RefKind("job", "Scheduled job", "⏰", _list_jobs, _render_job,
                lambda i: f"/plan?tab=jobs&job={quote(i)}",
                tools=("list_scheduled",), tool_groups=("schedule_management",)),
    ]
}


def kind_catalog() -> List[Dict[str, Any]]:
    """The picker's kind tabs: ``[{kind, noun, icon, workspace_scoped}, …]``."""
    return [
        {"kind": k.kind, "noun": k.noun, "icon": k.icon, "workspace_scoped": k.workspace_scoped}
        for k in KINDS.values()
    ]


def list_entities(
    kind: str,
    *,
    workspace: Optional[str] = None,
    project_id: Optional[str] = None,
    query: str = "",
    limit: int = LIST_LIMIT,
) -> List[Dict[str, Any]]:
    """Picker rows for one kind. An unknown kind raises ``KeyError``; a store that
    errors yields an empty list rather than a broken picker."""
    spec = KINDS[kind]
    try:
        items = spec.list_fn(workspace or None, project_id or None, query or "")
    except Exception:
        log.warning("context reference listing failed for kind=%s", kind, exc_info=True)
        return []
    for item in items:
        item.setdefault("kind", kind)
        item.setdefault("icon", spec.icon)
        item["url"] = spec.path_fn(item["id"])
    return items[:max(1, int(limit))]


def render_entity(kind: str, entity_id: str) -> Optional[Dict[str, str]]:
    """``{title, body}`` for one entity, or None when the kind is unknown, the
    entity is gone, or its store errored."""
    spec = KINDS.get(kind)
    if spec is None or not entity_id:
        return None
    try:
        rendered = spec.render_fn(str(entity_id))
    except Exception:
        log.warning("context reference render failed for %s:%s", kind, entity_id, exc_info=True)
        return None
    if not rendered:
        return None
    body = rendered.get("body") or ""
    if len(body) > MAX_REFERENCE_CHARS:
        body = body[:MAX_REFERENCE_CHARS] + "\n...[truncated — open the entity or use its tool for the rest]"
    return {"title": rendered.get("title") or str(entity_id), "body": body}


def agent_can_load(agent_id: Optional[str], kind: str) -> bool:
    """Whether ``agent_id`` holds a tool that can load this kind at runtime.

    Drives the "you can also call X" hint only. Tool lists are matched against
    both the individual tool names and the group aliases the factory expands
    (``task_management`` → ``get_task`` and friends).
    """
    spec = KINDS.get(kind)
    if spec is None or not agent_id or not (spec.tools or spec.tool_groups):
        return False
    try:
        from agents.registry import get_agent
        agent_spec = get_agent(agent_id)
    except Exception:
        return False
    if agent_spec is None:
        return False
    granted = set(getattr(agent_spec, "tools", None) or [])
    return bool(granted & (set(spec.tools) | set(spec.tool_groups)))


# ── request-side plumbing ────────────────────────────────────────────────────

def resolve_references(request) -> None:
    """Render every reference on ``request`` in place, dropping the unresolvable.

    Runs before the prompt is built (the way ``materialize_attachments`` does for
    files), so every chat pipeline gets the same resolved payloads no matter
    which transport or target it uses. A reference whose entity has been deleted
    since the user picked it is dropped silently — the message still sends.
    """
    references = list(getattr(request, "references", None) or [])
    if not references:
        return

    resolved = []
    spent = 0
    for ref in references[:MAX_REFERENCES]:
        rendered = render_entity(ref.kind, ref.id)
        if rendered is None:
            continue
        body = rendered["body"]
        remaining = MAX_TOTAL_REFERENCE_CHARS - spent
        if len(body) > remaining:
            # The note itself costs characters, so it comes out of the same
            # budget — otherwise a message at the cap would still overshoot it.
            head = body[:max(0, remaining - len(_BUDGET_NOTE))]
            if not head.strip():
                break
            body = head + _BUDGET_NOTE
        spent += len(body)
        ref.label = ref.label or rendered["title"]
        ref.content = body
        resolved.append(ref)
    request.references = resolved


def build_reference_lines(references: list, agent_id: Optional[str] = None) -> List[str]:
    """Rendered references as prompt lines, or an empty list when there are none.

    Each block carries the entity's kind and id so the agent can act on it, and —
    when the agent has a tool for that kind — a line naming the tool, so it can
    refresh or drill into what the user handed it.
    """
    items = [r for r in (references or []) if getattr(r, "content", None)]
    if not items:
        return []
    lines: List[str] = [
        "=== Attached context entities ===",
        "The user attached these records from the hub. Treat them as given context "
        "for this message; do not re-ask for what is already here.",
    ]
    for idx, ref in enumerate(items, start=1):
        spec = KINDS.get(ref.kind)
        noun = spec.noun if spec else ref.kind
        lines += ["", f"[{noun} {idx}] {ref.label or ref.id} (kind={ref.kind}, id={ref.id})"]
        if spec and agent_can_load(agent_id, ref.kind):
            tool_name = spec.tools[0]
            lines.append(f"(call `{tool_name}` with this id for the live record if you need more)")
        lines += ["```", ref.content, "```"]
    lines.append("")
    return lines
