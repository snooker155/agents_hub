"""
Provider abstraction over the Jira Cloud REST v3 API.

Mirrors connectors/git/providers.py's shape (one small class per backend, a
``TrackerError`` with UI-safe messages, a ``get_provider(name)`` factory that
reads the stored credentials) over a different surface: issues instead of
repos, a tracker-specific status name on top of the plain open/closed state,
and write operations (create, comment, transition) a tracker needs that a
git host does not.

Normalized issue shape, the same fields connectors/git/providers.py uses for
a repo issue plus a human key:

    {key ("PROJ-12"), number, title, body,
     state ("open"|"closed"), status (the tracker's own status name),
     labels, url, updated_at, author, assignee, priority}

Jira stores descriptions and comments as Atlassian Document Format (ADF), a
small rich-text JSON tree; ``_adf_to_text``/``_text_to_adf`` convert between
that and the plain text every other part of the hub expects. ``remote_id``
is the Jira project key, whatever ``connectors.trackers.sync`` and the
``tracker_*`` tools pass through as "the project this issue belongs to".
"""
from __future__ import annotations

import base64
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from common.ssrf import resolve_and_check

_API_TIMEOUT = 30.0


class TrackerError(Exception):
    """Raised for tracker configuration or API errors (message is UI-safe)."""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _adf_to_text(doc: Any) -> str:
    """Flatten an Atlassian Document Format node tree to plain text.

    Walks ``content``, joins adjacent ``text`` nodes within one block, and
    separates blocks (paragraphs, headings, ...) with newlines. Good enough
    for showing a description/comment as plain text; it drops marks
    (bold/italic/links) and anything that is not text content (tables,
    media, mentions).
    """
    if not isinstance(doc, dict):
        return ""

    def _walk(node: dict) -> str:
        parts: list[str] = []
        for child in node.get("content") or []:
            if not isinstance(child, dict):
                continue
            if child.get("type") == "text":
                parts.append(str(child.get("text") or ""))
            elif child.get("content"):
                parts.append(_walk(child))
        return "".join(parts)

    lines = [_walk(block) for block in (doc.get("content") or []) if isinstance(block, dict)]
    return "\n".join(lines)


def _text_to_adf(text: str) -> dict[str, Any]:
    """The inverse of ``_adf_to_text`` for posting a comment: one paragraph,
    no formatting — enough for what the tracker_comment tool sends."""
    return {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


class TrackerProvider:
    name: str = ""

    def test_connection(self) -> dict[str, Any]:
        raise NotImplementedError

    def list_issues(self, remote_id: str, state: str = "all", limit: int = 200) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_issue(self, key: str) -> dict[str, Any]:
        raise NotImplementedError

    def create_issue(self, remote_id: str, title: str, body: str = "",
                      labels: Optional[list[str]] = None) -> dict[str, Any]:
        raise NotImplementedError

    def add_comment(self, key: str, text: str) -> dict[str, Any]:
        raise NotImplementedError

    def transition(self, key: str, status_name: str) -> dict[str, Any]:
        raise NotImplementedError

    def list_transitions(self, key: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_remotes(self) -> list[dict[str, Any]]:
        """Projects the credentials can see, for a picker on the project's
        tracker-link form."""
        raise NotImplementedError


class JiraProvider(TrackerProvider):
    name = "Jira"

    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.email = (email or "").strip()
        self.api_token = (api_token or "").strip()
        if not self.base_url or not self.email or not self.api_token:
            raise TrackerError(
                "Jira is not fully configured. Add the base URL, email and "
                "API token on the Connectors page."
            )
        host = urlparse(self.base_url).hostname or ""
        if host and not host.endswith(".atlassian.net"):
            ok, reason = resolve_and_check(host)
            if not ok:
                raise TrackerError(f"Jira base URL refused: {reason}")
        self._api = f"{self.base_url}/rest/api/3"

    @classmethod
    def from_store(cls) -> "JiraProvider":
        from . import JIRA_STORE
        cfg = JIRA_STORE.get_config()
        return cls(cfg.get("base_url", ""), cfg.get("email", ""), cfg.get("api_token", ""))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Basic {_b64(f'{self.email}:{self.api_token}')}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> httpx.Response:
        try:
            return httpx.get(f"{self._api}{path}", headers=self._headers(),
                              params=params, timeout=_API_TIMEOUT)
        except httpx.HTTPError as e:
            raise TrackerError(f"Jira API request failed: {e.__class__.__name__}") from e

    def _post(self, path: str, json_body: dict) -> httpx.Response:
        try:
            resp = httpx.post(f"{self._api}{path}", headers=self._headers(),
                               json=json_body, timeout=_API_TIMEOUT)
        except httpx.HTTPError as e:
            raise TrackerError(f"Jira API request failed: {e.__class__.__name__}") from e
        return self._check(resp)

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code == 401:
            raise TrackerError("Jira credentials are invalid or expired")
        if resp.status_code == 403:
            raise TrackerError("Jira API access forbidden (missing scope or permission)")
        if resp.status_code == 404:
            raise TrackerError("Jira: resource not found")
        if resp.status_code >= 400:
            detail = ""
            try:
                data = resp.json()
                msgs = data.get("errorMessages") or []
                detail = "; ".join(msgs) if msgs else str(data.get("errors") or "")
            except (ValueError, AttributeError, TypeError):
                pass  # not a JSON error body
            suffix = f": {detail}" if detail else ""
            raise TrackerError(f"Jira API error {resp.status_code}{suffix}")
        return resp

    def test_connection(self) -> dict[str, Any]:
        resp = self._check(self._get("/myself"))
        data = resp.json()
        return {"ok": True, "identity": data.get("displayName"), "error": None}

    def _normalize(self, issue: dict) -> dict[str, Any]:
        fields = issue.get("fields") or {}
        status = fields.get("status") or {}
        category = (status.get("statusCategory") or {}).get("key")
        key = issue.get("key")
        return {
            "key": key,
            "number": key,
            "title": fields.get("summary") or "",
            "body": _adf_to_text(fields.get("description")),
            "state": "closed" if category == "done" else "open",
            "status": status.get("name"),
            "labels": list(fields.get("labels") or []),
            "url": f"{self.base_url}/browse/{key}" if key else None,
            "updated_at": fields.get("updated"),
            "author": (fields.get("creator") or {}).get("displayName"),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "priority": (fields.get("priority") or {}).get("name"),
        }

    _FIELDS_PARAM = "summary,description,status,labels,updated,creator,assignee,priority"

    def list_issues(self, remote_id: str, state: str = "all", limit: int = 200) -> list[dict[str, Any]]:
        jql = f"project = {remote_id} ORDER BY updated DESC"
        params = {"jql": jql, "maxResults": min(max(limit, 1), 100), "fields": self._FIELDS_PARAM}
        resp = self._get("/search/jql", params=params)
        if resp.status_code == 404:
            resp = self._get("/search", params=params)
        resp = self._check(resp)
        data = resp.json()
        issues = [self._normalize(it) for it in (data.get("issues") or [])]
        if state in ("open", "closed"):
            issues = [i for i in issues if i["state"] == state]
        return issues[:limit]

    def get_issue(self, key: str) -> dict[str, Any]:
        resp = self._check(self._get(f"/issue/{key}", params={"fields": self._FIELDS_PARAM}))
        return self._normalize(resp.json())

    def create_issue(self, remote_id: str, title: str, body: str = "",
                      labels: Optional[list[str]] = None) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": remote_id},
            "summary": title,
            "issuetype": {"name": "Task"},
        }
        if body:
            fields["description"] = _text_to_adf(body)
        if labels:
            fields["labels"] = list(labels)
        resp = self._post("/issue", {"fields": fields})
        data = resp.json()
        key = data.get("key")
        return {"key": key, "url": f"{self.base_url}/browse/{key}" if key else None}

    def add_comment(self, key: str, text: str) -> dict[str, Any]:
        resp = self._post(f"/issue/{key}/comment", {"body": _text_to_adf(text)})
        data = resp.json()
        return {"ok": True, "id": data.get("id")}

    def list_transitions(self, key: str) -> list[dict[str, Any]]:
        resp = self._check(self._get(f"/issue/{key}/transitions"))
        data = resp.json()
        return [{"id": t.get("id"), "name": t.get("name")} for t in (data.get("transitions") or [])]

    def transition(self, key: str, status_name: str) -> dict[str, Any]:
        transitions = self.list_transitions(key)
        target = status_name.strip().lower()
        match = next((t for t in transitions if (t.get("name") or "").strip().lower() == target), None)
        if match is None:
            available = ", ".join(t.get("name") or "" for t in transitions)
            raise TrackerError(f"No transition named {status_name!r} on {key}. Available: {available}")
        self._post(f"/issue/{key}/transitions", {"transition": {"id": match["id"]}})
        return {"ok": True, "status": status_name}

    def list_remotes(self) -> list[dict[str, Any]]:
        resp = self._check(self._get("/project/search", params={"maxResults": 100}))
        data = resp.json()
        return [{"key": p.get("key"), "name": p.get("name")} for p in (data.get("values") or [])]


def get_provider(provider: str, workspace: Optional[str] = None) -> TrackerProvider:
    """Build a provider client from the stored credentials of the connector
    in effect in ``workspace``: its own when it defines one, else the default
    workspace's (connectors/store.py). ``None`` means the running
    code's workspace (a run's context var); code acting for a workspace
    outside a run (a route, a background sync) names it."""
    if workspace is not None:
        from connectors.store import in_workspace
        return in_workspace(workspace, get_provider, provider)
    if provider == "jira":
        return JiraProvider.from_store()
    raise TrackerError(f"Unknown tracker provider: {provider!r}")


__all__ = [
    "TrackerError", "TrackerProvider", "JiraProvider", "get_provider",
]
