"""Readiness checks — can this imported agent actually run here?

The import flow never refuses an agent. A repository that does not yet meet the
contract still lands in the agent list, carrying the report produced here so the
operator sees exactly which pieces are missing and how to supply them. That is
the difference between "import failed" (nothing to act on) and "imported, needs
a Dockerfile and OPENAI_API_KEY" (a to-do list).

A check is *required* when its failure means a run would certainly fail. Health
is deliberately not required: an agent whose service has not been started yet is
correctly configured, merely idle.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.importer.manifest import AgentManifest, MANIFEST_FILENAMES, SUPPORTED_RUNTIME_KINDS


@dataclass
class ReadinessCheck:
    """One verdict about an imported agent, with its remedy attached."""
    id: str
    label: str
    ok: bool
    required: bool = True
    detail: str = ""
    # What the operator should do when ``ok`` is False. Empty for passing checks.
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass
class ReadinessReport:
    """The full verdict: every check, plus whether the agent can be run."""
    checks: List[ReadinessCheck] = field(default_factory=list)
    checked_at: str = ""

    @property
    def runnable(self) -> bool:
        """True when no required check failed."""
        return all(c.ok for c in self.checks if c.required)

    @property
    def blocking(self) -> List[ReadinessCheck]:
        return [c for c in self.checks if c.required and not c.ok]

    @property
    def warnings(self) -> List[ReadinessCheck]:
        return [c for c in self.checks if not c.required and not c.ok]

    def summary(self) -> str:
        """One line suitable for a badge tooltip or an API message."""
        if self.runnable:
            warn = len(self.warnings)
            return "Ready to run" if not warn else f"Ready to run ({warn} recommendation(s))"
        missing = ", ".join(c.label for c in self.blocking)
        return f"Cannot run yet — missing: {missing}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runnable": self.runnable,
            "summary": self.summary(),
            "checked_at": self.checked_at,
            "checks": [c.to_dict() for c in self.checks],
            "blocking": [c.to_dict() for c in self.blocking],
            "warnings": [c.to_dict() for c in self.warnings],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_env(workspace: Optional[str]) -> Dict[str, str]:
    """Env vars configured on a workspace, or {} — best effort.

    An imported agent may be run inside a workspace whose metadata supplies the
    keys it needs, so the env check has to look there as well as at the hub
    process environment.
    """
    name = (workspace or "").strip()
    if not name:
        return {}
    try:
        from workspace import get_workspace_metadata
        meta = get_workspace_metadata(name) or {}
    except Exception:
        return {}
    env = meta.get("env_vars")
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}


# ── individual checks ───────────────────────────────────────────────────────

def check_repo(repo_dir: Path, commit: str = "") -> ReadinessCheck:
    ok = repo_dir.is_dir() and any(repo_dir.iterdir())
    return ReadinessCheck(
        id="repo",
        label="Repository cloned",
        ok=ok,
        detail=(f"clone at {repo_dir.name}" + (f" @ {commit}" if commit else "")) if ok
        else "the clone is missing or empty",
        fix="" if ok else "Check the repository URL and branch, then run the check again.",
    )


def check_manifest(manifest: AgentManifest) -> ReadinessCheck:
    if not manifest.found:
        return ReadinessCheck(
            id="manifest",
            label="Manifest present",
            ok=False,
            detail=f"none of {', '.join(MANIFEST_FILENAMES)} exists at the repository root",
            fix=(
                "Add agent-hub.json to the repository root declaring id, name and a "
                "runtime block. You can also fill the fields in by hand below and "
                "import without one."
            ),
        )
    if manifest.problems:
        return ReadinessCheck(
            id="manifest",
            label="Manifest valid",
            ok=False,
            detail=f"{manifest.path}: " + "; ".join(manifest.problems),
            fix="Correct the manifest in the repository and run the check again.",
        )
    return ReadinessCheck(
        id="manifest",
        label="Manifest valid",
        ok=True,
        detail=f"{manifest.path} parsed cleanly",
    )


def check_agent_id(agent_id: str, *, conflict: Optional[str] = None) -> ReadinessCheck:
    from agents.importer.manifest import AGENT_ID_RE

    if not agent_id:
        return ReadinessCheck(
            id="agent_id", label="Agent id", ok=False,
            detail="no agent id was resolved",
            fix="Set 'id' in the manifest or type one in the import dialog.",
        )
    if not AGENT_ID_RE.match(agent_id):
        return ReadinessCheck(
            id="agent_id", label="Agent id", ok=False,
            detail=f"'{agent_id}' is not a valid id",
            fix="Use 2-64 characters: lowercase letters, digits, '-' or '_'.",
        )
    if conflict:
        return ReadinessCheck(
            id="agent_id", label="Agent id", ok=False,
            detail=conflict,
            fix="Choose a different id in the import dialog, or delete the existing agent first.",
        )
    return ReadinessCheck(id="agent_id", label="Agent id", ok=True, detail=agent_id)


def check_runtime(manifest: AgentManifest) -> ReadinessCheck:
    if not manifest.found:
        return ReadinessCheck(
            id="runtime", label="Runtime declared", ok=False,
            detail="no manifest, so no runtime block",
            fix="Declare runtime.kind = \"http\" with a run_path in the manifest.",
        )
    if manifest.runtime_kind not in SUPPORTED_RUNTIME_KINDS:
        return ReadinessCheck(
            id="runtime", label="Runtime declared", ok=False,
            detail=f"runtime.kind '{manifest.runtime_kind}' is not supported",
            fix=f"Set runtime.kind to one of: {', '.join(SUPPORTED_RUNTIME_KINDS)}.",
        )
    return ReadinessCheck(
        id="runtime", label="Runtime declared", ok=True,
        detail=f"{manifest.runtime_kind} — POST {manifest.run_path}",
    )


def check_endpoint(url: str) -> ReadinessCheck:
    if not (url or "").strip():
        return ReadinessCheck(
            id="endpoint", label="Endpoint URL", ok=False,
            detail="no URL configured for the agent's HTTP service",
            fix=(
                "Start the agent's service (its Dockerfile or its own run instructions) "
                "and enter the base URL here, e.g. http://localhost:8410."
            ),
        )
    return ReadinessCheck(id="endpoint", label="Endpoint URL", ok=True, detail=url)


def check_packaging(repo_dir: Path, manifest: AgentManifest, url: str) -> ReadinessCheck:
    """Whether the repo ships a way to start the service itself.

    Not required: an operator who already runs the agent elsewhere and supplies
    its URL owes the hub nothing further. It matters only as the answer to
    "how do I bring this up?", so it is reported as a recommendation.
    """
    if manifest.dockerfile:
        path = repo_dir / manifest.dockerfile
        if path.is_file():
            return ReadinessCheck(
                id="packaging", label="Start instructions", ok=True, required=False,
                detail=f"{manifest.dockerfile} found in the repository",
            )
        return ReadinessCheck(
            id="packaging", label="Start instructions", ok=False, required=False,
            detail=f"manifest names {manifest.dockerfile}, but that file is not in the repository",
            fix=f"Add {manifest.dockerfile}, or correct runtime.docker.dockerfile in the manifest.",
        )
    if (url or "").strip():
        return ReadinessCheck(
            id="packaging", label="Start instructions", ok=True, required=False,
            detail="no Dockerfile declared, but the service URL was supplied directly",
        )
    return ReadinessCheck(
        id="packaging", label="Start instructions", ok=False, required=False,
        detail="the repository declares no Dockerfile and no URL was supplied",
        fix=(
            "Declare runtime.docker.dockerfile in the manifest so the service can be built, "
            "or start it yourself and paste its URL."
        ),
    )


def check_streaming(manifest: AgentManifest) -> ReadinessCheck:
    """Whether the agent streams. Informational — a non-streaming agent is fine.

    Reported as its own line rather than buried in the docs because it is the
    difference between a code agent that shows its work for ten minutes and one
    that goes quiet until it is done, and that is worth knowing before the first
    long run rather than after it.
    """
    if manifest.stream_path:
        return ReadinessCheck(
            id="streaming", label="Live streaming", ok=True, required=False,
            detail=f"POST {manifest.stream_path} — tokens and tool events stream into chat",
        )
    return ReadinessCheck(
        id="streaming", label="Live streaming", ok=False, required=False,
        detail="not declared — runs return in one response, with no live output",
        fix=(
            "Declare runtime.stream_path and answer it with NDJSON/SSE frames "
            "(token, thinking, tool_start, tool_end, usage, done) to stream this "
            "agent's output into chat and restore token accounting."
        ),
    )


def check_env(manifest: AgentManifest, workspace: Optional[str] = None) -> ReadinessCheck:
    required = [e for e in manifest.env if e.required]
    if not required:
        return ReadinessCheck(
            id="env", label="Environment variables", ok=True,
            detail="none declared as required",
        )
    ws_env = _workspace_env(workspace)
    missing = [
        e for e in required
        if not (os.environ.get(e.name) or "").strip() and not (ws_env.get(e.name) or "").strip()
    ]
    if not missing:
        return ReadinessCheck(
            id="env", label="Environment variables", ok=True,
            detail=", ".join(e.name for e in required) + " are set",
        )
    described = "; ".join(
        f"{e.name}" + (f" ({e.description})" if e.description else "") for e in missing
    )
    return ReadinessCheck(
        id="env", label="Environment variables", ok=False,
        detail=f"not set: {described}",
        fix=(
            "Set these in the hub's environment, or on the workspace under "
            "Workspace → Environment variables, then run the check again."
        ),
    )


def check_health(url: str, manifest: AgentManifest, remote: Optional[Dict[str, Any]] = None) -> ReadinessCheck:
    """Probe the live service. Optional — an unstarted service is not an error."""
    if not (url or "").strip():
        return ReadinessCheck(
            id="health", label="Service reachable", ok=False, required=False,
            detail="not probed — no URL configured",
            fix="Supply the service URL to have the hub verify it responds.",
        )
    from agents.remote_agent import RemoteAgent

    descriptor = dict(remote or {})
    descriptor.setdefault("url", url)
    descriptor.setdefault("health_path", manifest.health_path)
    probe = RemoteAgent(agent_id="__probe__", name="probe", remote=descriptor).check_health()
    if probe["ok"]:
        return ReadinessCheck(
            id="health", label="Service reachable", ok=True, required=False,
            detail=f"{url}{manifest.health_path} answered {probe['detail']}",
        )
    return ReadinessCheck(
        id="health", label="Service reachable", ok=False, required=False,
        detail=f"{url}{manifest.health_path} — {probe['detail']}",
        fix=(
            "Start the agent's service. The agent is still imported; re-run the check "
            "from its page once it is up."
        ),
    )


# ── the whole report ────────────────────────────────────────────────────────

def evaluate(
    repo_dir: Path,
    manifest: AgentManifest,
    *,
    agent_id: str,
    url: str = "",
    commit: str = "",
    workspace: Optional[str] = None,
    id_conflict: Optional[str] = None,
    probe_health: bool = True,
    remote: Optional[Dict[str, Any]] = None,
) -> ReadinessReport:
    """Run every check and return the combined report."""
    checks = [
        check_repo(repo_dir, commit),
        check_manifest(manifest),
        check_agent_id(agent_id, conflict=id_conflict),
        check_runtime(manifest),
        check_endpoint(url),
        check_env(manifest, workspace),
        check_packaging(repo_dir, manifest, url),
        check_streaming(manifest),
    ]
    if probe_health:
        checks.append(check_health(url, manifest, remote))
    return ReadinessReport(checks=checks, checked_at=_now())


__all__ = [
    "ReadinessCheck",
    "ReadinessReport",
    "evaluate",
    "check_repo",
    "check_manifest",
    "check_agent_id",
    "check_runtime",
    "check_endpoint",
    "check_packaging",
    "check_env",
    "check_streaming",
    "check_health",
]
