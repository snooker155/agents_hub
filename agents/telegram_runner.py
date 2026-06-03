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
from common import telegram_store


log = logging.getLogger("telegram")


_TG_API = "https://api.telegram.org"
_MAX_FILE_BYTES = 5 * 1024 * 1024  # keep parity with the web chat 5 MB cap
_MEDIA_GROUP_DEBOUNCE = 1.5  # seconds — wait this long for the rest of an album


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    async def send_message(self, chat_id: int, text: str, *, parse_mode: str | None = None) -> dict[str, Any]:
        chunks = _split_for_telegram(text)
        last: dict[str, Any] = {}
        for chunk in chunks:
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            last = await self._post("sendMessage", payload)
        return last

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
    "Setup order: pick a workspace, then pick an agent OR a flow allowed in "
    "that workspace.\n"
    "\n"
    "Commands:\n"
    "/workspace [name] — show or set this chat's workspace\n"
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
    """Return all defined flows, or [] on any error."""
    try:
        from pathlib import Path
        import json
        flows_file = Path(__file__).resolve().parents[1] / "agents" / "state" / "flows.json"
        if not flows_file.exists():
            return []
        data = json.loads(flows_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
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
        workspaces = _workspace_names()
        preview = ", ".join(workspaces[:10]) or "(no workspaces)"
        await _send_text(
            api, chat_id,
            "Welcome! First pick a workspace with /workspace <name>, then pick an "
            "agent with /agent <id> or a flow with /flow <id>.\n"
            f"Workspaces: {preview}\n"
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
        if not args:
            current = binding.get("workspace") or "(not set)"
            await _send_text(api, chat_id, f"Current workspace: {current}. Use /workspace <name> to change it.")
            return True
        workspaces = _workspace_names()
        if args not in workspaces:
            await _send_text(
                api, chat_id,
                f"Unknown workspace `{args}`. Available: {', '.join(workspaces) or '(none)'}",
            )
            return True
        # Setting (or changing) the workspace invalidates the previous target
        # (agent or flow) if the new workspace doesn't authorise/contain it.
        was_changed = bool(binding.get("workspace")) and binding.get("workspace") != args
        new_agent = binding.get("agent_id")
        new_flow = binding.get("flow_id")
        if was_changed:
            allowed = _allowed_agent_ids_for(args)
            if allowed is not None and new_agent not in allowed:
                new_agent = None
            if new_flow and not any(f.get("id") == new_flow for f in _flows_visible_in(args)):
                new_flow = None
        telegram_store.upsert_binding(
            chat_id=chat_id,
            agent_id=new_agent or "",
            flow_id=new_flow,
            workspace=args,
            conversation_id=binding.get("conversation_id") or str(uuid.uuid4()),
            title=_chat_title(message),
        )
        if new_flow:
            flow = _get_flow(new_flow)
            flow_name = (flow or {}).get("name") or new_flow
            await _send_text(
                api, chat_id,
                f"Workspace set to `{args}`.\nFlow stays as `{flow_name}`. "
                "Send a message to run it, or /agent <id> / /flow <id> to change.",
            )
            return True
        visible = _agents_visible_in(args)
        flows_here = _flows_visible_in(args)
        flow_hint = (
            f"\nFlows available: {', '.join((f.get('name') or f.get('id')) for f in flows_here[:15])} (/flow <id>)"
            if flows_here else ""
        )
        if not visible:
            await _send_text(
                api, chat_id,
                f"Workspace `{args}` set. No agents are authorised in this workspace yet."
                + flow_hint,
            )
        else:
            preview = ", ".join(a.id for a in visible[:15])
            extra_lines = (
                ["The previous agent isn't authorised here — pick another with /agent <id>."]
                if was_changed and not new_agent else
                [f"Agent stays as `{new_agent}`. Send a message to start, or /agent <id> to change."]
                if new_agent else
                ["Now pick an agent with /agent <id>, or a flow with /flow <id>."]
            )
            await _send_text(
                api, chat_id,
                f"Workspace set to `{args}`.\nAvailable agents: {preview}{flow_hint}\n"
                + "\n".join(extra_lines),
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
    from routes.chat import run_chat_pipeline, run_chat_flow_pipeline
    from models import ChatRequest, ChatAttachment

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

    request = ChatRequest(
        agent_id=None if is_flow else binding.get("agent_id"),
        flow_id=binding.get("flow_id") if is_flow else None,
        message=text or "",
        workspace=workspace,
        conversation_id=conv_id,
        conversation_title=binding.get("title") or f"Telegram chat {chat_id}",
        attachments=[ChatAttachment(**a) for a in attachments],
    )

    await api.send_chat_action(chat_id, "typing")

    final_text: str = ""
    final_ok: bool = False
    final_error: Optional[str] = None
    try:
        pipeline = run_chat_flow_pipeline(request) if is_flow else run_chat_pipeline(request)
        async for event in pipeline:
            if event.get("type") == "done":
                final_ok = bool(event.get("ok"))
                final_error = event.get("error")
                if is_flow:
                    final_text = _format_flow_reply(event.get("responses") or [])
                else:
                    final_text = str(event.get("response") or "")
                break
    except Exception as exc:
        final_error = str(exc)

    telegram_store.touch_binding(chat_id)

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
                        json={"offset": offset, "timeout": 25, "allowed_updates": ["message"]},
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
