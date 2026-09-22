"""Per-workspace outbound notification endpoints and alert rules.

Stored inside the workspace's metadata, the same way ``mcp_client.store`` keeps
its server list: a hub has a handful of endpoints and rules, they change
rarely, and living inside the workspace record means they follow the
workspace without a store of their own to migrate.

Two lists live here, under separate metadata keys.

``notify_endpoints``
    Where an outbound event can be delivered::

        {"id", "kind": "webhook"|"slack", "url", "secret" (webhook only),
         "events": [...], "enabled"}

``alert_rules``
    When a run or the workspace's spend should raise a notification on its
    own, without an agent or a person asking for it::

        {"id", "kind": "run_failed"|"spend_daily_over"|"spend_run_over",
         "threshold_usd", "agent_id" (optional filter),
         "channels": ["dashboard", "telegram", "slack", "webhook"], "enabled",
         "last_fired_date"}

**Secrets.** A webhook's secret is stored as the operator typed it and never
sent back in a GET: :func:`masked_endpoint` replaces it with its last four
characters, the same convention ``mcp_client.store.masked`` uses for headers
and env values.

**Inbound idempotency.** ``inbound_deliveries`` is a small SQLite table, added
lazily the way ``loops/store.py`` adds its ``progress`` column: additive, no
migration, created on first use rather than in the shared schema (which
belongs to someone else this round).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from common import db

log = logging.getLogger(__name__)

ENDPOINTS_KEY = "notify_endpoints"
RULES_KEY = "alert_rules"
# The secret POST /api/webhooks/tasks authenticates against. Separate from a
# webhook endpoint's secret (those sign what this hub sends *out*; this one
# verifies what an external system sends *in*), so it gets its own key.
INBOUND_SECRET_KEY = "notify_inbound_secret"

ENDPOINT_KINDS = ("webhook", "slack")
RULE_KINDS = ("run_failed", "spend_daily_over", "spend_run_over")
CHANNELS = ("dashboard", "telegram", "slack", "webhook")

_MASK = "••••"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_secret(value: Optional[str]) -> str:
    text = value or ""
    return f"{_MASK}{text[-4:]}" if text else ""


def _is_masked(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_MASK)


# ── Endpoints ────────────────────────────────────────────────────────────────

def list_endpoints(workspace: str) -> List[Dict[str, Any]]:
    from workspace import get_workspace_metadata

    meta = get_workspace_metadata(workspace) or {}
    items = meta.get(ENDPOINTS_KEY)
    return list(items) if isinstance(items, list) else []


def get_endpoint(workspace: str, endpoint_id: str) -> Optional[Dict[str, Any]]:
    for item in list_endpoints(workspace):
        if item.get("id") == endpoint_id:
            return item
    return None


def _save_endpoints(workspace: str, items: List[Dict[str, Any]]) -> None:
    from workspace import create_workspace_folder, update_workspace_metadata

    create_workspace_folder(workspace)
    update_workspace_metadata(workspace, {ENDPOINTS_KEY: items})


def create_endpoint(workspace: str, data: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in ENDPOINT_KINDS:
        raise ValueError(f"kind must be one of {ENDPOINT_KINDS}")
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")
    record: Dict[str, Any] = {
        "id": str(uuid4()),
        "kind": kind,
        "url": url,
        "secret": str(data.get("secret") or "") if kind == "webhook" else "",
        "events": list(data.get("events") or ["notification"]),
        "enabled": bool(data.get("enabled", True)),
        "created_at": _utc_now_iso(),
    }
    items = list_endpoints(workspace)
    items.append(record)
    _save_endpoints(workspace, items)
    return record


def update_endpoint(workspace: str, endpoint_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply an edit. A secret sent back masked (unchanged in the UI) keeps
    its stored value, the same round trip ``mcp_client.store`` uses."""
    items = list_endpoints(workspace)
    updated = None
    for index, item in enumerate(items):
        if item.get("id") != endpoint_id:
            continue
        merged = dict(item)
        if "kind" in changes and changes["kind"] in ENDPOINT_KINDS:
            merged["kind"] = changes["kind"]
        for key in ("url", "events", "enabled"):
            if key in changes and changes[key] is not None:
                merged[key] = changes[key]
        if "secret" in changes and changes["secret"] is not None and not _is_masked(changes["secret"]):
            merged["secret"] = str(changes["secret"])
        items[index] = merged
        updated = merged
        break
    if updated is None:
        return None
    _save_endpoints(workspace, items)
    return updated


def delete_endpoint(workspace: str, endpoint_id: str) -> bool:
    items = list_endpoints(workspace)
    kept = [item for item in items if item.get("id") != endpoint_id]
    if len(kept) == len(items):
        return False
    _save_endpoints(workspace, kept)
    return True


def masked_endpoint(record: Dict[str, Any]) -> Dict[str, Any]:
    """One endpoint, safe to send to a browser: its secret down to four chars."""
    out = dict(record or {})
    if out.get("kind") == "webhook":
        out["secret"] = _mask_secret(out.get("secret"))
    else:
        out.pop("secret", None)
    return out


def endpoints_for_event(workspace: str, event_name: str) -> List[Dict[str, Any]]:
    """Every enabled endpoint subscribed to ``event_name``, secrets intact.

    For internal use only (outbound delivery reads the real secret); anything
    that reaches a browser must go through :func:`masked_endpoint` first.
    """
    out = []
    for item in list_endpoints(workspace):
        if not item.get("enabled", True):
            continue
        if event_name in (item.get("events") or []):
            out.append(item)
    return out


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
        for key in ("threshold_usd", "agent_id", "enabled", "last_fired_date"):
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


# ── Inbound secret, for POST /api/webhooks/tasks ────────────────────────────

def get_inbound_secret(workspace: str) -> Optional[str]:
    from workspace import get_workspace_metadata

    meta = get_workspace_metadata(workspace) or {}
    value = meta.get(INBOUND_SECRET_KEY)
    return str(value) if value else None


def set_inbound_secret(workspace: str, secret: Optional[str]) -> None:
    from workspace import create_workspace_folder, update_workspace_metadata

    create_workspace_folder(workspace)
    update_workspace_metadata(workspace, {INBOUND_SECRET_KEY: (secret or None)})


# ── Inbound idempotency ──────────────────────────────────────────────────────
# A small table, added lazily so this feature needs no change to the shared
# schema in common/db.py. Mirrors loops/store.py's `_ensure_progress_column`.

_INBOUND_TABLE_READY: set = set()
_REPLAY_WINDOW_SECONDS = 24 * 3600


def _ensure_inbound_table() -> None:
    key = str(getattr(db, "DB_FILE", ""))
    if key in _INBOUND_TABLE_READY:
        return
    with db.transaction() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS inbound_deliveries ("
            "delivery_id TEXT PRIMARY KEY, seen_at TEXT NOT NULL)"
        )
    _INBOUND_TABLE_READY.add(key)


def record_delivery(delivery_id: str) -> bool:
    """Record an inbound delivery id as seen.

    Returns True when it was already recorded within the last 24 hours (a
    replay) and False the first time. Rows older than the window are swept on
    each call, so the table stays small and an id can be reused once its
    window has passed.
    """
    if not delivery_id:
        return False
    _ensure_inbound_table()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=_REPLAY_WINDOW_SECONDS)).isoformat()
    with db.transaction() as conn:
        conn.execute("DELETE FROM inbound_deliveries WHERE seen_at < ?", (cutoff,))
        row = conn.execute(
            "SELECT 1 FROM inbound_deliveries WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        if row is not None:
            return True
        conn.execute(
            "INSERT INTO inbound_deliveries (delivery_id, seen_at) VALUES (?, ?)",
            (delivery_id, now.isoformat()),
        )
    return False
