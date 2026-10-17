"""
This hub as a sync client of the hub it is linked to (hublink/).

The settings live in the link record under ``sync`` (hublink/store.py), so
unlinking forgets them with everything else:

    {"enabled": bool, "encrypted": bool, "encrypt_all": bool, "actor": "<user id>",
     "mappings": [{"workspace": "<here>", "space": "<there>", "encrypted": bool}],
     "key": {"key_id", "salt", "iterations", "sealed"},   # sealed: Fernet, this hub's secret key
     "cursors": {"<space>": seq}, "last_pass_at", "last_result", "last_error"}

Encryption is chosen per mapping: one workspace can travel sealed while
another goes in clear. ``encrypt_all`` (chosen in ``ah setup`` or on the
Settings page) seals every mapping, default and all the others, including
ones added later, and the per mapping choice waits until it is off. The
top-level ``encrypted`` only says whether any mapping is, for the header
indicator; a mapping saved before the choice was per mapping takes the old
top-level value (:func:`mappings`).

The sync key is derived from the person's passphrase once, then kept sealed
under ``AGENTS_HUB_SECRET_KEY`` (the same key that seals workspace secrets):
the hub has to open records on its own, every pass, with nobody typing. The
passphrase is never stored; the remote holds only the KDF parameters and a
verifier (hubsync/crypto.py), which is how a second device proves it typed
the same passphrase.

Turning sync on turns the old one-way mirror off as a whole, runs included,
and the link store keeps it off while sync is on (hublink/store.py): the same
chat or run must not travel twice, once as a mirror copy and once as a synced
record, and in an encrypted space a mirror in clear would hand the remote
exactly what the encryption keeps from it.

Whose records a pass syncs (``actor``) is fixed when sync is first set up:
the person who set it up, or the local operator. Reading it from whoever
triggered a pass would make "Sync now" (an administrator's request) and the
background pass (no request at all) see different records, and each would
push the other's as deleted.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hubsync import crypto, engine

log = logging.getLogger(__name__)

PASS_SECONDS = 30.0
LEASE_ROLE = "hubsync-client"


class SyncConfigError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status

    @property
    def detail(self) -> Any:
        """What the route answers with: the sentence, unless a subclass
        carries something the page acts on."""
        return str(self)


class KeyExistsError(SyncConfigError):
    """The account already has a sync key on the linked hub and no passphrase
    came to unlock it. ``replace_unused`` says whether the linked hub reports
    no record sealed with any of its keys, the one case where replacing loses
    nothing; the page offers the replacement only then, so the detail is an
    object it can read rather than a sentence it would have to parse."""

    code = "key_exists"

    def __init__(self, message: str, *, replace_unused: bool) -> None:
        super().__init__(message, status=409)
        self.replace_unused = bool(replace_unused)

    @property
    def detail(self) -> Dict[str, Any]:
        return {"message": str(self), "code": self.code, "replace_unused": self.replace_unused}


def _unused(keys: List[Dict[str, Any]]) -> bool:
    """No record sealed with any of these keys, as the linked hub counts
    them. A count that is missing or not a number reads as used: offering to
    replace a key that sealed records would make them unreadable for good."""
    def sealed(key: Dict[str, Any]) -> Optional[int]:
        value = key.get("records")
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return bool(keys) and all(sealed(k) == 0 for k in keys)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _link() -> Dict[str, Any]:
    from hublink import store
    doc = store.get()
    if not doc:
        raise SyncConfigError("this hub is not linked to another; link it first", status=409)
    return doc


def _remote(doc: Optional[Dict[str, Any]] = None):
    from hublink.link import remote
    return remote(doc or _link())


def config(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        doc = doc if doc is not None else _link()
    except SyncConfigError:
        return {}
    return dict(doc.get("sync") or {})


def mappings(sync: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The mappings, each with its own ``encrypted``. One saved before the
    flag moved onto the mapping takes the old setting for the whole sync."""
    sync = sync if sync is not None else config()
    legacy = bool(sync.get("encrypted"))
    every = bool(sync.get("encrypt_all"))
    out: List[Dict[str, Any]] = []
    for m in sync.get("mappings") or []:
        if not isinstance(m, dict) or not m.get("workspace"):
            continue
        enc = m.get("encrypted")
        out.append({"workspace": m["workspace"], "space": m.get("space") or m["workspace"],
                    "encrypted": every or (legacy if enc is None else bool(enc))})
    return out


def _save(**changes: Any) -> Dict[str, Any]:
    from hublink import store
    doc = _link()
    sync = {**(doc.get("sync") or {}), **changes}
    store.update(sync=sync)
    return sync


def _fernet():
    from common import secrets as hub_secrets
    fernet = hub_secrets._fernet()
    if fernet is None:
        raise SyncConfigError("encryption needs AGENTS_HUB_SECRET_KEY on this hub (ah secrets keygen)",
                              status=409)
    return fernet


def unlocked_key(sync: Optional[Dict[str, Any]] = None) -> Optional[bytes]:
    sync = sync if sync is not None else config()
    sealed = (sync.get("key") or {}).get("sealed")
    if not sealed:
        return None
    try:
        return _fernet().decrypt(sealed.encode("ascii"))
    except Exception:  # noqa: BLE001 - a rotated secret key: the person unlocks again
        log.warning("sync: the stored sync key does not open with this hub's secret key")
        return None


def status() -> Dict[str, Any]:
    """What the header and the Settings page show. No secrets: the key is
    only ``set`` or not, the passphrase never anywhere."""
    from hublink import store
    doc = store.get()
    if not doc:
        return {"linked": False, "enabled": False}
    sync = dict(doc.get("sync") or {})
    key = sync.get("key") or {}
    maps = mappings(sync)
    open_conflicts = 0
    try:
        from hubsync import state
        open_conflicts = len(state.conflicts(""))
    except Exception:  # noqa: BLE001 - status must answer even before the migration ran
        log.debug("sync: conflicts unreadable", exc_info=True)
    return {
        "linked": True, "remote": doc.get("url"), "remote_name": doc.get("name") or "",
        "actor": sync.get("actor") or "",
        "enabled": bool(sync.get("enabled")), "encrypted": any(m["encrypted"] for m in maps),
        "encrypt_all": bool(sync.get("encrypt_all")),
        "key_set": bool(key.get("sealed")), "key_id": key.get("key_id") or "",
        "key_unlocked": unlocked_key(sync) is not None if key.get("sealed") else False,
        "mappings": maps, "last_pass_at": sync.get("last_pass_at"),
        "last_result": sync.get("last_result") or {}, "last_error": sync.get("last_error") or "",
        "has_error": bool(sync.get("last_error")),
        "conflicts": open_conflicts, "catalog": bool(doc.get("catalog")),
    }


def configure(*, enabled: bool, mappings: List[Dict[str, Any]],
              encrypted: Optional[bool] = None, encrypt_all: Optional[bool] = None) -> Dict[str, Any]:
    """Save the sync settings. Each mapping carries its own ``encrypted``;
    ``encrypted`` here is only the value for a mapping that does not (a
    caller from before the choice was per mapping). ``encrypt_all`` seals
    every mapping whatever it says; None keeps the stored choice."""
    every = bool(config().get("encrypt_all")) if encrypt_all is None else bool(encrypt_all)
    clean: List[Dict[str, Any]] = []
    seen_spaces = set()
    seen_workspaces = set()
    for m in mappings or []:
        workspace = str(m.get("workspace") or "").strip()
        space = str(m.get("space") or "").strip() or workspace
        if not workspace:
            continue
        if space in seen_spaces:
            raise SyncConfigError(f"two workspaces map to the space '{space}' there; give each its own")
        if workspace in seen_workspaces:
            raise SyncConfigError(f"the workspace '{workspace}' is mapped twice; keep one")
        seen_spaces.add(space)
        seen_workspaces.add(workspace)
        enc = m.get("encrypted")
        clean.append({"workspace": workspace, "space": space,
                      "encrypted": every or (bool(encrypted) if enc is None else bool(enc))})
    sealed_any = any(m["encrypted"] for m in clean)
    sync = config()
    key = sync.get("key") or {}
    if enabled and (sealed_any or (every and clean)) and not key.get("sealed"):
        raise SyncConfigError("set up the encryption key first", status=409)
    if enabled and clean:
        remote = _remote()
        for m in clean:
            remote.put_json(f"/api/sync/spaces/{_q(m['space'])}",
                            {"encrypted": m["encrypted"], "key_id": key.get("key_id") if m["encrypted"] else None,
                             "origin": "client"}, timeout=30)
    if enabled:
        _disable_old_mirror()
    # Fixed once: a later save by another administrator must not move the
    # pass to their records, which would push everything synced so far as
    # deleted. Unlinking forgets it with the rest of the sync settings.
    actor = sync.get("actor") or _actor()
    saved = _save(enabled=bool(enabled), encrypted=sealed_any, encrypt_all=every, mappings=clean, actor=actor)
    _wake()
    return saved


def set_workspace(workspace: str, *, synced: bool, space: Optional[str] = None,
                  encrypted: bool = False) -> Dict[str, Any]:
    """One workspace's part of the settings, from its own Settings tab: whether
    it syncs, into which space there, and whether sealed. The other mappings
    and the on/off switch stay as they are."""
    workspace = str(workspace or "").strip()
    if not workspace:
        raise SyncConfigError("which workspace?")
    _link()  # refuses on a hub that is not linked
    sync = config()
    if synced and sync.get("encrypt_all") and not encrypted:
        raise SyncConfigError("every workspace is encrypted on this hub; turn that off in Settings, "
                              "Remote hub to choose per workspace")
    current = mappings(sync)
    rows = [m for m in current if m["workspace"] != workspace]
    if synced:
        before = next((m for m in current if m["workspace"] == workspace), None)
        target = str(space or "").strip() or (before or {}).get("space") or workspace
        rows.append({"workspace": workspace, "space": target, "encrypted": bool(encrypted)})
    return configure(enabled=bool(sync.get("enabled")), mappings=rows)


def _q(text: str) -> str:
    from urllib.parse import quote
    return quote(text, safe="")


def _disable_old_mirror() -> None:
    """Every mirror kind off, runs too, and what is still queued dropped: a
    finished run would otherwise reach the remote as a span in clear."""
    from hublink import mirror as hub_mirror
    from hublink import store
    doc = store.get() or {}
    mirror = {**dict(doc.get("mirror") or {}), **{k: False for k in store.MIRROR_KINDS}}
    store.update(mirror=mirror)
    hub_mirror.clear_outbox()


def setup_key(*, passphrase: Optional[str] = None, generate: bool = False,
              replace_unused: bool = False) -> Dict[str, Any]:
    """Create the person's sync key, or unlock the one they already made on
    another device. Returns the generated passphrase once, when asked to
    generate one; it is not kept anywhere.

    ``replace_unused`` with ``generate`` is the way back from a generated
    passphrase nobody wrote down: the account's keys on the remote are
    removed and a new one made, as long as no record is sealed with any of
    them, since those records would become unreadable for good. The count
    the remote lists per key is checked first, and its DELETE refuses again
    should a record have been sealed in between. Only on that explicit ask:
    an existing key is otherwise unlocked, never replaced, and asking
    without a passphrase raises :class:`KeyExistsError`, which tells the page
    whether replacing is on offer."""
    # Before anything changes on the linked hub: a key this hub cannot seal
    # would leave a new remote key behind whose passphrase nobody kept.
    _fernet()
    remote = _remote()
    existing = remote.get_json("/api/sync/keys", timeout=30).get("keys") or []
    generated = ""
    if existing and generate and replace_unused and not passphrase:
        from hublink.remote import RemoteError
        if not _unused(existing):
            raise SyncConfigError("the sync key cannot be replaced: records are sealed with it, and they "
                                  "would never open again", status=409)
        for old in existing:
            try:
                remote.call("DELETE", f"/api/sync/keys/{_q(old['key_id'])}", ok=(200, 204))
            except RemoteError as exc:
                if exc.status == 409:
                    raise SyncConfigError(f"the sync key cannot be replaced: {exc}", status=409) from exc
                raise
        existing = []
        # Encrypted sync ran on the old key; it waits until it is set up
        # again with the new one. Plain sync goes on as it was.
        if any(m["encrypted"] for m in mappings()):
            _save(key={}, enabled=False)
        else:
            _save(key={})
    if existing:
        if not passphrase:
            unused = _unused(existing)
            raise KeyExistsError("this account already has a sync key: type its passphrase to unlock it"
                                 + (", or replace it, since no record is sealed with it yet" if unused else ""),
                                 replace_unused=unused)
        known = existing[-1]
        key = crypto.derive_key(passphrase, known["salt"], int(known["iterations"]))
        if not crypto.check_verifier(key, known["verifier"]):
            raise SyncConfigError("that passphrase does not open this account's sync key", status=403)
        params = {"key_id": known["key_id"], "salt": known["salt"], "iterations": int(known["iterations"])}
    else:
        if generate or not passphrase:
            generated = crypto.generate_passphrase()
            passphrase = generated
        elif len(passphrase) < 12:
            raise SyncConfigError("a passphrase needs at least 12 characters, or let the hub generate one")
        salt = crypto.new_salt()
        iterations = crypto.ITERATIONS
        key = crypto.derive_key(passphrase, salt, iterations)
        params = {"key_id": crypto.key_id(key), "salt": salt, "iterations": iterations}
        remote.put_json("/api/sync/keys", {**params, "kdf": crypto.KDF, "verifier": crypto.make_verifier(key)},
                        timeout=30)
    sealed = _fernet().encrypt(key).decode("ascii")
    _save(key={**params, "sealed": sealed})
    return {"key_id": params["key_id"], "generated_passphrase": generated}


def forget_key() -> None:
    """Sealed mappings keep their choice and wait for a key: sync stops while
    any mapping is encrypted, so none of them goes out in clear."""
    maps = mappings()
    sealed_any = any(m["encrypted"] for m in maps)
    _save(key={}, mappings=maps, **({"enabled": False} if sealed_any else {}))


_pass_lock = threading.Lock()


def run_pass() -> Dict[str, Any]:
    """One pass over every mapping, never two at once: a pass started by
    Sync now and the background one would both push the same changes, and
    the second would meet its own records as conflicts. A lock covers this
    process, the lease covers the other replicas."""
    from common import leases
    with _pass_lock:
        if not leases.hold(LEASE_ROLE, 120):
            return {"skipped": "another replica is syncing right now"}
        return _run_pass()


def _run_pass() -> Dict[str, Any]:
    """Errors are reported per mapping and kept in the record, so the header
    can say what went wrong."""
    sync = config()
    if not sync.get("enabled"):
        return {"skipped": "sync is off"}
    maps = mappings(sync)
    key = unlocked_key(sync) if any(m["encrypted"] for m in maps) else None
    locked = "the sync key is locked on this hub: unlock it in Settings"
    if maps and key is None and all(m["encrypted"] for m in maps):
        _save(last_error=locked, last_pass_at=_now())
        return {"error": "locked"}
    from common import identity
    remote = _remote()
    cursors = dict(sync.get("cursors") or {})
    totals: Dict[str, Any] = {"pulled": 0, "applied": 0, "pushed": 0, "conflicts": 0, "errors": []}
    actor = sync.get("actor") or identity.LOCAL_OPERATOR_ID
    if not sync.get("actor"):
        _save(actor=actor)  # a config from before the actor was kept: the background pass's view
    for m in maps:
        if m["encrypted"] and key is None:
            # A sealed workspace never goes in clear; the plain ones go on.
            totals["errors"].append(f"{m['workspace']}: {locked}")
            continue
        spec = engine.Spec(workspace=m["workspace"], space=m["space"],
                           transport=engine.HttpTransport(remote, m["space"]), state_owner="",
                           actor=actor, key=key if m["encrypted"] else None,
                           cursor=int(cursors.get(m["space"]) or 0))
        # Stores that stamp the current user (chats, tasks) stamp the actor,
        # whoever asked for this pass.
        token = identity.set_current_user(actor)
        try:
            result = engine.run(spec)
        except Exception as exc:  # noqa: BLE001 - one mapping failing must not stop the others
            log.warning("sync: pass for %s failed", m, exc_info=True)
            totals["errors"].append(f"{m['workspace']}: {exc}")
            continue
        finally:
            identity.reset_current_user(token)
        cursors[m["space"]] = result.cursor
        for k in ("pulled", "applied", "pushed", "conflicts"):
            totals[k] += getattr(result, k)
        totals["errors"] += result.errors[:20]
    _save(cursors=cursors, last_pass_at=_now(), last_result=totals,
          last_error="; ".join(totals["errors"][:3]))
    return totals


def _actor() -> str:
    """Who sets sync up: the administrator in the request, or the local
    operator. Read once, by :func:`configure`; a pass reads the stored one."""
    from common import identity
    return identity.current_user_id()


# ── the background pass ──────────────────────────────────────────────────────

_wake_event = threading.Event()
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def _wake() -> None:
    _wake_event.set()


def _loop() -> None:
    while not _stop_event.is_set():
        _wake_event.wait(PASS_SECONDS)
        _wake_event.clear()
        if _stop_event.is_set():
            break
        try:
            if not config().get("enabled"):
                continue
            run_pass()
        except Exception:  # noqa: BLE001 - the loop outlives any one pass
            log.warning("sync: background pass failed", exc_info=True)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="hubsync-client", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()
    _wake_event.set()


__all__ = ["KeyExistsError", "SyncConfigError", "config", "configure", "forget_key", "mappings", "run_pass",
           "set_workspace", "setup_key", "start", "status", "stop", "unlocked_key"]
