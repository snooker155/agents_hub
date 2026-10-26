"""
Messages (Individual Agent Runs) API routes.

A message represents a single agent run - its log, tools, token usage, etc.
This is the granular view of execution, as opposed to /api/sessions which
groups multiple messages into a process-level context.
"""
import json
import re

from fastapi import APIRouter, HTTPException, Request
from typing import Optional
from pathlib import Path

from common import access, audit, identity, live_runs
from managers import run_manager
from managers.run_manager import update_run as update_message_run
from tasks import service as tasks_service

# Re-use all helper logic from sessions module
from routes.sessions import (
    _enrich_run,
    _resolve_session_model,
    _resolve_session_provider,
    _build_context_window_metrics,
    _extract_chat_message_runs,
    _extract_worker_io,

    _extract_tools_from_progress,
    _extract_tools_from_log,
    _attach_tool_verdicts,
    _build_thinking_trace,
    _parse_reasoning_line,
)
import logging

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/messages", tags=["messages"])

# Separate router (its own prefix, /api/runs): the agent-version and rollback
# endpoints below are about a run's *agent* rather than the run's own log/tool
# data, and /api/messages/{run_id}/... would read oddly for "roll the agent
# back". Registered in dashboard/backend/main.py alongside ``router`` above
# (see that file's own app.include_router calls next to messages.router).
runs_router = APIRouter(prefix="/api/runs", tags=["runs"])

FLOW_AGENT_IDS = {"flow-graph", "flow-custom-graph", "custom-graph"}


def _compute_is_flow(run: dict) -> bool:
    """Detect if a run is part of a factory/flow execution."""
    if run.get("is_flow") is not None:
        return bool(run["is_flow"])
    return run.get("agent_id", "") in FLOW_AGENT_IDS


def _enrich_message(run: dict, tasks_by_id: dict) -> dict:
    enriched = _enrich_run(run, tasks_by_id)
    enriched["is_flow"] = _compute_is_flow(run)
    enriched["session_id"] = run.get("session_id")
    return enriched


def _enrich_page(runs: list) -> list:
    """Attach task titles to a page of runs, fetching only the tasks it needs."""
    task_ids = {str(r.get("task_id")) for r in runs if r.get("task_id")}
    tasks_by_id = tasks_service.get_tasks(task_ids) if task_ids else {}
    return [_enrich_message(r, tasks_by_id) for r in runs]


def _require_run_visible(request: Request, run: dict) -> None:
    """403 unless the caller may see the workspace this run belongs to."""
    principal = identity.request_principal(request)
    access.require_visible(principal, run.get("workspace"))


@router.get("")
async def list_messages(
    request: Request,
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    session_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    is_flow: Optional[bool] = None,
    channel: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    sort: Optional[str] = None,
    order: Optional[str] = None,
):
    """A page of agent run messages, filtered and ordered in SQL.

    ``sort`` is a key of ``managers.runs.store.RUN_SORTS`` (default ``started``) and
    ``order`` is ``asc`` or ``desc``; the list pages load rows in batches and refetch the whole loaded window
    from the top, so ``limit`` goes up to 2000.

    Returns ``{items, total, limit, offset}``. Filtering used to happen in
    Python over every run ever recorded, which a workspace running a thousand
    agents in parallel turns into a full-table scan on every refresh.

    A request naming no workspace is otherwise open to any signed-in account
    (common/auth.py authorize()); the page is additionally narrowed here to
    runs whose own workspace the caller can see, a no-op outside ``multi``
    mode. Filtered after the SQL page is fetched, so ``total`` still counts
    the unfiltered page — see common/access.py's filter_by_workspace.
    """
    try:
        page = run_manager.query_runs(
            workspace=workspace,
            agent_id=agent_id,
            status=status,
            session_id=session_id,
            instance_id=instance_id,
            channel=channel,
            is_flow=is_flow,
            flow_agent_ids=sorted(FLOW_AGENT_IDS),
            from_date=from_date,
            to_date=to_date,
            q=q,
            limit=max(1, min(int(limit), 2000)),
            offset=max(0, int(offset)),
            sort=sort,
            order=order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    principal = identity.request_principal(request)
    items = access.filter_by_workspace(principal, page["items"])
    return {**page, "items": _enrich_page(items)}


@router.get("/{run_id}")
async def get_message(run_id: str, request: Request):
    """Get details for a single message (agent run)."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")
    _require_run_visible(request, run)

    task_id = run.get("task_id")
    tasks_by_id = tasks_service.get_tasks([task_id]) if task_id else {}
    enriched = _enrich_message(run, tasks_by_id)
    if not enriched.get("model"):
        enriched["model"] = _resolve_session_model(enriched)
    return enriched


@router.get("/{run_id}/logs")
async def get_message_logs(run_id: str, request: Request):
    """Return the log file content for a message."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")
    _require_run_visible(request, run)

    log_file = run.get("log_file")
    if not log_file or not Path(log_file).exists():
        return {"logs": ""}

    try:
        content = Path(log_file).read_text(encoding="utf-8", errors="replace")
        return {"logs": content}
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{run_id}/live")
async def get_message_live(run_id: str, request: Request):
    """What this run has produced so far, for a run that is still going.

    A finished run answers from its record: the log, the payloads, the process
    graph. A running one has none of that yet, and its events are a broadcast
    nobody kept — so opening the page of a run in progress used to show a status
    badge and an empty log until it ended. This returns the live tail held in
    :mod:`common.live_runs`, which the page then continues from the session
    channel.

    ``{"turn": null}`` when nothing live is known: the run is over, or it was
    never one of the kinds that report (see the module docstring there).
    """
    run = run_manager.get_run_by_id(run_id)
    if run is not None:
        _require_run_visible(request, run)
    # Async on purpose: a run executing on another replica has its live tail
    # only in the Redis mirror (common/live_runs.py), which is read awaited.
    return {"turn": await live_runs.by_run_async(run_id)}


_TRAIL_TOOL_CHARS = 2000
_TRAIL_REASONING_CHARS = 8000
_TRAIL_TEXT_CHARS = 20000
_TRAIL_PSEUDO_TOOLS = {"think", "plan"}
_TRAIL_TOOL_KEYS = {"step", "tool", "input", "output", "running", "status",
                    "started_at", "finished_at", "agent_id", "run_id"}


def _trail_clip(value, limit: int):
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit] + "\u2026 (truncated)" if len(text) > limit else text


def trail_tool_status(tool: dict) -> str:
    """Same rule as the frontend: an explicit status wins, then the output."""
    explicit = tool.get("status")
    if explicit:
        return "error" if str(explicit).lower() == "error" else "ok"
    out = tool.get("output")
    if isinstance(out, str):
        head = out.lstrip()
        if head.startswith(("ERROR:", "Error:")):
            return "error"
        if re.match(r'^\{\s*"ok"\s*:\s*false', head):
            return "error"
    return "ok"


def _place_file_steps(timeline: list, files: list) -> None:
    """Each file change as a step of the timeline, the way a live turn has it
    (an ``artifact`` entry, diff included): right after the last tool call
    whose input names the file, else after every step. The log does not say
    which call wrote it, so the path in the call's input is the best tie."""
    for art in files:
        path = art.get("path")
        if not path:
            continue
        entry = {"type": "artifact", **{k: v for k, v in art.items() if k != "run_id"}}
        at = len(timeline)
        for i in range(len(timeline) - 1, -1, -1):
            e = timeline[i]
            if e.get("type") == "tool" and path in str(e.get("input") or ""):
                at = i + 1
                while at < len(timeline) and timeline[at].get("type") == "artifact":
                    at += 1
                break
        timeline.insert(at, entry)


#: A file tool's call in a node run's log (flow/task_driver.py writes
#: ``[tool_start] tool=<name> input=<dict>``), and the change it makes.
_FILE_TOOL_LINE = re.compile(
    r"^\[tool_start\] tool=(write_file|create_file|apply_unified_diff|delete_file) "
    r"input=.*?['\"]path['\"]:\s*['\"]([^'\"]+)['\"]", re.M)
_FILE_TOOL_OP = {"write_file": "modify", "create_file": "add",
                 "apply_unified_diff": "modify", "delete_file": "delete"}


def _node_log_timeline(log_text: str, tool_calls: list) -> list:
    """The steps of a flow node run written before nodes were logged by the
    chat callback (the old NodeFileCallback format: ``[tool_start] tool=…
    input=…`` lines, no step numbers), in the order they happened. The k-th tool line is
    the k-th entry of the run's recorded tool calls, which carry the whole
    input, output and verdict; a line past them keeps what the log shows."""
    timeline: list = []
    calls = list(tool_calls or [])
    used = 0
    step = 0
    for line in log_text.splitlines():
        if line.startswith("[reasoning] content="):
            try:
                content = json.loads(line[len("[reasoning] content="):])
            except ValueError:
                content = line[len("[reasoning] content="):]
            step += 1
            timeline.append({"type": "reasoning", "kind": "think", "step": step,
                             "content": _trail_clip(str(content or ""), _TRAIL_REASONING_CHARS)})
        elif line.startswith("[tool_start] tool="):
            step += 1
            call = calls[used] if used < len(calls) and isinstance(calls[used], dict) else None
            used += 1
            if call is None:
                head = line[len("[tool_start] tool="):]
                name, _, rest = head.partition(" input=")
                call = {"tool": name, "input": rest}
            entry = {"type": "tool", "step": step, "tool": call.get("tool") or "tool",
                     "input": _trail_clip(call.get("input") or "", _TRAIL_TOOL_CHARS),
                     "output": _trail_clip(call.get("output"), _TRAIL_TOOL_CHARS),
                     "status": trail_tool_status(call)}
            for k, v in call.items():
                if k not in _TRAIL_TOOL_KEYS:
                    entry[k] = v
            timeline.append(entry)
    return timeline


def _files_from_tool_lines(log_text: str, run_id: str) -> list:
    """The files a run's file tools wrote, read off its log, for a run that
    recorded no changes of its own (a flow node run from before the task
    driver caught them). No diff: the file is shown as it is now."""
    files: dict = {}
    for tool, path in _FILE_TOOL_LINE.findall(log_text):
        op = _FILE_TOOL_OP[tool]
        prev = files.get(path)
        if prev and prev["op"] == "add" and op == "modify":
            op = "add"
        files[path] = {"op": op, "path": path, "run_id": run_id}
    return list(files.values())


def build_run_trail(run_id: str) -> Optional[dict]:
    """Chronological timeline (reasoning, tools, file steps, final text), file
    changes, step stats and, when it touched any, the service entities of one
    run. None when the run does not exist."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        return None
    log_text = ""
    log_file = run.get("log_file")
    if log_file:
        try:
            p = Path(log_file)
            if p.exists():
                log_text = p.read_text(encoding="utf-8", errors="replace")[-120_000:]
        except Exception:  # noqa: BLE001 - unreadable log falls back to an empty trail
            log.debug("build_run_trail: unreadable log", exc_info=True)
            log_text = ""
    message_runs: list = []
    if log_text:
        try:
            if "=== Message at" not in log_text:
                log_text = "=== Message at worker ===\n" + log_text
            message_runs = _extract_chat_message_runs(log_text)
        except Exception:  # noqa: BLE001
            log.debug("build_run_trail: log parse failed", exc_info=True)
            message_runs = []

    timeline: list = []
    outputs: list = []
    # A flow node run logged before nodes used the chat callback is not a chat
    # log: its steps are read in its old format, with the recorded tool calls.
    node_log = "--- Node run started at" in log_text
    if node_log:
        try:
            timeline = _node_log_timeline(log_text, (run_manager.get_run_process(run_id) or {}).get("tool_calls"))
        except Exception:  # noqa: BLE001 - an unreadable node log falls back to the chat parse
            log.debug("build_run_trail: node log parse failed", exc_info=True)
            timeline = []
        if timeline:
            message_runs = []
    for mr in message_runs:
        entries = []
        for r in (mr.get("reasoning") or []):
            entries.append((int(r.get("step") or 0), 0,
                            {"type": "reasoning", "kind": "think", "step": int(r.get("step") or 0),
                             "content": _trail_clip(r.get("content") or "", _TRAIL_REASONING_CHARS)}))
        for t in (mr.get("tools") or []):
            step = int(t.get("step") or 0)
            entry = {"type": "tool", "step": step, "tool": t.get("tool") or "tool",
                     "input": _trail_clip(t.get("input") or "", _TRAIL_TOOL_CHARS),
                     "output": _trail_clip(t.get("output"), _TRAIL_TOOL_CHARS),
                     "status": trail_tool_status(t)}
            for k, v in t.items():
                if k not in _TRAIL_TOOL_KEYS:
                    entry[k] = v
            entries.append((step, 1, entry))
        entries.sort(key=lambda e: (e[0], e[1]))  # stable: reasoning first at an equal step
        timeline.extend(e[2] for e in entries)
        if mr.get("output"):
            outputs.append(mr["output"])

    files: list = []
    # The service entities the run touched (its views, tasks, files), so a
    # reopened turn links and lists them as the live one did.
    entities: list = []
    try:
        process = run_manager.get_run_process(run_id) or {}
        for art in (process.get("artifacts") or []):
            if isinstance(art, dict):
                files.append({**art, "run_id": run_id})
        entities = [e for e in (run.get("entities") or process.get("entities") or [])
                    if isinstance(e, dict)]
    except Exception:  # noqa: BLE001
        log.debug("build_run_trail: no process data", exc_info=True)
        files, entities = [], []
    if not files and log_text:
        files = _files_from_tool_lines(log_text, run_id)
    _place_file_steps(timeline, files)

    final = run.get("output") or "\n\n".join(outputs)
    if isinstance(final, str) and final.strip():
        timeline.append({"type": "text", "text": _trail_clip(final, _TRAIL_TEXT_CHARS)})

    tools = [e for e in timeline if e["type"] == "tool"]
    stats = {
        "tools": sum(1 for e in tools if e["tool"] not in _TRAIL_PSEUDO_TOOLS),
        "errors": sum(1 for e in tools if e["status"] == "error"),
        "thoughts": sum(1 for e in timeline if e["type"] == "reasoning"),
        "files": len({f.get("path") for f in files if f.get("path")}),
    }
    trail = {"run_id": run_id, "timeline": timeline, "files": files, "stats": stats}
    # What the run was given: a flow node's log line may not carry it.
    if run.get("input"):
        trail["input"] = _trail_clip(str(run["input"]), _TRAIL_TEXT_CHARS)
    if entities:
        trail["entities"] = entities
    return trail


@router.get("/{run_id}/trail")
async def get_message_trail(run_id: str):
    """The run as the chat draws a turn: timeline, file changes, stats."""
    trail = build_run_trail(run_id)
    if trail is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return trail


@router.get("/{run_id}/insights")
async def get_message_insights(run_id: str, request: Request):
    """Return message insights: process graph, tools, thinking trace, token usage."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")
    _require_run_visible(request, run)

    messages = []
    tools = []
    thinking = []
    message_runs = []
    artifacts = []
    inbound_total = 0
    outbound_total = 0
    llm_invoke_responses = []
    input_contexts = []

    # Heavy process payload lives in a per-run sidecar (see run_manager); the slim
    # value on the index record only carries token totals.
    rr_process = run_manager.get_run_process(run_id) or {}

    # File-change artifacts (diffs) captured during chat/flow runs, tagged with
    # the run_id so the UI can rebuild the Artifacts panel on conversation reload.
    for art in (rr_process.get("artifacts") or []):
        if isinstance(art, dict):
            artifacts.append({**art, "run_id": run.get("run_id"), "agent_id": run.get("agent_id")})
    rr_llm = rr_process.get("llm_invoke_responses") or []
    rr_input_context = rr_process.get("llm_input_context")
    if rr_input_context:
        # New runs store a structured object {system_prompt, history,
        # user_message, response, llm_invocations}; older runs stored a plain
        # string. Pass the object through and keep the string under "context"
        # for backward compatibility with the existing UI fallback.
        entry = {
            "run_id": run.get("run_id"),
            "agent_id": run.get("agent_id"),
        }
        if isinstance(rr_input_context, dict):
            entry["structured"] = rr_input_context
            entry["context"] = rr_input_context.get("user_message") or ""
        else:
            entry["context"] = str(rr_input_context)
        input_contexts.append(entry)
    if isinstance(rr_llm, list):
        for entry in rr_llm:
            llm_invoke_responses.append({
                "run_id": run.get("run_id"),
                "agent_id": run.get("agent_id"),
                "payload": entry,
            })

    log_text = ""
    log_file = run.get("log_file")
    if log_file:
        p = Path(log_file)
        if p.exists():
            try:
                log_text = p.read_text(encoding="utf-8", errors="replace")[-120_000:]
            except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
                log.debug("get_message_insights: falling back after a failure", exc_info=True)
                log_text = ""

    if run.get("session_type") == "chat":
        chat_runs = _extract_chat_message_runs(log_text)
        for mr in chat_runs:
            msg_agent_id = mr.get("agent_id") or run.get("agent_id")
            mr["agent_id"] = msg_agent_id
            mr["run_id"] = run.get("run_id")
            message_runs.append(mr)
            if mr.get("input"):
                messages.append({"role": "user", "content": mr.get("input"), "agent_id": msg_agent_id})
            if mr.get("output"):
                messages.append({"role": "assistant", "content": mr.get("output"), "agent_id": msg_agent_id})
            inbound_total += int(mr.get("inbound_tokens") or 0)
            outbound_total += int(mr.get("outbound_tokens") or 0)
        for mr in chat_runs:
            msg_agent_id = mr.get("agent_id") or run.get("agent_id")
            for t in (mr.get("tools") or []):
                t["agent_id"] = t.get("agent_id") or msg_agent_id
                t["run_id"] = run.get("run_id")
                tools.append(t)
        thinking.extend(sum([(mr.get("thinking") or []) for mr in chat_runs], []))
    else:
        # Prefer stored fields; fall back to log parsing for older runs
        stored_input = run.get("input") or ""
        stored_output = run.get("output") or ""
        if stored_input or stored_output:
            in_text, out_text = stored_input, stored_output
        else:
            in_text, out_text = _extract_worker_io(log_text)
        # If output is still empty but log exists, try extracting from log
        # (node runs store input but may not store output in the run record)
        if not out_text and log_text:
            _, log_out = _extract_worker_io(log_text)
            out_text = log_out

        run_tools = _extract_tools_from_progress(run)
        if not run_tools:
            run_tools = _extract_tools_from_log(log_text)
        # Fall back to stored process data (node runs — print() goes to node stdout, not per-run log)
        if not run_tools and rr_process.get("tool_calls"):
            for i, tc in enumerate(rr_process["tool_calls"]):
                run_tools.append({
                    "step": i + 1,
                    "tool": tc.get("tool", "tool"),
                    "input": tc.get("input", ""),
                    "output": tc.get("output", ""),
                    "started_at": None,
                    "finished_at": None,
                    "running": False,
                    **({"evaluated_permission": tc["evaluated_permission"],
                        "reason_code": tc.get("reason_code") or ""}
                       if tc.get("evaluated_permission") else {}),
                })
        # What the tool gate made of each call (tools/permission_policy.py).
        _attach_tool_verdicts(run_tools, log_text, rr_process.get("tool_calls"))
        for t in run_tools:
            t["agent_id"] = run.get("agent_id")
            t["run_id"] = run.get("run_id")
        tools.extend(run_tools)

        run_inbound = run_outbound = run_total = 0
        for u in re.findall(
            r"\[llm_usage\]\s+prompt_tokens=(\d+)\s+completion_tokens=(\d+)\s+total_tokens=(\d+)",
            log_text,
        ):
            run_inbound += int(u[0])
            run_outbound += int(u[1])
            run_total += int(u[2])
        # Fall back to stored token_usage for node runs (log file missing print() output)
        if run_inbound == 0 and run_outbound == 0:
            stored_tu = rr_process.get("token_usage") or {}
            run_inbound = int(stored_tu.get("inbound_tokens") or 0)
            run_outbound = int(stored_tu.get("outbound_tokens") or 0)
            run_total = int(stored_tu.get("total_tokens") or (run_inbound + run_outbound))
        inbound_total += run_inbound
        outbound_total += run_outbound

        # Use stored process thinking for node runs (where print() doesn't reach the log file)
        stored_thinking = list(rr_process.get("thinking") or [])
        # Native model thoughts captured during the run ([reasoning] lines)
        stored_reasoning = [
            r for r in (_parse_reasoning_line(line) for line in stored_thinking) if r
        ]
        if not stored_reasoning and log_text:
            stored_reasoning = [
                r for r in (_parse_reasoning_line(line) for line in log_text.splitlines()) if r
            ]

        pseudo = {
            "message_id": run.get("run_id"),
            "timestamp": run.get("started_at"),
            "agent_id": run.get("agent_id"),
            "run_id": run.get("run_id"),
            "input": in_text,
            "output": out_text,
            "tools": run_tools,
            "thinking": stored_thinking,
            "reasoning": stored_reasoning,
            "inbound_tokens": run_inbound,
            "outbound_tokens": run_outbound,
            "total_tokens": run_total or (run_inbound + run_outbound),
            "tool_calls": len(run_tools),
            "duration_ms": rr_process.get("duration_ms") or 0,
        }
        message_runs.append(pseudo)
        if in_text:
            messages.append({"role": "user", "content": in_text, "agent_id": run.get("agent_id")})
        if out_text:
            messages.append({"role": "assistant", "content": out_text, "agent_id": run.get("agent_id")})

        trace = _build_thinking_trace(run, run_tools, log_text, messages)
        # Merge stored thinking lines not already covered by log-based trace
        if stored_thinking and not any("[llm_start]" in line for line in trace):
            trace = stored_thinking + trace
        thinking.extend(trace)

    if not thinking:
        thinking = ["No process trace captured."]

    token_usage = {
        "inbound_tokens": inbound_total,
        "outbound_tokens": outbound_total,
        "total_tokens": inbound_total + outbound_total,
    }

    model = _resolve_session_model(run)
    # Context fill is measured by the LARGEST single prompt the run sent, not by
    # the sum of its prompts. An agent loop resends the whole conversation on
    # every step, so `inbound_tokens` (30 steps x ~5k here) is a billing figure
    # and would report a window many times "overfull" while the context never
    # came close. Prefer the per-call records, fall back to the log's usage
    # lines, and only then to the total (single-call runs, where they agree).
    peak_prompt = 0
    for inv in (rr_process.get("llm_invocations") or []):
        if isinstance(inv, dict):
            peak_prompt = max(peak_prompt, int((inv.get("token_usage") or {}).get("inbound_tokens") or 0))
    if not peak_prompt and log_text:
        for m in re.findall(r"\[llm_usage\]\s+prompt_tokens=(\d+)", log_text):
            peak_prompt = max(peak_prompt, int(m))
    if not peak_prompt:
        peak_prompt = int(token_usage.get("inbound_tokens") or 0)
    context_window = _build_context_window_metrics(
        model, peak_prompt, _resolve_session_provider(run)
    )

    cost = _run_cost(run, model, _resolve_session_provider(run), token_usage)

    # Structured response (buttons / keyboard) recorded on the unified payload,
    # if the agent emitted one — the response JSON connected to this run.
    response_block = rr_process.get("response") if isinstance(rr_process.get("response"), dict) else None
    structured_response = (response_block or {}).get("structured")

    return {
        "run_id": run_id,
        "session_type": run.get("session_type"),
        "session_id": run.get("session_id"),
        "is_flow": run.get("is_flow", False),
        "model": model,
        "messages": messages,
        "tools": tools,
        "thinking": thinking,
        "message_runs": message_runs,
        "artifacts": artifacts,
        # Links to the service entities this run touched (tasks, views, files, …),
        # resolved when the run finished — see common/entity_links.py. Kept on
        # the run record itself (chat/streaming.py): the payload table has a
        # fixed set of columns and does not store them.
        "entities": list(run.get("entities") or rr_process.get("entities") or []),
        # The sources the answer cites as [n] (common/citation_sink.py), kept
        # on the record the same way.
        "citations": list(run.get("citations") or rr_process.get("citations") or []),
        "aggregated_logs": log_text,
        "llm_invoke_responses": llm_invoke_responses,
        "input_contexts": input_contexts,
        "structured_response": structured_response,
        "token_usage": token_usage,
        "cost": cost,
        "context_window": context_window,
    }


def _run_cost(run: dict, model: str, provider: str, token_usage: dict) -> dict:
    """The run's estimated cost, the same figure the Costs page and budgets
    count (common/costs_report.py ``_run_cost``). ``priced`` is False when the
    catalog has no price for the run's model and it is not a local one, so the
    page can say "no price" instead of a misleading $0. Chat runs parse their
    totals from the log, so those replace the record's slim totals when it has
    none."""
    from common.costs_report import _run_cost as costs_run_cost
    from common.pricing import cost_is_known, load_price_map, run_tokens

    prices = load_price_map()
    priced_run = dict(run)
    if not (priced_run.get("model") or "").strip():
        priced_run["model"] = model
    if not (priced_run.get("provider") or "").strip():
        priced_run["provider"] = provider
    if run_tokens(run) == (0, 0) and token_usage.get("total_tokens"):
        process = dict(run.get("process") or {})
        process["token_usage"] = {**(process.get("token_usage") or {}), **token_usage}
        priced_run["process"] = process
    try:
        usd = costs_run_cost(priced_run, prices)
    except Exception:  # noqa: BLE001 - a cost estimate never breaks the run page
        log.debug("could not price run %s", run.get("run_id"), exc_info=True)
        return {"usd": 0.0, "priced": False}
    return {"usd": round(usd, 6), "priced": cost_is_known(priced_run, prices, usd)}


@router.post("/{run_id}/stop")
async def stop_message(run_id: str, request: Request):
    """Stop a running message (agent run)."""
    from datetime import datetime, timezone
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")
    _require_run_visible(request, run)

    stopped = run_manager.stop_run_by_id(run_id)
    if stopped:
        # For in-process chat runs (no PID), eagerly finalize as "stopped" so the
        # status is never permanently stuck in the transient "stop" signal state.
        updated = run_manager.get_run_by_id(run_id) or {}
        if updated.get("status") == "stop" and not updated.get("pid") and not updated.get("container_name"):
            update_message_run(run_id, {
                "status": "stopped",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "stopped by user",
            })
    return {"stopped": stopped}


@router.delete("/{run_id}")
async def delete_message(run_id: str, request: Request, delete_log: bool = True):
    """Delete a message record. Running messages must be stopped first."""
    run = run_manager.get_run_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Message not found")
    _require_run_visible(request, run)

    if run.get("status") == "running":
        raise HTTPException(status_code=400, detail="Stop the running message before deleting it")

    log_file = run.get("log_file")
    task_id = run.get("task_id")
    # delete_run removes both the run record and its structured payload row.
    run_manager.delete_run(run_id)

    if task_id:
        try:
            from uuid import UUID
            task = tasks_service.get_task(UUID(str(task_id)))
            if task and str(task.assigned_agent_run_id) == run_id:
                tasks_service.update_task(
                    task.id,
                    assigned_agent_run_id=None,
                    assigned_agent_type=None,
                )
        except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
            log.debug("delete_message: best-effort step failed", exc_info=True)

    log_deleted = False
    if delete_log and log_file:
        try:
            p = Path(log_file)
            if p.exists():
                p.unlink()
                log_deleted = True
        except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
            log.debug("delete_message: falling back after a failure", exc_info=True)
            log_deleted = False

    return {"deleted": True, "run_id": run_id, "log_deleted": log_deleted}


# -------------------- Agent version (pin, rollback) --------------------
#
# What agent version a run built from (agents/versions.py), and a one-button
# way to make it the agent's current definition again — the run page's own
# rollback, as opposed to picking a version off the agent's full history
# (dashboard/backend/routes/agents.py already has that one).

def _run_task(run: dict):
    """The run's task, or None (no task_id, or it no longer exists)."""
    task_id = run.get("task_id")
    if not task_id:
        return None
    try:
        from uuid import UUID
        return tasks_service.get_task(UUID(str(task_id)))
    except Exception:  # noqa: BLE001 - best-effort; a bad task_id must not break the run-version read
        return None


def _current_agent_version(agent_id: str) -> Optional[int]:
    """The version number that holds the agent's live definition right now,
    snapshotting it when history does not have it yet — see
    agents.versions.ensure_current_version. None on any lookup failure."""
    from agents import versions as agent_versions
    try:
        fp = agent_versions.definition_fingerprint(agent_id)
        found = agent_versions.version_for_hash(agent_id, fp["hash"])
        return found if found is not None else agent_versions.ensure_current_version(agent_id)
    except Exception:  # noqa: BLE001 - best-effort; a lookup failure just means no current_version in the response
        return None


@runs_router.get("/{run_id}/agent-version")
async def get_run_agent_version(run_id: str, request: Request):
    """What agent and stored version this run built from.

    ``version``/``hash`` are the run's own (``run.agent_version``,
    ``managers.runs.lifecycle``); ``current_version`` is what the agent would
    build today; ``is_current`` compares the two; ``pinned`` is true only when
    the run was asked for this exact version (``run.agent_version_pinned``,
    set by a task, launch, chat turn or service pin) or its task carries it,
    as opposed to the run merely having landed on the version that happens
    to be live.
    """
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    _require_run_visible(request, run)

    agent_id = run.get("agent_id") or None
    version = run.get("agent_version")

    version_hash = run.get("definition_hash")
    if agent_id and version is not None:
        from agents import versions as agent_versions
        row = agent_versions.get_version_row(agent_id, version)
        if row is not None:
            version_hash = row["hash"]

    current_version = _current_agent_version(agent_id) if agent_id else None

    task = _run_task(run)
    pinned = bool(
        version is not None
        and (run.get("agent_version_pinned")
             or (task is not None and getattr(task, "agent_version", None) == version))
    )

    return {
        "agent_id": agent_id,
        "version": version,
        "hash": version_hash,
        "current_version": current_version,
        "is_current": version is not None and current_version is not None and version == current_version,
        "pinned": pinned,
    }


@runs_router.post("/{run_id}/rollback-agent")
async def rollback_run_agent(run_id: str, request: Request):
    """Roll the agent back to the version this run ran.

    Mirrors the checks of the per-agent rollback route
    (``POST /api/agents/{agent_id}/versions/{version}/rollback``,
    dashboard/backend/routes/agents.py): 404 when the agent or the version
    row is gone, 400 when the restored tool set is now refused by the
    capability guard. Recorded in the audit log like any other agent-record
    write.
    """
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    _require_run_visible(request, run)

    agent_id = run.get("agent_id") or None
    version = run.get("agent_version")
    if not agent_id or version is None:
        raise HTTPException(status_code=400, detail="This run has no recorded agent version to roll back to")

    from agents import registry
    from agents import versions as agent_versions
    from agents.capability_guard import CapabilityViolation

    spec = registry.get_agent(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        restored = agent_versions.rollback_to(agent_id, version, actor="dashboard")
    except CapabilityViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    audit.record(
        "agent.rollback",
        principal=identity.request_principal(request),
        object_type="agent", object_id=agent_id,
        ip=identity.client_ip(request),
        details={"version": version, "run_id": run_id},
    )
    return {"agent_id": agent_id, "restored_to": version, "agent": restored}
