from typing import Any, Dict, List, Optional, Iterable
from pydantic import BaseModel, Field
from .utils import load_json, save_json, read, write
from .config import Paths
from core.models.task import Task

P = Paths()
TASKS_PATH = P.plan + "/tasks.json"
TASKS_MD_PATH = P.plan + "/tasks.md"

def load_tasks() -> List[Task]:
    raw = load_json(TASKS_PATH, default=[])
    return [Task(**t) for t in raw]

def save_tasks(tasks: List[Task]):
    save_json(TASKS_PATH, [t.model_dump() for t in tasks])

def next_id(tasks: List[Task], prefix: str) -> str:
    n = 1 + sum(1 for t in tasks if str(t.id).startswith(prefix))
    return f"{prefix}-{n}"

def dump_tasks():
    tasks = load_tasks()
    lines = ["# Реестр задач", "", "| ID | Title | Assignee | Status | Artifacts | Parent | Description |",
             "|---|---|---|---|---|---|---|"]
    for t in tasks:
        lines.append(f"| {t.id} | {t.title} | {t.assignee or ''} | {t.status} | {t.artifacts or ''} | {t.parent_id or ''} | {t.description or ''} |")
    write(TASKS_MD_PATH, "\n".join(lines))

def match_tasks(
    tasks: List[Task],
    *,
    assignee: str,
    task_id: str | None = None,
    title_query: str | None = None,
    status_in: Iterable[str] = ("Todo",)
) -> List[Task]:
    """Вернёт задачи нужного исполнителя с фильтрами по id/части названия/статусу."""
    title_query_lc = (title_query or "").lower()
    out: List[Task] = []
    for t in tasks:
        if t.assignee != assignee: 
            continue
        if t.status not in status_in:
            continue
        if task_id and str(t.id) != task_id:
            continue
        if title_query and title_query_lc not in t.title.lower():
            continue
        out.append(t)
    return out
