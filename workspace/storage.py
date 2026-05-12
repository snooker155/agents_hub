"""
Workspace management utilities shared across the project.

Handles creation and management of per-task workspaces inside the shared
`.agents_hub/workspaces` directory. This module keeps backward-compatible
helpers named with 'project' but exposes preferred 'workspace' helpers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any
import re
import uuid
import json
import shutil

from common.paths import WORKSPACES_ROOT as DEFAULT_WORKSPACES_ROOT, ensure_workspaces_root

# Default workspaces root under the shared .agents_hub state directory
WORKSPACES_ROOT = DEFAULT_WORKSPACES_ROOT


def ensure_workspaces_dir() -> Path:
    """Ensure the workspaces root directory exists."""
    return ensure_workspaces_root()


def create_project_folder(project_name: Optional[str] = None) -> Path:
    """
    Create a new workspace folder in the workspaces directory and initialize metadata.

    Args:
        project_name: Optional name for the workspace folder. If not provided,
                     generates a unique name using UUID.

    Returns:
        Path to the created workspace folder (absolute).
    """
    ensure_workspaces_dir()

    if project_name is None:
        project_name = str(uuid.uuid4())[:8]

    project_path = WORKSPACES_ROOT / project_name
    project_path.mkdir(parents=True, exist_ok=True)

    # Initialize workspace metadata if not exists
    meta_path = project_path / ".workspace.json"
    if not meta_path.exists():
        default_meta = {
            "name": project_name,
            "created_at": str(uuid.uuid4()),  # Placeholder for actual time if needed
            "allowed_agents": ["swe_agent", "orchestrator"],
            "env_vars": {},
            "settings": {},
        }
        meta_path.write_text(json.dumps(default_meta, indent=2))

    return project_path.resolve()


def _normalize_workspace_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy workspace metadata keys to the current schema."""
    if not isinstance(meta, dict):
        return {}

    normalized = dict(meta)

    settings = normalized.get("settings")
    if not isinstance(settings, dict):
        settings = {}

    legacy_settings = normalized.pop("settings_overrides", None)
    if isinstance(legacy_settings, dict):
        settings = {**legacy_settings, **settings}

    legacy_agent_mode = normalized.pop("agent_mode", None)
    if legacy_agent_mode is not None and "agent_mode" not in settings:
        settings["agent_mode"] = legacy_agent_mode

    legacy_default_model = normalized.pop("default_model", None)
    if isinstance(legacy_default_model, dict) and "default_model" not in settings:
        settings["default_model"] = legacy_default_model

    legacy_default_provider = normalized.pop("default_provider", None)
    if legacy_default_provider is not None and "default_provider" not in settings:
        settings["default_provider"] = legacy_default_provider

    normalized["settings"] = settings
    return normalized


def get_workspace_settings_meta(meta: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return normalized workspace settings from metadata."""
    if not isinstance(meta, dict):
        return {}
    settings = meta.get("settings")
    return settings if isinstance(settings, dict) else {}


def get_workspace_default_model_config(meta: Dict[str, Any] | None) -> Dict[str, str]:
    """Resolve the workspace default model config from settings.

    Priority inside workspace settings:
    1. settings.default_model.{provider,model}
    2. settings.default_provider + that provider's configured model field
    """
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
    """Get metadata for a workspace."""
    folder = get_workspace_folder(name)
    if not folder:
        return {}
    meta_path = folder / ".workspace.json"
    if meta_path.exists():
        raw = json.loads(meta_path.read_text())
        normalized = _normalize_workspace_metadata(raw)
        if normalized != raw:
            meta_path.write_text(json.dumps(normalized, indent=2))
        return normalized
    return {}


def update_workspace_metadata(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update metadata for a workspace."""
    folder = get_workspace_folder(name)
    if not folder:
        return {}
    meta_path = folder / ".workspace.json"
    meta = get_workspace_metadata(name)
    meta.update(updates)
    meta = _normalize_workspace_metadata(meta)
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


def get_project_folder(project_name: str) -> Optional[Path]:
    """
    Get the path to an existing workspace folder.

    Args:
        project_name: Name of the workspace folder.

    Returns:
        Path to the workspace folder if it exists, None otherwise.
    """
    project_path = WORKSPACES_ROOT / project_name
    if project_path.exists() and project_path.is_dir():
        return project_path.resolve()
    return None


def list_project_folders() -> list[Path]:
    """
    List all existing workspace folders.

    Returns:
        List of absolute paths to all workspace folders.
    """
    ensure_workspaces_dir()
    return [p for p in WORKSPACES_ROOT.iterdir() if p.is_dir()]


def delete_project_folder(project_name: str) -> bool:
    """Delete an existing workspace folder recursively.

    Returns True when the folder existed and was removed, otherwise False.
    """
    project_path = WORKSPACES_ROOT / project_name
    if not project_path.exists() or not project_path.is_dir():
        return False
    shutil.rmtree(project_path)
    return True


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
      .agents_hub/workspaces/{workspace_name}/                  <- .workspace.json and .logs/ live here
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


# -------- Preferred API (aliases) --------

def create_workspace_folder(name: Optional[str] = None) -> Path:
    return create_project_folder(name)


def get_workspace_folder(name: str) -> Optional[Path]:
    return get_project_folder(name)


def list_workspace_folders() -> list[Path]:
    return list_project_folders()


def delete_workspace_folder(name: str) -> bool:
    return delete_project_folder(name)


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
        "workspace_root":          "WORKSPACE_ROOT",
        "orch_poll_interval":      "ORCH_POLL_INTERVAL",
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
