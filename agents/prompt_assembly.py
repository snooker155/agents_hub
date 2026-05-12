"""Assemble agent system prompts from per-agent markdown files.

Each agent has a folder at ``agents/definitions/<agent_id>/`` containing:
- ``instructions.md`` (required) — the main system prompt
- ``capabilities.md`` (optional) — what the agent can do
- ``usage.md``        (optional) — when/how to invoke the agent

The assembled prompt is the concatenation of these files (in order),
with section headers prepended to capabilities/usage when those files exist.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


DEFINITIONS_DIR = Path(__file__).parent / "definitions"

INSTRUCTIONS_FILE = "instructions.md"
CAPABILITIES_FILE = "capabilities.md"
USAGE_FILE = "usage.md"


def agent_dir(agent_id: str, definitions_dir: Optional[Path] = None) -> Path:
    base = definitions_dir or DEFINITIONS_DIR
    return base / agent_id


def has_definition(agent_id: str, definitions_dir: Optional[Path] = None) -> bool:
    return (agent_dir(agent_id, definitions_dir) / INSTRUCTIONS_FILE).is_file()


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def assemble_prompt(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    """Build the full system prompt for *agent_id* from its markdown files.

    Raises FileNotFoundError when ``instructions.md`` is missing.
    """
    folder = agent_dir(agent_id, definitions_dir)
    instructions_path = folder / INSTRUCTIONS_FILE
    if not instructions_path.is_file():
        raise FileNotFoundError(
            f"Agent definition not found: {instructions_path}. "
            f"Each agent must have an instructions.md file."
        )

    parts: list[str] = [_read(instructions_path)]

    capabilities = _read(folder / CAPABILITIES_FILE)
    if capabilities:
        parts.append("## Capabilities\n\n" + capabilities)

    usage = _read(folder / USAGE_FILE)
    if usage:
        parts.append("## Usage\n\n" + usage)

    return "\n\n".join(parts)


def read_instructions(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    """Return the raw contents of ``instructions.md`` (or empty string)."""
    return _read(agent_dir(agent_id, definitions_dir) / INSTRUCTIONS_FILE)


def read_capabilities(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    return _read(agent_dir(agent_id, definitions_dir) / CAPABILITIES_FILE)


def read_usage(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    return _read(agent_dir(agent_id, definitions_dir) / USAGE_FILE)


def write_instructions(agent_id: str, content: str, definitions_dir: Optional[Path] = None) -> None:
    folder = agent_dir(agent_id, definitions_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / INSTRUCTIONS_FILE).write_text(content, encoding="utf-8")


def write_capabilities(agent_id: str, content: str, definitions_dir: Optional[Path] = None) -> None:
    folder = agent_dir(agent_id, definitions_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CAPABILITIES_FILE).write_text(content, encoding="utf-8")


def write_usage(agent_id: str, content: str, definitions_dir: Optional[Path] = None) -> None:
    folder = agent_dir(agent_id, definitions_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / USAGE_FILE).write_text(content, encoding="utf-8")


def delete_definition(agent_id: str, definitions_dir: Optional[Path] = None) -> bool:
    """Delete the entire definition folder for *agent_id*. Returns True if removed."""
    import shutil

    folder = agent_dir(agent_id, definitions_dir)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


__all__ = [
    "DEFINITIONS_DIR",
    "INSTRUCTIONS_FILE",
    "CAPABILITIES_FILE",
    "USAGE_FILE",
    "agent_dir",
    "has_definition",
    "assemble_prompt",
    "read_instructions",
    "read_capabilities",
    "read_usage",
    "write_instructions",
    "write_capabilities",
    "write_usage",
    "delete_definition",
]
