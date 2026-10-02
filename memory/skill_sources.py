"""
Skill sources: public repositories of Agent Skills a workspace can connect.

A source is an ordinary project with a cloned repository (projects/,
``Project.repo``), nothing more: once it is cloned, the skill sync
(memory/skill_import.py) walks it like any project repository, every skill it
finds becomes a catalog entry with its review (memory/skill_review.py), and
**Pull** on the project brings new versions in. What this module adds is the
shortcut from a URL to that state, and a curated list of sources whose
license was checked by hand, so the Skills page can offer them in one click.

The list holds what was verified when it was written (October 2026): the
publisher, the license of the repository and where its skills live. A skill
inside such a repository may still carry its own license (anthropics/skills
ships four document skills as source-available next to Apache 2.0 ones); the
review reads the ``license`` field of every SKILL.md, so those are marked and
cannot be published to the global catalog whatever the repository says.

Not on the list, on purpose: install-count leaderboards and open marketplaces
(skills.sh, ClawHub) where Snyk's ToxicSkills audit found the malicious
skills, and link indexes such as VoltAgent/awesome-agent-skills, which hold
no skills themselves. Any URL can still be connected by hand; it goes through
the same review.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Verified public sources. ``license`` is the repository's; ``note`` says
#: what to expect inside. ``repo`` is ``owner/name`` on github.com.
CURATED_SOURCES: List[Dict[str, Any]] = [
    {
        "id": "anthropics-skills", "repo": "anthropics/skills",
        "publisher": "Anthropic", "license": "Apache-2.0", "kind": "vendor",
        "note": "The reference collection and the Agent Skills spec. docx, pdf, pptx and "
                "xlsx are source-available, not open; the review marks them.",
    },
    {
        "id": "getsentry-skills", "repo": "getsentry/skills",
        "publisher": "Sentry", "license": "Apache-2.0", "kind": "vendor",
        "note": "Engineering workflow skills: commit, code review, PR writing, security review.",
    },
    {
        "id": "huggingface-skills", "repo": "huggingface/skills",
        "publisher": "Hugging Face", "license": "Apache-2.0", "kind": "vendor",
        "note": "Hub, datasets, training and deployment skills.",
    },
    {
        "id": "microsoft-skills", "repo": "microsoft/skills",
        "publisher": "Microsoft", "license": "MIT", "kind": "vendor",
        "note": "Azure SDK skills per language, under .github/plugins/<bundle>/skills.",
    },
    {
        "id": "trailofbits-skills", "repo": "trailofbits/skills",
        "publisher": "Trail of Bits", "license": "CC-BY-SA-4.0", "kind": "vendor",
        "note": "Security review skills, one plugin folder per skill.",
    },
    {
        "id": "expo-skills", "repo": "expo/skills",
        "publisher": "Expo", "license": "MIT", "kind": "vendor",
        "note": "React Native and Expo development skills.",
    },
    {
        "id": "cloudflare-security-audit", "repo": "cloudflare/security-audit-skill",
        "publisher": "Cloudflare", "license": "MIT", "kind": "vendor",
        "note": "One skill at the root of its repository: a security audit procedure.",
    },
    {
        "id": "seb1n-awesome-ai-agent-skills", "repo": "seb1n/awesome-ai-agent-skills",
        "publisher": "community (seb1n)", "license": "MIT", "kind": "community",
        "note": "About a hundred self-contained skills by category, no outbound links.",
    },
]

_HOST_TYPES = {"github.com": "github", "gitlab.com": "gitlab", "bitbucket.org": "bitbucket"}
_SHORTHAND = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


class SourceError(ValueError):
    """A URL that cannot be a skill source, or a source that cannot be added."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def normalize_url(url: str) -> Dict[str, str]:
    """``{"url", "type", "owner", "name"}`` for an https URL on a known host,
    or ``owner/name`` shorthand for github.com. Anything else (ssh, file,
    an unknown host, a URL that is not a repository) is refused: a source
    is public text, and the clone must not reach into the hub's network."""
    raw = (url or "").strip()
    if _SHORTHAND.match(raw):
        raw = f"https://github.com/{raw}"
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SourceError(400, "A skill source is an https URL of a public repository")
    host = parsed.hostname or ""
    repo_type = _HOST_TYPES.get(host.lower())
    if repo_type is None or parsed.username or parsed.password or parsed.port:
        raise SourceError(400, "A skill source lives on github.com, gitlab.com or bitbucket.org")
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise SourceError(400, "The URL names no repository (expected owner/name)")
    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name or owner.startswith(".") or name.startswith("."):
        raise SourceError(400, "The URL names no repository (expected owner/name)")
    return {"url": f"https://{host.lower()}/{owner}/{name}", "type": repo_type,
            "owner": owner, "name": name}


def _same_repo(url_a: Optional[str], url_b: str) -> bool:
    if not url_a:
        return False
    try:
        return normalize_url(url_a)["url"].lower() == url_b.lower()
    except SourceError:
        return False


def _projects(workspace: str) -> List[Any]:
    from projects.storage import ProjectStore
    return [p for p in ProjectStore().list() if p.workspace == workspace]


def _skill_counts(workspace: str) -> Dict[str, Dict[str, int]]:
    """Per project id: how many catalog entries came from it, how many are
    flagged medium or high, how many carry a license that is not open."""
    from memory.procedural import ProcedureStore
    out: Dict[str, Dict[str, int]] = {}
    for p in ProcedureStore(workspace).load():
        if p.source != "repo" or p.agent_id:
            continue
        pid = (p.repo or {}).get("project_id")
        if not pid:
            continue
        row = out.setdefault(pid, {"skills": 0, "flagged": 0, "not_open": 0, "scripts": 0})
        row["skills"] += 1
        safety = p.safety or {}
        if safety.get("severity") in ("medium", "high"):
            row["flagged"] += 1
        if safety.get("license_open") is False:
            row["not_open"] += 1
        if safety.get("scripts"):
            row["scripts"] += 1
    return out


def list_sources(workspace: str) -> List[Dict[str, Any]]:
    """The curated list, each entry saying whether this workspace already has
    it (``project_id``) and what the sync made of it, followed by the
    workspace's other repository projects tagged ``skills`` (sources added by
    URL), so the page shows every source in one list."""
    projects = _projects(workspace)
    counts = _skill_counts(workspace)
    items: List[Dict[str, Any]] = []
    listed_ids = set()
    for src in CURATED_SOURCES:
        url = f"https://github.com/{src['repo']}"
        match = next((p for p in projects if _same_repo(p.repo.url, url)), None)
        row = {**src, "url": url, "project_id": match.id if match else None,
               "project_name": match.name if match else None,
               **(counts.get(match.id, {}) if match else {})}
        if match:
            listed_ids.add(match.id)
        items.append(row)
    for p in projects:
        if p.id in listed_ids or not p.repo.url or "skills" not in (p.tags or []):
            continue
        try:
            info = normalize_url(p.repo.url)
        except SourceError:
            continue
        items.append({"id": f"project-{p.id}", "repo": f"{info['owner']}/{info['name']}",
                      "publisher": info["owner"], "license": "", "kind": "custom",
                      "note": "", "url": info["url"], "project_id": p.id,
                      "project_name": p.name, **counts.get(p.id, {})})
    return items


def add_source(workspace: str, url: str, *, branch: Optional[str] = None,
               name: Optional[str] = None) -> Dict[str, Any]:
    """Connect a repository of skills to ``workspace``: a project with the
    repository cloned under its folder, then a sync of that project. Returns
    ``{"project": {...}, "sync": <report>, "already_present": bool}``. A
    repository the workspace already has is synced again, not cloned twice.
    Blocking (a clone); callers run it in a thread."""
    from connectors.git import git_ops
    from memory.skill_import import sync_workspace
    from projects.models import Project, RepoConfig
    from projects.storage import ProjectStore
    from workspace import get_workspace_folder
    from workspace.storage import project_folder_name, resolve_project_root

    info = normalize_url(url)
    ws_folder = get_workspace_folder(workspace)
    if ws_folder is None:
        raise SourceError(404, f"Workspace {workspace!r} not found")

    existing = next((p for p in _projects(workspace) if _same_repo(p.repo.url, info["url"])), None)
    if existing is not None:
        report = sync_workspace(workspace, project_id=existing.id)
        return {"project": _project_row(existing), "sync": report, "already_present": True}

    project_name = (name or "").strip() or f"{info['name']} ({info['owner']})"
    folder = project_folder_name(project_name)
    if any(project_folder_name(p.name) == folder for p in _projects(workspace)):
        raise SourceError(409, f"A project named {project_name!r} already exists in this workspace")
    local_path = f"{folder}/repo"
    clone_dir: Path = ws_folder / local_path
    if clone_dir.exists():
        raise SourceError(409, f"Target directory already exists: {clone_dir}")

    resolve_project_root(workspace, folder)
    try:
        git_ops.clone(info["url"], clone_dir, branch=(branch or "").strip() or None,
                      provider=info["type"])
    except git_ops.GitOpsError as e:
        raise SourceError(504 if "timed out" in str(e) else 502, f"Clone failed: {e}")

    project = Project(
        name=project_name,
        description=f"Agent Skills from {info['owner']}/{info['name']}",
        type="code",
        workspace=workspace,
        tags=["skills"],
        repo=RepoConfig(type=info["type"], url=info["url"],
                        branch=(branch or "").strip() or None, local_path=local_path,
                        remote_id=f"{info['owner']}/{info['name']}"),
    )
    ProjectStore().add(project)
    report = sync_workspace(workspace, project_id=project.id)
    return {"project": _project_row(project), "sync": report, "already_present": False}


def _project_row(project: Any) -> Dict[str, Any]:
    return {"id": project.id, "name": project.name, "workspace": project.workspace,
            "url": project.repo.url, "branch": project.repo.branch,
            "local_path": project.repo.local_path}


__all__ = ["CURATED_SOURCES", "SourceError", "add_source", "list_sources", "normalize_url"]
