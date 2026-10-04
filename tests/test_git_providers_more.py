"""
Tests for the Bitbucket Cloud and Gitea additions to connectors/git:
providers.py's BitbucketProvider/GiteaProvider, and store.py's PROVIDERS,
public_config, get_base_url/get_username for the two new providers.

No network: httpx.get/httpx.post are monkeypatched, the same approach
tests/test_git_publish.py uses for GitHub/GitLab.

Run: python -m pytest tests/test_git_providers_more.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.git.providers import BitbucketProvider, GiteaProvider, GitProviderError, get_provider
import connectors.git.store as git_store


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


# ── Bitbucket: _normalize_repo ──────────────────────────────────────────

_BITBUCKET_REPO = {
    "full_name": "acme/widgets",
    "name": "widgets",
    "description": "Widgets repo",
    "is_private": True,
    "mainbranch": {"name": "develop"},
    "links": {
        "html": {"href": "https://bitbucket.org/acme/widgets"},
        "clone": [
            {"name": "https", "href": "https://bitbucket.org/acme/widgets.git"},
            {"name": "ssh", "href": "git@bitbucket.org:acme/widgets.git"},
        ],
    },
}


def test_bitbucket_normalize_repo():
    provider = BitbucketProvider("app-pw", "acmeuser")
    normalized = provider._normalize_repo(_BITBUCKET_REPO)
    assert normalized == {
        "provider": "bitbucket",
        "remote_id": "acme/widgets",
        "name": "widgets",
        "full_name": "acme/widgets",
        "description": "Widgets repo",
        "default_branch": "develop",
        "clone_url": "https://bitbucket.org/acme/widgets.git",
        "web_url": "https://bitbucket.org/acme/widgets",
        "private": True,
    }


def test_bitbucket_requires_a_username():
    with pytest.raises(GitProviderError):
        BitbucketProvider("app-pw", "")


# ── Bitbucket: list_issues state mapping ────────────────────────────────

_BITBUCKET_ISSUES_PAGE = {
    "values": [
        {"id": 1, "title": "Bug A", "state": "new",
         "content": {"raw": "steps"}, "links": {"html": {"href": "https://bitbucket.org/x/1"}},
         "updated_on": "2026-01-01T00:00:00Z", "reporter": {"display_name": "Alice"}},
        {"id": 2, "title": "Bug B", "state": "resolved",
         "content": {"raw": ""}, "links": {"html": {"href": "https://bitbucket.org/x/2"}},
         "updated_on": "2026-01-02T00:00:00Z", "reporter": {"display_name": "Bob"}},
    ],
    "next": None,
}


def test_bitbucket_list_issues_maps_state(monkeypatch):
    monkeypatch.setattr(
        "httpx.get",
        lambda url, headers=None, params=None, timeout=None, follow_redirects=None:
            _FakeResponse(_BITBUCKET_ISSUES_PAGE),
    )
    provider = BitbucketProvider("app-pw", "acmeuser")
    issues = provider.list_issues("acme/widgets")
    assert len(issues) == 2
    assert issues[0]["state"] == "open"
    assert issues[1]["state"] == "closed"


def test_bitbucket_list_issues_returns_empty_when_tracker_disabled(monkeypatch):
    monkeypatch.setattr(
        "httpx.get",
        lambda url, headers=None, params=None, timeout=None, follow_redirects=None:
            _FakeResponse({"error": {"message": "not found"}}, status_code=404),
    )
    provider = BitbucketProvider("app-pw", "acmeuser")
    assert provider.list_issues("acme/widgets") == []


# ── Bitbucket: create_pull_request ──────────────────────────────────────

def test_bitbucket_create_pull_request_posts_the_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({
            "id": 9,
            "links": {"html": {"href": "https://bitbucket.org/acme/widgets/pull-requests/9"}},
        })

    monkeypatch.setattr("httpx.post", fake_post)
    provider = BitbucketProvider("app-pw", "acmeuser")

    result = provider.create_pull_request(
        "acme/widgets", title="Add feature", body="does a thing",
        head="agent/demo-1-add-feature", base="develop", draft=True,
    )

    assert captured["url"] == "https://api.bitbucket.org/2.0/repositories/acme/widgets/pullrequests"
    assert captured["json"] == {
        "title": "Add feature", "description": "does a thing",
        "source": {"branch": {"name": "agent/demo-1-add-feature"}},
        "destination": {"branch": {"name": "develop"}},
    }
    assert result == {"url": "https://bitbucket.org/acme/widgets/pull-requests/9", "number": 9}


# ── Gitea: _normalize_repo ───────────────────────────────────────────────

_GITEA_REPO = {
    "full_name": "team/service",
    "name": "service",
    "description": "A service",
    "private": False,
    "default_branch": "main",
    "clone_url": "https://git.example.com/team/service.git",
    "html_url": "https://git.example.com/team/service",
}


def test_gitea_normalize_repo():
    provider = GiteaProvider("tok", "https://git.example.com")
    normalized = provider._normalize_repo(_GITEA_REPO)
    assert normalized == {
        "provider": "gitea",
        "remote_id": "team/service",
        "name": "service",
        "full_name": "team/service",
        "description": "A service",
        "default_branch": "main",
        "clone_url": "https://git.example.com/team/service.git",
        "web_url": "https://git.example.com/team/service",
        "private": False,
    }


def test_gitea_uses_the_configured_base_url():
    provider = GiteaProvider("tok", "https://git.example.com/")
    assert provider._api == "https://git.example.com/api/v1"


def test_gitea_requires_a_base_url():
    with pytest.raises(GitProviderError):
        GiteaProvider("tok", "")


# ── Gitea: list_issues state mapping ─────────────────────────────────────

_GITEA_ISSUES_PAGE = [
    {"number": 1, "title": "Issue A", "body": "desc", "state": "open",
     "labels": [{"name": "bug"}], "html_url": "https://git.example.com/team/service/issues/1",
     "updated_at": "2026-01-01T00:00:00Z", "user": {"login": "alice"}},
    {"number": 2, "title": "Issue B", "body": "", "state": "closed",
     "labels": [], "html_url": "https://git.example.com/team/service/issues/2",
     "updated_at": "2026-01-02T00:00:00Z", "user": {"login": "bob"}},
]


def test_gitea_list_issues_maps_state(monkeypatch):
    monkeypatch.setattr(
        "httpx.get",
        lambda url, headers=None, params=None, timeout=None, follow_redirects=None:
            _FakeResponse(_GITEA_ISSUES_PAGE),
    )
    provider = GiteaProvider("tok", "https://git.example.com")
    issues = provider.list_issues("team/service")
    assert len(issues) == 2
    assert issues[0]["state"] == "open"
    assert issues[0]["labels"] == ["bug"]
    assert issues[1]["state"] == "closed"


# ── Gitea: create_pull_request ───────────────────────────────────────────

def test_gitea_create_pull_request_posts_the_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({
            "number": 4, "html_url": "https://git.example.com/team/service/pulls/4",
        })

    monkeypatch.setattr("httpx.post", fake_post)
    provider = GiteaProvider("tok", "https://git.example.com")

    result = provider.create_pull_request(
        "team/service", title="Add feature", body="does a thing",
        head="agent/demo-1-add-feature", base="main", draft=False,
    )

    assert captured["url"] == "https://git.example.com/api/v1/repos/team/service/pulls"
    assert captured["json"] == {
        "title": "Add feature", "body": "does a thing",
        "head": "agent/demo-1-add-feature", "base": "main",
    }
    assert result == {"url": "https://git.example.com/team/service/pulls/4", "number": 4}


# ── store.py: PROVIDERS, public_config, get_base_url, get_username ──────

def test_store_providers_includes_all_four():
    assert git_store.PROVIDERS == ("github", "gitlab", "bitbucket", "gitea")


def test_store_public_config_reports_bitbucket_and_gitea():
    git_store.set_token("bitbucket", "app-pw")
    git_store.set_username("bitbucket", "acmeuser")
    git_store.set_token("gitea", "tok")
    git_store.set_base_url("gitea", "https://git.example.com")

    cfg = git_store.public_config()
    assert cfg["bitbucket"] == {"has_token": True, "username": "acmeuser"}
    assert cfg["gitea"] == {"has_token": True, "base_url": "https://git.example.com"}


def test_get_provider_gitea_uses_the_stored_base_url(monkeypatch):
    monkeypatch.setattr(git_store, "get_token", lambda provider: "tok")
    monkeypatch.setattr(git_store, "get_base_url", lambda provider: "https://git.example.com")

    provider = get_provider("gitea")
    assert isinstance(provider, GiteaProvider)
    assert provider._api == "https://git.example.com/api/v1"


def test_get_provider_bitbucket_uses_the_stored_username(monkeypatch):
    monkeypatch.setattr(git_store, "get_token", lambda provider: "app-pw")
    monkeypatch.setattr(git_store, "get_username", lambda provider: "acmeuser")

    provider = get_provider("bitbucket")
    assert isinstance(provider, BitbucketProvider)
    assert provider.username == "acmeuser"
