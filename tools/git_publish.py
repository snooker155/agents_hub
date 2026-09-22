"""
git_publish — the git write path: branch, commit, push, then a pull/merge
request whose description comes from the task the agent is working on.

Read and write are asymmetric here on purpose. connectors/git/providers.py can
already read issues and PRs with a token; this module is the one place a push
leaves the workspace and lands on GitHub or GitLab, so it carries the
capability grant and the approval gate the read path never needed:

  - tools/capabilities.py classifies "git_publish" CAN_EXFILTRATE — a push is
    workspace content leaving the system, the same shape as fetch_url or
    notify_user, just over git instead of HTTP.
  - tools/approval.py lists "git_publish" in NEEDS_APPROVAL, so a workspace
    that has turned the approval gate on parks the task before the push runs,
    the same as it would for run_shell or delete_file.

Two refusals hold regardless of what the caller asks for, and both live in
``run_git_publish`` rather than the tool wrapper alone, so the dashboard route
(dashboard/backend/routes/projects.py) enforces them identically to an agent's
own call:

  - it never force pushes (connectors/git/git_ops.push has no --force path);
  - it never pushes straight onto the repo's default branch, and never opens
    a pull/merge request from a branch onto itself.

A denylist (.env, *.pem, id_rsa*, plus anything the repo's own .gitignore
already excludes) is never staged by the underlying commit, even if the
caller's ``paths`` would otherwise include it — see connectors/git/git_ops.py.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional
from uuid import UUID

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from common.agent_context import current_task_id
from common.entity_sink import record_entity


# ── small local helpers (mirrors the _json_ok/_json_err shape used across
# tools/project_management.py, tools/entity_runs.py, …) ─────────────────────

def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, **payload}


def _err(message: str, *, code: str = "bad_request", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return body


def _slugify(text: str, max_len: int = 40) -> str:
    """Lowercase, hyphenated, ASCII — safe inside a branch name on both providers."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    s = s[:max_len].strip("-")
    return s or "change"


def _resolve_project(ref: str):
    """A project by id, or by an unambiguous name/folder match.

    Mirrors how the dashboard's own pickers accept either — see
    tools/project_management.py's get_project_tool (id only) and
    list_projects_tool (which shows both id and name). This tool is used from
    chat as often as from a task run, where an agent is more likely to have
    the project's name in hand than its id.
    """
    from common.paths import PROJECTS_FILE
    from projects.storage import ProjectStore
    from workspace import project_folder_name

    projects = ProjectStore(path=PROJECTS_FILE).list()
    for p in projects:
        if p.id == ref:
            return p
    matches = [p for p in projects if p.name == ref or project_folder_name(p.name) == ref]
    return matches[0] if len(matches) == 1 else None


def _repo_dir(project):
    from workspace import get_workspace_folder

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        return None
    return ws_folder / (project.repo.local_path or "repo")


def _current_task_context() -> Optional[Dict[str, str]]:
    """Title, description and result-so-far of the task this run belongs to.

    Looks at the same two sources agents/hooks.py._task_id() does: the
    contextvar the chat/task pipelines set around an agent run, then the
    AGENT_TASK_ID env var node_run.py exports for the orchestrator loop. Both
    are empty in a plain chat turn with no tracked task, which is a normal
    case here, not an error — the caller is told so in the PR/MR body.
    """
    task_id = ""
    try:
        task_id = current_task_id.get() or ""
    except Exception:
        task_id = ""
    if not task_id:
        task_id = os.environ.get("AGENT_TASK_ID", "").strip()
    if not task_id:
        return None
    try:
        tid = UUID(str(task_id))
    except ValueError:
        return None

    from tasks.service import get_task, get_task_result

    task = get_task(tid)
    if task is None:
        return None
    return {
        "key": task.key or "",
        "title": task.title or "",
        "description": (task.description or "").strip(),
        "output": (get_task_result(tid) or "").strip(),
    }


def _build_body(task_ctx: Optional[Dict[str, str]]) -> str:
    if not task_ctx:
        return (
            "No task context was available when this was published. The agent "
            "was not running as part of a tracked task, so there is no title, "
            "description or result to summarize here."
        )
    parts = [f"## {task_ctx['title']}"] if task_ctx["title"] else []
    if task_ctx["description"]:
        parts.append(task_ctx["description"])
    if task_ctx["output"]:
        parts.append("### Result so far\n\n" + task_ctx["output"])
    return "\n\n".join(parts) or "(the task had no title, description or result to summarize)"


def _default_branch_name(task_ctx: Optional[Dict[str, str]], title: str) -> str:
    if task_ctx and task_ctx.get("key"):
        base = task_ctx["key"].lower()
    elif task_ctx and task_ctx.get("title"):
        base = _slugify(task_ctx["title"], 24)
    else:
        base = "task"
    return f"agent/{base}-{_slugify(title, 30)}"


def run_git_publish(
    project: str, *, branch: Optional[str] = None, title: str, body: str = "",
    base: Optional[str] = None, draft: bool = True, open_pr: bool = True,
) -> Dict[str, Any]:
    """Shared implementation behind the ``git_publish`` tool and the dashboard route.

    One function so the branch-protection refusals apply identically whether
    the call came from an agent or a person pressing the button on the
    project page — see dashboard/backend/routes/projects.py.
    """
    from connectors.git import git_ops
    from connectors.git.git_ops import GitOpsError
    from connectors.git.providers import GitProviderError, get_provider, remote_id_from_url

    if not title.strip():
        return _err("title is required: it becomes both the commit message and the PR/MR title")

    proj = _resolve_project(project)
    if proj is None:
        return _err(f"No project found matching {project!r}", code="not_found")

    provider_name = str(getattr(proj.repo.type, "value", proj.repo.type) or "")
    if provider_name not in ("github", "gitlab"):
        return _err(
            f"Project {proj.name!r} is not connected to a GitHub or GitLab repo "
            "(git_publish only knows how to open a pull/merge request on those two)",
            code="unsupported_repo",
        )

    repo_dir = _repo_dir(proj)
    if repo_dir is None or not repo_dir.exists():
        return _err(f"Repo directory not found for project {proj.name!r}. Clone it first.", code="not_found")

    remote_id = proj.repo.remote_id
    if not remote_id:
        origin = git_ops.remote_url(repo_dir)
        remote_id = remote_id_from_url(origin) if origin else None
    if not remote_id:
        return _err(
            "Could not determine the owner/repo for this project's remote. "
            "Connect it to a repo through the Projects page first",
            code="unresolved_remote",
        )

    try:
        provider = get_provider(provider_name)
    except GitProviderError as e:
        return _err(str(e), code="provider_error")

    try:
        repo_default = provider.default_branch(remote_id)
    except GitProviderError as e:
        return _err(str(e), code="provider_error")

    target_base = (base or "").strip() or repo_default
    task_ctx = _current_task_context()
    branch_name = (branch or "").strip() or _default_branch_name(task_ctx, title)

    # Branch protection: a publish always goes through its own branch. Checked
    # before anything touches the repo, so a bad request never leaves a
    # half-made branch/commit behind.
    if branch_name == repo_default:
        return _err(
            f"Refusing to push to the repo's default branch ({repo_default!r}). "
            "Publish through a branch, not directly onto it.",
            code="branch_protection",
        )
    if branch_name == target_base:
        return _err(
            f"Refusing to open a pull/merge request from {branch_name!r} onto itself.",
            code="branch_protection",
        )

    try:
        git_ops.create_branch(repo_dir, branch_name)
        commit_sha = git_ops.commit_all(repo_dir, title)
        git_ops.push(repo_dir, branch_name, provider=provider_name)
    except GitOpsError as e:
        return _err(str(e), code="git_error")

    result: Dict[str, Any] = {
        "branch": branch_name, "commit": commit_sha, "pushed": True, "pr_url": None,
    }

    if open_pr:
        pr_body = body.strip() or _build_body(task_ctx)
        try:
            if provider_name == "github":
                pr = provider.create_pull_request(
                    remote_id, title=title, body=pr_body, head=branch_name,
                    base=target_base, draft=draft,
                )
            else:
                pr = provider.create_merge_request(
                    remote_id, title=title, body=pr_body, head=branch_name,
                    base=target_base, draft=draft,
                )
            result["pr_url"] = pr.get("url")
            result["pr_number"] = pr.get("number")
        except GitProviderError as e:
            # The push already succeeded — say so, plus why the PR/MR did not,
            # rather than reporting the whole call as a failure.
            result["pr_error"] = str(e)

    record_entity("project", proj.id, "updated", proj.name)
    return _ok(result)


class GitPublishInput(BaseModel):
    project: str = Field(..., description="Project id or name to publish from")
    branch: Optional[str] = Field(
        None,
        description="Branch to create/push. Defaults to agent/<task-key-or-slug>-<title-slug>",
    )
    title: str = Field(..., min_length=1, description="Commit message, and the PR/MR title")
    body: str = Field(
        "",
        description=(
            "PR/MR description. When left empty it is built from the task this "
            "run belongs to: its title, description and result so far"
        ),
    )
    base: Optional[str] = Field(None, description="Target branch for the PR/MR; defaults to the repo's default branch")
    draft: bool = Field(True, description="Open the PR/MR as a draft")
    open_pr: bool = Field(True, description="Open a pull/merge request after pushing; false just commits and pushes")


@tool("git_publish", args_schema=GitPublishInput)
def git_publish(
    project: str, branch: Optional[str] = None, title: str = "", body: str = "",
    base: Optional[str] = None, draft: bool = True, open_pr: bool = True,
) -> str:
    """Commit the project's working tree, push a branch, and open a pull/merge request.

    This is the only tool that sends workspace content to GitHub or GitLab. It
    never force pushes, and it never pushes to the repo's default branch: a
    change always lands on its own branch (agent/<task>-<slug> unless you name
    one), so whatever review already protects the default branch still applies.
    A small denylist (.env, *.pem, id_rsa*, plus anything the repo's own
    .gitignore already excludes) is never staged, even if it falls inside the
    change. Refuses outright when there is nothing to commit.

    Leave `body` empty to have it built from the task you are working on: its
    title, description and result so far. Or write your own summary.
    Set `open_pr=False` to just commit and push without opening anything.
    """
    result = run_git_publish(
        project, branch=branch, title=title, body=body, base=base,
        draft=draft, open_pr=open_pr,
    )
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


GIT_PUBLISH_TOOLS = [git_publish]

__all__ = ["git_publish", "GIT_PUBLISH_TOOLS", "run_git_publish"]
