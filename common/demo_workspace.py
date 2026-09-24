"""
The demo workspace: a small sample web shop, seeded end to end so every page
of the product has something real to show without an operator having to build
anything first. See docs/demo.md.

Content lives in ``bootstrap/workspaces/demo/`` (a ``.workspace.json`` template
plus a single ``seed.json`` with fixed ``demo_``-prefixed ids for the agents,
project, flow, team and scenario) so a content change never needs a code
change. This module turns that JSON into real records through the same store
functions every other part of the product uses — never raw SQL — and is the
one place besides ``bootstrap.py`` that is allowed to write into the shared
state on the operator's behalf.

Idempotency is at the workspace level: :func:`ensure_demo_workspace` seeds
everything in one pass and returns ``False`` immediately if the ``demo``
workspace already exists. Individual entities below therefore don't need
fixed ids of their own to avoid duplication (tasks, chats, sessions, runs and
views all mint their own ids the normal way); the agents, the project, the
flow, the team and the scenario use the fixed ids from ``seed.json`` because
callers (routes, tests, `docs/demo.md`) reasonably expect those ids to be
stable across an install.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

DEMO_WORKSPACE_NAME = "demo"

_SEED_ROOT = Path(__file__).resolve().parents[1] / "bootstrap" / "workspaces" / "demo"
_SEED_FILE = _SEED_ROOT / "seed.json"
_WORKSPACE_TEMPLATE_FILE = _SEED_ROOT / ".workspace.json"

#: The three custom agents the demo ships, in seeding (and pipeline) order.
DEMO_AGENT_IDS: tuple[str, ...] = ("demo_writer", "demo_analyst", "demo_reviewer")

DEMO_PROJECT_ID = "demo_project_site"
DEMO_FLOW_ID = "demo_content_pipeline"
DEMO_TEAM_ID = "demo_editorial_team"
DEMO_SCENARIO_ID = "demo_market"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ── presence ─────────────────────────────────────────────────────────────────

def _workspace_present() -> bool:
    from workspace.storage import get_workspace_folder, get_workspace_metadata
    return get_workspace_folder(DEMO_WORKSPACE_NAME) is not None or bool(
        get_workspace_metadata(DEMO_WORKSPACE_NAME)
    )


# ── seeding ──────────────────────────────────────────────────────────────────

def _seed_workspace_metadata() -> None:
    from workspace.storage import create_workspace_folder, update_workspace_metadata

    create_workspace_folder(DEMO_WORKSPACE_NAME)
    template = _load_json(_WORKSPACE_TEMPLATE_FILE)
    update_workspace_metadata(DEMO_WORKSPACE_NAME, {
        "allowed_agents": list(template.get("allowed_agents") or []),
        "orchestrator": dict(template.get("orchestrator") or {}),
        "settings": dict(template.get("settings") or {}),
    })


def _seed_agents(seed: Dict[str, Any]) -> None:
    from agents.prompt_assembly import write_instructions
    from agents.registry import AgentSpec, add_agent

    for a in seed.get("agents") or []:
        agent_id = str(a["id"])
        instructions_path = _SEED_ROOT / "agents" / agent_id / "instructions.md"
        write_instructions(agent_id, instructions_path.read_text(encoding="utf-8"))
        spec = AgentSpec(
            id=agent_id,
            name=str(a.get("name") or agent_id),
            type="langchain",
            entrypoint="agents.agent_factory:build_agent_executor",
            description=str(a.get("description") or ""),
            tools=list(a.get("tools") or []),
            owner_workspace=DEMO_WORKSPACE_NAME,
        )
        add_agent(spec, user_edit=False)


def _seed_project(seed: Dict[str, Any]) -> None:
    from projects.models import Project
    from projects.storage import ProjectStore
    from workspace.storage import create_workspace_folder, project_folder_name

    project = seed.get("project") or {}
    ws_folder = create_workspace_folder(DEMO_WORKSPACE_NAME)
    folder_name = project_folder_name(str(project.get("name") or "Demo site"))
    project_dir = ws_folder / folder_name
    for rel_path, content in (project.get("files") or {}).items():
        dest = project_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Sample repo files are only ever written when absent: an operator (or
        # a demo agent) may have edited them since the demo was first seeded,
        # and re-seeding must not clobber that.
        if not dest.exists():
            dest.write_text(content, encoding="utf-8")

    store = ProjectStore()
    if store.get(str(project.get("id") or DEMO_PROJECT_ID)) is None:
        store.add(Project(
            id=str(project.get("id") or DEMO_PROJECT_ID),
            name=str(project.get("name") or "Demo site"),
            description=str(project.get("description") or ""),
            workspace=DEMO_WORKSPACE_NAME,
        ))


def _seed_flow(seed: Dict[str, Any]) -> None:
    from flow.store import save_flow

    flow = seed.get("flow")
    if isinstance(flow, dict) and flow.get("id"):
        save_flow(dict(flow))


def _seed_team(seed: Dict[str, Any]) -> None:
    from teams.models import Team
    from teams.store import save_team

    team = seed.get("team")
    if isinstance(team, dict) and team.get("team_id"):
        save_team(Team.from_dict({**team, "workspace": DEMO_WORKSPACE_NAME}))


def _seed_scenario(seed: Dict[str, Any]) -> None:
    from playground.models import Scenario
    from playground.store import save_scenario

    scenario = seed.get("scenario")
    if isinstance(scenario, dict) and scenario.get("scenario_id"):
        save_scenario(Scenario.from_dict({**scenario, "workspace": DEMO_WORKSPACE_NAME}))


def _seed_views(seed: Dict[str, Any]) -> Dict[str, str]:
    """Create the demo views. Returns ``{id_hint: real view_id}`` — views mint
    their own id, so anything that needs to reference one back (a chat bubble
    pointing at the chart) resolves it through this map."""
    from views.store import create_view

    view_ids: Dict[str, str] = {}
    for v in seed.get("views") or []:
        env = create_view(
            v["kind"],
            title=str(v.get("title") or ""),
            spec=v.get("spec") or {},
            workspace=DEMO_WORKSPACE_NAME,
            summary=str(v.get("summary") or ""),
            data=v.get("data"),
        )
        hint = v.get("id_hint")
        if hint:
            view_ids[str(hint)] = env.view_id
    return view_ids


def _seed_tasks(seed: Dict[str, Any]) -> None:
    from tasks.models import CreatedBy, TaskStatus
    from tasks.service import create_task, set_task_result
    from workspace.storage import project_folder_name

    project = seed.get("project") or {}
    folder_name = project_folder_name(str(project.get("name") or "Demo site"))

    for t in seed.get("tasks") or []:
        status = TaskStatus(str(t.get("status") or "todo"))
        task = create_task(
            title=str(t.get("title") or ""),
            description=str(t.get("description") or ""),
            created_by=CreatedBy.user,
            status=status,
            workspace=DEMO_WORKSPACE_NAME,
            project=folder_name,
            project_id=str(project.get("id") or DEMO_PROJECT_ID),
            blocked_reason=t.get("blocked_reason"),
        )
        if status == TaskStatus.done and t.get("result"):
            set_task_result(task.id, str(t["result"]), agent_id="demo_reviewer")


def _write_run_log(run_id: str, lines: List[str]) -> Path:
    from managers.run_manager import run_log_path

    path = run_log_path(run_id)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _seed_chats(seed: Dict[str, Any], view_ids: Dict[str, str]) -> None:
    """Two recorded chat turns, each backed by a real run and session so the
    Chat, Sessions, Messages and Run pages all have something to show."""
    from common import chat_store
    from common.session_service import get_or_create_chat_session
    from managers.run_manager import close_run, open_run

    for c in seed.get("chats") or []:
        chat_id = str(c["id"])
        agent_id = str(c.get("agent_id") or DEMO_AGENT_IDS[0])
        run_id = f"{chat_id}_run"
        session_id = get_or_create_chat_session(chat_id, str(c.get("title") or ""),
                                                 workspace=DEMO_WORKSPACE_NAME, agent_id=agent_id)

        log_path = _write_run_log(run_id, [
            f"[demo] {agent_id} answering: {c.get('user_message', '')}",
            f"[demo] {agent_id} replied.",
        ])
        open_run(run_id, agent_id, session_id=session_id, session_type="chat",
                 workspace=DEMO_WORKSPACE_NAME, title=str(c.get("title") or ""),
                 log_file=str(log_path), input=str(c.get("user_message") or ""))
        close_run(run_id, status="completed", exit_code=0,
                  output=str(c.get("agent_message") or ""))

        agent_message: Dict[str, Any] = {
            "id": f"srv-a-{run_id}", "role": "agent", "agent_id": agent_id,
            "content": str(c.get("agent_message") or ""), "error": False,
            "run_id": run_id,
        }
        view_hint = c.get("view_id_hint")
        if view_hint and view_ids.get(str(view_hint)):
            vid = view_ids[str(view_hint)]
            agent_message["view"] = {
                "kind": "view_ref", "view_id": vid, "view_kind": "chart",
                "title": "Sales by category", "summary": "", "complexity": "inline",
            }
        chat_store.save_chat({
            "id": chat_id,
            "title": str(c.get("title") or ""),
            "workspace": DEMO_WORKSPACE_NAME,
            "agent_id": agent_id,
            "messages": [
                {"id": f"srv-u-{run_id}", "role": "user", "content": str(c.get("user_message") or "")},
                agent_message,
            ],
        })


def _seed_task_run() -> None:
    """One more completed run, not tied to a chat, so the Run/Messages pages
    show more than just the two chat turns."""
    from managers.run_manager import close_run, open_run

    run_id = "demo_task_run_rename"
    log_path = _write_run_log(run_id, [
        "[demo] demo_writer renaming Rucksack 32 -> Trailpack 32L",
        "[demo] updated src/app.py, docs/notes.md, data/sales.csv",
        "[demo] done",
    ])
    open_run(run_id, "demo_writer", workspace=DEMO_WORKSPACE_NAME,
             title="Rename Rucksack 32 to Trailpack 32L", log_file=str(log_path),
             input="Rename the product everywhere it appears.")
    close_run(run_id, status="completed", exit_code=0,
              output="Renamed across src/app.py, docs/notes.md and data/sales.csv.")


def ensure_demo_workspace() -> bool:
    """Seed the demo workspace end to end if it is missing. Idempotent:
    returns ``False`` without touching anything when it is already present."""
    if _workspace_present():
        return False
    if not _SEED_FILE.is_file():
        log.warning("demo workspace: seed file missing at %s", _SEED_FILE)
        return False

    seed = _load_json(_SEED_FILE)
    _seed_workspace_metadata()
    _seed_agents(seed)
    _seed_project(seed)
    _seed_flow(seed)
    _seed_team(seed)
    _seed_scenario(seed)
    view_ids = _seed_views(seed)
    _seed_tasks(seed)
    _seed_chats(seed, view_ids)
    _seed_task_run()
    return True


# ── removal ──────────────────────────────────────────────────────────────────

def remove_demo_workspace() -> Dict[str, int]:
    """Remove everything the demo seeded, and nothing else. Returns counts.

    Tasks, chats, sessions, runs and views are matched by ``workspace ==
    "demo"`` (they mint their own ids, so that is the only reliable handle);
    the agents, the project, the flow, the team and the scenario are matched
    by the fixed ``demo_`` ids from ``seed.json``. The workspace folder and
    its metadata are removed last, after everything that lived under it.
    """
    counts: Dict[str, int] = {
        "agents": 0, "project": 0, "flow": 0, "team": 0, "scenario": 0,
        "views": 0, "tasks": 0, "chats": 0, "sessions": 0, "runs": 0,
        "workspace": 0,
    }

    from agents.prompt_assembly import delete_definition
    from agents.registry import remove_agent
    for agent_id in DEMO_AGENT_IDS:
        if remove_agent(agent_id):
            counts["agents"] += 1
        delete_definition(agent_id)

    from projects.storage import ProjectStore
    if ProjectStore().delete(DEMO_PROJECT_ID):
        counts["project"] += 1

    from flow.store import delete_flow
    if delete_flow(DEMO_FLOW_ID):
        counts["flow"] += 1

    from teams.store import delete_team
    if delete_team(DEMO_TEAM_ID):
        counts["team"] += 1

    from playground.store import delete_scenario
    if delete_scenario(DEMO_SCENARIO_ID):
        counts["scenario"] += 1

    from views.store import delete_view, list_views
    for row in list_views(workspace=DEMO_WORKSPACE_NAME):
        if delete_view(row["view_id"]):
            counts["views"] += 1

    from tasks.service import delete_task, list_tasks
    for task in list_tasks():
        if task.workspace == DEMO_WORKSPACE_NAME:
            delete_task(task.id, cascade=True)
            counts["tasks"] += 1

    from common import chat_store
    for item in chat_store.list_chats(workspace=DEMO_WORKSPACE_NAME, limit=500)["items"]:
        if chat_store.delete_chat(item["id"]):
            counts["chats"] += 1

    from common.session_service import delete_context, query_contexts
    sessions_page = query_contexts(workspace=DEMO_WORKSPACE_NAME, limit=500)
    for ctx in sessions_page["items"]:
        if delete_context(ctx["session_id"]):
            counts["sessions"] += 1

    from managers.run_manager import delete_run, query_runs
    runs_page = query_runs(workspace=DEMO_WORKSPACE_NAME, limit=500)
    for run in runs_page["items"]:
        if delete_run(run["run_id"]):
            counts["runs"] += 1

    from workspace.storage import delete_workspace_folder
    if delete_workspace_folder(DEMO_WORKSPACE_NAME):
        counts["workspace"] += 1

    return counts


# ── status ───────────────────────────────────────────────────────────────────

def demo_status() -> Dict[str, Any]:
    """Whether the demo is enabled (the ``DEMO_WORKSPACE`` setting) and/or
    present (actually seeded), plus a rough count of what it holds — enough
    for the Settings toggle to render its current state."""
    from common.config import settings

    present = _workspace_present()
    counts: Dict[str, int] = {"agents": 0, "views": 0, "tasks": 0, "chats": 0}
    if present:
        from agents.registry import get_agent
        counts["agents"] = sum(1 for aid in DEMO_AGENT_IDS if get_agent(aid) is not None)

        from views.store import list_views
        counts["views"] = len(list_views(workspace=DEMO_WORKSPACE_NAME))

        from tasks.service import list_tasks
        counts["tasks"] = sum(1 for t in list_tasks() if t.workspace == DEMO_WORKSPACE_NAME)

        from common import chat_store
        counts["chats"] = chat_store.list_chats(workspace=DEMO_WORKSPACE_NAME, limit=1)["total"]

    return {
        "enabled": bool(getattr(settings, "demo_workspace", False)),
        "present": present,
        "workspace": DEMO_WORKSPACE_NAME,
        "counts": counts,
    }


__all__ = [
    "DEMO_WORKSPACE_NAME", "DEMO_AGENT_IDS", "DEMO_PROJECT_ID", "DEMO_FLOW_ID",
    "DEMO_TEAM_ID", "DEMO_SCENARIO_ID",
    "ensure_demo_workspace", "remove_demo_workspace", "demo_status",
]
