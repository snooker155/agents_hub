"""Doc-vs-code sync checks for ARCHITECTURE.md, SETUP.md, README.md and docs/.

These files describe paths and agent ids that live elsewhere in the repo, and
both drift the same way code does: a folder moves, a file is renamed, an
agent's seed status changes, and the prose is never told. This week's own
example is `agents/definitions/pm_agent/` (and twenty siblings) moving to
`examples/agents/waterfall/pm_agent/` — no longer seeded, but still named as
"built-in" in a few places until fixed.

Rather than pin either side by hand, these tests re-derive the ground truth
from the repository itself — `bootstrap/agents.json` for the seed roster,
`examples/agents/*/*/` for what moved out, the filesystem for paths, and
`docs/*.md` for the corpus — and check the docs against it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
SETUP = ROOT / "SETUP.md"
README = ROOT / "README.md"
DOCS_DIR = ROOT / "docs"
DOCS_INDEX = DOCS_DIR / "index.json"

_BUILTIN_DOCS = {"ARCHITECTURE.md": ARCHITECTURE, "SETUP.md": SETUP, "README.md": README}


def _seed_agent_ids() -> set:
    data = json.loads((ROOT / "bootstrap" / "agents.json").read_text())
    return {a["id"] for a in data["agents"]}


def _moved_agent_ids() -> set:
    """Agent ids that ship as examples, not seeded: one folder per id under
    examples/agents/<theme>/<id>/. Read from disk rather than hardcoded, so a
    future move is caught automatically instead of needing this test updated
    too."""
    examples_root = ROOT / "examples" / "agents"
    return {
        agent_dir.name
        for theme_dir in examples_root.iterdir() if theme_dir.is_dir()
        for agent_dir in theme_dir.iterdir() if agent_dir.is_dir()
    }


# ── ARCHITECTURE.md: every backtick path of the form `<dir>/` or `<file>.py` ─
# actually exists in the repo.

# Inside this bullet list, every entry is shorthand *relative to the state
# directory* ("flows/", "workspaces/", ...) rather than a repo path — the
# block says so in its own first line, and is skipped outright rather than
# resolved against the repo root.
_STATE_DIR_MARKER = "under `.agents_hub/` unless noted"

# A path that starts here is generated runtime state (see tests/conftest.py,
# which points a fresh `.agents_hub` at a throwaway temp dir for every test
# run), never a checked-in path, so it is not something this test can verify.
_SKIP_PREFIXES = (".agents_hub/",)

_IGNORE_DIRS = {".git", "node_modules", ".venv", "__pycache__", "chroma_db", "site"}


def _architecture_path_candidates() -> list:
    lines = ARCHITECTURE.read_text().splitlines()
    candidates = []
    in_fence = False
    in_state_dir_block = False
    for line in lines:
        if line.startswith("## "):
            in_state_dir_block = False
        if _STATE_DIR_MARKER in line:
            in_state_dir_block = True
            continue
        if in_state_dir_block:
            continue
        # Ascii tree diagrams and shell/jsonc examples live in fenced blocks
        # and are full of path-shaped text (box-drawing prefixes, a
        # hypothetical external repo's own files) that is illustrative, not a
        # claim about this repo — skip fenced content entirely.
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in re.finditer(r"`([^`\n]+)`", line):
            token = m.group(1)
            if "<" in token or " " in token or token.startswith("http"):
                continue  # placeholder, prose, or a URL — not a path
            if token.endswith("/") or token.endswith(".py"):
                candidates.append(token)
    return candidates


def _path_exists(token: str) -> bool:
    if token.startswith(_SKIP_PREFIXES):
        return True
    if token.endswith("/"):
        return (ROOT / token.rstrip("/")).is_dir()
    if (ROOT / token).is_file():
        return True
    if "/" not in token:
        # A bare filename, mentioned as shorthand for a path spelled out in
        # full earlier in the same bullet (e.g. "hooks.py" after
        # "agents/hooks.py") — accepted if it exists anywhere in the repo.
        return any(
            not set(p.parts) & _IGNORE_DIRS for p in ROOT.rglob(token)
        )
    return False


def test_architecture_paths_exist():
    candidates = _architecture_path_candidates()
    assert candidates, "path-scanning regex found nothing in ARCHITECTURE.md — check it still matches backtick paths"
    missing = sorted({c for c in candidates if not _path_exists(c)})
    assert not missing, f"ARCHITECTURE.md names paths that do not exist in the repo: {missing}"


# ── Built-in agent ids: never a moved one, and every claimed one is seeded ──

def _backtick_tokens(text: str) -> list:
    return re.findall(r"`([^`\n]+)`", text)


def test_moved_agents_are_never_named_as_builtin():
    """pm_agent, qa_agent, devops_agent, memory_agent and the rest of the
    example roster moved to examples/agents/ this week and bootstrap/agents.json
    no longer creates them. None of the three top-level docs may still name one
    as if it ships built in."""
    moved = _moved_agent_ids()
    assert moved, "no example agents found under examples/agents/*/* — check the fixture path"
    for name, path in _BUILTIN_DOCS.items():
        offenders = set(_backtick_tokens(path.read_text())) & moved
        assert not offenders, f"{name} names moved example agent(s) as if built in: {sorted(offenders)}"


# Restricted to the couple of phrasings actually used to introduce a list of
# built-in agent ids, rather than every line containing the word "built-in" —
# ARCHITECTURE.md also uses that word in an unrelated sentence about streaming
# frame names (`token`, `tool_start`, ...), which are not agent ids at all.
_BUILTIN_LIST_MARKER = re.compile(r"built-in agents listed|seed roster is", re.IGNORECASE)
_ID_TOKEN = re.compile(r"^[a-z][a-z0-9_\-]*$")


def test_builtin_agent_mentions_are_actually_seeded():
    """Any agent id called out as part of the built-in / seed roster must be a
    real entry in bootstrap/agents.json, the single source of truth for what a
    fresh install actually creates."""
    seeded = _seed_agent_ids()
    checked_any = False
    for name, path in _BUILTIN_DOCS.items():
        for line in path.read_text().splitlines():
            if not _BUILTIN_LIST_MARKER.search(line):
                continue
            for token in _backtick_tokens(line):
                if not _ID_TOKEN.match(token):
                    continue  # not id-shaped: a path, a filename, an env var
                checked_any = True
                assert token in seeded, (
                    f"{name} names `{token}` as a built-in/seeded agent, "
                    "but it is not in bootstrap/agents.json"
                )
    assert checked_any, (
        "no 'built-in agents listed' / 'seed roster is' phrasing found — "
        "check the docs still introduce the seed roster somewhere, or update this test's marker"
    )


# ── docs/index.json stays in step with docs/*.md ─────────────────────────────

def test_docs_index_matches_files_on_disk():
    index = json.loads(DOCS_INDEX.read_text())
    indexed_ids = {e["id"] for e in index["docs"]}
    on_disk_ids = {p.stem for p in DOCS_DIR.glob("*.md")}
    assert indexed_ids == on_disk_ids, (
        f"only in docs/index.json: {sorted(indexed_ids - on_disk_ids)}; "
        f"only on disk with no index entry: {sorted(on_disk_ids - indexed_ids)}"
    )
