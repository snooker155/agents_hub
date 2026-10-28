"""
What the sync engine needs from each kind of data, and the registry of kinds.

The stores in this repository were written one at a time, years apart: a
chat is a SQL row with a JSON document, an agent a DocStore record plus three
markdown files, a workspace file a row plus bytes on disk. Few have an
``updated_at`` the engine could trust, and almost nothing keeps a tombstone.
So the engine does not hook every store. It asks an adapter for the records
of a workspace, hashes each portable document, and compares with what it
last exchanged (hubsync/state.py): a new hash is a change, a missing record
is a deletion. An adapter only has to say what a record is.

The contract, per kind:

* :meth:`Adapter.list_ids` the record ids in a workspace that belong to the
  person (``owner``: the local user, or on the remote the vault's owner).
* :meth:`Adapter.read` a record as a **portable document**: JSON-safe, with
  every id that names this hub's internals left out (positions, caches,
  run-time state), binary content as base64 under keys ending ``_b64``, and
  no secret values ever (names of secrets are fine). Same content, same
  document: :attr:`Adapter.volatile` lists top-level keys that change without
  the content changing (``updated_at`` stamped on every save) and are dropped
  before hashing.
* :meth:`Adapter.apply` writes a document received from the other end into
  this workspace, owned by ``owner``, through the store's own save path (so
  its checks, versions and change events run), and returns the local id.
* :meth:`Adapter.delete` removes the record here.

Two optional hooks, with defaults that do nothing:

* :meth:`Adapter.merge` joins a local record and an incoming one that never
  met (a person's personal memory pool on a second device). ``None`` means
  "cannot merge", and the engine then picks one version and keeps the other
  as a conflict.
* :meth:`Adapter.replaced` removes the record an apply moved away from, for
  a store whose save mints a new id instead of updating in place (files).
  The engine calls it only once the new record is mapped, so a write the
  engine refuses afterwards destroys nothing.

``order`` sorts kinds when applying a batch, so what is referenced lands
before what references it (agents before flows, flows before loops).
``title`` gives the line a person sees in a list; it travels inside the
document, so an encrypted record's title is encrypted too.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional


class AdapterError(Exception):
    """A record this adapter cannot write here (say, an id taken by someone
    else's agent); the engine records it and moves on."""


class Adapter:
    kind: str = ""
    order: int = 50
    volatile: tuple = ("updated_at",)
    label: str = ""

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        raise NotImplementedError

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        raise NotImplementedError

    def delete(self, workspace: str, record_id: str) -> None:
        raise NotImplementedError

    def existing_local(self, workspace: str, record_id: str, doc: Dict[str, Any], *,
                       owner: str) -> Optional[str]:
        """The local record an incoming one will land on when that is not
        ``record_id`` itself: a person's personal memory pool has a different
        id on every hub. Returning it lets the engine see that both sides hold
        content and decide as for any conflict, instead of overwriting a
        second device's notes on its first pass."""
        return None

    def merge(self, local: Dict[str, Any], incoming: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """One document holding both ``local`` and ``incoming``, or ``None``
        when this kind cannot merge. Most kinds cannot: two versions of a
        chat or an agent are not a union of their parts, so the default keeps
        the engine on its conflict path."""
        return None

    def replaced(self, workspace: str, old_local: str, new_local: str, doc: Dict[str, Any]) -> None:
        """``apply`` put the record ``old_local`` used to be on ``new_local``.
        Called after the engine accepted the new mapping, never before: the
        engine may still refuse it (the new id already travels under another
        sync id), and the old record must then stay as it was, or the next
        pass would push it as deleted. Stores that update in place keep their
        id and never get here."""
        return None

    def title(self, doc: Dict[str, Any]) -> str:
        for key in ("title", "name", "display_name", "id"):
            value = doc.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return ""

    def modified_at(self, doc: Dict[str, Any]) -> Optional[str]:
        """The record's own modification time when it keeps one."""
        value = doc.get("updated_at") or doc.get("modified_at")
        return value if isinstance(value, str) and value else None

    def content_hash(self, doc: Dict[str, Any]) -> str:
        stable = {k: v for k, v in doc.items() if k not in self.volatile}
        text = json.dumps(stable, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


_REGISTRY: Dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    if not adapter.kind:
        raise ValueError("an adapter needs a kind")
    _REGISTRY[adapter.kind] = adapter
    return adapter


def get(kind: str) -> Optional[Adapter]:
    _load_all()
    return _REGISTRY.get(kind)


def all_adapters() -> List[Adapter]:
    _load_all()
    return sorted(_REGISTRY.values(), key=lambda a: (a.order, a.kind))


def kinds() -> List[str]:
    return [a.kind for a in all_adapters()]


_loaded = False


def _load_all() -> None:
    """Import every adapter module once; each registers itself."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    import pkgutil
    from hubsync import adapters as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name in ("base",) or info.name.startswith("_"):
            continue
        importlib.import_module(f"hubsync.adapters.{info.name}")


def sort_for_apply(items: Iterable[Any], kind_of=lambda item: item["kind"]) -> List[Any]:
    rank = {a.kind: a.order for a in all_adapters()}
    return sorted(items, key=lambda item: rank.get(kind_of(item), 99))


__all__ = ["Adapter", "AdapterError", "all_adapters", "get", "kinds", "register", "sort_for_apply"]
