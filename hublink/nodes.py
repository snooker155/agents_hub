"""
The nodes linked to this hub: the records, their credentials, and what each
one offers (docs/hub-link.md, "Nodes").

A node is another hub, usually one person's own machine, that dials in with
a **node token** cut here (:func:`create_node`, shown once, stored as a
hash). It says hello with what it has (:func:`hello`): the local models it
shares and whether it runs a sandbox. Two things follow from that record:

* its models become a custom backend ``node-<slug>`` whose base URL is this
  hub's own proxy route (``/api/node-link/<id>/v1``), so every model driver
  reaches them the way it reaches any OpenAI-compatible server. The proxy
  checks a **proxy key**, a second credential cut here and kept only in the
  backend record, so nothing on the model path carries the node's token;
* its sandbox is the provider ``node:<id>`` (sandbox/node.py), which an
  environment names to run snippets on that machine.

A node is online while its last poll is younger than
:data:`ONLINE_SECONDS`. The owner of a node may limit it to some workspaces;
the sandbox route refuses a run from any other, since a snippet on someone's
laptop should never be another team's. The models are offered to the whole
catalog: a catalog is hub-wide, and the owner chose to share them.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from common.docstore import DocStore

log = logging.getLogger(__name__)

_store = DocStore("hub_nodes")
_lock = threading.Lock()

TOKEN_PREFIX = "ahnode_"
PROXY_PREFIX = "ahnp_"
ONLINE_SECONDS = 90.0
BACKEND_PREFIX = "node-"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:32] or "node"


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_online(record: Dict[str, Any]) -> bool:
    seen = _parse(record.get("last_seen"))
    return bool(seen) and seen + timedelta(seconds=ONLINE_SECONDS) > _now()


def backend_id(record: Dict[str, Any]) -> str:
    return f"{BACKEND_PREFIX}{record.get('slug') or slugify(record.get('name', ''))}"


# ── records ──────────────────────────────────────────────────────────────────

def list_nodes(*, owner: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for doc in _store.values():
        if not isinstance(doc, dict):
            continue
        if owner is not None and doc.get("owner") != owner:
            continue
        out.append(dict(doc))
    out.sort(key=lambda d: str(d.get("created_at") or ""))
    return out


def get_node(node_id: str) -> Optional[Dict[str, Any]]:
    doc = _store.get(str(node_id or ""))
    return dict(doc) if isinstance(doc, dict) else None


def public(record: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in record.items() if k not in ("token_hash", "proxy_key_hash")}
    out["online"] = is_online(record)
    out["backend_id"] = backend_id(record) if record.get("models") else None
    out["sandbox_provider"] = f"node:{record['id']}" if (record.get("capabilities") or {}).get("sandbox") else None
    return out


def _unique_slug(name: str, *, skip_id: Optional[str] = None) -> str:
    base = slugify(name)
    taken = {d.get("slug") for d in list_nodes() if d.get("id") != skip_id}
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"[:32]
        n += 1
    return slug


def create_node(*, owner: str, name: str, workspaces: Optional[List[str]] = None) -> Tuple[str, Dict[str, Any]]:
    """A new node record and its token, which is returned once."""
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("a node needs a name")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    proxy_key = PROXY_PREFIX + secrets.token_urlsafe(32)
    with _lock:
        node_id = f"node-{secrets.token_hex(6)}"
        record = {
            "id": node_id,
            "name": name,
            "slug": _unique_slug(name),
            "owner": str(owner or ""),
            "token_hash": _hash(token),
            "token_hint": token[-4:],
            "proxy_key_hash": _hash(proxy_key),
            "workspaces": [str(w) for w in workspaces] if workspaces else None,
            "capabilities": {},
            "models": [],
            "host": "",
            "version": "",
            "disabled": False,
            "created_at": _iso(_now()),
            "connected_at": None,
            "last_seen": None,
        }
        _store.put(node_id, record)
    # The proxy key is kept only in the backend record it authenticates; it is
    # written when the node first announces models (``hello``), so it is
    # carried on the record until then.
    _pending_proxy_keys[node_id] = proxy_key
    return token, record


#: Proxy keys of nodes that have not announced models yet (so no backend
#: record holds the key). Process-local; a restart before the first hello
#: means the key is reissued on that hello (``_ensure_proxy_key``).
_pending_proxy_keys: Dict[str, str] = {}


def update_node(node_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
    with _store.transaction():
        doc = _store.get(str(node_id))
        if not isinstance(doc, dict):
            return None
        doc = {**doc, **changes}
        _store.put(str(node_id), doc)
    return dict(doc)


def rotate_token(node_id: str) -> Optional[str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    rec = update_node(node_id, token_hash=_hash(token), token_hint=token[-4:])
    return token if rec else None


def delete_node(node_id: str) -> bool:
    rec = get_node(node_id)
    if rec is None:
        return False
    _remove_backend(rec)
    _pending_proxy_keys.pop(node_id, None)
    return _store.delete(str(node_id))


# ── credentials ──────────────────────────────────────────────────────────────

def looks_like_token(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith(TOKEN_PREFIX)


def resolve_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The node a token belongs to, when it is live, else None."""
    if not looks_like_token(token):
        return None
    digest = _hash(str(token))
    for doc in _store.values():
        if isinstance(doc, dict) and doc.get("token_hash") == digest and not doc.get("disabled"):
            return dict(doc)
    return None


def resolve_proxy_key(node_id: str, key: Optional[str]) -> bool:
    rec = get_node(node_id)
    if rec is None or rec.get("disabled") or not key:
        return False
    return secrets.compare_digest(rec.get("proxy_key_hash") or "", _hash(str(key)))


def touch(node_id: str) -> None:
    update_node(node_id, last_seen=_iso(_now()))


# ── what a node offers ───────────────────────────────────────────────────────

def _self_base() -> str:
    """Where a model driver on this hub (or its workers) reaches the proxy
    route: the public URL when one is set, else this process's own address."""
    try:
        from common.hub_urls import public_base
        base = public_base()
        if base:
            return base
    except Exception:  # noqa: BLE001 - no public URL configured
        log.debug("no public URL configured", exc_info=True)
    from common.hostnet import hub_base_url
    return hub_base_url()


def _ensure_proxy_key(record: Dict[str, Any]) -> str:
    """The proxy key for the backend record: the one minted at creation if this
    process still has it, else the one the existing backend record carries,
    else a fresh one (the hash on the node record is updated to match)."""
    from providers import registry
    key = _pending_proxy_keys.pop(record["id"], None)
    if key:
        return key
    existing = registry.get_backend(backend_id(record))
    if existing and existing.get("api_key") and secrets.compare_digest(
            record.get("proxy_key_hash") or "", _hash(existing["api_key"])):
        return existing["api_key"]
    key = PROXY_PREFIX + secrets.token_urlsafe(32)
    update_node(record["id"], proxy_key_hash=_hash(key))
    record["proxy_key_hash"] = _hash(key)
    return key


def _sync_backend(record: Dict[str, Any]) -> None:
    """The custom backend and the catalog rows for the node's shared models."""
    from providers import registry
    from providers.catalog import load_catalog_raw, save_catalog_raw

    models = [m for m in record.get("models") or [] if isinstance(m, dict) and m.get("id")]
    bid = backend_id(record)
    if not models:
        _remove_backend(record)
        return
    key = _ensure_proxy_key(record)
    wanted = {
        "id": bid,
        "label": f"Node: {record.get('name') or bid}",
        "adapter": "openai",
        "base_url": f"{_self_base()}/api/node-link/{record['id']}/v1",
        "api_key": key,
        "default_model": str(models[0]["id"]),
    }
    current = registry.get_backend(bid)
    if not current or any(current.get(k) != v for k, v in wanted.items()):
        registry.upsert_backend({**(current or {}), **wanted})

    raw = load_catalog_raw()
    catalog = dict(raw) if isinstance(raw, dict) else {}
    entry = catalog.get(bid)
    old_rows = {m.get("id"): m for m in ((entry or {}).get("models") or []) if isinstance(m, dict)}
    rows = []
    for m in models:
        row = dict(old_rows.get(m["id"]) or {
            "id": m["id"], "enabled": True, "input_price": 0.0, "output_price": 0.0,
            "cached_input_price": 0.0, "price_source": "auto", "released_at": 0,
        })
        if m.get("context_window"):
            row["context_window"] = int(m["context_window"])
        rows.append(row)
    default = str((entry or {}).get("default") or "")
    if default not in {r["id"] for r in rows}:
        default = next((r["id"] for r in rows if r.get("enabled")), rows[0]["id"])
    new_entry = {"default": default, "models": rows}
    if entry != new_entry:
        catalog[bid] = new_entry
        save_catalog_raw(catalog)


def _remove_backend(record: Dict[str, Any]) -> None:
    from providers import registry
    from providers.catalog import load_catalog_raw, save_catalog_raw
    bid = backend_id(record)
    try:
        registry.delete_backend(bid)
    except Exception:  # noqa: BLE001 - a snapshot refuses writes; nothing to remove then
        log.debug("could not remove node backend %s", bid, exc_info=True)
    raw = load_catalog_raw()
    if isinstance(raw, dict) and bid in raw:
        catalog = dict(raw)
        catalog.pop(bid, None)
        try:
            save_catalog_raw(catalog)
        except Exception:  # noqa: BLE001 - same: a read-only catalog keeps the row, harmless
            log.debug("could not prune catalog entry %s", bid, exc_info=True)


def hello(record: Dict[str, Any], announcement: Dict[str, Any]) -> Dict[str, Any]:
    """Record what a node announced and register what it offers."""
    models = []
    for m in announcement.get("models") or []:
        if isinstance(m, dict) and m.get("id"):
            models.append({"id": str(m["id"])[:200],
                           "context_window": int(m.get("context_window") or 0),
                           "label": str(m.get("label") or "")[:200]})
        elif isinstance(m, str) and m.strip():
            models.append({"id": m.strip()[:200], "context_window": 0, "label": ""})
    caps = announcement.get("capabilities") or {}
    changes = {
        "models": models[:200],
        "capabilities": {
            "sandbox": bool(caps.get("sandbox")),
            "sandbox_provider": str(caps.get("sandbox_provider") or "")[:40],
            "docker": bool(caps.get("docker")),
        },
        "host": str(announcement.get("host") or "")[:120],
        "version": str(announcement.get("version") or "")[:40],
        "connected_at": record.get("connected_at") or _iso(_now()),
        "last_seen": _iso(_now()),
    }
    rec = update_node(record["id"], **changes) or {**record, **changes}
    try:
        _sync_backend(rec)
    except Exception:  # noqa: BLE001 - a catalog that cannot be written keeps the node usable for exec
        log.warning("node %s: could not register its models", rec.get("id"), exc_info=True)
    return rec


def allows_workspace(record: Dict[str, Any], workspace: Optional[str]) -> bool:
    """Whether a run in ``workspace`` may use the node's sandbox: any
    workspace when the owner set no list, else only the listed ones."""
    allowed = record.get("workspaces")
    if not allowed:
        return True
    return (workspace or "default") in {str(w) for w in allowed}


def sandbox_availability() -> Dict[str, Dict[str, object]]:
    """For sandbox/registry.py ``available()``: every node that runs a
    sandbox, keyed by its provider name."""
    out: Dict[str, Dict[str, object]] = {}
    for rec in list_nodes():
        if rec.get("disabled") or not (rec.get("capabilities") or {}).get("sandbox"):
            continue
        online = is_online(rec)
        out[f"node:{rec['id']}"] = {
            "available": online,
            "reason": "" if online else "the node is offline",
            "label": f"Node: {rec.get('name') or rec['id']}",
            "node_id": rec["id"],
        }
    return out


__all__ = [
    "BACKEND_PREFIX", "ONLINE_SECONDS", "PROXY_PREFIX", "TOKEN_PREFIX", "allows_workspace",
    "backend_id", "create_node", "delete_node", "get_node", "hello", "is_online", "list_nodes",
    "looks_like_token", "public", "resolve_proxy_key", "resolve_token", "rotate_token",
    "sandbox_availability", "slugify", "touch", "update_node",
]
