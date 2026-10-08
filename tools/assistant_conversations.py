"""
``assistant_conversations``: the assistant's past conversations with the
person, and the way back into one of them (docs/assistant.md "Past
conversations").

The person's assistant thread keeps every conversation they started a new one
from (common/entity_chat_store.py ``sessions``). This tool reads them for the
turn that calls it, and only them: the thread is the one the running turn
belongs to, found from the run record, so it cannot name anyone else's.

* ``list`` gives ten at a time, newest first, the conversation in progress
  left out: an id, the title (what the person opened it with), their last
  message, the size, when, and the workspace it belongs to. By default
  only the conversations that ran in this turn's workspace; ``workspace="all"``
  for every one, ``offset=10`` for the next ten, ``query`` to look for words in
  what the person said.
* ``open`` asks for one to become the live conversation. A turn cannot swap the
  thread it is writing into, so the swap happens when the turn ends
  (routes/assistant.py ``_switch_after_turn``) and the page reloads it; the
  next message continues that conversation.

Titles and messages returned are the person's own words in their own thread,
never the assistant's answers, so it reads private data but no text anyone
else wrote (tools/capabilities.py).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

#: The assistant's thread kind (routes/assistant.py ``ASSISTANT_CHAT_KIND``).
KIND = "assistant"
#: Conversations per page of ``list``.
PAGE = 10
#: How much of the person's last message a row carries.
LAST_CHARS = 120


class _Refused(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _run_id() -> str:
    """The run calling the tool: the loop's record of it, else the env."""
    try:
        from agents.agent_loop import current_state
        state = current_state()
    except Exception:  # noqa: BLE001 - outside the loop: the environment names it
        state = None
    return str(getattr(state, "run_id", "") or os.environ.get("AGENT_RUN_ID") or "")


def _turn_thread() -> Tuple[str, Dict[str, Any]]:
    """The assistant thread key the running turn belongs to, and the run.
    A turn files its run under ``assistantchat:<key>[:<epoch>]``
    (chat/entity_chat.py)."""
    from common.entity_chat_store import entity_chat_store
    from managers.run_manager import get_run_by_id
    run_id = _run_id()
    run = get_run_by_id(run_id) if run_id else None
    task = str((run or {}).get("task_id") or "")
    prefix = f"{KIND}chat:"
    if run is None or not task.startswith(prefix) or run.get("message_origin") != f"{KIND}-chat":
        raise _Refused("Past conversations are there only in the person's assistant conversation.",
                       "not_assistant_thread")
    rest = task[len(prefix):]
    store = entity_chat_store()
    head, _, tail = rest.rpartition(":")
    if head and tail.isdigit() and store.has_chat(KIND, head):
        return head, run
    if store.has_chat(KIND, rest):
        return rest, run
    raise _Refused("This conversation is not stored yet.", "not_found")


def thread_home(key: str, user_id: str) -> str:
    """The home of the assistant thread stored under ``key``
    (routes/assistant.py ``resolve_thread``): where a turn that names no
    workspace runs, so where a conversation from before turns were stamped
    belongs."""
    from common import identity, personal_workspace
    from common.auth import MULTI
    if identity.current_mode() != MULTI or key.startswith("service-"):
        return "default"
    return personal_workspace.name_for(user_id)


def _row(thread: Dict[str, Any]) -> Dict[str, Any]:
    said = [m for m in thread.get("transcript") or [] if m.get("role") == "user"]
    last = " ".join(str((said[-1] if said else {}).get("content") or "").split())
    row = {
        "id": thread.get("id"),
        "title": thread.get("title") or "",
        "messages": thread.get("messages"),
        "started_at": thread.get("started_at"),
        "updated_at": thread.get("updated_at"),
        "workspace": thread.get("workspace"),
    }
    if len(said) > 1 and last:
        row["last_message"] = last[:LAST_CHARS] + ("…" if len(last) > LAST_CHARS else "")
    return row


def _matches(thread: Dict[str, Any], words: List[str]) -> bool:
    text = " ".join([str(thread.get("title") or "")] + [
        str(m.get("content") or "") for m in thread.get("transcript") or [] if m.get("role") == "user"
    ]).lower()
    return all(w in text for w in words)


def list_conversations(entity_id: str, *, workspace: str = "", query: str = "",
                       offset: int = 0, limit: int = PAGE, home: str = "default") -> Dict[str, Any]:
    """The thread's past conversations, newest first, without the live one.
    ``workspace`` keeps that workspace's (each conversation stays in the one
    it started in; one from before turns were stamped is the ``home``'s);
    empty or ``all`` keeps every one."""
    from common.entity_chat_store import entity_chat_store
    threads = [{**t, "workspace": t.get("workspace") or home}
               for t in entity_chat_store().threads(KIND, entity_id) if not t.get("active")]
    scope = str(workspace or "").strip()
    if scope and scope.lower() != "all":
        threads = [t for t in threads if t["workspace"] == scope]
    words = [w for w in str(query or "").lower().split() if w]
    if words:
        threads = [t for t in threads if _matches(t, words)]
    threads.sort(key=lambda t: (t.get("updated_at") or "", t.get("epoch") or 0), reverse=True)
    offset = max(0, int(offset or 0))
    page = threads[offset:offset + limit]
    more = offset + len(page) < len(threads)
    return {
        "conversations": [_row(t) for t in page],
        "total": len(threads),
        "offset": offset,
        "has_more": more,
        "next_offset": offset + len(page) if more else None,
        "workspace": scope if scope and scope.lower() != "all" else "all",
        "url": "/assistant",
    }


class AssistantConversationsInput(BaseModel):
    action: str = Field("list", description=(
        "list: the person's past conversations with you, newest first, ten at a time; "
        "open: go back to one of them, so the person continues it"))
    offset: int = Field(0, ge=0, description="list: 0 for the latest ten, 10 for the next ten, and so on")
    query: Optional[str] = Field(None, description=(
        "list: words to look for in what the person said, to find the conversation they mean"))
    workspace: Optional[str] = Field(None, description=(
        "list: only the conversations that ran in this workspace; empty for the one this turn runs in, "
        "'all' for every one"))
    conversation: Optional[str] = Field(None, description="open: the conversation's id from list, like s3")


@tool("assistant_conversations", args_schema=AssistantConversationsInput)
def assistant_conversations(action: str = "list", offset: int = 0, query: Optional[str] = None,
                            workspace: Optional[str] = None, conversation: Optional[str] = None) -> str:
    """The person's past conversations with you, the assistant, and the way back
    into one. `list` gives the latest ten (title, their last message, when, the
    workspace), only those in this turn's workspace unless `workspace` is
    'all'; `has_more` and `next_offset` say whether there are ten more. `open`
    with the id of a conversation of this workspace (one of another workspace
    is continued from there) switches the page to that conversation when your
    answer ends: say in one short sentence that you are opening it, and do not
    go on with its subject in this answer.
    """
    try:
        entity_id, run = _turn_thread()
        home = thread_home(entity_id, str(run.get("launched_by") or ""))
        act = str(action or "list").strip().lower()
        if act == "list":
            scope = workspace if workspace else str(run.get("workspace") or "")
            return json_ok(list_conversations(entity_id, workspace=scope, query=query or "",
                                              offset=offset, home=home))
        if act != "open":
            return json_err("action must be list or open", code="bad_action")
        wanted = str(conversation or "").strip()
        if wanted.isdigit():
            wanted = f"s{wanted}"
        if not wanted:
            return json_err("Give the conversation's id from list.", code="missing_id")
        from common.entity_chat_store import entity_chat_store
        store = entity_chat_store()
        threads = {t.get("id"): t for t in store.threads(KIND, entity_id)}
        chosen = threads.get(wanted)
        if chosen is None:
            return json_err(f"No conversation '{wanted}' here; list them first.", code="not_found")
        if chosen.get("active"):
            return json_ok({"opening": None, "note": "That is the conversation in progress."})
        # A conversation stays in its workspace: it is continued from there.
        here = str(run.get("workspace") or home)
        theirs = chosen.get("workspace") or home
        if theirs != here:
            return json_err(f"That conversation is in workspace '{theirs}'; the person continues it "
                            f"after switching the page to that workspace.", code="other_workspace")
        if not store.request_switch(KIND, entity_id, wanted, str(run.get("run_id") or "")):
            return json_err(f"No conversation '{wanted}' here; list them first.", code="not_found")
        return json_ok({"opening": wanted, "title": chosen.get("title") or "",
                        "note": "The page opens it when this answer ends."})
    except _Refused as exc:
        return json_err(str(exc), code=exc.code)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        return json_err(f"Reading past conversations failed: {exc}", code="internal")


ASSISTANT_CONVERSATION_TOOLS = [assistant_conversations]

__all__ = ["assistant_conversations", "list_conversations", "ASSISTANT_CONVERSATION_TOOLS"]
