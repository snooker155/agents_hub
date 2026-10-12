"""
Linking this hub to another: what the uplink registers here and there.

:func:`link` checks the credential against the remote (``/api/auth/me``),
then does three reversible things:

* registers the remote's ``/v1`` as the custom backend ``hub-remote``
  (providers/registry.py) and seeds the catalog with the models it serves,
  when ``use_models`` is on;
* attaches the remote's ``/v1/mcp`` to the ``default`` workspace as the MCP
  server ``hub-remote`` (mcp_client/store.py), when ``use_agents`` is on, so
  the remote's agents are tools here (``ask_agent`` and the rest);
* makes sure a connection exists on the remote for the run mirror
  (``POST /api/connections``, kind ``hub``) and keeps its token, when runs
  are mirrored. The connection's own token can only report, which is all the
  mirror needs for runs.

:func:`unlink` undoes all three here. Nothing is deleted on the remote: the
copies it holds are the point of the mirror, and the connection stays for
the day the link is made again. :func:`sync` is "do it now": drain the
outbox and say hello as a node.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hublink import store
from hublink.remote import Remote, RemoteError

log = logging.getLogger(__name__)


class LinkError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def remote(doc: Optional[Dict[str, Any]] = None) -> Optional[Remote]:
    doc = doc if doc is not None else store.get()
    if not doc:
        return None
    return Remote(doc["url"], doc.get("api_key") or "")


def _target_workspace(me: Dict[str, Any], wanted: str) -> str:
    """Where mirrored records land on the remote: what the operator asked
    for, else the key owner's personal workspace there, else ``default``."""
    if wanted:
        return wanted
    personal = me.get("personal_workspace")
    if isinstance(personal, str) and personal:
        return personal
    return "default"


# ── registrations on this hub ────────────────────────────────────────────────

def _register_backend(url: str, api_key: str, name: str) -> None:
    from providers import registry
    wanted = {"id": store.BACKEND_ID, "label": f"Remote hub ({name})", "adapter": "openai",
              "base_url": f"{url}/v1", "api_key": api_key}
    current = registry.get_backend(store.BACKEND_ID)
    if not current or any(current.get(k) != v for k, v in wanted.items()):
        registry.upsert_backend({**(current or {}), **wanted})


def _seed_remote_models(rm: Remote) -> int:
    """Catalog rows for what the remote serves. Prices stay 0 here: the
    remote hub does the accounting for the key that calls it."""
    from providers.catalog import load_catalog_raw, save_catalog_raw
    try:
        data = rm.get_json("/v1/models")
    except RemoteError as exc:
        log.warning("hub link: could not list the remote's models: %s", exc)
        return 0
    served = (data.get("data") if isinstance(data, dict) else None) or []
    ids = [str(m.get("id")) for m in served if isinstance(m, dict) and m.get("id")]
    if not ids:
        return 0
    raw = load_catalog_raw()
    catalog = dict(raw) if isinstance(raw, dict) else {}
    entry = catalog.get(store.BACKEND_ID) or {}
    old = {m.get("id"): m for m in (entry.get("models") or []) if isinstance(m, dict)}
    rows = []
    for m in served:
        mid = str(m.get("id"))
        row = dict(old.get(mid) or {"id": mid, "enabled": True, "input_price": 0.0,
                                     "output_price": 0.0, "cached_input_price": 0.0,
                                     "price_source": "auto", "released_at": 0})
        if m.get("context_window"):
            row["context_window"] = int(m["context_window"])
        rows.append(row)
    default = str(entry.get("default") or "")
    if default not in {r["id"] for r in rows}:
        default = rows[0]["id"]
    catalog[store.BACKEND_ID] = {"default": default, "models": rows}
    save_catalog_raw(catalog)
    return len(rows)


def _remove_backend() -> None:
    from providers import registry
    from providers.catalog import load_catalog_raw, save_catalog_raw
    registry.delete_backend(store.BACKEND_ID)
    raw = load_catalog_raw()
    if isinstance(raw, dict) and store.BACKEND_ID in raw:
        catalog = dict(raw)
        catalog.pop(store.BACKEND_ID, None)
        save_catalog_raw(catalog)


def _register_mcp(url: str, api_key: str, name: str) -> None:
    from mcp_client import store as mcp_store
    data = {
        "id": store.MCP_SERVER_ID, "name": f"Remote hub ({name})",
        "description": "The agents, files and knowledge of the linked hub, as tools (docs/hub-link.md).",
        "transport": "streamable_http", "url": f"{url}/v1/mcp",
        "headers": {"Authorization": f"Bearer {api_key}"},
        "enabled": True,
        "capabilities": {"ingests_untrusted": False, "reads_private": True, "can_exfiltrate": True},
        "approval": "none",
    }
    if mcp_store.get_server("default", store.MCP_SERVER_ID) is None:
        mcp_store.create_server("default", data)
    else:
        mcp_store.update_server("default", store.MCP_SERVER_ID, data)


def _remove_mcp() -> None:
    from mcp_client import store as mcp_store
    mcp_store.delete_server("default", store.MCP_SERVER_ID)


# ── the mirror connection on the remote ──────────────────────────────────────

def _ensure_connection(rm: Remote, name: str, workspace: str,
                       existing: Dict[str, Any]) -> Dict[str, Any]:
    """The connection the run mirror reports through, created on the remote
    or reused with a fresh token (the old one is never stored anywhere but
    here, so rotating is the only way to get it back)."""
    conn_id = f"node-{store.slugify(name)}"
    if existing.get("id") == conn_id and existing.get("token"):
        return existing
    body = {"id": conn_id, "name": f"{name} (mirror)", "kind": "hub",
            "description": "Runs mirrored from a linked hub (docs/hub-link.md).",
            "workspace": workspace}
    try:
        created = rm.post_json("/api/connections", body)
        return {"id": conn_id, "token": str(created.get("token") or "")}
    except RemoteError as exc:
        if exc.status not in (400, 409):
            raise
    rotated = rm.post_json(f"/api/connections/{conn_id}/rotate",
                           headers={"X-Workspace": workspace})
    return {"id": conn_id, "token": str(rotated.get("token") or "")}


# ── link, unlink, status, sync ───────────────────────────────────────────────

def link(*, url: str, api_key: str, name: Optional[str] = None,
         use_models: bool = True, use_agents: bool = True,
         mirror: Optional[Dict[str, bool]] = None, target_workspace: str = "",
         node: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Attach this hub to ``url`` with ``api_key``. Returns the stored record."""
    try:
        url = store.normalize_url(url)
    except ValueError as exc:
        raise LinkError(str(exc)) from exc
    existing = store.get() or {}
    api_key = (api_key or "").strip() or str((existing.get("api_key") if existing.get("url") == url else "") or "")
    if not api_key:
        raise LinkError("an API key for the remote hub is required (Settings → API access → "
                        "Personal keys, on that hub)")
    rm = Remote(url, api_key)
    try:
        me = rm.whoami()
    except RemoteError as exc:
        raise LinkError(f"the remote hub refused the key or is unreachable: {exc}",
                        status=502 if exc.status == 0 else 401) from exc
    if not me or not me.get("id"):
        # /api/auth/me answers with the principal when the credential was
        # accepted; anything else means the key was not.
        raise LinkError("the remote hub did not accept the key", status=401)

    node_name = str(name or existing.get("name") or store.default_node_name()).strip()[:60]
    doc = {**store.blank(), **existing}
    doc.update({
        "url": url, "api_key": api_key, "name": node_name,
        "use_models": bool(use_models), "use_agents": bool(use_agents),
        "mirror": {k: bool((mirror or doc.get("mirror") or {}).get(k, doc["mirror"].get(k)))
                   for k in store.MIRROR_KINDS},
        "target_workspace": _target_workspace(me, (target_workspace or "").strip()),
        "remote": {"mode": me.get("mode"), "principal": me.get("principal"),
                   "personal_workspace": me.get("personal_workspace")},
        "linked_at": existing.get("linked_at") or _now_iso(),
        "last_error": "",
    })
    node_doc = dict(doc.get("node") or {})
    if node is not None:
        for key in ("enabled", "share_models", "sandbox"):
            if key in node:
                node_doc[key] = bool(node[key])
        if node.get("token"):
            node_doc["token"] = str(node["token"]).strip()
        if isinstance(node.get("shared_providers"), list):
            node_doc["shared_providers"] = [str(p).strip().lower() for p in node["shared_providers"] if str(p).strip()]
    if node_doc.get("enabled") and not node_doc.get("token"):
        raise LinkError("acting as a node needs a node token (Nodes page on the remote hub)")
    doc["node"] = node_doc

    if doc["mirror"].get("runs"):
        try:
            doc["connection"] = _ensure_connection(rm, node_name, doc["target_workspace"],
                                                   dict(existing.get("connection") or {}))
        except RemoteError as exc:
            raise LinkError(f"could not create the mirror connection on the remote hub: {exc}",
                            status=502) from exc

    if doc["use_models"]:
        _register_backend(url, api_key, node_name)
        _seed_remote_models(rm)
    else:
        _remove_backend()
    if doc["use_agents"]:
        _register_mcp(url, api_key, node_name)
    else:
        _remove_mcp()

    store.put(doc)
    from hublink import runtime
    runtime.refresh()
    return doc


def unlink() -> bool:
    doc = store.get()
    if doc is None:
        return False
    from hublink import runtime
    runtime.stop()
    _remove_backend()
    _remove_mcp()
    from hublink import mirror
    mirror.clear_outbox()
    store.clear()
    return True


def status() -> Dict[str, Any]:
    from hublink import mirror, runtime
    doc = store.get()
    out: Dict[str, Any] = {"linked": doc is not None, "link": store.public(doc)}
    out["outbox"] = mirror.outbox_stats()
    out["runtime"] = runtime.status()
    return out


def sync() -> Dict[str, Any]:
    from hublink import mirror, runtime
    doc = store.get()
    if doc is None:
        raise LinkError("this hub is not linked", status=404)
    drained = mirror.drain(force=True)
    said_hello = runtime.hello_now()
    store.update(last_sync_at=_now_iso())
    return {"drained": drained, "hello": said_hello}


def remote_models(limit: int = 500) -> List[Dict[str, Any]]:
    rm = remote()
    if rm is None:
        return []
    data = rm.get_json("/v1/models")
    served = (data.get("data") if isinstance(data, dict) else None) or []
    return [m for m in served if isinstance(m, dict)][:limit]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = ["LinkError", "link", "remote", "remote_models", "status", "sync", "unlink"]
