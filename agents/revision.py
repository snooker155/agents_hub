"""
Optimistic concurrency for agent edits: a write names the definition it was
made against, and is refused when the agent has moved on since.

The token is the agent's *definition hash* (agents/versions.py
``definition_fingerprint``: the behaviour fields of the registry record plus
the three markdown files), the same hash a run records as
``definition_hash``. Two people editing one agent each read it with the
agent, and each write carries it back:

* ``If-Match: "<hash>"`` on any ``PUT``, ``POST`` or ``PATCH`` under
  ``/api/agents/{agent_id}`` (the record's own fields, its definition, its
  loop settings, tool policy, guardrails, secrets, proactive profile), or
* ``expected_version`` in the JSON body: a stored version number (the write
  goes through when that version's hash is the current one) or the hash
  itself as a string.

A write that names a definition other than the current one is answered
``409`` with ``{"detail", "error": "version_conflict", "current_hash",
"current_version", "expected"}``, and nothing is written. A write that names
none goes through as before: the check is opt in, so scripts and older
clients keep working. ``If-Match: *`` always matches (an explicit overwrite).

:class:`AgentRevisionMiddleware` (mounted in dashboard/backend/main.py) does
the check before the route runs and stamps the current hash as ``ETag`` (and
the stored version, when there is one, as ``X-Agent-Version``) on
``GET /api/agents/{id}``, ``GET /api/agents/{id}/definition`` and every
successful write under ``/api/agents/{id}``, so an editor learns the token
of what it just saved without reading the agent back. The agent editor
(dashboard/frontend/src/api/agentRevision.js) sends it and asks the user
to reload or overwrite on a conflict.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

log = logging.getLogger(__name__)

#: The body field a client may send instead of the If-Match header.
BODY_FIELD = "expected_version"
#: Response header carrying the stored version of the current definition.
VERSION_HEADER = "x-agent-version"
#: Writes the check applies to.
WRITE_METHODS = frozenset({"PUT", "POST", "PATCH"})
#: First path segments under /api/agents that are not an agent id.
COLLECTION_SEGMENTS = frozenset({"", "tools", "workspace-capacities", "capability-check", "create",
                                 "import"})
#: Sub paths whose GET carries the ETag (the editor's two reads).
TAGGED_READS = frozenset({"", "definition"})
#: The largest request body read for ``expected_version``.
MAX_BODY_BYTES = 2_000_000

_PATH = re.compile(r"^/api/agents/([^/]+)(?:/(.*))?$")


class RevisionConflict(Exception):
    """The write named a definition that is no longer the agent's."""

    def __init__(self, agent_id: str, *, expected: Any, current_hash: str,
                 current_version: Optional[int]) -> None:
        super().__init__(f"agent '{agent_id}' changed since it was read")
        self.agent_id = agent_id
        self.expected = expected
        self.current_hash = current_hash
        self.current_version = current_version

    def body(self) -> Dict[str, Any]:
        version = f" (now v{self.current_version})" if self.current_version is not None else ""
        return {
            "detail": (f"Agent '{self.agent_id}' was changed by someone else since you opened it"
                       f"{version}. Reload to see the changes, or save again to overwrite them."),
            "error": "version_conflict",
            "agent_id": self.agent_id,
            "current_hash": self.current_hash,
            "current_version": self.current_version,
            "expected": self.expected,
        }


def current(agent_id: str) -> Optional[Dict[str, Any]]:
    """``{"hash", "version"}`` of the agent's definition now, or None when
    there is no such agent. ``version`` is the stored version whose hash this
    is, None when the current state has no row yet."""
    from agents import versions
    try:
        fp = versions.definition_fingerprint(agent_id)
    except ValueError:
        return None
    return {"hash": fp["hash"], "version": versions.version_for_hash(agent_id, fp["hash"])}


def parse_if_match(value: Optional[str]) -> List[str]:
    """The entity tags of an If-Match header: quotes and weak markers off,
    ``*`` kept as is."""
    tags: List[str] = []
    for part in str(value or "").split(","):
        tag = part.strip()
        if tag.startswith("W/"):
            tag = tag[2:]
        tag = tag.strip().strip('"').strip()
        if tag:
            tags.append(tag)
    return tags


def _matches(agent_id: str, expected: Any, now: Dict[str, Any]) -> bool:
    if isinstance(expected, bool):
        return False
    if isinstance(expected, int):
        if now.get("version") == expected:
            return True
        from agents import versions
        row = versions.get_version_row(agent_id, expected)
        return row is not None and row["hash"] == now["hash"]
    text = str(expected).strip().strip('"')
    if text == "*":
        return True
    if text.isdigit():
        return _matches(agent_id, int(text), now)
    return text == now["hash"]


def check(agent_id: str, *, if_match: Optional[str] = None, expected_version: Any = None) -> None:
    """Raise :class:`RevisionConflict` when the write names a definition other
    than the current one. A no-op when it names none, or the agent does not
    exist (the route answers 404 itself)."""
    tags = parse_if_match(if_match)
    if not tags and expected_version in (None, ""):
        return
    now = current(agent_id)
    if now is None:
        return
    if tags and not any(_matches(agent_id, tag, now) for tag in tags):
        raise RevisionConflict(agent_id, expected=if_match, current_hash=now["hash"],
                               current_version=now["version"])
    if expected_version not in (None, "") and not _matches(agent_id, expected_version, now):
        raise RevisionConflict(agent_id, expected=expected_version, current_hash=now["hash"],
                               current_version=now["version"])


def agent_path(path: str) -> Optional[tuple]:
    """``(agent_id, sub_path)`` for a path under ``/api/agents/{id}``, else None."""
    match = _PATH.match(path or "")
    if not match:
        return None
    agent_id = unquote(match.group(1))
    if agent_id in COLLECTION_SEGMENTS:
        return None
    return agent_id, (match.group(2) or "").strip("/")


def _canonical_agent_scope(scope: Dict[str, Any]) -> Dict[str, Any]:
    """A renamed agent's old id in the path (``/api/agents/researcher_agent/
    versions``, from a bookmark or an old script) becomes its current id, so
    every per agent route, the version history included, answers for the
    agent rather than for an id nothing is stored under any more."""
    path = scope.get("path") or ""
    target = agent_path(path)
    if target is None:
        return scope
    from agents.registry import LEGACY_AGENT_IDS, resolve_agent_id

    old_id = target[0]
    if old_id not in LEGACY_AGENT_IDS:
        return scope
    new_id = resolve_agent_id(old_id)
    if new_id == old_id:
        return scope
    prefix = "/api/agents/"
    rest = path[len(prefix):].split("/", 1)
    new_path = prefix + new_id + ("/" + rest[1] if len(rest) > 1 else "")
    return {**scope, "path": new_path, "raw_path": new_path.encode("latin-1")}


def _expected_from_body(body: bytes) -> Any:
    if not body or len(body) > MAX_BODY_BYTES:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data.get(BODY_FIELD) if isinstance(data, dict) else None


class AgentRevisionMiddleware:
    """Pure ASGI middleware: the conflict check before an agent write, the
    ETag after it (see the module docstring). Every other request passes
    through untouched."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        scope = _canonical_agent_scope(scope)
        target = agent_path(scope.get("path") or "")
        method = str(scope.get("method") or "").upper()
        if target is None or (method not in WRITE_METHODS and method != "GET"):
            await self.app(scope, receive, send)
            return
        agent_id, sub = target
        if method == "GET" and sub not in TAGGED_READS:
            await self.app(scope, receive, send)
            return

        if method in WRITE_METHODS:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers") or []}
            body = b""
            if "json" in headers.get("content-type", ""):
                body, receive = await _buffer(receive)
            try:
                from anyio import to_thread
                await to_thread.run_sync(lambda: check(
                    agent_id, if_match=headers.get("if-match"),
                    expected_version=_expected_from_body(body)))
            except RevisionConflict as conflict:
                await _send_json(send, 409, conflict.body())
                return

        async def send_with_tag(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and int(message.get("status") or 500) < 400:
                try:
                    from anyio import to_thread
                    now = await to_thread.run_sync(current, agent_id)
                except Exception:  # noqa: BLE001 - a missing tag only costs the editor its next check
                    log.debug("agent revision: no tag for %s", agent_id, exc_info=True)
                    now = None
                if now is not None:
                    extra = [(b"etag", f'"{now["hash"]}"'.encode("latin-1"))]
                    if now.get("version") is not None:
                        extra.append((VERSION_HEADER.encode("latin-1"), str(now["version"]).encode("latin-1")))
                    kept = [(k, v) for k, v in message.get("headers") or []
                            if k.lower() not in (b"etag", VERSION_HEADER.encode("latin-1"))]
                    message = {**message, "headers": kept + extra}
            await send(message)

        await self.app(scope, receive, send_with_tag)


async def _buffer(receive: Any) -> tuple:
    """Read the whole request body and hand back a ``receive`` that replays it."""
    chunks: List[bytes] = []
    more = True
    while more:
        message = await receive()
        if message.get("type") != "http.request":
            # A disconnect before the body arrived: replay it as it came.
            async def gone() -> Dict[str, Any]:
                return message
            return b"", gone
        chunks.append(message.get("body") or b"")
        more = bool(message.get("more_body"))
    body = b"".join(chunks)
    sent = False

    async def replay() -> Dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


async def _send_json(send: Any, status: int, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode("latin-1"))]})
    await send({"type": "http.response.body", "body": data})


__all__ = ["RevisionConflict", "AgentRevisionMiddleware", "check", "current", "parse_if_match",
           "agent_path", "BODY_FIELD"]
