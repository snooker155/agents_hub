"""
First-run state bootstrap.

When the runtime state directory (`.agents_hub/`) is empty or missing key
pieces, seed it from the repo-shipped `bootstrap/` folder so the system starts
with the predefined default workspace and core agents (orchestrator,
agent_creator, decomposer).

Operator state is never overwritten. Bootstrap is per-entity additive, with one
deliberate exception (the last bullet):
- `.agents_hub/agents.json` is copied from `bootstrap/agents.json` only when
  it does not exist.
- `.agents_hub/workspaces/default/` is created from
  `bootstrap/workspaces/default/` only when it does not exist.
- system agents shipped in `bootstrap/agents.json` are added to an existing
  registry when missing, so installs predating a new system agent pick it up.
- for system agents that already exist, the fields the seed owns (tools,
  description, tier) are synced from it on every start, so a newly granted tool
  reaches installs that were created before it. Records the operator has edited
  carry `user_modified` and are skipped. See `_sync_system_agents`.
"""
from __future__ import annotations

import shutil

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


def _ensure_system_agents() -> list[str]:
    """Add system agents from bootstrap that are missing from the live
    registry. Additive only: existing agents are left untouched here, and are
    brought up to date separately by :func:`_sync_system_agents`. Returns the
    ids that were added.

    Runs after :func:`_seed_agents_file`, so on a first run (where the registry
    was just copied wholesale) every agent already exists and this is a no-op;
    on an existing install it backfills newly-shipped system agents.
    """
    if not BOOTSTRAP_AGENTS_FILE.is_file():
        return []
    try:
        import json
        from agents.registry import get_agent, add_agent, _validate_agent_dict
        raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    added: list[str] = []
    for ad in (raw.get("agents") or []):
        if not isinstance(ad, dict) or not _is_system_seed(ad):
            continue
        aid = ad.get("id")
        if not aid or get_agent(aid) is not None:
            continue
        try:
            add_agent(_validate_agent_dict(ad), user_edit=False)
            added.append(aid)
        except Exception:
            continue
    return added


def ensure_system_agent(agent_id: str) -> bool:
    """Register one shipped System agent on demand, if it is missing.

    :func:`_ensure_system_agents` backfills the whole set at startup, which
    covers the normal case. This is the on-demand version for a surface that
    *needs* a specific agent the moment it is opened — an entity's build chat
    cannot ask the user to restart the server — so it mirrors that one entry
    from ``bootstrap/agents.json`` instead of hardcoding a second copy of the
    spec next to the route. Returns whether the agent is present afterwards.
    """
    try:
        from agents.registry import get_agent
        if get_agent(agent_id) is not None:
            return True
    except Exception:
        # A missing or unreadable agents.json is a broken install, not a crash
        # for the caller: a surface that needs this agent should report it as
        # unavailable rather than 500 on the lookup itself.
        return False
    if not BOOTSTRAP_AGENTS_FILE.is_file():
        return False
    try:
        import json
        from agents.registry import add_agent, _validate_agent_dict
        raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
        spec = next((a for a in (raw.get("agents") or [])
                     if isinstance(a, dict) and a.get("id") == agent_id), None)
        if not spec:
            return False
        add_agent(_validate_agent_dict(spec), user_edit=False)
        return True
    except Exception:
        return False


# Fields the seed replaces outright on a system agent. `delegates` is here
# because it is part of a system agent's design, not a preference: the
# Researcher is built around calling exactly one other agent, and an empty
# allowlist would silently turn it into a general-purpose delegator. A field
# absent from a seed record is left alone, so this only reaches the agents that
# declare it. Everything else (provider, model, temperature, capacity, memory
# assignment, workspace ownership) is the operator's and is never touched.
# Tools are handled separately, and are merged rather than replaced (see below).
_SEED_OWNED_FIELDS = ("description", "system", "delegates")


def _is_system_seed(ad: dict) -> bool:
    """Whether a bootstrap record describes a system agent.

    Reads the `system` flag, falling back to the legacy `domain == "System"`
    convention so a seed written before the flag still works.
    """
    return bool(ad.get("system", ad.get("domain") == "System"))


def _sync_system_agents() -> list[str]:
    """Bring existing system agents up to date with the shipped seed.

    :func:`_ensure_system_agents` only adds agents that are missing, so an
    install that predates a newly granted tool kept the old tool list forever:
    editing `bootstrap/agents.json` had no effect on anyone who had already run
    the product once. This closes that gap for the fields the seed owns
    (``_SEED_OWNED_FIELDS``).

    Records carrying ``user_modified`` are skipped: once the operator edits a
    system agent through the dashboard or the agent-management tools, that
    record stops tracking the seed. The first sync also writes a one-time
    backup of the registry, because installs that predate the flag cannot
    distinguish a hand-tuned agent from an untouched one.

    Returns the ids that changed.
    """
    import json
    import logging
    import shutil
    from common.paths import AGENTS_FILE

    log = logging.getLogger(__name__)
    if not (AGENTS_FILE.is_file() and BOOTSTRAP_AGENTS_FILE.is_file()):
        return []
    try:
        seed_raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
        live_raw = json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    seed_by_id = {
        a["id"]: a for a in (seed_raw.get("agents") or [])
        if isinstance(a, dict) and a.get("id") and _is_system_seed(a)
    }
    if not seed_by_id:
        return []

    try:
        from tools.capabilities import check_combination
    except Exception:
        check_combination = None  # type: ignore[assignment]

    changed: list[str] = []
    for ad in (live_raw.get("agents") or []):
        if not isinstance(ad, dict):
            continue
        seed = seed_by_id.get(ad.get("id"))
        if seed is None or ad.get("user_modified"):
            continue

        updates = {
            k: seed[k] for k in _SEED_OWNED_FIELDS
            if k in seed and ad.get(k) != seed[k]
        }

        # Tools are merged, never replaced. On an install that predates the
        # `user_modified` flag there is no way to tell a deliberate extra grant
        # from a stale list, and silently revoking a tool the operator relies on
        # is far worse than carrying one they no longer need. Seed order first,
        # so the record still reads like the shipped one.
        live_tools = list(ad.get("tools") or [])
        seed_tools = list(seed.get("tools") or [])
        effective = seed_tools + [t for t in live_tools if t not in seed_tools]

        # The merged set is checked even when it is identical to what is already
        # on disk: a record can already hold a blocked combination (the guard
        # post-dates some installs), and this is the pass that notices.
        #
        # Safety outranks preservation. When the extra local grants are what
        # create the blocked combination but the seed's own set is clean, the
        # seed wins and those grants are dropped — an agent that can both read
        # private data and reach the web is exactly what the guard exists to
        # prevent, so keeping the grant is not an option. Only when the seed
        # itself is blocked does the record go untouched.
        if check_combination is not None and not ad.get("capability_override"):
            violation = check_combination(effective)
            if violation is not None and violation.blocking:
                seed_violation = check_combination(seed_tools)
                if seed_violation is not None and seed_violation.blocking:
                    log.warning(
                        "system agent sync: skipping %r — the seed's own tools form "
                        "a blocked capability combination: %s",
                        ad.get("id"), violation.message,
                    )
                    continue
                log.warning(
                    "system agent sync: %r keeps only the seed's tools; dropping %s. "
                    "Holding them alongside the seed's forms a blocked capability "
                    "combination: %s",
                    ad.get("id"),
                    [t for t in live_tools if t not in seed_tools],
                    violation.message,
                )
                effective = seed_tools

        if effective != live_tools:
            updates["tools"] = effective

        if not updates:
            continue

        # Never write a tool set the capability guard would reject: the sync
        # bypasses registry.add_agent, which is where that check normally runs.
        # Safety outranks preservation here. When merging is what creates the
        # blocked combination but the seed's own set is clean, the seed wins and
        # the extra local grants are dropped — an agent that can both read
        # private data and reach the web is exactly what the guard exists to
        # prevent, so keeping the local grant is not an option. Only when the
        # seed itself is blocked does the record go untouched.
        if "tools" in updates and check_combination is not None and not ad.get("capability_override"):
            violation = check_combination(updates["tools"])
            if violation is not None and violation.blocking:
                seed_tools = list(seed.get("tools") or [])
                seed_violation = check_combination(seed_tools)
                if seed_violation is not None and seed_violation.blocking:
                    log.warning(
                        "system agent sync: skipping %r — the seed's own tools form "
                        "a blocked capability combination: %s",
                        ad.get("id"), violation.message,
                    )
                    continue
                dropped = [t for t in (ad.get("tools") or []) if t not in seed_tools]
                log.warning(
                    "system agent sync: %r keeps only the seed's tools; dropping %s. "
                    "Merging them with the seed would form a blocked capability "
                    "combination: %s",
                    ad.get("id"), dropped, violation.message,
                )
                updates["tools"] = seed_tools

        if "tools" in updates:
            log.info(
                "system agent sync: %r tools %d -> %d",
                ad.get("id"), len(ad.get("tools") or []), len(updates["tools"]),
            )
        ad.update(updates)
        changed.append(str(ad.get("id")))

    if not changed:
        return []

    # One-time safety net: the pre-sync registry, kept next to the live one.
    backup = AGENTS_FILE.with_suffix(".json.pre-sync-backup")
    if not backup.exists():
        try:
            shutil.copyfile(AGENTS_FILE, backup)
            log.warning(
                "system agent sync: wrote a one-time registry backup to %s "
                "before updating %d agent(s) from the seed.", backup, len(changed),
            )
        except Exception:
            pass

    AGENTS_FILE.write_text(
        json.dumps(live_raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        from agents.registry import _REGISTRY_CACHE
        _REGISTRY_CACHE["mtime"] = None
    except Exception:
        pass
    return changed


def _grandfather_capability_violations() -> list[str]:
    """Stamp pre-existing capability-guard violators with ``capability_override``.

    The guard (``tools/capabilities.py``) hard-blocks by default. Turning it on
    over a roster that predates it would break agents that already hold a
    blocked tool combination — typically anything granted ``run_shell``, which
    is the whole trifecta on its own. Rather than fail those agents at build
    time, this one-time migration records the operator's implicit prior consent
    as an explicit ``capability_override`` on the record, and logs each one so
    the decision is visible rather than silent.

    Idempotent: an agent that already carries the override, or that no longer
    violates, is left alone. New violations still hard-block — the override is
    only granted to combinations that were already on disk.
    """
    import json
    import logging
    from common.paths import AGENTS_FILE

    log = logging.getLogger(__name__)
    if not AGENTS_FILE.is_file():
        return []
    try:
        from tools.capabilities import check_combination
        raw = json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    stamped: list[str] = []
    for ad in (raw.get("agents") or []):
        if not isinstance(ad, dict) or ad.get("capability_override"):
            continue
        violation = check_combination(ad.get("tools") or [])
        if violation is None or not violation.blocking:
            continue
        ad["capability_override"] = True
        stamped.append(str(ad.get("id")))
        log.warning(
            "capability guard: grandfathering existing agent %r — %s "
            "Review its tool grants, or run it container-isolated.",
            ad.get("id"), violation.message,
        )

    if stamped:
        AGENTS_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            from agents.registry import _REGISTRY_CACHE
            _REGISTRY_CACHE["mtime"] = None
        except Exception:
            pass
    return stamped


def ensure_initial_state() -> dict[str, bool]:
    """Seed missing first-run state from `bootstrap/`. Returns what was seeded."""
    result = {
        "agents_file": _seed_agents_file(),
        "default_workspace": _seed_default_workspace(),
    }
    result["system_agents_added"] = bool(_ensure_system_agents())
    result["system_agents_synced"] = bool(_sync_system_agents())
    result["capabilities_grandfathered"] = bool(_grandfather_capability_violations())
    return result
