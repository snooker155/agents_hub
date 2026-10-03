"""
Skill sources: public repositories of Agent Skills a workspace can connect.

A source is a clone of the repository inside the workspace folder, under
``.skills/sources/<id>``, with a small ``<id>.json`` next to it naming the
URL and branch. It is not a project: a collection of skills is something the
Skills page reads from, not work the team does, so it gets no tasks, files
tab or board. The skill sync (memory/skill_import.py) walks each clone as a
root of its own, every skill it finds becomes a catalog entry with its review
(memory/skill_review.py) and ``repo.source_id`` naming the source, and
**Update** on the Skills page pulls and syncs again. The folder is hidden, so
the workspace's file index and the Artifacts page leave it out.

What this module adds on top of the clone is a curated list of sources whose
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

import json
import logging
import re
import shutil
from datetime import datetime, timezone
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


#: Where the clones live, relative to the workspace folder. Hidden, so the
#: file index (files/service.py) and the agents' file listings skip it.
SOURCES_DIR = Path(".skills") / "sources"
_ID_UNSAFE = re.compile(r"[^a-z0-9_.-]+")


def source_id_for(info: Dict[str, str]) -> str:
    """A folder-safe id for a normalized URL: ``owner-name`` on github.com,
    prefixed with the host type elsewhere so two hosts never collide."""
    base = f"{info['owner']}-{info['name']}".lower()
    if info.get("type") != "github":
        base = f"{info['type']}-{base}"
    return _ID_UNSAFE.sub("-", base).strip("-.") or "source"


def sources_dir(workspace: str) -> Optional[Path]:
    from workspace import get_workspace_folder

    ws_folder = get_workspace_folder(workspace)
    return None if ws_folder is None else ws_folder / SOURCES_DIR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_meta(base: Path, meta: Dict[str, Any]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{meta['id']}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def connected_sources(workspace: str) -> List[Dict[str, Any]]:
    """The sources a workspace has: one dict per ``<id>.json`` whose clone
    is on disk, with ``path`` (absolute) added. Sorted by id."""
    base = sources_dir(workspace)
    if base is None or not base.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for meta_file in sorted(base.glob("*.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("skill source: unreadable %s", meta_file)
            continue
        if not isinstance(meta, dict) or meta.get("id") != meta_file.stem:
            continue
        clone = base / meta_file.stem
        if not clone.is_dir() or clone.is_symlink():
            continue
        out.append({**meta, "path": str(clone)})
    return out


def get_source(workspace: str, source_id: str) -> Dict[str, Any]:
    match = next((s for s in connected_sources(workspace) if s["id"] == source_id), None)
    if match is None:
        raise SourceError(404, f"Skill source {source_id!r} is not connected to this workspace")
    return match


def source_label(meta: Dict[str, Any]) -> str:
    return f"{meta.get('owner', '')}/{meta.get('name', '')}".strip("/") or meta.get("id", "")


def _skill_counts(workspace: str) -> Dict[str, Dict[str, int]]:
    """Per source id: how many catalog entries came from it, how many are
    flagged medium or high, how many carry a license that is not open, how
    many ship files an agent could run."""
    from memory.procedural import ProcedureStore
    out: Dict[str, Dict[str, int]] = {}
    for p in ProcedureStore(workspace).load():
        if p.source != "repo" or p.agent_id:
            continue
        repo = p.repo or {}
        sid = repo.get("source_id")
        if not sid or repo.get("missing"):
            continue
        row = out.setdefault(sid, {"skills": 0, "flagged": 0, "not_open": 0, "scripts": 0})
        row["skills"] += 1
        safety = p.safety or {}
        if safety.get("severity") in ("medium", "high"):
            row["flagged"] += 1
        if safety.get("license_open") is False:
            row["not_open"] += 1
        if safety.get("scripts"):
            row["scripts"] += 1
    return out


def _connection(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if meta is None:
        return {"source_id": None}
    return {"source_id": meta["id"], "branch": meta.get("branch"),
            "added_at": meta.get("added_at"), "updated_at": meta.get("updated_at")}


def list_sources(workspace: str) -> List[Dict[str, Any]]:
    """The curated list, each entry saying whether this workspace already has
    it (``source_id``) and what the sync made of it, followed by the sources
    added by URL, so the page shows every source in one list."""
    migrate_project_sources(workspace)
    connected = connected_sources(workspace)
    counts = _skill_counts(workspace)
    items: List[Dict[str, Any]] = []
    listed = set()
    for src in CURATED_SOURCES:
        url = f"https://github.com/{src['repo']}"
        match = next((m for m in connected if _same_repo(m.get("url"), url)), None)
        if match:
            listed.add(match["id"])
        items.append({**src, "url": url, **_connection(match),
                      **(counts.get(match["id"], {}) if match else {})})
    for meta in connected:
        if meta["id"] in listed:
            continue
        items.append({"id": f"source-{meta['id']}", "repo": source_label(meta),
                      "publisher": meta.get("owner", ""), "license": "", "kind": "custom",
                      "note": "", "url": meta.get("url", ""), **_connection(meta),
                      **counts.get(meta["id"], {})})
    return items


def add_source(workspace: str, url: str, *, branch: Optional[str] = None) -> Dict[str, Any]:
    """Connect a repository of skills to ``workspace``: clone it under
    ``.skills/sources/<id>``, then sync it. Returns ``{"source": {...},
    "sync": <report>, "already_present": bool}``. A repository the workspace
    already has is updated (pulled and synced), not cloned twice. Blocking
    (a clone); callers run it in a thread."""
    from connectors.git import git_ops

    info = normalize_url(url)
    base = sources_dir(workspace)
    if base is None:
        raise SourceError(404, f"Workspace {workspace!r} not found")
    migrate_project_sources(workspace)

    existing = next((m for m in connected_sources(workspace) if _same_repo(m.get("url"), info["url"])), None)
    if existing is not None:
        result = update_source(workspace, existing["id"])
        return {**result, "already_present": True}

    sid = source_id_for(info)
    clone_dir = base / sid
    if clone_dir.exists():
        # A folder left behind by a clone that failed half way, or a json
        # deleted by hand: nothing references it, so it is cleared.
        shutil.rmtree(clone_dir, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    wanted = (branch or "").strip() or None
    try:
        git_ops.clone(info["url"], clone_dir, branch=wanted, provider=info["type"])
    except git_ops.GitOpsError as e:
        shutil.rmtree(clone_dir, ignore_errors=True)
        raise SourceError(504 if "timed out" in str(e) else 502, f"Clone failed: {e}")

    now = _now()
    meta = {"id": sid, "url": info["url"], "type": info["type"], "owner": info["owner"],
            "name": info["name"], "branch": wanted, "added_at": now, "updated_at": now}
    _write_meta(base, meta)
    report = _sync(workspace, sid)
    return {"source": _source_row(meta), "sync": report, "already_present": False}


def update_source(workspace: str, source_id: str) -> Dict[str, Any]:
    """Pull the clone of a connected source and sync its skills. Blocking."""
    from connectors.git import git_ops

    meta = get_source(workspace, source_id)
    try:
        git_ops.pull(Path(meta["path"]), provider=meta.get("type"))
    except git_ops.GitOpsError as e:
        raise SourceError(504 if "timed out" in str(e) else 502, f"Pull failed: {e}")
    meta = {k: v for k, v in meta.items() if k != "path"}
    meta["updated_at"] = _now()
    _write_meta(Path(sources_dir(workspace)), meta)
    return {"source": _source_row(meta), "sync": _sync(workspace, source_id)}


def remove_source(workspace: str, source_id: str) -> Dict[str, Any]:
    """Disconnect a source: delete its clone and the catalog entries that came
    from it. Copies already attached to agents keep their own text (they are
    separate records), so no agent loses a skill it was given."""
    from memory.procedural import ProcedureStore

    meta = get_source(workspace, source_id)
    base = Path(sources_dir(workspace))
    shutil.rmtree(meta["path"], ignore_errors=True)
    (base / f"{source_id}.json").unlink(missing_ok=True)
    store = ProcedureStore(workspace)
    removed: List[Dict[str, Any]] = []
    for p in store.load():
        if p.source != "repo" or p.agent_id or (p.repo or {}).get("source_id") != source_id:
            continue
        if store.delete(p.id):
            removed.append({"id": str(p.id), "name": p.name})
    return {"source_id": source_id, "removed": removed}


def _sync(workspace: str, source_id: str) -> Dict[str, Any]:
    from memory.skill_import import sync_workspace
    return sync_workspace(workspace, source_id=source_id)


def _source_row(meta: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": meta["id"], "repo": source_label(meta), "url": meta.get("url"),
            "branch": meta.get("branch"), "path": (SOURCES_DIR / meta["id"]).as_posix(),
            "added_at": meta.get("added_at"), "updated_at": meta.get("updated_at")}


# ── sources made as projects by an earlier build ─────────────────────────────

def migrate_project_sources(workspace: str) -> List[str]:
    """Move the sources an earlier build made as projects (tagged ``skills``,
    described "Agent Skills from owner/name", clone under ``<folder>/repo``)
    to ``.skills/sources``: the clone is moved, the catalog entries and their
    attached copies are pointed at the new folder (same ids, so agents keep
    their skills and history), and the project is deleted. Idempotent;
    returns the ids of the sources moved."""
    from memory.procedural import ProcedureStore
    from projects.storage import ProjectStore
    from workspace import get_workspace_folder

    ws_folder = get_workspace_folder(workspace)
    if ws_folder is None:
        return []
    try:
        store = ProjectStore()
        candidates = [p for p in store.list() if p.workspace == workspace
                      and "skills" in (p.tags or []) and p.repo and p.repo.url
                      and (p.description or "").startswith("Agent Skills from ")]
    except Exception:  # noqa: BLE001 - a broken project store must not hide the sources page
        log.warning("skill sources: could not list projects of %s", workspace, exc_info=True)
        return []
    moved: List[str] = []
    base = ws_folder / SOURCES_DIR
    for project in candidates:
        try:
            info = normalize_url(project.repo.url)
        except SourceError:
            continue
        old_rel = (project.repo.local_path or "").strip("/")
        old_dir = ws_folder / old_rel if old_rel else None
        if old_dir is None or not old_dir.is_dir():
            continue
        sid = source_id_for(info)
        new_dir = base / sid
        base.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)
        else:
            shutil.move(str(old_dir), str(new_dir))
        new_rel = (SOURCES_DIR / sid).as_posix()
        meta = {"id": sid, "url": info["url"], "type": info["type"], "owner": info["owner"],
                "name": info["name"], "branch": project.repo.branch,
                "added_at": project.created_at.isoformat() if getattr(project, "created_at", None) else _now(),
                "updated_at": _now()}
        _write_meta(base, meta)

        procedures = ProcedureStore(workspace)
        for proc in procedures.load():
            repo = dict(proc.repo or {})
            rel = str(repo.get("dir") or "")
            if repo.get("project_id") != project.id and not (rel == old_rel or rel.startswith(old_rel + "/")):
                continue
            if rel == old_rel or rel.startswith(old_rel + "/"):
                repo["dir"] = new_rel + rel[len(old_rel):]
            repo["project_id"] = None
            repo["source_id"] = sid
            repo["root"] = source_label(meta)
            proc.repo = repo
            procedures.update(proc)

        store.delete(project.id)
        try:
            from projects.graph_store import ProjectGraphStore
            ProjectGraphStore().delete_project(project.id)
        except Exception:  # noqa: BLE001 - saved diagram views are cosmetic
            log.debug("skill sources: graph cleanup failed for %s", project.id, exc_info=True)
        project_dir = old_dir.parent
        if project_dir != ws_folder and project_dir.is_dir() and not any(project_dir.iterdir()):
            project_dir.rmdir()
        moved.append(sid)
        log.info("skill sources: moved project %s to %s", project.id, new_rel)
    return moved


__all__ = ["CURATED_SOURCES", "SOURCES_DIR", "SourceError", "add_source", "connected_sources",
           "get_source", "list_sources", "migrate_project_sources", "normalize_url",
           "remove_source", "source_id_for", "source_label", "update_source"]
