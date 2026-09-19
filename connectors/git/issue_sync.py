"""
Import / refresh repo issues as project tasks.

Tasks created here carry an `external_source` payload identifying the origin
issue, which makes re-syncs idempotent: an issue maps to at most one task.

Status mapping is deliberately conservative — a re-sync never touches tasks
that moved beyond todo/ready (someone is working on them):
  - new issue, open    -> task status todo
  - new issue, closed  -> task status done
  - issue closed       -> task -> done, only if task is todo/ready
  - issue reopened     -> task -> todo, only if task is done
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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


def sync_issues(project: Project) -> dict[str, int]:
    """Sync repo issues into tasks for the given project.

    Returns {"imported": n, "updated": n, "total": n}.
    Raises GitProviderError / ValueError on configuration problems.
    """
    provider_name = str(project.repo.type.value if hasattr(project.repo.type, "value") else project.repo.type)
    remote_id = project.repo.remote_id
    if not remote_id:
        raise ValueError("Project repo has no remote_id — connect it to a GitHub/GitLab repo first")

    provider = get_provider(provider_name)
    issues = provider.list_issues(remote_id)

    existing: dict[tuple, Any] = {}
    for t in tasks_service.list_tasks():
        if t.project_id != project.id:
            continue
        src = getattr(t, "external_source", None) or {}
        if src.get("number") is not None:
            existing[_source_key(src.get("provider"), src.get("remote_id"), src.get("number"))] = t

    folder = project_folder_name(project.name)
    synced_at = datetime.now(timezone.utc).isoformat()
    imported = updated = 0

    for issue in issues:
        source = {
            "provider": provider_name,
            "remote_id": str(remote_id),
            "number": issue["number"],
            "url": issue.get("url"),
            "state": issue["state"],
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
        state_changed = old_src.get("state") != source["state"] or old_src.get("labels") != source["labels"]
        if meaningful or state_changed:
            tasks_service.update_task(task.id, **fields)
            updated += 1

    return {"imported": imported, "updated": updated, "total": len(issues)}
