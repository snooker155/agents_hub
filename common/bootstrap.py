"""
First-run state bootstrap.

When the runtime state directory (`.agents_hub/`) is empty or missing key
pieces, seed it from the repo-shipped `bootstrap/` folder so the system starts
with the predefined default workspace and core agents (orchestrator,
agent_creator, decomposer).

Existing state is never overwritten — bootstrap is per-entity additive:
- `.agents_hub/agents.json` is copied from `bootstrap/agents.json` only when
  it does not exist.
- `.agents_hub/workspaces/default/` is created from
  `bootstrap/workspaces/default/` only when it does not exist.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from common.paths import (
    AGENTS_FILE,
    PROJECT_ROOT,
    WORKSPACES_ROOT,
    ensure_agents_hub_root,
    ensure_workspaces_root,
)


BOOTSTRAP_ROOT = PROJECT_ROOT / "bootstrap"
BOOTSTRAP_AGENTS_FILE = BOOTSTRAP_ROOT / "agents.json"
BOOTSTRAP_WORKSPACES_ROOT = BOOTSTRAP_ROOT / "workspaces"


def _seed_agents_file() -> bool:
    if AGENTS_FILE.exists():
        return False
    if not BOOTSTRAP_AGENTS_FILE.is_file():
        return False
    ensure_agents_hub_root()
    shutil.copyfile(BOOTSTRAP_AGENTS_FILE, AGENTS_FILE)
    return True


def _seed_default_workspace() -> bool:
    src = BOOTSTRAP_WORKSPACES_ROOT / "default"
    if not src.is_dir():
        return False
    dst = WORKSPACES_ROOT / "default"
    if dst.exists():
        return False
    ensure_workspaces_root()
    shutil.copytree(src, dst)
    return True


def ensure_initial_state() -> dict[str, bool]:
    """Seed missing first-run state from `bootstrap/`. Returns what was seeded."""
    return {
        "agents_file": _seed_agents_file(),
        "default_workspace": _seed_default_workspace(),
    }
