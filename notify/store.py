"""Per-workspace alert rules.

Stored inside the workspace's metadata, the way other per-workspace settings
are kept: a hub has a handful of rules, they change rarely, and living
inside the workspace record means they follow the workspace without a store of
their own to migrate.

``alert_rules``
    When a run or the workspace's spend should raise a notification on its
    own, without an agent or a person asking for it::

        {"id", "kind": "run_failed"|"spend_daily_over"|"spend_run_over",
         "threshold_usd", "agent_id" (optional filter),
         "channels": ["dashboard"], "enabled",
         "last_fired_date", "state"}

    A rule fires into the notification inbox (the bell); ``dashboard`` is
    the only channel.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

RULES_KEY = "alert_rules"

RULE_KINDS = ("run_failed", "spend_daily_over", "spend_run_over")
CHANNELS = ("dashboard",)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Alert rules ──────────────────────────────────────────────────────────────

def list_rules(workspace: str) -> List[Dict[str, Any]]:
    from workspace import get_workspace_metadata

    meta = get_workspace_metadata(workspace) or {}
    items = meta.get(RULES_KEY)
    return list(items) if isinstance(items, list) else []


def get_rule(workspace: str, rule_id: str) -> Optional[Dict[str, Any]]:
    for item in list_rules(workspace):
        if item.get("id") == rule_id:
            return item
    return None


def _save_rules(workspace: str, items: List[Dict[str, Any]]) -> None:
    from workspace import create_workspace_folder, update_workspace_metadata

    create_workspace_folder(workspace)
    update_workspace_metadata(workspace, {RULES_KEY: items})


def create_rule(workspace: str, data: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in RULE_KINDS:
        raise ValueError(f"kind must be one of {RULE_KINDS}")
    channels = [c for c in (data.get("channels") or ["dashboard"]) if c in CHANNELS]
    record: Dict[str, Any] = {
        "id": str(uuid4()),
        "kind": kind,
        "threshold_usd": float(data.get("threshold_usd") or 0.0),
        "agent_id": data.get("agent_id") or None,
        "channels": channels or ["dashboard"],
        "enabled": bool(data.get("enabled", True)),
        "last_fired_date": None,
        "state": None,
        "created_at": _utc_now_iso(),
    }
    items = list_rules(workspace)
    items.append(record)
    _save_rules(workspace, items)
    return record


def update_rule(workspace: str, rule_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    items = list_rules(workspace)
    updated = None
    for index, item in enumerate(items):
        if item.get("id") != rule_id:
            continue
        merged = dict(item)
        if "kind" in changes and changes["kind"] in RULE_KINDS:
            merged["kind"] = changes["kind"]
        for key in ("threshold_usd", "agent_id", "enabled", "last_fired_date", "state"):
            if key in changes and changes[key] is not None:
                merged[key] = changes[key]
        if "channels" in changes and changes["channels"] is not None:
            merged["channels"] = [c for c in changes["channels"] if c in CHANNELS] or ["dashboard"]
        items[index] = merged
        updated = merged
        break
    if updated is None:
        return None
    _save_rules(workspace, items)
    return updated


def delete_rule(workspace: str, rule_id: str) -> bool:
    items = list_rules(workspace)
    kept = [item for item in items if item.get("id") != rule_id]
    if len(kept) == len(items):
        return False
    _save_rules(workspace, kept)
    return True
