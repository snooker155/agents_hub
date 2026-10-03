"""
Tool results too long for the context go to a workspace file (docs/agent-loop.md).

A tool that returns a whole web page, a long command log or a big query result
used to put all of it into the conversation, where it stayed until compaction
(agents/loop_ext/compaction.py) cut it down, after the model had already paid
for reading it once and possibly lost the start of its context to it. This
module catches such a result at the moment it comes back: past the workspace
loop setting ``tool_output_spill_chars`` (agents/loop_ext/settings.py, 20000 by
default, 0 turns it off) the full text is written to
``tool-outputs/<run_id>/<n>-<tool>.txt`` under the agent's own operating
folder, registered as a workspace file (files/service.py, so it has a file id
and the run page links it by id), and the model gets the first and the last
part plus the path and a hint that ``read_file`` reads the rest by range.

The file is written under the folder the agent's filesystem tools are rooted
at (``tools/filesystem.py``), the folder a container run has mounted, so
``read_file`` reaches it in a local run and in a container alike. Registering
needs the database: a container that relays its state over HTTP writes the
file and leaves the record to the backend, which registers it when the run
closes (:func:`register_relayed_spills`).

Saved outputs live as long as run records do (``run_retention_days``): each
new spill sweeps the run folders past that age (:func:`_sweep_old`). The
folder carries a ``.gitignore`` of ``*``, since the operating folder is often
a project's git checkout.

``read_file`` itself is never spilled: its result is a file already. A read of
a spilled file that is still too long is clipped instead, with a pointer to
the ``offset`` and ``limit`` arguments, so reading the file cannot spill it
again in a loop.

Every tool of a built agent that has ``read_file`` is wrapped once, innermost
(:func:`wrap_tools`, called by agents/agent_factory.py before the approval
guard and the think gate), so the hooks, the run's tool-call record
(agents/callbacks/run_statistics.py, which adds the file id) and the trail all
see the preview, never the full text.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool

log = logging.getLogger(__name__)

#: Folder, under the agent's operating folder, that holds spilled outputs.
SPILL_DIR = "tool-outputs"

#: The default threshold in characters (the loop setting overrides it).
DEFAULT_THRESHOLD = 20000

#: A threshold below this is raised to it: a preview of a few hundred
#: characters plus a file would cost more turns than it saves.
MIN_THRESHOLD = 1000

#: Tools whose result is never spilled: ``read_file`` returns a file that
#: already exists (see the module docstring), the others end or pause the turn.
NEVER_SPILLED = frozenset({"read_file", "ask_user", "handoff_to_agent", "consult_advisor"})

#: First line of a preview. :func:`spill_fields` reads the file id and path
#: back from it, so the run's tool-call record can link the file.
_HEADER_RE = re.compile(
    r"^\[Tool output saved to a file: (?P<chars>\d+) characters, path (?P<path>\S+)"
    r"(?:, file id (?P<file_id>file_[0-9a-f]{16}))?\]")


def _threshold(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    if number <= 0:
        return 0
    return max(MIN_THRESHOLD, number)


def threshold_for(workspace: Optional[str]) -> int:
    """The spill threshold for an agent working in ``workspace`` (its
    operating path), 0 when spilling is off."""
    from agents.loop_ext.settings import workspace_loop_setting
    return _threshold(workspace_loop_setting(workspace, "tool_output_spill_chars", DEFAULT_THRESHOLD))


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "tool"))[:60] or "tool"


def _run_id(state: Any) -> str:
    run_id = str(getattr(state, "run_id", "") or os.environ.get("AGENT_RUN_ID") or "")
    return _safe(run_id) if run_id else time.strftime("run-%Y%m%d-%H%M%S", time.gmtime())


def _next_index(state: Any, folder: Path) -> int:
    """A per-run counter for file names, kept on the loop state when there is
    one, else read off what the folder already holds."""
    if state is not None:
        n = int(state.scratch.get("tool_spill_n") or 0) + 1
        state.scratch["tool_spill_n"] = n
        return n
    try:
        return len(list(folder.glob("*.txt"))) + 1
    except OSError:
        return 1


def _register(workspace: str, rel: str, tool: str, run_id: str) -> Optional[str]:
    """Register the written file as a workspace file; its id, or None when
    the operating folder is not under the workspaces root or the registry
    cannot be reached (a container without the database)."""
    try:
        from common import db
        from common.config import run_state_transport
        if run_state_transport() == "http" and not db.is_postgres():
            # A container that relays its state over HTTP has its SQLite state
            # mounted read only: nothing to register in; the backend does it
            # when the run closes (register_relayed_spills). Under Postgres
            # the relay is the default even for processes that do hold the
            # database URL (the backend, a chat replica), and a process that
            # holds it registers here; one handed no URL reads as SQLite.
            return None
        from tools.filesystem_langchain import _registry_target
        target = _registry_target(workspace)
        if target is None:
            return None
        reg_ws, prefix = target
        from files import service as files_service
        from tools.workspace_files import agent_provenance
        meta = {**agent_provenance(), "tool_output_of": tool, "run_id": run_id}
        record = files_service.register_path(reg_ws, f"{prefix}{rel}", source="agent",
                                             created_by=meta.get("agent_id"), meta=meta)
        return str(record.get("file_id") or "") or None
    except Exception:  # noqa: BLE001 - the file is written; the next index registers it
        log.debug("tool spill: could not register %s", rel, exc_info=True)
        return None


def _sweep_old(root: Path, *, keep: str) -> int:
    """Delete the saved outputs of runs older than ``run_retention_days``
    (the age the run records themselves are kept, common/maintenance.py)
    under ``root``, and tombstone their file records. Runs on every spill, so
    a folder that keeps getting long results stays bounded without a
    separate job; one ``stat`` per run folder when nothing is old. Returns how
    many run folders went."""
    try:
        from common.config import settings
        days = int(getattr(settings, "run_retention_days", 30) or 0)
    except (TypeError, ValueError):
        days = 30
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    base = root / SPILL_DIR
    gone = 0
    try:
        entries = [p for p in base.iterdir() if p.is_dir() and p.name != keep]
    except OSError:
        return 0
    import shutil
    for folder in entries:
        try:
            if folder.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(folder)
        except OSError:
            log.debug("tool spill: could not remove %s", folder, exc_info=True)
            continue
        gone += 1
        try:
            from tools.filesystem_langchain import _registry_target
            target = _registry_target(str(root))
            if target is not None:
                from files import service as files_service
                reg_ws, prefix = target
                files_service.unregister_tree(reg_ws, f"{prefix}{SPILL_DIR}/{folder.name}")
        except Exception:  # noqa: BLE001 - the folder is gone; the next index drops the records
            log.debug("tool spill: could not unregister %s", folder, exc_info=True)
    return gone


def register_relayed_spills(run_id: str, loop: Dict[str, Any]) -> Dict[str, Any]:
    """``loop`` with a file id on every spill a container wrote without one.

    Called by the backend when a container run closes over the HTTP relay
    (dashboard/backend/routes/run_state.py): the container wrote the files
    into the workspace folder it has mounted but had no database to register
    them in. Each is found under the run's workspace folder by its path and
    registered here, so the run page can link it. Never raises.
    """
    spills = loop.get("tool_spills") if isinstance(loop, dict) else None
    if not spills or all(s.get("file_id") for s in spills if isinstance(s, dict)):
        return loop
    try:
        from files import service as files_service
        from managers import run_manager
        from workspace import WORKSPACES_ROOT
        run = run_manager.get_run_by_id(run_id) or {}
        ws = str(run.get("workspace") or "")
        root = Path(WORKSPACES_ROOT) / ws
        if not ws or not root.is_dir():
            return loop
        out = []
        for spill in spills:
            item = dict(spill) if isinstance(spill, dict) else spill
            path = str(item.get("path") or "") if isinstance(item, dict) else ""
            if isinstance(item, dict) and path.startswith(f"{SPILL_DIR}/") and not item.get("file_id"):
                match = next(iter(sorted(root.glob(f"**/{path}"))), None)
                if match is not None and match.is_file():
                    record = files_service.register_path(
                        ws, match.relative_to(root).as_posix(), source="agent",
                        meta={"tool_output_of": item.get("tool"), "run_id": run_id})
                    item["file_id"] = record.get("file_id")
            out.append(item)
        return {**loop, "tool_spills": out}
    except Exception:  # noqa: BLE001 - the run closes either way; the files stay findable by path
        log.debug("tool spill: could not register the relayed spills of %s", run_id, exc_info=True)
        return loop


def preview(text: str, *, threshold: int, path: str, file_id: Optional[str],
            has_reader: bool = True) -> str:
    """What the model sees instead of ``text``: a header naming the file, the
    first and the last part, and how to read the rest."""
    head_n = min(6000, max(400, threshold // 2))
    tail_n = min(3000, max(200, threshold // 4))
    total = len(text)
    ref = f"path {path}" + (f", file id {file_id}" if file_id else "")
    how = ("Read any part of it with read_file (offset and limit select a range of "
           "characters) or search it with search_text; read only what you need."
           if has_reader else "The full output is kept in that file.")
    omitted = max(0, total - head_n - tail_n)
    return (
        f"[Tool output saved to a file: {total} characters, {ref}]\n"
        f"The output was too long for the context. {how}\n\n"
        f"--- first {head_n} characters ---\n{text[:head_n]}\n"
        f"--- {omitted} characters not shown ---\n"
        f"--- last {tail_n} characters ---\n{text[-tail_n:]}"
    )


def spill_fields(output: Any) -> Dict[str, Any]:
    """``{"spill": {path, file_id, chars}}`` when ``output`` is a preview this
    module made, else ``{}``. The run's tool-call record carries it."""
    if not isinstance(output, str) or not output.startswith("[Tool output saved"):
        return {}
    match = _HEADER_RE.match(output)
    if match is None:
        return {}
    return {"spill": {"path": match.group("path"), "file_id": match.group("file_id"),
                      "chars": int(match.group("chars"))}}


def _clip_spill_read(output: str, tool_input: Any, threshold: int) -> str:
    """A ``read_file`` of a spilled file that is itself too long: the start of
    the content and a pointer to the range arguments, never a new spill."""
    path = tool_input.get("path") if isinstance(tool_input, dict) else tool_input
    if f"{SPILL_DIR}/" not in str(path or "").replace("\\", "/"):
        return output
    try:
        data = json.loads(output)
        content = str(data.get("content") or "")
    except (ValueError, AttributeError):
        content = output
    limit = max(MIN_THRESHOLD, threshold - 400)
    if len(content) <= limit:
        return output
    return json.dumps({
        "path": path,
        "content": content[:limit],
        "offset": 0,
        "returned_chars": limit,
        "total_chars": len(content),
        "note": (f"This saved tool output is {len(content)} characters. Read the rest with "
                 f"read_file and offset (next: {limit}) and limit."),
    }, ensure_ascii=False)


def spill_output(tool: str, tool_input: Any, output: Any, *, workspace: Optional[str],
                 threshold: int, has_reader: bool = True) -> Any:
    """``output`` as the model should see it: unchanged when short (or not
    text), else written to a file and replaced by :func:`preview`."""
    if not threshold or not workspace or not isinstance(output, str) or len(output) <= threshold:
        return output
    if tool == "read_file":
        return _clip_spill_read(output, tool_input, threshold)
    if tool in NEVER_SPILLED:
        return output
    from agents.agent_loop import current_state

    state = current_state()
    run_id = _run_id(state)
    root = Path(workspace).resolve()
    folder = root / SPILL_DIR / run_id
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # The operating folder is often a project's git checkout: keep the
        # saved outputs out of its status and commits.
        ignore = root / SPILL_DIR / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        _sweep_old(root, keep=run_id)
        name = f"{_next_index(state, folder):03d}-{_safe(tool)}.txt"
        (folder / name).write_text(output, encoding="utf-8")
    except OSError:
        log.warning("tool spill: could not write the output of %s, keeping it inline", tool,
                    exc_info=True)
        return output
    rel = f"{SPILL_DIR}/{run_id}/{name}"
    file_id = _register(str(workspace), rel, tool, run_id)
    if state is not None:
        state.tool_spills.append({"tool": tool, "path": rel, "file_id": file_id,
                                  "chars": len(output)})
    return preview(output, threshold=threshold, path=rel, file_id=file_id, has_reader=has_reader)


def _merge_tool_input(args: tuple, kwargs: dict) -> Any:
    if kwargs:
        return dict(kwargs)
    if len(args) == 1:
        return args[0]
    return list(args)


class SpillingTool(BaseTool):
    """Wraps a tool so a result past the threshold goes to a file."""

    inner: BaseTool
    workspace: str = ""
    threshold: int = DEFAULT_THRESHOLD
    has_reader: bool = True

    name: str = ""
    description: str = ""
    # Any: an MCP tool's schema is a plain JSON Schema dict, not a model.
    args_schema: Any = None

    def __init__(self, inner: BaseTool, *, workspace: str, threshold: int,
                 has_reader: bool = True, **kwargs: Any) -> None:
        super().__init__(
            inner=inner, workspace=workspace, threshold=threshold, has_reader=has_reader,
            name=inner.name, description=inner.description, args_schema=inner.args_schema,
            return_direct=bool(getattr(inner, "return_direct", False)), **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.pop("run_manager", None)
        tool_input = _merge_tool_input(args, kwargs)
        return spill_output(self.name, tool_input, self.inner.run(tool_input),
                            workspace=self.workspace, threshold=self.threshold,
                            has_reader=self.has_reader)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.pop("run_manager", None)
        tool_input = _merge_tool_input(args, kwargs)
        output = await self.inner.arun(tool_input)
        return spill_output(self.name, tool_input, output, workspace=self.workspace,
                            threshold=self.threshold, has_reader=self.has_reader)


def wrap_tools(tools: List[Any], *, workspace: Optional[str]) -> List[Any]:
    """Every tool of an agent wrapped by :class:`SpillingTool`, or the list
    unchanged when spilling is off for the workspace, the agent has no
    operating folder to write into, or it has no ``read_file`` to read a
    saved output back with (it would only lose the middle of the result; its
    long results are left to compaction, as before)."""
    if not workspace:
        return tools
    names = {getattr(t, "name", "") for t in tools}
    has_reader = "read_file" in names
    if not has_reader:
        return tools
    threshold = threshold_for(workspace)
    if not threshold:
        return tools
    out: List[Any] = []
    for t in tools:
        name = getattr(t, "name", "")
        # A single-input tool with no schema is left alone: a wrapper could
        # not show the model the same arguments (the approval guard has the
        # same limit), and the hub's own tools all carry one.
        if (not isinstance(t, BaseTool) or getattr(t, "args_schema", None) is None
                or (name in NEVER_SPILLED and name != "read_file")):
            out.append(t)
        else:
            out.append(SpillingTool(t, workspace=str(workspace), threshold=threshold,
                                    has_reader=has_reader))
    return out


__all__ = [
    "DEFAULT_THRESHOLD", "NEVER_SPILLED", "SPILL_DIR", "SpillingTool", "preview",
    "register_relayed_spills", "spill_fields", "spill_output", "threshold_for", "wrap_tools",
]
