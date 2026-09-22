"""
Chat prompt-context builders.

Pure functions that turn a :class:`ChatRequest` into what the agent runs
against: the conversation as bounded messages, this turn's text with its
attachment and reference blocks, and the resolved workspace. Shared by both the
single-agent and flow chat pipelines.

History used to be rendered into the prompt string as ``User:`` / ``Assistant:``
lines. It now travels as messages (:func:`build_history_messages`), which is
both what the model reads best and what keeps the prompt prefix stable enough
for a provider cache to recognise. The text rendering stays for the callers that
do not build a prompt for an agent executor, and for reading back sessions that
were recorded the old way.
"""
import re

from chat.models import ChatRequest
from chat.references import build_reference_lines
from common.workspace_context import (
    _workspace_ctx,
    _project_ctx,
    normalize_project_id,
    workspace_name_from_path,
)

# Markers used by ``history_block_lines`` / ``build_chat_context`` to fold prior
# turns into a single prompt string; ``split_embedded_history`` reads them back.
_HISTORY_HEADER = "Conversation history:"
_LATEST_MARKER = "Latest user message:"
_TURN_RE = re.compile(r"^(User|Assistant):\s?")


def split_embedded_history(prompt: str) -> tuple[list[dict], str]:
    """Recover the structured history turns folded into a chat prompt.

    Sessions recorded before history became structured messages — and any caller
    that still asks ``build_chat_context`` to embed it — carry prior turns as a
    "Conversation history:" block of ``User:``/``Assistant:`` lines followed by a
    "Latest user message:" section. This splits that text back into ``[{role, content}]``
    turns plus the real latest message, so the stored run input-context holds one
    dedicated history block per prior session message — rather than the whole
    blob, or the agent's own intra-run loop output. Returns ``([], prompt)`` when
    no history block is present (first turn, or a non-chat prompt).
    """
    text = str(prompt or "")
    header_idx = text.find(_HISTORY_HEADER)
    latest_idx = text.find(_LATEST_MARKER)
    if header_idx == -1 or latest_idx == -1 or latest_idx < header_idx:
        return [], text
    block = text[header_idx + len(_HISTORY_HEADER):latest_idx]
    latest = text[latest_idx + len(_LATEST_MARKER):]
    if latest.startswith("\n"):
        latest = latest[1:]

    history: list[dict] = []
    current: dict | None = None
    for line in block.split("\n"):
        m = _TURN_RE.match(line)
        if m:
            if current is not None:
                history.append(current)
            current = {
                "role": "user" if m.group(1) == "User" else "assistant",
                "content": line[m.end():],
            }
        elif current is not None:
            current["content"] += "\n" + line
    if current is not None:
        history.append(current)
    # Trim trailing blank lines left by the block separators.
    for h in history:
        h["content"] = h["content"].rstrip()
    return history, latest.rstrip()


#: Bounds on the conversation a turn carries. The last 40 messages, each
#: truncated to 4k chars, up to 60k chars in total.
HISTORY_MAX_MESSAGES = 40
HISTORY_MAX_MESSAGE_CHARS = 4000
HISTORY_CHAR_BUDGET = 60_000

_ROLE_LABELS = {"user": "User", "assistant": "Assistant"}


def bounded_history(history: list) -> list[tuple[str, str]]:
    """The tail of a conversation that fits the prompt bounds, oldest first.

    Returns ``(role, content)`` pairs with ``role`` one of ``user`` /
    ``assistant``. One definition behind both renderings — text lines and
    structured messages — so a turn that reaches the model as a message is
    exactly the turn that used to reach it as a line, truncation included.
    """
    turns: list[tuple[str, str]] = []
    budget = HISTORY_CHAR_BUDGET
    for msg in reversed(history[-HISTORY_MAX_MESSAGES:]):
        role = "user" if str(msg.role) == "user" else "assistant"
        content = str(msg.content or "")
        if len(content) > HISTORY_MAX_MESSAGE_CHARS:
            content = content[:HISTORY_MAX_MESSAGE_CHARS] + "\n...[truncated]"
        # The budget is spent on the rendered line, label included, which is what
        # it has always been measured against.
        cost = len(f"{_ROLE_LABELS[role]}: {content}")
        if budget - cost < 0:
            break
        budget -= cost
        turns.insert(0, (role, content))
    return turns


def build_history_lines(history: list) -> list[str]:
    """Bounded conversation history as ``Role: content`` lines (oldest first).

    The text rendering, kept for the prompts that are not built for a
    ``StandardAgent`` executor: the team goal (handed to ``teams.runner``) and
    the flow nodes' text blocks. Agent chat sends
    :func:`build_history_messages` instead.
    """
    return [f"{_ROLE_LABELS[role]}: {content}" for role, content in bounded_history(history)]


def build_history_messages(history: list) -> list:
    """Bounded conversation history as LangChain messages (oldest first).

    ``HumanMessage`` / ``AIMessage`` only: a tool call belongs to the run that
    made it and is never replayed into a later turn. Same bounds as
    :func:`build_history_lines`. The agent prompt takes these through its
    ``chat_history`` placeholder, which is what lets the model read the
    conversation as a conversation and lets a provider prompt cache recognise
    the prefix it saw last turn.
    """
    from langchain_core.messages import AIMessage, HumanMessage
    return [
        HumanMessage(content=content) if role == "user" else AIMessage(content=content)
        for role, content in bounded_history(history)
    ]


def history_block_lines(history_lines: list[str]) -> list[str]:
    """The history preamble + lines to prepend before the latest user message,
    or an empty list when there is no history."""
    if not history_lines:
        return []
    return [
        "Use the conversation history for context when answering the latest user message.",
        "",
        "Conversation history:",
        *history_lines,
        "",
    ]


def build_attachment_lines(attachments: list) -> list[str]:
    """Render chat attachments as prompt lines (header + one block per file),
    or an empty list when there are none. Binary payloads are referenced by
    their stored workspace path rather than dumped into the prompt."""
    if not attachments:
        return []
    lines: list[str] = ["=== Attached files ==="]
    for idx, att in enumerate(attachments, start=1):
        stored_note = ""
        if getattr(att, "stored_workspace_path", None):
            stored_note = f" (stored at workspace path: {att.stored_workspace_path})"
        is_binary = bool(getattr(att, "content_b64", None))
        mime = getattr(att, "mime_type", None) or ""
        mime_note = f" mime={mime}" if mime else ""
        lines.append(f"[Attachment {idx}] {att.filename}{stored_note}{mime_note}")
        if is_binary:
            lines.append("(binary file — see stored workspace path above)")
        else:
            content = (att.content or "")
            if len(content) > 40000:
                content = content[:40000] + "\n...[truncated]"
            lines.extend(["```", content, "```"])
        lines.append("")
    return lines


def resolve_project(request: ChatRequest):
    """The selected Project record for the request, or None.

    Returns None when no project is selected or the id no longer resolves, so
    callers transparently fall back to whole-workspace scope.
    """
    pid = normalize_project_id(getattr(request, "project_id", None))
    if not pid:
        return None
    try:
        from common.paths import PROJECTS_FILE
        from projects.storage import ProjectStore
        return ProjectStore(path=PROJECTS_FILE).get(pid)
    except Exception:
        return None


def project_scope_note(request: ChatRequest) -> str | None:
    """The one-line project-scope preamble for a chat prompt, or None when no
    project is selected. Shared by single-agent and flow chat so both surfaces
    tell the agent it is operating inside the project."""
    proj = resolve_project(request)
    if proj is None:
        return None
    return (
        f'You are scoped to the project "{proj.name}". Your file tools operate '
        f"inside this project's folder, and task tools list only this project's "
        f"tasks. Treat paths and tasks as relative to this project."
    )


def resolve_workspace_abs(request: ChatRequest) -> str | None:
    """Absolute path the agent's file tools operate in, or None when unresolvable.

    When the request selects a project, this is the project's subfolder under the
    workspace (so file tools see only that project's code); otherwise it is the
    workspace root.
    """
    if not request.workspace:
        return None
    try:
        from workspace import (
            create_workspace_folder,
            resolve_project_root,
            project_folder_name,
        )
        proj = resolve_project(request)
        if proj is not None:
            return str(resolve_project_root(
                proj.workspace or request.workspace, project_folder_name(proj.name)
            ))
        return str(create_workspace_folder(request.workspace))
    except Exception:
        return None


def apply_workspace_ctx(request: ChatRequest, workspace_abs: str | None) -> str | None:
    """Derive the workspace name and publish it (plus the selected project id) on
    the thread/async-safe context vars so agent tools (e.g. list_tasks) scope to
    them during the run. Returns the resolved workspace name.

    ``workspace_abs`` may now point at a project subfolder, so the name is
    recovered with ``workspace_name_from_path`` (which walks back to the
    workspace component) rather than a bare basename."""
    ws_name = request.workspace or workspace_name_from_path(workspace_abs)
    _workspace_ctx.set(ws_name)
    _project_ctx.set(normalize_project_id(getattr(request, "project_id", None)))
    return ws_name


def context_block_lines(request: ChatRequest) -> list[str]:
    """The attached-entity and attached-file blocks that trail a chat prompt.

    One builder for all three pipelines (agent / flow / team) so a reference
    attached in the composer reads the same whichever target answers. Assumes
    ``resolve_references`` has already run — an unresolved reference carries no
    content and is skipped.
    """
    blocks: list[str] = []
    for group in (
        build_reference_lines(request.references, getattr(request, "agent_id", None)),
        build_attachment_lines(request.attachments),
    ):
        if group:
            blocks.append("")
            blocks.extend(group)
    return blocks


def build_chat_context(request: ChatRequest, *, embed_history: bool = False) -> tuple[str, str | None]:
    """The single-agent human message + resolved workspace path.

    The project-scope note, the latest user message, then the attached-entity and
    attachment blocks. History is *not* part of it: it travels as structured
    messages (:func:`build_history_messages`) so the text of this turn stays the
    text of this turn. ``embed_history=True`` restores the old single-blob prompt
    for a caller that cannot send messages.
    """
    workspace_abs = resolve_workspace_abs(request)

    lines: list[str] = []
    note = project_scope_note(request)
    if note:
        lines.extend([note, ""])

    # The "Latest user message:" marker pairs with the "Conversation history:"
    # block (split_embedded_history reads them back together), so it is gated on
    # history presence only — not on the optional project note above.
    history_lines = (history_block_lines(build_history_lines(request.history))
                     if embed_history else [])
    lines.extend(history_lines)
    if history_lines:
        lines.append("Latest user message:")
    lines.append(request.message)
    # Attached hub entities lead the attached files: they are the records the
    # turn is *about*, while an uploaded file is usually raw material.
    lines.extend(context_block_lines(request))
    full_prompt = "\n".join(lines)
    return full_prompt, workspace_abs
