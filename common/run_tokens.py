"""
Run tokens: the credential a run's own process presents to the hub.

An agent run, a flow, a loop or a resident instance's carrier runs in a
process of its own (a subprocess or a container) and calls back into the API:
it relays its live events and, from a container with a read-only state mount,
writes its run state under ``/api/run-state``. It used to present the shared
API token (``token`` mode) or the admin service credential (``multi`` mode),
and either one reaches the whole API. An agent holding a shell could read it
out of its own process's environment and do anything an administrator can.

Now each launch gets a token of its own:

* **It reaches only the relay routes** (``common.auth.RELAY_ROUTES``):
  ``common.auth.authorize`` refuses a run principal everywhere else, and the
  relay routes in turn refuse people, so neither side can stand in for the
  other.
* **It is stored only as a hash** (table ``run_tokens``, migration 0040), so a
  run that can read the database learns nothing it can present.
* **It lives as long as the process uses it.** ``expires_at`` slides forward
  on use (:data:`TTL_SECONDS` from the last renewal), so a loop that runs for a
  week keeps its relays, and a token whose process died stops working a TTL
  later. Closing the run retires it (:func:`retire_for_run`): the sliding
  stops and the token keeps :data:`GRACE_SECONDS` for the events still in
  flight.

Minted only in ``token`` and ``multi`` mode: in ``single`` mode the API asks
nobody for a credential, so there is nothing to scope.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from common import db

log = logging.getLogger(__name__)

#: The environment variable a run's process finds its token in.
ENV = "AGENTS_HUB_RUN_TOKEN"

#: Every run token starts with this, so the identity layer can tell one from a
#: session token or an API key without a database lookup.
PREFIX = "ahrun_"

#: How long a token lives past its last renewal, in seconds, unless
#: ``AGENTS_HUB_RUN_TOKEN_TTL`` says otherwise.
TTL_SECONDS = 24 * 3600
TTL_ENV = "AGENTS_HUB_RUN_TOKEN_TTL"

#: What a retired token keeps for the events a closing run still sends.
GRACE_SECONDS = 600

#: How long a resolved token is trusted from memory before the database is
#: asked again: a retirement or expiry lands within this many seconds.
CACHE_SECONDS = 30.0

_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ttl_seconds() -> int:
    try:
        value = int(os.environ.get(TTL_ENV, "") or TTL_SECONDS)
    except ValueError:
        value = TTL_SECONDS
    return max(300, value)


def looks_like_token(presented: Optional[str]) -> bool:
    return bool(presented) and str(presented).startswith(PREFIX)


def mint(*, kind: str = "run", run_id: Optional[str] = None, session_id: Optional[str] = None,
         instance_id: Optional[str] = None, workspace: Optional[str] = None) -> str:
    """A new token for one launch. Returns the token; only its hash is kept."""
    token = PREFIX + secrets.token_urlsafe(32)
    now = _now()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO run_tokens (token_hash, token_id, kind, run_id, session_id, instance_id, "
            "workspace, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_hash(token), "rt_" + uuid.uuid4().hex[:12], str(kind or "run"), run_id or None,
             session_id or None, instance_id or None, workspace or None, now.isoformat(),
             (now + timedelta(seconds=ttl_seconds())).isoformat()),
        )
    return token


def _row(row: Any) -> Dict[str, Any]:
    return {k: row[k] for k in ("token_id", "kind", "run_id", "session_id", "instance_id",
                                "workspace", "created_at", "expires_at", "retired_at")}


def resolve(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The live token's row, or None for an unknown, expired or malformed one.

    Renews the expiry when less than half the TTL is left and the token is not
    retired, so a process that keeps working keeps its token.
    """
    if not looks_like_token(token):
        return None
    digest = _hash(str(token))
    now_mono = time.monotonic()
    with _cache_lock:
        hit = _cache.get(digest)
    if hit is not None and now_mono - hit[1] < CACHE_SECONDS:
        row = hit[0]
        return row if _live(row) else None
    found = db.get_conn().execute(
        "SELECT * FROM run_tokens WHERE token_hash = ?", (digest,)).fetchone()
    if found is None:
        with _cache_lock:
            _cache.pop(digest, None)
        return None
    row = _row(found)
    if not _live(row):
        with _cache_lock:
            _cache.pop(digest, None)
        return None
    if not row.get("retired_at"):
        row = _renew(digest, row)
    with _cache_lock:
        if len(_cache) > 4096:
            _cache.clear()
        _cache[digest] = (row, now_mono)
    return row


def get_by_id(token_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The row of ``token_id`` (a run principal's ``credential_id``): which
    run, session, instance and workspace the token was minted for. None when
    there is none. Never the token itself."""
    if not token_id:
        return None
    found = db.get_conn().execute(
        "SELECT * FROM run_tokens WHERE token_id = ?", (str(token_id),)).fetchone()
    return _row(found) if found is not None else None


def _live(row: Dict[str, Any]) -> bool:
    try:
        return _now() < datetime.fromisoformat(str(row.get("expires_at")))
    except (TypeError, ValueError):
        return False


def _renew(digest: str, row: Dict[str, Any]) -> Dict[str, Any]:
    ttl = ttl_seconds()
    try:
        left = (datetime.fromisoformat(str(row["expires_at"])) - _now()).total_seconds()
    except (TypeError, ValueError):
        return row
    if left > ttl / 2:
        return row
    expires = (_now() + timedelta(seconds=ttl)).isoformat()
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE run_tokens SET expires_at = ? WHERE token_hash = ? AND retired_at IS NULL",
                         (expires, digest))
    except Exception:  # noqa: BLE001 - a failed renewal leaves the old expiry, the request still passes
        log.debug("run token renewal failed", exc_info=True)
        return row
    return {**row, "expires_at": expires}


def _retire(where: str, value: str, grace: float) -> int:
    now = _now()
    until = (now + timedelta(seconds=max(0.0, float(grace)))).isoformat()
    with db.transaction() as conn:
        cur = conn.execute(
            f"UPDATE run_tokens SET retired_at = ?, expires_at = CASE WHEN expires_at < ? "
            f"THEN expires_at ELSE ? END WHERE {where} = ? AND retired_at IS NULL",
            (now.isoformat(), until, until, value))
        changed = int(getattr(cur, "rowcount", 0) or 0)
    with _cache_lock:
        _cache.clear()
    return changed


def retire_for_run(run_id: Optional[str], *, grace: float = GRACE_SECONDS) -> int:
    """Stop the tokens minted for ``run_id`` from sliding; they keep ``grace``
    seconds for the last events. Never raises: closing a run must not fail on it."""
    if not run_id:
        return 0
    try:
        return _retire("run_id", str(run_id), grace)
    except Exception:  # noqa: BLE001 - see the docstring; the TTL still ends the token
        log.debug("run token retirement failed for %s", run_id, exc_info=True)
        return 0


def retire_for_instance(instance_id: Optional[str], *, grace: float = 0.0) -> int:
    """The carrier tokens of ``instance_id``, on a restart or a stop."""
    if not instance_id:
        return 0
    try:
        return _retire("instance_id", str(instance_id), grace)
    except Exception:  # noqa: BLE001 - the TTL still ends the token
        log.debug("run token retirement failed for instance %s", instance_id, exc_info=True)
        return 0


def prune(older_than_days: int = 7) -> int:
    """Delete tokens expired more than ``older_than_days`` ago."""
    cutoff = (_now() - timedelta(days=max(0, int(older_than_days)))).isoformat()
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM run_tokens WHERE expires_at < ?", (cutoff,))
        return int(getattr(cur, "rowcount", 0) or 0)


def mint_for_env(env: Dict[str, str], **fields: Any) -> Dict[str, str]:
    """Put a fresh token into a child's ``env`` in place of every hub-wide
    credential, when the mode asks for credentials at all.

    The shared API token, the service credential and a personal API key are
    removed whatever the mode: none of them belongs in a process an agent's
    tools run in. Returns ``env``.
    """
    for name in ("AGENTS_HUB_API_TOKEN", "AGENTS_HUB_SERVICE_TOKEN", "AGENTS_HUB_API_KEY", ENV):
        env.pop(name, None)
    try:
        from common.identity import SINGLE, current_mode
        if current_mode() == SINGLE:
            return env
        env[ENV] = mint(**fields)
    except Exception:  # noqa: BLE001 - a run without relays still runs; the failure is logged
        log.warning("could not mint a run token; the run's live relays will be refused", exc_info=True)
    return env


__all__ = ["ENV", "GRACE_SECONDS", "PREFIX", "TTL_SECONDS", "get_by_id", "looks_like_token", "mint",
           "mint_for_env",
           "prune", "resolve", "retire_for_instance", "retire_for_run", "ttl_seconds"]
