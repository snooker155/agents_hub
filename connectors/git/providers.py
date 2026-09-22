"""
Provider abstraction over the GitHub REST v3 and GitLab REST v4 APIs.

Only the small surface needed by the dashboard is implemented:
  - test_connection()  — validate the token, return the authenticated login
  - list_repos(search) — repos the token can access, normalized
  - get_repo(remote_id) — single repo lookup, normalized
  - list_issues(remote_id) — all issues (open + closed), normalized
  - auth_header()      — HTTP header for token-safe git clone/pull injection
  - default_branch(remote_id) — the branch a publish must never push directly to
  - create_pull_request(...)  — GitHubProvider only, POST /repos/{o}/{r}/pulls
  - create_merge_request(...) — GitLabProvider only, POST /projects/{id}/merge_requests

Normalized shapes:
  repo:  {provider, remote_id, name, full_name, description, default_branch,
          clone_url, web_url, private}
  issue: {number, title, body, state ("open"|"closed"), labels, url,
          updated_at, author}
  change request: {url, number}
"""
from __future__ import annotations

import base64
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx

from . import store

_API_TIMEOUT = 30.0
_PER_PAGE = 100
_MAX_ISSUES = 1000


class GitProviderError(Exception):
    """Raised for provider configuration or API errors (message is UI-safe)."""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class GitProvider:
    name: str = ""

    def __init__(self, token: str):
        self.token = token.strip()
        if not self.token:
            raise GitProviderError(
                f"No {self.name or 'git'} token configured. Add one in Settings → Git."
            )

    # --- interface ---
    def test_connection(self) -> dict[str, Any]:
        raise NotImplementedError

    def list_repos(self, search: Optional[str] = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_repo(self, remote_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def list_issues(self, remote_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def auth_header(self) -> str:
        """Authorization header value for git HTTP operations."""
        raise NotImplementedError

    def default_branch(self, remote_id: str) -> str:
        """The repo's default branch — a publish must never push directly onto it.

        Both providers already normalize this through get_repo(), so one
        implementation here covers GitHub and GitLab instead of two copies.
        """
        return str(self.get_repo(remote_id)["default_branch"])

    # --- shared helpers ---
    def _get(self, url: str, headers: dict[str, str], params: Optional[dict] = None) -> httpx.Response:
        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=_API_TIMEOUT, follow_redirects=True)
        except httpx.HTTPError as e:
            raise GitProviderError(f"{self.name} API request failed: {e.__class__.__name__}") from e
        return self._check(resp)

    def _post(self, url: str, headers: dict[str, str], json_body: dict) -> httpx.Response:
        try:
            resp = httpx.post(url, headers=headers, json=json_body, timeout=_API_TIMEOUT)
        except httpx.HTTPError as e:
            raise GitProviderError(f"{self.name} API request failed: {e.__class__.__name__}") from e
        return self._check(resp)

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise GitProviderError(f"{self.name} token is invalid or expired")
        if resp.status_code == 403:
            raise GitProviderError(f"{self.name} API access forbidden (missing scope or rate limited)")
        if resp.status_code == 404:
            raise GitProviderError(f"{self.name}: resource not found")
        if resp.status_code >= 400:
            detail = ""
            try:
                data = resp.json()
                detail = str(data.get("message") or data.get("error") or "").strip()
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise GitProviderError(f"{self.name} API error {resp.status_code}{suffix}")
        return resp

    def _paginate(self, url: str, headers: dict[str, str], params: dict, limit: int) -> list[dict]:
        items: list[dict] = []
        page = 1
        while len(items) < limit:
            resp = self._get(url, headers, {**params, "per_page": _PER_PAGE, "page": page})
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < _PER_PAGE:
                break
            page += 1
        return items[:limit]


class GitHubProvider(GitProvider):
    name = "GitHub"
    _api = "https://api.github.com"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def test_connection(self) -> dict[str, Any]:
        resp = self._get(f"{self._api}/user", self._headers())
        data = resp.json()
        return {"ok": True, "login": data.get("login"), "error": None}

    def _normalize_repo(self, r: dict) -> dict[str, Any]:
        return {
            "provider": "github",
            "remote_id": r.get("full_name"),
            "name": r.get("name"),
            "full_name": r.get("full_name"),
            "description": r.get("description"),
            "default_branch": r.get("default_branch") or "main",
            "clone_url": r.get("clone_url"),
            "web_url": r.get("html_url"),
            "private": bool(r.get("private")),
        }

    def list_repos(self, search: Optional[str] = None) -> list[dict[str, Any]]:
        repos = self._paginate(
            f"{self._api}/user/repos", self._headers(),
            {"sort": "updated", "affiliation": "owner,collaborator,organization_member"},
            limit=300,
        )
        out = [self._normalize_repo(r) for r in repos]
        if search:
            q = search.lower()
            out = [r for r in out if q in (r["full_name"] or "").lower()]
        return out

    def get_repo(self, remote_id: str) -> dict[str, Any]:
        resp = self._get(f"{self._api}/repos/{remote_id}", self._headers())
        return self._normalize_repo(resp.json())

    def list_issues(self, remote_id: str) -> list[dict[str, Any]]:
        items = self._paginate(
            f"{self._api}/repos/{remote_id}/issues", self._headers(),
            {"state": "all"},
            limit=_MAX_ISSUES,
        )
        issues = []
        for it in items:
            if "pull_request" in it:  # the issues API also returns PRs
                continue
            issues.append({
                "number": it.get("number"),
                "title": it.get("title") or "",
                "body": it.get("body") or "",
                "state": "closed" if it.get("state") == "closed" else "open",
                "labels": [l.get("name") for l in (it.get("labels") or []) if isinstance(l, dict)],
                "url": it.get("html_url"),
                "updated_at": it.get("updated_at"),
                "author": (it.get("user") or {}).get("login"),
            })
        return issues

    def auth_header(self) -> str:
        return f"Authorization: Basic {_b64(f'x-access-token:{self.token}')}"

    def create_pull_request(
        self, owner_repo: str, *, title: str, body: str, head: str, base: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        """POST /repos/{owner}/{repo}/pulls. GitHub's PR API takes `draft` natively."""
        resp = self._post(
            f"{self._api}/repos/{owner_repo}/pulls", self._headers(),
            {"title": title, "body": body, "head": head, "base": base, "draft": bool(draft)},
        )
        data = resp.json()
        return {"url": data.get("html_url"), "number": data.get("number")}


class GitLabProvider(GitProvider):
    name = "GitLab"

    def __init__(self, token: str, base_url: str = store.DEFAULT_GITLAB_BASE_URL):
        super().__init__(token)
        self._api = f"{base_url.rstrip('/')}/api/v4"

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token}

    def test_connection(self) -> dict[str, Any]:
        resp = self._get(f"{self._api}/user", self._headers())
        data = resp.json()
        return {"ok": True, "login": data.get("username"), "error": None}

    def _normalize_repo(self, r: dict) -> dict[str, Any]:
        return {
            "provider": "gitlab",
            "remote_id": r.get("path_with_namespace"),
            "name": r.get("path"),
            "full_name": r.get("path_with_namespace"),
            "description": r.get("description"),
            "default_branch": r.get("default_branch") or "main",
            "clone_url": r.get("http_url_to_repo"),
            "web_url": r.get("web_url"),
            "private": (r.get("visibility") or "private") != "public",
        }

    def list_repos(self, search: Optional[str] = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"membership": True, "order_by": "last_activity_at"}
        if search:
            params["search"] = search
        repos = self._paginate(f"{self._api}/projects", self._headers(), params, limit=300)
        return [self._normalize_repo(r) for r in repos]

    def get_repo(self, remote_id: str) -> dict[str, Any]:
        encoded = quote(str(remote_id), safe="")
        resp = self._get(f"{self._api}/projects/{encoded}", self._headers())
        return self._normalize_repo(resp.json())

    def list_issues(self, remote_id: str) -> list[dict[str, Any]]:
        encoded = quote(str(remote_id), safe="")
        items = self._paginate(
            f"{self._api}/projects/{encoded}/issues", self._headers(),
            {"state": "all"},
            limit=_MAX_ISSUES,
        )
        return [{
            "number": it.get("iid"),
            "title": it.get("title") or "",
            "body": it.get("description") or "",
            "state": "closed" if it.get("state") == "closed" else "open",
            "labels": list(it.get("labels") or []),
            "url": it.get("web_url"),
            "updated_at": it.get("updated_at"),
            "author": (it.get("author") or {}).get("username"),
        } for it in items]

    def auth_header(self) -> str:
        return f"Authorization: Basic {_b64(f'oauth2:{self.token}')}"

    def create_merge_request(
        self, owner_repo: str, *, title: str, body: str, head: str, base: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        """POST /projects/{id}/merge_requests.

        GitLab's stable way to mark a merge request as a draft is the "Draft: "
        title prefix (the boolean field is newer and not reliably present on
        self-hosted instances), so that is what draft=True adds here.
        """
        encoded = quote(str(owner_repo), safe="")
        payload = {
            "title": f"Draft: {title}" if draft else title,
            "description": body,
            "source_branch": head,
            "target_branch": base,
        }
        resp = self._post(f"{self._api}/projects/{encoded}/merge_requests", self._headers(), payload)
        data = resp.json()
        return {"url": data.get("web_url"), "number": data.get("iid")}


def get_provider(provider: str) -> GitProvider:
    """Build a provider client from the stored configuration."""
    if provider == "github":
        return GitHubProvider(store.get_token("github"))
    if provider == "gitlab":
        return GitLabProvider(store.get_token("gitlab"), store.get_base_url("gitlab"))
    raise GitProviderError(f"Unknown git provider: {provider!r}")


def remote_id_from_url(url: str) -> Optional[str]:
    """Best-effort owner/repo (or GitLab group/.../project) from a remote URL.

    Handles the two shapes a clone URL takes — HTTPS
    ("https://host/owner/repo.git") and SSH ("git@host:owner/repo.git") — the
    same two ``clone_url`` / ``http_url_to_repo`` return from the provider
    APIs. Used as a fallback for a project attached in place (see
    dashboard/backend/routes/projects.py's /attach), which has no stored
    ``remote_id`` and only whatever ``origin`` already points at.
    """
    s = (url or "").strip()
    if not s:
        return None
    if s.startswith("git@") or (":" in s and "://" not in s):
        # scp-like syntax: user@host:path
        _, _, path = s.partition(":")
    else:
        path = urlparse(s).path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path or None
