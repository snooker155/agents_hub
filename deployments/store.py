"""Project deployments as a document collection (``project_deployments``),
one record per project, through :class:`common.docstore.DocStore` like the
projects themselves (projects/storage.py)."""
from __future__ import annotations

import threading
from typing import Any, Callable, List, Optional

from common.docstore import DocStore

from .models import ProjectDeployment

_docs: Optional[DocStore] = None
_lock = threading.RLock()


def docs() -> DocStore:
    global _docs
    if _docs is None:
        _docs = DocStore("project_deployments")
    return _docs


def _parse(data: Any) -> Optional[ProjectDeployment]:
    try:
        return ProjectDeployment.model_validate(dict(data))
    except Exception:  # noqa: BLE001 - a malformed record is skipped, not fatal
        return None


def get(deployment_id: str) -> Optional[ProjectDeployment]:
    doc = docs().get(str(deployment_id))
    return _parse(doc) if doc is not None else None


def for_project(project_id: str) -> Optional[ProjectDeployment]:
    for doc in docs().values():
        if isinstance(doc, dict) and str(doc.get("project_id") or "") == str(project_id):
            return _parse(doc)
    return None


def by_slug(slug: str) -> Optional[ProjectDeployment]:
    slug = (slug or "").strip().lower()
    if not slug:
        return None
    for doc in docs().values():
        if isinstance(doc, dict) and str(doc.get("slug") or "").lower() == slug:
            return _parse(doc)
    return None


def list_all(*, workspace: Optional[str] = None) -> List[ProjectDeployment]:
    out: List[ProjectDeployment] = []
    for doc in docs().values():
        dep = _parse(doc) if isinstance(doc, dict) else None
        if dep is None:
            continue
        if workspace and dep.workspace != workspace:
            continue
        out.append(dep)
    out.sort(key=lambda d: d.updated_at, reverse=True)
    return out


def save(dep: ProjectDeployment) -> ProjectDeployment:
    dep.touch()
    docs().put(dep.id, dep.model_dump(mode="json"))
    return dep


def mutate(deployment_id: str, fn: Callable[[ProjectDeployment], Any]) -> Optional[ProjectDeployment]:
    """Load, apply ``fn`` and save under one lock, so the supervisor and a
    route never write over each other's change of the same record."""
    with _lock:
        dep = get(deployment_id)
        if dep is None:
            return None
        fn(dep)
        return save(dep)


def delete(deployment_id: str) -> bool:
    return docs().delete(str(deployment_id))


def reset_cache() -> None:
    """Tests: forget the cached collection handle."""
    global _docs
    _docs = None


__all__ = ["docs", "get", "for_project", "by_slug", "list_all", "save", "mutate", "delete",
           "reset_cache"]
