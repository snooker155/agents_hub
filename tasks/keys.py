"""
Jira-style task keys.

Every task gets a short human-readable key like ``DEMO-12``: an uppercase
prefix derived from the task's project (or workspace as a fallback) plus an
incrementing number. Keys are the identifier surfaced to users and LLM agents;
the UUID stays the canonical storage id.

Keys live only in ``tasks.json`` — there is no separate counter file. The next
number for a prefix is computed by scanning the existing tasks for the highest
``<PREFIX>-<n>`` and adding one, so numbering continues correctly across
restarts and after deletions (numbers are never reused while a higher one
exists).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

DEFAULT_PREFIX = "TASK"

# Matches a task key like "DEMO-12" (used by tools to tell keys from UUIDs)
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d+$")


def derive_prefix(name: str) -> str:
    """Derive an uppercase key prefix from a project/workspace name.

    Multi-word names use initials ("Agents Hub" -> "AH"); single words use the
    first four letters ("backend" -> "BACK").
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w]
    # Drop purely numeric fragments (e.g. the "20250101" in "ws_20250101")
    alpha_words = [w for w in words if not w.isdigit()] or words
    if not alpha_words:
        return DEFAULT_PREFIX
    if len(alpha_words) >= 2:
        prefix = "".join(w[0] for w in alpha_words[:4])
    else:
        prefix = alpha_words[0][:4]
    prefix = re.sub(r"[^A-Z0-9]", "", prefix.upper())
    if not prefix or prefix[0].isdigit():
        prefix = ("T" + prefix)[:4]
    return prefix or DEFAULT_PREFIX


def prefix_for_task(project_id: Optional[str], workspace: Optional[str]) -> str:
    """Resolve the key prefix for a task.

    Prefers the linked project's name, falls back to the workspace name, then
    to the global default prefix.
    """
    if project_id:
        try:
            from projects.storage import ProjectStore
            from common.paths import PROJECTS_FILE
            proj = ProjectStore(PROJECTS_FILE).get(str(project_id))
            if proj and proj.name:
                return derive_prefix(proj.name)
        except Exception:
            pass
    ws = (workspace or "").strip()
    if ws and ws != "default":
        return derive_prefix(ws)
    return DEFAULT_PREFIX


def next_key(
    tasks: Iterable,
    project_id: Optional[str] = None,
    workspace: Optional[str] = None,
) -> str:
    """Compute the next task key from the existing tasks.

    Scans `tasks` (objects with a ``key`` attribute or dicts) for keys with the
    resolved prefix and returns ``<PREFIX>-<max+1>``.
    """
    prefix = prefix_for_task(project_id, workspace)
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    highest = 0
    for t in tasks:
        key = getattr(t, "key", None) if not isinstance(t, dict) else t.get("key")
        m = pattern.match((key or "").strip().upper())
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{highest + 1}"


def looks_like_key(value: str) -> bool:
    """True when ``value`` has the shape of a task key (e.g. ``DEMO-12``)."""
    return bool(KEY_RE.match((value or "").strip().upper()))
