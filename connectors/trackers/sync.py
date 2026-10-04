"""
Import / refresh tracker issues (Jira, Linear) as project tasks.

The same idempotent mapping connectors/git/issue_sync.py uses for repo
issues, keyed on one more field: a tracker issue carries both an opaque
``number`` (for Jira, the same as its human key; for Linear, its
identifier) and a ``status`` — the tracker's own workflow status name, kept
alongside the plain open/closed ``state`` since the Projects page shows all
three. Reuses the exact status rules a re-sync never breaks:

  - new issue, open    -> task status todo
  - new issue, closed  -> task status done
  - issue closed       -> task -> done, only if task is todo/ready
  - issue reopened     -> task -> todo, only if task is done
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from connectors.channels.store import in_workspace
from projects.models import Project
from tasks import service as tasks_service
from tasks.models import CreatedBy, TaskStatus
from workspace import project_folder_name

from .providers import get_provider


def _source_key(provider: str, remote_id: str, number: Any) -> tuple:
    return (str(provider), str(remote_id), str(number))


def _task_description(issue: dict[str, Any]) -> str:
    body = (issue.get("body") or "").strip()
    link = f"Source issue: {issue.get('url')}"
    return f"{body}\n\n{link}" if body else link


def sync_tracker_issues(project: Project) -> dict[str, int]:
    """Sync a project's tracker issues into tasks.

    Returns {"imported": n, "updated": n, "total": n}.
    Raises TrackerError / ValueError on configuration problems.

    Uses the tracker connector of the project's workspace (its own Jira or
    Linear when it defines one, else the default workspace's), whoever calls:
    a tool in a run, the Projects page's route, a scheduled sync.
    """
    tracker = project.tracker
    provider_name = str(tracker.provider or "none")
    if provider_name not in ("jira", "linear"):
        raise ValueError("Project has no tracker configured. Set one on the Projects page first")
    remote_id = tracker.remote_id
    if not remote_id:
        raise ValueError(
            "Project tracker has no remote_id — pick a Jira project or Linear team first"
        )

    workspace = getattr(project, "workspace", None) or None
    provider = in_workspace(workspace, get_provider, provider_name)
    issues = provider.list_issues(remote_id, state="all", limit=500)

    existing: dict[tuple, Any] = {}
    for t in tasks_service.list_tasks():
        if t.project_id != project.id:
            continue
        src = getattr(t, "external_source", None) or {}
        if src.get("provider") in ("jira", "linear") and src.get("number") is not None:
            existing[_source_key(src.get("provider"), src.get("remote_id"), src.get("number"))] = t

    folder = project_folder_name(project.name)
    synced_at = datetime.now(timezone.utc).isoformat()
    imported = updated = 0

    for issue in issues:
        source = {
            "provider": provider_name,
            "remote_id": str(remote_id),
            "number": issue["number"],
            "key": issue.get("key"),
            "url": issue.get("url"),
            "state": issue["state"],
            "status": issue.get("status"),
            "labels": issue.get("labels") or [],
            "synced_at": synced_at,
        }
        task = existing.get(_source_key(provider_name, remote_id, issue["number"]))

        if task is None:
            tasks_service.create_task(
                title=issue["title"],
                description=_task_description(issue),
                created_by=CreatedBy.external,
                status=TaskStatus.done if issue["state"] == "closed" else TaskStatus.todo,
                workspace=project.workspace,
                project=folder,
                project_id=project.id,
                external_source=source,
            )
            imported += 1
            continue

        fields: dict[str, Any] = {"external_source": source}
        if issue["title"] != task.title:
            fields["title"] = issue["title"]
        new_desc = _task_description(issue)
        if new_desc != task.description:
            fields["description"] = new_desc
        if issue["state"] == "closed" and task.status in (TaskStatus.todo, TaskStatus.ready):
            fields["status"] = TaskStatus.done
        elif issue["state"] == "open" and task.status == TaskStatus.done:
            fields["status"] = TaskStatus.todo

        old_src = getattr(task, "external_source", None) or {}
        meaningful = [k for k in fields if k != "external_source"]
        state_changed = (
            old_src.get("state") != source["state"]
            or old_src.get("status") != source["status"]
            or old_src.get("labels") != source["labels"]
        )
        if meaningful or state_changed:
            tasks_service.update_task(task.id, **fields)
            updated += 1

    return {"imported": imported, "updated": updated, "total": len(issues)}


__all__ = ["sync_tracker_issues"]
