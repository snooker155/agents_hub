"""Assemble agent system prompts from per-agent markdown files.

Each agent has a folder named after its definition id containing:
- ``instructions.md`` (required) — the main system prompt
- ``capabilities.md`` (optional) — what the agent can do
- ``usage.md``        (optional) — when/how to invoke the agent

The folder lives in one of two layers. ``agents/definitions/<id>/`` in the
repository holds the system agents' prompts exactly as git tracks them, and
the hub never writes there. ``<state root>/definitions/<id>/``
(``common.paths.AGENT_DEFINITIONS_DIR``) holds everything the hub writes: each
custom agent's files, and an operator's edits to a system agent. A file there
shadows the shipped file of the same name, an empty one included, so clearing
a shipped ``capabilities.md`` leaves an empty file rather than the original.
Removing the state folder of a system agent gives it back its shipped text.

A ``definitions_dir`` other than the shipped folder (tests, a custom factory)
is a single folder that is both read and written.

The assembled prompt is the concatenation of these files (in order),
with section headers prepended to capabilities/usage when those files exist.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from common.paths import AGENT_DEFINITIONS_DIR

log = logging.getLogger(__name__)

#: The repository folder of the system agents' prompts, read only.
SYSTEM_DEFINITIONS_DIR = Path(__file__).parent / "definitions"
#: The folder a call without ``definitions_dir`` resolves. Tests point it at a
#: throwaway folder, which then stands alone, without the state layer.
DEFINITIONS_DIR = SYSTEM_DEFINITIONS_DIR
#: Where the hub writes prompts when it resolves the shipped folder.
USER_DEFINITIONS_DIR = AGENT_DEFINITIONS_DIR

INSTRUCTIONS_FILE = "instructions.md"
CAPABILITIES_FILE = "capabilities.md"
USAGE_FILE = "usage.md"
PART_FILES = (INSTRUCTIONS_FILE, CAPABILITIES_FILE, USAGE_FILE)


def _layers(definitions_dir: Optional[Path]) -> Tuple[Path, ...]:
    """The folders a definition is read from, the written one first."""
    base = Path(definitions_dir) if definitions_dir else DEFINITIONS_DIR
    if base == SYSTEM_DEFINITIONS_DIR:
        return (USER_DEFINITIONS_DIR, SYSTEM_DEFINITIONS_DIR)
    return (base,)


def agent_dir(agent_id: str, definitions_dir: Optional[Path] = None) -> Path:
    """The folder the hub writes *agent_id*'s files to (it may not exist yet)."""
    return _layers(definitions_dir)[0] / agent_id


def part_path(agent_id: str, filename: str, definitions_dir: Optional[Path] = None) -> Optional[Path]:
    """The file that holds one part of the definition: the written layer's
    when it has one, else the shipped one, else None."""
    for layer in _layers(definitions_dir):
        path = layer / agent_id / filename
        if path.is_file():
            return path
    return None


def part_paths(agent_id: str, definitions_dir: Optional[Path] = None) -> List[Path]:
    """Every place a part of the definition may be read from, present or not.
    The agent cache keys on these, so writing or removing any of them is seen."""
    return [layer / agent_id / name for layer in _layers(definitions_dir) for name in PART_FILES]


def has_definition(agent_id: str, definitions_dir: Optional[Path] = None) -> bool:
    return part_path(agent_id, INSTRUCTIONS_FILE, definitions_dir) is not None


def is_system_definition(agent_id: str, definitions_dir: Optional[Path] = None) -> bool:
    """True when the shipped folder has this definition (a system agent's)."""
    layers = _layers(definitions_dir)
    return len(layers) > 1 and (layers[-1] / agent_id / INSTRUCTIONS_FILE).is_file()


def is_customized(agent_id: str, definitions_dir: Optional[Path] = None) -> bool:
    """True when a system agent's shipped text is shadowed by an edit."""
    folder = agent_dir(agent_id, definitions_dir)
    return (is_system_definition(agent_id, definitions_dir)
            and any((folder / name).is_file() for name in PART_FILES))


def _read(path: Optional[Path]) -> str:
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def assemble_prompt(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    """Build the full system prompt for *agent_id* from its markdown files.

    Raises FileNotFoundError when ``instructions.md`` is missing.
    """
    instructions_path = part_path(agent_id, INSTRUCTIONS_FILE, definitions_dir)
    if instructions_path is None:
        raise FileNotFoundError(
            f"Agent definition not found: {agent_dir(agent_id, definitions_dir) / INSTRUCTIONS_FILE}. "
            f"Each agent must have an instructions.md file."
        )

    parts: list[str] = [_read(instructions_path)]

    capabilities = read_capabilities(agent_id, definitions_dir)
    if capabilities:
        parts.append("## Capabilities\n\n" + capabilities)

    usage = read_usage(agent_id, definitions_dir)
    if usage:
        parts.append("## Usage\n\n" + usage)

    return "\n\n".join(parts)


def read_instructions(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    """Return the raw contents of ``instructions.md`` (or empty string)."""
    return _read(part_path(agent_id, INSTRUCTIONS_FILE, definitions_dir))


def read_capabilities(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    return _read(part_path(agent_id, CAPABILITIES_FILE, definitions_dir))


def read_usage(agent_id: str, definitions_dir: Optional[Path] = None) -> str:
    return _read(part_path(agent_id, USAGE_FILE, definitions_dir))


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


def clear_part(agent_id: str, filename: str, definitions_dir: Optional[Path] = None) -> None:
    """Remove an optional part. A shipped file is shadowed by an empty one,
    since the shipped folder is never written."""
    layers = _layers(definitions_dir)
    path = layers[0] / agent_id / filename
    if any((layer / agent_id / filename).is_file() for layer in layers[1:]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    elif path.exists():
        path.unlink()


def delete_definition(agent_id: str, definitions_dir: Optional[Path] = None) -> bool:
    """Delete the written folder for *agent_id*. Returns True if removed.

    A system agent's shipped folder stays: removing its edits gives it back
    the text git tracks.
    """
    folder = agent_dir(agent_id, definitions_dir)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def move_untracked_definitions(owned_by_hub: Callable[[str], bool]) -> List[str]:
    """Move prompt files the hub once wrote into the repository folder to the
    state folder, where it writes them now. Returns the moved paths.

    Only files git does not track move, and only for a definition
    *owned_by_hub* accepts (a custom agent's, or a system agent the operator
    edited): an uncommitted system agent someone is writing by hand stays.
    A file whose target exists stays too. Without git (an image) nothing moves.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z", "."],
            cwd=SYSTEM_DEFINITIONS_DIR, capture_output=True, timeout=10, check=True,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError):
        return []
    moved: List[str] = []
    for rel in filter(None, out.split("\0")):
        parts = Path(rel).parts
        if len(parts) < 2 or not owned_by_hub(parts[0]):
            continue
        src, dst = SYSTEM_DEFINITIONS_DIR / rel, USER_DEFINITIONS_DIR / rel
        if dst.exists():
            log.warning("agent definitions: %s stays in the repository, %s already exists", src, dst)
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(rel)
        except OSError:
            log.warning("agent definitions: could not move %s to %s", src, dst, exc_info=True)
            continue
        folder = src.parent
        while folder != SYSTEM_DEFINITIONS_DIR and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    return moved


__all__ = [
    "DEFINITIONS_DIR",
    "SYSTEM_DEFINITIONS_DIR",
    "USER_DEFINITIONS_DIR",
    "PART_FILES",
    "INSTRUCTIONS_FILE",
    "CAPABILITIES_FILE",
    "USAGE_FILE",
    "agent_dir",
    "part_path",
    "part_paths",
    "has_definition",
    "is_system_definition",
    "is_customized",
    "assemble_prompt",
    "read_instructions",
    "read_capabilities",
    "read_usage",
    "write_instructions",
    "write_capabilities",
    "write_usage",
    "clear_part",
    "delete_definition",
    "move_untracked_definitions",
]
