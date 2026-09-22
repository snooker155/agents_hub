"""The manifest an external repository publishes to be importable as an agent.

This file *is* the contract. A repository that wants its agent to run inside the
hub drops one JSON file at its root; everything the import pipeline knows about
a foreign agent comes from here, and the import modal renders
:data:`REQUIREMENTS` so an operator can read the same contract before they paste
a URL.

Minimal example (``agent-hub.json`` at the repo root)::

    {
      "schema": "agents-hub/agent-manifest@1",
      "id": "aider",
      "name": "Aider",
      "description": "AI pair programmer that edits code in a local git repo.",
      "domain": "Software Engineering",
      "runtime": {
        "kind": "http",
        "port": 8410,
        "run_path": "/run",
        "health_path": "/health",
        "docker": {"dockerfile": "Dockerfile.agenthub"},
        "env": [
          {"name": "OPENAI_API_KEY", "required": true,
           "description": "Model key the agent uses for its own completions."}
        ]
      }
    }

Nothing here is guessed from the repo's contents. An agent that ships no
manifest is still importable — the pipeline records precisely which parts are
missing and registers the agent in a "needs setup" state — but it cannot run
until the gaps are filled in.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# Filenames scanned at the repo root, in order. The dotted variant exists so a
# repo that dislikes a visible config file can hide it.
MANIFEST_FILENAMES = ("agent-hub.json", ".agent-hub.json", "agenthub.json")

SCHEMA_ID = "agents-hub/agent-manifest@1"

# The runtime kinds implemented today. Declared as a set so an unsupported
# value produces a named, actionable error rather than a silent misconfiguration.
#
# ``http`` is this hub's own tiny contract (see the module docstring).
# ``a2a`` is the Agent2Agent protocol: the agent is already running and speaks
# JSON-RPC behind an Agent Card, so ``runtime.url`` is its JSON-RPC endpoint and
# no other path is needed. Such an agent is usually imported straight from its
# card URL, without a repository at all (see agents/importer/a2a_import.py).
SUPPORTED_RUNTIME_KINDS = ("http", "a2a")

AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


# Rendered in the import modal so the requirements are visible *before* an
# import is attempted, which is the point at which they are cheapest to fix.
REQUIREMENTS: List[Dict[str, str]] = [
    {
        "id": "manifest",
        "title": "A manifest at the repository root",
        "detail": (
            "One of " + ", ".join(MANIFEST_FILENAMES) + " declaring the agent's id, "
            "name and runtime block. This is the only file the hub requires the "
            "repository to add."
        ),
    },
    {
        "id": "http",
        "title": "An HTTP endpoint that runs one prompt",
        "detail": (
            "POST <run_path> receiving {\"prompt\": str, \"run_id\": str|null, "
            "\"workspace\": str|null} and replying with JSON {\"ok\": bool, "
            "\"output\": str, \"error\": str|null}. The hub never imports the "
            "agent's Python, so its dependencies stay in its own process."
        ),
    },
    {
        "id": "streaming",
        "title": "A streaming endpoint (optional)",
        "detail": (
            "Declare runtime.stream_path and answer it with NDJSON or SSE frames "
            "({\"type\": \"token\"|\"thinking\"|\"tool_start\"|\"tool_end\"|"
            "\"usage\"|\"done\"}). The agent's tokens then appear live in chat "
            "like a built-in agent's, and a usage frame restores token and cost "
            "accounting. Without it a run is a single request and response."
        ),
    },
    {
        "id": "health",
        "title": "A health endpoint (recommended)",
        "detail": (
            "GET <health_path> returning any 2xx/3xx status. Used by the readiness "
            "check and by the agent card to show whether the service is up."
        ),
    },
    {
        "id": "packaging",
        "title": "A way to start the service",
        "detail": (
            "Either a Dockerfile named in runtime.docker.dockerfile, or an already "
            "running service whose URL you supply at import time. Without one of "
            "the two the agent imports but cannot be run."
        ),
    },
    {
        "id": "a2a",
        "title": "Or: an A2A agent card",
        "detail": (
            "An agent that already speaks the Agent2Agent protocol needs no "
            "manifest and no repository. Paste the URL of its card "
            "(.../.well-known/agent-card.json) instead of a repository URL and "
            "the hub reads its endpoint, name and skills from there."
        ),
    },
    {
        "id": "env",
        "title": "Declared environment variables",
        "detail": (
            "runtime.env lists what the agent needs (model keys, tokens). Required "
            "entries that are unset in the hub's environment are reported as "
            "missing rather than failing at run time."
        ),
    },
]


@dataclass
class EnvRequirement:
    """One environment variable the external agent declares it needs."""
    name: str
    required: bool = True
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "required": self.required, "description": self.description}


@dataclass
class AgentManifest:
    """A parsed manifest. ``problems`` holds soft validation errors.

    Parsing never raises for a malformed manifest: the import pipeline wants to
    report *every* problem at once in the readiness report, not stop at the
    first one. A manifest that could not be found at all is represented by
    ``found=False`` with an empty rest.
    """
    found: bool = False
    path: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    id: str = ""
    name: str = ""
    description: str = ""
    domain: str = "external"

    runtime_kind: str = "http"
    url: str = ""
    run_path: str = "/run"
    health_path: str = "/health"
    # Optional. When set, runs stream from this endpoint instead of awaiting a
    # single POST; empty means the agent does not stream.
    stream_path: str = ""
    # Optional. An agent that is internally a graph (LangGraph, and anything
    # else that knows its own shape) may publish that shape here, and the hub
    # draws it instead of rendering the agent as one opaque box. Empty means the
    # agent has no shape to show, which is true of most agents.
    graph_path: str = ""
    # Optional. An agent that can be *continued* declares this: the hub posts
    # the human's answer here, and the agent picks its own work back up where it
    # stopped. Without it a paused agent can be asked a question and told the
    # answer, but only by being run again from the start — which for a graph
    # with a checkpointer is not the same thing at all.
    resume_path: str = ""
    # A2A only. ``card_url`` is where the agent's card was read from, and
    # ``card`` is the card itself, kept so the readiness report and the agent
    # page can show what the agent declared without fetching it again.
    card_url: str = ""
    card: Dict[str, Any] = field(default_factory=dict)
    port: Optional[int] = None
    timeout: Optional[int] = None
    auth_token_env: str = ""
    auth_header: str = ""

    dockerfile: str = ""
    docker_context: str = "."

    env: List[EnvRequirement] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    docs_path: str = ""

    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "path": self.path,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "runtime_kind": self.runtime_kind,
            "url": self.url,
            "run_path": self.run_path,
            "health_path": self.health_path,
            "stream_path": self.stream_path,
            "graph_path": self.graph_path,
            "resume_path": self.resume_path,
            "card_url": self.card_url,
            "card": dict(self.card),
            "port": self.port,
            "timeout": self.timeout,
            "auth_token_env": self.auth_token_env,
            "auth_header": self.auth_header,
            "dockerfile": self.dockerfile,
            "docker_context": self.docker_context,
            "env": [e.to_dict() for e in self.env],
            "tools": list(self.tools),
            "docs_path": self.docs_path,
            "problems": list(self.problems),
        }


def find_manifest_file(repo_dir: Path) -> Optional[Path]:
    """Return the manifest path inside *repo_dir*, or None when absent."""
    for name in MANIFEST_FILENAMES:
        candidate = repo_dir / name
        if candidate.is_file():
            return candidate
    return None


def _as_str(value: Any, default: str = "") -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and str(value).strip() else default


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_path(value: str, default: str) -> str:
    """Coerce an endpoint path to a leading-slash form."""
    path = (value or "").strip() or default
    return path if path.startswith("/") else "/" + path


def parse_manifest(repo_dir: Path) -> AgentManifest:
    """Read and validate the manifest in *repo_dir*, collecting all problems."""
    path = find_manifest_file(repo_dir)
    if path is None:
        return AgentManifest(found=False)

    manifest = AgentManifest(found=True, path=path.name)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        manifest.problems.append(f"{path.name} is not valid JSON: {exc}")
        return manifest
    except OSError as exc:
        manifest.problems.append(f"{path.name} could not be read: {exc}")
        return manifest

    if not isinstance(raw, dict):
        manifest.problems.append(f"{path.name} must contain a JSON object at the top level")
        return manifest

    manifest.raw = raw

    schema = _as_str(raw.get("schema"))
    if schema and schema != SCHEMA_ID:
        # A wrong schema is a warning, not a hard stop: the fields we read may
        # still all be present, and refusing outright would be unhelpful.
        manifest.problems.append(
            f"unknown manifest schema '{schema}' (this hub understands '{SCHEMA_ID}')"
        )

    manifest.id = _as_str(raw.get("id"))
    if not manifest.id:
        manifest.problems.append("'id' is missing — it becomes the agent id in the hub")
    elif not AGENT_ID_RE.match(manifest.id):
        manifest.problems.append(
            f"'id' must be lowercase letters, digits, '-' or '_' (2-64 chars); got '{manifest.id}'"
        )

    manifest.name = _as_str(raw.get("name")) or manifest.id
    manifest.description = _as_str(raw.get("description"))
    manifest.domain = _as_str(raw.get("domain"), "external")
    manifest.docs_path = _as_str(raw.get("docs"))

    runtime = raw.get("runtime")
    if not isinstance(runtime, dict):
        manifest.problems.append("'runtime' block is missing — the hub has no way to reach the agent")
        return manifest

    manifest.runtime_kind = _as_str(runtime.get("kind"), "http")
    if manifest.runtime_kind not in SUPPORTED_RUNTIME_KINDS:
        manifest.problems.append(
            f"runtime.kind '{manifest.runtime_kind}' is not supported "
            f"(supported: {', '.join(SUPPORTED_RUNTIME_KINDS)})"
        )

    manifest.url = _as_str(runtime.get("url"))
    manifest.run_path = _normalize_path(_as_str(runtime.get("run_path")), "/run")
    manifest.health_path = _normalize_path(_as_str(runtime.get("health_path")), "/health")
    # No default: an unset stream_path means "this agent does not stream", and
    # guessing a path would cost a failed request on every single run.
    raw_stream = _as_str(runtime.get("stream_path"))
    manifest.stream_path = _normalize_path(raw_stream, "") if raw_stream else ""
    # Same rule as stream_path: no default. An undeclared graph endpoint means
    # "this agent is not a graph", and probing a guessed path would spend a
    # failed request on every import to learn nothing.
    raw_graph = _as_str(runtime.get("graph_path"))
    manifest.graph_path = _normalize_path(raw_graph, "") if raw_graph else ""
    raw_resume = _as_str(runtime.get("resume_path"))
    manifest.resume_path = _normalize_path(raw_resume, "") if raw_resume else ""
    manifest.card_url = _as_str(runtime.get("card_url"))
    manifest.port = _as_int(runtime.get("port"))
    manifest.timeout = _as_int(runtime.get("timeout"))
    manifest.auth_token_env = _as_str(runtime.get("auth_token_env"))
    manifest.auth_header = _as_str(runtime.get("auth_header"))

    docker = runtime.get("docker")
    if isinstance(docker, dict):
        manifest.dockerfile = _as_str(docker.get("dockerfile"))
        manifest.docker_context = _as_str(docker.get("context"), ".")

    raw_env = runtime.get("env")
    if isinstance(raw_env, list):
        for entry in raw_env:
            if isinstance(entry, str):
                manifest.env.append(EnvRequirement(name=entry.strip()))
            elif isinstance(entry, dict):
                name = _as_str(entry.get("name"))
                if not name:
                    manifest.problems.append("runtime.env entry without a 'name' was ignored")
                    continue
                manifest.env.append(EnvRequirement(
                    name=name,
                    required=bool(entry.get("required", True)),
                    description=_as_str(entry.get("description")),
                ))
    elif raw_env is not None:
        manifest.problems.append("runtime.env must be a list")

    capabilities = raw.get("capabilities")
    if isinstance(capabilities, dict):
        raw_tools = capabilities.get("tools")
        if isinstance(raw_tools, list):
            manifest.tools = [str(t).strip() for t in raw_tools if str(t).strip()]

    return manifest


__all__ = [
    "AgentManifest",
    "EnvRequirement",
    "MANIFEST_FILENAMES",
    "SCHEMA_ID",
    "SUPPORTED_RUNTIME_KINDS",
    "AGENT_ID_RE",
    "REQUIREMENTS",
    "find_manifest_file",
    "parse_manifest",
]
