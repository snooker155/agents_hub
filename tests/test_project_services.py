"""
The three services split out of ``routes/projects.py``: ``projects.git_service``,
``projects.planner_service`` and ``projects.proxy_service``.

Covers what the route split moved the risk into: the api-request proxy's
allowlist (scheme + cloud metadata address) and its container host rewrite,
and git status read against a real temporary repository. Both raise
``ServiceError(status, detail)`` on failure — the shape every route in
``routes/projects.py`` now maps to an ``HTTPException`` with one ``except``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from projects.errors import ServiceError
from projects.models import Project, RepoConfig


# ── proxy_service: base-URL allowlist ───────────────────────────────────────

def test_rejects_a_non_http_scheme():
    from projects.proxy_service import validated_api_base_url

    with pytest.raises(ValueError, match="http or https"):
        validated_api_base_url("file:///etc/passwd")


def test_rejects_the_cloud_metadata_hostname():
    from projects.proxy_service import validated_api_base_url

    with pytest.raises(ValueError, match="metadata"):
        validated_api_base_url("http://metadata.google.internal/computeMetadata/v1/")


def test_rejects_the_cloud_metadata_ip():
    from projects.proxy_service import validated_api_base_url

    with pytest.raises(ValueError, match="metadata"):
        validated_api_base_url("http://169.254.169.254/latest")


def test_an_ordinary_url_passes_through_unchanged_on_the_host(monkeypatch):
    """host_service_url() is a no-op outside a container."""
    from projects.proxy_service import validated_api_base_url

    monkeypatch.delenv("AGENTS_HUB_IN_CONTAINER", raising=False)
    assert validated_api_base_url("http://localhost:9") == "http://localhost:9"


def test_a_loopback_url_is_rewritten_to_the_host_gateway_inside_a_container(monkeypatch):
    """Same input, run as if this process were the one inside the container —
    the point of pushing the URL through host_service_url() at all."""
    from projects.proxy_service import validated_api_base_url

    monkeypatch.setenv("AGENTS_HUB_IN_CONTAINER", "1")
    assert (validated_api_base_url("http://localhost:9")
            == "http://host.docker.internal:9")


# ── proxy_service: the forwarded request ────────────────────────────────────

def test_proxy_api_request_rejects_a_bad_base_url_as_a_service_error():
    from projects.proxy_service import proxy_api_request
    import asyncio

    with pytest.raises(ServiceError) as exc:
        asyncio.run(proxy_api_request("file:///etc/passwd", "GET", "/"))
    assert exc.value.status == 400


def test_proxy_api_request_maps_a_connect_error_to_502(monkeypatch):
    import asyncio
    import httpx
    from projects import proxy_service

    class _FailingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, **kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)

    with pytest.raises(ServiceError) as exc:
        asyncio.run(proxy_service.proxy_api_request("http://localhost:9", "GET", "/"))
    assert exc.value.status == 502


def test_proxy_api_request_forwards_a_successful_call(monkeypatch):
    import asyncio
    import httpx
    from projects import proxy_service

    class _FakeResponse:
        status_code = 200
        headers = {"x-test": "1"}

        def json(self):
            return {"ok": True}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    result = asyncio.run(proxy_service.proxy_api_request("http://localhost:9", "GET", "/health"))
    assert result == {"status_code": 200, "headers": {"x-test": "1"}, "body": {"ok": True}}


# ── git_service: status against a real temp repo ────────────────────────────

def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=str(path), check=True, capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (path / "README.md").write_text("hello\n")
    run("add", "README.md")
    run("commit", "-q", "-m", "initial")


def _project(workspace_name: str, local_path: str) -> Project:
    return Project(name="p", workspace=workspace_name, repo=RepoConfig(local_path=local_path))


def test_git_status_reads_branch_and_a_clean_tree(tmp_path):
    from projects.git_service import git_status

    _init_repo(tmp_path / "repo")
    project = _project("ws", "repo")

    result = git_status(project, tmp_path)
    assert result["branch"] == "main"
    assert result["status"] == ""
    assert "initial" in result["recent_commits"]


def test_git_status_reports_uncommitted_changes(tmp_path):
    from projects.git_service import git_status

    _init_repo(tmp_path / "repo")
    (tmp_path / "repo" / "README.md").write_text("changed\n")
    project = _project("ws", "repo")

    result = git_status(project, tmp_path)
    assert "README.md" in result["status"]


def test_git_status_raises_a_404_service_error_when_the_repo_is_missing(tmp_path):
    from projects.git_service import git_status

    project = _project("ws", "nope")

    with pytest.raises(ServiceError) as exc:
        git_status(project, tmp_path)
    assert exc.value.status == 404


def test_repo_provider_only_names_github_and_gitlab():
    from projects.git_service import repo_provider

    assert repo_provider(_project("ws", "repo")) is None  # RepoType default: none
    assert repo_provider(Project(name="p", workspace="ws",
                                 repo=RepoConfig(type="github"))) == "github"
    assert repo_provider(Project(name="p", workspace="ws",
                                 repo=RepoConfig(type="local"))) is None


# ── planner_service: the prompt ─────────────────────────────────────────────

def test_build_prompt_carries_the_conversation_and_the_request():
    from projects.planner_service import build_prompt

    history = [{"role": "user", "content": "split the backend"},
              {"role": "assistant", "content": "done"}]
    prompt = build_prompt(history, "also add a QA pass")

    assert "split the backend" in prompt
    assert "also add a QA pass" in prompt
    assert "list_tasks" in prompt


def test_build_prompt_with_no_history_says_so():
    from projects.planner_service import build_prompt

    prompt = build_prompt([], "generate the plan")
    assert "(none)" in prompt
