from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from common.utils import load_json, save_json, read, write
from common.config import Paths
from typing import Iterable


def get_tasks_path() -> str:
    return Paths().plan + "/tasks.json"

def get_tasks_md_path() -> str:
    return Paths().plan + "/tasks.md"

class Task(BaseModel):
    id: str
    title: str
    assignee: str     # "BA"|"SD"|"TL"|"BE"|"FE"|"DO"
    status: str="Todo"  # Todo|InProgress|Review|Done|NeedsChanges|Closed
    artifacts: Optional[List[str]]=None
    artifact_key: Optional[str]=None
    parent_id: Optional[str]=None
    description: Optional[str]=None
    payload: Dict[str,Any]=Field(default_factory=dict)

def load_tasks() -> List[Task]:
    raw = load_json(get_tasks_path(), default=[])
    return [Task(**t) for t in raw]

def save_tasks(tasks: List[Task]):
    save_json(get_tasks_path(), [t.model_dump() for t in tasks])

def next_id(tasks: List[Task], prefix: str) -> str:
    n = 1 + sum(1 for t in tasks if t.id.startswith(prefix))
    return f"{prefix}-{n}"

def dump_tasks():
    tasks = load_tasks()
    lines = ["# Реестр задач", "", "| ID | Title | Assignee | Status | Artifacts | Parent | Description |",
             "|---|---|---|---|---|---|---|"]
    for t in tasks:
        lines.append(f"| {t.id} | {t.title} | {t.assignee} | {t.status} | {t.artifacts or ''} | {t.parent_id or ''} | {t.description or ''} |")
    write(get_tasks_md_path(), "\n".join(lines))

def match_tasks(
    tasks: list[Task],
    *,
    assignee: str,
    task_id: str | None = None,
    title_query: str | None = None,
    status_in: Iterable[str] = ("Todo",)
) -> list[Task]:
    """Вернёт задачи нужного исполнителя с фильтрами по id/части названия/статусу."""
    title_query_lc = (title_query or "").lower()
    out: list[Task] = []
    for t in tasks:
        if t.assignee != assignee: 
            continue
        if t.status not in status_in:
            continue
        if task_id and t.id != task_id:
            continue
        if title_query and title_query_lc not in t.title.lower():
            continue
        out.append(t)
    return out
