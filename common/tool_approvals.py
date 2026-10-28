"""
A tool call that waits for a person inside a chat turn (docs/hooks.md, "In chat").

A gated call in a task parks the task and the operator answers from the task
page (tools/approval.py). A chat turn has no task to park, and until this
existed the gate could only refuse the call and let the agent ask in prose:
the person's "yes" then arrived as a new turn, the agent had to repeat the
call, and the gate refused it again. Now a dashboard chat turn holds the call
instead: the guard records it here, the chat shows a card with Approve and
Deny, and the turn's worker thread waits until a decision, a timeout or a
stop. Approve runs the call in the same turn; Deny hands the refusal (with
the person's note) back as the tool's output. No new user message, no new
turn, the history does not move.

Who answers: the run's owner or an admin, the rule steering's ``system`` mode
follows (dashboard/backend/routes/tool_approvals.py checks it). Surfaces with
nobody in the dashboard to answer (Telegram, the widget, channels, ``/v1``)
keep the advisory refusal: :func:`chat_context` returns None for them.

A flow node that runs in the background (a flow on a task, a flow started
from a chat or a schedule, a loop's pass) and a team member's turn have no
chat turn to show the card in and, most of the time, no task to park either.
The node's driver, or the team runner, opens a :func:`background_scope`
around the agent, and the call waits there the same way: the row names its
flow run or team run (``flow_run_id``, ``team_run_id``, migrations 0049 and
0050), the card goes out on that run's live channel, a notification lands in
the bell, and the agent's thread waits for an answer, a stop of the run, or
the longer background timeout. The run's page, the chat that started the
run and the bell all answer it.

Rows live in ``tool_approvals`` (migration 0035). The waiting process reaches
them through :mod:`common.state_transport`, the way the agent loop reaches its
steering messages: directly when it can open the database (the backend, a
service replica), over ``/api/run-state`` when it is a run container with a
read-only state mount.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

from common import db

log = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED, STATUS_EXPIRED, STATUS_CANCELLED)

#: How a person answers.
DECISIONS = {"approve": STATUS_APPROVED, "deny": STATUS_DENIED}

#: How long a call waits for a person, in seconds, unless the workspace
#: (``settings.tool_approval_timeout``) or ``AGENTS_HUB_TOOL_APPROVAL_TIMEOUT``
#: says otherwise. Past it the call is refused with a reason that says so.
DEFAULT_TIMEOUT_SECONDS = 600.0
TIMEOUT_ENV = "AGENTS_HUB_TOOL_APPROVAL_TIMEOUT"
MIN_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 24 * 3600.0

#: How long a call of a background flow node waits, in seconds, unless the
#: workspace (``settings.tool_approval_background_timeout``) or
#: ``AGENTS_HUB_BACKGROUND_TOOL_APPROVAL_TIMEOUT`` says otherwise. Nobody
#: watches a background run, so the person gets a day to see the bell.
DEFAULT_BACKGROUND_TIMEOUT_SECONDS = 24 * 3600.0
BACKGROUND_TIMEOUT_ENV = "AGENTS_HUB_BACKGROUND_TOOL_APPROVAL_TIMEOUT"
#: A background wait looks for an answer, and for a stop of its flow, less
#: often: nobody is watching it to the second.
BACKGROUND_POLL_SECONDS = 2.0
#: A background wait has no chat relay to keep awake; its rarer heartbeat only
#: keeps the flow run's live channel (and its replay) from going quiet.
BACKGROUND_HEARTBEAT_SECONDS = 300.0

#: How often the waiting thread looks for an answer. One indexed read of a
#: primary key, so it costs nothing a person would notice.
POLL_SECONDS = 0.5
#: How often a waiting turn says it is still alive on its stream. The chat
#: relay (chat/routing.py) gives up on a turn that has been quiet for its
#: idle timeout, and a person reading the card for a while is not a hung turn.
HEARTBEAT_SECONDS = 20.0

#: Message origins of a run whose chat can show the card: the dashboard's Chat
#: page (``source`` None, recorded as ``chat``) and the assistant's thread
#: (routes/assistant.py). Telegram, the widget, channels, ``/v1`` and an
#: instance's page have nobody in front of the card.
INTERACTIVE_ORIGINS = frozenset({"chat", "assistant-chat"})

#: Longest note kept, in characters.
MAX_NOTE_CHARS = 2000

_COLUMNS = ("approval_id", "run_id", "flow_run_id", "team_run_id", "agent_id", "workspace",
            "conversation_id", "owner", "tool", "input", "reason", "fingerprint", "decided_by_rule",
            "hook", "status", "note", "decided_by", "decided_by_name", "created_at", "expires_at",
            "decided_at")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(row: Any) -> Dict[str, Any]:
    out = {k: row[k] for k in _COLUMNS}
    out["input"] = db.loads(out.get("input"), out.get("input"))
    # ``by`` is the guard's word for what asked (hook, policy, auto,
    # guardrail); the column has a longer name so it is not read as a person.
    out["by"] = out.pop("decided_by_rule", None)
    return out


def _author_fields(author: Any) -> tuple:
    """``(id, name)`` from a principal, a dict or a plain name."""
    if author is None:
        return None, None
    if isinstance(author, str):
        return None, author or None
    if isinstance(author, dict):
        return (str(author.get("id") or "") or None,
                str(author.get("name") or author.get("username") or "") or None)
    return (str(getattr(author, "id", "") or "") or None,
            str(getattr(author, "username", "") or getattr(author, "name", "") or "") or None)


# -------------------- the store --------------------

def open_approval(*, run_id: str, tool: str, tool_input: Any = None, reason: str = "",
                  fingerprint: str = "", by: str = "", hook: str = "", agent_id: str = "",
                  workspace: Optional[str] = None, conversation_id: Optional[str] = None,
                  owner: Optional[str] = None, flow_run_id: Optional[str] = None,
                  team_run_id: Optional[str] = None,
                  timeout_s: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Record one call that waits for a person. Returns the stored row.
    ``flow_run_id`` is set for a call held in a background flow node,
    ``team_run_id`` for one held in a team member's turn."""
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    created = _now()
    expires = created + timedelta(seconds=float(timeout_s))
    approval_id = "appr_" + uuid.uuid4().hex[:16]
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tool_approvals (approval_id, run_id, flow_run_id, team_run_id, agent_id, "
            "workspace, conversation_id, owner, tool, input, reason, fingerprint, decided_by_rule, hook, "
            "status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (approval_id, run_id, flow_run_id or None, team_run_id or None, agent_id or None,
             workspace or None,
             conversation_id or None, owner or None, str(tool or ""),
             json.dumps(tool_input, ensure_ascii=False, default=str),
             str(reason or ""), fingerprint or None, by or None, hook or None, STATUS_PENDING,
             created.isoformat(), expires.isoformat()),
        )
    return get(approval_id) or {}


def get(approval_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM tool_approvals WHERE approval_id = ?", (str(approval_id),)).fetchone()
    return _row(row) if row is not None else None


def list_for_run(run_id: str, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every call of ``run_id`` that waited, oldest first; ``status`` narrows it."""
    if status:
        rows = db.get_conn().execute(
            "SELECT * FROM tool_approvals WHERE run_id = ? AND status = ? ORDER BY created_at",
            (str(run_id), str(status))).fetchall()
    else:
        rows = db.get_conn().execute(
            "SELECT * FROM tool_approvals WHERE run_id = ? ORDER BY created_at",
            (str(run_id),)).fetchall()
    return [_row(r) for r in rows]


def list_for_flow_run(flow_run_id: str, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every call the nodes of a background flow run waited on, oldest first;
    ``status`` narrows it. What the flow run's page, the loop's page and the
    chat that started the run read to show the cards."""
    return _list_for_group("flow_run_id", flow_run_id, status)


def list_for_team_run(team_run_id: str, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every call the members of a team run waited on, oldest first; what the
    team page and the chat that started the run read to show the cards."""
    return _list_for_group("team_run_id", team_run_id, status)


def _list_for_group(column: str, group_run_id: str, status: Optional[str]) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM tool_approvals WHERE {column} = ?"
    params: List[Any] = [str(group_run_id)]
    if status:
        sql += " AND status = ?"
        params.append(str(status))
    sql += " ORDER BY created_at"
    return [_row(r) for r in db.get_conn().execute(sql, tuple(params)).fetchall()]


def list_for_tool(tool: str, *, status: Optional[str] = None,
                  workspace: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Every call of ``tool`` that waited, newest first; ``status`` and
    ``workspace`` narrow it. The Connectors page lists the connection
    proposals still open this way (connectors/proposals.py)."""
    sql = "SELECT * FROM tool_approvals WHERE tool = ?"
    params: List[Any] = [str(tool)]
    if status:
        sql += " AND status = ?"
        params.append(str(status))
    if workspace:
        sql += " AND (workspace = ? OR workspace IS NULL)"
        params.append(str(workspace))
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit or 100), 500)))
    return [_row(r) for r in db.get_conn().execute(sql, tuple(params)).fetchall()]


def list_pending(*, since: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Calls still waiting, newest first; ``since`` (ISO time) keeps those
    opened after it. The assistant looks here for the card a spoken yes is
    about (routes/assistant.py)."""
    sql = "SELECT * FROM tool_approvals WHERE status = ?"
    params: List[Any] = [STATUS_PENDING]
    if since:
        sql += " AND created_at >= ?"
        params.append(str(since))
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit or 50), 500)))
    return [_row(r) for r in db.get_conn().execute(sql, tuple(params)).fetchall()]


def _settle(approval_id: str, status: str, *, note: str = "", author: Any = None) -> Optional[Dict[str, Any]]:
    """Move a pending row to ``status``. Returns the row when this call moved
    it, None when it was no longer pending (answered, expired or cancelled by
    somebody else first): the conditional UPDATE is what makes one answer win."""
    author_id, author_name = _author_fields(author)
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE tool_approvals SET status = ?, note = ?, decided_by = ?, decided_by_name = ?, "
            "decided_at = ? WHERE approval_id = ? AND status = 'pending'",
            (status, (str(note or "").strip()[:MAX_NOTE_CHARS]) or None, author_id, author_name,
             _now().isoformat(), str(approval_id)))
        changed = int(getattr(cur, "rowcount", 0) or 0) > 0
    return get(approval_id) if changed else None


def decide(approval_id: str, decision: str, *, note: str = "", author: Any = None) -> Optional[Dict[str, Any]]:
    """A person's answer. ``decision`` is ``approve`` or ``deny``.

    Returns the row when the answer landed, None when the call was no longer
    waiting. Raises ``ValueError`` for an unknown decision. An answer that
    arrives after ``expires_at`` but before the waiting thread noticed is
    refused too: the agent is about to be told nobody answered, and a late
    yes must not run a call the person was told had timed out.
    """
    status = DECISIONS.get(str(decision or "").strip().lower())
    if status is None:
        raise ValueError("decision must be approve or deny")
    row = get(approval_id)
    if row is None or row.get("status") != STATUS_PENDING:
        return None
    if _expired(row):
        _settle(approval_id, STATUS_EXPIRED)
        return None
    return _settle(approval_id, status, note=note, author=author)


def close(approval_id: str, status: str, *, note: str = "") -> Optional[Dict[str, Any]]:
    """End a pending row without a person's answer (timeout, stop). Returns
    the row as it now stands, whoever settled it."""
    if status not in (STATUS_EXPIRED, STATUS_CANCELLED):
        raise ValueError("close takes expired or cancelled")
    _settle(approval_id, status, note=note)
    return get(approval_id)


def _expired(row: Dict[str, Any]) -> bool:
    try:
        return _now() >= datetime.fromisoformat(str(row.get("expires_at")))
    except (TypeError, ValueError):
        return False


def is_expired(row: Dict[str, Any]) -> bool:
    """Whether a row's deadline has passed, whatever its status still says: a
    call nobody waits on any more (a proposal left on the Connectors page) is
    only closed when somebody next answers it."""
    return _expired(row)


# -------------------- where a call may wait --------------------

def _timeout(settings: Optional[Dict[str, Any]], key: str, env: str, default: float) -> float:
    raw: Any = (settings or {}).get(key)
    if raw in (None, ""):
        try:
            from common.config import live_setting
            raw = live_setting(env, "")
        except Exception:  # noqa: BLE001 - no config module in a stripped-down process; the environment decides
            raw = os.environ.get(env, "")
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, value))


def timeout_seconds(settings: Optional[Dict[str, Any]] = None) -> float:
    """How long a call waits in a chat turn: the workspace setting, else the
    env, else 600 s, clamped so a typo can neither refuse every call at once
    nor hold a turn for days."""
    return _timeout(settings, "tool_approval_timeout", TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)


def background_timeout_seconds(settings: Optional[Dict[str, Any]] = None) -> float:
    """How long a call waits in a background flow node: the workspace setting
    (``tool_approval_background_timeout``), else the env, else a day."""
    return _timeout(settings, "tool_approval_background_timeout", BACKGROUND_TIMEOUT_ENV,
                    DEFAULT_BACKGROUND_TIMEOUT_SECONDS)


def transport_for(run_id: str) -> Any:
    """The state transport for ``run_id``'s approvals: the run's own transport
    in its own process (a run container may only have HTTP), direct database
    access everywhere else (the backend, a service replica). The same rule
    agents/loop_ext/steering.py applies."""
    from common.state_transport import DirectStateTransport, get_state_transport
    if run_id and os.environ.get("AGENT_RUN_ID", "") == run_id:
        try:
            return get_state_transport()
        except Exception:  # noqa: BLE001 - an unreadable setting falls back to direct access
            return DirectStateTransport()
    return DirectStateTransport()


def _turn_source() -> Optional[str]:
    """The ``source`` of the chat turn this thread belongs to, when the turn
    context carries it (chat/turns.py). None when there is no turn context."""
    try:
        from chat.runs import turn_context
        ctx = turn_context()
    except Exception:  # noqa: BLE001 - no chat package in this process: not a chat turn
        return None
    if not ctx or "source" not in ctx:
        return None
    return str(ctx.get("source") or "chat")


def _owner_of(run: Dict[str, Any]) -> Optional[str]:
    """The user who may answer besides an admin: the conversation's owner,
    else the user this turn acts for."""
    conv_id = str(run.get("task_id") or run.get("conversation_id") or "")
    if conv_id and str(run.get("session_type") or "") == "chat":
        try:
            from common import chat_store
            owner = str((chat_store.get_chat(conv_id) or {}).get("owner") or "")
            if owner:
                return owner
        except Exception:  # noqa: BLE001 - an unreadable chat leaves the turn's own user
            log.debug("tool approvals: chat owner lookup failed for %s", conv_id, exc_info=True)
    # The person the run is charged to (common/attribution.py): right on a
    # runner replica too, where no request carries the user.
    from common.auth import LOCAL_OPERATOR_ID
    launched_by = str(run.get("launched_by") or "")
    if launched_by and launched_by not in (LOCAL_OPERATOR_ID, "service"):
        return launched_by
    try:
        from common import identity
        return identity.current_user_id() or None
    except Exception:  # noqa: BLE001 - no identity: admins answer
        return None


def chat_context(run_id: str) -> Optional[Dict[str, Any]]:
    """What holding a call in this chat turn needs, or None when it cannot be
    held here and the advisory refusal stands.

    Held only when all of these are true: there is a run id; the turn streams
    to a client (``common.stream_sink`` has an emitter, which every chat
    pipeline installs); and the run is a dashboard chat turn, by its record's
    ``message_origin`` or, for a run started inside one (a delegated agent),
    by the turn's own source.
    """
    run_id = str(run_id or "").strip()
    if not run_id:
        return None
    try:
        from common import stream_sink
        emitter = stream_sink.get_emitter()
    except Exception:  # noqa: BLE001 - no stream module: nothing can show the card
        return None
    if emitter is None:
        return None
    transport = transport_for(run_id)
    try:
        run = transport.get_run(run_id) or {}
    except Exception:  # noqa: BLE001 - an unreadable run cannot be answered from its chat
        log.debug("tool approvals: run %s unreadable", run_id, exc_info=True)
        return None
    if str(run.get("session_type") or "") == "chat":
        origin = str(run.get("message_origin") or "chat")
    else:
        origin = _turn_source() or ""
    if origin not in INTERACTIVE_ORIGINS:
        return None
    return {
        "run_id": run_id,
        "run": run,
        "emitter": emitter,
        "transport": transport,
        "owner": _owner_of(run),
        # A chat run's task_id is its conversation (chat/runs.py create_chat_run).
        "conversation_id": (run.get("task_id") if str(run.get("session_type") or "") == "chat"
                            else None) or run.get("conversation_id"),
    }


# -------------------- a background flow node or team turn --------------------

#: The kinds of background run a call can wait in, and the column that links
#: a waiting call to the run (migrations 0049, 0050).
KIND_FLOW = "flow"
KIND_TEAM = "team"
RUN_COLUMNS = {KIND_FLOW: "flow_run_id", KIND_TEAM: "team_run_id"}

_background: ContextVar[Optional[Dict[str, Any]]] = ContextVar("tool_approval_background", default=None)


@contextmanager
def background_scope(*, kind: str, group_run_id: str, entity_id: str = "", entity_name: str = "",
                     seat_id: str = "", seat_label: str = "",
                     emitter: Optional[Callable[[dict], None]] = None,
                     should_stop: Optional[Callable[[], bool]] = None,
                     on_wait: Optional[Callable[[bool], None]] = None,
                     link: Optional[Dict[str, Any]] = None) -> Iterator[None]:
    """Around one agent run of a background flow node (flow/task_driver.py,
    ``kind`` ``flow``) or of a team member's turn (teams/runner.py, ``team``):
    a call of that run that needs a person waits for one
    (:func:`background_context`) instead of being refused.

    ``group_run_id`` is the flow run or the team run, ``entity_id`` and
    ``entity_name`` the flow or the team, ``seat_id`` and ``seat_label`` the
    node or the member. ``emitter`` puts the card on the run's live channel,
    stamped with the node or the turn; ``should_stop`` is the run's own stop
    check (a flow's, a loop's, a team's), so a stopped run ends the wait;
    ``on_wait`` is told ``True`` when a call starts waiting and ``False``
    when it stops (a team pauses its turn clock meanwhile); ``link`` adds
    what a notification links to (a loop's ``loop_id`` and ``loop_run_id``).
    """
    if kind not in RUN_COLUMNS:
        raise ValueError(f"unknown background run kind: {kind!r}")
    token = _background.set({
        "kind": kind, "group_run_id": str(group_run_id or ""),
        "entity_id": str(entity_id or ""), "entity_name": str(entity_name or ""),
        "seat_id": str(seat_id or ""), "seat_label": str(seat_label or ""),
        "emitter": emitter, "should_stop": should_stop, "on_wait": on_wait,
        "link": {k: v for k, v in (link or {}).items() if v},
    })
    try:
        yield
    finally:
        _background.reset(token)


def _no_emit(_event: dict) -> None:
    return None


def background_context(run_id: str) -> Optional[Dict[str, Any]]:
    """What holding a call in a background flow node or team turn needs, or
    None when this run is not inside a :func:`background_scope` and the
    caller's other rule stands (a task parks, anything else gets the advisory
    refusal)."""
    scope = _background.get()
    run_id = str(run_id or "").strip()
    if not scope or not run_id or not scope.get("group_run_id"):
        return None
    transport = transport_for(run_id)
    try:
        run = transport.get_run(run_id) or {}
    except Exception:  # noqa: BLE001 - the run record only names who launched it
        log.debug("tool approvals: run %s unreadable", run_id, exc_info=True)
        run = {}
    notice: Dict[str, Any] = {}
    on_wait = scope.get("on_wait")

    def on_open(row: Dict[str, Any]) -> None:
        if on_wait is not None:
            on_wait(True)
        notice["id"] = _notify_waiting(row, scope)

    def on_settle(_row: Dict[str, Any]) -> None:
        if on_wait is not None:
            on_wait(False)
        _retire_notification(notice.get("id"))

    return {
        "run_id": run_id,
        "run": run,
        "emitter": scope.get("emitter") or _no_emit,
        "transport": transport,
        "owner": _owner_of(run) if run else None,
        "conversation_id": None,
        RUN_COLUMNS[scope["kind"]]: scope["group_run_id"],
        "should_stop": scope.get("should_stop"),
        "poll": BACKGROUND_POLL_SECONDS,
        "heartbeat": BACKGROUND_HEARTBEAT_SECONDS,
        "on_open": on_open,
        "on_settle": on_settle,
        "background": True,
    }


def _notify_waiting(row: Dict[str, Any], scope: Dict[str, Any]) -> Optional[str]:
    """The bell entry for a call that waits in a background run: nobody is
    looking at the run, so this is how the person learns it stopped. Returns
    the notification's id, None when none could be made."""
    try:
        from managers.runs.notifications import notification_message
        from plans import service as plan_service
        team = scope.get("kind") == KIND_TEAM
        tool = str(row.get("tool") or "")
        agent = str(row.get("agent_id") or "")
        where = scope.get("entity_name") or scope.get("entity_id") or ""
        seat = scope.get("seat_label") or scope.get("seat_id") or ""
        reason = str(row.get("reason") or "").strip()
        body = (f"'{seat or agent}' in the {'team' if team else 'flow'} '{where}' wants to call "
                f"{tool} and waits for your answer. Approve or deny it here or on the run page.")
        if reason:
            body += f" {reason}"
        ids = ({"team_id": scope.get("entity_id") or None,
                "team_run_id": scope.get("group_run_id") or None} if team else
               {"flow_id": scope.get("entity_id") or None,
                "flow_run_id": scope.get("group_run_id") or None,
                "node_id": scope.get("seat_id") or None})
        params = {"tool": tool, "agent": (seat if team else agent or seat) or None,
                  "reason": reason or None, ("team" if team else "flow"): where or None}
        n = plan_service.create_notification(
            title=f"Approval needed: {tool}",
            body=body,
            severity="warning",
            source={
                "kind": "tool_approval",
                "approval_id": row.get("approval_id"),
                "run_id": row.get("run_id"),
                **ids,
                **(scope.get("link") or {}),
                "message": notification_message(
                    "toolApprovalNeededTeam" if team else "toolApprovalNeeded", **params),
            },
            workspace=row.get("workspace"),
        )
        return str(getattr(n, "id", "") or "") or None
    except Exception:  # noqa: BLE001 - the card on the run page still answers the call
        log.debug("tool approvals: no notification for %s", row.get("approval_id"), exc_info=True)
        return None


def _retire_notification(notification_id: Optional[str]) -> None:
    """Mark the bell entry of a call that no longer waits as read."""
    if not notification_id:
        return
    try:
        from plans import service as plan_service
        plan_service.mark_notification_read(notification_id)
    except Exception:  # noqa: BLE001 - an unread entry whose buttons say "no longer waiting" is harmless
        log.debug("tool approvals: notification %s not retired", notification_id, exc_info=True)


# -------------------- the wait --------------------

def _emit(emitter: Callable[[dict], None], event: Dict[str, Any]) -> None:
    try:
        emitter(event)
    except Exception:  # noqa: BLE001 - the card is the way to answer, but the wait still ends on its own
        log.debug("tool approvals: could not emit %s", event.get("type"), exc_info=True)


def _event(row: Dict[str, Any], kind: str) -> Dict[str, Any]:
    event = {
        "type": kind,
        "approval_id": row.get("approval_id"),
        "run_id": row.get("run_id"),
        "tool": row.get("tool"),
        "status": row.get("status"),
    }
    if kind == "tool_approval":
        event.update(input=row.get("input"), reason=row.get("reason") or "", by=row.get("by") or "",
                     created_at=row.get("created_at"), expires_at=row.get("expires_at"))
    else:
        event.update(note=row.get("note") or "", decided_by_name=row.get("decided_by_name") or "",
                     decided_at=row.get("decided_at"))
    return event


def _stopped(transport: Any, run_id: str) -> bool:
    try:
        status = (transport.get_run(run_id) or {}).get("status")
    except Exception:  # noqa: BLE001 - an unreadable run is not a stopped one; the timeout still bounds the wait
        return False
    return status in ("stop", "stopped")


def _mark_awaiting(transport: Any, run_id: str, value: Optional[Dict[str, Any]]) -> None:
    """Say on the run record what it waits on (``awaiting``), or that it no
    longer does. The status stays ``running``: Stop, steering and the
    watchdog all treat a waiting turn as the running turn it is."""
    try:
        transport.update_run(run_id, {"awaiting": value})
    except Exception:  # noqa: BLE001 - the run page's label is an extra, the wait is not
        log.debug("tool approvals: awaiting flag not written for %s", run_id, exc_info=True)


def _asked_to_stop(should_stop: Optional[Callable[[], bool]]) -> bool:
    """A background flow's (or loop's) own stop check, never fatal."""
    if should_stop is None:
        return False
    try:
        return bool(should_stop())
    except Exception:  # noqa: BLE001 - an unreadable flow record is not a stop; the timeout still bounds the wait
        return False


def _call_hook(hook: Optional[Callable[[Dict[str, Any]], None]], row: Dict[str, Any]) -> None:
    if hook is None:
        return
    try:
        hook(row)
    except Exception:  # noqa: BLE001 - a notification is an extra, the wait is not
        log.debug("tool approvals: hook failed for %s", row.get("approval_id"), exc_info=True)


def hold(ctx: Dict[str, Any], *, tool: str, tool_input: Any, reason: str, fingerprint: str,
         by: str, hook: str = "", agent_id: str = "", workspace: Optional[str] = None,
         timeout_s: float = DEFAULT_TIMEOUT_SECONDS, poll: Optional[float] = None,
         heartbeat: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Wait for a person to answer one call. Blocks the calling thread.

    Returns the settled row (``approved``, ``denied``, ``expired`` or
    ``cancelled``), or None when the call could not be recorded at all, in
    which case the caller falls back to the advisory refusal.
    """
    poll_s = float(poll if poll is not None else (ctx.get("poll") or POLL_SECONDS))
    beat_s = float(heartbeat if heartbeat is not None else (ctx.get("heartbeat") or HEARTBEAT_SECONDS))
    transport = ctx["transport"]
    run_id = ctx["run_id"]
    emitter = ctx["emitter"]
    record = {
        "run_id": run_id, "tool": tool, "tool_input": tool_input, "reason": reason,
        "fingerprint": fingerprint, "by": by, "hook": hook, "agent_id": agent_id,
        "workspace": workspace, "conversation_id": ctx.get("conversation_id"),
        "owner": ctx.get("owner"), "timeout_s": timeout_s,
    }
    for column in RUN_COLUMNS.values():
        if ctx.get(column):
            record[column] = ctx[column]
    row = transport.open_tool_approval(record)
    if not row or not row.get("approval_id"):
        return None
    approval_id = str(row["approval_id"])
    _mark_awaiting(transport, run_id, {"kind": "approval", "approval_id": approval_id,
                                       "tool": tool, "since": row.get("created_at")})
    _emit(emitter, _event(row, "tool_approval"))
    _call_hook(ctx.get("on_open"), row)
    deadline = time.monotonic() + float(timeout_s)
    last_beat = time.monotonic()
    settled: Optional[Dict[str, Any]] = None
    try:
        while True:
            current = transport.tool_approval(approval_id)
            if current and current.get("status") != STATUS_PENDING:
                settled = current
                break
            if _stopped(transport, run_id) or _asked_to_stop(ctx.get("should_stop")):
                closed = transport.close_tool_approval(
                    approval_id, STATUS_CANCELLED, "The run was stopped while the call waited.")
                # A yes that raced the stop does not run the call: the turn is ending.
                settled = {**(closed or row), "status": STATUS_CANCELLED}
                break
            now = time.monotonic()
            if now >= deadline:
                settled = transport.close_tool_approval(approval_id, STATUS_EXPIRED, "")
                break
            if now - last_beat >= beat_s:
                last_beat = now
                _emit(emitter, {"type": "heartbeat", "run_id": run_id, "approval_id": approval_id})
            time.sleep(poll_s)
    finally:
        _mark_awaiting(transport, run_id, None)
    if not settled or settled.get("status") == STATUS_PENDING:
        # The store could not be reached to settle it: nobody answered, as
        # far as this turn can tell, so the call is refused as timed out.
        settled = {**row, "status": STATUS_EXPIRED}
    _emit(emitter, _event(settled, "tool_approval_resolved"))
    _call_hook(ctx.get("on_settle"), settled)
    return settled


__all__ = [
    "DECISIONS", "DEFAULT_TIMEOUT_SECONDS", "INTERACTIVE_ORIGINS", "STATUSES",
    "STATUS_APPROVED", "STATUS_CANCELLED", "STATUS_DENIED", "STATUS_EXPIRED", "STATUS_PENDING",
    "TIMEOUT_ENV",
    "BACKGROUND_TIMEOUT_ENV", "DEFAULT_BACKGROUND_TIMEOUT_SECONDS",
    "KIND_FLOW", "KIND_TEAM", "RUN_COLUMNS",
    "background_context", "background_scope", "background_timeout_seconds",
    "chat_context", "close", "decide", "get", "hold", "is_expired", "list_for_flow_run",
    "list_for_run", "list_for_team_run", "list_for_tool", "open_approval",
    "list_pending",
    "timeout_seconds", "transport_for",
]
