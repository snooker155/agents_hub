"""The TypeScript SDK's generated files, and its own test suite, stay in sync
with the routes they were built from.

Two things could silently drift apart from `clients/agents-hub-ts/`:

- A route's shape changes (a field added to a model, an endpoint added or
  removed) and nobody reruns `scripts/gen_ts_sdk.py`, so the committed
  `openapi.schema.json` and `src/generated/types.ts` describe a hub that no
  longer exists.
- The hand-written layer on top (`src/client.ts` and friends) stops matching
  its own tests.

The first is pure Python (`--check` dumps the schema from the app in
process) and always runs, so a route change that forgets the SDK fails the
ordinary test job. The second needs Node 22.18 or newer and is skipped
without it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "clients" / "agents-hub-ts"

def _node_version() -> tuple[int, int] | None:
    """`(major, minor)` of whatever `node` is on PATH, or None if it cannot be run."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    # "v22.6.0\n" -> (22, 6)
    parts = result.stdout.strip().lstrip("v").split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


# Node's own TypeScript support (plain type annotations stripped, no
# transform) arrived at 22.6 behind --experimental-strip-types and runs
# unflagged from 22.18 (and 23.6 on the 23 line); this package's sources rely
# on it since there is no build step. An older `node` on PATH is not a broken package, just an
# environment this test cannot exercise, so it is skipped rather than failed.
_NODE_VERSION = _node_version()
# The freshness check is pure Python and runs everywhere, so a route change
# that forgets the SDK fails the ordinary test job; only the package's own
# tests need a modern node.
needs_node = pytest.mark.skipif(
    _NODE_VERSION is None
    or _NODE_VERSION < (22, 18)
    or (_NODE_VERSION[0] == 23 and _NODE_VERSION < (23, 6)),
    reason="node is missing or older than 22.18 (no unflagged TypeScript support)",
)


def test_generated_files_match_the_current_routes(tmp_path):
    """`python scripts/gen_ts_sdk.py --check` against the live app: a route
    changed since `openapi.schema.json` / `src/generated/types.ts` were last
    committed shows up here instead of as a silent mismatch downstream."""
    result = subprocess.run(
        [sys.executable, "scripts/gen_ts_sdk.py", "--check"],
        cwd=REPO_ROOT,
        env={**_base_env(), "AGENTS_HUB_ROOT": str(tmp_path / "hubroot")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "the TypeScript SDK's generated files are stale; run "
        f"python scripts/gen_ts_sdk.py\n{result.stdout}\n{result.stderr}"
    )


@needs_node
def test_package_tests_pass():
    """The SDK's own `node --test` suite (request building, auth header,
    error mapping, SSE parsing) against its TypeScript sources directly;
    Node 22.18+ strips the types natively, so this needs no build step and no
    `npm install` (the package has zero runtime dependencies)."""
    result = subprocess.run(
        ["node", "--test", "test/*.test.ts"],
        cwd=PACKAGE_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _base_env() -> dict:
    import os

    env = dict(os.environ)
    env.setdefault("AGENTS_HUB_DATABASE_URL", "")
    env.setdefault("AGENTS_HUB_CHAT_EXECUTION", "inprocess")
    return env
