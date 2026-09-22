"""
Workspace management utilities shared across the project.

Handles creation and management of per-task workspaces inside the shared
`.agents_hub/workspaces` directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import re
import uuid
import json
import shutil

from filelock import FileLock

from common.paths import (
    WORKSPACES_ROOT as DEFAULT_WORKSPACES_ROOT,
    WORKSPACES_META_FILE,
    ensure_workspaces_root,
    ensure_agents_hub_root,
)

# Default workspaces root under the shared .agents_hub state directory
WORKSPACES_ROOT = DEFAULT_WORKSPACES_ROOT

# All workspace metadata lives in a single JSON file keyed by workspace name
# (see WORKSPACES_META_FILE). A file lock guards the read-modify-write so the
# dashboard, node workers and agent subprocesses can update it concurrently.
_WORKSPACES_META_LOCK = str(WORKSPACES_META_FILE) + ".lock"
_migration_done = False


def _load_all_metadata() -> Dict[str, Dict[str, Any]]:
    """Load the central name -> metadata map (empty dict when absent/corrupt)."""
    if not WORKSPACES_META_FILE.exists():
        return {}
    try:
        txt = WORKSPACES_META_FILE.read_text(encoding="utf-8")
        data = json.loads(txt) if txt.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all_metadata(all_meta: Dict[str, Dict[str, Any]]) -> None:
    """Atomically write the central metadata map."""
    ensure_agents_hub_root()
    tmp = WORKSPACES_META_FILE.with_suffix(WORKSPACES_META_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(all_meta, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(WORKSPACES_META_FILE)


def _migrate_legacy_metadata() -> None:
    """One-time merge of per-folder .workspace.json files into the central store.

    Runs at most once per process. For every workspace folder that still has a
    legacy ``.workspace.json``, its contents are folded into the central map
    (only when the name is not already present, so the central copy wins) and the
    per-folder file is then removed. Cheap no-op once migration has happened.
    """
    global _migration_done
    if _migration_done:
        return
    with FileLock(_WORKSPACES_META_LOCK):
        all_meta = _load_all_metadata()
        changed = False
        if WORKSPACES_ROOT.exists():
            for folder in WORKSPACES_ROOT.iterdir():
                if not folder.is_dir():
                    continue
                legacy = folder / ".workspace.json"
                if not legacy.exists():
                    continue
                name = folder.name
                if name not in all_meta:
                    try:
                        raw = json.loads(legacy.read_text(encoding="utf-8"))
                    except Exception:
                        raw = None
                    if isinstance(raw, dict):
                        all_meta[name] = _normalize_workspace_metadata(raw)
                        changed = True
                # The central store is now authoritative — drop the per-folder file.
                try:
                    legacy.unlink()
                except Exception:
                    pass
        if changed:
            _save_all_metadata(all_meta)
    _migration_done = True

# Reserved system folder inside each workspace where planning-capable agents
# persist their execution plans (and their step statuses) across runs. Scoped
# at the workspace level — shared across projects and agents — alongside the
# .logs/ and knowledge/ folders.
PLANS_DIRNAME = ".plans"

# Fallback set, used only when the registry cannot be read. The real list is
# computed from the registry: every agent marked `system` (see
# agents.registry.system_agent_ids). Keeping a literal here means a corrupt
# agents.json degrades to the historical five rather than stripping every
# workspace of its system agents at once.
SYSTEM_AGENT_IDS: tuple[str, ...] = (
    "orchestrator", "agent_creator", "decomposer", "flow_creator", "memory_extractor",
)

# The agent a freshly created workspace pre-selects in the Chat page. Applied
# only when it is actually registered, so an install without it falls back to
# the old behaviour (Chat picks the first available agent).
DEFAULT_CHAT_AGENT_ID = "main-agent"


def system_agent_ids() -> tuple[str, ...]:
    """Ids every workspace must include, in registry order.

    An agent is either system or custom, with nothing in between. System agents
    are the product's own: they are added to a workspace's ``allowed_agents``
    on creation, backfilled into existing workspaces on read, and cannot be
    removed from one. Custom agents are the operator's, and are added per
    workspace by hand.
    """
    try:
        from agents.registry import system_agent_ids as _registry_ids
        ids = tuple(_registry_ids())
    except Exception:
        ids = ()
    return ids or SYSTEM_AGENT_IDS


def _default_chat_agent_id() -> str | None:
    """The chat default to seed, or None when it is not registered.

    Guarded by a registry lookup so a workspace never stores a dangling id: the
    Chat page treats an unknown default as "no default" anyway, but persisting
    one would mislead anyone reading workspaces.json.
    """
    try:
        from agents.registry import get_agent
        return DEFAULT_CHAT_AGENT_ID if get_agent(DEFAULT_CHAT_AGENT_ID) else None
    except Exception:
        return None


def is_system_agent(agent_id: str) -> bool:
    """Return True if the agent is shipped by the product, not the operator."""
    return agent_id in system_agent_ids()


def ensure_workspaces_dir() -> Path:
    """Ensure the workspaces root directory exists."""
    return ensure_workspaces_root()


class InvalidWorkspaceName(ValueError):
    """A caller-supplied workspace name that is not a single path component."""


def is_valid_workspace_name(name: str) -> bool:
    """True when ``name`` is a single ordinary path component.

    No "." or "..", no separators, no absolute path: ``WORKSPACES_ROOT / name``
    must land on a direct child of the workspaces root.
    """
    if not name or name in (".", ".."):
        return False
    candidate = Path(name)
    return candidate.name == name and not candidate.is_absolute()


def create_workspace_folder(name: Optional[str] = None) -> Path:
    """
    Create a new workspace folder in the workspaces directory and initialize metadata.

    Args:
        name: Optional name for the workspace folder. If not provided,
              generates a unique name using UUID.

    Returns:
        Path to the created workspace folder (absolute).
    """
    ensure_workspaces_dir()

    if name is None:
        name = str(uuid.uuid4())[:8]

    # Every caller passes a bare workspace name, and several of them take it
    # straight from a request. The mkdir below is unconditional, so a name
    # like "../../etc" would create (and later serve from) a directory
    # outside the workspaces root. Refuse anything that is not one ordinary
    # path component here, once, rather than at each of the thirty callers.
    if not is_valid_workspace_name(name):
        raise InvalidWorkspaceName(f"Invalid workspace name: {name!r}")

    workspace_path = WORKSPACES_ROOT / name
    workspace_path.mkdir(parents=True, exist_ok=True)

    _seed_workspace_metadata(name)

    return workspace_path.resolve()


def _seed_workspace_metadata(name: str, attached_path: Optional[str] = None) -> None:
    """Initialize central metadata for a workspace that has no entry yet.

    Fast path: skip the lock entirely when the entry already exists (the common
    case, since this runs on most requests); only lock to seed a new entry.
    ``attached_path`` records where an attached workspace points, so the API can
    report it without a filesystem round trip.
    """
    from common.identity import claim_workspace, current_user_id

    _migrate_legacy_metadata()
    if name in _load_all_metadata() and attached_path is None:
        return
    seeded = False
    with FileLock(_WORKSPACES_META_LOCK):
        all_meta = _load_all_metadata()
        if name not in all_meta:
            all_meta[name] = {
                "name": name,
                "created_at": str(uuid.uuid4()),  # Placeholder for actual time if needed
                # Who created it. ``local`` outside AUTH_MODE=multi, where there
                # is exactly one operator and an owner would be a fiction; a
                # user id under multi, where it decides who may configure or
                # delete the workspace. See common/identity.py.
                "owner": current_user_id(),
                "allowed_agents": list(system_agent_ids()),
                "env_vars": {},
                "settings": {},
            }
            default_chat = _default_chat_agent_id()
            if default_chat:
                all_meta[name]["default_chat_agent"] = default_chat
            seeded = True
        if attached_path is not None:
            all_meta[name]["attached_path"] = attached_path
        _save_all_metadata(all_meta)
    # Outside the file lock: the membership row is a database write, and the
    # creator has to become an ``owner`` member or they could not reach the
    # workspace they just made. A no-op in every mode but multi.
    if seeded:
        try:
            claim_workspace(name)
        except Exception:
            # A workspace that exists but has no membership row is recoverable
            # (an admin can add one); a create that fails because of it is not.
            pass


def attach_workspace_folder(target: Path | str, name: Optional[str] = None) -> Path:
    """Register a directory that lives outside the state root as a workspace.

    The workspace entry is a symlink at ``WORKSPACES_ROOT/<name>`` pointing at
    ``target``; nothing is copied, and the directory keeps its own identity (a
    git repo stays a git repo). Everything downstream — the filesystem tools,
    the files API, project git-status, run cwd resolution — goes through the OS,
    which follows the link, so no other code has to know.

    The workspace name is forced to the target's own folder name. Several
    callers derive the workspace name from ``create_workspace_folder(x).name``,
    which resolves the link, so a name that differed from the target's basename
    would silently file work under the wrong workspace.

    Raises ValueError when the target is missing, is not a directory, already
    lies inside the state root, or the name is taken by something else.
    """
    ensure_workspaces_dir()

    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        raise ValueError(f"No such directory: {target_path}")
    if not target_path.is_dir():
        raise ValueError(f"Not a directory: {target_path}")

    root = WORKSPACES_ROOT.resolve()
    if target_path == root or root in target_path.parents:
        raise ValueError(
            f"{target_path} is already inside the workspaces root — create it as a "
            "plain workspace instead of attaching it"
        )

    derived = target_path.name
    if name and name != derived:
        raise ValueError(
            f"An attached workspace takes its name from the folder, so this one must be "
            f"'{derived}', not '{name}'. Rename the folder, or attach it as a project "
            f"inside a workspace instead."
        )
    name = derived

    link = WORKSPACES_ROOT / name
    if link.is_symlink():
        existing = link.resolve()
        if existing == target_path:
            _seed_workspace_metadata(name, attached_path=str(target_path))
            return link
        raise ValueError(
            f"Workspace '{name}' is already attached to {existing}"
        )
    if link.exists():
        raise ValueError(f"Workspace '{name}' already exists as a real directory")

    link.symlink_to(target_path, target_is_directory=True)
    _seed_workspace_metadata(name, attached_path=str(target_path))
    return link


def is_attached_workspace(name: str) -> bool:
    """True when this workspace is a link to a directory outside the state root."""
    return (WORKSPACES_ROOT / name).is_symlink()


def workspace_target(name: str) -> Optional[Path]:
    """The real directory behind a workspace — the link target when attached."""
    link = WORKSPACES_ROOT / name
    if not link.exists():
        return None
    return link.resolve()


def _normalize_workspace_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy workspace metadata keys to the current schema."""
    if not isinstance(meta, dict):
        return {}

    normalized = dict(meta)

    # Copy settings so in-place edits below don't mutate the caller's dict — the
    # persisted-vs-loaded comparison in get_workspace_metadata relies on
    # ``normalized != raw`` to detect changes and write them back.
    settings = normalized.get("settings")
    settings = dict(settings) if isinstance(settings, dict) else {}

    legacy_settings = normalized.pop("settings_overrides", None)
    if isinstance(legacy_settings, dict):
        settings = {**legacy_settings, **settings}

    legacy_agent_mode = normalized.pop("agent_mode", None)
    if legacy_agent_mode is not None and "agent_mode" not in settings:
        settings["agent_mode"] = legacy_agent_mode

    # Model selection (which provider/model a workspace uses) is now configured
    # globally on the Models page, with per-workspace overrides handled via the
    # header model picker (stored in top-level ``model_override``). The old
    # per-workspace default-model fields are obsolete: strip them so the picker
    # doesn't show a stale "workspace default" alongside the global default
    # (e.g. LM Studio appearing twice). API keys, base URLs, temperature, etc.
    # remain valid workspace overrides and are left untouched.
    normalized.pop("default_model", None)
    normalized.pop("default_provider", None)
    for _legacy_model_key in (
        "default_model", "default_provider",
        "model", "anthropic_model", "google_model", "ollama_model", "lmstudio_model",
    ):
        settings.pop(_legacy_model_key, None)

    normalized["settings"] = settings

    # Backfill system agents: every workspace must include all of them, so the
    # product works end to end without the operator adding anything.
    # Copy the list so the persisted-vs-loaded comparison in get_workspace_metadata
    # can detect the change and write it back.
    allowed = normalized.get("allowed_agents")
    if isinstance(allowed, list):
        allowed = list(allowed)
        for sys_id in system_agent_ids():
            if sys_id not in allowed:
                allowed.append(sys_id)
        normalized["allowed_agents"] = allowed

    # Backfill the chat default for workspaces created before it was seeded.
    # Only fills a missing value — an explicit choice is never overwritten.
    if not normalized.get("default_chat_agent"):
        default_chat = _default_chat_agent_id()
        if default_chat:
            normalized["default_chat_agent"] = default_chat

    return normalized


def get_workspace_settings_meta(meta: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return normalized workspace settings from metadata."""
    if not isinstance(meta, dict):
        return {}
    settings = meta.get("settings")
    return settings if isinstance(settings, dict) else {}


def get_workspace_default_model_config(meta: Dict[str, Any] | None) -> Dict[str, str]:
    """Resolve the workspace's default model (the per-workspace default tier).

    Priority:
    0. top-level ``model_default`` ({provider, model}) — current storage, set from
       the Models page and robust against the Settings page replacing ``settings``.
    1. legacy ``settings.default_model.{provider,model}``
    2. legacy ``settings.default_provider`` + that provider's model field
    """
    if isinstance(meta, dict):
        md = meta.get("model_default")
        if isinstance(md, dict):
            provider = str(md.get("provider") or "").strip()
            model = str(md.get("model") or "").strip()
            if provider or model:
                return {"provider": provider, "model": model}

    settings = get_workspace_settings_meta(meta)
    default_model = settings.get("default_model")
    if isinstance(default_model, dict):
        provider = str(default_model.get("provider") or "").strip()
        model = str(default_model.get("model") or "").strip()
        if provider or model:
            return {"provider": provider, "model": model}

    provider = str(settings.get("default_provider") or "").strip()
    if not provider or provider == "global":
        return {"provider": "", "model": ""}

    provider_model_map = {
        "openai": "model",
        "anthropic": "anthropic_model",
        "google": "google_model",
        "ollama": "ollama_model",
        "lmstudio": "lmstudio_model",
    }
    model_field = provider_model_map.get(provider, "model")
    model = str(settings.get(model_field) or "").strip()
    return {"provider": provider, "model": model}


def get_workspace_metadata(name: str) -> Dict[str, Any]:
    """Get metadata for a workspace from the central store."""
    _migrate_legacy_metadata()
    raw = _load_all_metadata().get(name)
    if not isinstance(raw, dict):
        return {}
    normalized = _normalize_workspace_metadata(raw)
    if normalized != raw:
        with FileLock(_WORKSPACES_META_LOCK):
            cur = _load_all_metadata()
            cur[name] = normalized
            _save_all_metadata(cur)
    return normalized


def update_workspace_metadata(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update metadata for a workspace in the central store.

    Raises FileNotFoundError when the workspace folder does not exist — callers
    must create the workspace explicitly (e.g. via create_workspace_folder)
    before updating its metadata.
    """
    if not get_workspace_folder(name):
        raise FileNotFoundError(f"Workspace '{name}' does not exist")
    _migrate_legacy_metadata()
    with FileLock(_WORKSPACES_META_LOCK):
        all_meta = _load_all_metadata()
        meta = all_meta.get(name)
        if not isinstance(meta, dict):
            meta = {}
        meta.update(updates)
        meta = _normalize_workspace_metadata(meta)
        all_meta[name] = meta
        _save_all_metadata(all_meta)
    return meta


def get_workspace_folder(name: str) -> Optional[Path]:
    """
    Get the path to an existing workspace folder.

    Args:
        name: Name of the workspace folder.

    Returns:
        Path to the workspace folder if it exists, None otherwise.
    """
    if not is_valid_workspace_name(name):
        return None
    workspace_path = WORKSPACES_ROOT / name
    if workspace_path.exists() and workspace_path.is_dir():
        return workspace_path.resolve()
    return None


def list_workspace_folders() -> list[Path]:
    """
    List all existing workspace folders.

    Returns:
        List of absolute paths to all workspace folders.
    """
    ensure_workspaces_dir()
    return [p for p in WORKSPACES_ROOT.iterdir() if p.is_dir()]


def delete_workspace_folder(name: str) -> bool:
    """Delete an existing workspace folder recursively and its central metadata.

    An *attached* workspace is only detached: the link is removed and the
    directory it pointed at is left completely alone. That directory belongs to
    the user and may be a whole git checkout, so it is never a candidate for
    deletion from here. (``shutil.rmtree`` refuses to act through a symlink
    anyway, so without this branch detaching would just raise.)

    Returns True when the folder existed and was removed, otherwise False.
    """
    workspace_path = WORKSPACES_ROOT / name
    if workspace_path.is_symlink():
        workspace_path.unlink()
        existed = True
    else:
        existed = workspace_path.exists() and workspace_path.is_dir()
        if existed:
            shutil.rmtree(workspace_path)
    # Drop the central metadata entry regardless, so a recreated workspace of the
    # same name starts clean rather than inheriting stale settings.
    with FileLock(_WORKSPACES_META_LOCK):
        all_meta = _load_all_metadata()
        if name in all_meta:
            del all_meta[name]
            _save_all_metadata(all_meta)
    return existed


def project_folder_name(name: str) -> str:
    """Convert a human-readable project name to a filesystem-safe folder name.

    Examples: "My Cool Project" -> "My_Cool_Project", "test-app" -> "test-app"
    """
    slug = re.sub(r"[^\w\s.\-]", "", name.strip())
    slug = re.sub(r"[\s]+", "_", slug)
    return slug or "project"


def resolve_project_root(workspace_name: str, project_name: Optional[str] = None) -> Path:
    """Return the directory agents should operate in.

    Structure:
      .agents_hub/workspaces/{workspace_name}/                  <- .logs/, .plans/, knowledge/ live here (metadata is in workspaces.json)
      .agents_hub/workspaces/{workspace_name}/{project_name}/   <- agents read/write here when project is set

    Creates the project subfolder if it does not exist.
    Falls back to the workspace root when project_name is empty/None.
    """
    workspace = create_workspace_folder(workspace_name)
    if not project_name:
        return workspace
    project_root = workspace / project_name
    project_root.mkdir(parents=True, exist_ok=True)
    return project_root.resolve()


def resolve_task_project_name(task: Any, params: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve the project folder name for a task, or None when it has no project.

    Cascade: explicit ``task.project`` / ``params["project"]`` first, otherwise
    derive the folder name from the linked Project record via ``task.project_id``.
    Shared by the subprocess and flow launchers so both place a task's runs in the
    same project subfolder under its workspace.
    """
    project_name = getattr(task, "project", None) or (params or {}).get("project") or None
    if project_name:
        return str(project_name)
    project_id = getattr(task, "project_id", None)
    if project_id:
        try:
            from common.paths import PROJECTS_FILE
            from projects.storage import ProjectStore
            proj = ProjectStore(path=PROJECTS_FILE).get(str(project_id))
            if proj:
                return project_folder_name(proj.name)
        except Exception:
            pass
    return None


def resolve_workspace_arg(arg: Optional[str]) -> tuple[str, str]:
    """Interpret a ``--workspace`` CLI/launcher argument.

    Returns ``(ws_path, ws_name)``:

    - ``ws_path`` — the absolute directory the agent operates in.
    - ``ws_name`` — the bare workspace name (never a project), used for
      task/agent/shell scoping (e.g. via ``AGENT_WORKSPACE``).

    The argument may already be an absolute project path
    (``.agents_hub/workspaces/<ws>/<project>/``, as launchers pass it) — in
    which case it is used verbatim and is NOT re-resolved into a project
    subfolder — or a bare workspace name from the CLI, which resolves to
    ``WORKSPACES_ROOT/<name>``. With no argument, both fall back to the
    workspaces root. ``ws_name`` is always the argument's basename so a project
    path does not leak the project folder into the workspace name.
    """
    if arg and Path(arg).is_absolute():
        ws_path = arg
    elif arg:
        ws_path = str((WORKSPACES_ROOT / Path(arg).name).resolve())
    else:
        ws_path = str(WORKSPACES_ROOT.resolve())
    ws_name = Path(arg).name if arg else WORKSPACES_ROOT.name
    return ws_path, ws_name


def as_param_dict(params: Any) -> Dict[str, Any]:
    """Coerce a launcher ``params`` value into a plain dict.

    Pydantic models are truthy, so ``params or {}`` wouldn't fall back to ``{}``;
    this normalises models / None / odd inputs to a real dict so downstream
    ``.get()`` calls are always safe.
    """
    if isinstance(params, dict):
        return params
    if params is None:
        return {}
    try:
        return dict(params) if hasattr(params, "__iter__") else vars(params)
    except Exception:
        return {}


def resolve_task_workspace(task: Any, params: Any = None) -> tuple[str, Path]:
    """Resolve where an agent run for ``task`` should operate.

    Returns ``(ws_name, ws_path)`` where ``ws_name`` is the workspace name
    (from ``params['workspace']`` or ``task.workspace``, possibly empty) and
    ``ws_path`` is the directory the agent runs in: the task's project subfolder
    when it has one, otherwise the workspace root, falling back to the current
    working directory when no workspace is set. Shared by the subprocess and
    flow launchers so both place a task's runs in the same directory.
    """
    params = as_param_dict(params)
    ws_name = params.get("workspace") or getattr(task, "workspace", None) or ""
    if not ws_name:
        return "", Path.cwd()
    ws_path = (WORKSPACES_ROOT / ws_name).resolve()
    project_name = resolve_task_project_name(task, params)
    if project_name:
        ws_path = resolve_project_root(ws_name, project_name)
    return ws_name, ws_path


# -------------------- plan storage --------------------

_VALID_STEP_STATUSES = ("todo", "in_progress", "done", "blocked", "skipped")
_VALID_PLAN_STATUSES = ("active", "completed", "abandoned")

# Checkbox marker rendered per step status in the Markdown body. The status is
# also written explicitly in a trailing tag so parsing is exact (not inferred
# from the box), but the box keeps the file glanceable.
_STEP_BOX = {
    "todo": " ",
    "in_progress": "~",
    "done": "x",
    "blocked": "!",
    "skipped": "-",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Plans are persisted as human-readable Markdown: a YAML-style frontmatter block
# carries the metadata and a checklist body carries the steps. The in-memory
# record dict shape is unchanged, so callers (and the plan tools) are unaffected.

def _md_escape(value: str) -> str:
    """Flatten a scalar to a single safe frontmatter line."""
    return str(value or "").replace("\n", " ").strip()


def _serialize_plan_md(record: Dict[str, Any]) -> str:
    """Render a plan record as Markdown (frontmatter + checklist body)."""
    lines: List[str] = ["---"]
    for key in ("id", "title", "description", "status", "agent_id", "project",
                "created_at", "updated_at"):
        val = record.get(key)
        lines.append(f"{key}: {_md_escape('' if val is None else val)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {_md_escape(record.get('title') or record.get('id') or 'Plan')}")
    desc = _md_escape(record.get("description"))
    if desc:
        lines.append("")
        lines.append(desc)
    lines.append("")
    lines.append("## Steps")
    lines.append("")
    for step in record.get("steps", []) or []:
        status = str(step.get("status") or "todo")
        box = _STEP_BOX.get(status, " ")
        text = _md_escape(step.get("text"))
        sid = step.get("id")
        line = f"- [{box}] {text} <!-- id:{sid} status:{status} -->"
        lines.append(line)
        notes = _md_escape(step.get("notes"))
        if notes:
            lines.append(f"  - notes: {notes}")
    lines.append("")
    return "\n".join(lines)


_STEP_RE = re.compile(
    r"^- \[.\] (?P<text>.*?)\s*<!-- id:(?P<id>\d+) status:(?P<status>[a-z_]+) -->\s*$"
)
_NOTES_RE = re.compile(r"^\s+- notes:\s*(?P<notes>.*)$")


def _parse_plan_md(text: str) -> Dict[str, Any]:
    """Parse a plan Markdown document back into a record dict."""
    record: Dict[str, Any] = {}
    steps: List[Dict[str, Any]] = []
    lines = text.splitlines()

    # Frontmatter between the first pair of '---' fences.
    i = 0
    if i < len(lines) and lines[i].strip() == "---":
        i += 1
        while i < len(lines) and lines[i].strip() != "---":
            raw = lines[i]
            if ":" in raw:
                key, _, val = raw.partition(":")
                record[key.strip()] = val.strip()
            i += 1
        i += 1  # skip closing fence

    # Body: collect checklist steps; attach notes to the preceding step.
    for raw in lines[i:]:
        m = _STEP_RE.match(raw)
        if m:
            steps.append({
                "id": int(m.group("id")),
                "text": m.group("text").strip(),
                "status": m.group("status"),
                "notes": "",
            })
            continue
        n = _NOTES_RE.match(raw)
        if n and steps:
            steps[-1]["notes"] = n.group("notes").strip()

    record["steps"] = steps
    record.setdefault("description", "")
    # Normalize empty-string metadata that maps to None in the JSON shape.
    for key in ("agent_id", "project"):
        if record.get(key) in ("", None):
            record[key] = None
    return record


def _slugify_plan_id(value: str) -> str:
    """Turn a plan title/id into a filesystem-safe, collision-resistant slug."""
    slug = re.sub(r"[^\w\s.\-]", "", str(value).strip().lower())
    slug = re.sub(r"[\s]+", "-", slug).strip("-.")
    return slug or "plan"


def ensure_plans_dir(workspace_name: str) -> Path:
    """Ensure (and return) the ``.plans`` folder for a workspace.

    Creates the workspace folder too when it does not exist yet.
    """
    workspace = create_workspace_folder(workspace_name)
    plans_dir = workspace / PLANS_DIRNAME
    plans_dir.mkdir(parents=True, exist_ok=True)
    return plans_dir


def _plan_path(workspace_name: str, plan_id: str) -> Path:
    return ensure_plans_dir(workspace_name) / f"{plan_id}.md"


def _coerce_steps(steps: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Normalize a list of plan steps into dicts with id/text/status/notes."""
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(steps or [], start=1):
        if isinstance(raw, dict):
            text = str(raw.get("text") or raw.get("title") or "").strip()
            status = str(raw.get("status") or "todo").strip()
            notes = str(raw.get("notes") or "").strip()
        else:
            text = str(raw).strip()
            status = "todo"
            notes = ""
        if status not in _VALID_STEP_STATUSES:
            status = "todo"
        out.append({"id": i, "text": text, "status": status, "notes": notes})
    return out


def create_plan(
    workspace_name: str,
    title: str,
    *,
    plan_id: Optional[str] = None,
    description: str = "",
    steps: Optional[List[Any]] = None,
    agent_id: Optional[str] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """Create and persist a new plan in the workspace ``.plans`` folder.

    Returns the stored plan record. Raises ``ValueError`` if a plan with the
    resolved id already exists, so callers don't silently overwrite.
    """
    pid = _slugify_plan_id(plan_id or title)
    path = _plan_path(workspace_name, pid)
    if path.exists():
        raise ValueError(f"Plan '{pid}' already exists in workspace '{workspace_name}'")
    now = _now_iso()
    record: Dict[str, Any] = {
        "id": pid,
        "title": str(title).strip(),
        "description": str(description or "").strip(),
        "status": "active",
        "steps": _coerce_steps(steps),
        "agent_id": agent_id,
        "project": project,
        "created_at": now,
        "updated_at": now,
    }
    path.write_text(_serialize_plan_md(record), encoding="utf-8")
    return record


def get_plan(workspace_name: str, plan_id: str) -> Optional[Dict[str, Any]]:
    """Return a stored plan record, or None when it does not exist."""
    path = _plan_path(workspace_name, _slugify_plan_id(plan_id))
    if not path.exists():
        return None
    try:
        return _parse_plan_md(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_plans(workspace_name: str) -> List[Dict[str, Any]]:
    """Return all plans stored in the workspace, most recently updated first."""
    plans_dir = ensure_plans_dir(workspace_name)
    records: List[Dict[str, Any]] = []
    for p in plans_dir.glob("*.md"):
        try:
            records.append(_parse_plan_md(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    records.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return records


def update_plan(
    workspace_name: str,
    plan_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    steps: Optional[List[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Update top-level plan fields and/or replace the full step list.

    Returns the updated record, or None when the plan does not exist.
    """
    record = get_plan(workspace_name, plan_id)
    if record is None:
        return None
    if title is not None:
        record["title"] = str(title).strip()
    if description is not None:
        record["description"] = str(description).strip()
    if status is not None:
        if status not in _VALID_PLAN_STATUSES:
            raise ValueError(
                f"Invalid plan status '{status}'. Use one of: {', '.join(_VALID_PLAN_STATUSES)}"
            )
        record["status"] = status
    if steps is not None:
        record["steps"] = _coerce_steps(steps)
    record["updated_at"] = _now_iso()
    path = _plan_path(workspace_name, record["id"])
    path.write_text(_serialize_plan_md(record), encoding="utf-8")
    return record


def update_plan_step(
    workspace_name: str,
    plan_id: str,
    step_id: int,
    *,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update the status and/or notes of a single step within a plan.

    Returns the updated record, or None when the plan/step does not exist.
    """
    record = get_plan(workspace_name, plan_id)
    if record is None:
        return None
    target = next((s for s in record.get("steps", []) if int(s.get("id", 0)) == int(step_id)), None)
    if target is None:
        return None
    if status is not None:
        if status not in _VALID_STEP_STATUSES:
            raise ValueError(
                f"Invalid step status '{status}'. Use one of: {', '.join(_VALID_STEP_STATUSES)}"
            )
        target["status"] = status
    if notes is not None:
        target["notes"] = str(notes).strip()
    record["updated_at"] = _now_iso()
    path = _plan_path(workspace_name, record["id"])
    path.write_text(_serialize_plan_md(record), encoding="utf-8")
    return record


def delete_plan(workspace_name: str, plan_id: str) -> bool:
    """Delete a stored plan. Returns True when it existed and was removed."""
    path = _plan_path(workspace_name, _slugify_plan_id(plan_id))
    if path.exists():
        path.unlink()
        return True
    return False


INSTRUCTIONS_FILENAME = "INSTRUCTIONS.md"


def get_workspace_instructions(name: str) -> str:
    """Return the workspace-level instructions markdown, or empty string if none."""
    folder = get_workspace_folder(name)
    if not folder:
        return ""
    instructions_path = folder / INSTRUCTIONS_FILENAME
    if instructions_path.exists():
        return instructions_path.read_text(encoding="utf-8")
    return ""


def set_workspace_instructions(name: str, content: str) -> None:
    """Write workspace-level instructions markdown to INSTRUCTIONS.md."""
    folder = create_workspace_folder(name)
    instructions_path = folder / INSTRUCTIONS_FILENAME
    instructions_path.write_text(content, encoding="utf-8")


def resolve_var_references(value: str, env_vars: Dict[str, str]) -> str:
    """Resolve ${VAR_NAME} references in a string using workspace env_vars."""
    def _replace(m: re.Match) -> str:
        return env_vars.get(m.group(1), m.group(0))
    return re.sub(r"\$\{([^}]+)\}", _replace, value)


def get_effective_settings(workspace_name: str) -> Dict[str, Any]:
    """Return merged settings: global .env values overridden by workspace settings.

    ${VAR_NAME} references in override values are resolved from the workspace env_vars.
    Keys use the Settings field names (e.g. 'openai_api_key', 'model').
    """
    from common.config import read_dot_env

    # Map field names -> env var names (same mapping as in routes/settings.py)
    field_to_env: Dict[str, str] = {
        "default_provider":        "DEFAULT_PROVIDER",
        "openai_api_key":          "OPENAI_API_KEY",
        "model":                   "OPENAI_MODEL",
        "anthropic_api_key":       "ANTHROPIC_API_KEY",
        "anthropic_model":         "ANTHROPIC_MODEL",
        "google_api_key":          "GOOGLE_API_KEY",
        "google_model":            "GOOGLE_MODEL",
        "openai_base_url":         "OPENAI_BASE_URL",
        "temperature":             "LLM_TEMPERATURE",
        "max_tokens":              "LLM_MAX_TOKENS",
        "ollama_base_url":         "OLLAMA_BASE_URL",
        "ollama_model":            "OLLAMA_MODEL",
        "lmstudio_base_url":       "LMSTUDIO_BASE_URL",
        "lmstudio_model":          "LMSTUDIO_MODEL",
        "langfuse_secret_key":     "LANGFUSE_SECRET_KEY",
        "langfuse_public_key":     "LANGFUSE_PUBLIC_KEY",
        "langfuse_base_url":       "LANGFUSE_BASE_URL",
        "orch_log_level":          "ORCH_LOG_LEVEL",
        "rag_vector_db":           "RAG_VECTOR_DB",
        "rag_vector_db_url":       "RAG_VECTOR_DB_URL",
        "rag_vector_db_api_key":   "RAG_VECTOR_DB_API_KEY",
        "rag_vector_db_collection":"RAG_VECTOR_DB_COLLECTION",
        "rag_embedding_provider":  "RAG_EMBEDDING_PROVIDER",
        "rag_embedding_model":     "RAG_EMBEDDING_MODEL",
        "rag_embedding_api_key":   "RAG_EMBEDDING_API_KEY",
        "rag_embedding_base_url":  "RAG_EMBEDDING_BASE_URL",
        "agent_mode":              "AGENT_EXECUTION_MODE",
        "agent_docker_image":      "AGENT_DOCKER_IMAGE",
        "agent_docker_network":    "AGENT_DOCKER_NETWORK",
        "agent_docker_extra_args": "AGENT_DOCKER_EXTRA_ARGS",
        "task_assignment_mode":    "TASK_ASSIGNMENT_MODE",
    }

    env = read_dot_env()
    effective: Dict[str, Any] = {}
    for field_name, env_key in field_to_env.items():
        if env_key in env:
            effective[field_name] = env[env_key]

    meta = get_workspace_metadata(workspace_name)
    settings = get_workspace_settings_meta(meta)
    env_vars = meta.get("env_vars", {}) if isinstance(meta, dict) else {}
    if not isinstance(env_vars, dict):
        env_vars = {}

    for key, value in settings.items():
        if isinstance(value, str):
            effective[key] = resolve_var_references(value, env_vars)
        else:
            effective[key] = value

    return effective
