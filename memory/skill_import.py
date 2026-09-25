"""
Skills from a project's repository: ``.claude/skills/<name>/SKILL.md``.

The format is the one Claude Code and the Agent Skills spec use: a folder per
skill holding ``SKILL.md`` (YAML frontmatter with ``name`` and
``description``, then Markdown instructions) and, optionally, files the
instructions refer to (scripts, templates, reference notes). A team that
already keeps its skills next to its code gets them in the hub without
retyping: :func:`sync_workspace` scans every skill root of a workspace (the
workspace folder itself, each project's cloned repository and each project
folder) and keeps one catalog entry per skill folder in step with it.

What a sync does, per skill folder found:

- a new folder becomes a catalog entry (``source="repo"``, not attached to any
  agent) at version 1;
- a changed ``SKILL.md`` or file list becomes a new version of that entry
  (``memory.skill_versions``, op ``sync``). Copies of the entry already
  attached to agents in the same workspace follow it when nobody edited the
  copy since it was taken; an edited copy keeps its own text and shows that
  an update is available, and a pinned copy keeps serving its pinned version
  either way;
- a folder that disappeared marks its entry ``repo.missing`` rather than
  deleting it, so an agent does not lose a skill because a branch was
  switched; the Skills page offers to delete it.

Only the folder of a skill is ever read, never anything above it: resources
are listed with their paths relative to the skill folder, symlinks are
skipped, and ``read_resource`` refuses a path that resolves outside.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

SKILLS_DIR = Path(".claude") / "skills"
SKILL_FILE = "SKILL.md"
#: Limits for one skill folder. A skill is instructions plus a few helper
#: files; anything bigger is a repository that happens to sit there.
MAX_SKILL_MD_BYTES = 256 * 1024
MAX_RESOURCES = 200
MAX_RESOURCE_READ_BYTES = 200 * 1024
_NAME_MAX = 120
_DESCRIPTION_MAX = 1024

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


class SkillFormatError(ValueError):
    """A SKILL.md that cannot become a skill (no frontmatter, no name...)."""


@dataclass
class ParsedSkill:
    name: str
    description: str
    body: str
    allowed_tools: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in re.split(r"[,\s]+", value) if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def parse_skill_md(text: str, *, fallback_name: str = "") -> ParsedSkill:
    """Split a SKILL.md into its frontmatter fields and its Markdown body.

    ``name`` falls back to the folder name (the spec makes the two equal) and
    ``description`` is required: it is the line the agent matches a task
    against, so a skill without one would never be picked.
    """
    import yaml

    text = (text or "").lstrip("﻿")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillFormatError("SKILL.md must start with a YAML frontmatter block (--- ... ---)")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillFormatError(f"SKILL.md frontmatter is not valid YAML: {e}") from e
    if not isinstance(meta, dict):
        raise SkillFormatError("SKILL.md frontmatter must be a mapping")
    name = str(meta.get("name") or fallback_name or "").strip()[:_NAME_MAX]
    if not name:
        raise SkillFormatError("SKILL.md has no name")
    description = " ".join(str(meta.get("description") or "").split())[:_DESCRIPTION_MAX]
    if not description:
        raise SkillFormatError(f"SKILL.md for {name!r} has no description")
    body = text[match.end():].strip()
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    return ParsedSkill(
        name=name,
        description=description,
        body=body,
        allowed_tools=_as_list(meta.get("allowed-tools") or meta.get("allowed_tools")),
        tags=_as_list(meta.get("tags") or metadata.get("tags")),
    )


def render_skill_md(procedure: Any) -> str:
    """A skill as SKILL.md text, for export. Steps become a numbered list
    after the body, so a steps-only skill exports as a readable procedure."""
    import yaml

    meta: Dict[str, Any] = {"name": procedure.name, "description": procedure.description}
    if getattr(procedure, "allowed_tools", None):
        meta["allowed-tools"] = " ".join(procedure.allowed_tools)
    if getattr(procedure, "tags", None):
        meta["metadata"] = {"tags": list(procedure.tags)}
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    parts = [f"---\n{front}\n---", ""]
    body = (getattr(procedure, "body", "") or "").strip()
    if body:
        parts.append(body)
    steps = [s for s in (getattr(procedure, "steps", None) or []) if str(s).strip()]
    if steps:
        if body:
            parts.append("")
            parts.append("## Steps")
            parts.append("")
        parts.extend(f"{i}. {s}" for i, s in enumerate(steps, start=1))
    return "\n".join(parts).rstrip() + "\n"


# ── discovery ────────────────────────────────────────────────────────────────

@dataclass
class FoundSkill:
    skill_dir: Path            # absolute
    rel_dir: str               # relative to the workspace folder, posix
    root_label: str            # which root it came from (workspace / project name)
    project_id: Optional[str]
    parsed: Optional[ParsedSkill]
    resources: List[str]
    sha256: str
    error: Optional[str] = None


def _list_resources(skill_dir: Path) -> List[str]:
    out: List[str] = []
    base = skill_dir.resolve()
    for path in sorted(skill_dir.rglob("*")):
        if len(out) >= MAX_RESOURCES:
            break
        if path.is_symlink() or not path.is_file():
            continue
        if path.name == SKILL_FILE and path.parent == skill_dir:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        try:
            path.resolve().relative_to(base)
        except ValueError:
            continue
        out.append(rel)
    return out


def _folder_hash(skill_md: bytes, resources: List[str], skill_dir: Path) -> str:
    digest = hashlib.sha256(skill_md)
    for rel in resources:
        digest.update(b"\0" + rel.encode("utf-8") + b"\0")
        try:
            stat = (skill_dir / rel).stat()
            digest.update(f"{stat.st_size}".encode())
            with open(skill_dir / rel, "rb") as fh:
                digest.update(hashlib.sha256(fh.read(4 * 1024 * 1024)).digest())
        except OSError:
            continue
    return digest.hexdigest()


def discover(root: Path, ws_folder: Path, *, label: str = "",
             project_id: Optional[str] = None) -> List[FoundSkill]:
    """Every ``.claude/skills/<name>/SKILL.md`` under ``root``."""
    skills_root = root / SKILLS_DIR
    if not skills_root.is_dir() or skills_root.is_symlink():
        return []
    found: List[FoundSkill] = []
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir() and not p.is_symlink()):
        skill_md = skill_dir / SKILL_FILE
        if not skill_md.is_file() or skill_md.is_symlink():
            continue
        try:
            rel_dir = skill_dir.resolve().relative_to(ws_folder.resolve()).as_posix()
        except ValueError:
            continue
        raw = skill_md.read_bytes()[: MAX_SKILL_MD_BYTES + 1]
        resources = _list_resources(skill_dir)
        entry = FoundSkill(skill_dir=skill_dir, rel_dir=rel_dir, root_label=label,
                           project_id=project_id, parsed=None, resources=resources,
                           sha256=_folder_hash(raw, resources, skill_dir))
        if len(raw) > MAX_SKILL_MD_BYTES:
            entry.error = f"SKILL.md is larger than {MAX_SKILL_MD_BYTES // 1024} KB"
        else:
            try:
                entry.parsed = parse_skill_md(raw.decode("utf-8", errors="replace"),
                                              fallback_name=skill_dir.name)
            except SkillFormatError as e:
                entry.error = str(e)
        found.append(entry)
    return found


def skill_roots(workspace: str) -> List[Tuple[Path, str, Optional[str]]]:
    """``(root, label, project_id)`` for each place a workspace keeps skills:
    the workspace folder, each project's cloned repository and each project
    folder. Duplicates (a repo cloned into the project folder) collapse."""
    from workspace import get_workspace_folder
    from workspace.storage import project_folder_name

    ws_folder = get_workspace_folder(workspace)
    if ws_folder is None:
        return []
    roots: List[Tuple[Path, str, Optional[str]]] = [(ws_folder, workspace, None)]
    try:
        from projects.storage import ProjectStore
        projects = [p for p in ProjectStore().list() if p.workspace == workspace]
    except Exception:  # noqa: BLE001 - a broken project store must not hide the workspace's own skills
        log.warning("skill sync: could not list projects of %s", workspace, exc_info=True)
        projects = []
    for project in projects:
        candidates = []
        if project.repo and (project.repo.url or project.repo.local_path):
            candidates.append(ws_folder / (project.repo.local_path or "repo"))
        candidates.append(ws_folder / project_folder_name(project.name))
        for candidate in candidates:
            if candidate.is_dir():
                roots.append((candidate, project.name, project.id))
    seen = set()
    unique: List[Tuple[Path, str, Optional[str]]] = []
    for root, label, pid in roots:
        key = root.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append((root, label, pid))
    return unique


# ── sync ─────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_key(procedure: Any) -> Optional[str]:
    repo = getattr(procedure, "repo", None) or {}
    return str(repo.get("dir") or "") or None


def sync_workspace(workspace: str, *, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Bring the workspace's repo skills in step with the folders on disk.

    With ``project_id`` only that project's roots are scanned, and only its
    entries can be marked missing. Returns a report: ``added``, ``updated``,
    ``unchanged``, ``missing``, ``followed`` (attached copies moved along),
    ``errors`` (folders that could not be read, with the reason).
    """
    from memory.procedural import Procedure, ProcedureStore
    from memory import skill_versions as sv
    from workspace import get_workspace_folder

    report: Dict[str, Any] = {"workspace": workspace, "added": [], "updated": [],
                              "unchanged": [], "missing": [], "followed": [], "errors": []}
    ws_folder = get_workspace_folder(workspace)
    if ws_folder is None:
        report["errors"].append({"dir": "", "error": f"workspace {workspace!r} has no folder"})
        return report
    roots = skill_roots(workspace)
    if project_id:
        roots = [r for r in roots if r[2] == project_id]

    found: Dict[str, FoundSkill] = {}
    for root, label, pid in roots:
        for entry in discover(root, ws_folder, label=label, project_id=pid):
            found.setdefault(entry.rel_dir, entry)

    store = ProcedureStore(workspace)
    existing = [p for p in store.load() if p.source == "repo" and not p.agent_id]
    by_dir = {_repo_key(p): p for p in existing if _repo_key(p)}
    scanned_projects = {pid for _root, _label, pid in roots}

    for rel_dir, entry in found.items():
        if entry.parsed is None:
            report["errors"].append({"dir": rel_dir, "error": entry.error or "unreadable"})
            continue
        repo_meta = {
            "dir": rel_dir, "project_id": entry.project_id, "root": entry.root_label,
            "sha256": entry.sha256, "synced_at": _now(), "missing": False,
        }
        current = by_dir.get(rel_dir)
        if current is None:
            procedure = Procedure(
                name=entry.parsed.name, description=entry.parsed.description,
                steps=[], body=entry.parsed.body, tags=entry.parsed.tags,
                allowed_tools=entry.parsed.allowed_tools, resources=entry.resources,
                source="repo", agent_id="", workspace=workspace, repo=repo_meta,
            )
            store.add(procedure, version_op=sv.OP_IMPORT, version_note=f"from {rel_dir}")
            report["added"].append({"id": str(procedure.id), "name": procedure.name, "dir": rel_dir})
            continue
        was_missing = bool((current.repo or {}).get("missing"))
        if (current.repo or {}).get("sha256") == entry.sha256 and not was_missing:
            report["unchanged"].append({"id": str(current.id), "name": current.name, "dir": rel_dir})
            continue
        previous_version = current.version
        current.name = entry.parsed.name
        current.description = entry.parsed.description
        current.body = entry.parsed.body
        current.tags = entry.parsed.tags
        current.allowed_tools = entry.parsed.allowed_tools
        current.resources = entry.resources
        current.repo = repo_meta
        current.touch()
        store.update(current, version_op=sv.OP_SYNC, version_note=f"from {rel_dir}")
        report["updated"].append({"id": str(current.id), "name": current.name, "dir": rel_dir,
                                  "version": current.version})
        if current.version != previous_version:
            report["followed"].extend(_follow_origin(store, current, previous_version))

    for rel_dir, procedure in by_dir.items():
        if rel_dir in found:
            continue
        repo = dict(procedure.repo or {})
        if project_id and repo.get("project_id") not in scanned_projects:
            continue
        if repo.get("missing"):
            continue
        repo["missing"] = True
        repo["synced_at"] = _now()
        procedure.repo = repo
        procedure.touch()
        store.update(procedure)
        report["missing"].append({"id": str(procedure.id), "name": procedure.name, "dir": rel_dir})
    return report


def _follow_origin(store: Any, origin: Any, previous_version: Optional[int]) -> List[Dict[str, Any]]:
    """Move the attached copies of ``origin`` in the same workspace to its new
    content, when a copy still holds exactly the version it was taken from
    (nobody edited it since). Edited copies are left alone."""
    from memory import skill_versions as sv

    moved: List[Dict[str, Any]] = []
    if not previous_version:
        return moved
    base = sv.get_version(str(origin.id), int(previous_version))
    if base is None:
        return moved
    base_hash = base["content_hash"]
    for copy in store.load():
        if copy.origin_skill_id != str(origin.id) or not copy.agent_id:
            continue
        if sv.content_hash(sv.content_of(copy)) != base_hash:
            continue
        apply_origin_content(copy, origin)
        store.update(copy, version_op=sv.OP_ORIGIN,
                     version_note=f"followed {origin.name} v{origin.version}")
        moved.append({"id": str(copy.id), "agent_id": copy.agent_id, "version": copy.version})
    return moved


def apply_origin_content(copy: Any, origin: Any) -> None:
    """Copy the content fields and the repo location of ``origin`` onto an
    installed ``copy`` and remember which origin version it now holds."""
    copy.name = origin.name
    copy.description = origin.description
    copy.steps = list(origin.steps)
    copy.body = origin.body
    copy.tags = list(origin.tags)
    copy.allowed_tools = list(origin.allowed_tools)
    copy.resources = list(origin.resources)
    copy.repo = dict(origin.repo) if origin.repo else None
    copy.origin_version = origin.version
    copy.touch()


# ── resources ────────────────────────────────────────────────────────────────

def skill_dir_for(procedure: Any) -> Optional[Path]:
    """The folder a repo skill's resources live in, or None when it has none
    on disk (a hand-written skill, a folder that disappeared)."""
    from workspace import get_workspace_folder

    repo = getattr(procedure, "repo", None) or {}
    rel = str(repo.get("dir") or "")
    if not rel or repo.get("missing"):
        return None
    ws_folder = get_workspace_folder(procedure.workspace)
    if ws_folder is None:
        return None
    path = (ws_folder / rel).resolve()
    try:
        path.relative_to(ws_folder.resolve())
    except ValueError:
        return None
    return path if path.is_dir() else None


def read_resource(procedure: Any, rel_path: str) -> str:
    """Text of one resource file of a repo skill, bounded. Raises
    ``FileNotFoundError`` or ``PermissionError`` with a readable message."""
    base = skill_dir_for(procedure)
    if base is None:
        raise FileNotFoundError(f"skill {procedure.name!r} has no files on disk")
    rel = str(rel_path or "").strip().lstrip("/")
    if not rel or rel not in set(procedure.resources or []):
        raise FileNotFoundError(
            f"{rel_path!r} is not a file of skill {procedure.name!r}; "
            f"its files: {', '.join(procedure.resources or []) or 'none'}")
    target = (base / rel)
    if target.is_symlink():
        raise PermissionError(f"{rel!r} is a symlink")
    resolved = target.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as e:
        raise PermissionError(f"{rel!r} is outside the skill folder") from e
    if not resolved.is_file():
        raise FileNotFoundError(f"{rel!r} no longer exists")
    with open(resolved, "rb") as fh:
        data = fh.read(MAX_RESOURCE_READ_BYTES + 1)
    text = data[:MAX_RESOURCE_READ_BYTES].decode("utf-8", errors="replace")
    if len(data) > MAX_RESOURCE_READ_BYTES:
        text += f"\n\n[truncated at {MAX_RESOURCE_READ_BYTES // 1024} KB]"
    return text


__all__ = [
    "FoundSkill", "ParsedSkill", "SkillFormatError", "apply_origin_content", "discover",
    "parse_skill_md", "read_resource", "render_skill_md", "skill_dir_for", "skill_roots",
    "sync_workspace",
]
