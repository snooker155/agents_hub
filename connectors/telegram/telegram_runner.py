"""
Telegram long-polling adapter.

The service singleton owns:
- an asyncio task running the getUpdates loop
- a per-chat lock so each Telegram chat has at most one in-flight agent run
- start()/stop()/restart() helpers driven by the Settings UI

Each Telegram chat is bound to an agent via `/agent <id>`; subsequent messages
are turned into ChatRequest objects and run through dashboard.backend.routes.chat
.run_chat_pipeline — the same code path the web Chat page uses — so sessions,
tool history, logs, and journaling all work uniformly.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from agents import registry
from common.session_broker import notify_change
from connectors.telegram import telegram_store


log = logging.getLogger("telegram")


_TG_API = "https://api.telegram.org"
_MAX_FILE_BYTES = 5 * 1024 * 1024  # keep parity with the web chat 5 MB cap
_MEDIA_GROUP_DEBOUNCE = 1.5  # seconds — wait this long for the rest of an album

# Bump when the connector's message-handling capabilities change. Logged at
# poller start so a stale process (started before a code change and never
# restarted) is obvious from the logs — its banner will be missing or older
# than the running code. The long-polling task holds in-memory module code, so
# only a full process restart picks up edits to this file or agent_factory.
_CONNECTOR_BUILD = "2026-06-17"
_CONNECTOR_FEATURES = "structured-responses(buttons/telegram-keyboard), callback-query"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chat_id_from_update(update: dict[str, Any]) -> Optional[int]:
    """Best-effort chat id extraction from a message or a callback_query update.

    Used to gate on the allowlist before either kind of update is dispatched,
    so the check has one place to live instead of one per update kind.
    """
    message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    try:
        return int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        return None


# ── Telegram Bot API client ──────────────────────────────────────────────────


class TelegramAPI:
    """Thin async wrapper over the Telegram Bot HTTP API."""

    def __init__(self, token: str, *, timeout: float = 30.0):
        self.token = token
        self.timeout = timeout

    @property
    def _base(self) -> str:
        return f"{_TG_API}/bot{self.token}"

    async def _post(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self._base}/{method}", json=payload or {})
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description') or body}")
        return body.get("result") or {}

    async def get_me(self) -> dict[str, Any]:
        return await self._post("getMe")

    async def send_message(
        self, chat_id: int, text: str, *,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        chunks = _split_for_telegram(text)
        last: dict[str, Any] = {}
        for idx, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            # Attach the keyboard only to the final chunk so it sits under the
            # whole message, not after the first 4k slice.
            if reply_markup and idx == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            last = await self._post("sendMessage", payload)
        return last

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict[str, Any] | None = None,
    ) -> None:
        """Replace (or, with reply_markup=None, remove) a message's inline keyboard."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            await self._post("editMessageReplyMarkup", payload)
        except Exception:
            pass

    async def answer_callback_query(self, callback_query_id: str, *, text: str | None = None) -> None:
        """Acknowledge a button press so Telegram stops the client's spinner.

        An optional ``text`` is shown as a brief toast on the user's client.
        """
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]  # Telegram caps callback answers at 200 chars
        try:
            await self._post("answerCallbackQuery", payload)
        except Exception:
            pass

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self._post("sendChatAction", {"chat_id": chat_id, "action": action})
        except Exception:
            pass

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return await self._post("getFile", {"file_id": file_id})

    async def download_file(self, file_path: str) -> bytes:
        url = f"{_TG_API}/file/bot{self.token}/{file_path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content


def _split_for_telegram(text: str, limit: int = 4000) -> list[str]:
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < 0:
            cut = remaining.rfind("\n", 0, limit)
        if cut < 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut < 0 or cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


# ── Update dispatch ──────────────────────────────────────────────────────────


_HELP_TEXT = (
    "Agents Hub bot\n"
    "\n"
    "Setup order: an operator binds this chat to a workspace from the "
    "dashboard, then you pick an agent OR a flow allowed in that workspace.\n"
    "\n"
    "Commands:\n"
    "/workspace — show this chat's workspace (set by the operator, not here)\n"
    "/workspaces — list available workspaces\n"
    "/agent [id] — bind this chat to an agent in the current workspace\n"
    "/agents — list agents allowed in the current workspace\n"
    "/flow [id] — bind this chat to a flow in the current workspace\n"
    "/flows — list flows available in the current workspace\n"
    "/reset — start a fresh conversation (keeps workspace + target)\n"
    "/status — show current binding\n"
    "/help — show this help\n"
    "\n"
    "Binding an agent and binding a flow are mutually exclusive — the latest "
    "one wins. After binding, send any text or attach a photo / document and "
    "the bound agent/flow will reply."
)


def _workspace_names() -> list[str]:
    try:
        from workspace import list_workspace_folders
        return sorted(p.name for p in list_workspace_folders())
    except Exception:
        return []


def _allowed_agent_ids_for(workspace: str) -> Optional[list[str]]:
    """Return the workspace's allowed_agents list, or None if no restriction."""
    try:
        from workspace import get_workspace_metadata
        meta = get_workspace_metadata(workspace) or {}
        allowed = meta.get("allowed_agents")
        if isinstance(allowed, list) and allowed:
            return [str(a) for a in allowed]
    except Exception:
        pass
    return None


def _agents_visible_in(workspace: str) -> list[Any]:
    """Return AgentSpec list filtered by the workspace's allowed_agents (if any)."""
    specs = registry.list_agents()
    allowed = _allowed_agent_ids_for(workspace)
    if allowed is None:
        return specs
    allowed_set = set(allowed)
    return [s for s in specs if s.id in allowed_set]


def _load_flows() -> list[dict[str, Any]]:
    """Return all defined flows (logic+visual merged), or [] on any error."""
    try:
        from flow import store as flow_store
        return flow_store.list_flows()
    except Exception:
        return []


def _flows_visible_in(workspace: str) -> list[dict[str, Any]]:
    """Return flows usable in a workspace: global flows plus ones scoped to it.

    Mirrors the dashboard's flow list filter (routes/flows.list_flows): a flow
    with no ``workspace`` is global; otherwise it must match.
    """
    flows = _load_flows()
    return [f for f in flows if not f.get("workspace") or f.get("workspace") == workspace]


def _get_flow(flow_id: str) -> Optional[dict[str, Any]]:
    for f in _load_flows():
        if f.get("id") == flow_id:
            return f
    return None


def _chat_title(message: dict[str, Any]) -> str:
    chat = message.get("chat") or {}
    if chat.get("username"):
        return f"@{chat['username']}"
    name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
    return name or chat.get("title") or str(chat.get("id") or "")


async def _send_text(api: TelegramAPI, chat_id: int, text: str) -> None:
    try:
        await api.send_message(chat_id, text)
    except Exception as exc:
        log.warning("telegram send_message failed for chat=%s: %s", chat_id, exc)


async def _handle_command(api: TelegramAPI, message: dict[str, Any], text: str) -> bool:
    """Return True if the message was handled as a slash command."""
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id") or 0)
    if not chat_id:
        return False

    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@", 1)[0].lower()  # strip "@botname" suffix Telegram appends in groups
    args = args.strip()
    binding = telegram_store.get_binding(chat_id) or {}

    if cmd == "/start":
        if binding.get("workspace") and (binding.get("agent_id") or binding.get("flow_id")):
            if binding.get("flow_id"):
                flow = _get_flow(binding["flow_id"])
                target = f"flow: `{(flow or {}).get('name') or binding['flow_id']}`"
            else:
                target = f"agent: `{binding['agent_id']}`"
            await _send_text(
                api, chat_id,
                f"Ready. Workspace: `{binding['workspace']}`, {target}. "
                "Send a message, or /help for commands.",
            )
            return True
        await _send_text(
            api, chat_id,
            f"Welcome! This chat isn't bound to a workspace yet. Ask the operator to bind "
            f"chat `{chat_id}` to one from the dashboard, then come back and pick an agent "
            "with /agent <id> or a flow with /flow <id>.\n"
            "See /help for the full list of commands.",
        )
        return True

    if cmd == "/help":
        await _send_text(api, chat_id, _HELP_TEXT)
        return True

    if cmd == "/workspaces":
        workspaces = _workspace_names()
        if not workspaces:
            await _send_text(api, chat_id, "No workspaces found.")
        else:
            await _send_text(api, chat_id, "Workspaces:\n" + "\n".join(f"• {w}" for w in workspaces))
        return True

    if cmd == "/workspace":
        # Binding a chat to a workspace is an operator decision made from the
        # dashboard (POST /api/telegram/bindings), not something a Telegram
        # command can do — otherwise anyone who reaches the bot could grant
        # themselves access to a workspace. This command is read-only: it shows
        # the current binding and, if there is none or the caller asks to
        # change it, points them at the operator instead of touching state.
        if not args:
            current = binding.get("workspace") or "(not set)"
            await _send_text(api, chat_id, f"Current workspace: {current}. Ask the operator to change it.")
            return True
        await _send_text(
            api, chat_id,
            f"This chat's workspace is set by the operator, not by chat. Ask them to bind "
            f"chat `{chat_id}` to workspace `{args}` from the dashboard.",
        )
        return True

    if cmd == "/agents":
        workspace = binding.get("workspace")
        if not workspace:
            await _send_text(api, chat_id, "Pick a workspace first with /workspace <name>.")
            return True
        visible = _agents_visible_in(workspace)
        if not visible:
            await _send_text(api, chat_id, f"No agents authorised in workspace `{workspace}`.")
        else:
            lines = "\n".join(f"• {a.id} — {a.name}" for a in visible)
            await _send_text(api, chat_id, f"Agents in `{workspace}`:\n{lines}")
        return True

    if cmd == "/agent":
        workspace = binding.get("workspace")
        if not workspace:
            await _send_text(
                api, chat_id,
                "Pick a workspace first with /workspace <name>. Then choose an agent allowed there.",
            )
            return True
        if not args:
            current = binding.get("agent_id") or "(not set)"
            await _send_text(api, chat_id, f"Current agent: {current}. Use /agent <id> to change it.")
            return True
        spec = registry.get_agent(args)
        if not spec:
            await _send_text(api, chat_id, f"Unknown agent: {args}")
            return True
        allowed = _allowed_agent_ids_for(workspace)
        if allowed is not None and args not in allowed:
            preview = ", ".join(allowed) or "(none)"
            await _send_text(
                api, chat_id,
                f"Agent `{args}` isn't authorised in workspace `{workspace}`.\n"
                f"Allowed: {preview}",
            )
            return True
        telegram_store.upsert_binding(
            chat_id=chat_id,
            agent_id=args,
            workspace=workspace,
            conversation_id=binding.get("conversation_id") or str(uuid.uuid4()),
            title=_chat_title(message),
        )
        notify_change("telegram", chat_id=chat_id)
        await _send_text(
            api, chat_id,
            f"Bound to agent `{args}` in workspace `{workspace}`. Send a message to start.",
        )
        return True

    if cmd == "/flows":
        workspace = binding.get("workspace")
        if not workspace:
            await _send_text(api, chat_id, "Pick a workspace first with /workspace <name>.")
            return True
        flows = _flows_visible_in(workspace)
        if not flows:
            await _send_text(api, chat_id, f"No flows available in workspace `{workspace}`.")
        else:
            lines = "\n".join(
                f"• {f.get('name') or f.get('id')}\n    id: {f.get('id')}" for f in flows
            )
            await _send_text(
                api, chat_id,
                f"Flows in `{workspace}`:\n{lines}\n\nBind with /flow <id> or /flow <name>.",
            )
        return True

    if cmd == "/flow":
        workspace = binding.get("workspace")
        if not workspace:
            await _send_text(
                api, chat_id,
                "Pick a workspace first with /workspace <name>. Then choose a flow available there.",
            )
            return True
        if not args:
            current = binding.get("flow_id")
            if current:
                flow = _get_flow(current)
                name = (flow or {}).get("name") or current
                await _send_text(api, chat_id, f"Current flow: {name} ({current}). Use /flow <id> to change it.")
            else:
                await _send_text(api, chat_id, "No flow bound. Use /flow <id> or /flow <name>. /flows to list.")
            return True
        visible = _flows_visible_in(workspace)
        # Match by exact id first, then by exact (case-insensitive) name.
        match = next((f for f in visible if f.get("id") == args), None)
        if not match:
            by_name = [f for f in visible if (f.get("name") or "").lower() == args.lower()]
            if len(by_name) == 1:
                match = by_name[0]
            elif len(by_name) > 1:
                await _send_text(
                    api, chat_id,
                    f"Multiple flows named `{args}`. Use the id instead — /flows to list them.",
                )
                return True
        if not match:
            preview = ", ".join((f.get("name") or f.get("id")) for f in visible) or "(none)"
            await _send_text(
                api, chat_id,
                f"No flow `{args}` available in workspace `{workspace}`.\nAvailable: {preview}",
            )
            return True
        telegram_store.upsert_binding(
            chat_id=chat_id,
            agent_id="",
            flow_id=match["id"],
            workspace=workspace,
            conversation_id=binding.get("conversation_id") or str(uuid.uuid4()),
            title=_chat_title(message),
        )
        notify_change("telegram", chat_id=chat_id)
        await _send_text(
            api, chat_id,
            f"Bound to flow `{match.get('name') or match['id']}` in workspace `{workspace}`. "
            "Send a message to run the flow.",
        )
        return True

    if cmd == "/reset":
        new_conv = str(uuid.uuid4())
        updated = telegram_store.reset_conversation(chat_id, new_conv)
        if updated:
            await _send_text(api, chat_id, "Conversation reset. Next message starts a fresh thread.")
        else:
            await _send_text(api, chat_id, "Nothing to reset — use /workspace and /agent first.")
        return True

    if cmd == "/status":
        ws = binding.get("workspace") or "(not set)"
        if binding.get("flow_id"):
            flow = _get_flow(binding["flow_id"])
            target = f"Flow: {(flow or {}).get('name') or binding['flow_id']}"
        elif binding.get("agent_id"):
            target = f"Agent: {binding['agent_id']}"
        else:
            target = "Target: (not set)"
        await _send_text(api, chat_id, f"Workspace: {ws}\n{target}")
        return True

    if text.startswith("/"):
        await _send_text(api, chat_id, f"Unknown command: {cmd}. Try /help.")
        return True

    return False


async def _build_attachments_from_message(
    api: TelegramAPI, messages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Build ChatAttachment dicts for any photo/document in the given messages.

    Returns (attachments, errors). Errors are user-facing strings (e.g.
    "file too large") that should be relayed to the chat.
    """
    attachments: list[dict[str, Any]] = []
    errors: list[str] = []

    for msg in messages:
        # Photo: pick the largest size variant.
        if msg.get("photo"):
            photo = max(msg["photo"], key=lambda p: int(p.get("file_size") or 0))
            file_id = photo.get("file_id")
            uniq = photo.get("file_unique_id") or uuid.uuid4().hex[:8]
            size = int(photo.get("file_size") or 0)
            if size and size > _MAX_FILE_BYTES:
                errors.append(f"Photo too large ({size} bytes); 5 MB limit.")
                continue
            try:
                meta = await api.get_file(file_id)
                data = await api.download_file(meta["file_path"])
            except Exception as exc:
                errors.append(f"Failed to download photo: {exc}")
                continue
            if len(data) > _MAX_FILE_BYTES:
                errors.append("Photo too large (over 5 MB).")
                continue
            attachments.append({
                "filename": f"telegram_photo_{uniq}.jpg",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mime_type": "image/jpeg",
                "store_to_workspace": True,
            })

        doc = msg.get("document")
        if doc:
            file_id = doc.get("file_id")
            size = int(doc.get("file_size") or 0)
            if size and size > _MAX_FILE_BYTES:
                errors.append(f"Document '{doc.get('file_name') or 'file'}' too large ({size} bytes); 5 MB limit.")
                continue
            try:
                meta = await api.get_file(file_id)
                data = await api.download_file(meta["file_path"])
            except Exception as exc:
                errors.append(f"Failed to download document: {exc}")
                continue
            if len(data) > _MAX_FILE_BYTES:
                errors.append("Document too large (over 5 MB).")
                continue
            attachments.append({
                "filename": doc.get("file_name") or f"telegram_doc_{uuid.uuid4().hex[:8]}",
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mime_type": doc.get("mime_type"),
                "store_to_workspace": True,
            })

    return attachments, errors


def _button_to_tg(b: dict[str, Any]) -> dict[str, Any]:
    """One AgentResponse Button dict -> a Telegram inline keyboard button."""
    btn: dict[str, Any] = {"text": str(b.get("label") or b.get("text") or "")}
    if b.get("url"):
        btn["url"] = b["url"]
    else:
        # callback_data is capped at 64 bytes by Telegram; fall back to the label.
        value = b.get("value")
        btn["callback_data"] = str(value if value is not None else btn["text"])[:64]
    return btn


def _pressed_button_label(callback_query: dict[str, Any], data: str) -> Optional[str]:
    """Find the visible label of the pressed button from the original keyboard.

    The callback_query only carries opaque ``callback_data``; the human-readable
    label lives on the message the buttons were attached to. Returns None if the
    keyboard isn't available (e.g. the message was edited away).
    """
    keyboard = ((callback_query.get("message") or {}).get("reply_markup") or {}).get("inline_keyboard") or []
    for row in keyboard:
        for btn in row:
            if btn.get("callback_data") == data:
                return btn.get("text")
    return None


def _view_ref_note(ref: dict[str, Any]) -> str:
    """A short note for a view_ref reply: kind + title + summary + Studio link.

    The deep-link is included only when ``AGENTS_HUB_PUBLIC_URL`` is configured
    (there's no reliable public URL otherwise)."""
    import os
    title = ref.get("title") or "view"
    summary = (ref.get("summary") or "").strip()
    lines = [f"📊 {title} ({ref.get('view_kind') or 'view'})"]
    if summary:
        lines.append(summary)
    base = (os.environ.get("AGENTS_HUB_PUBLIC_URL") or "").rstrip("/")
    if base and ref.get("view_id"):
        lines.append(f"Open in the Studio: {base}/studio/{ref['view_id']}")
    else:
        lines.append("Open the Visualization Studio to view it.")
    return "\n".join(lines)


def _render_for_telegram(payload: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    """Map an AgentResponse payload to send_message kwargs, or None.

    None means "no native Telegram rendering for this kind" — the caller then
    falls back to plain text. Understands the surface-agnostic ``buttons`` kind
    and the Telegram-native ``telegram`` kind.
    """
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")

    if kind == "buttons":
        buttons = payload.get("buttons") or []
        cols = max(1, int(payload.get("columns") or 1))
        rows = [
            [_button_to_tg(b) for b in buttons[i:i + cols]]
            for i in range(0, len(buttons), cols)
        ]
        if not rows:
            return None
        return {
            "text": payload.get("text") or payload.get("fallback_text") or "",
            "reply_markup": {"inline_keyboard": rows},
        }

    if kind == "telegram":
        rows = [
            [_button_to_tg(b) for b in row]
            for row in (payload.get("inline_keyboard") or []) if row
        ]
        out: dict[str, Any] = {"text": payload.get("text") or payload.get("fallback_text") or ""}
        if rows:
            out["reply_markup"] = {"inline_keyboard": rows}
        if payload.get("parse_mode"):
            out["parse_mode"] = payload["parse_mode"]
        return out

    return None


def _format_flow_reply(responses: list[dict[str, Any]]) -> str:
    """Join a flow's per-node outputs into one Telegram message.

    A single-node flow reads as a plain reply; multi-node flows are labelled by
    agent so the user can see which step produced what.
    """
    parts = [r for r in responses if str(r.get("response") or "").strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return str(parts[0].get("response") or "").strip()
    return "\n\n".join(
        f"*{r.get('agent_label') or r.get('agent_id') or 'step'}*\n{str(r.get('response') or '').strip()}"
        for r in parts
    )


async def _run_agent_for_telegram(
    api: TelegramAPI, chat_id: int, binding: dict[str, Any],
    text: str, attachments: list[dict[str, Any]],
) -> None:
    """Build a ChatRequest, drive the chat pipeline (agent or flow), send the reply."""
    # Lazy import to avoid module-load circular dependency between agents/ and routes/.
    from chat import run_chat_pipeline, run_chat_flow_pipeline
    from chat.models import ChatRequest, ChatAttachment
    from chat.runs import build_conversation_history

    workspace = binding.get("workspace")
    if attachments and not workspace:
        await _send_text(
            api, chat_id,
            "Attachments need a workspace on this binding. Set a workspace, then resend.",
        )
        return

    is_flow = bool(binding.get("flow_id"))
    conv_id = binding.get("conversation_id") or str(uuid.uuid4())
    if not binding.get("conversation_id"):
        telegram_store.upsert_binding(
            chat_id=chat_id,
            agent_id="" if is_flow else binding.get("agent_id", ""),
            flow_id=binding.get("flow_id") if is_flow else None,
            workspace=workspace,
            conversation_id=conv_id, title=binding.get("title"),
        )
        notify_change("telegram", chat_id=chat_id)

    # Telegram has no client-side transcript to send, so rebuild the prior turns
    # from this conversation's completed runs — otherwise every message would run
    # context-free, with no history block in the prompt.
    history = build_conversation_history(conv_id)

    request = ChatRequest(
        agent_id=None if is_flow else binding.get("agent_id"),
        flow_id=binding.get("flow_id") if is_flow else None,
        message=text or "",
        workspace=workspace,
        history=history,
        conversation_id=conv_id,
        conversation_title=binding.get("title") or f"Telegram chat {chat_id}",
        attachments=[ChatAttachment(**a) for a in attachments],
        source="telegram",  # tags the run's message_origin for the Messages list
    )

    await api.send_chat_action(chat_id, "typing")

    final_text: str = ""
    final_ok: bool = False
    final_error: Optional[str] = None
    final_response_obj: Optional[dict[str, Any]] = None
    # Links to the service entities the run touched (tasks, views, files, …).
    # Telegram has no dashboard to click through to, so they are appended to the
    # reply text as absolute links (see AGENTS_HUB_PUBLIC_URL).
    final_entities: list[dict[str, Any]] = []
    try:
        pipeline = run_chat_flow_pipeline(request) if is_flow else run_chat_pipeline(request)
        async for event in pipeline:
            if event.get("type") == "node_done":
                final_entities.extend(event.get("entities") or [])
            if event.get("type") == "done":
                final_entities.extend(event.get("entities") or [])
                final_ok = bool(event.get("ok"))
                final_error = event.get("error")
                if is_flow:
                    final_text = _format_flow_reply(event.get("responses") or [])
                else:
                    final_text = str(event.get("response") or "")
                    final_response_obj = event.get("response_obj")
                break
    except Exception as exc:
        final_error = str(exc)

    telegram_store.touch_binding(chat_id)

    log.info(
        "telegram reply chat=%s ok=%s response_obj=%s text_len=%d",
        chat_id, final_ok,
        (final_response_obj or {}).get("kind") if isinstance(final_response_obj, dict) else None,
        len(final_text or ""),
    )

    # A view_ref reply (a rich view was produced) has no native Telegram surface;
    # append a note with the view's summary + a Studio deep-link so the user can
    # open it (design §8: summary + deep-link now, server-rendered photo later).
    if isinstance(final_response_obj, dict) and final_response_obj.get("kind") == "view_ref":
        note = _view_ref_note(final_response_obj)
        final_text = f"{final_text}\n\n{note}".strip() if final_text else note

    # Entities the run touched live on dashboard pages Telegram cannot show;
    # append one link per entity so the user can open them from the chat.
    if final_entities and (final_ok or is_flow):
        from common.entity_links import append_entity_links
        final_text = append_entity_links(final_text, final_entities)

    # A structured response (buttons / inline keyboard) renders even when the
    # prose is empty — the model may emit only the UI block, in which case
    # final_text (the block-stripped text) is blank but the keyboard still must
    # show. On any Telegram rejection, log and fall back to plain text below.
    rendered = _render_for_telegram(final_response_obj) if (final_ok or is_flow) else None
    if rendered and rendered.get("reply_markup"):
        # The prose answer (final_text) is the main message; the block's own
        # `text` is a short prompt that sits above the buttons. Telegram has one
        # text field, so show both — prose first — de-duplicated (when the block
        # carries no own text the renderer echoes the prose, so they're equal).
        prose = (final_text or "").strip()
        block_text = (rendered.get("text") or "").strip()
        if prose and block_text and block_text != prose:
            msg_text = f"{prose}\n\n{block_text}"
        else:
            msg_text = prose or block_text or "⁣"  # invisible char: TG needs non-empty text
        try:
            await api.send_message(
                chat_id,
                msg_text,
                parse_mode=rendered.get("parse_mode"),
                reply_markup=rendered.get("reply_markup"),
            )
            return
        except Exception as exc:
            log.warning("telegram structured send failed for chat=%s: %s", chat_id, exc)
            # fall through to plain text so the user still gets the reply

    if final_text and (final_ok or is_flow):
        # Flows can partially fail; still surface whatever output was produced.
        await _send_text(api, chat_id, final_text)
    elif final_error:
        await _send_text(api, chat_id, f"Error: {final_error}")
    else:
        await _send_text(api, chat_id, "(no response)")


# ── Media group buffer ──────────────────────────────────────────────────────


class _MediaGroupBuffer:
    """Aggregate album updates that share a media_group_id within a debounce window."""

    def __init__(self):
        self._groups: dict[str, dict[str, Any]] = {}

    def add(self, message: dict[str, Any]) -> Optional[str]:
        """Add to a group; return the group id if a new flush task should be scheduled."""
        gid = message.get("media_group_id")
        if not gid:
            return None
        bucket = self._groups.setdefault(gid, {"messages": [], "deadline": 0.0})
        bucket["messages"].append(message)
        bucket["deadline"] = asyncio.get_running_loop().time() + _MEDIA_GROUP_DEBOUNCE
        return gid

    def pop_if_ready(self, gid: str) -> Optional[list[dict[str, Any]]]:
        bucket = self._groups.get(gid)
        if not bucket:
            return None
        if asyncio.get_running_loop().time() < bucket["deadline"]:
            return None
        return self._groups.pop(gid)["messages"]


# ── Service singleton ───────────────────────────────────────────────────────


class TelegramService:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._status: dict[str, Any] = {
            "running": False,
            "last_poll": None,
            "last_error": None,
            "bot_username": None,
        }
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._media_groups = _MediaGroupBuffer()
        # Chat ids we've already logged as disallowed, so a chat that keeps
        # writing to an unlisted bot doesn't spam the log on every message.
        self._logged_disallowed_chats: set[int] = set()

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    def _chat_lock(self, chat_id: int) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    def _chat_allowed(self, chat_id: int) -> bool:
        """Gate every inbound update on the configured chat allowlist.

        Without this, any Telegram user who finds the bot could talk to it
        (and, before the /workspace change above, bind themselves into a
        workspace). Disallowed chats are silently dropped, logged once per
        chat id rather than once per message.
        """
        if telegram_store.is_chat_allowed(chat_id):
            return True
        if chat_id not in self._logged_disallowed_chats:
            self._logged_disallowed_chats.add(chat_id)
            log.info("telegram update dropped: chat_id=%s is not on the allowlist", chat_id)
        return False

    async def start(self) -> None:
        if self.is_running():
            return
        token = telegram_store.get_token()
        if not token:
            self._status["last_error"] = "no token configured"
            return
        if not telegram_store.is_enabled():
            self._status["last_error"] = "telegram integration disabled"
            return

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        try:
            me = await TelegramAPI(token).get_me()
            self._status["bot_username"] = me.get("username")
            self._status["last_error"] = None
        except Exception as exc:
            self._status["last_error"] = f"getMe failed: {exc}"
            return

        self._status["running"] = True
        # Capability banner — if this line is absent or shows an older build than
        # the code on disk, the running poller is stale and needs a full process
        # restart to pick up connector / agent_factory changes.
        log.info(
            "Telegram poller started: bot=@%s build=%s capabilities=[%s]",
            self._status.get("bot_username") or "?", _CONNECTOR_BUILD, _CONNECTOR_FEATURES,
        )
        self._task = asyncio.create_task(self._poll_loop(token), name="telegram-poll")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:
                pass
        self._task = None
        self._stop_event = None
        self._status["running"] = False

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ── poll loop ────────────────────────────────────────────────────────────

    async def _poll_loop(self, token: str) -> None:
        api = TelegramAPI(token, timeout=35.0)
        assert self._stop_event is not None
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                offset = telegram_store.get_update_offset()
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.post(
                        f"{api._base}/getUpdates",
                        json={"offset": offset, "timeout": 25,
                              "allowed_updates": ["message", "callback_query"]},
                    )
                body = resp.json()
                if not body.get("ok"):
                    raise RuntimeError(body.get("description") or "getUpdates failed")
                updates = body.get("result") or []
                if updates:
                    max_id = max(int(u["update_id"]) for u in updates)
                    telegram_store.set_update_offset(max_id + 1)
                    for upd in updates:
                        # Dispatch each update on its own task so the poll loop never blocks
                        # on an in-flight agent run; per-chat locking serialises same-chat work.
                        asyncio.create_task(self._dispatch_update(api, upd))
                self._status["last_poll"] = _utc_iso()
                backoff = 1.0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._status["last_error"] = str(exc)[:200]
                log.warning("telegram poll error: %s", exc)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
        self._status["running"] = False

    async def _dispatch_update(self, api: TelegramAPI, update: dict[str, Any]) -> None:
        try:
            chat_id = _chat_id_from_update(update)
            if chat_id is not None and not self._chat_allowed(chat_id):
                return

            callback_query = update.get("callback_query")
            if callback_query:
                await self._handle_callback_query(api, callback_query)
                return

            message = update.get("message")
            if not message:
                return
            chat = message.get("chat") or {}
            chat_id = int(chat.get("id") or 0)
            if not chat_id:
                return

            # Reject unsupported message kinds early.
            unsupported = ("voice", "audio", "video_note", "sticker", "video")
            if any(message.get(k) for k in unsupported):
                await _send_text(api, chat_id, "This bot only supports text and photo/document attachments.")
                return

            # Media-group albums: buffer and process as one logical message.
            gid = self._media_groups.add(message)
            if gid:
                # Wait the debounce window then flush whatever has accumulated.
                await asyncio.sleep(_MEDIA_GROUP_DEBOUNCE + 0.1)
                messages = self._media_groups.pop_if_ready(gid)
                if not messages:
                    return  # another task is handling this group
                await self._handle_messages(api, chat_id, messages)
                return

            await self._handle_messages(api, chat_id, [message])
        except Exception as exc:
            log.exception("telegram dispatch error: %s", exc)
            self._status["last_error"] = str(exc)[:200]

    async def _handle_callback_query(self, api: TelegramAPI, callback_query: dict[str, Any]) -> None:
        """Treat an inline-button press as the user's next message.

        The button's ``callback_data`` is fed to the bound agent/flow exactly as
        if the user had typed it, so a single text-parsing path handles both.
        """
        cq_id = callback_query.get("id")
        data = (callback_query.get("data") or "").strip()
        label = _pressed_button_label(callback_query, data)
        # Acknowledge with a toast showing the choice (stops the client spinner).
        if cq_id:
            await api.answer_callback_query(cq_id, text=label or data)

        chat = (callback_query.get("message") or {}).get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        if not chat_id or not data:
            return

        binding = telegram_store.get_binding(chat_id)
        if not binding or not binding.get("workspace") or not (
            binding.get("agent_id") or binding.get("flow_id")
        ):
            await _send_text(api, chat_id, "This chat has no agent or flow bound. Use /agent <id> or /flow <id>.")
            return

        # Remove the keyboard from the original message so the (now-answered)
        # buttons can't be pressed again.
        message_id = (callback_query.get("message") or {}).get("message_id")
        if message_id:
            await api.edit_message_reply_markup(chat_id, int(message_id))

        # Inline-button presses aren't posted to the chat by Telegram, so echo the
        # selection as a visible message — otherwise the conversation has no record
        # of what the user chose.
        await _send_text(api, chat_id, f"➡️ {label or data}")

        async with self._chat_lock(chat_id):
            await _run_agent_for_telegram(api, chat_id, binding, data, [])

    async def _handle_messages(self, api: TelegramAPI, chat_id: int, messages: list[dict[str, Any]]) -> None:
        # Slash commands only make sense for single-message updates.
        if len(messages) == 1:
            text = (messages[0].get("text") or "").strip()
            if text.startswith("/"):
                handled = await _handle_command(api, messages[0], text)
                if handled:
                    return

        binding = telegram_store.get_binding(chat_id)
        if not binding or not binding.get("workspace"):
            await _send_text(
                api, chat_id,
                "Pick a workspace first with /workspace <name>, then /agent <id> or /flow <id>. /help for details.",
            )
            return
        if not binding.get("agent_id") and not binding.get("flow_id"):
            await _send_text(
                api, chat_id,
                f"Workspace `{binding['workspace']}` is set, but no agent or flow yet. "
                "Use /agent <id> (/agents to list) or /flow <id> (/flows to list).",
            )
            return

        async with self._chat_lock(chat_id):
            # Collect caption text (first non-empty) + attachments across the group.
            text = ""
            for m in messages:
                t = (m.get("text") or m.get("caption") or "").strip()
                if t and not text:
                    text = t
            attachments, errors = await _build_attachments_from_message(api, messages)
            for err in errors:
                await _send_text(api, chat_id, err)

            if not text and not attachments:
                return

            await _run_agent_for_telegram(api, chat_id, binding, text, attachments)


# Module-level singleton — imported by main.py + routes/telegram.py.
service = TelegramService()
