"""
First-run state bootstrap.

When the runtime state directory (`.agents_hub/`) is empty or missing key
pieces, seed it from the repo-shipped `bootstrap/` folder so the system starts
with the predefined default workspace and core agents (orchestrator,
agent_creator, decomposer).

Operator state is never overwritten. Bootstrap is per-entity additive, with one
deliberate exception (the last bullet):
- the agent registry is seeded from `bootstrap/agents.json` only when
  it does not exist.
- `.agents_hub/workspaces/default/` is created from
  `bootstrap/workspaces/default/` only when it does not exist.
- system agents shipped in `bootstrap/agents.json` are added to an existing
  registry when missing, so installs predating a new system agent pick it up.
- an agent that was renamed (`agents.registry.LEGACY_AGENT_IDS`) has its stored
  references rewritten to the new id, once, with a backup written first
  (common/legacy_agent_ids.py). The old id keeps resolving through the alias.
- for system agents that already exist, the fields the seed owns (tools,
  description, tier) are synced from it on every start, so a newly granted tool
  reaches installs that were created before it. Records the operator has edited
  carry `user_modified` and are skipped. See `_sync_system_agents`.
"""
from __future__ import annotations

import logging
import shutil

from common.paths import (
    AGENTS_FILE,
    PROJECT_ROOT,
    WORKSPACES_ROOT,
    ensure_agents_hub_root,
    ensure_workspaces_root,
)


log = logging.getLogger(__name__)

BOOTSTRAP_ROOT = PROJECT_ROOT / "bootstrap"
BOOTSTRAP_AGENTS_FILE = BOOTSTRAP_ROOT / "agents.json"
BOOTSTRAP_WORKSPACES_ROOT = BOOTSTRAP_ROOT / "workspaces"


def _seed_agents_file() -> bool:
    """Seed the registry from ``bootstrap/agents.json`` when it is empty.

    An install that still has a legacy ``agents.json`` is not empty: the
    registry imports that file on first use, before this check runs, so the
    seed only ever lands on a genuinely fresh state directory."""
    if not BOOTSTRAP_AGENTS_FILE.is_file():
        return False
    from agents.registry import load_all_raw, replace_all_raw
    if load_all_raw():
        return False
    try:
        import json
        raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    records = [a for a in (raw.get("agents") or []) if isinstance(a, dict) and a.get("id")]
    if not records:
        return False
    ensure_agents_hub_root()
    replace_all_raw(records)
    return True


def seed_registry_from_bootstrap() -> int:
    """Replace the registry with the shipped seed, whatever it holds now.
    For tests and for a deliberate reset; ``ensure_initial_state`` only seeds
    an empty registry. Returns the number of records loaded."""
    import json
    from agents.registry import replace_all_raw
    raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
    records = [a for a in (raw.get("agents") or []) if isinstance(a, dict) and a.get("id")]
    replace_all_raw(records)
    return len(records)


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
        from agents.registry import get_agent
        raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    added: list[str] = []
    for ad in (raw.get("agents") or []):
        if not isinstance(ad, dict) or not _is_system_seed(ad):
            continue
        aid = ad.get("id")
        if not aid or get_agent(aid) is not None:
            continue
        try:
            _add_seed_record(ad)
            added.append(aid)
        except Exception:  # noqa: BLE001 - one bad seed record must not stop the rest from being added
            log.debug("could not add system agent %r from the seed", aid, exc_info=True)
    return added


def _add_seed_record(ad: dict) -> None:
    """Store one seed record. A record that ``extends`` another is already in
    stored form (its own list deltas, agents/inheritance.py), so it is saved
    as written; any other is a full spec."""
    from agents.registry import add_agent, save_raw, _validate_agent_dict
    spec = _validate_agent_dict(ad)
    if spec.extends:
        save_raw(spec, user_edit=False)
    else:
        add_agent(spec, user_edit=False)


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
    except Exception:  # noqa: BLE001 - an unreadable registry must report "unavailable", not 500
        # An unreadable registry is a broken install, not a crash
        # for the caller: a surface that needs this agent should report it as
        # unavailable rather than 500 on the lookup itself.
        log.debug("registry lookup failed for %s", agent_id, exc_info=True)
        return False
    if not BOOTSTRAP_AGENTS_FILE.is_file():
        return False
    try:
        import json
        raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
        spec = next((a for a in (raw.get("agents") or [])
                     if isinstance(a, dict) and a.get("id") == agent_id), None)
        if not spec:
            return False
        parent = spec.get("extends")
        if parent and parent != agent_id and not ensure_system_agent(str(parent)):
            return False
        _add_seed_record(spec)
        return True
    except Exception:  # noqa: BLE001 - on-demand seed must report "unavailable", not 500
        log.debug("could not seed system agent %s on demand", agent_id, exc_info=True)
        return False


# Fields the seed replaces outright on a system agent. `delegates` is here
# because it is part of a system agent's design, not a preference: the
# Researcher is built around calling exactly one other agent, and an empty
# allowlist would silently turn it into a general-purpose delegator. A field
# absent from a seed record is left alone, so this only reaches the agents that
# declare it. Everything else (provider, model, temperature, capacity, memory
# assignment, workspace ownership) is the operator's and is never touched.
# Tools are handled separately, and are merged rather than replaced (see below).
_SEED_OWNED_FIELDS = ("description", "system", "delegates", "handoffs",
                      # A shipped child (``assistant``): its parent and its
                      # own list deltas are part of its design too.
                      "extends", "list_deltas")


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
    from agents.registry import load_all_raw, replace_all_raw

    if not BOOTSTRAP_AGENTS_FILE.is_file():
        return []
    try:
        seed_raw = json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8"))
        live_raw = {"agents": load_all_raw()}
    except Exception:  # noqa: BLE001 - startup must not raise; the seed's field sync just skips this run
        log.debug("system agent sync: could not read the seed or the registry", exc_info=True)
        return []
    if not live_raw["agents"]:
        return []
    import copy
    before = copy.deepcopy(live_raw["agents"])

    seed_by_id = {
        a["id"]: a for a in (seed_raw.get("agents") or [])
        if isinstance(a, dict) and a.get("id") and _is_system_seed(a)
    }
    if not seed_by_id:
        return []

    try:
        from tools.capabilities import check_combination, is_recognised_tool_id as _recognised
    except ImportError:
        check_combination = None  # type: ignore[assignment]

        def _recognised(_tool_id: str) -> bool:  # without the model, nothing can be called retired
            return True

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
        #
        # The one exception is an id the capability model no longer recognises
        # at all: a tool that was renamed or retired (nodes became instances,
        # so `node_logs` became `instance_logs`). It grants nothing, resolves
        # to nothing at build time and only makes the guard log an unknown id
        # on every build, so carrying it is not preservation, just noise.
        live_tools = list(ad.get("tools") or [])
        seed_tools = list(seed.get("tools") or [])
        extra = [t for t in live_tools if t not in seed_tools]
        retired = [t for t in extra if not _recognised(t)]
        if retired:
            log.warning(
                "system agent sync: %r drops %s — no such tool exists any more.",
                ad.get("id"), retired,
            )
            extra = [t for t in extra if t not in retired]
        effective = seed_tools + extra

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

    # One-time safety net: the pre-sync registry, written out as a file next
    # to where agents.json used to live. A backup is an export, not state.
    backup = AGENTS_FILE.with_suffix(".json.pre-sync-backup")
    if not backup.exists():
        try:
            ensure_agents_hub_root()
            backup.write_text(json.dumps({"agents": before}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
            log.warning(
                "system agent sync: wrote a one-time registry backup to %s "
                "before updating %d agent(s) from the seed.", backup, len(changed),
            )
        except Exception:
            log.exception("system agent sync: could not write the pre-sync backup to %s", backup)

    held = _hold_back_inherited_grants(before, live_raw["agents"], changed)
    replace_all_raw(live_raw["agents"])
    _record_held_back(held)
    return changed


#: List fields a seed update can grow on a system parent and a child can
#: decline with a ``-item`` delta (agents/inheritance.py LIST_FIELDS).
_HOLD_BACK_FIELDS = ("tools", "delegates", "handoffs")


def _hold_back_inherited_grants(before: list, after: list, changed: list) -> dict[str, dict]:
    """Keep an upgrade from handing a child a blocked capability combination.

    Every other save of a parent re-checks its children and is refused when
    one of them would form a blocked combination (agents.registry.add_agent).
    A seed update cannot be refused: it is the product changing. So a child
    that the updated system parent would push over the line instead declines
    what the parent gained in this update: each new item becomes a ``-item``
    delta on the child, which therefore runs exactly as before. Children that
    stay within the guard follow the parent as usual.

    Mutates ``after`` (raw records) and returns ``{child_id: {field: [items]}}``
    for :func:`_record_held_back`. Never raises: on any failure the sync goes
    ahead and the build time guard still stops a child that would violate.
    """
    held: dict[str, dict] = {}
    if not changed:
        return held
    try:
        from agents import inheritance
        from agents.capability_guard import CapabilityViolation, enforce_agent_tools
        from agents.registry import _validate_agent_dict
        from tools.capabilities import secret_grant_ids

        def specs(records):
            out = []
            for rec in records:
                try:
                    out.append(_validate_agent_dict(rec))
                except Exception:  # noqa: BLE001 - an invalid record is skipped here as everywhere else
                    log.debug("hold back: skipping invalid record %r", rec.get("id"), exc_info=True)
            return out

        old_resolved = inheritance.resolve_all(specs(before))
        by_id = {str(rec.get("id")): rec for rec in after if isinstance(rec, dict)}
        children: list[str] = []
        for parent_id in changed:
            for cid in inheritance.unpinned_descendants(parent_id, specs(after)):
                if cid not in children:
                    children.append(cid)

        for cid in children:  # nearest first, so a grandchild sees its parent's hold back
            new_resolved = inheritance.resolve_all(specs(after))
            new, old, rec = new_resolved.get(cid), old_resolved.get(cid), by_id.get(cid)
            if new is None or old is None or rec is None:
                continue
            try:
                enforce_agent_tools(
                    cid, list(new.tools or []) + secret_grant_ids(new.secrets),
                    previous_tools=list(old.tools or []) + secret_grant_ids(old.secrets),
                    override=bool(rec.get("capability_override")),
                    workspace=rec.get("owner_workspace"),
                    delegates=list(new.delegates or []),
                )
                continue
            except CapabilityViolation:
                pass
            deltas = dict(rec.get("list_deltas") or {})
            gained: dict[str, list] = {}
            for field_name in _HOLD_BACK_FIELDS:
                was = list(getattr(old, field_name, None) or [])
                new_items = [x for x in (getattr(new, field_name, None) or []) if x not in was]
                if not new_items:
                    continue
                delta = dict(deltas.get(field_name) or {})
                delta["remove"] = list(delta.get("remove") or []) + [
                    x for x in new_items if x not in (delta.get("remove") or [])]
                delta["add"] = [x for x in (delta.get("add") or []) if x not in new_items]
                deltas[field_name] = delta
                gained[field_name] = new_items
            if gained:
                rec["list_deltas"] = deltas
                held[cid] = gained
                log.warning(
                    "system agent sync: %r keeps its previous setup and declines %s from its "
                    "updated parent, which would have given it a blocked capability combination.",
                    cid, gained,
                )
    except Exception:  # noqa: BLE001 - the sync must go ahead; the build time guard still applies
        log.warning("system agent sync: could not check the children of updated system agents",
                    exc_info=True)
    return held


def _record_held_back(held: dict[str, dict]) -> None:
    """A version history row and an inbox notification for every child the
    upgrade held back, so the decision is visible, not silent."""
    for cid, gained in held.items():
        items = ", ".join(x for values in gained.values() for x in values)
        note = (f"Upgrade: kept the previous setup and declined {items} from the parent, "
                "which would have formed a blocked capability combination.")
        try:
            from agents.versions import snapshot_if_changed
            snapshot_if_changed(cid, actor="upgrade", note=note)
        except Exception:  # noqa: BLE001 - history is best effort
            log.debug("could not snapshot %r after holding it back", cid, exc_info=True)
        try:
            from agents.registry import get_agent_raw
            from plans.service import create_notification
            raw = get_agent_raw(cid)
            create_notification(
                title=f"Agent {cid} did not take its parent's new tools",
                body=(f"After the update its parent gained {items}. Together with this agent's own "
                      "setup that would form a blocked capability combination, so the agent keeps "
                      "working as before without them. Review it on its Inheritance tab."),
                severity="warning",
                source={"kind": "agent", "id": cid},
                workspace=getattr(raw, "owner_workspace", None),
            )
        except Exception:  # noqa: BLE001 - the inbox is best effort
            log.debug("could not notify about holding back %r", cid, exc_info=True)


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
    from agents.registry import load_all_raw, replace_all_raw

    try:
        from tools.capabilities import check_combination
        raw = {"agents": load_all_raw()}
    except Exception:  # noqa: BLE001 - startup must not raise; grandfathering just skips this run
        log.debug("capability grandfathering: could not read the registry", exc_info=True)
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
        replace_all_raw(raw["agents"])
    return stamped


def ensure_initial_state() -> dict[str, bool]:
    """Seed missing first-run state from `bootstrap/`. Returns what was seeded."""
    result = {
        "agents_file": _seed_agents_file(),
        "default_workspace": _seed_default_workspace(),
    }
    # Before missing system agents are added: a renamed agent's old record is
    # renamed in place rather than shadowed by a fresh copy of the seed.
    result["legacy_agent_ids_renamed"] = _rename_legacy_agent_ids()
    result["system_agents_added"] = bool(_ensure_system_agents())
    result["system_agents_synced"] = bool(_sync_system_agents())
    result["role_references_adopted"] = bool(_adopt_role_references())
    result["capabilities_grandfathered"] = bool(_grandfather_capability_violations())
    result["system_workspace"] = _seed_system_workspace()
    result["demo_workspace"] = _seed_demo_workspace()
    return result


#: Marks that :func:`_adopt_role_references` has run on this install.
_ROLE_REFS_MARKER = "role_references_adopted"


def _adopt_role_references() -> bool:
    """Once per install: a system agent the operator edited keeps the ids the
    seed has since replaced with workspace roles (agents/roles.py), since the
    seed sync skips ``user_modified`` records. Where the seed's ``delegates``
    or ``handoffs`` now name ``@coder`` and the record still names the role's
    default agent (``swe_agent``), the id becomes the reference. Nothing else
    the operator chose is touched, and until a workspace binds the role the
    reference reaches the same agent. Never raising."""
    import json
    from datetime import datetime, timezone
    try:
        from common.docstore import DocStore
        from agents.registry import load_all_raw, replace_all_raw
        from agents.roles import ROLES, ref
        markers = DocStore("bootstrap_markers")
        if markers.get(_ROLE_REFS_MARKER):
            return False
        if not BOOTSTRAP_AGENTS_FILE.is_file():
            return False
        seed = {a["id"]: a for a in json.loads(BOOTSTRAP_AGENTS_FILE.read_text(encoding="utf-8")).get("agents") or []
                if isinstance(a, dict) and a.get("id")}
        records = load_all_raw()
        if not records:
            return False
        before = json.loads(json.dumps(records))
        default_to_ref = {r.default: ref(r.id) for r in ROLES}
        changed: list[str] = []
        for rec in records:
            seed_rec = seed.get(rec.get("id"))
            if not isinstance(rec, dict) or seed_rec is None or not _is_system_seed(seed_rec):
                continue
            for fld in ("delegates", "handoffs"):
                live = list(rec.get(fld) or [])
                wanted = set(seed_rec.get(fld) or [])
                new = []
                for aid in live:
                    role_ref = default_to_ref.get(aid)
                    target = role_ref if role_ref and role_ref in wanted else aid
                    if target not in new:
                        new.append(target)
                if new != live:
                    rec[fld] = new
                    changed.append(f"{rec['id']}.{fld}")
        if changed:
            # A backup is an export, not state: next to where agents.json used
            # to live, like the seed sync's.
            backup = AGENTS_FILE.with_suffix(".json.pre-roles-backup")
            if not backup.exists():
                try:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_text(json.dumps({"agents": before}, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
                except OSError:
                    log.exception("workspace roles: could not write the backup to %s", backup)
            replace_all_raw(records)
            log.info("workspace roles: system agents now name roles in %s", ", ".join(changed))
        markers.put(_ROLE_REFS_MARKER, {"at": datetime.now(timezone.utc).isoformat(), "changed": changed})
        return bool(changed)
    except Exception:  # noqa: BLE001 - startup must not raise; the literal ids keep working
        log.debug("role reference adoption skipped", exc_info=True)
        return False


def _rename_legacy_agent_ids() -> bool:
    """Rewrite stored references to a renamed agent's old id
    (common/legacy_agent_ids.py). Idempotent and never raising."""
    try:
        from common.legacy_agent_ids import migrate_legacy_agent_ids
        done = migrate_legacy_agent_ids()
    except Exception:  # noqa: BLE001 - startup must not raise; the registry alias keeps old ids working
        log.debug("legacy agent id rename skipped", exc_info=True)
        return False
    return bool(done.get("documents") or done.get("rows"))


def _seed_system_workspace() -> bool:
    """Seed the system workspace (common/system_workspace.py) when the setting
    is on. Additive and never raising: a failure here must not stop the
    service from starting, it is reported by the doctor instead."""
    try:
        from common.config import settings
        if not bool(getattr(settings, "system_workspace", True)):
            return False
        from common.system_workspace import ensure_system_workspace
        return bool(ensure_system_workspace())
    except Exception:  # noqa: BLE001 - startup must not raise; the doctor reports a missing system workspace
        log.debug("system workspace seed skipped", exc_info=True)
        return False


def _seed_demo_workspace() -> bool:
    """Seed the demo workspace (common/demo_workspace.py) when the setting is
    on. Additive and never raising, like the system workspace."""
    try:
        from common.config import settings
        if not bool(getattr(settings, "demo_workspace", False)):
            return False
        from common.demo_workspace import ensure_demo_workspace
        return bool(ensure_demo_workspace())
    except Exception:  # noqa: BLE001 - startup must not raise; a missing demo is only a missing demo
        log.debug("demo workspace seed skipped", exc_info=True)
        return False
