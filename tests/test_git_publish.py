"""The git write path: connectors/git/git_ops.py's new commit/push helpers,
connectors/git/providers.py's PR/MR creation, and the git_publish tool + route
that tie them together.

Everything that touches an actual git process uses a real temporary repo with
a bare "origin" next to it in tmp_path, so push is exercised for real, offline
— no network, no real GitHub/GitLab call. Anything that would hit a provider's
HTTP API is monkeypatched at the ``_post``/``get_repo`` layer instead.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.git import git_ops
from connectors.git.git_ops import GitOpsError
from connectors.git.providers import GitHubProvider, GitLabProvider, remote_id_from_url
import connectors.git.store as git_store


# ── repo fixtures ────────────────────────────────────────────────────────────

def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(repo_dir), capture_output=True, text=True,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


@pytest.fixture
def bare_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    return origin


@pytest.fixture
def repo(tmp_path, bare_origin):
    """A real work tree with one commit on "main", pushed to a bare origin."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "agent@example.com")
    _git(work, "config", "user.name", "Agent")
    _git(work, "remote", "add", "origin", str(bare_origin))
    (work / "README.md").write_text("hello\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-q", "-m", "initial")
    _git(work, "branch", "-M", "main")
    _git(work, "push", "-u", "origin", "main")
    return work


# ── git_ops: current_branch / has_changes ───────────────────────────────────

def test_current_branch(repo):
    assert git_ops.current_branch(repo) == "main"


def test_has_changes_false_on_a_clean_tree(repo):
    assert git_ops.has_changes(repo) is False


def test_has_changes_true_after_an_edit(repo):
    (repo / "README.md").write_text("changed\n")
    assert git_ops.has_changes(repo) is True


# ── git_ops: create_branch ───────────────────────────────────────────────────

def test_create_branch_creates_and_checks_out(repo):
    git_ops.create_branch(repo, "feature/x")
    assert git_ops.current_branch(repo) == "feature/x"


def test_create_branch_reuses_an_existing_local_branch(repo):
    git_ops.create_branch(repo, "feature/y")
    _git(repo, "checkout", "main")
    # Must not fail because the branch already exists — it just checks it out,
    # so a retried publish lands back on the same branch.
    git_ops.create_branch(repo, "feature/y")
    assert git_ops.current_branch(repo) == "feature/y"


# ── git_ops: commit_all ──────────────────────────────────────────────────────

def test_commit_all_stages_and_commits_a_new_file(repo):
    (repo / "new.txt").write_text("content\n")
    sha = git_ops.commit_all(repo, "add new file")
    assert sha
    files = _git(repo, "show", "--name-only", "--pretty=", sha).stdout.split()
    assert files == ["new.txt"]


def test_commit_all_respects_author(repo):
    (repo / "a.txt").write_text("x\n")
    sha = git_ops.commit_all(repo, "msg", author="Someone <someone@example.com>")
    who = _git(repo, "show", "-s", "--format=%an <%ae>", sha).stdout.strip()
    assert who == "Someone <someone@example.com>"


def test_commit_all_refuses_when_nothing_changed(repo):
    with pytest.raises(GitOpsError, match="nothing to commit"):
        git_ops.commit_all(repo, "no-op")


def test_commit_all_never_commits_denylisted_files(repo):
    (repo / ".env").write_text("SECRET=1\n")
    (repo / "id_rsa").write_text("private key\n")
    (repo / "cert.pem").write_text("cert\n")
    (repo / "safe.txt").write_text("ok\n")

    sha = git_ops.commit_all(repo, "add mixed files")

    files = _git(repo, "show", "--name-only", "--pretty=", sha).stdout.split()
    assert files == ["safe.txt"]
    # The denylisted files are still on disk, just left untracked — commit_all
    # unstages them, it does not delete anything.
    status = _git(repo, "status", "--porcelain").stdout
    assert "?? .env" in status
    assert "?? id_rsa" in status
    assert "?? cert.pem" in status


def test_commit_all_refuses_when_only_denylisted_files_changed(repo):
    (repo / ".env").write_text("SECRET=1\n")
    with pytest.raises(GitOpsError, match="nothing to commit"):
        git_ops.commit_all(repo, "only secrets")


def test_commit_all_never_commits_gitignored_files(repo):
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "ignored.txt").write_text("noise\n")
    (repo / "tracked.txt").write_text("ok\n")

    sha = git_ops.commit_all(repo, "respect gitignore")

    files = set(_git(repo, "show", "--name-only", "--pretty=", sha).stdout.split())
    assert "ignored.txt" not in files
    assert "tracked.txt" in files


# ── git_ops: push ─────────────────────────────────────────────────────────────

def test_push_lands_the_branch_on_the_bare_remote(repo, bare_origin):
    git_ops.create_branch(repo, "feature/push-me")
    (repo / "pushed.txt").write_text("x\n")
    git_ops.commit_all(repo, "add pushed file")

    git_ops.push(repo, "feature/push-me")

    refs = subprocess.run(
        ["git", "ls-remote", "--heads", str(bare_origin)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "refs/heads/feature/push-me" in refs


# ── providers: remote_id_from_url ────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/repo.git", "acme/repo"),
    ("https://gitlab.example.com/group/sub/project.git", "group/sub/project"),
    ("git@github.com:acme/repo.git", "acme/repo"),
    ("", None),
    (None, None),
])
def test_remote_id_from_url(url, expected):
    assert remote_id_from_url(url) == expected


# ── providers: default_branch ────────────────────────────────────────────────

def test_default_branch_delegates_to_get_repo(monkeypatch):
    monkeypatch.setattr(GitHubProvider, "get_repo", lambda self, remote_id: {"default_branch": "develop"})
    provider = GitHubProvider("fake-token")
    assert provider.default_branch("acme/repo") == "develop"


# ── providers: create_pull_request / create_merge_request ───────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_github_create_pull_request_posts_the_expected_payload(monkeypatch):
    captured = {}

    def fake_post(self, url, headers, json_body):
        captured["url"] = url
        captured["json"] = json_body
        return _FakeResponse({"html_url": "https://github.com/acme/repo/pull/7", "number": 7})

    monkeypatch.setattr(GitHubProvider, "_post", fake_post)
    provider = GitHubProvider("fake-token")

    result = provider.create_pull_request(
        "acme/repo", title="Add feature", body="does a thing",
        head="agent/demo-1-add-feature", base="main", draft=True,
    )

    assert captured["url"] == "https://api.github.com/repos/acme/repo/pulls"
    assert captured["json"] == {
        "title": "Add feature", "body": "does a thing",
        "head": "agent/demo-1-add-feature", "base": "main", "draft": True,
    }
    assert result == {"url": "https://github.com/acme/repo/pull/7", "number": 7}


def test_gitlab_create_merge_request_posts_the_expected_payload(monkeypatch):
    captured = {}

    def fake_post(self, url, headers, json_body):
        captured["url"] = url
        captured["json"] = json_body
        return _FakeResponse({"web_url": "https://gitlab.com/acme/repo/-/merge_requests/3", "iid": 3})

    monkeypatch.setattr(GitLabProvider, "_post", fake_post)
    provider = GitLabProvider("fake-token")

    result = provider.create_merge_request(
        "acme/group/repo", title="Add feature", body="does a thing",
        head="agent/demo-1-add-feature", base="main", draft=True,
    )

    assert captured["url"] == "https://gitlab.com/api/v4/projects/acme%2Fgroup%2Frepo/merge_requests"
    assert captured["json"]["title"] == "Draft: Add feature"
    assert captured["json"]["source_branch"] == "agent/demo-1-add-feature"
    assert captured["json"]["target_branch"] == "main"
    assert result == {"url": "https://gitlab.com/acme/repo/-/merge_requests/3", "number": 3}


# ── the tool + route, end to end ─────────────────────────────────────────────

def _make_project(tmp_path_factory, *, provider="github", remote_id="acme/repo"):
    """A real project backed by a real repo, registered in the (test-isolated)
    project store, with a workspace folder the tool's own resolution walks
    through (see tools/git_publish.py's _repo_dir / workspace.get_workspace_folder).
    """
    from common.paths import PROJECTS_FILE
    from projects.models import Project, RepoConfig
    from projects.storage import ProjectStore
    from workspace.storage import WORKSPACES_ROOT

    tag = uuid4().hex[:8]
    ws_name = f"gitpub_ws_{tag}"
    ws_dir = WORKSPACES_ROOT / ws_name
    ws_dir.mkdir(parents=True)

    repo_dir = ws_dir / "proj" / "repo"
    repo_dir.mkdir(parents=True)
    origin = ws_dir / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "agent@example.com")
    _git(repo_dir, "config", "user.name", "Agent")
    _git(repo_dir, "remote", "add", "origin", str(origin))
    (repo_dir / "README.md").write_text("hello\n")
    _git(repo_dir, "add", "README.md")
    _git(repo_dir, "commit", "-q", "-m", "initial")
    _git(repo_dir, "branch", "-M", "main")
    _git(repo_dir, "push", "-u", "origin", "main")

    project = Project(
        name=f"gitpub-project-{tag}",
        type="code",
        workspace=ws_name,
        repo=RepoConfig(type=provider, url=str(origin), branch="main",
                         local_path="proj/repo", remote_id=remote_id),
    )
    ProjectStore(path=PROJECTS_FILE).add(project)
    return project, repo_dir, origin


@pytest.fixture
def fake_token(monkeypatch):
    """No real credential store touched — both providers just see a token."""
    monkeypatch.setattr(git_store, "get_token", lambda provider: "fake-token")
    monkeypatch.setattr(git_store, "has_token", lambda provider: True)


@pytest.fixture
def fake_github_pr(monkeypatch, fake_token):
    """default_branch + create_pull_request never hit the network."""
    calls = {}
    monkeypatch.setattr(GitHubProvider, "default_branch", lambda self, remote_id: "main")

    def fake_create(self, owner_repo, *, title, body, head, base, draft=False):
        calls["args"] = {"owner_repo": owner_repo, "title": title, "body": body,
                          "head": head, "base": base, "draft": draft}
        return {"url": "https://github.com/acme/repo/pull/42", "number": 42}

    monkeypatch.setattr(GitHubProvider, "create_pull_request", fake_create)
    return calls


def test_git_publish_tool_end_to_end(tmp_path_factory, fake_github_pr):
    from tools.git_publish import git_publish

    project, repo_dir, origin = _make_project(tmp_path_factory)
    (repo_dir / "feature.py").write_text("print('hi')\n")

    raw = git_publish.invoke({
        "project": project.id, "title": "Add a feature",
        "body": "hand-written description", "draft": True, "open_pr": True,
    })

    import json
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["branch"].startswith("agent/")
    assert result["commit"]
    assert result["pushed"] is True
    assert result["pr_url"] == "https://github.com/acme/repo/pull/42"

    # The PR really was requested with this run's branch/base/body.
    args = fake_github_pr["args"]
    assert args["owner_repo"] == "acme/repo"
    assert args["title"] == "Add a feature"
    assert args["body"] == "hand-written description"
    assert args["base"] == "main"
    assert args["draft"] is True

    # And the branch really landed on the bare remote.
    refs = subprocess.run(
        ["git", "ls-remote", "--heads", str(origin)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert f"refs/heads/{result['branch']}" in refs


def test_git_publish_refuses_to_push_the_default_branch(tmp_path_factory, fake_github_pr):
    from tools.git_publish import run_git_publish

    project, repo_dir, _origin = _make_project(tmp_path_factory)
    (repo_dir / "feature.py").write_text("print('hi')\n")

    result = run_git_publish(project.id, branch="main", title="Sneaky direct push")

    assert result["ok"] is False
    assert result["code"] == "branch_protection"
    # The PR must never have been attempted once the branch check refused.
    assert "args" not in fake_github_pr


def test_git_publish_refuses_when_nothing_changed(tmp_path_factory, fake_github_pr):
    from tools.git_publish import run_git_publish

    project, _repo_dir, _origin = _make_project(tmp_path_factory)
    # No edits made — the repo is exactly what _make_project committed.
    result = run_git_publish(project.id, title="Nothing to see here")

    assert result["ok"] is False
    assert result["code"] == "git_error"
    assert "nothing to commit" in result["error"]


def test_git_publish_builds_the_body_from_the_current_task(tmp_path_factory, fake_github_pr):
    from common.agent_context import current_task_id
    from tasks.service import create_task, set_task_result
    from tools.git_publish import run_git_publish

    project, repo_dir, _origin = _make_project(tmp_path_factory)
    (repo_dir / "feature.py").write_text("print('hi')\n")

    task = create_task(
        "Ship the feature", description="Wire up the new endpoint.",
        workspace=project.workspace,
    )
    set_task_result(task.id, "Implemented and tested locally.")

    token = current_task_id.set(str(task.id))
    try:
        result = run_git_publish(project.id, title="Ship the feature")
    finally:
        current_task_id.reset(token)

    assert result["ok"] is True
    args = fake_github_pr["args"]
    assert "Ship the feature" in args["body"]
    assert "Wire up the new endpoint." in args["body"]
    assert "Implemented and tested locally." in args["body"]
    # The default branch name draws from the task's key, not a generic slug.
    assert task.key.lower() in result["branch"]


def test_git_publish_route(tmp_path_factory, fake_github_pr):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.projects import router as projects_router

    project, repo_dir, origin = _make_project(tmp_path_factory)
    (repo_dir / "feature.py").write_text("print('hi')\n")

    app = FastAPI()
    app.include_router(projects_router)
    client = TestClient(app)

    resp = client.post(
        f"/api/projects/{project.id}/git/publish",
        json={"title": "Add a feature via the route", "draft": True, "open_pr": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["pr_url"] == "https://github.com/acme/repo/pull/42"

    refs = subprocess.run(
        ["git", "ls-remote", "--heads", str(origin)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert f"refs/heads/{body['branch']}" in refs


def test_git_publish_route_refuses_base_equal_to_head(tmp_path_factory, fake_github_pr):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.projects import router as projects_router

    project, repo_dir, _origin = _make_project(tmp_path_factory)
    (repo_dir / "feature.py").write_text("print('hi')\n")

    app = FastAPI()
    app.include_router(projects_router)
    client = TestClient(app)

    resp = client.post(
        f"/api/projects/{project.id}/git/publish",
        json={"branch": "same-name", "base": "same-name", "title": "No-op PR"},
    )
    assert resp.status_code == 400
    assert "onto itself" in resp.json()["detail"]
