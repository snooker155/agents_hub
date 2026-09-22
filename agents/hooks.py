"""
Tool-call hooks and the approval gate that sits on top of them.

Two mechanisms, one wrapper, because they answer the same question at the same
moment: *may this tool call happen?*

* **Hooks** are operator-owned code that runs around every tool call. A
  ``PreToolUse`` hook sees the call before it happens and can deny it; a
  ``PostToolUse`` hook sees the result and can log it (an HTTP one may also
  rewrite the text the agent gets back). They are configured per workspace, so
  an operator can enforce a house rule (no shell in this workspace, every patch
  goes past the linter) without touching an agent record or this repo.
* **The approval gate** holds a call that needs a human yes (see
  tools/approval.py for which, and why the list is what it is).

Configuration lives per workspace, in ``<workspace>/.hooks.json`` or under a
``hooks`` key in the workspace metadata (the metadata wins when both exist, so a
hook set can be managed centrally without a file in the agent's working tree)::

    {
      "PreToolUse": [
        {"matcher": "run_shell|delete_file", "type": "command",
         "command": "./scripts/check_tool_call.py", "timeout": 10},
        {"matcher": "apply_unified_diff", "type": "http",
         "url": "http://localhost:9000/review", "fail_closed": true}
      ],
      "PostToolUse": [
        {"matcher": ".*", "type": "command", "command": "./scripts/audit.sh"}
      ]
    }

A command hook is handed the call as JSON on stdin — ``{hook, agent_id, run_id,
task_id, workspace, tool, input}``, plus ``output`` for ``PostToolUse`` — and
answers with its exit code: 0 allows, 2 denies (its stderr becomes the tool's
output, so the agent reads why), and anything else is logged and allowed.
Failing open is deliberate: a hook that cannot run is an infrastructure problem,
and an unreachable linter must not silently freeze every agent in the
workspace. An operator who would rather stop than proceed sets
``"fail_closed": true`` on that hook.

An HTTP hook gets the same JSON as a POST body and answers with
``{"decision": "allow"|"deny"|"ask", "reason": "..."}``. ``ask`` is the
interesting one: it requires approval for this call even when the tool is not on
the approval list, which is how a hook expresses "this particular command looks
dangerous" rather than a blanket rule about the tool.

Hook processes run with :func:`tools.shell.scrubbed_env`, so a hook cannot read
the backend's provider keys out of its own environment. It is operator code, not
agent code, but it is spawned inside an agent run and gets the same treatment as
anything else an agent run spawns.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"

HOOKS_FILENAME = ".hooks.json"

# A hook that has not answered in this long is treated as not having answered at
# all (allowed, or denied under fail_closed). Per-hook ``timeout`` overrides it.
DEFAULT_HOOK_TIMEOUT = 10


@dataclass
class HookOutcome:
    """What the ``PreToolUse`` hooks decided about one call."""

    decision: str = "allow"          # allow | deny | ask
    reason: str = ""
    hook: str = ""                   # which hook decided, for the audit trail

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    @property
    def asks_approval(self) -> bool:
        return self.decision == "ask"


# -------------------- configuration --------------------

def load_hooks(workspace: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Read the hook configuration for *workspace*.

    The workspace metadata ``hooks`` key wins over the ``.hooks.json`` file, so a
    centrally managed hook set is not silently overridden by a file an agent
    could write into its own workspace. Returns ``{}`` for anything unreadable or
    malformed: a broken hook file must not stop every agent in the workspace.
    """
    from tools.approval import workspace_name
    ws = workspace_name(workspace)
    if not ws:
        return {}

    raw: Any = None
    try:
        from workspace import get_workspace_metadata
        meta_hooks = get_workspace_metadata(ws).get("hooks")
        if isinstance(meta_hooks, dict):
            raw = meta_hooks
    except Exception:
        raw = None

    if raw is None:
        try:
            from workspace import get_workspace_folder
            folder = get_workspace_folder(ws)
            if folder is None:
                return {}
            path = folder / HOOKS_FILENAME
            if not path.is_file():
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - config errors are logged, not fatal
            logger.warning("hooks: unreadable %s for workspace %s: %s", HOOKS_FILENAME, ws, exc)
            return {}

    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event in (PRE_TOOL_USE, POST_TOOL_USE):
        entries = raw.get(event)
        if isinstance(entries, list):
            out[event] = [e for e in entries if isinstance(e, dict)]
    return out


def matches(matcher: Any, tool_id: str) -> bool:
    """True when *matcher* selects *tool_id*.

    The matcher is a regular expression matched whole, so ``run_shell|write_file``
    means those two tools and not every tool whose name contains them. An empty
    matcher (or ``*``) selects every tool. An invalid pattern selects nothing and
    is logged: a typo must not accidentally gate the entire tool set.
    """
    pattern = str(matcher or "").strip()
    if not pattern or pattern == "*":
        return True
    try:
        return re.fullmatch(pattern, str(tool_id or "")) is not None
    except re.error as exc:
        logger.warning("hooks: invalid matcher %r: %s", pattern, exc)
        return False


# -------------------- running one hook --------------------

def _hook_label(hook: Dict[str, Any]) -> str:
    return str(hook.get("command") or hook.get("url") or hook.get("type") or "hook")


def _infrastructure_failure(hook: Dict[str, Any], detail: str) -> HookOutcome:
    """Fail open (log only) unless the hook asked to fail closed."""
    label = _hook_label(hook)
    logger.warning("hooks: %s did not answer (%s)", label, detail)
    if bool(hook.get("fail_closed")):
        return HookOutcome("deny", f"Hook {label} could not run ({detail}) and is configured fail_closed.", label)
    return HookOutcome("allow", "", label)


def _hook_cwd(workspace: Any) -> Optional[str]:
    """The workspace folder a hook command runs in, or None when unresolvable."""
    name = str(workspace or "").strip()
    if not name:
        return None
    try:
        from workspace import get_workspace_folder
        folder = get_workspace_folder(name)
        return str(folder) if folder else None
    except Exception:
        return None


def _run_command_hook(hook: Dict[str, Any], payload: Dict[str, Any]) -> HookOutcome:
    """Run a command hook: JSON on stdin, decision in the exit code."""
    command = str(hook.get("command") or "").strip()
    label = _hook_label(hook)
    if not command:
        return _infrastructure_failure(hook, "no command configured")

    from tools.shell import scrubbed_env
    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=json.dumps(payload, ensure_ascii=False, default=str),
            capture_output=True,
            text=True,
            timeout=float(hook.get("timeout") or DEFAULT_HOOK_TIMEOUT),
            # Provider keys are stripped: a hook is operator code, but it is
            # spawned inside an agent run and gets what an agent's shell gets.
            env=scrubbed_env(),
            # Run the hook in the workspace it belongs to, so a relative command
            # (./scripts/check.sh) means the same thing as it does to the agent.
            cwd=_hook_cwd(payload.get("workspace")),
        )
    except subprocess.TimeoutExpired:
        return _infrastructure_failure(hook, "timed out")
    except Exception as exc:  # noqa: BLE001 - a hook that cannot spawn is infrastructure
        return _infrastructure_failure(hook, f"{type(exc).__name__}: {exc}")

    if proc.returncode == 0:
        return HookOutcome("allow", (proc.stdout or "").strip(), label)
    if proc.returncode == 2:
        reason = (proc.stderr or "").strip() or f"Denied by hook {label}."
        return HookOutcome("deny", reason, label)
    return _infrastructure_failure(hook, f"exit code {proc.returncode}: {(proc.stderr or '').strip()[:200]}")


def _run_http_hook(hook: Dict[str, Any], payload: Dict[str, Any]) -> tuple[HookOutcome, Dict[str, Any]]:
    """POST the call to an HTTP hook. Returns its outcome and its raw JSON body.

    The body is handed back as well because a ``PostToolUse`` hook may rewrite
    the tool output through it, which is not a decision and does not fit in a
    HookOutcome.
    """
    url = str(hook.get("url") or "").strip()
    label = _hook_label(hook)
    if not url:
        return _infrastructure_failure(hook, "no url configured"), {}

    import requests
    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=float(hook.get("timeout") or DEFAULT_HOOK_TIMEOUT),
        )
        if resp.status_code >= 400:
            return _infrastructure_failure(hook, f"HTTP {resp.status_code}"), {}
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - unreachable hook is infrastructure
        return _infrastructure_failure(hook, f"{type(exc).__name__}: {exc}"), {}

    if not isinstance(body, dict):
        return _infrastructure_failure(hook, "response was not a JSON object"), {}

    decision = str(body.get("decision") or "allow").strip().lower()
    if decision not in ("allow", "deny", "ask"):
        return _infrastructure_failure(hook, f"unknown decision {decision!r}"), body
    return HookOutcome(decision, str(body.get("reason") or ""), label), body


# -------------------- the two events --------------------

def _payload(
    event: str,
    tool_id: str,
    tool_input: Any,
    *,
    agent_id: str,
    run_id: str,
    task_id: str,
    workspace: Optional[str],
) -> Dict[str, Any]:
    return {
        "hook": event,
        "agent_id": agent_id or "",
        "run_id": run_id or "",
        "task_id": task_id or "",
        "workspace": workspace or "",
        "tool": tool_id,
        "input": tool_input,
    }


def run_pre_tool_use(
    tool_id: str,
    tool_input: Any,
    *,
    agent_id: str = "",
    run_id: str = "",
    task_id: str = "",
    workspace: Optional[str] = None,
    config: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> HookOutcome:
    """Run every matching ``PreToolUse`` hook. The first deny or ask wins.

    Order is the configured order, so an operator can put the cheap check first.
    A deny stops immediately (nothing else needs to run); an ask keeps looking
    for a deny, because a hard no outranks "have a human look at it".
    """
    hooks = (config if config is not None else load_hooks(workspace)).get(PRE_TOOL_USE) or []
    if not hooks:
        return HookOutcome()

    payload = _payload(PRE_TOOL_USE, tool_id, tool_input, agent_id=agent_id,
                       run_id=run_id, task_id=task_id, workspace=workspace)
    pending_ask: Optional[HookOutcome] = None
    for hook in hooks:
        if not matches(hook.get("matcher"), tool_id):
            continue
        kind = str(hook.get("type") or "command").strip().lower()
        if kind == "http":
            outcome, _body = _run_http_hook(hook, payload)
        else:
            outcome = _run_command_hook(hook, payload)
        if outcome.denied:
            return outcome
        if outcome.asks_approval and pending_ask is None:
            pending_ask = outcome
    return pending_ask or HookOutcome()


def run_post_tool_use(
    tool_id: str,
    tool_input: Any,
    output: Any,
    *,
    agent_id: str = "",
    run_id: str = "",
    task_id: str = "",
    workspace: Optional[str] = None,
    config: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Any:
    """Run every matching ``PostToolUse`` hook. Returns the output to hand back.

    The call has already happened, so nothing here can stop it: a command hook's
    verdict is logged and the output passes through unchanged. An HTTP hook may
    return ``{"output": "..."}`` to replace what the agent reads, which is how a
    redaction or summarisation hook earns its place. Command hooks deliberately
    cannot: their stdout is a log line, and treating it as tool output would make
    every ``echo`` in an audit script silently overwrite a result.
    """
    hooks = (config if config is not None else load_hooks(workspace)).get(POST_TOOL_USE) or []
    if not hooks:
        return output

    payload = _payload(POST_TOOL_USE, tool_id, tool_input, agent_id=agent_id,
                       run_id=run_id, task_id=task_id, workspace=workspace)
    payload["output"] = output if isinstance(output, (str, int, float, bool, type(None))) else str(output)

    current = output
    for hook in hooks:
        if not matches(hook.get("matcher"), tool_id):
            continue
        kind = str(hook.get("type") or "command").strip().lower()
        if kind == "http":
            outcome, body = _run_http_hook(hook, payload)
            if outcome.denied:
                logger.info("hooks: PostToolUse %s objected to %s: %s",
                            outcome.hook, tool_id, outcome.reason)
            rewritten = body.get("output") if isinstance(body, dict) else None
            if isinstance(rewritten, str):
                current = rewritten
                payload["output"] = rewritten
        else:
            outcome = _run_command_hook(hook, payload)
            if outcome.denied:
                logger.info("hooks: PostToolUse %s objected to %s: %s",
                            outcome.hook, tool_id, outcome.reason)
    return current


# -------------------- the gate --------------------

class ToolGuard:
    """Per-build policy shared by every wrapped tool of one agent.

    One instance is created per ``create_agent`` call, so it is scoped to a
    single agent instance the way ``ThinkGate`` is, and never shared between
    concurrent runs in the backend process. It also carries the call the gate
    stopped the run for (:attr:`pending`), because that is how the parked call
    reaches ``invoke_agent``: the executor swallows exceptions raised inside a
    tool into a failed result, so the signal alone cannot be relied on.

    Deliberately a plain class, not a dataclass: it is held as a pydantic field
    on :class:`GuardedTool`, and pydantic revalidates a dataclass into a *copy*.
    Every wrapped tool has to share this one object, or the parked call would be
    recorded on a copy nobody reads.

    A built agent is *cached* and reused across runs (see agents/agent_cache.py),
    so this object outlives the run that stopped. The parked call is therefore
    kept per thread, and ``invoke_agent`` clears it before each run: a run must
    never inherit the call an earlier one stopped on.
    """

    def __init__(self, agent_id: str = "", spec: Any = None, workspace: Optional[str] = None) -> None:
        from tools.approval import workspace_name
        self.agent_id = agent_id
        self.spec = spec
        # Resolved to the bare workspace name once, here: the factory builds an
        # agent with its operating path, and everything downstream (settings,
        # hook config, the hook payload, the hook's cwd) keys on the name.
        self.workspace = workspace_name(workspace) or workspace
        self._pending = threading.local()
        self._config: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._gate_enabled: Optional[bool] = None

    @property
    def pending(self) -> Optional[Dict[str, Any]]:
        """The call this thread's run stopped on, if any."""
        return getattr(self._pending, "call", None)

    @pending.setter
    def pending(self, value: Optional[Dict[str, Any]]) -> None:
        self._pending.call = value

    def hooks(self) -> Dict[str, List[Dict[str, Any]]]:
        """The workspace hook config, read once per agent build and cached.

        Re-reading it per tool call would mean a file read (and a metadata load)
        on every step of every run; a hook change applies to the next run, which
        is the same granularity as every other agent setting.
        """
        if self._config is None:
            self._config = load_hooks(self.workspace)
        return self._config

    def gate_enabled(self) -> bool:
        """Whether this workspace gates tool calls, read once per agent build.

        Cached for the same reason the hook config is: re-reading the workspace
        settings on every step of every run buys nothing, since turning the gate
        on applies to the next run, like every other agent setting.
        """
        if self._gate_enabled is None:
            from tools.approval import approval_gate_enabled
            self._gate_enabled = approval_gate_enabled(self.workspace)
        return self._gate_enabled

    # ---- context ----

    def _task_id(self) -> str:
        """The task this run belongs to, if any. Empty in chat."""
        try:
            from common.agent_context import current_task_id
            tid = current_task_id.get()
            if tid:
                return str(tid)
        except Exception:
            pass
        return str(os.environ.get("AGENT_TASK_ID") or "")

    @staticmethod
    def _run_id() -> str:
        return str(os.environ.get("AGENT_RUN_ID") or "")

    # ---- the two halves of a call ----

    def before(self, tool_id: str, tool_input: Any) -> Optional[str]:
        """Decide what happens to a call. Returns refusal text, or None to run it.

        Raises :class:`agents.callbacks.guards.ApprovalSignal` when the call has
        to wait for a human and there is a task to park it on.
        """
        task_id = self._task_id()
        outcome = run_pre_tool_use(
            tool_id, tool_input,
            agent_id=self.agent_id, run_id=self._run_id(), task_id=task_id,
            workspace=self.workspace, config=self.hooks(),
        )
        if outcome.denied:
            # The hook's own words go back as the tool's output, so the agent
            # learns why and can choose a different route instead of retrying.
            return outcome.reason

        from tools.approval import call_fingerprint, gate_refusal_text, needs_approval
        gated = outcome.asks_approval or (
            self.gate_enabled() and needs_approval(tool_id, self.spec)
        )
        if not gated:
            return None

        reason = outcome.reason or (
            f"`{tool_id}` is on this workspace's approval list."
            if not outcome.asks_approval else ""
        )

        if not task_id:
            # Chat: there is nothing to park and nobody to answer a parked call,
            # so the gate stays advisory, exactly like tools/service_ops.py.
            return gate_refusal_text(tool_id, tool_input, reason)

        fingerprint = call_fingerprint(tool_id, tool_input)
        try:
            from tasks.service import consume_approved_call
            if consume_approved_call(task_id, fingerprint):
                return None  # the operator already said yes to this exact call
        except Exception:
            pass

        pending = {
            "tool": tool_id,
            "input": tool_input,
            "reason": reason,
            "run_id": self._run_id(),
            "agent_id": self.agent_id,
            "hook": outcome.hook,
            "fingerprint": fingerprint,
        }
        self.pending = pending
        from agents.callbacks.guards import ApprovalSignal
        raise ApprovalSignal(pending)

    def after(self, tool_id: str, tool_input: Any, output: Any) -> Any:
        return run_post_tool_use(
            tool_id, tool_input, output,
            agent_id=self.agent_id, run_id=self._run_id(), task_id=self._task_id(),
            workspace=self.workspace, config=self.hooks(),
        )


def _merge_tool_input(args: tuple, kwargs: dict) -> Any:
    """Reconstruct the tool input from the ``_run`` call shape.

    Same reconstruction as ``reasoning.think_gate``: LangChain hands a wrapper
    the arguments it parsed, and the inner tool wants the original dict/value.
    """
    if kwargs:
        return dict(kwargs)
    if len(args) == 1:
        return args[0]
    return list(args)


class GuardedTool(BaseTool):
    """Wraps a tool so hooks run around it and gated calls wait for a human."""

    # pydantic model fields
    inner: BaseTool
    guard: ToolGuard

    # Mirror the wrapped tool's public surface so the LLM sees no difference.
    name: str = ""
    description: str = ""
    args_schema: Optional[Type[BaseModel]] = None

    def __init__(self, inner: BaseTool, guard: ToolGuard, **kwargs: Any) -> None:
        super().__init__(
            inner=inner,
            guard=guard,
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            **kwargs,
        )

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        # Strip the run manager LangChain injects; the inner tool manages its own.
        kwargs.pop("run_manager", None)
        tool_input = _merge_tool_input(args, kwargs)
        refusal = self.guard.before(self.name, tool_input)
        if refusal is not None:
            return refusal
        return self.guard.after(self.name, tool_input, self.inner.run(tool_input))

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.pop("run_manager", None)
        tool_input = _merge_tool_input(args, kwargs)
        refusal = self.guard.before(self.name, tool_input)
        if refusal is not None:
            return refusal
        return self.guard.after(self.name, tool_input, await self.inner.arun(tool_input))


def guard_action_tools(
    tools: list,
    *,
    agent_id: str = "",
    spec: Any = None,
    workspace: Optional[str] = None,
) -> list:
    """Wrap every action tool so hooks and the approval gate apply to it.

    Reasoning tools (and ``ask_user``, the agent's own way to reach the human)
    are left alone: they have no effect outside the run, and gating the question
    tool would need approval to ask for approval. Returns the list unchanged when
    the workspace has neither hooks nor the gate turned on, so an installation
    that uses neither pays nothing but one config read per agent build.
    """
    from tools.approval import NEVER_GATED

    guard = ToolGuard(agent_id=agent_id, spec=spec, workspace=workspace)
    if not guard.hooks() and not guard.gate_enabled():
        return tools

    wrapped = []
    for t in tools:
        name = getattr(t, "name", "")
        if name in NEVER_GATED or not isinstance(t, BaseTool):
            wrapped.append(t)
        else:
            wrapped.append(GuardedTool(t, guard))
    return wrapped


def _guards_of(agent: Any):
    """The tool guards an agent was built with (empty for an unwrapped agent).

    Looks through one wrapping layer: ``agent_factory`` builds each action
    tool as ``GatedTool(GuardedTool(tool))`` (the think-gate outermost, see
    the comment in ``agent_factory._build_agent``), so the object in the
    agent's tool list carries a ``.gate`` and an ``.inner`` (the reasoning
    module's attribute name for the wrapped tool) rather than a ``.guard``
    directly. Unwrap via ``inner`` when the outer object has none, so a
    parked approval is still found through the gate the same way it was found
    directly before that reordering.
    """
    for tool in (getattr(agent, "_tools", None) or getattr(agent, "tools", None) or []):
        guard = getattr(tool, "guard", None)
        if guard is None:
            guard = getattr(getattr(tool, "inner", None), "guard", None)
        if isinstance(guard, ToolGuard):
            yield guard


def clear_pending_approval(agent: Any) -> None:
    """Forget any call a previous run of this (cached) agent stopped on."""
    for guard in _guards_of(agent):
        guard.pending = None


def pending_approval_for(agent: Any) -> Optional[Dict[str, Any]]:
    """The call an agent's gate stopped its run for, if any.

    Read off the agent's tools rather than a module-level global, so two runs in
    the same backend process can never see each other's parked call.
    """
    for guard in _guards_of(agent):
        if guard.pending:
            return dict(guard.pending)
    return None


__all__ = [
    "DEFAULT_HOOK_TIMEOUT",
    "GuardedTool",
    "clear_pending_approval",
    "HOOKS_FILENAME",
    "HookOutcome",
    "POST_TOOL_USE",
    "PRE_TOOL_USE",
    "ToolGuard",
    "guard_action_tools",
    "load_hooks",
    "matches",
    "pending_approval_for",
    "run_post_tool_use",
    "run_pre_tool_use",
]
