"""
Blender connector configuration.

Blender is an *external resource* in the same sense GitHub is: something the
operator points the hub at, whose availability is a fact about the machine
rather than about the code. State lives in ``.agents_hub/blender_connector.json``
and nothing here is secret, so the whole config is readable from the API.

Two ways to reach an engine, in the order the operator is expected to try them:

``local``
    A Blender the operator installed. ``binary_path`` names it; left empty, the
    usual install locations are probed (:func:`discover_binary`), which is a
    convenience and never an override — an explicit path always wins.

``docker``
    Containers the hub starts from a Blender image, so several daemons can be
    managed, capped and torn down centrally. The config accepts it now; the
    launcher is not built yet and says so rather than pretending.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from filelock import FileLock

from common.paths import AGENTS_HUB_ROOT, ensure_agents_hub_root

try:  # the same JSON helpers every connector uses
    import json
except ImportError:  # pragma: no cover
    raise

MODES = ("local", "docker")

_FILE = AGENTS_HUB_ROOT / "blender_connector.json"
_LOCK = AGENTS_HUB_ROOT / "blender_connector.json.lock"

#: Where a Blender install usually lands, per platform. Probed in order.
_CANDIDATE_PATHS: List[str] = [
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/usr/local/bin/blender",
    "/usr/bin/blender",
    "/opt/blender/blender",
    "/snap/bin/blender",
    r"C:\Program Files\Blender Foundation\Blender\blender.exe",
]


def _defaults() -> Dict[str, Any]:
    return {
        "enabled": True,
        "mode": "local",
        "binary_path": "",           # empty: discover
        "docker_image": "blender:latest",
        "max_daemons": 4,
        "idle_timeout_s": 900,       # a daemon nobody talks to exits on its own
        "startup_timeout_s": 90,     # cold Blender start, generous for a slow disk
        "command_timeout_s": 120,    # a single geometry command
    }


def _load_unlocked() -> Dict[str, Any]:
    out = _defaults()
    if not _FILE.exists():
        return out
    try:
        text = _FILE.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except Exception:
        return out
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in out})
    return out


def load() -> Dict[str, Any]:
    with FileLock(str(_LOCK), timeout=5.0):
        return _load_unlocked()


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``patch`` into the stored config and return the new state."""
    clean = _validate(patch)
    with FileLock(str(_LOCK), timeout=5.0):
        data = _load_unlocked()
        data.update(clean)
        ensure_agents_hub_root()
        tmp = _FILE.with_suffix(_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _FILE)
        _probe_cache.clear()
        return data


def _validate(patch: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "enabled" in patch:
        out["enabled"] = bool(patch["enabled"])
    if "mode" in patch:
        mode = str(patch["mode"] or "").strip().lower()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        out["mode"] = mode
    if "binary_path" in patch:
        out["binary_path"] = str(patch["binary_path"] or "").strip()
    if "docker_image" in patch:
        out["docker_image"] = str(patch["docker_image"] or "").strip()
    for key, lo, hi in (("max_daemons", 1, 32), ("idle_timeout_s", 30, 86400),
                        ("startup_timeout_s", 10, 600), ("command_timeout_s", 5, 3600)):
        if key in patch:
            try:
                value = int(patch[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an integer")
            if not lo <= value <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            out[key] = value
    return out


def discover_binary() -> str:
    """Best guess at an installed Blender, or "" when there is none.

    A guess, not a default: it fills the UI's placeholder and makes the common
    case work without configuration, but an operator-set path is never
    second-guessed.
    """
    found = shutil.which("blender")
    if found:
        return found
    for candidate in _CANDIDATE_PATHS:
        if Path(candidate).exists():
            return candidate
    return ""


def binary_path() -> str:
    """The Blender binary to launch: the configured path, else a discovered one."""
    configured = (load().get("binary_path") or "").strip()
    return configured or discover_binary()


#: ``--version`` costs a process launch. The connector page polls, the health
#: snapshot asks on every call, and the answer changes only when someone
#: installs or moves Blender, so the verdict is cached for a minute. The
#: explicit "test" button passes ``force`` and always pays for a fresh one.
_PROBE_TTL_S = 60.0
_probe_cache: dict[str, tuple[float, Dict[str, Any]]] = {}


def probe(path: str = "", *, force: bool = False) -> Dict[str, Any]:
    """Check that a binary exists and is really Blender; report its version.

    Runs ``--version``, which starts and exits in well under a second and does
    not open a scene, so this is safe to call from a config page.
    """
    target = (path or "").strip() or binary_path()
    cached = _probe_cache.get(target)
    if cached and not force and (time.monotonic() - cached[0]) < _PROBE_TTL_S:
        return cached[1]
    if not target:
        return {"ok": False, "path": "", "error": "no Blender binary configured or found"}

    result = _probe_uncached(target)
    _probe_cache[target] = (time.monotonic(), result)
    return result


def _probe_uncached(target: str) -> Dict[str, Any]:
    if not Path(target).exists():
        return {"ok": False, "path": target, "error": f"no such file: {target}"}
    try:
        proc = subprocess.run([target, "--version"], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": target, "error": "timed out running --version"}
    except OSError as exc:
        return {"ok": False, "path": target, "error": str(exc)}
    first = (proc.stdout or "").strip().splitlines()
    version = first[0].strip() if first else ""
    if proc.returncode != 0 or not version.lower().startswith("blender"):
        return {"ok": False, "path": target,
                "error": (proc.stderr or proc.stdout or "unexpected output").strip()[:300]}
    return {"ok": True, "path": target, "version": version}


def public_config() -> Dict[str, Any]:
    """Config as the UI sees it: stored values plus what the machine can do."""
    data = load()
    discovered = discover_binary()
    resolved = (data.get("binary_path") or "").strip() or discovered
    return {**data, "discovered_binary": discovered, "resolved_binary": resolved}


def runtime_dir() -> Path:
    """Where daemon logs and the registry live."""
    path = AGENTS_HUB_ROOT / "blender"
    path.mkdir(parents=True, exist_ok=True)
    return path


def daemons_dir() -> Path:
    """The daemon registry: one JSON record per running engine.

    On disk rather than in memory because agents run in their own processes
    (subprocess, container or node). A pool that only knew about the engines
    *this* process started would show an operator an empty page while four
    Blenders ran, and would start a second engine for a scene that already has
    one — two processes, one scene, divergent geometry.
    """
    path = runtime_dir() / "daemons"
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["MODES", "load", "save", "probe", "discover_binary", "binary_path",
           "public_config", "runtime_dir", "daemons_dir"]
