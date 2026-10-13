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
import re
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
#: Example requests for the empty chat, by UI language (not part of the prompt).
EXAMPLES_FILE = "examples.yaml"
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


_GOOD_FITS = re.compile(r"^\s*good fits\b", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-*]\s+")
_QUOTED = re.compile(r'"([^"]{8,200})"|\u201c([^\u201d]{8,200})\u201d')


def example_prompts(usage: str, limit: int = 6) -> List[str]:
    """Example requests to the agent, quoted in the bullets of its ``usage.md``.

    The "Good fits:" list is read when there is one (its bullets quote what
    a person would say, ``- "Compare X with Y"``), every bullet otherwise.
    A bullet that opens with a quote gives one example per quoted phrase on
    its first line; any other bullet describes a kind of request rather than
    one (``- Any "find me some options" request``), and is skipped.
    """
    lines = usage.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if _GOOD_FITS.match(line)), None)
    if start is not None:
        section: List[str] = []
        for line in lines[start:]:
            # The list ends at the next label ("Poor fits:", "How to invoke:").
            if line.strip().endswith(":") and not _BULLET.match(line):
                break
            section.append(line)
        lines = section
    found: List[str] = []
    for line in lines:
        bullet = _BULLET.match(line)
        if not bullet or not line[bullet.end():].startswith(('"', "\u201c")):
            continue
        for match in _QUOTED.finditer(line):
            text = (match.group(1) or match.group(2)).replace("`", "").strip()
            text = text[:1].upper() + text[1:]
            if text and text not in found:
                found.append(text)
            if len(found) >= limit:
                return found
    return found


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
        # The hub writes only the prompt parts; anything else (examples.yaml)
        # is a file someone is adding to the repository by hand.
        if len(parts) != 2 or parts[1] not in PART_FILES or not owned_by_hub(parts[0]):
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


def read_examples(agent_id: str, lang: str = "en",
                  definitions_dir: Optional[Path] = None) -> List[str]:
    """The example requests in ``examples.yaml`` for UI language *lang*
    (``{en: [...], ru: [...], de: [...]}``), English when that language has
    none, empty when the file is missing or unreadable."""
    path = part_path(agent_id, EXAMPLES_FILE, definitions_dir)
    if path is None:
        return []
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("Unreadable %s: %s", path, exc)
        return []
    if not isinstance(data, dict):
        return []
    items = data.get(lang) or data.get("en") or []
    return [str(item).strip() for item in items if str(item).strip()] if isinstance(items, list) else []


__all__ = [
    "DEFINITIONS_DIR",
    "SYSTEM_DEFINITIONS_DIR",
    "USER_DEFINITIONS_DIR",
    "PART_FILES",
    "INSTRUCTIONS_FILE",
    "CAPABILITIES_FILE",
    "USAGE_FILE",
    "EXAMPLES_FILE",
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
    "example_prompts",
    "read_examples",
    "write_instructions",
    "write_capabilities",
    "write_usage",
    "clear_part",
    "delete_definition",
    "move_untracked_definitions",
]
