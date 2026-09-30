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

A source may also be an **A2A agent card URL** rather than a repository. There
is nothing to clone then: the card is the declaration, so ``inspect`` and
``register`` branch into the card path (see :mod:`agents.importer.a2a_import`)
and rejoin the same readiness, documentation and registration steps.

The resulting registry record is a normal ``AgentSpec`` with ``type="remote"``,
so every existing surface — the agent list, chat, tasks, flows — reaches it
through the unchanged ``create_agent`` path (see :mod:`agents.remote_agent`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from agents import prompt_assembly, registry
from agents.importer import a2a_import, checks, clone
from agents.importer.manifest import AgentManifest, parse_manifest
from agents.remote_agent import DEFAULT_HEALTH_PATH, DEFAULT_RUN_PATH, RemoteAgent, normalize_base_url

# ``entrypoint`` is required on every registry record and must be an importable
# "module:attr". For remote agents it documents the adapter that will run them
# rather than a factory the loader calls — agent_factory branches on
# ``type == "remote"`` before it ever resolves an entrypoint.
REMOTE_ENTRYPOINT = "agents.remote_agent:RemoteAgent"

# Where the bundled examples live. Presets are one of them, addressed by a
# short id rather than a path so the frontend never has to know the
# repository's own layout.
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "imported-agents"

# preset id -> example directory name. Kept as a small literal map (not
# discovered from the filesystem) so an example can exist without being
# offered as a one-click preset, and so the ids here are the ones the
# frontend and the docs commit to rather than whatever a directory happens to
# be named.
PRESETS: Dict[str, str] = {
    "claude-code": "claude-code-agenthub",
    "codex": "codex-agenthub",
}


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
    if manifest.runtime_kind == "a2a":
        # An A2A agent has one endpoint and no paths: every call is a JSON-RPC
        # method on the same URL. ``kind`` is what RemoteAgent branches on, and
        # ``streaming`` is copied off the card so a run does not have to fetch
        # the card again to find out whether message/stream is worth trying.
        from a2a.card import card_streaming

        a2a_descriptor: Dict[str, Any] = {
            "kind": "a2a",
            "url": normalize_base_url(url),
            "run_path": "",
            "health_path": "",
            "card_url": manifest.card_url,
            "streaming": card_streaming(manifest.card),
            "card": dict(manifest.card),
            "repo_url": repo_url,
            "branch": branch or "",
            "commit": commit or "",
            "clone_path": clone_path,
            "manifest": manifest.to_dict(),
        }
        if manifest.timeout:
            a2a_descriptor["timeout"] = manifest.timeout
        if manifest.auth_token_env:
            a2a_descriptor["auth_token_env"] = manifest.auth_token_env
        if manifest.auth_header:
            a2a_descriptor["auth_header"] = manifest.auth_header
        return a2a_descriptor

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
    # Same opt-in rule: only an agent that says it can describe its own shape is
    # ever asked for it.
    if manifest.graph_path:
        descriptor["graph_path"] = manifest.graph_path
    # Same opt-in rule again: an agent that cannot be continued simply never
    # declares this, and the hub never tries.
    if manifest.resume_path:
        descriptor["resume_path"] = manifest.resume_path
    return descriptor


def refresh_topology(agent_id: str, descriptor: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch the remote's graph and store it on the descriptor, in place.

    Called at registration and at every re-check, which are the two moments the
    operator has just changed something about the service. Not called per run:
    a graph's shape changes when its repository is redeployed, not between
    requests, and fetching it on the hot path would add a round trip to work
    that is already waiting on a model.

    A failure is recorded rather than raised. The panel then shows why there is
    no picture, which is more useful than an agent that refuses to import
    because its topology endpoint was briefly down.
    """
    if not descriptor.get("graph_path") or not descriptor.get("url"):
        return descriptor
    agent = RemoteAgent(agent_id, agent_id, descriptor)
    topology = agent.fetch_topology()
    topology["fetched_at"] = _now_iso()
    descriptor["topology"] = topology
    return descriptor


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _a2a_definition_markdown(manifest: AgentManifest, descriptor: Dict[str, Any]) -> tuple[str, str, str]:
    """The same three files, for an agent reached over the A2A protocol.

    Written separately rather than with conditionals inside the HTTP version
    because almost every line differs: there is no repository, no commit, no
    run path and no health endpoint, just one JSON-RPC URL and a card.
    """
    name = manifest.name or manifest.id
    endpoint = descriptor.get("url") or "<not configured>"
    card_url = descriptor.get("card_url") or "not recorded"
    skills = "\n".join(f"- {t}" for t in manifest.tools) or "- none declared"
    streaming = (
        "message/stream, the card declares capabilities.streaming"
        if descriptor.get("streaming")
        else "not declared by the card, runs use message/send"
    )
    protocol = manifest.card.get("protocolVersion") or "not declared"

    instructions = f"""# {name} (A2A agent)

This agent runs **outside** this service and speaks the Agent2Agent protocol.
The hub is its client: it sends one JSON-RPC `message/send` per run and reads
the resulting Task back. Its behaviour, prompt and tools live wherever it is
deployed, and this hub never sees them.

- Endpoint: `POST {endpoint}` (JSON-RPC 2.0)
- Agent card: {card_url}
- Streaming: {streaming}
- Resume: a paused task is continued with `message/send` carrying its `taskId`

{manifest.description}

Editing this file changes only the hub-side documentation. To change what this
agent does, change the service that serves its card.
"""

    capabilities = f"""Declared by the agent's own A2A card.

Skills (the agent performs them itself; this hub grants it no tools):
{skills}

Protocol version: {protocol}
"""

    usage = """Send this agent a prompt exactly as you would any other agent, from Chat, a
task, or a flow node.

What is different:

- Every run is one A2A task on the remote side. When the agent pauses and asks
  something, the run is parked as `awaiting_input` and the answer is sent back
  as a second message on the same task, so the agent continues rather than
  starting over.
- Token and cost accounting depend on the agent reporting usage, and A2A has no
  field for it. Runs of this agent therefore record a duration and an output
  but no tokens.
- The hub supplies no tools. Anything this agent can do, it does on its own side.
"""
    return instructions, capabilities, usage


def _definition_markdown(manifest: AgentManifest, descriptor: Dict[str, Any]) -> tuple[str, str, str]:
    """Generate the definition markdown shown on the agent's page.

    A remote agent's real prompt lives in its own repository and the hub never
    sees it. Writing a definition anyway keeps every existing surface working
    (the agent page, the marketplace, prompt assembly) and turns that folder
    into the place an operator reads to find out what this agent is and how it
    is wired.
    """
    if descriptor.get("kind") == "a2a":
        return _a2a_definition_markdown(manifest, descriptor)

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
- Graph: {f"`GET {descriptor.get('url') or '<not configured>'}{descriptor['graph_path']}`" if descriptor.get('graph_path') else 'not declared'}
- Resume: {f"`POST {descriptor.get('url') or '<not configured>'}{descriptor['resume_path']}`" if descriptor.get('resume_path') else 'not declared'}

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


def list_presets() -> List[Dict[str, Any]]:
    """Bundled examples ready to import as a one-click preset.

    Reads each preset's own manifest rather than duplicating what it declares
    here, so this list can never drift from what the example actually needs:
    an operator adding ``CLAUDE_MODEL`` to the Claude Code example's manifest
    sees it here on the next request, with no second place to update.
    """
    out: List[Dict[str, Any]] = []
    for preset_id, dirname in PRESETS.items():
        example_dir = EXAMPLES_DIR / dirname
        manifest = parse_manifest(example_dir) if example_dir.is_dir() else AgentManifest(found=False)
        out.append({
            "id": preset_id,
            "agent_id": manifest.id or preset_id,
            "name": manifest.name or preset_id,
            "description": manifest.description,
            # False when the example directory is missing from this install
            # (a stripped-down deployment) or its manifest failed to parse:
            # the button should be disabled rather than offer an import that
            # can only fail.
            "available": example_dir.is_dir() and manifest.found and not manifest.problems,
            "docker": bool(manifest.dockerfile),
            "env": [e.to_dict() for e in manifest.env],
        })
    return out


def _inspect_preset(
    preset: str,
    *,
    agent_id: Optional[str] = None,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage a bundled example and report whether it can run here.

    Same shape as inspecting a repository, so the import dialog needs no
    second code path: ``repo_url`` in the result is a ``preset:<id>``
    provenance marker rather than a URL, which is what a re-import (and the id
    collision check) compares against on a later import of the same preset.
    """
    dirname = PRESETS.get(preset)
    if not dirname:
        raise ImportError_(f"Unknown preset '{preset}'. Known presets: {', '.join(PRESETS) or 'none'}.")
    example_dir = EXAMPLES_DIR / dirname
    if not example_dir.is_dir():
        raise ImportError_(
            f"The bundled example for preset '{preset}' is missing from this install ({example_dir})."
        )

    provenance = f"preset:{preset}"
    token, repo_dir = clone.stage_preset(example_dir)
    try:
        manifest = parse_manifest(repo_dir)
        resolved_id = (agent_id or "").strip() or manifest.id
        resolved_url = normalize_base_url((url or "").strip() or manifest.url)

        descriptor = _descriptor(
            manifest, url=resolved_url, repo_url=provenance,
            branch="", commit="", clone_path="",
        )
        report = checks.evaluate(
            repo_dir, manifest,
            agent_id=resolved_id,
            url=resolved_url,
            workspace=workspace,
            id_conflict=_conflict_for(resolved_id, provenance) if resolved_id else None,
            remote=descriptor,
        )
        return {
            "token": token,
            "repo_url": provenance,
            "preset": preset,
            "branch": "",
            "commit": "",
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
        clone.discard(token)
        raise


def inspect(
    repo_url: str = "",
    *,
    branch: Optional[str] = None,
    agent_id: Optional[str] = None,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
    preset: Optional[str] = None,
) -> Dict[str, Any]:
    """Clone and analyse a repository without registering anything.

    Returns a dict with the staging ``token`` (pass it to :func:`register`), the
    parsed manifest, the readiness report, and the pre-filled field values the
    import dialog should show.

    ``repo_url`` may also be an A2A agent card URL. There is nothing to clone
    then: the card is the declaration, and the flow continues on the same
    manifest/report/suggested shape so the dialog needs no second code path.

    ``preset`` selects a bundled example (see :data:`PRESETS`) instead of a
    repository or a card; ``repo_url`` is ignored when it is given.
    """
    if preset:
        return _inspect_preset(preset, agent_id=agent_id, url=url, workspace=workspace)

    if a2a_import.is_card_url(repo_url):
        return _inspect_card(repo_url, agent_id=agent_id, url=url, workspace=workspace)

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

    if a2a_import.is_card_url(repo_url):
        return _register_card(
            repo_url, agent_id=agent_id, name=name, description=description,
            domain=domain, url=url, workspace=workspace,
        )

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
    return _persist(
        agent_id, manifest, descriptor, report,
        name=name, description=description, domain=domain, workspace=workspace,
    )


def _persist(
    agent_id: str,
    manifest: AgentManifest,
    descriptor: Dict[str, Any],
    report: "checks.ReadinessReport",
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    domain: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the readiness report, the documentation and the registry record.

    Shared by both import sources: a repository and an A2A card differ in how
    the manifest is obtained and in nothing after that, and one registration
    path is what keeps an imported agent identical to every surface downstream.
    """
    descriptor["readiness"] = report.to_dict()
    refresh_topology(agent_id, descriptor)

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


# ── the A2A source ──────────────────────────────────────────────────────────

def _card_manifest(card_url: str, agent_id: Optional[str]) -> AgentManifest:
    """Fetch and parse the card, turning a failure into an operator-facing error."""
    try:
        manifest, _card = a2a_import.load_manifest(card_url, agent_id=agent_id)
    except a2a_import.CardError as exc:
        raise ImportError_(str(exc))
    return manifest


def _inspect_card(
    card_url: str,
    *,
    agent_id: Optional[str] = None,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Read an A2A agent card and report whether the agent can be used here.

    Returns the same shape as a repository inspection, with an empty ``token``:
    nothing was staged, so there is nothing to promote or discard later.
    """
    from a2a.card import describe_card

    manifest = _card_manifest(card_url, agent_id)
    resolved_id = (agent_id or "").strip() or manifest.id
    resolved_url = normalize_base_url((url or "").strip() or manifest.url)

    descriptor = _descriptor(
        manifest, url=resolved_url, repo_url=card_url,
        branch="", commit="", clone_path="",
    )
    report = checks.evaluate(
        Path(card_url), manifest,
        agent_id=resolved_id,
        url=resolved_url,
        workspace=workspace,
        id_conflict=_conflict_for(resolved_id, card_url) if resolved_id else None,
        remote=descriptor,
    )
    return {
        "token": "",
        "repo_url": card_url,
        "branch": "",
        "commit": "",
        "manifest": manifest.to_dict(),
        "report": report.to_dict(),
        "card": describe_card(manifest.card, card_url=card_url),
        "suggested": {
            "id": resolved_id,
            "name": manifest.name or resolved_id,
            "description": manifest.description,
            "domain": manifest.domain or "external",
            "url": resolved_url,
            "run_path": "",
            "health_path": "",
        },
    }


def _register_card(
    card_url: str,
    *,
    agent_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    domain: Optional[str] = None,
    url: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Register an agent from its A2A card. The card is re-read, deliberately:
    the operator may have fixed something between inspecting and importing, and
    a card is one cheap GET."""
    manifest = _card_manifest(card_url, agent_id)
    resolved_url = normalize_base_url((url or "").strip() or manifest.url)
    descriptor = _descriptor(
        manifest, url=resolved_url, repo_url=card_url,
        branch="", commit="", clone_path="",
    )
    report = checks.evaluate(
        Path(card_url), manifest,
        agent_id=agent_id, url=resolved_url, workspace=workspace, remote=descriptor,
    )
    return _persist(
        agent_id, manifest, descriptor, report,
        name=name, description=description, domain=domain, workspace=workspace,
    )


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

    if descriptor.get("kind") == "a2a":
        return _recheck_a2a(agent_id, spec, descriptor, workspace=workspace)

    repo_dir = Path(descriptor.get("clone_path") or "")
    manifest = parse_manifest(repo_dir) if repo_dir.is_dir() else AgentManifest(found=False)
    # A clone that has been deleted from disk should not silently downgrade the
    # record's manifest data — fall back to what was captured at import time.
    if not manifest.found and isinstance(descriptor.get("manifest"), dict):
        stored = descriptor["manifest"]
        manifest.run_path = stored.get("run_path") or DEFAULT_RUN_PATH
        manifest.health_path = stored.get("health_path") or DEFAULT_HEALTH_PATH
        manifest.graph_path = stored.get("graph_path") or ""

    report = checks.evaluate(
        repo_dir, manifest,
        agent_id=agent_id,
        url=descriptor.get("url") or "",
        commit=descriptor.get("commit") or "",
        workspace=workspace,
        remote=descriptor,
    )
    descriptor["readiness"] = report.to_dict()
    # A re-check is usually "I have just started the service", which is exactly
    # when the graph became fetchable for the first time.
    if manifest.graph_path and not descriptor.get("graph_path"):
        descriptor["graph_path"] = manifest.graph_path
    refresh_topology(agent_id, descriptor)

    # The generated definition quotes the endpoint, so a re-check that moved it
    # would otherwise leave the agent's own page describing the old address.
    if descriptor.get("url") != (spec.remote or {}).get("url"):
        instructions, capabilities, usage = _definition_markdown(manifest, descriptor)
        prompt_assembly.write_instructions(agent_id, instructions)
        prompt_assembly.write_capabilities(agent_id, capabilities)
        prompt_assembly.write_usage(agent_id, usage)

    import dataclasses
    registry.add_agent(dataclasses.replace(spec, remote=descriptor))
    return {
        "report": report.to_dict(),
        "runnable": report.runnable,
        "url": descriptor.get("url") or "",
        "topology": descriptor.get("topology"),
    }


def _recheck_a2a(
    agent_id: str,
    spec: Any,
    descriptor: Dict[str, Any],
    *,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-read the card of an imported A2A agent and refresh what it declares.

    A re-check is normally "I have just started the service", and for an A2A
    agent the card is where every answer to that lives: whether it is up, where
    its endpoint is now, and whether it streams. The stored copy is replaced
    when the fetch succeeds and kept when it fails, so a brief outage costs a
    failed check rather than the agent's configuration.
    """
    import dataclasses

    stored = descriptor.get("manifest") if isinstance(descriptor.get("manifest"), dict) else {}
    card_url = descriptor.get("card_url") or stored.get("card_url") or ""
    manifest: Optional[AgentManifest] = None
    if card_url:
        try:
            manifest, _card = a2a_import.load_manifest(card_url, agent_id=agent_id)
        except a2a_import.CardError:
            manifest = None
    if manifest is None:
        manifest = a2a_import.manifest_from_card(
            descriptor.get("card") or stored.get("card") or {},
            card_url=card_url,
            agent_id=agent_id,
        )
    else:
        from a2a.card import card_streaming

        descriptor["card"] = dict(manifest.card)
        descriptor["streaming"] = card_streaming(manifest.card)
        descriptor["manifest"] = manifest.to_dict()
        # An agent that moved is followed, unless the operator pinned the URL
        # by hand in this very call: their value is the more recent statement.
        if manifest.url and not descriptor.get("url"):
            descriptor["url"] = normalize_base_url(manifest.url)

    report = checks.evaluate(
        Path(card_url), manifest,
        agent_id=agent_id,
        url=descriptor.get("url") or "",
        workspace=workspace,
        remote=descriptor,
    )
    descriptor["readiness"] = report.to_dict()

    if descriptor.get("url") != (spec.remote or {}).get("url"):
        instructions, capabilities, usage = _definition_markdown(manifest, descriptor)
        prompt_assembly.write_instructions(agent_id, instructions)
        prompt_assembly.write_capabilities(agent_id, capabilities)
        prompt_assembly.write_usage(agent_id, usage)

    registry.add_agent(dataclasses.replace(spec, remote=descriptor))
    return {
        "report": report.to_dict(),
        "runnable": report.runnable,
        "url": descriptor.get("url") or "",
        "topology": descriptor.get("topology"),
    }


def cleanup(agent_id: str) -> bool:
    """Remove the stored clone for an imported agent. Called on agent deletion."""
    return clone.remove_clone(agent_id)


__all__ = ["inspect", "register", "recheck", "cleanup", "list_presets", "PRESETS",
           "ImportError_", "REMOTE_ENTRYPOINT"]
