"""Orchestration for importing an agent from an external repository.

Three entry points, matching the three moments in the flow:

``inspect``  clone the repo into scratch space, read its manifest, and report
             what works and what is missing — nothing is registered yet.
``register`` promote the inspected clone and add the agent to the registry,
             *whether or not* it is runnable. An agent that failed a required
             check is registered anyway, carrying its report, so it shows up in
             the agent list with the reasons attached instead of vanishing.
``recheck``  re-run the checks against an already-imported agent, after the
             operator has started its service or supplied a missing key.

The resulting registry record is a normal ``AgentSpec`` with ``type="remote"``,
so every existing surface — the agent list, chat, tasks, flows — reaches it
through the unchanged ``create_agent`` path (see :mod:`agents.remote_agent`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from agents import prompt_assembly, registry
from agents.importer import checks, clone
from agents.importer.manifest import AgentManifest, parse_manifest
from agents.remote_agent import DEFAULT_HEALTH_PATH, DEFAULT_RUN_PATH, normalize_base_url

# ``entrypoint`` is required on every registry record and must be an importable
# "module:attr". For remote agents it documents the adapter that will run them
# rather than a factory the loader calls — agent_factory branches on
# ``type == "remote"`` before it ever resolves an entrypoint.
REMOTE_ENTRYPOINT = "agents.remote_agent:RemoteAgent"


class ImportError_(Exception):
    """Raised for import failures that are the operator's to fix."""


def _conflict_for(agent_id: str, repo_url: str) -> Optional[str]:
    """Describe an id collision, or None when the id is free to use.

    Re-importing the *same* repository over an existing imported agent is an
    update, not a collision — that is how an operator picks up a newer commit.
    """
    existing = registry.get_agent(agent_id)
    if existing is None:
        return None
    if not existing.is_remote():
        return f"agent '{agent_id}' already exists in this hub and is not an imported agent"
    previous_repo = (existing.remote or {}).get("repo_url") or ""
    if previous_repo and repo_url and previous_repo != repo_url:
        return (
            f"agent '{agent_id}' was already imported from a different repository "
            f"({previous_repo})"
        )
    return None


def _descriptor(
    manifest: AgentManifest,
    *,
    url: str,
    repo_url: str,
    branch: str,
    commit: str,
    clone_path: str,
) -> Dict[str, Any]:
    """Build the ``AgentSpec.remote`` blob from a manifest plus operator input."""
    descriptor: Dict[str, Any] = {
        "url": normalize_base_url(url),
        "run_path": manifest.run_path or DEFAULT_RUN_PATH,
        "health_path": manifest.health_path or DEFAULT_HEALTH_PATH,
        "repo_url": repo_url,
        "branch": branch or "",
        "commit": commit or "",
        "clone_path": clone_path,
        "manifest": manifest.to_dict(),
    }
    if manifest.timeout:
        descriptor["timeout"] = manifest.timeout
    if manifest.auth_token_env:
        descriptor["auth_token_env"] = manifest.auth_token_env
    if manifest.auth_header:
        descriptor["auth_header"] = manifest.auth_header
    if manifest.port:
        descriptor["port"] = manifest.port
    if manifest.dockerfile:
        descriptor["dockerfile"] = manifest.dockerfile
    # Only written when the repository declares it — its presence is what turns
    # streaming on in RemoteAgent.
    if manifest.stream_path:
        descriptor["stream_path"] = manifest.stream_path
    return descriptor


def _definition_markdown(manifest: AgentManifest, descriptor: Dict[str, Any]) -> tuple[str, str, str]:
    """Generate the definition markdown shown on the agent's page.

    A remote agent's real prompt lives in its own repository and the hub never
    sees it. Writing a definition anyway keeps every existing surface working
    (the agent page, the marketplace, prompt assembly) and turns that folder
    into the place an operator reads to find out what this agent is and how it
    is wired.
    """
    name = manifest.name or manifest.id
    env_lines = "\n".join(
        f"- `{e.name}`{' (required)' if e.required else ' (optional)'}"
        + (f" — {e.description}" if e.description else "")
        for e in manifest.env
    ) or "- none declared"

    instructions = f"""# {name} (imported agent)

This agent runs **outside** this service. Its behaviour, prompt and tools live
in its own repository; the hub forwards prompts to it over HTTP and records the
answer like any other run.

- Repository: {descriptor.get('repo_url') or 'n/a'}
- Commit: {descriptor.get('commit') or 'n/a'}
- Endpoint: `POST {descriptor.get('url') or '<not configured>'}{descriptor.get('run_path')}`
- Health: `GET {descriptor.get('url') or '<not configured>'}{descriptor.get('health_path')}`
- Streaming: {f"`POST {descriptor.get('url') or '<not configured>'}{descriptor['stream_path']}`" if descriptor.get('stream_path') else 'not declared'}

{manifest.description}

Editing this file changes only the hub-side documentation — it does not affect
what the imported agent does. To change its behaviour, change its repository and
re-import it.
"""

    capabilities = f"""Declared by the repository's manifest.

Tools (provided by the remote agent itself, not by this hub):
{chr(10).join(f'- {t}' for t in manifest.tools) or '- not declared'}

Environment the agent requires:
{env_lines}
"""

    streaming_line = (
        f"- Runs stream: `POST {descriptor.get('stream_path')}` sends tokens and tool\n"
        f"  events as they happen, so its output appears live in chat."
        if descriptor.get("stream_path")
        else "- Runs do not stream: this agent declares no stream_path, so a run is a\n"
             "  single request and response with no live output."
    )
    usage = f"""Send this agent a prompt exactly as you would any other agent — from Chat, a
task, or a flow node.

What is different:

{streaming_line}
- Token and cost accounting depend on the agent reporting its own usage. The
  model call happens in the remote process, so unless it sends a `usage` frame
  this hub records a duration and an output but no tokens.
- The hub supplies no tools. Anything this agent can do, it does with its own
  tool layer inside its own container.
"""
    return instructions, capabilities, usage


def inspect(
    repo_url: str,
    *,
    branch: Optional[str] = None,
    agent_id: Optional[str] = None,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Clone and analyse a repository without registering anything.

    Returns a dict with the staging ``token`` (pass it to :func:`register`), the
    parsed manifest, the readiness report, and the pre-filled field values the
    import dialog should show.
    """
    token, repo_dir = clone.stage(repo_url, branch=branch)
    try:
        manifest = parse_manifest(repo_dir)
        commit = clone.head_commit(repo_dir)

        resolved_id = (agent_id or "").strip() or manifest.id
        # An explicit URL from the dialog wins: the manifest's URL is only ever
        # the repository author's guess at where their service will be running.
        resolved_url = normalize_base_url((url or "").strip() or manifest.url)

        descriptor = _descriptor(
            manifest, url=resolved_url, repo_url=repo_url,
            branch=branch or "", commit=commit, clone_path="",
        )
        report = checks.evaluate(
            repo_dir, manifest,
            agent_id=resolved_id,
            url=resolved_url,
            commit=commit,
            workspace=workspace,
            id_conflict=_conflict_for(resolved_id, repo_url) if resolved_id else None,
            remote=descriptor,
        )
        return {
            "token": token,
            "repo_url": repo_url,
            "branch": branch or "",
            "commit": commit,
            "manifest": manifest.to_dict(),
            "report": report.to_dict(),
            "suggested": {
                "id": resolved_id,
                "name": manifest.name or resolved_id,
                "description": manifest.description,
                "domain": manifest.domain or "external",
                "url": resolved_url,
                "run_path": manifest.run_path,
                "health_path": manifest.health_path,
            },
        }
    except Exception:
        # An inspection that blew up leaves no scratch clone behind.
        clone.discard(token)
        raise


def register(
    token: str,
    *,
    repo_url: str,
    agent_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    domain: Optional[str] = None,
    url: Optional[str] = None,
    branch: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Promote an inspected clone and add the agent to the registry.

    The agent is registered whether or not it passed its required checks; the
    report travels with it under ``remote.readiness`` so the agent list can show
    what is still missing. Raises only for problems that make registration
    itself impossible — an unusable id, or a collision with a different agent.
    """
    agent_id = (agent_id or "").strip()
    conflict = _conflict_for(agent_id, repo_url)
    id_check = checks.check_agent_id(agent_id, conflict=conflict)
    if not id_check.ok:
        raise ImportError_(f"{id_check.detail}. {id_check.fix}".strip())

    repo_dir = clone.promote(token, agent_id)
    manifest = parse_manifest(repo_dir)
    commit = clone.head_commit(repo_dir)
    resolved_url = normalize_base_url((url or "").strip() or manifest.url)

    descriptor = _descriptor(
        manifest, url=resolved_url, repo_url=repo_url,
        branch=branch or "", commit=commit, clone_path=str(repo_dir),
    )
    report = checks.evaluate(
        repo_dir, manifest,
        agent_id=agent_id, url=resolved_url, commit=commit,
        workspace=workspace, remote=descriptor,
    )
    descriptor["readiness"] = report.to_dict()

    instructions, capabilities, usage = _definition_markdown(manifest, descriptor)
    prompt_assembly.write_instructions(agent_id, instructions)
    prompt_assembly.write_capabilities(agent_id, capabilities)
    prompt_assembly.write_usage(agent_id, usage)

    owner_workspace = (workspace or "").strip() or None
    if owner_workspace == "default":
        owner_workspace = None

    spec = registry.AgentSpec(
        id=agent_id,
        name=(name or "").strip() or manifest.name or agent_id,
        type="remote",
        entrypoint=REMOTE_ENTRYPOINT,
        description=(description if description is not None else manifest.description) or "",
        domain=(domain or "").strip() or manifest.domain or "external",
        # A remote agent brings its own tools; the hub grants it none, so the
        # capability guard has nothing to weigh here.
        tools=[],
        owner_workspace=owner_workspace,
        remote=descriptor,
    )
    registry.add_agent(spec)

    if owner_workspace:
        _add_to_workspace(owner_workspace, agent_id)

    return {"agent": spec.to_dict(), "report": report.to_dict(), "runnable": report.runnable}


def _add_to_workspace(workspace: str, agent_id: str) -> None:
    """Make a workspace-owned import visible in that workspace immediately."""
    try:
        from workspace import create_workspace_folder, get_workspace_metadata, update_workspace_metadata
        create_workspace_folder(workspace)
        metadata = get_workspace_metadata(workspace)
        allowed = list(metadata.get("allowed_agents") or [])
        if agent_id not in allowed:
            allowed.append(agent_id)
            update_workspace_metadata(workspace, {"allowed_agents": allowed})
    except Exception:
        # Best effort: the agent itself is registered and owner_workspace is set.
        pass


def recheck(agent_id: str, *, url: Optional[str] = None, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Re-run the readiness checks for an imported agent and store the result.

    ``url`` optionally updates the endpoint at the same time, which is the usual
    reason to re-check: the operator has just started the service somewhere.
    """
    spec = registry.get_agent(agent_id)
    if spec is None:
        raise ImportError_(f"Agent '{agent_id}' not found")
    if not spec.is_remote():
        raise ImportError_(f"Agent '{agent_id}' is not an imported agent")

    descriptor = dict(spec.remote or {})
    if url is not None:
        descriptor["url"] = normalize_base_url(url)

    repo_dir = Path(descriptor.get("clone_path") or "")
    manifest = parse_manifest(repo_dir) if repo_dir.is_dir() else AgentManifest(found=False)
    # A clone that has been deleted from disk should not silently downgrade the
    # record's manifest data — fall back to what was captured at import time.
    if not manifest.found and isinstance(descriptor.get("manifest"), dict):
        stored = descriptor["manifest"]
        manifest.run_path = stored.get("run_path") or DEFAULT_RUN_PATH
        manifest.health_path = stored.get("health_path") or DEFAULT_HEALTH_PATH

    report = checks.evaluate(
        repo_dir, manifest,
        agent_id=agent_id,
        url=descriptor.get("url") or "",
        commit=descriptor.get("commit") or "",
        workspace=workspace,
        remote=descriptor,
    )
    descriptor["readiness"] = report.to_dict()

    # The generated definition quotes the endpoint, so a re-check that moved it
    # would otherwise leave the agent's own page describing the old address.
    if descriptor.get("url") != (spec.remote or {}).get("url"):
        instructions, capabilities, usage = _definition_markdown(manifest, descriptor)
        prompt_assembly.write_instructions(agent_id, instructions)
        prompt_assembly.write_capabilities(agent_id, capabilities)
        prompt_assembly.write_usage(agent_id, usage)

    import dataclasses
    registry.add_agent(dataclasses.replace(spec, remote=descriptor))
    return {"report": report.to_dict(), "runnable": report.runnable, "url": descriptor.get("url") or ""}


def cleanup(agent_id: str) -> bool:
    """Remove the stored clone for an imported agent. Called on agent deletion."""
    return clone.remove_clone(agent_id)


__all__ = ["inspect", "register", "recheck", "cleanup", "ImportError_", "REMOTE_ENTRYPOINT"]
