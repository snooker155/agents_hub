"""
tracker_* tools: read and write Jira/Linear issues from an agent run.

Mirrors tools/git_publish.py's shape (one JSON-returning @tool per action,
project resolution by id or unique name) over connectors/trackers instead of
connectors/git: list/get/create/comment/transition tracker issues, plus
tracker_sync to import/refresh a project's tracker issues as tasks (the
tracker counterpart of connectors/git/issue_sync.py, reached through
connectors/trackers/sync.py).

Issue bodies and comments returned here come from a third party tracker, so
they go through tools.web.wrap_untrusted before reaching the agent: read,
never instructions to follow.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

_PROVIDERS = ("jira", "linear")


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, **payload}


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


def _not_configured(provider: str) -> Dict[str, Any]:
    label = "Jira" if provider == "jira" else "Linear" if provider == "linear" else provider
    return _err(f"{label} is not configured on the Connectors page")


def _check_provider(provider: str) -> Optional[str]:
    if (provider or "").strip().lower() not in _PROVIDERS:
        return f"Unknown tracker provider: {provider!r}. Use 'jira' or 'linear'."
    return None


def _is_configured(provider: str) -> bool:
    from connectors.trackers import JIRA, LINEAR
    spec = JIRA if provider == "jira" else LINEAR
    return spec.is_configured()


def _resolve_project(ref: str):
    """A project by id, or by an unambiguous name/folder match — the same
    resolution tools/git_publish.py's ``_resolve_project`` uses, since an
    agent is as likely to have a project's name in hand as its id. Only the
    projects of the run's workspace: another workspace's is not found."""
    from tools.project_management import resolve_visible_project
    return resolve_visible_project(ref)


# ── tracker_list_issues ──────────────────────────────────────────────────

class TrackerListIssuesInput(BaseModel):
    provider: str = Field(..., description="Tracker provider: jira or linear")
    remote_id: str = Field(..., description="Jira project key or Linear team key")
    state: str = Field("open", description="open, closed or all")
    limit: int = Field(50, description="Maximum number of issues to return")


@tool("tracker_list_issues", args_schema=TrackerListIssuesInput)
def tracker_list_issues(provider: str, remote_id: str, state: str = "open", limit: int = 50) -> str:
    """List issues from a Jira project or a Linear team."""
    from connectors.trackers.providers import TrackerError, get_provider

    bad = _check_provider(provider)
    if bad:
        return json.dumps(_err(bad))
    provider = provider.strip().lower()
    if not _is_configured(provider):
        return json.dumps(_not_configured(provider))
    try:
        issues = get_provider(provider).list_issues(remote_id, state=state, limit=limit)
    except TrackerError as e:
        return json.dumps(_err(str(e)))
    return json.dumps(_ok({"issues": issues, "total": len(issues)}), ensure_ascii=False, default=str)


# ── tracker_get_issue ────────────────────────────────────────────────────

class TrackerGetIssueInput(BaseModel):
    provider: str = Field(..., description="Tracker provider: jira or linear")
    key: str = Field(..., description="Issue key, e.g. PROJ-12 or ENG-123")


@tool("tracker_get_issue", args_schema=TrackerGetIssueInput)
def tracker_get_issue(provider: str, key: str) -> str:
    """Fetch a single tracker issue by its key."""
    from connectors.trackers.providers import TrackerError, get_provider
    from tools.web import wrap_untrusted

    bad = _check_provider(provider)
    if bad:
        return json.dumps(_err(bad))
    provider = provider.strip().lower()
    if not _is_configured(provider):
        return json.dumps(_not_configured(provider))
    try:
        issue = dict(get_provider(provider).get_issue(key))
    except TrackerError as e:
        return json.dumps(_err(str(e)))
    issue["body"] = wrap_untrusted(f"{provider} issue {issue.get('key') or key}", issue.get("body") or "")
    return json.dumps(_ok({"issue": issue}), ensure_ascii=False, default=str)


# ── tracker_sync ─────────────────────────────────────────────────────────

class TrackerSyncInput(BaseModel):
    project: str = Field(..., description="Project id or unique name")


@tool("tracker_sync", args_schema=TrackerSyncInput)
def tracker_sync(project: str) -> str:
    """Import new and refresh previously imported tracker issues as tasks."""
    from connectors.trackers.providers import TrackerError
    from connectors.trackers.sync import sync_tracker_issues

    proj = _resolve_project(project)
    if proj is None:
        return json.dumps(_err(f"No project found matching {project!r}"))
    provider = str(proj.tracker.provider or "none")
    if provider not in _PROVIDERS:
        return json.dumps(_err(f"Project {proj.name!r} has no tracker configured"))
    # The sync uses the project's workspace's tracker connector (its own, else
    # the default's), so that is the one that must be configured.
    from connectors.channels.store import in_workspace
    if not in_workspace(getattr(proj, "workspace", None) or None, _is_configured, provider):
        return json.dumps(_not_configured(provider))
    try:
        result = sync_tracker_issues(proj)
    except (TrackerError, ValueError) as e:
        return json.dumps(_err(str(e)))
    return json.dumps(_ok(result))


# ── tracker_comment ──────────────────────────────────────────────────────

class TrackerCommentInput(BaseModel):
    provider: str = Field(..., description="Tracker provider: jira or linear")
    key: str = Field(..., description="Issue key, e.g. PROJ-12 or ENG-123")
    text: str = Field(..., description="Comment text")


@tool("tracker_comment", args_schema=TrackerCommentInput)
def tracker_comment(provider: str, key: str, text: str) -> str:
    """Post a comment on a tracker issue (sent as Atlassian Document Format on Jira)."""
    from connectors.trackers.providers import TrackerError, get_provider

    bad = _check_provider(provider)
    if bad:
        return json.dumps(_err(bad))
    provider = provider.strip().lower()
    if not _is_configured(provider):
        return json.dumps(_not_configured(provider))
    try:
        result = get_provider(provider).add_comment(key, text)
    except TrackerError as e:
        return json.dumps(_err(str(e)))
    return json.dumps(_ok(result))


# ── tracker_transition ───────────────────────────────────────────────────

class TrackerTransitionInput(BaseModel):
    provider: str = Field(..., description="Tracker provider: jira or linear")
    key: str = Field(..., description="Issue key, e.g. PROJ-12 or ENG-123")
    status: str = Field(..., description="Target status name, e.g. 'In Progress' or 'Done'")


@tool("tracker_transition", args_schema=TrackerTransitionInput)
def tracker_transition(provider: str, key: str, status: str) -> str:
    """Move a tracker issue to a named status, from the tracker's own workflow."""
    from connectors.trackers.providers import TrackerError, get_provider

    bad = _check_provider(provider)
    if bad:
        return json.dumps(_err(bad))
    provider = provider.strip().lower()
    if not _is_configured(provider):
        return json.dumps(_not_configured(provider))
    try:
        result = get_provider(provider).transition(key, status)
    except TrackerError as e:
        return json.dumps(_err(str(e)))
    return json.dumps(_ok(result))


# ── tracker_create_issue ─────────────────────────────────────────────────

class TrackerCreateIssueInput(BaseModel):
    provider: str = Field(..., description="Tracker provider: jira or linear")
    remote_id: str = Field(..., description="Jira project key or Linear team key")
    title: str = Field(..., description="Issue title / summary")
    body: str = Field("", description="Issue description")
    labels: Optional[List[str]] = Field(None, description="Labels to attach")


@tool("tracker_create_issue", args_schema=TrackerCreateIssueInput)
def tracker_create_issue(provider: str, remote_id: str, title: str, body: str = "",
                          labels: Optional[List[str]] = None) -> str:
    """Create a new issue in a Jira project or a Linear team."""
    from connectors.trackers.providers import TrackerError, get_provider

    bad = _check_provider(provider)
    if bad:
        return json.dumps(_err(bad))
    provider = provider.strip().lower()
    if not _is_configured(provider):
        return json.dumps(_not_configured(provider))
    try:
        result = get_provider(provider).create_issue(remote_id, title, body=body, labels=labels)
    except TrackerError as e:
        return json.dumps(_err(str(e)))
    return json.dumps(_ok(result))


CONNECTOR_TOOLS = [
    tracker_list_issues, tracker_get_issue, tracker_sync,
    tracker_comment, tracker_transition, tracker_create_issue,
]

__all__ = ["CONNECTOR_TOOLS"]
