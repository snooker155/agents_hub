"""
Owner resolution: whose run a freshly created view belongs to.

A view is always made *by* some run, but which run should own it is not
always the one whose Python frame called ``create_view``. Inside a flow node,
a team member's turn or a scenario decision, that frame belongs to a leaf
agent run (a row in ``runs``) with a ``parent_run_id`` pointing at the flow/
team/scenario run in ``entity_runs`` (common/entity_runs.py), and it is that
parent a person actually opens to see the work, not the leaf turn buried
inside it. A process launched directly for an entity run (runtime/
entity_launch.py) skips that lookup entirely: its environment already says
which entity run it is.

:func:`current_owner` is the one place this is decided, so every view-creating
surface (the ``create_view`` tool, the inline ``<<<ui>>>`` publish path, the
Studio) agrees on it without each re-deriving the rule.
"""
from __future__ import annotations

import os
from typing import Optional

from views.models import ViewOwner

#: Kinds a process can be launched for directly (runtime/entity_launch.py).
#: "loop" is not among them today (loops/launcher.py runs its own way), but a
#: loop run can still end up as a view's owner via the parent_run_id path
#: below, so it stays a valid ViewOwner.kind.
_ENV_ENTITY_KINDS = ("flow", "loop", "team", "scenario")


def current_owner(
    explicit: Optional[ViewOwner] = None, *, leaf_run_id: Optional[str] = None
) -> Optional[ViewOwner]:
    """Resolve the (kind, id) of the run a new view should be owned by.

    Precedence:

    1. ``explicit``: the caller already knows the owner (e.g. a view created
       on behalf of another run); always wins.
    2. The entity run this *process* was launched for
       (``AGENTS_HUB_ENTITY_RUN_ID`` / ``AGENTS_HUB_ENTITY_RUN_KIND``, set by
       runtime/entity_launch.py): the view belongs to the flow/team/scenario
       run itself, whichever leaf inside it is doing the creating.
    3. The current leaf run: ``leaf_run_id`` if the caller already knows it
       (e.g. views.publish, which is handed the run id of the reply being
       published), else a delegated run's id via
       common.stream_sink.current_run_id(), else this process's own
       ``AGENT_RUN_ID``. If that leaf run has a ``parent_run_id`` naming an
       entity run, ownership climbs to that entity run: a flow node, a team
       member's turn and a scenario decision are all leaves of one run a
       person watches as a whole. Otherwise the leaf run itself owns the view,
       as kind ``"run"``.
    4. ``None`` when no run is known at all (e.g. a script run outside any
       tracked run): the caller stores the view without an owner.
    """
    if explicit is not None:
        return explicit

    entity_run_id = os.environ.get("AGENTS_HUB_ENTITY_RUN_ID")
    entity_run_kind = os.environ.get("AGENTS_HUB_ENTITY_RUN_KIND")
    if entity_run_id and entity_run_kind in _ENV_ENTITY_KINDS:
        return ViewOwner(kind=entity_run_kind, id=entity_run_id)

    leaf = (leaf_run_id or "").strip() or _current_leaf_run_id()
    if not leaf:
        return None

    owner = _entity_owner_of_leaf(leaf)
    if owner is not None:
        return owner

    return ViewOwner(kind="run", id=leaf)


def _current_leaf_run_id() -> Optional[str]:
    """The run id of whichever agent run is currently executing, in-process."""
    from common.stream_sink import current_run_id
    return current_run_id() or os.environ.get("AGENT_RUN_ID") or None


def _entity_owner_of_leaf(leaf_run_id: str) -> Optional[ViewOwner]:
    """The entity run ``leaf_run_id`` runs inside, if any.

    Best-effort: an unknown run id, a missing parent, or a parent of a kind
    this envelope doesn't recognize all fall back to ``None`` (leaf ownership)
    rather than raising. A view is worth keeping even when its owner can't be
    fully resolved.
    """
    try:
        from managers.run_manager import get_run_by_id
        from common import entity_runs
    except Exception:
        return None
    run = get_run_by_id(leaf_run_id) or {}
    parent_run_id = run.get("parent_run_id")
    if not parent_run_id:
        return None
    parent = entity_runs.get(parent_run_id)
    if not parent:
        return None
    kind = parent.get("kind")
    if kind not in _ENV_ENTITY_KINDS:
        return None
    return ViewOwner(kind=kind, id=str(parent_run_id))


__all__ = ["current_owner"]
