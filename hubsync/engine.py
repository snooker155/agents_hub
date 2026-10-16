"""
One sync pass: pull what changed in the vault, then push what changed here.

The same pass runs in two roles, which is why the vault access is a
:class:`Transport`:

* **On a person's own hub** (the client), against the remote over HTTP
  (:class:`HttpTransport`), for each local workspace mapped to a space there.
* **On the remote**, for a space synced in plain text, against its own vault
  (:class:`LocalTransport`): the remote "materializes" records into its real
  pages (the chat appears on its Chat page, the agent on its Agents page) and
  pushes back what people change there. An encrypted space is never
  materialized; the remote cannot read it.

How a pass decides, per record (hubsync/state.py keeps the last exchange):

* pull: a vault version newer than the one we hold. Apply it, unless the
  local record changed since the last exchange too; then the newer
  modification time wins and the other side is kept in sync_conflicts. A
  kind whose records keep no time of their own is timed by when this end
  first saw its copy change (state ``changed_at``), on both pull and push.
* first meeting: a record that exists here and was never exchanged (a
  person's personal memory pool on a second device) is merged when the
  adapter knows how (:meth:`Adapter.merge`), else the incoming version wins and the
  local one is kept as the conflict's loser. Time alone never decides it: a
  pool created empty a minute ago is newer than notes written last week.
* push: a local record whose content hash moved, sent based on the version
  we hold; a record we held that is gone here is pushed as a deletion,
  unless its kind could not be listed this pass. A push the vault refuses
  (someone else moved it) is a conflict, resolved the same way.

A record that cannot be written or deleted here (an agent id another
workspace owns, a base agent others extend) keeps its last good exchange and
notes the refused version beside it, so the pass neither retries it forever
nor mistakes it for a local deletion, and a local edit made since still
travels. One local record carries at most one sync id: a second record that
would land on it (a store that dedupes equal files) is merged into it when
the adapter can, and otherwise refused with a conflict, never mapped twice.

With a key, a record must be sealed with that key: the remote is the party
encryption keeps out, so a plain record or one under another key that it
serves into an encrypted space is refused, not applied.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from hubsync import crypto, state
from hubsync.adapters import base as adapters
from hubsync.adapters.base import AdapterError

log = logging.getLogger(__name__)

#: Batches stay under the vault's limits (hubsync/vault.py).
PUSH_BATCH = 100
PUSH_BATCH_BYTES = 32 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Transport:
    def push(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def changes(self, since: int) -> Dict[str, Any]:
        raise NotImplementedError


class LocalTransport(Transport):
    """The remote's own vault, for materializing a plain space."""

    def __init__(self, owner: str, space: str) -> None:
        self.owner, self.space = owner, space

    def push(self, records):
        from hubsync import vault
        return vault.push(self.owner, self.space, records, origin="hub")

    def changes(self, since):
        from hubsync import vault
        return vault.changes(self.owner, self.space, since=since)


class HttpTransport(Transport):
    """A remote hub's vault over its API, with the link's personal key."""

    def __init__(self, remote: Any, space: str) -> None:
        self.remote, self.space = remote, space

    def _path(self, tail: str) -> str:
        from urllib.parse import quote
        return f"/api/sync/spaces/{quote(self.space, safe='')}{tail}"

    def push(self, records):
        return self.remote.post_json(self._path("/push"), {"records": records}, timeout=120)["results"]

    def changes(self, since):
        return self.remote.get_json(self._path(f"/changes?since={int(since)}"), timeout=60)


@dataclass
class Spec:
    """One pairing of a local workspace with a vault space."""
    workspace: str
    space: str
    transport: Transport
    state_owner: str = ""
    actor: str = "local"
    key: Optional[bytes] = None
    kinds: Optional[List[str]] = None
    cursor: int = 0


@dataclass
class Result:
    pulled: int = 0
    applied: int = 0
    pushed: int = 0
    deleted_here: int = 0
    deleted_there: int = 0
    conflicts: int = 0
    errors: List[str] = field(default_factory=list)
    cursor: int = 0
    seconds: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in ("pulled", "applied", "pushed", "deleted_here", "deleted_there",
                                              "conflicts", "errors", "cursor", "seconds")}


def _adapters(spec: Spec) -> List[adapters.Adapter]:
    found = adapters.all_adapters()
    return [a for a in found if spec.kinds is None or a.kind in spec.kinds]


def _wrap(adapter: adapters.Adapter, record_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """What travels: the adapter's document plus its title, so a vault viewer
    can list records by name once it has opened them."""
    return {"kind": adapter.kind, "id": record_id, "title": adapter.title(doc), "data": doc}


def _decode(spec: Spec, rec: Dict[str, Any]) -> Dict[str, Any]:
    if spec.key is not None:
        # The remote decides what "encrypted" says on the wire. Trusting it
        # would let the party encryption keeps out hand us plain records.
        if not rec.get("encrypted"):
            raise crypto.CryptoError("a plain record in an encrypted space; refused")
        if rec.get("key_id") != crypto.key_id(spec.key):
            raise crypto.CryptoError("the record is sealed with another key than this space's; refused")
    wrapped = crypto.decode_record(rec["payload"], kind=rec["kind"], record_id=rec["record_id"],
                                   encrypted=bool(rec.get("encrypted")), key=spec.key, space=spec.space)
    return wrapped.get("data") if isinstance(wrapped, dict) and "data" in wrapped else wrapped


def _newer(a: Optional[str], b: Optional[str]) -> bool:
    """Whether time a is later than b; an unknown time loses."""
    return bool(a) and (not b or str(a) > str(b))


def _failed_at(row: Optional[Dict[str, Any]]) -> int:
    return int((row or {}).get("failed_version") or 0)


class Pass:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.result = Result(cursor=spec.cursor)
        self.rows = state.rows(spec.state_owner, spec.space)
        # (kind, local_id) -> sync ids mapped to it, kept in step with rows.
        self._by_local: Dict[Tuple[str, str], set] = {}
        for (kind, record_id), row in self.rows.items():
            if row.get("local_id"):
                self._by_local.setdefault((kind, row["local_id"]), set()).add(record_id)
        self._failed_now: set = set()

    # -- helpers --------------------------------------------------------------
    def _index(self, kind: str, record_id: str, local_id: str) -> None:
        old = (self.rows.get((kind, record_id)) or {}).get("local_id")
        if old:
            self._by_local.get((kind, old), set()).discard(record_id)
        if local_id:
            self._by_local.setdefault((kind, local_id), set()).add(record_id)

    def _records_for(self, kind: str, local_id: str) -> List[str]:
        """Sync ids mapped to a local record, the one it travels under first."""
        found = self._by_local.get((kind, local_id)) or set()
        return sorted(found, key=lambda rid: (rid != local_id, rid))

    def _held_by_other(self, kind: str, record_id: str, local_id: str) -> Optional[str]:
        others = [rid for rid in self._records_for(kind, local_id) if rid != record_id]
        return others[0] if others else None

    def _remember(self, kind: str, record_id: str, *, local_id: Optional[str], hash_: str, version: int,
                  modified_at: Optional[str] = None, failed_version: int = 0,
                  changed_hash: Optional[str] = None, changed_at: Optional[str] = None) -> None:
        state.put(self.spec.state_owner, self.spec.space, kind, record_id, local_id=local_id or "",
                  hash_=hash_, version=version, modified_at=modified_at, failed_version=failed_version,
                  changed_hash=changed_hash, changed_at=changed_at)
        self._index(kind, record_id, local_id or "")
        self.rows[(kind, record_id)] = {"kind": kind, "record_id": record_id, "local_id": local_id or "",
                                        "hash": hash_, "version": version, "modified_at": modified_at,
                                        "failed_version": failed_version, "changed_hash": changed_hash,
                                        "changed_at": changed_at}

    def _forget(self, kind: str, record_id: str) -> None:
        state.forget(self.spec.state_owner, self.spec.space, kind, record_id)
        self._index(kind, record_id, "")
        self.rows.pop((kind, record_id), None)

    def _mark_failed(self, kind: str, record_id: str, version: int, error: str, *, retry: bool = True) -> None:
        """A version this end could not write. The last good exchange stays as
        it was (local id, hash, version), so a local edit is still told from
        none and the next newer version meets it as a real conflict.
        ``retry`` lets the pull try it once more after the rest of the page
        landed, which helps a missing dependency and nothing else."""
        self.result.errors.append(error)
        row = self.rows.get((kind, record_id))
        if row is None:
            self._remember(kind, record_id, local_id=None, hash_="", version=0, failed_version=version)
        else:
            self._remember(kind, record_id, local_id=row.get("local_id"), hash_=row.get("hash") or "",
                           version=int(row.get("version") or 0), modified_at=row.get("modified_at"),
                           failed_version=max(version, _failed_at(row)), changed_hash=row.get("changed_hash"),
                           changed_at=row.get("changed_at"))
        if retry:
            self._failed_now.add((kind, record_id))

    def _change_time(self, kind: str, record_id: str, hash_: str) -> str:
        """When this end first saw its copy hash to ``hash_``: the modification
        time of a record that keeps none, the same in pull and in push."""
        row = self.rows.get((kind, record_id))
        if row is not None and row.get("changed_hash") == hash_ and row.get("changed_at"):
            return str(row["changed_at"])
        at = _now()
        if row is not None:
            state.note_change(self.spec.state_owner, self.spec.space, kind, record_id, hash_=hash_, at=at)
            row["changed_hash"], row["changed_at"] = hash_, at
        return at

    def _local_time(self, adapter: adapters.Adapter, record_id: str, doc: Dict[str, Any]) -> str:
        return adapter.modified_at(doc) or self._change_time(adapter.kind, record_id, adapter.content_hash(doc))

    def _conflict(self, kind: str, record_id: str, kept: str, lost: Optional[Dict[str, Any]]) -> None:
        state.add_conflict(self.spec.state_owner, self.spec.space, kind, record_id, kept=kept, lost_doc=lost)
        self.result.conflicts += 1

    def _apply(self, adapter: adapters.Adapter, record_id: str, local_id: str, doc: Dict[str, Any],
               version: int, modified_at: Optional[str], *, lost: Optional[Dict[str, Any]] = None,
               base_hash: Optional[str] = None) -> None:
        """Write ``doc`` here. ``lost`` is the local copy it replaces, kept as
        a conflict once the write went through (a refused write replaced
        nothing). ``base_hash`` is the hash to remember instead of what was
        written: a merge remembers the incoming version, so the merged copy
        reads as a local change and is pushed."""
        kind = adapter.kind
        try:
            new_local = adapter.apply(self.spec.workspace, local_id, doc, owner=self.spec.actor) or local_id
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the pass
            if not isinstance(exc, AdapterError):
                log.warning("sync: applying %s %s failed", kind, record_id, exc_info=True)
            self._mark_failed(kind, record_id, version, f"{kind} {record_id}: {exc}")
            return
        holder = self._held_by_other(kind, record_id, new_local) if new_local != local_id else None
        if holder is not None:
            # The store put it on a record another sync id already carries (a
            # file with the same bytes). Two ids on one record would make the
            # next scan see one of them as deleted, so this one keeps what it
            # had (nothing, for a new record) and the version is refused.
            self._mark_failed(kind, record_id, version,
                              f"{kind} {record_id}: lands on the same record here as {holder}", retry=False)
            self._conflict(kind, record_id, "local", doc)
            return
        if new_local != local_id and self._held_by_other(kind, record_id, local_id) is None:
            # The store moved the record to a new id (a file whose bytes
            # changed). Only now, with the new mapping accepted, may the old
            # one go: removing it before the check above would leave nothing
            # behind when the mapping is refused, and the next scan would push
            # that absence as a deletion. A record another sync id still
            # carries is that id's, and stays.
            try:
                adapter.replaced(self.spec.workspace, local_id, new_local, doc)
            except Exception as exc:  # noqa: BLE001 - the write landed; a leftover is reported, not fatal
                log.warning("sync: removing the replaced %s %s failed", kind, local_id, exc_info=True)
                self.result.errors.append(f"{kind} {record_id}: the replaced record stayed: {exc}")
        if lost is not None:
            self._conflict(kind, record_id, "remote", lost)
        reread = adapter.read(self.spec.workspace, new_local)
        hash_ = base_hash or (adapter.content_hash(reread) if reread is not None else "")
        self._remember(kind, record_id, local_id=new_local, hash_=hash_, version=version,
                       modified_at=modified_at)
        self.result.applied += 1

    # -- pull -----------------------------------------------------------------
    def _pull_guarded(self, rec: Dict[str, Any]) -> None:
        """One record, whatever goes wrong with it: a record that fails here
        is refused at its version, and the rest of the page, the cursor and
        the push still go ahead. Letting it escape would end the pass before
        the cursor is saved, so every later pass would stop at it again."""
        try:
            self._pull_one(rec)
        except Exception as exc:  # noqa: BLE001 - one record must not stall the space
            if not isinstance(exc, (AdapterError, crypto.CryptoError)):
                log.warning("sync: pulling %s %s failed", rec.get("kind"), rec.get("record_id"), exc_info=True)
            self._mark_failed(rec["kind"], rec["record_id"], int(rec.get("version") or 0),
                              f"{rec.get('kind')} {rec.get('record_id')}: {exc}")

    def pull(self) -> None:
        cursor = self.spec.cursor
        refused: List[Dict[str, Any]] = []
        before: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        while True:
            page = self.spec.transport.changes(cursor)
            records = adapters.sort_for_apply(page.get("records") or [])
            for rec in records:
                self.result.pulled += 1
                key = (rec["kind"], rec["record_id"])
                if key not in before:
                    row = self.rows.get(key)
                    before[key] = dict(row) if row is not None else None
                self._pull_guarded(rec)
                if key in self._failed_now:
                    refused.append(rec)
            cursor = int(page.get("next") or cursor)
            if not page.get("more"):
                break
        # Once more for what was refused: an agent that extends another, or a
        # flow whose agent came later in the same pull, lands now. Each goes
        # back to the row it had before this pass, so the second try decides
        # exactly as the first did.
        if refused:
            first_errors = list(self.result.errors)
            for rec in refused:
                key = (rec["kind"], rec["record_id"])
                prior = before.get(key)
                if prior is None:
                    self._forget(*key)
                else:
                    self._remember(key[0], key[1], local_id=prior.get("local_id"), hash_=prior.get("hash") or "",
                                   version=int(prior.get("version") or 0), modified_at=prior.get("modified_at"),
                                   failed_version=_failed_at(prior), changed_hash=prior.get("changed_hash"),
                                   changed_at=prior.get("changed_at"))
                self._failed_now.discard(key)
                self._pull_guarded(rec)
            landed = {f"{rec['kind']} {rec['record_id']}:" for rec in refused
                      if (rec["kind"], rec["record_id"]) not in self._failed_now}
            self.result.errors = [e for e in first_errors if not any(e.startswith(p) for p in landed)]
        self.result.cursor = cursor

    def _pull_one(self, rec: Dict[str, Any]) -> None:
        kind, record_id, version = rec["kind"], rec["record_id"], int(rec["version"])
        if self.spec.kinds is not None and kind not in self.spec.kinds:
            return
        adapter = adapters.get(kind)
        if adapter is None:
            return  # a kind a newer hub knows and this one does not
        row = self.rows.get((kind, record_id))
        if row is not None and (int(row["version"]) >= version or _failed_at(row) >= version):
            return  # our own push coming back, something already applied, or refused at this version
        deleted = bool(rec.get("deleted"))
        incoming = None if deleted else _decode(self.spec, rec)
        mapped = bool((row or {}).get("local_id"))
        if mapped:
            self._pull_mapped(adapter, record_id, version, rec, row, incoming)
        elif row is not None and incoming is None:
            self._forget(kind, record_id)  # refused here and now deleted there: nothing left to hold
        else:
            self._pull_first(adapter, record_id, version, rec, incoming)

    def _pull_mapped(self, adapter, record_id, version, rec, row, incoming) -> None:
        """A record this end exchanged before: compare with the last exchange."""
        kind, local_id = adapter.kind, row["local_id"]
        local_doc = adapter.read(self.spec.workspace, local_id)
        local_changed = local_doc is not None and adapter.content_hash(local_doc) != row.get("hash")
        if incoming is None:
            if local_changed:
                # Edited here after it was deleted there: keep the edit; the push re-creates it.
                self._remember(kind, record_id, local_id=local_id, hash_="", version=version)
                self._conflict(kind, record_id, "local", None)
                return
            if local_doc is not None:
                adapter.delete(self.spec.workspace, local_id)
                self.result.deleted_here += 1
            self._forget(kind, record_id)
            return
        if local_changed and adapter.content_hash(local_doc) == adapter.content_hash(incoming):
            # Both sides made the same change: nothing to choose between.
            local_changed = False
        if local_changed:
            local_time = self._local_time(adapter, record_id, local_doc)
            if _newer(local_time, rec.get("modified_at")):
                # Ours is newer: base the coming push on their version, keep theirs as the loser.
                current = self.rows.get((kind, record_id)) or row
                self._remember(kind, record_id, local_id=local_id, hash_=row.get("hash") or "",
                               version=version, modified_at=local_time,
                               changed_hash=current.get("changed_hash"), changed_at=current.get("changed_at"))
                self._conflict(kind, record_id, "local", incoming)
                return
        self._apply(adapter, record_id, local_id, incoming, version, rec.get("modified_at"),
                    lost=local_doc if local_changed else None)

    def _pull_first(self, adapter, record_id, version, rec, incoming) -> None:
        """A record this end has no mapping for: new here, or met for the
        first time (the same id, or the person's own pool, exists already)."""
        kind = adapter.kind
        if incoming is None:
            local_doc = adapter.read(self.spec.workspace, record_id)
            if local_doc is not None and not self._records_for(kind, record_id):
                # Never exchanged, and it exists here: keep it; the push sends it.
                self._remember(kind, record_id, local_id=record_id, hash_="", version=version)
                self._conflict(kind, record_id, "local", None)
            else:
                self._forget(kind, record_id)
            return
        local_id = adapter.existing_local(self.spec.workspace, record_id, incoming,
                                          owner=self.spec.actor) or record_id
        local_doc = adapter.read(self.spec.workspace, local_id)
        holder = self._held_by_other(kind, record_id, local_id)
        if holder is not None:
            # The record it would land on already travels under another id.
            merged = adapter.merge(local_doc, incoming) if local_doc is not None else None
            if merged is None:
                self._mark_failed(kind, record_id, version,
                                  f"{kind} {record_id}: lands on the same record here as {holder}", retry=False)
                self._conflict(kind, record_id, "local", incoming)
                return
            try:
                adapter.apply(self.spec.workspace, local_id, merged, owner=self.spec.actor)
            except Exception as exc:  # noqa: BLE001 - refused like any write
                self._mark_failed(kind, record_id, version, f"{kind} {record_id}: {exc}")
                return
            # Merged into the other id's record, which the push then sends;
            # this id is remembered at its version with nothing mapped to it.
            self._remember(kind, record_id, local_id=None, hash_="", version=version)
            self.result.applied += 1
            return
        if local_doc is None or adapter.content_hash(local_doc) == adapter.content_hash(incoming):
            self._apply(adapter, record_id, local_id, incoming, version, rec.get("modified_at"))
            return
        merged = adapter.merge(local_doc, incoming)
        if merged is not None:
            self._apply(adapter, record_id, local_id, merged, version, rec.get("modified_at"),
                        base_hash=adapter.content_hash(incoming))
            return
        # Both sides hold something different and never met: the version that
        # already travels wins, and what was here is kept as the loser.
        self._apply(adapter, record_id, local_id, incoming, version, rec.get("modified_at"), lost=local_doc)

    # -- scan and push --------------------------------------------------------
    def notice(self) -> None:
        """Stamp the change time of local edits without pushing anything: a
        pass that cannot reach the remote still learns when an edit happened,
        so a record that keeps no time of its own is not timed by when the
        connection came back."""
        for adapter in _adapters(self.spec):
            try:
                for local_id in adapter.list_ids(self.spec.workspace, self.spec.actor):
                    ids = self._records_for(adapter.kind, local_id)
                    if not ids:
                        continue
                    doc = adapter.read(self.spec.workspace, local_id)
                    if doc is None or adapter.modified_at(doc):
                        continue
                    hash_ = adapter.content_hash(doc)
                    if hash_ != (self.rows.get((adapter.kind, ids[0])) or {}).get("hash"):
                        self._change_time(adapter.kind, ids[0], hash_)
            except Exception:  # noqa: BLE001 - only a best effort beside the real failure
                log.debug("sync: noticing %s changes failed", adapter.kind, exc_info=True)

    def scan(self) -> List[Tuple[adapters.Adapter, str, Dict[str, Any]]]:
        """[(adapter, record_id, record)] to push: changes and deletions."""
        out = []
        seen = set()
        unlisted = set()
        for adapter in _adapters(self.spec):
            try:
                ids = adapter.list_ids(self.spec.workspace, self.spec.actor)
            except Exception as exc:  # noqa: BLE001 - one broken store must not stop the others
                log.warning("sync: listing %s failed", adapter.kind, exc_info=True)
                self.result.errors.append(f"{adapter.kind}: {exc}")
                # Not knowing what is here is not the same as nothing being
                # here: no deletion of this kind goes out this pass.
                unlisted.add(adapter.kind)
                continue
            for local_id in ids:
                record_ids = self._records_for(adapter.kind, local_id) or [local_id]
                seen.update((adapter.kind, rid) for rid in record_ids)
                record_id = record_ids[0]
                row = self.rows.get((adapter.kind, record_id))
                if row is not None and not row.get("local_id"):
                    continue  # that id was refused here; the local record of that id is not it
                try:
                    doc = adapter.read(self.spec.workspace, local_id)
                except Exception as exc:  # noqa: BLE001
                    self.result.errors.append(f"{adapter.kind} {local_id}: {exc}")
                    continue
                if doc is None:
                    continue
                hash_ = adapter.content_hash(doc)
                if row is not None and row.get("hash") == hash_:
                    continue
                modified = adapter.modified_at(doc) or (
                    self._change_time(adapter.kind, record_id, hash_) if row is not None else _now())
                out.append((adapter, record_id, {"local_id": local_id, "doc": doc, "hash": hash_,
                                                 "base": int((row or {}).get("version") or 0),
                                                 "modified_at": modified}))
        for (kind, record_id), row in list(self.rows.items()):
            if (kind, record_id) in seen or not row.get("local_id") or kind in unlisted:
                continue
            if self.spec.kinds is not None and kind not in self.spec.kinds:
                continue
            adapter = adapters.get(kind)
            if adapter is None:
                continue
            out.append((adapter, record_id, {"local_id": row["local_id"], "doc": None, "hash": "",
                                             "base": int(row.get("version") or 0), "modified_at": _now(),
                                             "deleted": True}))
        return out

    def _wire(self, adapter: adapters.Adapter, record_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        rec = {"kind": adapter.kind, "record_id": record_id, "base_version": item["base"],
               "modified_at": item["modified_at"], "deleted": bool(item.get("deleted"))}
        if item.get("deleted"):
            return rec
        rec["encrypted"] = self.spec.key is not None
        if self.spec.key is not None:
            rec["key_id"] = crypto.key_id(self.spec.key)
        rec["payload"] = crypto.encode_record(_wrap(adapter, record_id, item["doc"]), kind=adapter.kind,
                                              record_id=record_id, key=self.spec.key, space=self.spec.space)
        return rec

    def push(self) -> None:
        pending = self.scan()
        batch: List[Tuple[adapters.Adapter, str, Dict[str, Any], Dict[str, Any]]] = []
        size = 0
        for adapter, record_id, item in pending:
            wire = self._wire(adapter, record_id, item)
            weight = len(wire.get("payload") or "")
            if batch and (len(batch) >= PUSH_BATCH or size + weight > PUSH_BATCH_BYTES):
                self._send(batch)
                batch, size = [], 0
            batch.append((adapter, record_id, item, wire))
            size += weight
        if batch:
            self._send(batch)

    def _send(self, batch) -> None:
        results = self.spec.transport.push([wire for _, _, _, wire in batch])
        for (adapter, record_id, item, wire), res in zip(batch, results):
            try:
                self._settle(adapter, record_id, item, res)
            except Exception as exc:  # noqa: BLE001 - one record's answer must not drop the rest
                log.warning("sync: settling %s %s failed", adapter.kind, record_id, exc_info=True)
                self.result.errors.append(f"{adapter.kind} {record_id}: {exc}")

    def _settle(self, adapter, record_id, item, res) -> None:
        status = res.get("status")
        if status == "ok":
            if item.get("deleted"):
                self._forget(adapter.kind, record_id)
                self.result.deleted_there += 1
            else:
                self._remember(adapter.kind, record_id, local_id=item["local_id"], hash_=item["hash"],
                               version=int(res.get("version") or 0), modified_at=item["modified_at"])
                self.result.pushed += 1
        elif status == "conflict":
            self._resolve_push_conflict(adapter, record_id, item, res.get("current"))
        else:
            self.result.errors.append(f"{adapter.kind} {record_id}: {res.get('error') or status}")

    def _resolve_push_conflict(self, adapter, record_id, item, current) -> None:
        if current is None:
            # The vault lost it (a cleared space): send it again as new next pass.
            self._remember(adapter.kind, record_id, local_id=item["local_id"], hash_="", version=0)
            return
        version = int(current.get("version") or 0)
        if current.get("deleted"):
            if item.get("deleted"):
                self._forget(adapter.kind, record_id)
            else:
                # Deleted there, changed here: the change is newer news; push it on their version.
                self._remember(adapter.kind, record_id, local_id=item["local_id"], hash_="", version=version)
                self._conflict(adapter.kind, record_id, "local", None)
            return
        theirs = _decode(self.spec, current)
        if not item.get("deleted") and adapter.content_hash(theirs) == item["hash"]:
            # The vault already holds exactly this: adopt its version, no conflict.
            self._remember(adapter.kind, record_id, local_id=item["local_id"], hash_=item["hash"],
                           version=version, modified_at=current.get("modified_at"))
            return
        if item.get("deleted") or not _newer(item["modified_at"], current.get("modified_at")):
            self._apply(adapter, record_id, item["local_id"], theirs, version, current.get("modified_at"),
                        lost=item.get("doc"))
            return
        # Ours is newer: keep it and push again on their version next pass.
        self._remember(adapter.kind, record_id, local_id=item["local_id"], hash_="", version=version)
        self._conflict(adapter.kind, record_id, "local", theirs)


def run(spec: Spec) -> Result:
    started = time.monotonic()
    p = Pass(spec)
    try:
        p.pull()
    except Exception:
        p.notice()
        raise
    p.push()
    p.result.seconds = round(time.monotonic() - started, 3)
    return p.result


__all__ = ["HttpTransport", "LocalTransport", "Result", "Spec", "Transport", "run"]
