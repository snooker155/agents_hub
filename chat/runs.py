"""
Chat run records, request validation, model overrides, and shared-memory journaling.

Helpers that tie a chat message exchange to the run/session bookkeeping the rest
of the dashboard reads: each message becomes a row in the ``runs`` table with a
log file so it appears in the Sessions list, attached to the chat's instance.
"""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

from agents.callbacks import write_log as _write_log
from agents import registry
from managers.run_manager import (
    run_log_path,
    new_unique_run_id,
    open_run as register_run,
)
from chat.models import ChatRequest
from common.session_service import get_or_create_chat_session


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_pool_id(agent_id: str, workspace: str | None = None) -> str | None:
    """Return the primary shared memory pool id for agent_id in workspace, or None.

    Auto-journal and interaction episodes are written to the primary pool
    only; extra attached pools are read-only context. The assignment is
    resolved per workspace.
    """
    try:
        from memory.binding import effective_memory_pools
        spec = registry.get_agent(agent_id)
        pools = effective_memory_pools(spec, workspace) if spec else []
        if pools:
            return pools[0]
    except Exception:
        pass
    return None


def auto_journal(agent_id: str, pool_id: str | None, user_message: str, response: str, run_id: str) -> None:
    """Fire-and-forget silent journal append + interaction episode for agents with shared memory."""
    if not pool_id:
        return
    try:
        from memory.tool import silent_journal_append, silent_interaction_episode
        silent_journal_append(pool_id, agent_id, user_message, response, run_id=run_id)
        silent_interaction_episode(pool_id, agent_id, user_message, response, run_id=run_id)
    except Exception:
        pass
    # Auto-extraction is off by default; opt-in via GRAPH_AUTO_EXTRACT env flag.
    try:
        from memory.graph_extract import silent_graph_extract
        silent_graph_extract(pool_id, user_message, response)
    except Exception:
        pass


def build_conversation_history(conversation_id: str, *, max_turns: int = 20):
    """Reconstruct a conversation's prior turns as ``ChatHistoryMessage`` list.

    The web Chat page posts its history from the browser, but non-web surfaces
    (e.g. the Telegram connector) hold no client-side transcript, so they would
    otherwise run every turn context-free. Rebuild it server-side from this
    conversation's completed chat runs: each run stores that exchange's
    ``user_message`` and ``response`` under ``process.llm_input_context``, so a
    chronological scan yields the alternating user/assistant transcript that
    ``build_history_messages`` turns into the turn's messages. Returns the last ``max_turns``
    exchanges, oldest first; ``[]`` on any error or for a fresh conversation.
    """
    from chat.models import ChatHistoryMessage
    from managers.run_manager import load_runs, get_run_process

    try:
        runs = [
            r for r in load_runs()
            if str(r.get("task_id")) == str(conversation_id)
            and r.get("session_type") == "chat"
            and str(r.get("status")) == "completed"
        ]
    except Exception:
        return []
    runs.sort(key=lambda r: str(r.get("started_at") or r.get("created_at") or ""))

    history: list[ChatHistoryMessage] = []
    for r in runs[-max_turns:]:
        ctx = (get_run_process(r.get("run_id")) or {}).get("llm_input_context") or {}
        user_msg = str(ctx.get("user_message") or "").strip()
        response = str(ctx.get("response") or "").strip()
        if user_msg:
            history.append(ChatHistoryMessage(role="user", content=user_msg))
        if response:
            history.append(ChatHistoryMessage(role="agent", content=response))
    return history


def agent_overrides(agent_id: str) -> dict:
    """Per-agent model overrides from the registry AgentSpec.

    Delegates to ``AgentSpec.model_overrides`` — the shared source of truth also
    used by ``agent_launcher`` for subprocess launches — so chat and task runs
    apply the identical override cascade.
    """
    try:
        spec = registry.get_agent(agent_id)
        return spec.model_overrides() if spec else {}
    except Exception:
        return {}


def validate_chat_request(request: ChatRequest):
    if request.flow_id or request.team_id:
        # Flow and team targets are validated lazily in their own pipelines.
        return None
    spec = registry.get_agent(request.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_id}' not found")
    return spec


def load_flow_definition(flow_id: str) -> dict:
    """Load a flow (logic+visual merged) via flow_store or raise 404."""
    from flow import store as flow_store
    try:
        flow = flow_store.get_flow(flow_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read flow: {e}")
    if not flow:
        raise HTTPException(status_code=404, detail=f"Flow '{flow_id}' not found")
    return flow


def create_chat_run(request: ChatRequest):
    """Create a new run record for each chat message exchange."""
    conv_id = request.conversation_id or str(uuid4())
    run_id = new_unique_run_id()
    run_title = request.message[:60] + ("…" if len(request.message) > 60 else "")
    session_title = request.conversation_title or run_title
    log_file = run_log_path(run_id)
    started = utc_iso()

    # Ensure/create a session context for this conversation
    try:
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=session_title,
            workspace=request.workspace,
            agent_id=request.agent_id,
        )
    except Exception:
        session_id = None

    msg_id = str(uuid4())[:8]
    log_lines = [
        f"=== Chat message  run_id={run_id} ===",
        f"Started   : {started}",
        f"Agent     : {request.agent_id}",
        f"Workspace : {request.workspace or '—'}",
        f"Conv ID   : {conv_id}",
        f"Session ID: {session_id or '—'}",
        f"Title     : {run_title}",
        "",
        f"=== Message at {started} id={msg_id} ===",
    ]
    if request.history:
        log_lines.append("--- Conversation history ---")
        for msg in request.history[-20:]:
            role = "User" if msg.role == "user" else "Assistant"
            log_lines.append(f"{role}: {msg.content}")
        log_lines.append("")
    log_lines.extend([
        "--- User message ---",
        request.message,
        "",
    ])
    if request.attachments:
        log_lines.append("--- Attachments ---")
        for idx, att in enumerate(request.attachments, start=1):
            if getattr(att, "content_b64", None):
                # base64 length is ~4/3 of the binary size; rough estimate is fine for the log.
                bytes_len = int(len(att.content_b64) * 3 / 4)
                kind = "binary"
            else:
                bytes_len = len((att.content or "").encode("utf-8", errors="ignore"))
                kind = "text"
            stored = getattr(att, "stored_workspace_path", None)
            store_text = f"stored={stored}" if stored else f"store_requested={bool(att.store_to_workspace)}"
            log_lines.append(f"[{idx}] file={att.filename} kind={kind} bytes={bytes_len} {store_text}")
        log_lines.append("")
    log_lines.extend([
        "--- Agent response (stream) ---",
    ])
    _write_log(log_file, log_lines)

    channel = "telegram" if request.source == "telegram" else "chat"

    # Capability guard, run level. Telegram inbound is attacker-controllable
    # text entering agent context without any tool grant saying so, so the
    # agent's tool set is re-checked with `ingests_untrusted` folded in. This is
    # warn-only by design — an agent that is safe on its own tools must not stop
    # working because a message arrived over Telegram — but the exposure lands
    # in the log instead of being invisible.
    try:
        from agents.capability_guard import check_run_channel
        from agents.registry import get_agent as _cap_get_agent
        _cap_spec = _cap_get_agent(request.agent_id)
        if _cap_spec is not None:
            check_run_channel(request.agent_id, list(_cap_spec.tools or []), channel)
    except Exception:
        pass

    # One chat instance per conversation: the same live copy answers every
    # message in the thread, so its instance page carries the whole exchange.
    instance_id = None
    try:
        from instances import registry as instance_registry
        instance = instance_registry.ensure_instance(
            request.agent_id,
            # An explicit id means this message was addressed to one live copy
            # (from its instance page); the run joins that copy's journal
            # instead of opening a second one for the same conversation.
            instance_id=request.instance_id,
            kind="chat",
            workspace=request.workspace,
            session_id=session_id,
            task_id=conv_id,
            project_id=request.project_id,
            hint=run_title,
            state="active",
            reuse_session=not request.instance_id,
        )
        instance_id = instance["instance_id"]
    except Exception:
        pass

    register_run(
        run_id,
        request.agent_id,
        task_id=conv_id,
        session_id=session_id,
        session_type="chat",
        message_origin=request.source or "chat",
        channel=channel,
        workspace=request.workspace,
        title=run_title,
        log_file=str(log_file),
        instance_id=instance_id,
    )

    return run_id, msg_id, log_file, log_lines, session_id
