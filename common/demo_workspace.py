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
fixed ids of their own to avoid duplication (tasks, chats, sessions, views,
instances, services and files all mint their own ids the normal way, and the
seed refers to them through ``*_hint`` keys); the agents, the project, the
flow, the team, the scenario and the runs use the fixed ids from ``seed.json``
because callers (routes, tests, `docs/demo.md`, the recorded fixtures) expect
those ids to be stable across an install.
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

#: The custom agents the demo ships, in seeding (and pipeline) order. The
#: first three form the content pipeline; the fourth answers customer mail.
DEMO_AGENT_IDS: tuple[str, ...] = ("demo_writer", "demo_analyst", "demo_reviewer", "demo_support")

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
            # A published agent is what the Marketplace page lists; the seed
            # publishes one so that page is not empty.
            shared=bool(a.get("shared", False)),
            # The support agent ships with a pulse (docs/proactive.md); the
            # job it owns is created by _seed_pulse once the agent exists.
            proactive=dict(a.get("proactive") or {}),
        )
        add_agent(spec, user_edit=False)


def _seed_pulse(seed: Dict[str, Any]) -> None:
    """Create the heartbeat job of every seeded agent whose profile is on,
    and the tick history the Pulse card shows, from ``seed["ticks"]``
    (``hours_ago`` relative to now, so the feed never looks stale)."""
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone

    from agents.registry import add_agent, get_agent
    from plans import service as plans
    from plans.models import FireRecord
    from proactive.profile import validate_profile
    from proactive.service import sync_job

    jobs: Dict[str, str] = {}
    for a in seed.get("agents") or []:
        spec = get_agent(str(a["id"]))
        if spec is None or not (spec.proactive or {}).get("enabled"):
            continue
        profile = sync_job(spec, validate_profile(spec.proactive))
        add_agent(replace(spec, proactive=profile), user_edit=False)
        jobs[spec.id] = profile["job_id"]

    now = datetime.now(timezone.utc)
    for tick in seed.get("ticks") or []:
        job_id = jobs.get(str(tick.get("agent_id") or ""))
        if not job_id:
            continue
        at = now - timedelta(hours=float(tick.get("hours_ago") or 0))
        outcome = str(tick.get("outcome") or "quiet")
        started = outcome in ("acted", "quiet", "blocked", "error")
        plans.fire_store.add(FireRecord(
            job_id=job_id, workspace=DEMO_WORKSPACE_NAME, at=at, slot=at,
            trigger=str(tick.get("trigger") or "schedule"), ok=outcome != "error",
            # A started tick's task is long pruned in a demo; the row keeps a
            # placeholder id so the feed counts it as a run of the day.
            task_id=f"demo-tick-{abs(hash((job_id, at.isoformat()))) % 10**8}" if started else None,
            outcome=outcome, summary=tick.get("summary"), next_check=tick.get("next_check"),
            cost_usd=tick.get("cost_usd"),
        ))


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


def _seed_views(seed: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Create the demo views. Returns ``{id_hint: {view_id, kind, title}}`` —
    views mint their own id, so anything that needs to reference one back (a
    chat bubble pointing at the chart) resolves it through this map."""
    from views.store import create_view

    view_ids: Dict[str, Dict[str, str]] = {}
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
            view_ids[str(hint)] = {
                "view_id": env.view_id, "kind": str(v["kind"]), "title": str(v.get("title") or ""),
            }
    return view_ids


def _seed_tasks(seed: Dict[str, Any]) -> Dict[str, str]:
    """Create the demo tasks. Returns ``{title: task_id}`` so a run or an
    instance can be tied to the task it worked on."""
    from tasks.models import CreatedBy, TaskStatus
    from tasks.service import create_task, set_task_result
    from workspace.storage import project_folder_name

    project = seed.get("project") or {}
    folder_name = project_folder_name(str(project.get("name") or "Demo site"))

    task_ids: Dict[str, str] = {}
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
        task_ids[str(t.get("title") or "")] = str(task.id)
        if status == TaskStatus.done and t.get("result"):
            set_task_result(task.id, str(t["result"]), agent_id="demo_reviewer")
    return task_ids


def _seed_services(seed: Dict[str, Any]) -> Dict[str, str]:
    """One example service per seed entry, paused, so the Services page has a
    record to show. Returns ``{id_hint: service_id}``."""
    from services import store as sstore

    service_ids: Dict[str, str] = {}
    for s in seed.get("services") or []:
        rec = sstore.create(
            name=str(s.get("name") or ""),
            agent_id=str(s.get("agent_id") or DEMO_AGENT_IDS[0]),
            workspace=DEMO_WORKSPACE_NAME,
            replicas_min=int(s.get("replicas_min") or 1),
            replicas_max=int(s.get("replicas_max") or 1),
            concurrency=int(s.get("concurrency") or 4),
            idle_stop_seconds=int(s.get("idle_stop_seconds") or 600),
            created_by="local",
        )
        hint = s.get("id_hint")
        if hint:
            service_ids[str(hint)] = rec["service_id"]
    return service_ids


def _seed_service_events(seed: Dict[str, Any], service_ids: Dict[str, str],
                         instance_ids: Dict[str, str]) -> None:
    """The service journal, written once its replica instance exists so an
    event can name it. A seeded service ends paused: nothing runs in the demo,
    and an active service with no live replica would read as a failure."""
    from services import store as sstore

    for s in seed.get("services") or []:
        service_id = service_ids.get(str(s.get("id_hint") or ""))
        if not service_id:
            continue
        for ev in s.get("events") or []:
            if str(ev.get("kind")) == "paused":
                continue  # written by pause() below
            sstore.add_event(
                service_id, str(ev.get("kind") or "note"), str(ev.get("detail") or ""),
                instance_id=instance_ids.get(str(ev.get("instance_id_hint") or "")),
            )
        sstore.pause(service_id, str(s.get("paused_reason") or "paused by operator"))


def _seed_instances(seed: Dict[str, Any], task_ids: Dict[str, str],
                    service_ids: Dict[str, str]) -> Dict[str, str]:
    """Example instance records: a resident copy, a task copy and a service
    replica. Returns ``{id_hint: instance_id}``. The final state is applied
    after the runs are seeded (:func:`_settle_instances`), since closing a
    run moves its instance along on its own."""
    from instances import store as istore

    instance_ids: Dict[str, str] = {}
    for i in seed.get("instances") or []:
        rec = istore.create(
            str(i.get("agent_id") or DEMO_AGENT_IDS[0]),
            kind=str(i.get("kind") or "task"),
            workspace=DEMO_WORKSPACE_NAME,
            state="starting",
            label=str(i.get("label") or ""),
            project_id=DEMO_PROJECT_ID,
            task_id=task_ids.get(str(i.get("task_title") or "")),
            service_id=service_ids.get(str(i.get("service_id_hint") or "")),
        )
        hint = i.get("id_hint")
        if hint:
            instance_ids[str(hint)] = rec["instance_id"]
    return instance_ids


def _settle_instances(seed: Dict[str, Any], instance_ids: Dict[str, str]) -> None:
    from instances import store as istore

    now = istore.utc_iso()
    for i in seed.get("instances") or []:
        instance_id = instance_ids.get(str(i.get("id_hint") or ""))
        if not instance_id:
            continue
        istore.update(
            instance_id,
            state=str(i.get("state") or "finished"),
            finished_at=now, last_activity_at=now, current_run_id=None,
            last_activity=str(i.get("last_activity") or ""),
        )


def _seed_files(seed: Dict[str, Any]) -> Dict[str, str]:
    """Workspace files: every file of the sample project, registered the way
    an agent's write would be, plus the seed's uploads. Returns
    ``{id_hint: file_id}`` for the uploads."""
    from files import service as files

    file_ids: Dict[str, str] = {}
    try:
        files.index_workspace(DEMO_WORKSPACE_NAME, created_by="local")
    except files.FileError:
        log.debug("demo workspace: folder index skipped", exc_info=True)
    for f in seed.get("files") or []:
        rec = files.create_file(
            DEMO_WORKSPACE_NAME, str(f.get("name") or "file"),
            str(f.get("content") or "").encode("utf-8"),
            mime_type=f.get("mime_type"), source="upload", created_by="local",
        )
        hint = f.get("id_hint")
        if hint:
            file_ids[str(hint)] = rec["file_id"]
    return file_ids


def _write_run_log(run_id: str, lines: List[str]) -> Path:
    from managers.run_manager import run_log_path

    path = run_log_path(run_id)
    path.write_text("\n".join(f"[demo] {line}" for line in lines) + "\n", encoding="utf-8")
    return path


def _chat_turns(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A chat's turns: the ``turns`` list, or the older single
    ``user_message``/``agent_message`` pair as one turn."""
    turns = c.get("turns")
    if isinstance(turns, list) and turns:
        return [t for t in turns if isinstance(t, dict)]
    return [{
        "user": c.get("user_message", ""), "agent": c.get("agent_message", ""),
        "view_id_hint": c.get("view_id_hint"),
    }]


def _seed_chats(seed: Dict[str, Any], view_ids: Dict[str, Dict[str, str]],
                instance_ids: Dict[str, str], file_ids: Dict[str, str]) -> None:
    """Recorded chats, each turn backed by a real run, all turns of a chat on
    one session, so the Chat, Sessions, Messages and Run pages all have
    something to show. A chat may hang off an instance (its runs then show on
    the instance's timeline) and may have used an uploaded file."""
    from common import chat_store
    from common.session_service import get_or_create_chat_session
    from files import service as files
    from managers.run_manager import close_run, open_run

    for c in seed.get("chats") or []:
        chat_id = str(c["id"])
        agent_id = str(c.get("agent_id") or DEMO_AGENT_IDS[0])
        title = str(c.get("title") or "")
        session_id = get_or_create_chat_session(chat_id, title, workspace=DEMO_WORKSPACE_NAME,
                                                 agent_id=agent_id)
        instance_id = instance_ids.get(str(c.get("instance_id_hint") or ""))
        file_id = file_ids.get(str(c.get("file_id_hint") or ""))

        messages: List[Dict[str, Any]] = []
        for n, turn in enumerate(_chat_turns(c), start=1):
            # The first turn keeps the historical ``<chat>_run`` id the
            # recorded fixtures and the docs refer to.
            run_id = f"{chat_id}_run" if n == 1 else f"{chat_id}_run_{n}"
            user_text = str(turn.get("user") or "")
            agent_text = str(turn.get("agent") or "")
            log_lines = [f"{agent_id} answering: {user_text}"]
            log_lines += [str(line) for line in (turn.get("log") or [])]
            log_lines.append(f"{agent_id} replied.")
            log_path = _write_run_log(run_id, log_lines)

            extra: Dict[str, Any] = {}
            if instance_id:
                extra["instance_id"] = instance_id
            open_run(run_id, agent_id, session_id=session_id, session_type="chat",
                     workspace=DEMO_WORKSPACE_NAME, title=title,
                     log_file=str(log_path), input=user_text, **extra)
            close_run(run_id, status="completed", exit_code=0, output=agent_text)

            user_message: Dict[str, Any] = {"id": f"srv-u-{run_id}", "role": "user", "content": user_text}
            agent_message: Dict[str, Any] = {
                "id": f"srv-a-{run_id}", "role": "agent", "agent_id": agent_id,
                "content": agent_text, "error": False, "run_id": run_id,
            }
            view = view_ids.get(str(turn.get("view_id_hint") or ""))
            if view:
                # The reply's own view, the way a turn that called create_view
                # records it (chat/turn.py); the bubble renders response_obj.
                agent_message["response_obj"] = {
                    "kind": "view_ref", "view_id": view["view_id"], "view_kind": view["kind"],
                    "title": view["title"], "summary": "", "complexity": "inline",
                }
            messages += [user_message, agent_message]

        if file_id:
            files.record_use(file_id, "chat", chat_id, title)
        chat_store.save_chat({
            "id": chat_id,
            "title": title,
            "workspace": DEMO_WORKSPACE_NAME,
            "agent_id": agent_id,
            "messages": messages,
        })


def _seed_runs(seed: Dict[str, Any], task_ids: Dict[str, str],
               instance_ids: Dict[str, str]) -> None:
    """Completed runs not tied to a chat: the work behind the done tasks, so
    the Run and Messages pages show more than the chat turns and a task's
    page has the run that closed it."""
    from managers.run_manager import close_run, open_run

    for r in seed.get("runs") or []:
        run_id = str(r["id"])
        agent_id = str(r.get("agent_id") or DEMO_AGENT_IDS[0])
        log_path = _write_run_log(run_id, [str(line) for line in (r.get("log") or [])])
        extra: Dict[str, Any] = {}
        instance_id = instance_ids.get(str(r.get("instance_id_hint") or ""))
        if instance_id:
            extra["instance_id"] = instance_id
        open_run(run_id, agent_id, task_id=task_ids.get(str(r.get("task_title") or "")),
                 workspace=DEMO_WORKSPACE_NAME, title=str(r.get("title") or ""),
                 log_file=str(log_path), input=str(r.get("input") or ""), **extra)
        close_run(run_id, status="completed", exit_code=0, output=str(r.get("output") or ""))


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
    _seed_pulse(seed)
    _seed_project(seed)
    _seed_flow(seed)
    _seed_team(seed)
    _seed_scenario(seed)
    view_ids = _seed_views(seed)
    task_ids = _seed_tasks(seed)
    service_ids = _seed_services(seed)
    instance_ids = _seed_instances(seed, task_ids, service_ids)
    _seed_service_events(seed, service_ids, instance_ids)
    file_ids = _seed_files(seed)
    _seed_chats(seed, view_ids, instance_ids, file_ids)
    _seed_runs(seed, task_ids, instance_ids)
    _settle_instances(seed, instance_ids)
    return True


# ── removal ──────────────────────────────────────────────────────────────────

def remove_demo_workspace() -> Dict[str, int]:
    """Remove everything the demo seeded, and nothing else. Returns counts.

    Tasks, chats, sessions, runs, views, instances, services and files are
    matched by ``workspace == "demo"`` (they mint their own ids, so that is
    the only reliable handle); the agents, the project, the flow, the team and
    the scenario are matched by the fixed ``demo_`` ids from ``seed.json``.
    The workspace folder and its metadata are removed last, after everything
    that lived under it.
    """
    counts: Dict[str, int] = {
        "agents": 0, "project": 0, "flow": 0, "team": 0, "scenario": 0,
        "views": 0, "tasks": 0, "chats": 0, "sessions": 0, "runs": 0,
        "instances": 0, "services": 0, "files": 0, "jobs": 0, "ticks": 0,
        "workspace": 0,
    }

    # The pulse jobs and their tick journal, matched by workspace like the
    # tasks below; before the agents, so a tick cannot start meanwhile.
    try:
        from plans import service as plans
        from plans.models import JobKind
        for job in plans.list_jobs(workspace=DEMO_WORKSPACE_NAME):
            if job.kind != JobKind.heartbeat:
                continue
            for row in plans.fire_store.list_for_job(job.id, limit=100000):
                counts["ticks"] += plans.fire_store.delete(row.id)
            counts["jobs"] += plans.delete_job(job.id)
    except Exception:  # noqa: BLE001 - a plans store that cannot be read must not stop the removal
        log.debug("demo workspace: pulse removal skipped", exc_info=True)

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

    from instances import store as istore
    instances_page = istore.list_instances(limit=500, workspace=DEMO_WORKSPACE_NAME,
                                           include_archived=True)
    for inst in instances_page["items"]:
        if istore.delete(inst["instance_id"]):
            counts["instances"] += 1

    from services import store as sstore
    for svc in sstore.list_services(workspace=DEMO_WORKSPACE_NAME):
        if sstore.delete(svc["service_id"]):
            counts["services"] += 1

    from files import service as files
    for rec in files.list_files(DEMO_WORKSPACE_NAME, limit=500):
        if files.delete_file(rec["file_id"]):
            counts["files"] += 1

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
