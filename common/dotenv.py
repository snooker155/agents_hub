"""
Single parser for the repository's .env file.

Every other place in the codebase that needs a value from .env (``common.config``,
subprocess entrypoints, node/flow launchers) used to hand-roll its own
line-by-line parser. They agreed on the easy cases and quietly diverged on the
rest (quoting, comments, the ``export`` prefix). This module is the one parser:
its output matches ``python_dotenv.dotenv_values`` on every format the repo
actually writes or reads (``KEY="value"``, ``#`` comments, blank lines, an
optional ``export`` prefix, single- and double-quoted values with the usual
backslash escapes in double quotes). It does not implement dotenv's multi-line
quoted values — nothing in this repo writes those, and the Settings route
(``dashboard/backend/routes/settings.py: _write_env_key``) always emits a single
double-quoted line per key.

Reads are cached per path by (mtime_ns, size), so a request that calls
``read_env`` several times only opens the file once. A write through the
Settings route changes both, so the very next read sees it without any
explicit invalidation — ``invalidate`` exists for callers (tests, mostly) that
rewrite a file fast enough to land on the same mtime.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from common.paths import PROJECT_ROOT

# One assignment per line: an optional ``export`` prefix (bash-style .env files
# support sourcing them directly), the key, then the raw value up to end of
# line. Quote handling happens afterwards in ``_unquote`` — a quoted value can
# itself contain '#' or trailing spaces that must survive.
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

# Backslash escapes recognised inside a double-quoted value, matching
# python-dotenv. An unrecognised escape (``\q``) is left as-is (backslash kept).
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}
_ESCAPE_RE = re.compile(r"\\(.)")

# path -> ((mtime_ns, size), parsed values)
_cache: Dict[str, Tuple[Tuple[int, int], Dict[str, str]]] = {}


def env_path() -> Path:
    """Canonical .env location: <project root>/.env.

    No env var overrides this today (grepped the repo; nothing reads one), so
    none is added here — a module that needs a different file passes its own
    ``path`` to ``read_env`` instead.
    """
    return PROJECT_ROOT / ".env"


def _unescape_double_quoted(body: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPES.get(m.group(1), "\\" + m.group(1)), body)


def _unquote(raw: str) -> str:
    """Strip quoting/escaping from one matched value, python-dotenv style."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return _unescape_double_quoted(raw[1:-1])
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    # Unquoted: an inline comment counts only when '#' is preceded by whitespace
    # (so a bare "#" inside a value like a URL fragment is not treated as one).
    for i, ch in enumerate(raw):
        if ch == "#" and i > 0 and raw[i - 1] in " \t":
            raw = raw[:i]
            break
    return raw.strip()


def _parse(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        key, raw_value = match.group(1), match.group(2)
        result[key] = _unquote(raw_value)
    return result


def _stat_key(path: Path) -> Optional[Tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read_env(path: Optional[Path] = None) -> Dict[str, str]:
    """Parse a .env file into a plain dict, cached by (mtime_ns, size).

    Defaults to ``env_path()``. A missing file reads as empty, same as
    ``dotenv_values`` on a path that does not exist.
    """
    target = Path(path) if path is not None else env_path()
    cache_key = str(target)
    stat_key = _stat_key(target)
    if stat_key is None:
        _cache.pop(cache_key, None)
        return {}

    cached = _cache.get(cache_key)
    if cached is not None and cached[0] == stat_key:
        return cached[1]

    values = _parse(target.read_text(encoding="utf-8"))
    _cache[cache_key] = (stat_key, values)
    return values


def invalidate(path: Optional[Path] = None) -> None:
    """Drop the cached parse for ``path`` (or every cached path when omitted).

    The mtime/size check already picks up a normal write; this is for callers
    (mainly tests) that rewrite a file fast enough to keep the same mtime and
    size, where the cache would otherwise mask the change.
    """
    if path is None:
        _cache.clear()
        return
    _cache.pop(str(Path(path)), None)
