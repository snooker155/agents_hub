"""
One chat turn executed inside a service replica.

The backend no longer runs an agent for a chat, ``/v1``, widget or Telegram
turn: chat/routing.py writes the turn into a replica's mailbox as a ``turn``
message (instances/inbox.py) and relays what comes back. This module is the
other end, called by the replica's loop (runtime/instance_run.py) for such a
message: it rebuilds the request, runs the very same pipeline the backend
used to run (chat/pipelines.py execute_locally) in this process, and posts
every event to the backend, which fans it out on the conversation's channel
(``chat:<conversation_id>``, what the Chat page and the relay read) and on the
instance's (``instance:<id>``, what the instance page reads).

What the pipeline does around the agent is unchanged and happens here: the
prompt and its context, attachments, compaction, the run record and its log,
handoffs, the transcript written to the stored chat, journaling into shared
memory. The run record carries this replica's id and its service's, so the
watchdog treats it as a carrier run and a service conversation keeps one
history across replicas (instances/history.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Consecutive ``token`` events are merged before posting: one request per
#: token would cost more than the model call for a long answer.
TOKEN_FLUSH_CHARS = 160
TOKEN_FLUSH_SECONDS = 0.08
POST_TIMEOUT_SECONDS = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventForwarder:
    """Posts a turn's events to the backend, in order, tokens batched.

    ``POST /api/instances/{id}/events`` (routes/instances.py) republishes each
    event on the channels named in the body. Posting is synchronous and
    best-effort: a backend that cannot be reached loses the live view, never
    the run, which records itself into the database as it goes.
    """

    def __init__(self, instance_id: str, channels: List[str], port: Optional[int] = None) -> None:
        self.instance_id = instance_id
        self.channels = list(channels)
        port = port or int(os.environ.get("DASHBOARD_PORT", "8000"))
        self._url = f"http://localhost:{port}/api/instances/{instance_id}/events"
        self._buf: List[str] = []
        self._buf_len = 0
        self._buf_base: Optional[Dict[str, Any]] = None
        self._buf_channels: Optional[List[str]] = None
        self._last_flush = 0.0
        self._lock = threading.Lock()
        self._failed = 0

    def _send(self, event: Dict[str, Any], channels: Optional[List[str]]) -> None:
        try:
            import requests as _req
            from common.auth import auth_headers
            _req.post(self._url, json={"channels": channels or self.channels, "event": event},
                      headers=auth_headers(), timeout=POST_TIMEOUT_SECONDS)
            self._failed = 0
        except Exception as exc:  # noqa: BLE001 - the live view is an extra, the run goes on
            self._failed += 1
            if self._failed in (1, 10, 100):
                log.warning("turn events: could not reach the backend at %s: %s", self._url, exc)

    def flush(self) -> None:
        with self._lock:
            if not self._buf:
                return
            text = "".join(self._buf)
            base = dict(self._buf_base or {})
            channels = self._buf_channels
            self._buf.clear()
            self._buf_len = 0
            self._buf_base = None
            self._buf_channels = None
            self._last_flush = time.monotonic()
        self._send({**base, "token": text}, channels)

    def post(self, event: Dict[str, Any], channels: Optional[List[str]] = None) -> None:
        """Send one event, after whatever tokens are buffered."""
        if (event.get("type") == "token" and isinstance(event.get("token"), str)
                and not event.get("delegation")):
            with self._lock:
                same_stream = (self._buf_base is None
                               or (self._buf_base.get("run_id") == event.get("run_id")
                                   and self._buf_channels == channels))
            if not same_stream:
                self.flush()
            with self._lock:
                if self._buf_base is None:
                    self._buf_base = {k: v for k, v in event.items() if k != "token"}
                    self._buf_channels = channels
                    if self._last_flush == 0.0:
                        self._last_flush = time.monotonic()
                self._buf.append(event["token"])
                self._buf_len += len(event["token"])
                due = (self._buf_len >= TOKEN_FLUSH_CHARS
                       or time.monotonic() - self._last_flush >= TOKEN_FLUSH_SECONDS)
            if due:
                self.flush()
            return
        self.flush()
        self._send(event, channels)


def _pipeline_kind(request: Any, kind: Optional[str]) -> str:
    if kind in ("agent", "flow", "team"):
        return kind
    if getattr(request, "team_id", None):
        return "team"
    if getattr(request, "flow_id", None):
        return "flow"
    return "agent"


def execute_turn(instance_id: str, workspace_abs: Optional[str], message: Dict[str, Any]) -> Optional[str]:
    """Run the chat turn a mailbox message carries. Returns the run id, or None.

    Called on a worker thread of the replica's loop; it opens an event loop of
    its own for the pipeline. Every outcome ends with ``chat_stream_end`` on
    the conversation's channel and ``instance_stream_end`` on the instance's,
    so a relay or a page waiting on either is released.
    """
    from chat import runs as chat_runs
    from chat.models import ChatRequest
    from instances import inbox as instance_inbox
    from instances import registry as instance_registry
    from instances import store as instance_store

    msg_id = str(message.get("msg_id") or "")
    payload = instance_inbox.payload_of(message)
    instance = instance_store.get(instance_id) or {}
    try:
        request = ChatRequest(**(payload.get("request") or {}))
    except Exception as exc:  # noqa: BLE001 - a turn the request model rejects is answered as failed
        log.warning("turn %s: bad request payload: %s", msg_id[:12], exc)
        instance_inbox.mark_error(msg_id, f"bad turn payload: {exc}")
        return None
    kind = _pipeline_kind(request, payload.get("kind"))
    conversation_id = request.conversation_id
    public_cid = instance_inbox.public_conversation(
        instance_inbox.normalize_conversation(message.get("conversation_id")))
    chat_channel = f"chat:{conversation_id}" if conversation_id else None
    instance_channel = f"instance:{instance_id}"
    channels = [c for c in (chat_channel, instance_channel) if c]
    forwarder = EventForwarder(instance_id, channels)
    stamp = {"conversation_id": conversation_id, "origin_client": request.client_id,
             "turn_msg_id": msg_id}
    state: Dict[str, Any] = {"run_id": None, "done": False, "cap": False, "spent": 0.0}

    turn_context = {
        "instance_id": instance_id,
        "service_id": instance.get("service_id"),
        "msg_id": msg_id,
        "conversation_id": message.get("conversation_id"),
        "budget_usd": payload.get("budget_usd"),
        "agent_version": payload.get("agent_version"),
    }

    def _journal_budget(event: Dict[str, Any]) -> None:
        if event.get("error_code") != "budget" or not turn_context.get("service_id"):
            return
        try:
            from services import store as service_store
            cap = event.get("budget") or {}
            service_store.add_event(
                str(turn_context["service_id"]), "budget_cap",
                f"turn {str(event.get('run_id') or state['run_id'] or '')[:8]} stopped at "
                f"${float(cap.get('spent_usd') or 0):.4f} of ${float(cap.get('limit_usd') or 0):.2f}",
                instance_id)
        except Exception:  # noqa: BLE001 - the journal is an extra
            log.debug("budget event not journaled", exc_info=True)

    def _forward(event: Dict[str, Any]) -> None:
        out = {**event, **stamp}
        if event.get("type") == "meta":
            run_id = str(event.get("run_id") or "")
            if run_id and not state["run_id"]:
                state["run_id"] = run_id
                try:
                    instance_inbox.attach_run(msg_id, run_id)
                except Exception:  # noqa: BLE001 - the reply lookup falls back to the run's own record
                    log.debug("attach_run failed for %s", msg_id, exc_info=True)
            out.update({"instance_id": instance_id, "msg_id": msg_id,
                        "conversation_id": conversation_id or public_cid})
        if event.get("type") == "done":
            state["done"] = True
            _journal_budget(event)
        forwarder.post(out)

    async def _drive() -> None:
        from chat import pipelines
        from common import api_keys, identity

        tokens: List[Any] = []
        ctx_token = chat_runs.set_turn_context(turn_context)
        if payload.get("user_id"):
            tokens.append(("user", identity.set_current_user(str(payload["user_id"]))))
        if payload.get("key_id"):
            tokens.append(("key", api_keys.set_current_key_id(str(payload["key_id"]))))
        try:
            async for event in pipelines.execute_locally(request, kind):
                if isinstance(event, dict):
                    _forward(event)
        finally:
            # What the turn spent under its cap, kept on the run so an operator
            # can see the cap working (or not) without reading a log.
            try:
                from agents.callbacks import guards as _guards
                state["cap"] = _guards.turn_cap() is not None
                state["spent"] = _guards.turn_spend_usd()
                state["trace"] = _guards.turn_ledger_trace()
            except Exception:  # noqa: BLE001 - an extra, never the turn
                log.debug("turn %s: could not read the spend guard", msg_id[:12], exc_info=True)
            for which, token in reversed(tokens):
                try:
                    if which == "user":
                        identity.reset_current_user(token)
                    else:
                        api_keys.reset_current_key_id(token)
                except Exception:  # noqa: BLE001 - a reset that cannot apply changes nothing
                    log.debug("turn %s: could not reset the %s context", msg_id[:12], which, exc_info=True)
            chat_runs.reset_turn_context(ctx_token)

    def _scoped_run() -> None:
        # The service's money cap, bound to this turn's context: every model
        # call of the turn (and of the agents it delegates to) charges one
        # ledger, and the cap ends the turn as a failure the reply names.
        from common.run_budget import turn_cap
        ws_name = str(request.workspace or instance.get("workspace") or "default")
        with turn_cap(payload.get("budget_usd"), ws_name):
            if kind != "agent" or not request.agent_id:
                asyncio.run(_drive())
                return
            try:
                from common import secrets as _secrets
                scope = _secrets.activate(ws_name, request.agent_id,
                                          payload.get("user_id") or instance.get("started_by"))
            except Exception:  # noqa: BLE001 - no secret store: run without one
                import contextlib
                scope = contextlib.nullcontext()
            with scope:
                asyncio.run(_drive())

    log.info("turn %s (%s, %s) starting on %s", msg_id[:12], kind, public_cid, instance_id)
    instance_registry.mark_active(instance_id, None, str(request.message or "")[:200] or None)
    forwarder.post({**stamp, "type": "turn_start", "message": request.message,
                    "source": request.source or "chat", "agent_id": request.agent_id,
                    "flow_id": request.flow_id, "team_id": request.team_id,
                    "started_at": _now_iso()}, [chat_channel] if chat_channel else [instance_channel])
    try:
        _scoped_run()
    except Exception as exc:  # noqa: BLE001 - whatever escaped the pipeline is the turn's one failure
        status = getattr(exc, "status_code", None)
        detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
        log.warning("turn %s failed: %s", msg_id[:12], detail)
        try:
            instance_inbox.mark_error(msg_id, str(detail)[:2000])
        except Exception:  # noqa: BLE001 - the failure is already logged and posted below
            log.debug("turn %s: could not mark the message failed", msg_id[:12], exc_info=True)
        if not state["done"]:
            forwarder.post({**stamp, "type": "done", "ok": False, "error": str(detail),
                            "status": status, "run_id": state["run_id"],
                            "agent_id": request.agent_id})
    finally:
        if state["run_id"] and state.get("cap"):
            try:
                from managers.run_manager import update_run
                update_run(state["run_id"], {"budget_spent_usd": round(float(state.get("spent") or 0.0), 6),
                                             "budget_trace": state.get("trace") or {}})
            except Exception:  # noqa: BLE001 - an extra, never the turn
                log.debug("budget spend not recorded for %s", state["run_id"], exc_info=True)
        if chat_channel:
            forwarder.post({**stamp, "type": "chat_stream_end"}, [chat_channel])
        forwarder.post({"type": "instance_stream_end", "run_id": state["run_id"], "msg_id": msg_id,
                        "conversation_id": conversation_id or public_cid}, [instance_channel])
    return state["run_id"]


__all__ = ["EventForwarder", "execute_turn"]
