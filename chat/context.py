"""
Chat prompt-context builders.

Pure functions that turn a :class:`ChatRequest` into the prompt text the agent
runs against: bounded conversation history, attachment blocks, and workspace
resolution. Shared by both the single-agent and flow chat pipelines.
"""
import re
from pathlib import Path

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

    ``build_chat_context`` embeds prior session turns as a "Conversation
    history:" block of ``User:``/``Assistant:`` lines followed by a "Latest user
    message:" section. This splits that text back into ``[{role, content}]``
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


def build_history_lines(history: list) -> list[str]:
    """Bounded conversation history as ``Role: content`` lines (oldest first).

    Keeps the last 40 messages, truncates each to 4k chars, and stops once a
    60k total-character budget is exhausted. Shared by the agent-chat and
    flow-chat prompt builders.
    """
    history_lines: list[str] = []
    budget = 60_000
    for msg in reversed(history[-40:]):
        role = "User" if str(msg.role) == "user" else "Assistant"
        content = str(msg.content or "")
        if len(content) > 4000:
            content = content[:4000] + "\n...[truncated]"
        line = f"{role}: {content}"
        if budget - len(line) < 0:
            break
        budget -= len(line)
        history_lines.insert(0, line)
    return history_lines


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


def build_chat_context(request: ChatRequest) -> tuple[str, str | None]:
    """The full single-agent prompt + resolved workspace path: bounded history,
    the latest user message, then attachment blocks."""
    workspace_abs = resolve_workspace_abs(request)

    lines: list[str] = []
    note = project_scope_note(request)
    if note:
        lines.extend([note, ""])

    # The "Latest user message:" marker pairs with the "Conversation history:"
    # block (split_embedded_history reads them back together), so it is gated on
    # history presence only — not on the optional project note above.
    history_lines = history_block_lines(build_history_lines(request.history))
    lines.extend(history_lines)
    if history_lines:
        lines.append("Latest user message:")
    lines.append(request.message)
    # Attached hub entities lead the attached files: they are the records the
    # turn is *about*, while an uploaded file is usually raw material.
    lines.extend(context_block_lines(request))
    full_prompt = "\n".join(lines)
    return full_prompt, workspace_abs
