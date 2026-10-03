"""Plan and apply: compare a bundle with the hub, then make the hub match it.

:func:`plan` only reads. For every declared resource it reads the hub record
the lock points at (or looks for one it would collide with), puts the declared
and the observed value of every declared field into one canonical shape, and
decides:

``create``     not in the lock, or the lock's record is gone from the hub
``update``     some declared field differs from the hub
``unchanged``  every declared field matches (or is the hub's normal form of
               what the last apply wrote, see declarative/lock.py)
``drift``      the hub record changed since the last apply in a field the
               file would now overwrite; refused unless ``force``
``blocked``    a field that only creation sets differs, a reference points at
               nothing, the record is read only in the hub, or an id is taken
``delete``     in the lock, no longer declared, and ``prune`` asked for it
``orphan``     in the lock, no longer declared, kept (no ``prune``)
``forget``     as ``delete``, but the hub record is already gone

:func:`apply` refuses a plan with ``drift`` or ``blocked`` rows, so a file
with a problem writes nothing. It then creates and updates kind by kind
(memory pools, environments, agents, deployments: each refers only to kinds
before it), every create of a kind before the field writes of that kind, so
two agents that hand off to each other both exist when their handoffs are
set. Deletes run last, in the reverse order. The lock is updated after every
resource, so a failure halfway leaves it naming exactly what exists: a created
record is in it even when one of its field writes failed, and that field is
left unrecorded so the next plan offers it again.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from declarative.errors import PlanBlocked
from declarative.kinds import KIND_ORDER, KINDS, PENDING, Context, Kind, is_not_found
from declarative.lock import Lock, value_hash
from declarative.parser import Bundle, Resource

log = logging.getLogger(__name__)

Request = Callable[..., Any]

#: Actions that make the plan refuse to apply.
BLOCKING = ("drift", "blocked")
#: Actions that write to the hub.
WRITING = ("create", "update", "delete")


@dataclass
class Change:
    """One row of a plan."""
    address: str
    kind: str
    key: str
    action: str
    hub_id: Optional[str] = None
    fields: List[str] = field(default_factory=list)
    drift: List[str] = field(default_factory=list)
    message: str = ""
    source: Optional[str] = None
    line: Optional[int] = None
    version: Any = None
    locked_version: Any = None
    # What the plan read, reused by apply for a resource it leaves alone.
    observed: Optional[Dict[str, Any]] = field(default=None, repr=False)
    desired: Optional[Dict[str, Any]] = field(default=None, repr=False)

    @property
    def where(self) -> str:
        if not self.source:
            return ""
        return f"{self.source}:{self.line}" if self.line else self.source

    def to_dict(self) -> Dict[str, Any]:
        return {"address": self.address, "kind": self.kind, "key": self.key, "action": self.action,
                "hub_id": self.hub_id, "fields": list(self.fields), "drift": list(self.drift),
                "message": self.message, "source": self.where or None, "version": self.version,
                "locked_version": self.locked_version}


@dataclass
class Plan:
    """What :func:`apply` will do, and whether it may."""
    changes: List[Change]
    bundle: Bundle
    workspace: Optional[str] = None
    prune: bool = False
    force: bool = False
    problems: List[str] = field(default_factory=list)

    @property
    def blocking(self) -> List[Change]:
        return [c for c in self.changes if c.action in BLOCKING]

    @property
    def ok(self) -> bool:
        return not self.blocking and not self.problems

    @property
    def has_changes(self) -> bool:
        return any(c.action in WRITING or c.action == "forget" for c in self.changes)

    @property
    def deletes(self) -> List[Change]:
        return [c for c in self.changes if c.action == "delete"]

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in self.changes:
            out[c.action] = out.get(c.action, 0) + 1
        return out

    def explain(self) -> str:
        """Why the plan cannot be applied, one line per reason."""
        lines = list(self.problems)
        for c in self.blocking:
            where = f"{c.where}: " if c.where else ""
            lines.append(f"{where}{c.address}: {c.message}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"workspace": self.workspace, "prune": self.prune, "force": self.force,
                "ok": self.ok, "problems": list(self.problems), "counts": self.counts(),
                "changes": [c.to_dict() for c in self.changes]}


@dataclass
class Result:
    """What :func:`apply` did."""
    applied: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "applied": list(self.applied), "failed": list(self.failed)}


def _ws(value: Optional[str]) -> str:
    return (value or "").strip() or "default"


def _has_pending(value: Any) -> bool:
    return PENDING in json.dumps(value, default=str)


def _compare(kind: Kind, desired: Dict[str, Any], observed: Dict[str, Any],
             recorded: Dict[str, Dict[str, str]]) -> Tuple[List[str], List[str]]:
    """``(changed, drifted)`` field names. A field is unchanged when the two
    values match, or when they are exactly the pair the last apply recorded
    (the hub keeps its own normal form of what it was given). It drifted
    when the hub value is no longer the one the last apply left."""
    changed: List[str] = []
    drifted: List[str] = []
    for name, want in desired.items():
        have = kind.view(name, want, observed.get(name))
        dh, oh = value_hash(want), value_hash(have)
        if dh == oh:
            continue
        rec = recorded.get(name) or {}
        if rec.get("d") == dh and rec.get("o") == oh:
            continue
        changed.append(name)
        if rec and rec.get("o") != oh:
            drifted.append(name)
    return changed, drifted


def _field_records(kind: Kind, desired: Dict[str, Any], observed: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {name: {"d": value_hash(want), "o": value_hash(kind.view(name, want, observed.get(name)))}
            for name, want in desired.items()}


def _ordered(items: List[Any], key: Callable[[Any], str]) -> List[Any]:
    rank = {k: i for i, k in enumerate(KIND_ORDER)}
    return sorted(items, key=lambda x: rank.get(key(x), len(rank)))


def _show(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def plan(bundle: Bundle, request: Request, lock: Lock, workspace: Optional[str] = None, *,
         prune: bool = False, force: bool = False) -> Plan:
    """Compare ``bundle`` with the hub behind ``request``. Reads only."""
    result = Plan(changes=[], bundle=bundle, workspace=workspace, prune=prune, force=force)
    if len(lock) and lock.workspace is not None and _ws(lock.workspace) != _ws(workspace):
        result.problems.append(
            f"the lock was applied to workspace '{_ws(lock.workspace)}', not '{_ws(workspace)}'; "
            "pass --workspace to match it, or use another --lock for this workspace")
        return result
    ctx = Context(request, workspace, bundle, lock)

    for res in _ordered(list(bundle), key=lambda r: r.kind):
        result.changes.append(_plan_one(ctx, res, lock.get(res.address), force))

    declared = set(bundle.addresses())
    leftovers = [a for a in lock if a not in declared]
    for address in _ordered(leftovers, key=lambda a: (lock.get(a) or {}).get("kind", "")):
        entry = lock.get(address) or {}
        kind = KINDS.get(entry.get("kind", ""))
        change = Change(address=address, kind=entry.get("kind", ""), key=entry.get("key", ""),
                        action="orphan", hub_id=entry.get("id"), source=entry.get("source"),
                        locked_version=entry.get("version"))
        if kind is None:
            change.action, change.message = "blocked", f"unknown kind '{change.kind}' in the lock"
        elif not prune:
            change.message = "no longer declared; kept (pass --prune to delete it)"
        elif entry.get("id") and kind.exists(ctx, entry["id"]):
            change.action, change.message = "delete", "no longer declared"
        else:
            change.action, change.message = "forget", "no longer declared and already gone from the hub"
        result.changes.append(change)
    return result


def _plan_one(ctx: Context, res: Resource, entry: Optional[Dict[str, Any]], force: bool) -> Change:
    kind = KINDS[res.kind]
    change = Change(address=res.address, kind=res.kind, key=res.key, action="unchanged",
                    source=res.source, line=res.line,
                    hub_id=(entry or {}).get("id"), locked_version=(entry or {}).get("version"))

    for name, ref_kind, value in kind.refs(res):
        if ctx.declared(ref_kind, value):
            continue
        try:
            found = ctx.exists(ref_kind, value)
        except Exception as exc:  # noqa: BLE001 - reported on the row, the plan goes on
            log.debug("apply: checking %s '%s' failed", ref_kind, value, exc_info=True)
            found, change.message = False, f"could not check {name} '{value}': {exc}"
        if not found:
            change.action = "blocked"
            change.line = res.field_lines.get(name, res.line)
            change.message = change.message or (
                f"{name}: {ref_kind} '{value}' is not declared here and does not exist in the hub")
            return change

    desired = kind.desired(ctx, res)
    change.desired = desired
    observed = kind.observe(ctx, change.hub_id, res) if change.hub_id else None
    if observed is None:
        if entry is None:
            taken = kind.find_existing(ctx, res, desired)
            if taken:
                change.action = "blocked"
                change.hub_id = taken
                article = "an" if res.kind[0] in "aeiou" else "a"
                change.message = (f"{article} {res.kind.replace('_', ' ')} like this already exists in the hub ({taken}) and the "
                                  f"lock does not own it; export it with `ah apply --export` to take "
                                  "it over, or declare another id")
                return change
        change.action = "create"
        change.hub_id = None
        change.fields = list(desired)
        if entry is not None:
            change.message = "in the lock but gone from the hub; created again"
        return change

    change.observed = observed
    changed, drifted = _compare(kind, desired, observed, (entry or {}).get("fields") or {})
    fmap = kind.field_map
    fixed = [n for n in changed if fmap[n].create_only]
    if fixed:
        change.action = "blocked"
        change.fields = fixed
        change.line = res.field_lines.get(fixed[0], res.line)
        detail = ", ".join(f"{n} (hub {_show(kind.view(n, desired[n], observed.get(n)))}, "
                           f"file {_show(desired[n])})" for n in fixed)
        change.message = f"only set when the {res.kind} is created, cannot change in place: {detail}"
        return change
    why = kind.blocked(observed, changed)
    if why:
        change.action, change.fields, change.message = "blocked", changed, why
        return change
    if not changed:
        return change
    change.fields = changed
    change.action = "update"
    if drifted:
        change.drift = drifted
        try:
            change.version = kind.version(ctx, change.hub_id, observed)
        except Exception:  # noqa: BLE001 - the version only decorates the message
            log.debug("apply: no version for %s", res.address, exc_info=True)
        moved = (f"; version {change.locked_version} -> {change.version}"
                 if change.version is not None and change.version != change.locked_version else "")
        names = ", ".join(drifted)
        if force:
            change.message = f"changed in the hub since the last apply ({names}{moved}); overwritten (--force)"
        else:
            change.action = "drift"
            change.message = (f"changed in the hub since the last apply ({names}{moved}); "
                              "apply with --force to overwrite, or export it to take the hub's version")
    return change


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def apply(plan: Plan, request: Request, lock: Lock) -> Result:
    """Carry out ``plan`` against the hub behind ``request``, recording each
    resource in ``lock`` as it lands. The caller saves the lock, also when
    the result reports failures: it then names what was really created."""
    if not plan.ok:
        raise PlanBlocked(plan.explain())
    ctx = Context(request, plan.workspace, plan.bundle, lock)
    lock.workspace = _ws(plan.workspace)
    result = Result()
    failed: set = set()

    work = [c for c in plan.changes if c.action in ("create", "update", "unchanged")]
    for kind_name in KIND_ORDER:
        kind = KINDS[kind_name]
        rows = [c for c in work if c.kind == kind_name]
        for change in rows:
            if change.action == "create":
                if not _create(ctx, kind, plan.bundle.get(change.kind, change.key), change, lock, result):
                    failed.add(change.address)
        for change in rows:
            if change.address in failed:
                continue
            res = plan.bundle.get(change.kind, change.key)
            if change.action == "unchanged":
                _record_unchanged(ctx, kind, res, change, lock)
            else:
                _converge(ctx, kind, res, change, lock, result, force=plan.force)

    for kind_name in reversed(KIND_ORDER):
        for change in plan.changes:
            if change.kind != kind_name or change.action not in ("delete", "forget"):
                continue
            if change.action == "delete":
                try:
                    KINDS[kind_name].delete(ctx, change.hub_id)
                except Exception as exc:  # noqa: BLE001 - one failed delete must not stop the others
                    if not is_not_found(exc):
                        log.warning("apply: deleting %s failed", change.address, exc_info=True)
                        result.failed.append({"address": change.address, "action": "delete",
                                              "error": str(exc)})
                        continue
            lock.remove(change.address)
            result.applied.append({"address": change.address, "action": change.action,
                                   "hub_id": change.hub_id, "fields": []})
    return result


def _create(ctx: Context, kind: Kind, res: Resource, change: Change, lock: Lock, result: Result) -> bool:
    try:
        desired = kind.desired(ctx, res)
        if _has_pending(desired):
            raise ValueError("a resource it refers to was not created")
        hub_id = kind.create(ctx, res, desired)
    except Exception as exc:  # noqa: BLE001 - recorded on the result, the apply goes on
        log.warning("apply: creating %s failed", res.address, exc_info=True)
        result.failed.append({"address": res.address, "action": "create", "error": str(exc)})
        return False
    change.hub_id = hub_id
    ctx.ids[res.address] = hub_id
    # In the lock straight away: whatever happens to its fields next, the
    # record exists and the next apply must update it, not make another.
    lock.record(res.address, kind=res.kind, key=res.key, hub_id=hub_id,
                spec_hash=value_hash(res.spec), fields={}, source=res.source)
    return True


def _record_unchanged(ctx: Context, kind: Kind, res: Resource, change: Change, lock: Lock) -> None:
    entry = lock.get(res.address) or {}
    observed = change.observed or {}
    desired = change.desired if change.desired is not None else kind.desired(ctx, res)
    records = dict(entry.get("fields") or {})
    for name, rec in _field_records(kind, desired, observed).items():
        # A field the lock already has stays as recorded (it may be the
        # hub's normal form of an earlier write); a newly declared field
        # that already matches is taken in as it is.
        records.setdefault(name, rec)
    records = {k: v for k, v in records.items() if k in desired}
    version = entry.get("version")
    if version is None:
        version = observed.get("_version")
    lock.record(res.address, kind=res.kind, key=res.key, hub_id=change.hub_id or entry.get("id"),
                spec_hash=value_hash(res.spec), fields=records, version=version, source=res.source)


def _converge(ctx: Context, kind: Kind, res: Resource, change: Change, lock: Lock,
              result: Result, *, force: bool) -> None:
    hub_id = change.hub_id or ctx.ids.get(res.address)
    action = change.action
    entry = lock.get(res.address) or {}
    recorded = dict(entry.get("fields") or {})
    written: List[str] = []
    error: Optional[Exception] = None
    changed: List[str] = []
    after: Optional[Dict[str, Any]] = None
    desired: Dict[str, Any] = {}
    try:
        desired = kind.desired(ctx, res)
        if _has_pending(desired):
            raise ValueError("a resource it refers to was not created")
        observed = kind.observe(ctx, hub_id, res)
        if observed is None:
            raise ValueError("the hub record disappeared while applying")
        changed, drifted = _compare(kind, desired, observed, recorded)
        if drifted and not force and action == "update":
            raise ValueError(f"changed in the hub since the plan was made ({', '.join(drifted)})")
        for group in kind.write_groups(changed):
            kind.write(ctx, hub_id, res, {n: desired[n] for n in group}, observed)
            written.extend(group)
        after = kind.observe(ctx, hub_id, res) if changed else observed
    except Exception as exc:  # noqa: BLE001 - recorded on the result, the apply goes on
        log.warning("apply: %s of %s failed", action, res.address, exc_info=True)
        error = exc
        try:
            after = kind.observe(ctx, hub_id, res) if hub_id else None
        except Exception:  # noqa: BLE001 - the lock then keeps what it had for this resource
            log.debug("apply: re-reading %s failed", res.address, exc_info=True)
            after = None

    if after is not None and desired:
        fresh = _field_records(kind, desired, after)
        records: Dict[str, Dict[str, str]] = {}
        for name in desired:
            if name in changed and name not in written:
                if name in recorded:
                    records[name] = recorded[name]  # not written: the next plan offers it again
                continue
            records[name] = fresh[name]
        try:
            version = kind.version(ctx, hub_id, after)
        except Exception:  # noqa: BLE001 - a lock without a version still drives the next plan
            log.debug("apply: no version for %s", res.address, exc_info=True)
            version = None
        lock.record(res.address, kind=res.kind, key=res.key, hub_id=hub_id,
                    spec_hash=value_hash(res.spec), fields=records, version=version, source=res.source)

    if error is not None:
        result.failed.append({"address": res.address, "action": action, "error": str(error),
                              "written": written})
    else:
        result.applied.append({"address": res.address, "action": action, "hub_id": hub_id,
                               "fields": written})


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------


def adopt(bundle: Bundle, request: Request, lock: Lock, ids: Dict[str, str],
          workspace: Optional[str] = None) -> List[str]:
    """Record existing hub records in ``lock`` as owned by ``bundle``, as they
    are now: ``ids`` maps a declared address to its hub id. Used by
    ``ah apply --export``, so a plan right after an export reads unchanged.
    Returns the addresses recorded."""
    ctx = Context(request, workspace, bundle, lock)
    ctx.ids.update(ids)
    lock.workspace = _ws(workspace)
    done: List[str] = []
    for res in _ordered(list(bundle), key=lambda r: r.kind):
        hub_id = ids.get(res.address)
        if not hub_id:
            continue
        kind = KINDS[res.kind]
        observed = kind.observe(ctx, hub_id, res)
        if observed is None:
            continue
        desired = kind.desired(ctx, res)
        lock.record(res.address, kind=res.kind, key=res.key, hub_id=hub_id,
                    spec_hash=value_hash(res.spec), fields=_field_records(kind, desired, observed),
                    version=kind.version(ctx, hub_id, observed), source=res.source)
        done.append(res.address)
    return done


__all__ = ["Change", "Plan", "Result", "plan", "apply", "adopt", "BLOCKING"]
