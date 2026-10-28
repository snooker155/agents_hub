"""
Flows, loops and teams an agent starts from a chat, and the chat they report back to.

An agent in a chat that starts a flow, a loop or a team gets the run's id
back at once; the run goes on in a process of its own for minutes. Before
this the turn simply ended there: the agent said "I'll send the result" and
nothing ever woke it, since the process that finishes a run cannot start a
chat turn and nothing else knew the run came from a chat.

Now the run keeps where it came from (its ``origin``: the conversation, the
agent, the workspace), and when it ends the backend runs a new turn of that
agent in that conversation with the run's result (:func:`deliver_finished`).
The turn goes through the chat pipeline like any other, so an open chat
shows it live and the transcript keeps it; what triggered it is shown as a
notice, not as something the person said.

When it runs: the run's own process publishes ``<kind>_runs.changed`` as it
closes the record, which reaches the backend (:func:`poke`); the watchdog
tries every tick as well, for a run whose process died. A conversation in the
middle of a turn is left until that turn ends (:func:`poke` again, from
chat/broadcast.py), so the agent is never asked two things at once.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)

#: The key of a run record that says which chat started it.
ORIGIN_KEY = "origin"

#: The run kinds a chat can start in the background.
KINDS = ("flow", "loop", "team")

#: A finished run older than this is not reported any more (a backend that
#: was down for a day does not wake every chat on start).
WAKE_WINDOW = timedelta(hours=24)

#: How much of a run's result the woken agent is given.
RESULT_CHARS = 12000

# Conversations a wake waits on (a turn was running); the end of a turn there
# tries again.
_deferred: Set[str] = set()
_lock: Optional[asyncio.Lock] = None
_tasks: Set[asyncio.Task] = set()
# A pass was asked for while one ran: it goes round once more.
_again = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Origin ───────────────────────────────────────────────────────────────────

def chat_origin() -> Optional[Dict[str, Any]]:
    """Where the running tool call comes from, when it is a chat turn: the
    conversation, the agent answering there, its run and workspace. None
    outside a chat (a task run, a flow node, the CLI)."""
    from common.agent_context import current_agent_id, current_conversation_id, current_run_id
    conversation_id = current_conversation_id.get()
    if not conversation_id:
        return None
    try:
        from common.workspace_context import resolve_active_workspace
        workspace = resolve_active_workspace()
    except Exception:  # noqa: BLE001 - a run without its workspace still reports back
        workspace = None
    return {
        "conversation_id": str(conversation_id),
        "agent_id": current_agent_id.get(),
        "chat_run_id": current_run_id.get(),
        "workspace": workspace,
        "started_at": _now(),
    }


def started_note(kind: str, name: str, *, from_chat: bool) -> str:
    """What a launching tool tells the agent about the run it just started.

    The person is told in one line that it runs and the agent waits, with no
    ids: the chat shows the run itself and opens its page."""
    if from_chat:
        return (
            f"{kind.capitalize()} '{name}' started and runs in the background. Tell the "
            f"user in one short sentence, in their language, that the {kind} '{name}' "
            "is running in the background and you are waiting for it to finish (e.g. "
            f"\"The {kind} '{name}' is running in the background, waiting for it to "
            "finish.\"). Do not give the run id, the session id or any other id. When it "
            "ends, the hub starts a new turn of yours here with its result, so end this "
            "turn now; do not poll for completion or wait for it."
        )
    return (
        f"{kind.capitalize()} '{name}' started. It runs in the background — report "
        "this to the user; do not poll for completion."
    )


# ── What the agent is told ───────────────────────────────────────────────────

def _entity_name(rec: Dict[str, Any]) -> str:
    kind, entity_id = rec.get("kind"), str(rec.get("entity_id") or "")
    try:
        if kind == "flow":
            from flow import store as flow_store
            flow = flow_store.get_flow(entity_id) or {}
            return str(flow.get("name") or entity_id)
        if kind == "loop":
            from loops import store
            loop = store.get_loop(entity_id)
            return str(getattr(loop, "name", "") or entity_id)
        if kind == "team":
            from teams import store
            team = store.get_team(entity_id)
            return str(getattr(team, "name", "") or entity_id)
    except Exception:  # noqa: BLE001 - the id names it well enough
        log.debug("entity name lookup failed", exc_info=True)
    return entity_id


def _result_of(rec: Dict[str, Any]) -> str:
    """What the run produced: a flow's combined output, a loop's or a team's result."""
    text = rec.get("result") if rec.get("kind") in ("loop", "team") else rec.get("output")
    return str(text or "").strip()


#: How many of a run's files the notice carries.
FILES_MAX = 50


def _flow_run_files(flow_id: str, flow_run_id: str) -> List[Dict[str, Any]]:
    """The files the nodes of one flow run changed, from its log lines."""
    from flow import run_store
    out = []
    for ev in run_store.read_run_group_events(flow_id, flow_run_id):
        if ev.get("type") not in ("agent_finish", "agent_error"):
            continue
        for f in ev.get("files") or []:
            if isinstance(f, dict) and f.get("path"):
                out.append({**f, **({"run_id": ev["run_id"]} if ev.get("run_id") else {})})
    return out


def _team_run_files(team_run_id: str) -> List[Dict[str, Any]]:
    """The files the members of one team run changed, from their turns' runs."""
    from common import db
    from managers.run_manager import get_run_process
    rows = db.get_conn().execute(
        "SELECT run_id FROM runs WHERE parent_run_id = ? ORDER BY started_at", (team_run_id,)).fetchall()
    out = []
    for r in rows:
        for art in (get_run_process(r["run_id"]) or {}).get("artifacts") or []:
            if isinstance(art, dict) and art.get("path"):
                out.append({k: art[k] for k in ("op", "path", "url") if art.get(k)} | {"run_id": r["run_id"]})
    return out


def files_of(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The files a run changed, one per path (the last change wins; a file
    added and then edited stays added), each with the node or member run that
    changed it: what the chat lists under the woken agent's reply and in its
    Artifacts panel."""
    kind, run_id = rec.get("kind"), str(rec.get("run_id") or "")
    changes: List[Dict[str, Any]] = []
    try:
        if kind == "flow":
            changes = _flow_run_files(str(rec.get("entity_id")), run_id)
        elif kind == "loop":
            from loops import store
            loop = store.get_loop(str(rec.get("entity_id")))
            for it in store.list_iterations(run_id) if loop is not None else []:
                if it.get("flow_run_id"):
                    changes += _flow_run_files(loop.flow_id, str(it["flow_run_id"]))
        elif kind == "team":
            changes = _team_run_files(run_id)
    except Exception:  # noqa: BLE001 - the reply still comes, without its file list
        log.debug("files of background run %s failed", run_id, exc_info=True)
    files: Dict[str, Dict[str, Any]] = {}
    for f in changes:
        prev = files.get(f["path"])
        op = "add" if prev and prev.get("op") == "add" and f.get("op") == "modify" else f.get("op")
        url = (f.get("url") or (prev or {}).get("url")) if op != "delete" else None
        files[f["path"]] = {"op": op or "modify", "path": f["path"],
                            **({"url": url} if url else {}),
                            **({"run_id": f["run_id"]} if f.get("run_id") else {})}
    return list(files.values())[-FILES_MAX:]


def notice_of(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The line the chat shows above the woken agent's reply, as data the page
    words in its own language, with the files the run changed."""
    files = files_of(rec)
    return {
        "type": "background_run_finished",
        "kind": rec.get("kind"),
        "run_id": rec.get("run_id"),
        "entity_id": rec.get("entity_id"),
        "name": _entity_name(rec),
        "status": rec.get("status"),
        **({"files": files} if files else {}),
    }


def prompt_of(rec: Dict[str, Any], notice: Dict[str, Any]) -> str:
    """The message the woken agent is given: what ended, how, and its result."""
    from common.untrusted import agent_output_fence
    kind, name, status = rec.get("kind"), notice["name"], rec.get("status")
    facts = [f"status: {status}"]
    if kind == "loop":
        for key in ("iterations_done", "best_score", "final_score", "stop_reason"):
            if rec.get(key) not in (None, ""):
                facts.append(f"{key}: {rec[key]}")
    if kind == "team":
        for key in ("rounds_done", "stop_reason"):
            if rec.get(key) not in (None, ""):
                facts.append(f"{key}: {rec[key]}")
    if rec.get("error"):
        facts.append(f"error: {rec['error']}")
    files = [f for f in notice.get("files") or [] if f.get("op") != "delete"]
    if files:
        facts.append("files it wrote (the chat lists them under your reply): "
                     + ", ".join(f["path"] for f in files))
    result = _result_of(rec)
    if len(result) > RESULT_CHARS:
        result = result[:RESULT_CHARS] + "\n… (truncated)"
    head = (
        f"[Background {kind} finished] The {kind} '{name}' you started earlier in this "
        f"conversation has ended — {'; '.join(facts)}.\n"
        "This message comes from the hub, not from the user. Continue the user's request "
        "from where you left it: report what the run produced, and carry on with any "
        "steps that were waiting on it. Do not give run or session ids. Answer in the "
        "language the user writes in."
    )
    if not result:
        return head + "\n\nThe run produced no output."
    return head + "\n\nIts result:\n" + agent_output_fence(
        result, source=f"{kind} {rec.get('entity_id')}, run {rec.get('run_id')}")


# ── What runs in the background of a chat ────────────────────────────────────

def _team_working(team_run_id: str) -> List[str]:
    """The members whose turn is under way."""
    from common import db
    rows = db.get_conn().execute(
        "SELECT agent_id, extra FROM runs WHERE parent_run_id = ? AND status IN ('pending', 'running')",
        (team_run_id,)).fetchall()
    names = []
    for r in rows:
        extra = db.loads(r["extra"], {}) or {}
        names.append(str(extra.get("team_member") or r["agent_id"]))
    return names


def _progress(rec: Dict[str, Any]) -> Dict[str, Any]:
    """How far one run has got, in the terms of its kind, and the channels a
    page follows it on (common/run_stream.py)."""
    from common.run_stream import run_channel
    kind, run_id = rec.get("kind"), str(rec.get("run_id"))
    out: Dict[str, Any] = {}
    if kind == "flow":
        from flow.progress import run_progress
        out["progress"] = run_progress(str(rec.get("entity_id")), run_id)
        out["channels"] = [run_channel(run_id)]
    elif kind == "loop":
        from loops import store
        loop = store.get_loop(str(rec.get("entity_id")))
        iterations = store.list_iterations(run_id)
        last = iterations[-1] if iterations else {}
        scored = [it for it in iterations if it.get("score") is not None]
        progress: Dict[str, Any] = {
            "iteration": last.get("iteration") or 0,
            "max_iterations": getattr(loop, "max_iterations", None),
            "score": scored[-1].get("score") if scored else None,
            "best_score": rec.get("best_score"),
            "iteration_status": last.get("status"),
        }
        pass_run = last.get("flow_run_id") if last.get("status") in (None, "", "running") else None
        if pass_run and loop is not None:
            from flow.progress import run_progress
            progress["pass"] = run_progress(loop.flow_id, str(pass_run))
            progress["flow_run_id"] = pass_run
        out["progress"] = progress
        out["channels"] = [f"loop:{run_id}"] + ([run_channel(str(pass_run))] if pass_run else [])
    elif kind == "team":
        from teams import store
        team = store.get_team(str(rec.get("entity_id")))
        out["progress"] = {
            "round": int(rec.get("rounds_done") or 0) + 1,
            "max_rounds": getattr(team, "max_rounds", None),
            "working": _team_working(run_id),
        }
        out["channels"] = [f"team:{run_id}"]
    return out


def for_conversation(conversation_id: str) -> List[Dict[str, Any]]:
    """The runs a chat started that are still going (or waiting on a person),
    oldest first, each with its progress and the channels it streams on; and
    one that has ended but not yet reported (``finished``), so the chat keeps
    showing it until the agent's turn with its result begins."""
    from common import entity_runs, run_status
    statuses = set(run_status.ACTIVE_STATUSES) | set(run_status.PARKED_STATUSES)
    rows = list(entity_runs.list_runs(kinds=KINDS, statuses=statuses))
    rows += [r for r in pending()
             if str((r.get(ORIGIN_KEY) or {}).get("conversation_id")) == str(conversation_id)]
    out = []
    for rec in rows:
        origin = rec.get(ORIGIN_KEY)
        if not isinstance(origin, dict) or str(origin.get("conversation_id")) != str(conversation_id):
            continue
        item = {
            "run_id": rec.get("run_id"), "kind": rec.get("kind"),
            "entity_id": rec.get("entity_id"), "name": _entity_name(rec),
            "status": rec.get("status"), "started_at": rec.get("started_at") or rec.get("created_at"),
            "goal": rec.get("goal") or "", "agent_id": origin.get("agent_id"),
            "finished": run_status.is_terminal(str(rec.get("status") or "")),
        }
        try:
            item.update(_progress(rec))
        except Exception:  # noqa: BLE001 - a run without progress still shows
            log.debug("background run progress failed", exc_info=True)
        out.append(item)
    out.sort(key=lambda r: str(r.get("started_at") or ""))
    return out


# ── Waking the chat ──────────────────────────────────────────────────────────

def _due(rec: Dict[str, Any]) -> bool:
    origin = rec.get(ORIGIN_KEY)
    if not isinstance(origin, dict) or origin.get("woken_at") or not origin.get("conversation_id"):
        return False
    from common import run_status
    if not run_status.is_terminal(str(rec.get("status") or "")):
        return False
    finished = str(rec.get("finished_at") or "")
    if finished:
        try:
            when = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - when > WAKE_WINDOW:
                return False
        except ValueError:
            pass
    return True


def pending() -> List[Dict[str, Any]]:
    """Finished runs started from a chat whose chat has not been told yet, oldest first."""
    from common import entity_runs, run_status
    try:
        rows = entity_runs.list_runs(kinds=KINDS, statuses=run_status.TERMINAL_STATUSES,
                                     order_by="COALESCE(finished_at, '') DESC", limit=200)
    except Exception:  # noqa: BLE001 - nothing to report this time
        log.debug("background run scan failed", exc_info=True)
        return []
    return sorted((r for r in rows if _due(r)), key=lambda r: str(r.get("finished_at") or ""))


def _claim(run_id: str) -> Optional[Dict[str, Any]]:
    """Mark the run as reported, once: the record that won the claim, or None."""
    from common import entity_runs

    def change(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not _due(rec):
            return None
        return {ORIGIN_KEY: {**rec[ORIGIN_KEY], "woken_at": _now()}}
    return entity_runs.update_where(run_id, change)


def _conversation_busy(conversation_id: str) -> bool:
    try:
        from common import live_runs
        return conversation_id in set(live_runs.running_conversations())
    except Exception:  # noqa: BLE001 - unknown means not busy
        return False


#: How many of a chat's stored messages stand in for its history.
HISTORY_MESSAGES = 20


def _history(conversation_id: str, chat: Dict[str, Any]) -> list:
    """What was said before: the conversation's recorded turns, else the
    chat's stored messages (a chat whose turns left no run record)."""
    from chat.models import ChatHistoryMessage
    from chat.runs import build_conversation_history
    history = build_conversation_history(conversation_id)
    if history:
        return history
    out = []
    for m in (chat.get("messages") or [])[-HISTORY_MESSAGES:]:
        role, content = m.get("role"), str(m.get("content") or "").strip()
        if role in ("user", "agent") and content:
            out.append(ChatHistoryMessage(role=role, content=content))
    return out


def _build_request(rec: Dict[str, Any], chat: Dict[str, Any]):
    from chat.models import ChatRequest
    origin = rec[ORIGIN_KEY]
    conversation_id = str(origin["conversation_id"])
    notice = notice_of(rec)
    return ChatRequest(
        agent_id=origin.get("agent_id") or chat.get("agent_id"),
        message=prompt_of(rec, notice),
        notice=notice,
        workspace=origin.get("workspace") or chat.get("workspace"),
        project_id=chat.get("project_id"),
        history=_history(conversation_id, chat),
        conversation_id=conversation_id,
        conversation_title=chat.get("title"),
        source="chat",
    )


async def _wake(rec: Dict[str, Any]) -> bool:
    """Run the agent's turn for one claimed run. True when a turn ran."""
    from chat.pipelines import run_chat_pipeline
    from common import chat_store
    conversation_id = str(rec[ORIGIN_KEY]["conversation_id"])
    chat = await asyncio.to_thread(chat_store.get_chat, conversation_id)
    if not chat:
        # A conversation nobody keeps (deleted, or not a dashboard chat):
        # there is no one to tell.
        return False
    request = await asyncio.to_thread(_build_request, rec, chat)
    async for _event in run_chat_pipeline(request):
        pass
    return True


async def deliver_finished() -> int:
    """Wake the chat of every finished run that has not reported yet. Returns
    how many turns ran. One pass at a time in this process: a call while one
    runs makes it go round once more instead."""
    global _lock, _again
    if _lock is None:
        _lock = asyncio.Lock()
    if _lock.locked():
        _again = True
        return 0
    woken = 0
    async with _lock:
        _again = True
        while _again:
            _again = False
            woken += await _deliver_pass()
    return woken


async def _deliver_pass() -> int:
    """One look at the pending runs: wake each chat that is not mid-turn."""
    woken = 0
    for rec in await asyncio.to_thread(pending):
        conversation_id = str(rec[ORIGIN_KEY]["conversation_id"])
        if _conversation_busy(conversation_id):
            _deferred.add(conversation_id)
            continue
        claimed = await asyncio.to_thread(_claim, str(rec["run_id"]))
        if not claimed:
            continue
        _deferred.discard(conversation_id)
        try:
            woken += int(await _wake(claimed))
        except Exception:  # noqa: BLE001 - one chat failing does not stop the others
            log.exception("waking the chat of run %s failed", rec.get("run_id"))
    return woken


def poke(resource: Optional[str] = None, conversation_id: Optional[str] = None) -> None:
    """Try to deliver soon, from the backend's loop: a run's record changed
    (``resource`` is ``flow_runs``, ``loop_runs`` or ``team_runs``) or a turn
    ended in a conversation a wake waits on. Anything else is ignored. Safe
    from any thread; does nothing outside the backend process."""
    if resource is not None and resource not in ("flow_runs", "loop_runs", "team_runs"):
        return
    if conversation_id is not None and conversation_id not in _deferred:
        return
    from common.session_broker import broker
    loop = broker._loop
    if loop is None or not loop.is_running():
        return

    def _start() -> None:
        task = loop.create_task(deliver_finished())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    loop.call_soon_threadsafe(_start)


__all__ = ["ORIGIN_KEY", "chat_origin", "deliver_finished", "for_conversation", "notice_of",
           "pending", "poke", "prompt_of", "started_note"]
