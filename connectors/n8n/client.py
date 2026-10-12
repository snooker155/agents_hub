"""
connectors/n8n/client.py — a thin client for an n8n instance.

Two halves, matching the two things the hub does with n8n:

* the public REST API (``/api/v1``, ``X-N8N-API-KEY``): list workflows and
  read one, for the import dialog on the Flows page and for a ``run_n8n``
  node that names a workflow by id;
* the workflow's own trigger: n8n's public API has no "execute workflow"
  call, so a run goes through the URL the workflow already listens on, a
  Webhook trigger (``/webhook/<path>``) or a Chat trigger
  (``/webhook/<webhookId>/chat``). Those need no API key, only the base URL
  and, when the Webhook node uses header auth, that header.

The base URL is the operator's own choice and n8n is usually self hosted on
the LAN or on localhost, so there is no ``common.ssrf`` guard here, the same
reasoning connectors/git's Gitea provider gives. A run never takes a URL
from node config: it takes a path and joins it to this base URL, so a flow
cannot point the hub at another host.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote

import httpx

_API_TIMEOUT = 30.0
#: How long a workflow run may take when the node sets no timeout of its own.
DEFAULT_RUN_TIMEOUT = 300.0

#: Node types that start a workflow. They have no counterpart inside a hub
#: flow (the flow has its own entry), so the importer drops them and the run
#: node reads the trigger from them.
WEBHOOK_TYPE = "n8n-nodes-base.webhook"
CHAT_TRIGGER_TYPE = "@n8n/n8n-nodes-langchain.chatTrigger"

#: A webhook path as n8n writes them: segments of letters, digits, - _ . and
#: :param placeholders. No query, fragment or scheme can ride along.
_PATH_RE = re.compile(r"[A-Za-z0-9_\-.:]+(/[A-Za-z0-9_\-.:]+)*")


class N8nError(Exception):
    """A UI-safe error from an n8n call."""


def _join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def find_trigger(workflow: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The trigger a run can go through: ``{"kind": "webhook"|"chat", "path",
    "method", "node"}``, or None when the workflow starts some other way
    (manual, schedule, an app trigger). A Chat trigger wins over a Webhook,
    since it is the one an agent workflow is built around."""
    found: Optional[dict[str, Any]] = None
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict) or node.get("disabled"):
            continue
        params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
        if node.get("type") == CHAT_TRIGGER_TYPE and node.get("webhookId"):
            return {"kind": "chat", "path": f"{node['webhookId']}/chat", "method": "POST",
                    "node": node.get("name") or ""}
        if node.get("type") == WEBHOOK_TYPE and found is None:
            path = str(params.get("path") or node.get("webhookId") or "").strip("/")
            if path:
                method = str(params.get("httpMethod") or "GET").upper()
                found = {"kind": "webhook", "path": path, "method": method,
                         "node": node.get("name") or ""}
    return found


class N8nClient:
    def __init__(self, base_url: str, api_key: str = "", *,
                 header_name: str = "", header_value: str = "") -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise N8nError("No n8n connection configured. Add the base URL on the Connectors page (n8n tab).")
        if not self.base_url.startswith(("http://", "https://")):
            raise N8nError("The n8n base URL must start with http:// or https://")
        self.api_key = (api_key or "").strip()
        self.header_name = (header_name or "").strip()
        self.header_value = (header_value or "").strip()

    # ── REST API ──────────────────────────────────────────────────────────

    def _api(self, method: str, path: str, *, params: Optional[dict] = None) -> Any:
        if not self.api_key:
            raise N8nError("The n8n connector has no API key. Add one on the Connectors page to list and import workflows.")
        try:
            resp = httpx.request(
                method, _join(self.base_url, f"api/v1/{path.lstrip('/')}"),
                headers={"X-N8N-API-KEY": self.api_key, "Accept": "application/json"},
                params=params, timeout=_API_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise N8nError(f"n8n is not reachable at {self.base_url}: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            raise N8nError("n8n refused the API key")
        if resp.status_code == 404:
            raise N8nError("n8n has no such workflow (or the public API is turned off)")
        if resp.status_code >= 400:
            raise N8nError(f"n8n API error {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise N8nError("n8n answered with something that is not JSON") from exc

    def list_workflows(self, limit: int = 250) -> list[dict[str, Any]]:
        """Every workflow, following n8n's cursor, up to ``limit``."""
        out: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while len(out) < limit:
            params: dict[str, Any] = {"limit": min(100, limit - len(out))}
            if cursor:
                params["cursor"] = cursor
            page = self._api("GET", "workflows", params=params) or {}
            out.extend(w for w in (page.get("data") or []) if isinstance(w, dict))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return out

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        data = self._api("GET", f"workflows/{quote(str(workflow_id), safe='')}")
        if not isinstance(data, dict):
            raise N8nError("n8n returned an empty workflow")
        return data

    def test_connection(self) -> dict[str, Any]:
        if self.api_key:
            self._api("GET", "workflows", params={"limit": 1})
            return {"ok": True, "identity": self.base_url}
        try:
            resp = httpx.get(_join(self.base_url, "healthz"), timeout=10.0)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"n8n is not reachable: {exc.__class__.__name__}"}
        if resp.status_code >= 400:
            return {"ok": False, "error": f"n8n health check answered {resp.status_code}"}
        return {"ok": True, "identity": f"{self.base_url} (no API key: runs only, no import)"}

    # ── runs ──────────────────────────────────────────────────────────────

    def run(self, path: str, *, kind: str = "webhook", method: str = "POST",
            payload: Optional[dict[str, Any]] = None, message: str = "",
            session_id: str = "", timeout: Optional[float] = None) -> Any:
        """Start a workflow through its trigger and return what it answered.

        ``kind="chat"`` speaks the Chat trigger's protocol (``chatInput`` and a
        ``sessionId`` so one flow run is one n8n chat session); a webhook gets
        ``payload`` as JSON, or as query parameters for GET. What comes back
        depends on the trigger's response mode: the last node's output, the
        Respond to Webhook node's body, or only "Workflow was started".
        """
        clean = str(path or "").strip().strip("/")
        if not _PATH_RE.fullmatch(clean) or ".." in clean.split("/"):
            raise N8nError("The n8n node needs a webhook path, e.g. my-hook or <webhookId>/chat")
        url = _join(self.base_url, f"webhook/{clean}")
        headers = {"Accept": "application/json"}
        if self.header_name and self.header_value:
            headers[self.header_name] = self.header_value
        method = (method or "POST").upper()
        if kind == "chat":
            body: dict[str, Any] = {"action": "sendMessage", "sessionId": session_id or "hub",
                                    "chatInput": message}
            method = "POST"
        else:
            body = dict(payload or {})
        try:
            if method in ("GET", "HEAD", "DELETE"):
                flat = {k: v if isinstance(v, (str, int, float, bool)) else str(v) for k, v in body.items()}
                resp = httpx.request(method, url, headers=headers, params=flat,
                                     timeout=timeout or DEFAULT_RUN_TIMEOUT)
            else:
                resp = httpx.request(method, url, headers=headers, json=body,
                                     timeout=timeout or DEFAULT_RUN_TIMEOUT)
        except httpx.TimeoutException as exc:
            raise N8nError("The n8n workflow did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise N8nError(f"n8n is not reachable at {self.base_url}: {exc.__class__.__name__}") from exc
        if resp.status_code == 404:
            raise N8nError(
                f"n8n has no active webhook at /webhook/{clean}. Activate the workflow in n8n "
                "and check the path and HTTP method."
            )
        if resp.status_code in (401, 403):
            raise N8nError("n8n refused the webhook call: check the header auth on the n8n connector")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str((resp.json() or {}).get("message") or "")
            except (ValueError, AttributeError, TypeError):
                detail = resp.text[:200]
            raise N8nError(f"The n8n workflow failed ({resp.status_code}){': ' + detail if detail else ''}")
        try:
            return resp.json()
        except ValueError:
            return resp.text


def answer_of(response: Any) -> Any:
    """The useful part of a trigger's answer: an agent's ``output`` text when
    there is one (a Chat trigger, an AI Agent as last node), else the whole
    body. A one item list, how n8n returns the last node's items, is
    unwrapped."""
    if isinstance(response, list) and len(response) == 1:
        response = response[0]
    if isinstance(response, dict):
        for key in ("output", "text", "response"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return response


__all__ = ["N8nClient", "N8nError", "find_trigger", "answer_of", "DEFAULT_RUN_TIMEOUT",
           "WEBHOOK_TYPE", "CHAT_TRIGGER_TYPE"]
