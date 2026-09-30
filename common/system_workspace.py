"""
The system workspace: a workspace whose project is a git clone of this very
repository, kept under the state directory, where the doctor and a scheduled
maintenance loop can propose fixes to the service without ever touching the
running instance.

Three rules shape everything here, and each is enforced in code rather than
left to a prompt:

1. **The copy is never the working tree.** Every git operation goes through
   :func:`copy_dir`, which refuses unless the directory is the clone under
   ``<WORKSPACES_ROOT>/system/repo``, is its own git top level (so a missing
   clone can never make git walk up into ``PROJECT_ROOT``'s repository), and is
   not ``PROJECT_ROOT`` or one of its parents.
2. **Nothing leaves.** The clone's push URL is set to a value git cannot push
   to, and the loop's agents hold no push, shell or outbound tool (the
   capability guard's ``system_workspace_no_push`` rule, tools/capabilities.py).
   A human fetches a branch out of the copy with :func:`fetch_command`.
3. **Patches are branches.** A fix lands only as a commit on
   ``system/<YYYY-MM-DD>-<task slug>``; the copy's default branch only ever
   moves by a fast forward from the real repository (:func:`ensure_clone`).

Seeding (:func:`ensure_system_workspace`) is additive and idempotent: fixed ids
for the project, the flow, the loop and the scheduled job, each created only
when missing, so an operator's edits survive every restart. The clone itself is
made lazily, on the first sync, never at startup: a fresh install should not
spend its first minute copying a repository nobody asked for yet.

See docs/system-workspace.md.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.paths import PROJECT_ROOT

log = logging.getLogger(__name__)

# ── fixed identities ─────────────────────────────────────────────────────────

WORKSPACE = "system"
PROJECT_ID = "system-agents-hub-copy"
PROJECT_NAME = "Agents Hub (copy)"
REPO_DIRNAME = "repo"
FLOW_ID = "system_maintenance"
LOOP_ID = "system_loop"
#: A scheduled job's id is a UUID; a name-based one keeps it fixed across
#: installs so seeding stays idempotent.
JOB_ID = uuid.uuid5(uuid.NAMESPACE_URL, "agents-hub:system-workspace:maintenance-loop-job")
JOB_TITLE = "System maintenance loop"
DEFAULT_EVERY_HOURS = 6

#: The two agents the loop runs. The capability guard treats these ids as
#: system workspace agents wherever they are saved or built.
SYSTEM_LOOP_AGENTS = ("system_doctor", "system_engineer")

BRANCH_PREFIX = "system/"
#: Where a fresh clone's push URL points: a value git refuses to push to.
NO_PUSH_URL = "no-push://the-system-copy-never-pushes"
COMMIT_AUTHOR = "Agents Hub system loop <system-loop@agents-hub.local>"
_COMMITTER_NAME = "Agents Hub system loop"
_COMMITTER_EMAIL = "system-loop@agents-hub.local"
TASK_TRAILER = "System-Task"
_SYNC_STAMP = "agents_hub_synced_at"

GIT_TIMEOUT = 60
CLONE_TIMEOUT = 600
MAX_TEST_OUTPUT_CHARS = 12_000
MAX_PATCH_BYTES = 60 * 1024

EXIT_CRITERION = (
    "Every finding of the diagnostics and the error search is either healthy again "
    "or has an open [system] task; every code task has a branch with a passing test "
    "run attached."
)


class SystemCopyError(RuntimeError):
    """A refusal or failure on the repository copy. The message is UI safe."""


# ── settings and locations ───────────────────────────────────────────────────

def enabled() -> bool:
    try:
        from common.config import settings
        return bool(getattr(settings, "system_workspace", True))
    except Exception:  # noqa: BLE001 - an unreadable config means the default, which is on
        return True


def workspace_dir() -> Path:
    """The system workspace folder, whether or not it exists yet."""
    from workspace import storage
    return (storage.WORKSPACES_ROOT / WORKSPACE).resolve()


def repo_dir() -> Path:
    return workspace_dir() / REPO_DIRNAME


def _check_location(path: Path) -> Path:
    """Constraint 2: the path must be the clone under the system workspace
    folder, and neither PROJECT_ROOT nor anything containing it."""
    resolved = Path(path).resolve()
    root = Path(PROJECT_ROOT).resolve()
    if resolved == root or resolved in root.parents:
        raise SystemCopyError(
            f"refusing to work in {resolved}: that is the running instance's own "
            "tree, and the system workspace only ever works on its copy")
    ws = workspace_dir()
    if resolved != ws / REPO_DIRNAME:
        raise SystemCopyError(
            f"refusing to work in {resolved}: the system copy lives at {ws / REPO_DIRNAME}")
    return resolved


def copy_dir() -> Path:
    """The clone, checked: at the right place, present, and its own git top
    level. Every git operation in this module and in tools/system_ops.py goes
    through here first."""
    path = _check_location(repo_dir())
    if not (path / ".git").exists():
        raise SystemCopyError(
            "the repository copy does not exist yet: run a sync first "
            "(system_repo_sync, or POST /api/system/sync)")
    top = _git(["rev-parse", "--show-toplevel"], cwd=path, check=False)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != path:
        raise SystemCopyError(f"{path} is not the top of its own git repository")
    return path


# ── git plumbing ─────────────────────────────────────────────────────────────

def _git_env() -> Dict[str, str]:
    env = os.environ.copy()
    # Never prompt, never read a pager, never walk above the copy.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["GIT_CEILING_DIRECTORIES"] = str(workspace_dir())
    env.setdefault("GIT_COMMITTER_NAME", _COMMITTER_NAME)
    env.setdefault("GIT_COMMITTER_EMAIL", _COMMITTER_EMAIL)
    return env


def _git(args: List[str], *, cwd: Path, timeout: int = GIT_TIMEOUT,
         check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=_git_env())
    except subprocess.TimeoutExpired as exc:
        raise SystemCopyError(f"git {args[0]} timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise SystemCopyError("git is not installed on this host") from exc
    if check and proc.returncode != 0:
        raise SystemCopyError((proc.stderr or proc.stdout).strip() or f"git {args[0]} failed")
    return proc


def _is_git_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()


def default_branch(repo: Path) -> str:
    """The copy's default branch: whatever origin's HEAD pointed at when it was
    cloned (the branch PROJECT_ROOT had checked out), else main or master."""
    head = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo, check=False)
    name = head.stdout.strip()
    if head.returncode == 0 and name.startswith("origin/"):
        return name[len("origin/"):]
    for candidate in ("main", "master"):
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}"],
                cwd=repo, check=False).returncode == 0:
            return candidate
    return _git(["branch", "--show-current"], cwd=repo, check=False).stdout.strip() or "main"


def _current_branch(repo: Path) -> str:
    return _git(["branch", "--show-current"], cwd=repo, check=False).stdout.strip()


def _head(repo: Path) -> str:
    return _git(["rev-parse", "--short", "HEAD"], cwd=repo, check=False).stdout.strip()


def _stamp_path(repo: Path) -> Path:
    return repo / ".git" / _SYNC_STAMP


def _read_stamp(repo: Path) -> Optional[str]:
    try:
        return _stamp_path(repo).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_stamp(repo: Path) -> str:
    now = datetime.now(timezone.utc).isoformat()
    try:
        _stamp_path(repo).write_text(now, encoding="utf-8")
    except OSError:
        log.debug("could not write the sync stamp in %s", repo, exc_info=True)
    return now


def _configure_copy(repo: Path) -> None:
    """Repo-local settings every copy carries: an identity for the loop's
    commits, and a push URL git cannot push to (rule 2, a second lock behind
    the tool sets that hold no push tool at all)."""
    _git(["config", "user.name", _COMMITTER_NAME], cwd=repo)
    _git(["config", "user.email", _COMMITTER_EMAIL], cwd=repo)
    _git(["remote", "set-url", "--push", "origin", NO_PUSH_URL], cwd=repo, check=False)


# ── clone and sync ───────────────────────────────────────────────────────────

def probe_clone() -> Dict[str, Any]:
    """A read only look at the copy, for status pages and the doctor."""
    path = repo_dir()
    out: Dict[str, Any] = {"exists": False, "repo_dir": str(path), "head": None,
                           "branch": None, "default_branch": None, "synced_at": None,
                           "dirty": None, "error": None}
    try:
        _check_location(path)
    except SystemCopyError as exc:
        out["error"] = str(exc)
        return out
    if not _is_git_repo(path):
        return out
    try:
        repo = copy_dir()
        out.update(
            exists=True, head=_head(repo), branch=_current_branch(repo),
            default_branch=default_branch(repo), synced_at=_read_stamp(repo),
            dirty=bool(_git(["status", "--porcelain"], cwd=repo, check=False).stdout.strip()),
        )
    except SystemCopyError as exc:
        out["error"] = str(exc)
    return out


def ensure_clone() -> Dict[str, Any]:
    """Make the copy exist and bring its default branch up to date.

    Missing: ``git clone --no-hardlinks PROJECT_ROOT repo`` (no hard links, so
    nothing written in the copy can ever reach an object file the running
    tree shares). Present: ``git fetch origin`` and a fast forward of the
    default branch only; ``system/*`` branches are never touched, and a
    default branch that cannot fast forward is reported, not forced.

    Returns ``{"exists", "repo_dir", "head", "branch", "synced_at", "error"}``
    and never raises.
    """
    path = repo_dir()
    result: Dict[str, Any] = {"exists": False, "repo_dir": str(path), "head": None,
                              "branch": None, "synced_at": None, "error": None}
    try:
        _check_location(path)
        source = Path(PROJECT_ROOT).resolve()
        if not path.exists():
            if not _is_git_repo(source):
                raise SystemCopyError(
                    f"{source} is not a git repository, so there is nothing to copy")
            path.parent.mkdir(parents=True, exist_ok=True)
            _git(["clone", "--no-hardlinks", str(source), str(path)],
                 cwd=path.parent, timeout=CLONE_TIMEOUT)
            repo = copy_dir()
            _configure_copy(repo)
        else:
            repo = copy_dir()
            _git(["fetch", "origin", "--prune"], cwd=repo, timeout=CLONE_TIMEOUT)
            # Follow origin's HEAD if the real tree switched branches.
            _git(["remote", "set-head", "origin", "--auto"], cwd=repo, check=False)
            branch = default_branch(repo)
            if _current_branch(repo) == branch:
                ff = _git(["merge", "--ff-only", f"origin/{branch}"], cwd=repo, check=False)
            else:
                # Updates a branch that is not checked out, fast forward only.
                ff = _git(["fetch", "origin", f"{branch}:{branch}"], cwd=repo, check=False)
            if ff.returncode != 0:
                result["error"] = (
                    f"fetched, but {branch} could not fast forward to origin/{branch}: "
                    + ((ff.stderr or ff.stdout).strip().splitlines() or ["unknown reason"])[-1])
        result.update(exists=True, head=_head(repo), branch=_current_branch(repo),
                      synced_at=_write_stamp(repo))
    except SystemCopyError as exc:
        result["error"] = str(exc)
        result["exists"] = _is_git_repo(path)
    except Exception as exc:  # noqa: BLE001 - the contract is a result dict, never an exception
        log.warning("system copy sync failed", exc_info=True)
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


# ── branches ─────────────────────────────────────────────────────────────────

def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"^system-", "", slug)
    return (slug[:limit].rstrip("-")) or "task"


def branch_name(task_title: str, *, today: Optional[datetime] = None) -> str:
    day = (today or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"{BRANCH_PREFIX}{day}-{slugify(task_title)}"


def _branch_task_id(repo: Path, branch: str) -> Optional[str]:
    body = _git(["log", "-1", "--format=%B", branch], cwd=repo, check=False).stdout
    m = re.search(rf"^{TASK_TRAILER}:\s*(\S+)\s*$", body, re.MULTILINE)
    return m.group(1) if m else None


def list_branches() -> List[Dict[str, Any]]:
    """Every ``system/*`` branch of the copy, newest first. Empty when there
    is no copy."""
    try:
        repo = copy_dir()
    except SystemCopyError:
        return []
    fmt = "%(refname:short)%09%(objectname:short)%09%(committerdate:iso-strict)"
    out = _git(["for-each-ref", f"--format={fmt}", "--sort=-committerdate",
                f"refs/heads/{BRANCH_PREFIX}"], cwd=repo, check=False).stdout
    now = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, commit, created = parts
        age = None
        try:
            age = round((now - datetime.fromisoformat(created)).total_seconds() / 86400, 2)
        except ValueError:
            pass
        rows.append({"name": name, "commit": commit, "created_at": created,
                     "age_days": age, "task_id": _branch_task_id(repo, name)})
    return rows


def prune_branches(older_than_days: int) -> List[str]:
    """Delete ``system/*`` branches older than N days, from the copy only.
    The checked out branch is never deleted."""
    repo = copy_dir()
    current = _current_branch(repo)
    deleted: List[str] = []
    for b in list_branches():
        if b["name"] == current or not b["name"].startswith(BRANCH_PREFIX):
            continue
        if b["age_days"] is not None and b["age_days"] >= older_than_days:
            if _git(["branch", "-D", b["name"]], cwd=repo, check=False).returncode == 0:
                deleted.append(b["name"])
    return deleted


def _host_visible_path(path: Path) -> str:
    try:
        from managers.container_manager import _host_path
        return _host_path(path)
    except Exception:  # noqa: BLE001 - without the helper the local path is the best answer
        return str(path)


def _in_container() -> bool:
    try:
        from common.hostnet import in_container
        return in_container()
    except Exception:  # noqa: BLE001 - fall back to the plain marker file
        return Path("/.dockerenv").exists()


def fetch_command(branch: str) -> str:
    """What a human runs in their own clone to take a branch out of the copy.

    When the service runs in a container the copy's path may only exist
    inside it, so a second line (a shell comment, so the block stays
    runnable) says how to get the branch out through a bundle instead.
    """
    path = repo_dir()
    line = f"git fetch {_host_visible_path(path)} {branch}:{branch}"
    if not _in_container():
        return line
    import socket
    bundle = f"/tmp/{slugify(branch.replace('/', '-'), 60)}.bundle"
    hint = (f"# the service runs in docker; if that path is not on this host: "
            f"docker exec {socket.gethostname()} git -C {path} bundle create {bundle} {branch} "
            f"&& docker cp {socket.gethostname()}:{bundle} . "
            f"&& git fetch ./{Path(bundle).name} {branch}:{branch}")
    return f"{line}\n{hint}"


# ── commit, diff, tests ──────────────────────────────────────────────────────

def commit_on_branch(branch: str, message: str, *, task_id: Optional[str] = None,
                     paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """Commit the copy's working tree changes on ``branch`` (created from the
    default branch when missing), then check the default branch out again so
    the next fix starts clean. Refuses anything but a ``system/`` branch
    (rule 3). Uses connectors/git/git_ops.commit_all, so its denylist of
    secrets applies."""
    from connectors.git import git_ops

    repo = copy_dir()
    base = default_branch(repo)
    if not branch.startswith(BRANCH_PREFIX) or branch == base:
        raise SystemCopyError(
            f"refusing to commit on {branch!r}: the system loop commits only on "
            f"{BRANCH_PREFIX}* branches, never on the copy's default branch {base!r}")
    for p in paths or []:
        _safe_relpath(repo, p)
    _switch_carrying_changes(repo, branch, base)
    try:
        if _current_branch(repo) != branch:
            raise SystemCopyError(f"could not switch the copy to {branch}")
        body = message.strip() or f"System fix on {branch}"
        if task_id:
            body += f"\n\n{TASK_TRAILER}: {task_id}"
        try:
            sha = git_ops.commit_all(repo, body, author=COMMIT_AUTHOR, paths=paths or None)
        except git_ops.GitOpsError as exc:
            raise SystemCopyError(str(exc)) from exc
        files = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
                     cwd=repo, check=False).stdout.split()
    finally:
        # Back to the default branch. Anything left uncommitted comes along or
        # blocks the switch; either way the loop never commits on the default.
        _git(["checkout", base], cwd=repo, check=False)
    return {"branch": branch, "sha": sha, "commit": (sha or "")[:12], "files": files,
            "base": base, "repo_dir": str(repo)}


def _switch_carrying_changes(repo: Path, branch: str, base: str) -> None:
    """Check ``branch`` out with the working tree's changes on top.

    A new branch is created from ``base`` and the changes simply come along.
    An existing one (a second fix for the same task) may differ from ``base``
    in the very lines that were edited, so the changes travel through a stash
    and a three way merge; when they conflict with what the branch already
    has, everything is put back on ``base`` as it was and the call refuses.
    """
    from connectors.git import git_ops

    exists = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                  cwd=repo, check=False).returncode == 0
    dirty = bool(_git(["status", "--porcelain"], cwd=repo, check=False).stdout.strip())
    if not exists or not dirty:
        try:
            git_ops.create_branch(repo, branch, from_ref=base)
        except git_ops.GitOpsError as exc:
            raise SystemCopyError(str(exc)) from exc
        return
    _git(["stash", "push", "--include-untracked", "-m", f"system-commit {branch}"], cwd=repo)
    _git(["checkout", branch], cwd=repo)
    if _git(["stash", "pop"], cwd=repo, check=False).returncode == 0:
        return
    # The pop conflicted and the stash is still there: undo the half merge,
    # return to base and restore the work exactly as it was.
    _git(["reset", "--hard", "-q"], cwd=repo, check=False)
    _git(["clean", "-fdq"], cwd=repo, check=False)
    _git(["checkout", base], cwd=repo, check=False)
    _git(["stash", "pop"], cwd=repo, check=False)
    raise SystemCopyError(
        f"these changes conflict with the fix already on {branch}. Nothing was committed and "
        f"the changes are still in the copy on {base}; redo the fix so it applies on top of "
        f"{branch}, or mark the task blocked")


def branch_for_task(task_id: str, title: str) -> str:
    """The branch a task's fixes go on: its existing ``system/`` branch when
    one carries its trailer, else a new dated one."""
    for b in list_branches():
        if b.get("task_id") == str(task_id):
            return b["name"]
    return branch_name(title)


def diff_against_default(branch: str) -> Dict[str, Any]:
    repo = copy_dir()
    if not branch.startswith(BRANCH_PREFIX):
        raise SystemCopyError(f"{branch!r} is not a {BRANCH_PREFIX}* branch")
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo, check=False).returncode != 0:
        raise SystemCopyError(f"the copy has no branch {branch!r}")
    base = default_branch(repo)
    diff = _git(["diff", f"{base}...{branch}"], cwd=repo).stdout
    commit = _git(["rev-parse", branch], cwd=repo).stdout.strip()
    stat = _git(["diff", "--stat", f"{base}...{branch}"], cwd=repo, check=False).stdout.strip()
    return {"branch": branch, "base": base, "commit": commit, "diff": diff, "stat": stat,
            "repo_dir": str(repo)}


def _safe_relpath(repo: Path, rel: str) -> str:
    rel = str(rel or "").strip()
    if not rel:
        raise SystemCopyError("empty path")
    if rel.startswith("-"):
        raise SystemCopyError(f"{rel!r} looks like an option; pass test paths only")
    target = (repo / rel.split("::", 1)[0]).resolve()
    if target != repo and repo not in target.parents:
        raise SystemCopyError(f"{rel!r} is outside the repository copy")
    return rel


def _test_env(tmp_root: str) -> Dict[str, str]:
    """A minimal environment for the copy's tests: no API keys, no database
    URL, no token, and a throwaway state directory, so a test run can never
    touch the running instance's state."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "VIRTUAL_ENV",
            "CONDA_PREFIX", "SYSTEMROOT", "USER")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["AGENTS_HUB_ROOT"] = tmp_root
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["SYSTEM_WORKSPACE"] = "false"
    return env


def _pytest_counts(output: str) -> Dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    tail = "\n".join(output.strip().splitlines()[-5:])
    for key, pattern in (("passed", r"(\d+) passed"), ("failed", r"(\d+) failed"),
                         ("errors", r"(\d+) errors?\b"), ("skipped", r"(\d+) skipped")):
        m = re.search(pattern, tail)
        if m:
            counts[key] = int(m.group(1))
    return counts


def _test_runner() -> str:
    """``docker`` when the system workspace runs agents in containers and a
    daemon answers, else ``local``."""
    try:
        from runtime.entity_launch import execution_mode_for
        mode = execution_mode_for(WORKSPACE)
    except Exception:  # noqa: BLE001 - an unreadable workspace means the local default
        mode = "local"
    if mode != "docker":
        return "local"
    try:
        from tools.run_code import docker_available
        return "docker" if docker_available() else "local"
    except Exception:  # noqa: BLE001 - no docker helper, no docker
        return "local"


def run_tests(paths: Optional[List[str]] = None, timeout: int = 900) -> Dict[str, Any]:
    """``python -m pytest -q <paths>`` in the copy. In a no network container
    when the workspace runs agents in docker, else a subprocess with the copy
    as its working directory and a stripped environment.

    Returns ``{"exit_code", "duration_s", "passed", "failed", "errors",
    "skipped", "runner", "paths", "tail", "truncated"}``.
    """
    repo = copy_dir()
    rels = [_safe_relpath(repo, p) for p in (paths or [])]
    timeout = max(10, min(int(timeout or 900), 3600))
    runner = _test_runner()
    tmp_root = tempfile.mkdtemp(prefix="system-tests-")
    started = time.monotonic()
    try:
        if runner == "docker":
            from managers.container_manager import _host_path, image_tag_for_agent
            cmd = ["docker", "run", "--rm", "--network", "none",
                   "-v", f"{_host_path(repo)}:/repo", "-w", "/repo",
                   "-e", "AGENTS_HUB_ROOT=/tmp/agents_hub", "-e", "SYSTEM_WORKSPACE=false",
                   "--entrypoint", "python", image_tag_for_agent("system_engineer"),
                   "-m", "pytest", "-q", *rels]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", *rels]
            proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                                  timeout=timeout, env=_test_env(tmp_root))
        output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes)
                  else (exc.stdout or "")) + f"\n[timed out after {timeout}s]"
        exit_code = -1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    truncated = len(output) > MAX_TEST_OUTPUT_CHARS
    return {
        "exit_code": exit_code,
        "duration_s": round(time.monotonic() - started, 2),
        **_pytest_counts(output),
        "runner": runner,
        "paths": rels,
        "tail": output[-MAX_TEST_OUTPUT_CHARS:],
        "truncated": truncated,
    }


# ── the patch as a task result ───────────────────────────────────────────────

def _tests_markdown(tests: Any) -> str:
    if isinstance(tests, dict) and tests:
        parts = [f"exit code {tests.get('exit_code', '?')}"]
        for key in ("passed", "failed", "errors", "skipped"):
            if tests.get(key):
                parts.append(f"{tests[key]} {key}")
        if tests.get("duration_s") is not None:
            parts.append(f"{tests['duration_s']} s")
        if tests.get("runner"):
            parts.append(f"runner {tests['runner']}")
        line = ", ".join(parts)
        if tests.get("paths"):
            line += f" ({' '.join(tests['paths'])})"
        return line
    text = str(tests or "").strip()
    return text or "no test run was attached"


def patch_markdown(info: Dict[str, Any], tests: Any = "") -> Dict[str, Any]:
    """The task result for a patch: a machine readable first line the task
    page parses, then the human part."""
    branch = info["branch"]
    command = fetch_command(branch)
    marker = {"branch": branch, "repo_dir": info["repo_dir"], "commit": info["commit"],
              "fetch_command": command}
    diff = info.get("diff") or ""
    raw = diff.encode("utf-8")
    truncated = len(raw) > MAX_PATCH_BYTES
    if truncated:
        diff = raw[:MAX_PATCH_BYTES].decode("utf-8", errors="ignore")
    lines = [
        f"<!-- system-patch {json.dumps(marker, ensure_ascii=False)} -->",
        "## System patch",
        "",
        f"**Branch:** `{branch}`",
        f"**Commit:** `{info['commit']}`",
        f"**Base:** `{info.get('base', '')}`",
        "",
        "Fetch it into your own clone, review it and push it yourself:",
        "",
        "```bash",
        command,
        "```",
        "",
        f"**Tests:** {_tests_markdown(tests)}",
        "",
        "```diff",
        diff.rstrip("\n"),
        "```",
    ]
    if truncated:
        lines += ["", f"The diff was cut at {MAX_PATCH_BYTES // 1024} KB; fetch the "
                      "branch to see all of it."]
    return {"markdown": "\n".join(lines) + "\n", "truncated": truncated, "marker": marker}


# ── seeding ──────────────────────────────────────────────────────────────────

def _seed_metadata() -> Dict[str, Any]:
    from common.bootstrap import BOOTSTRAP_WORKSPACES_ROOT
    path = BOOTSTRAP_WORKSPACES_ROOT / WORKSPACE / ".workspace.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {"name": WORKSPACE}


def _ensure_workspace() -> bool:
    from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata

    is_new = not get_workspace_metadata(WORKSPACE)
    create_workspace_folder(WORKSPACE)
    if not is_new:
        return False
    seed = _seed_metadata()
    updates = {k: v for k, v in seed.items() if k != "name"}
    allowed = list(updates.get("allowed_agents") or [])
    try:
        from agents.registry import get_agent
        if get_agent("claude-code-agenthub") is not None and "claude-code-agenthub" not in allowed:
            allowed.append("claude-code-agenthub")
    except Exception:  # noqa: BLE001 - the optional imported agent is just left out
        log.debug("claude code agent lookup failed", exc_info=True)
    if allowed:
        updates["allowed_agents"] = allowed
    update_workspace_metadata(WORKSPACE, updates)
    return True


def _ensure_project() -> bool:
    from common.paths import PROJECTS_FILE
    from projects.models import Project, ProjectType, RepoConfig, RepoType
    from projects.storage import ProjectStore

    store = ProjectStore(path=PROJECTS_FILE)
    if store.get(PROJECT_ID) is not None:
        return False
    store.add(Project(
        id=PROJECT_ID, name=PROJECT_NAME, workspace=WORKSPACE, type=ProjectType.code,
        description=("A git clone of this Agents Hub repository, kept under the state "
                     "directory. The maintenance loop commits fixes here on system/ "
                     "branches; a human fetches, reviews and pushes them."),
        repo=RepoConfig(type=RepoType.local, local_path=REPO_DIRNAME, branch=None),
        tags=["system"],
    ))
    return True


TRIAGE_TASK = (
    "Run run_diagnostics and search_errors. For every finding that is not healthy and "
    "is not already an open [system] task in this workspace, create one task: title "
    "prefixed [system], description with the check id, the evidence, a suggested fix "
    "and a line `code: yes` or `code: no`. Never repeat a task that exists. End with a "
    "short report listing the task ids."
)
PATCH_TASK = (
    "Take the open [system] tasks marked `code: yes`. Sync the copy with "
    "system_repo_sync, make the smallest fix, run the relevant tests with "
    "system_run_tests, commit with system_commit, attach the diff and the test result "
    "with system_attach_patch, then mark the task done, or blocked with the reason. "
    "Never push and never open a pull request: a human reviews and pushes."
)


def flow_definition() -> Dict[str, Any]:
    """The maintenance flow in the stored shape (flow/store.py)."""
    def node(nid: str, agent: str, label: str, task: str, x: int) -> Dict[str, Any]:
        return {"id": nid, "type": "flowNode", "agent_id": agent,
                "position": {"x": x, "y": 120},
                "data": {"agent_id": agent, "label": label, "category": "agent",
                         "nodeTask": task}}
    return {
        "id": FLOW_ID,
        "name": "System maintenance",
        "description": ("Triage the service's own health into [system] tasks, then patch "
                        "the ones that need code on system/ branches of the copy."),
        "workspace": WORKSPACE,
        "entry_point": "triage",
        "nodes": [node("triage", "system_doctor", "Triage", TRIAGE_TASK, 80),
                  node("patch", "system_engineer", "Patch", PATCH_TASK, 380)],
        "edges": [{"id": "e-triage-patch", "source": "triage", "target": "patch"}],
    }


def _ensure_flow() -> bool:
    from flow import store as flow_store
    if flow_store.get_flow(FLOW_ID) is not None:
        return False
    flow_store.save_flow(flow_definition())
    return True


def _ensure_loop() -> bool:
    from loops import store as loop_store
    from loops.models import Loop
    if loop_store.get_loop(LOOP_ID) is not None:
        return False
    loop_store.save_loop(Loop(
        loop_id=LOOP_ID, name="System loop",
        description=("Diagnose the service and propose fixes as branches of the "
                     "repository copy. Never pushes."),
        workspace=WORKSPACE, flow_id=FLOW_ID, exit_criterion=EXIT_CRITERION,
        max_iterations=2, min_iterations=1, cost_ceiling=2.0, evaluator_mode="model",
    ))
    return True


def cron_for(every_hours: int) -> str:
    hours = max(1, min(int(every_hours or DEFAULT_EVERY_HOURS), 24))
    return "0 0 * * *" if hours == 24 else f"0 */{hours} * * *"


def every_hours_of(cron: Optional[str]) -> Optional[int]:
    m = re.fullmatch(r"0 \*/(\d+) \* \* \*", (cron or "").strip())
    if m:
        return int(m.group(1))
    if (cron or "").strip() == "0 0 * * *":
        return 24
    return None


def _next_cron(cron: str) -> datetime:
    from croniter import croniter
    return croniter(cron, datetime.now(timezone.utc)).get_next(datetime)


def _ensure_job() -> bool:
    from plans.models import JobKind, JobStatus, Recurrence, ScheduledJob
    from plans.service import plan_store
    if plan_store.get(JOB_ID) is not None:
        return False
    cron = cron_for(DEFAULT_EVERY_HOURS)
    plan_store.add(ScheduledJob(
        id=JOB_ID, kind=JobKind.loop, title=JOB_TITLE,
        message="Run the system maintenance loop over the repository copy.",
        run_at=_next_cron(cron), recurrence=Recurrence.cron, cron=cron,
        status=JobStatus.paused, workspace=WORKSPACE, created_by="system",
        loop_id=LOOP_ID, max_concurrent=1,
    ))
    return True


def ensure_system_workspace() -> bool:
    """Create whatever of the system workspace is missing: metadata and
    folder, the project record, the flow, the loop and the (paused) job.
    Never clones. Returns True when anything was created."""
    created = False
    for step in (_ensure_workspace, _ensure_project, _ensure_flow, _ensure_loop, _ensure_job):
        try:
            created = bool(step()) or created
        except Exception:  # noqa: BLE001 - one missing piece must not stop the rest; the doctor reports it
            log.warning("system workspace: %s failed", step.__name__, exc_info=True)
    return created


# ── schedule and status ──────────────────────────────────────────────────────

def set_schedule(enabled_: bool, every_hours: int = DEFAULT_EVERY_HOURS) -> Dict[str, Any]:
    """Turn the loop's job on or off and rewrite its cron. The next run is
    recomputed from now, so enabling never fires a stale slot at once."""
    from plans.models import JobStatus, Recurrence
    from plans import service as plans_service

    if plans_service.get_job(JOB_ID) is None:
        ensure_system_workspace()
    cron = cron_for(every_hours)
    job = plans_service.update_job(
        JOB_ID, cron=cron, recurrence=Recurrence.cron, run_at=_next_cron(cron),
        status=JobStatus.scheduled if enabled_ else JobStatus.paused,
    )
    if job is None:
        raise SystemCopyError("the system loop's scheduled job is missing and could not be seeded")
    return _loop_status()


def _last_run() -> Optional[Dict[str, Any]]:
    try:
        from loops import store as loop_store
        runs = loop_store.list_runs(LOOP_ID, limit=1)
    except Exception:  # noqa: BLE001 - a status page shows "no run" rather than failing
        log.debug("loop run lookup failed", exc_info=True)
        return None
    if not runs:
        return None
    r = runs[0]
    return {"loop_run_id": r.loop_run_id, "status": r.status, "started_at": r.started_at,
            "finished_at": r.finished_at, "iterations_done": r.iterations_done,
            "stop_reason": r.stop_reason, "total_cost": r.total_cost, "task_id": r.task_id,
            "error": r.error}


def _loop_status() -> Dict[str, Any]:
    out: Dict[str, Any] = {"loop_id": LOOP_ID, "flow_id": FLOW_ID, "job_id": str(JOB_ID),
                           "scheduled": False, "recurrence": None, "every_hours": None,
                           "next_run_at": None, "last_run": _last_run()}
    try:
        from plans.models import JobStatus
        from plans import service as plans_service
        job = plans_service.get_job(JOB_ID)
    except Exception:  # noqa: BLE001 - no plan store, no schedule to report
        log.debug("job lookup failed", exc_info=True)
        job = None
    if job is not None:
        scheduled = job.status == JobStatus.scheduled
        out.update(scheduled=scheduled, recurrence=job.cron, every_hours=every_hours_of(job.cron),
                   next_run_at=job.run_at.isoformat() if scheduled and job.run_at else None)
    return out


def status() -> Dict[str, Any]:
    """Everything the Health page's system section shows, read only."""
    return {
        "enabled": enabled(),
        "workspace": WORKSPACE,
        "project_id": PROJECT_ID,
        "repo_dir": str(repo_dir()),
        "clone": probe_clone(),
        "loop": _loop_status(),
        "branches": list_branches(),
    }


def is_system_workspace_agent(agent_id: str, owner_workspace: Optional[str] = None) -> bool:
    """Whether the capability guard's system workspace rule applies to an agent."""
    return agent_id in SYSTEM_LOOP_AGENTS or (owner_workspace or "") == WORKSPACE


__all__ = [
    "WORKSPACE", "PROJECT_ID", "FLOW_ID", "LOOP_ID", "JOB_ID", "SYSTEM_LOOP_AGENTS",
    "SystemCopyError", "enabled", "workspace_dir", "repo_dir", "copy_dir",
    "probe_clone", "ensure_clone", "list_branches", "prune_branches", "fetch_command",
    "commit_on_branch", "branch_for_task", "branch_name", "diff_against_default",
    "run_tests", "patch_markdown", "flow_definition", "ensure_system_workspace",
    "set_schedule", "status", "cron_for", "every_hours_of", "is_system_workspace_agent",
    "default_branch", "slugify",
]
