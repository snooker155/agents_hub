"""
The review gate shared by everything that can be published to the
marketplace: agents (``agents/registry.py``), flows (``flow/store.py``) and
skills (``memory/procedural.py``). See docs/registry.md.

One hub toggle, ``AGENTS_HUB_REGISTRY_REQUIRE_REVIEW`` (default off), and the
same four statuses everywhere: ``draft``, ``in_review``, ``approved``,
``rejected``. This module holds the decision logic only, not storage: each
kind keeps its own record shape (a dataclass field on ``AgentSpec``, a key on
a flow dict, a field on the ``Procedure`` model) and calls in here at its own
one write chokepoint (``agents.registry.add_agent``, ``flow.store.save_flow``,
``memory.procedural.ProcedureStore.add``/``update``).

The rule, stated once so the three call sites agree: publishing (a save that
turns ``shared`` from False to True) holds the item at ``in_review`` instead
of listing it, unless the save already set it to something more final
(``in_review``/``rejected``) itself. An admin then approves or rejects. A
later save that changes the item's actual content — a definition, flow nodes/
edges, a skill's steps/text — while it is still ``approved`` and ``shared``
reopens review, because what passed review may not describe the item any
more. Off (the default), ``review_status`` is tracked on every record but
nothing gates on it, so an install that never turns the toggle on sees no
behavior change.
"""
from __future__ import annotations

from typing import Optional

#: Every valid value of a record's ``review_status``.
STATUSES = ("draft", "in_review", "approved", "rejected")


def required() -> bool:
    """Whether publishing must wait on an admin's approval.

    Resolved live from .env (``common.config.live_setting``), not cached at
    import time, so flipping the toggle takes effect on the very next save.
    """
    from common.config import live_setting
    return live_setting("AGENTS_HUB_REGISTRY_REQUIRE_REVIEW", "false").strip().lower() in (
        "1", "true", "yes", "on")


def default_status(stored: Optional[str], shared: bool) -> str:
    """The status a record with no valid stored value gets on load.

    ``approved`` when the record was already ``shared`` (so an install
    upgrading into this feature never drops something already listed on the
    marketplace), ``draft`` otherwise. A stored value that is one of
    :data:`STATUSES` is always returned unchanged.
    """
    if stored in STATUSES:
        return stored
    return "approved" if shared else "draft"


def gate_on_publish(prev_shared: bool, next_shared: bool, current_status: str) -> Optional[str]:
    """What ``review_status`` becomes when a save changes ``shared``, or
    ``None`` to leave it exactly as the caller set it.

    Only fires on the not-shared -> shared transition, only when review is
    required, and never overrides a status the very same save already set to
    ``in_review`` or ``rejected`` (an explicit decision, such as a rejection
    recorded in the same call, must not be clobbered back to ``in_review``
    for no reason — though in practice it already would be).
    """
    if not required():
        return None
    if next_shared and not prev_shared and current_status not in ("in_review", "rejected"):
        return "in_review"
    return None


def gate_on_content_change(
    prev_status: str, current_status: str, shared: bool, changed: bool,
) -> Optional[str]:
    """What ``review_status`` becomes when a save changes an already
    approved, shared item's actual content, or ``None`` to leave it alone.

    Fires only when the content genuinely changed, the item is (still)
    shared, and both the stored and the about-to-be-written status are
    ``approved`` — the last part is what keeps this from firing on the very
    save an admin uses to *record* a decision (which never changes content in
    the same call) or on a save that already moved the status elsewhere.
    """
    if not required():
        return None
    if changed and shared and prev_status == "approved" and current_status == "approved":
        return "in_review"
    return None


__all__ = ["STATUSES", "required", "default_status", "gate_on_publish", "gate_on_content_change"]
