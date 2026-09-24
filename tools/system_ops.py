"""
System operations: the tools the system workspace's engineer uses to turn a
diagnosis into a reviewable patch on a copy of this repository.

The tools are thin. Everything that matters (where the copy is, which branch a
commit may land on, how tests run, what the patch looks like) lives in
``common/system_workspace.py``, so the routes, the tools and the tests all go
through the same checks. Every tool resolves the copy through
``system_workspace.copy_dir()``, which refuses anything but the clone under the
system workspace folder: none of them can reach the running instance's tree.

What is deliberately missing: a push. The branch stays in the copy; a human
fetches it (the task result carries the command), reviews it and pushes it
from their own clone. The capability guard makes sure no agent of the system
workspace holds a push, shell or outbound tool either (see
``tools.capabilities.check_system_workspace_tools``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok


def _ok(payload: Dict[str, Any]) -> str:
    return json_ok(payload, default=str)


def _err(message: str, *, code: str = "bad_request",
         extra: Optional[Dict[str, Any]] = None) -> str:
    return json_err(message, code=code, extra=extra, default=str)


def _task(task_id: str):
    from uuid import UUID
    from tasks import service as tasks_service
    try:
        return tasks_service.get_task(UUID(str(task_id)))
    except (ValueError, TypeError):
        return None


class NoArgs(BaseModel):
    pass


@tool("system_repo_sync", args_schema=NoArgs)
def system_repo_sync() -> str:
    """Make the repository copy exist and bring its default branch up to date
    with the real repository (a clone the first time, then a fetch and a fast
    forward). Call it before starting a fix. Your system/ branches are never
    touched. Returns the copy's path, head commit and checked out branch."""
    from common import system_workspace as sw
    result = sw.ensure_clone()
    if result.get("error") and not result.get("exists"):
        return _err(result["error"], code="sync_failed", extra={"clone": result})
    return _ok({"clone": result})


class RunTestsInput(BaseModel):
    paths: List[str] = Field(
        default_factory=list,
        description="Test files or node ids relative to the copy, e.g. tests/test_doctor.py. "
                    "Empty runs the whole suite, which is slow: name the relevant files.")
    timeout: int = Field(900, ge=10, le=3600, description="Seconds before the run is killed")


@tool("system_run_tests", args_schema=RunTestsInput)
def system_run_tests(paths: Optional[List[str]] = None, timeout: int = 900) -> str:
    """Run pytest in the repository copy and report exit code, duration,
    passed and failed counts and the tail of the output. Runs in a container
    with no network when the system workspace runs agents in docker, else as
    a subprocess with a stripped environment and a throwaway state directory.
    Attach the result to the patch with system_attach_patch."""
    from common import system_workspace as sw
    try:
        return _ok({"tests": sw.run_tests(list(paths or []), timeout=timeout)})
    except sw.SystemCopyError as exc:
        return _err(str(exc), code="refused")
    except Exception as exc:  # noqa: BLE001 - a tool reports failure in its envelope
        return _err(f"Failed to run the tests: {exc}", code="internal")


class CommitInput(BaseModel):
    task_id: str = Field(..., description="The [system] task this fix belongs to")
    message: str = Field(..., description="Commit message: what changed and why, in one or two lines")
    paths: List[str] = Field(
        default_factory=list,
        description="Limit the commit to these paths (relative to the copy). Empty commits every change.")


@tool("system_commit", args_schema=CommitInput)
def system_commit(task_id: str, message: str, paths: Optional[List[str]] = None) -> str:
    """Commit your changes in the copy on the task's branch,
    system/<date>-<task slug> (created from the default branch, or reused when
    the task already has one), then switch the copy back to its default
    branch. Never commits on the default branch and never pushes. Files that
    look like secrets (.env, *.pem, id_rsa*) are never staged. Returns the
    branch, the commit and the changed files."""
    from common import system_workspace as sw
    task = _task(task_id)
    if task is None:
        return _err(f"Task '{task_id}' not found", code="not_found")
    try:
        branch = sw.branch_for_task(str(task.id), task.title)
        return _ok(sw.commit_on_branch(branch, message, task_id=str(task.id),
                                       paths=list(paths or [])))
    except sw.SystemCopyError as exc:
        return _err(str(exc), code="refused")
    except Exception as exc:  # noqa: BLE001 - a tool reports failure in its envelope
        return _err(f"Failed to commit: {exc}", code="internal")


class AttachPatchInput(BaseModel):
    task_id: str = Field(..., description="The [system] task to attach the patch to")
    branch: str = Field(..., description="The system/ branch system_commit returned")
    tests: Union[Dict[str, Any], str] = Field(
        "", description="The `tests` object system_run_tests returned, or a sentence")


@tool("system_attach_patch", args_schema=AttachPatchInput)
def system_attach_patch(task_id: str, branch: str, tests: Union[Dict[str, Any], str] = "") -> str:
    """Write the patch into the task's result: the branch, the commit, the
    command a human runs to fetch it, the test summary and the diff against
    the copy's default branch (cut at 60 KB). The task page shows it with a
    fetch button. Call it after system_commit and system_run_tests."""
    from uuid import UUID
    from common import system_workspace as sw
    from tasks import service as tasks_service
    task = _task(task_id)
    if task is None:
        return _err(f"Task '{task_id}' not found", code="not_found")
    try:
        info = sw.diff_against_default(branch)
        info["commit"] = info["commit"][:12]
        patch = sw.patch_markdown(info, tests)
        tasks_service.set_task_result(UUID(str(task.id)), patch["markdown"],
                                      run_id=f"system-patch:{branch}", agent_id="system_engineer")
        return _ok({"task_id": str(task.id), "branch": branch, "commit": info["commit"],
                    "fetch_command": patch["marker"]["fetch_command"],
                    "stat": info["stat"], "truncated": patch["truncated"]})
    except sw.SystemCopyError as exc:
        return _err(str(exc), code="refused")
    except Exception as exc:  # noqa: BLE001 - a tool reports failure in its envelope
        return _err(f"Failed to attach the patch: {exc}", code="internal")


class PruneBranchesInput(BaseModel):
    older_than_days: int = Field(14, ge=1, le=3650, description="Delete system/ branches older than this")
    user_approved: bool = Field(False, description="Set only after the user has explicitly agreed")


@tool("system_prune_branches", args_schema=PruneBranchesInput)
def system_prune_branches(older_than_days: int = 14, user_approved: bool = False) -> str:
    """Delete system/ branches of the copy older than a cutoff. Refuses until
    the user has approved it. Only the copy is touched; a branch a human
    already fetched lives on in their clone."""
    from common import system_workspace as sw
    if not user_approved:
        from tools.approval import approval_required_text
        return approval_required_text(
            "system_prune_branches", f"older than {older_than_days} days",
            f"delete every system/ branch of the repository copy older than "
            f"{older_than_days} days, with the unfetched patches on them")
    try:
        deleted = sw.prune_branches(older_than_days)
        return _ok({"deleted": deleted, "older_than_days": older_than_days})
    except sw.SystemCopyError as exc:
        return _err(str(exc), code="refused")
    except Exception as exc:  # noqa: BLE001 - a tool reports failure in its envelope
        return _err(f"Failed to prune branches: {exc}", code="internal")


#: The engineer's tools over the copy. system_prune_branches is the one that
#: destroys something, and it is approval gated like the service action tools.
SYSTEM_OPS_TOOLS = [
    system_repo_sync, system_run_tests, system_commit, system_attach_patch,
    system_prune_branches,
]

__all__ = [
    "system_repo_sync", "system_run_tests", "system_commit", "system_attach_patch",
    "system_prune_branches", "SYSTEM_OPS_TOOLS",
]
