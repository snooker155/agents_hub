"""
Who a call from an agent container is charged to, decided on the hub.

An agent container (a task run's, or an instance carrier serving many
people's chat turns) has no database: it opens runs, reads stores and calls
model providers through the hub, presenting its run token. The token names the
container's run or its instance, never a person. The person and the personal
key a call is charged to are read here from records the hub wrote itself:

- the run the call names (``run``: the run executing at that moment,
  ``common.agent_context.current_run_id``), when it belongs to the token: the
  token's own run or a run of the token's instance, directly or through
  ``parent_run_id``. Its ``launched_by`` and ``key_id`` were stamped by the
  hub when the record was created (the API opens a chat turn's run before the
  turn is queued, chat/routing.py);
- the token's own run, for a task run's container;
- the chat turn the call names (``turn``: the id of the mailbox message the
  turn came in), when it is a message of the token's instance: the API wrote
  ``user_id`` and ``key_id`` into it when it queued the turn
  (services/routing.py ``turn_payload``).

The container sends ids only. An id that does not belong to its token is
ignored, so a container cannot charge its work to anyone its own turns were
not for.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger(__name__)

#: How far up ``parent_run_id`` a named run is followed to its token.
_MAX_DEPTH = 8

#: Seconds a resolution is kept: a turn makes many calls with the same ids.
_TTL = 30.0
_cache: Dict[tuple, tuple] = {}


def _nobody(user_id: Any) -> bool:
    from common.auth import LOCAL_OPERATOR_ID
    from common.identity import SERVICE_PRINCIPAL
    return not user_id or str(user_id) in (LOCAL_OPERATOR_ID, SERVICE_PRINCIPAL.id)


def _get_run(run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not run_id:
        return None
    from managers.run_manager import get_run_by_id
    return get_run_by_id(str(run_id))


def _belongs(rec: Optional[Dict[str, Any]], token_row: Mapping[str, Any]) -> bool:
    """Whether ``rec`` is the token's run, a run of the token's instance, or
    below one of them."""
    own_run = str(token_row.get("run_id") or "")
    own_instance = str(token_row.get("instance_id") or "")
    for _ in range(_MAX_DEPTH):
        if rec is None:
            return False
        if own_run and str(rec.get("run_id") or "") == own_run:
            return True
        if own_instance and str(rec.get("instance_id") or "") == own_instance:
            return True
        rec = _get_run(rec.get("parent_run_id"))
    return False


def _from_run(rec: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not rec or _nobody(rec.get("launched_by")):
        return {}
    out = {"user": str(rec["launched_by"])}
    if rec.get("key_id"):
        out["key"] = str(rec["key_id"])
    return out


def _from_turn(msg_id: Optional[str], token_row: Mapping[str, Any]) -> Dict[str, str]:
    own_instance = str(token_row.get("instance_id") or "")
    if not msg_id or not own_instance:
        return {}
    from instances import inbox
    message = inbox.get(str(msg_id))
    if not message or str(message.get("instance_id") or "") != own_instance:
        return {}
    payload = inbox.payload_of(message)
    if _nobody(payload.get("user_id")):
        return {}
    out = {"user": str(payload["user_id"])}
    if payload.get("key_id"):
        out["key"] = str(payload["key_id"])
    return out


def _resolve(token_row: Mapping[str, Any], run_id: str, turn: str) -> Dict[str, str]:
    named = _get_run(run_id) if run_id else None
    belongs = named is not None and _belongs(named, token_row)
    out: Dict[str, str] = {"run": run_id} if belongs else {}
    for found in (_from_run(named) if belongs else {},
                  _from_run(_get_run(token_row.get("run_id"))),
                  _from_turn(turn, token_row)):
        if found:
            out.update(found)
            break
    return out


def resolve(token_row: Mapping[str, Any], *, run_id: Optional[str] = None,
            turn: Optional[str] = None) -> Dict[str, str]:
    """``{"user", "key", "run"}``, each only when known: the person and key a
    call of the container holding ``token_row`` is charged to, and ``run``
    when the run the call named belongs to the token. Empty when nothing the
    hub wrote names a person. Never raises."""
    run_id = str(run_id or "").strip()
    turn = str(turn or "").strip()
    cache_key = (str(token_row.get("token_id") or token_row.get("token_hash") or ""), run_id, turn)
    now = time.monotonic()
    hit = _cache.get(cache_key)
    if hit is not None and now - hit[0] < _TTL:
        return dict(hit[1])
    try:
        out = _resolve(token_row, run_id, turn)
    except Exception:  # noqa: BLE001 - an unreadable record charges nobody, it never fails the call
        log.debug("charge not resolved for run %s turn %s", run_id, turn, exc_info=True)
        return {}
    if len(_cache) > 4096:
        _cache.clear()
    _cache[cache_key] = (now, out)
    return dict(out)


def call_ids() -> Dict[str, str]:
    """What a process with no database sends with a call to the hub: the run
    executing now and the chat turn it serves, ids only."""
    out: Dict[str, str] = {}
    try:
        from common.agent_context import current_run_id
        run_id = current_run_id.get()
        if run_id:
            out["run"] = str(run_id)
    except Exception:  # noqa: BLE001 - no run in context
        pass
    import sys
    runs = sys.modules.get("chat.runs")  # only a process that serves chat turns has one
    try:
        turn = ((runs.turn_context() if runs is not None else None) or {}).get("msg_id")
        if turn:
            out["turn"] = str(turn)
    except Exception:  # noqa: BLE001 - not a chat turn
        pass
    return out


__all__ = ["resolve", "call_ids"]
