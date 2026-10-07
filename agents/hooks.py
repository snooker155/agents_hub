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
  ``before_tool_call`` and ``after_tool_call`` are accepted as the same two
  events under the names other agent platforms use.
* **Run hooks** wrap a whole run the same way: ``before_run`` sees the agent,
  the input and the model before the first model call and can deny the run;
  ``after_run`` sees the final text, status, usage and cost, and may replace
  the text but not the outcome (:class:`RunHooks`, called by
  agents/standard_agent.py).
* **The approval gate** holds a call that needs a human yes (see
  tools/approval.py for which, and why the list is what it is).
* **The tool policy** (tools/permission_policy.py) sets a mode per tool, per
  agent or per workspace: ``always_allow``, ``always_ask``, or ``auto``, where a
  small model decides run, deny or ask. It is applied after the hooks (a hook
  deny or ask still wins) and, when nothing sets a mode, falls back to the
  approval gate above, so a workspace without a policy behaves as before.

Configuration lives per workspace, under the ``hooks`` key of the workspace
metadata, which only the workspace's owner sets. A ``<workspace>/.hooks.json``
file is never run: agents write into that folder (see :func:`load_hooks`)::

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
task_id, workspace, tool, input}``, plus ``output`` for ``PostToolUse``; a run
hook gets the run instead of a tool call (see :func:`run_before_run`) — and
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
BEFORE_RUN = "before_run"
AFTER_RUN = "after_run"

#: Every event a hook can be configured for, in the order they are stored.
EVENTS = (PRE_TOOL_USE, POST_TOOL_USE, BEFORE_RUN, AFTER_RUN)

#: Other names for the tool events, the ones other agent platforms use. A
#: config may use either spelling (or both); the entries run as one list,
#: canonical name first.
EVENT_ALIASES = {"before_tool_call": PRE_TOOL_USE, "after_tool_call": POST_TOOL_USE}

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
    """Read the hook configuration for *workspace*: the ``hooks`` key of the
    workspace metadata, which only the workspace's owner can set (the Settings
    tool policy block, ``PUT /api/workspaces/{name}/policy``).

    A ``.hooks.json`` file in the workspace folder is never read. Agents write
    into that folder, and a hook from it would run a command on the hub's host
    or post a call to any address on the agent's say so. A file left from
    before is reported (:func:`ignored_hooks_file`) and can be imported by the
    owner (``POST /api/workspaces/{name}/policy/import-hooks-file``).

    In an isolated workspace (common/isolation.py) an ``http`` hook never
    runs either: it sends the call out. Returns ``{}`` for anything unreadable
    or malformed: a broken config must not stop every agent in the workspace.
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
    if not isinstance(raw, dict):
        if ignored_hooks_file(ws) is not None:
            _warn_ignored_file(ws)
        return {}

    events = normalize_events(raw)
    try:
        from common import isolation
        isolated = isolation.is_isolated(ws)
    except Exception:  # noqa: BLE001 - an unreadable workspace is treated as isolated
        isolated = True
    if isolated:
        return {event: [h for h in entries
                        if str(h.get("type") or "command").strip().lower() != "http"]
                for event, entries in events.items()}
    return events


_WARNED_FILES: set = set()


def _warn_ignored_file(ws: str) -> None:
    if ws in _WARNED_FILES:
        return
    _WARNED_FILES.add(ws)
    logger.warning("hooks: %s in workspace %s is ignored; the owner can import it from Settings, "
                   "tool policy", HOOKS_FILENAME, ws)


def ignored_hooks_file(workspace: Optional[str]) -> Optional[Dict[str, Any]]:
    """The parsed ``.hooks.json`` of *workspace* when one is there (and is a
    JSON object), else None. Only ever shown or imported by a person, never run."""
    try:
        from workspace import get_workspace_folder
        folder = get_workspace_folder(str(workspace or ""))
        if folder is None:
            return None
        path = folder / HOOKS_FILENAME
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - an unreadable file is not one to import
        return None
    return raw if isinstance(raw, dict) else None


def normalize_events(raw: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """The config keyed by canonical event names, aliases folded in.

    An alias's entries run after the canonical name's, in their configured
    order. Unknown keys and entries that are not objects are dropped (the
    Settings route refuses them before they are stored)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event in EVENTS:
        names = [event, *[alias for alias, target in EVENT_ALIASES.items() if target == event]]
        for name in names:
            entries = raw.get(name)
            if isinstance(entries, list):
                out.setdefault(event, []).extend(e for e in entries if isinstance(e, dict))
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


# -------------------- the run events --------------------

def _current_task_id() -> str:
    """The task the current run belongs to, if any. Empty in chat."""
    try:
        from common.agent_context import current_task_id
        tid = current_task_id.get()
        if tid:
            return str(tid)
    except Exception:  # noqa: BLE001 - no agent context module: the env still names it
        pass
    return str(os.environ.get("AGENT_TASK_ID") or "")


def _run_payload(event: str, *, agent_id: str, run_id: str, task_id: str,
                 workspace: Optional[str], **fields: Any) -> Dict[str, Any]:
    return {"hook": event, "agent_id": agent_id or "", "run_id": run_id or "",
            "task_id": task_id or "", "workspace": workspace or "", **fields}


def run_before_run(
    *,
    agent_id: str = "",
    run_id: str = "",
    task_id: str = "",
    workspace: Optional[str] = None,
    input_text: str = "",
    model: str = "",
    provider: str = "",
    config: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> HookOutcome:
    """Run every matching ``before_run`` hook. The first deny wins.

    The payload is ``{hook, agent_id, run_id, task_id, workspace, input,
    model, provider}``; the matcher is matched against the agent id. ``ask``
    has no meaning here (there is no call to hold, and a run cannot wait for
    a person before it exists), so it is read as a deny: a hook that wanted a
    person to look did not want the run to go ahead unseen.
    """
    hooks = (config if config is not None else load_hooks(workspace)).get(BEFORE_RUN) or []
    if not hooks:
        return HookOutcome()
    payload = _run_payload(BEFORE_RUN, agent_id=agent_id, run_id=run_id, task_id=task_id,
                           workspace=workspace, input=input_text, model=model or "",
                           provider=provider or "")
    for hook in hooks:
        if not matches(hook.get("matcher"), agent_id):
            continue
        kind = str(hook.get("type") or "command").strip().lower()
        if kind == "http":
            outcome, _body = _run_http_hook(hook, payload)
        else:
            outcome = _run_command_hook(hook, payload)
        if outcome.asks_approval:
            outcome = HookOutcome("deny", outcome.reason or f"Hook {outcome.hook} asked for a person "
                                  "before this run, which a run cannot wait for.", outcome.hook)
        if outcome.denied:
            return outcome
    return HookOutcome()


def run_after_run(
    output: str,
    *,
    agent_id: str = "",
    run_id: str = "",
    task_id: str = "",
    workspace: Optional[str] = None,
    status: str = "",
    ok: bool = True,
    error: str = "",
    usage: Optional[Dict[str, Any]] = None,
    cost_usd: Optional[float] = None,
    model: str = "",
    provider: str = "",
    config: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> str:
    """Run every matching ``after_run`` hook. Returns the final text to keep.

    The run is over, so nothing here changes its outcome: a deny is logged.
    An HTTP hook may return ``{"output": "..."}`` to replace the final text,
    the same rule as ``PostToolUse`` and for the same reason a command hook
    cannot (its stdout is a log line, not an answer).
    """
    hooks = (config if config is not None else load_hooks(workspace)).get(AFTER_RUN) or []
    if not hooks:
        return output
    payload = _run_payload(AFTER_RUN, agent_id=agent_id, run_id=run_id, task_id=task_id,
                           workspace=workspace, output=output, status=status or "",
                           ok=bool(ok), error=error or "", usage=dict(usage or {}),
                           cost_usd=cost_usd, model=model or "", provider=provider or "")
    current = output
    for hook in hooks:
        if not matches(hook.get("matcher"), agent_id):
            continue
        kind = str(hook.get("type") or "command").strip().lower()
        if kind == "http":
            outcome, body = _run_http_hook(hook, payload)
            rewritten = body.get("output") if isinstance(body, dict) else None
            if isinstance(rewritten, str):
                current = rewritten
                payload["output"] = rewritten
        else:
            outcome = _run_command_hook(hook, payload)
        if outcome.denied:
            logger.info("hooks: after_run %s objected to run %s: %s", outcome.hook, run_id, outcome.reason)
    return current


class RunHooks:
    """``before_run`` and ``after_run`` around one run of an agent.

    Built per run (:meth:`for_agent`), with the config read once, so the two
    halves see the same hook set even if an operator edits it mid-run. Never
    raises: a hook that cannot run is handled by the hook's own
    ``fail_closed``, and anything else going wrong here leaves the run as it
    would be without hooks.
    """

    def __init__(self, agent: Any, kwargs: Dict[str, Any], config: Dict[str, List[Dict[str, Any]]]) -> None:
        self.agent = agent
        self.kwargs = kwargs
        self.config = config

    @classmethod
    def for_agent(cls, agent: Any, kwargs: Dict[str, Any]) -> "RunHooks":
        from tools.approval import workspace_name
        workspace = kwargs.get("workspace") or getattr(agent, "workspace", None)
        try:
            config = load_hooks(workspace_name(workspace) or workspace)
        except Exception:  # noqa: BLE001 - an unreadable config is no config, like load_hooks itself
            config = {}
        return cls(agent, kwargs, config)

    @property
    def active(self) -> bool:
        return bool(self.config.get(BEFORE_RUN) or self.config.get(AFTER_RUN))

    def _ids(self) -> Dict[str, Any]:
        from tools.approval import workspace_name
        workspace = self.kwargs.get("workspace") or getattr(self.agent, "workspace", None)
        return {
            "agent_id": str(getattr(self.agent, "agent_id", "") or ""),
            "run_id": str(self.kwargs.get("run_id") or os.environ.get("AGENT_RUN_ID") or ""),
            "task_id": _current_task_id(),
            "workspace": workspace_name(workspace) or workspace,
        }

    def before(self, instruction: str) -> Optional[Any]:
        """The result that ends the run when a hook denied it, else None."""
        if not self.config.get(BEFORE_RUN):
            return None
        try:
            outcome = run_before_run(
                input_text=str(instruction or ""), model=str(getattr(self.agent, "model", "") or ""),
                provider=str(getattr(self.agent, "provider", "") or ""), config=self.config,
                **self._ids())
        except Exception:  # noqa: BLE001 - see class docstring
            logger.warning("hooks: before_run failed", exc_info=True)
            return None
        if not outcome.denied:
            return None
        from agents.agent_base import AgentResult
        reason = outcome.reason or f"Denied by hook {outcome.hook}."
        return AgentResult(ok=False, status="error", error=f"A before_run hook stopped the run: {reason}",
                           loop={"hook_denied": {"event": BEFORE_RUN, "hook": outcome.hook,
                                                 "reason": reason}})

    def _usage(self) -> Dict[str, Any]:
        """Tokens of the run, read off whichever stats callback it carried."""
        for cb in self.kwargs.get("callbacks") or []:
            if hasattr(cb, "prompt_tokens") and hasattr(cb, "completion_tokens"):
                return {
                    "prompt_tokens": int(getattr(cb, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(cb, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(cb, "total_tokens", 0) or 0),
                    "cached_tokens": int(getattr(cb, "cached_prompt_tokens", 0) or 0),
                }
        return {}

    def after(self, result: Any) -> Any:
        """``result`` with the final text an ``after_run`` hook gave, if any."""
        if not self.config.get(AFTER_RUN):
            return result
        try:
            usage = self._usage()
            provider = str(getattr(self.agent, "provider", "") or "")
            model = str(getattr(self.agent, "model", "") or "")
            cost = None
            if usage:
                from common.pricing import serving_cost_usd
                cost = serving_cost_usd(provider, model, usage.get("prompt_tokens", 0),
                                        usage.get("completion_tokens", 0))
            output = str(getattr(result, "agent_output", "") or "")
            new = run_after_run(
                output, status=str(getattr(result, "status", "") or ""),
                ok=bool(getattr(result, "ok", False)), error=str(getattr(result, "error", "") or ""),
                usage=usage, cost_usd=cost, model=model, provider=provider, config=self.config,
                **self._ids())
        except Exception:  # noqa: BLE001 - see class docstring
            logger.warning("hooks: after_run failed", exc_info=True)
            return result
        if new != output:
            try:
                result.agent_output = new
            except Exception:  # noqa: BLE001 - a result that will not take the text keeps its own
                logger.debug("hooks: after_run output not applied", exc_info=True)
        return result


# -------------------- the gate --------------------

@dataclass
class _Call:
    """One tool call as the guard sees it while deciding."""

    tool: str
    input: Any
    fingerprint: str
    mode: str
    source: str
    task_id: str
    run_id: str


@dataclass
class _Verdict:
    """What :meth:`ToolGuard.evaluate` decided: run (both None), refuse with
    ``refusal`` as the tool's output, or park on ``pending``."""

    refusal: Optional[str] = None
    pending: Optional[Dict[str, Any]] = None


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
        self._settings: Optional[Dict[str, Any]] = None

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

    def settings(self) -> Dict[str, Any]:
        """The workspace settings block, read once per agent build.

        The tool policy (``settings.tool_policy``) and its classifier model
        (``settings.tool_policy_model``) live there; cached for the same reason
        as the gate flag.
        """
        if self._settings is None:
            from tools.permission_policy import workspace_settings
            self._settings = workspace_settings(self.workspace)
        return self._settings

    def has_policy(self) -> bool:
        """True when the agent or the workspace sets any tool policy mode."""
        from tools.permission_policy import has_policy
        return has_policy(self.spec, self.settings())

    def resolve(self, tool_id: str) -> tuple[str, str]:
        """``(mode, source)`` for *tool_id* (tools/permission_policy.py)."""
        from tools.permission_policy import resolve_mode
        return resolve_mode(tool_id, self.spec, self.workspace,
                            settings=self.settings(), gate_enabled=self.gate_enabled())

    # ---- context ----

    def _task_id(self) -> str:
        """The task this run belongs to, if any. Empty in chat."""
        return _current_task_id()

    @staticmethod
    def _run_id() -> str:
        """The run this call belongs to: the loop's own record of it first
        (a chat run in the backend has no ``AGENT_RUN_ID``), then the env."""
        try:
            from agents.agent_loop import current_state
            state = current_state()
            if state is not None and state.run_id:
                return str(state.run_id)
        except Exception:  # noqa: BLE001 - no loop state is a plain run, the env still names it
            logger.debug("hooks: no loop state for the run id", exc_info=True)
        return str(os.environ.get("AGENT_RUN_ID") or "")

    # ---- the two halves of a call ----

    def before(self, tool_id: str, tool_input: Any, description: str = "") -> Optional[str]:
        """Decide what happens to a call. Returns refusal text, or None to run it.

        Raises :class:`agents.callbacks.guards.ApprovalSignal` when the call has
        to wait for a human and there is a task to park it on.
        """
        return self._apply(self.evaluate(tool_id, tool_input, description))

    async def abefore(self, tool_id: str, tool_input: Any, description: str = "") -> Optional[str]:
        """:meth:`before` for an async run.

        The deciding half (hook processes, the ``auto`` classifier's model call)
        runs in a worker thread so it does not stall the event loop; the parked
        call is recorded back on this thread, because :attr:`pending` is kept
        per thread and ``invoke_agent`` reads it from the run's own.
        """
        import asyncio
        verdict = await asyncio.to_thread(self.evaluate, tool_id, tool_input, description)
        return self._apply(verdict)

    def _apply(self, verdict: "_Verdict") -> Optional[str]:
        if verdict.pending is not None:
            self.pending = verdict.pending
            from agents.callbacks.guards import ApprovalSignal
            raise ApprovalSignal(verdict.pending)
        return verdict.refusal

    def evaluate(self, tool_id: str, tool_input: Any, description: str = "") -> "_Verdict":
        """What should happen to one call, without acting on it.

        Order: a ``PreToolUse`` hook that denies or asks wins outright (it is the
        workspace's rule about this very call); then the tool policy mode
        (tools/permission_policy.py): ``always_allow`` runs, ``always_ask``
        waits for a person, ``auto`` asks the classifier. With no policy set
        anywhere the mode is the legacy one, so the approval list behaves
        exactly as it did before modes existed.
        """
        from tools import permission_policy as policy
        from tools.approval import call_fingerprint, needs_approval, policy_denied_text

        task_id = self._task_id()
        run_id = self._run_id()
        outcome = run_pre_tool_use(
            tool_id, tool_input,
            agent_id=self.agent_id, run_id=run_id, task_id=task_id,
            workspace=self.workspace, config=self.hooks(),
        )
        mode, source = self.resolve(tool_id)
        call = _Call(tool=tool_id, input=tool_input, fingerprint=call_fingerprint(tool_id, tool_input),
                     mode=mode, source=source, task_id=task_id, run_id=run_id)

        if outcome.denied:
            # The hook's own words go back as the tool's output, so the agent
            # learns why and can choose a different route instead of retrying.
            self._record(call, policy.DENY, outcome.reason, by="hook")
            return _Verdict(refusal=outcome.reason)
        if outcome.asks_approval:
            return self._hold(call, outcome.reason, by="hook", hook=outcome.hook)

        sequence = self._sequence(call)
        if sequence is not None:
            return sequence

        if mode == policy.ALWAYS_ALLOW:
            # An operator's explicit always_allow lifts the tool off the approval
            # list. Worth a row: it is the call an auditor will ask about.
            if (source in policy.EXPLICIT_SOURCES and self.gate_enabled()
                    and needs_approval(tool_id, self.spec)):
                self._record(call, policy.RUN,
                             "always_allow overrides the approval list for this tool.", by="policy")
            else:
                # A plain allow: no row anywhere, only the call's own trail entry.
                policy.note_call(call.tool, policy.PERMISSION_ALLOW,
                                 policy.reason_code(by="policy", decision=policy.RUN,
                                                    mode=mode, source=source),
                                 fingerprint=call.fingerprint)
            return _Verdict()

        if mode == policy.AUTO:
            return self._auto(call, description, policy_denied_text)

        # always_ask, from the policy or from the approval list. A tool that
        # can say what this call would do puts that sentence on the card.
        from tools.approval import describe_call
        reason = describe_call(tool_id, call.input) or (
            f"`{tool_id}` is on this workspace's approval list."
            if source == policy.SOURCE_APPROVAL_LIST
            else f"The tool policy asks a person before `{tool_id}` runs."
        )
        return self._hold(call, reason, by="policy")

    def _auto(self, call: "_Call", description: str, denied_text: Any) -> "_Verdict":
        """Let the classifier decide, once per distinct call per run."""
        from tools import permission_policy as policy

        # A person already said yes to this exact call (a parked auto call,
        # approved): that settles it, whatever the classifier would say now.
        if call.task_id and self._consume(call):
            self._record(call, policy.RUN, "A person approved this exact call.", by="auto",
                         approved=True)
            return _Verdict()

        cached = policy.cached_decision(call.fingerprint)
        if cached is not None:
            decision, reason = cached
        else:
            title, task_text = policy.task_context(call.task_id)
            decision, reason = policy.classify(
                tool_id=call.tool, tool_input=call.input, tool_description=description,
                agent_id=self.agent_id, agent_spec=self.spec, workspace=self.workspace,
                task_title=title, task_description=task_text, settings=self.settings(),
            )
            policy.remember_decision(call.fingerprint, decision, reason)

        self._record(call, decision, reason, by="auto", cached=cached is not None)
        if decision == policy.RUN:
            return _Verdict()
        if decision == policy.DENY:
            return _Verdict(refusal=denied_text(call.tool, call.input, reason))
        return self._hold(call, reason, by="auto", record=False, check_approved=False)

    def _hold(self, call: "_Call", reason: str, *, by: str, hook: str = "",
              record: bool = True, check_approved: bool = True) -> "_Verdict":
        """A call that needs a person: park it in a task, wait for an answer in
        a dashboard chat turn, refuse it in any other chat."""
        from tools import permission_policy as policy
        from tools.approval import gate_refusal_text

        if not call.task_id:
            if record:
                self._record(call, policy.ASK, reason, by=by)
            held = self._hold_in_chat(call, reason, by=by, hook=hook)
            if held is not None:
                return held
            # Nobody in front of a card (Telegram, the widget, a channel,
            # /v1): there is nothing to park and nobody to answer a parked
            # call, so the gate stays advisory, exactly like tools/service_ops.py.
            return _Verdict(refusal=gate_refusal_text(call.tool, call.input, reason))

        if check_approved and self._consume(call):
            # The operator already said yes to this exact call.
            if record:
                self._record(call, policy.RUN, "A person approved this exact call.", by=by,
                             approved=True)
            return _Verdict()

        if record:
            self._record(call, policy.ASK, reason, by=by)
        return _Verdict(pending={
            "tool": call.tool,
            "input": call.input,
            "reason": reason,
            "run_id": call.run_id,
            "agent_id": self.agent_id,
            "hook": hook,
            "fingerprint": call.fingerprint,
            "mode": call.mode,
            "by": by,
        })

    def _hold_in_chat(self, call: "_Call", reason: str, *, by: str, hook: str = "") -> Optional["_Verdict"]:
        """Wait in a dashboard chat turn for a person to answer this call
        (common/tool_approvals.py). None when the turn cannot hold it.

        Blocks this thread: for an async run the deciding half already runs
        in a worker thread (:meth:`abefore`), so the event loop, the stream
        and the Stop button keep working while the person reads the card.
        The answer goes on the policy trail and in the audit log like every
        other decided call: ``human_approved`` for a yes, ``human_denied``
        for a no, a timeout or a stop.
        """
        from tools import permission_policy as policy
        from tools.approval import chat_answer_text
        try:
            from common import tool_approvals
            ctx = tool_approvals.chat_context(call.run_id)
            if ctx is None:
                return None
            timeout = tool_approvals.timeout_seconds(self.settings())
            answer = tool_approvals.hold(
                ctx, tool=call.tool, tool_input=call.input, reason=reason,
                fingerprint=call.fingerprint, by=by, hook=hook, agent_id=self.agent_id,
                workspace=self.workspace, timeout_s=timeout)
        except Exception:  # noqa: BLE001 - a broken wait falls back to the advisory refusal, never to running
            logger.warning("tool approvals: could not hold %s in chat", call.tool, exc_info=True)
            return None
        if answer is None:
            return None
        note = str(answer.get("note") or "").strip()
        if answer.get("status") == tool_approvals.STATUS_APPROVED:
            self._record(call, policy.RUN, "A person approved this call in the chat."
                         + (f" Note: {note}" if note else ""), by=by, approved=True, trail=False)
            _revise_trail(call, policy.PERMISSION_ALLOW, "human_approved")
            return _Verdict()
        why = {
            tool_approvals.STATUS_DENIED: "A person denied this call in the chat.",
            tool_approvals.STATUS_CANCELLED: "The run was stopped while the call waited for a person.",
        }.get(str(answer.get("status")), f"Nobody answered within {int(timeout)} seconds.")
        self._record(call, policy.DENY, why + (f" Note: {note}" if note else ""), by=by,
                     code="human_denied", trail=False)
        _revise_trail(call, policy.PERMISSION_DENY, "human_denied")
        return _Verdict(refusal=chat_answer_text(call.tool, call.input, answer, timeout))

    @staticmethod
    def _consume(call: "_Call") -> bool:
        """Spend the operator's approval of this exact call, if there is one."""
        try:
            from tasks.service import consume_approved_call
            return bool(consume_approved_call(call.task_id, call.fingerprint))
        except Exception:  # noqa: BLE001 - an unreadable task means no approval to spend, so the call waits
            return False

    def _record(self, call: "_Call", decision: str, reason: str, *, by: str,
                cached: bool = False, approved: bool = False, code: str = "",
                trail: bool = True) -> Any:
        """Record one decided call: on the run and in the Tool policy store,
        on the call's trail entry (``evaluated_permission`` and ``reason_code``),
        and as an audit row unless it is a cached repeat. Every call that gets
        here is a deny, an ask, an ``auto`` decision, a spent approval or an
        always_allow that lifted the approval list; a plain allow never does."""
        from tools import permission_policy as policy
        entry = policy.Decision(tool=call.tool, mode=call.mode, decision=decision, reason=reason or "",
                                by=by, fingerprint=call.fingerprint, source=call.source, cached=cached)
        policy.record(entry, agent_id=self.agent_id, run_id=call.run_id, task_id=call.task_id,
                      workspace=self.workspace, tool_input=call.input)
        code = code or policy.reason_code(by=by, decision=decision, mode=call.mode, source=call.source,
                                          reason=reason, approved=approved)
        if trail:
            policy.note_call(call.tool, policy.permission_of(decision), code, fingerprint=call.fingerprint)
        if not cached:
            policy.audit_decision(entry, agent_id=self.agent_id, run_id=call.run_id,
                                  task_id=call.task_id, workspace=self.workspace, code=code)
        return entry

    def _sequence(self, call: "_Call") -> Optional["_Verdict"]:
        """The workspace's sequence guardrails on this call (guardrails/sequence.py):
        a refusal, a hold for a person, or None when every rule lets it through."""
        from guardrails import sequence
        try:
            decision = sequence.check_call(self, call.tool, call.input, run_id=call.run_id,
                                           task_id=call.task_id)
        except Exception:  # noqa: BLE001 - a broken rule store must not stop every tool call
            logger.warning("sequence guardrails: check failed for %s", call.tool, exc_info=True)
            return None
        if not decision:
            return None
        from tools import permission_policy as policy
        if decision.get("action") == "ask":
            return self._hold(call, str(decision.get("reason") or ""), by="guardrail")
        self._record(call, policy.DENY, str(decision.get("reason") or ""), by="guardrail")
        return _Verdict(refusal=str(decision.get("reason") or ""))

    def after(self, tool_id: str, tool_input: Any, output: Any) -> Any:
        try:
            from guardrails import sequence
            sequence.note_call(self, tool_id, tool_input, output, run_id=self._run_id(),
                               task_id=self._task_id())
        except Exception:  # noqa: BLE001 - bookkeeping for later checks, never the call's own result
            logger.debug("sequence guardrails: note failed for %s", tool_id, exc_info=True)
        return run_post_tool_use(
            tool_id, tool_input, output,
            agent_id=self.agent_id, run_id=self._run_id(), task_id=self._task_id(),
            workspace=self.workspace, config=self.hooks(),
        )


def _revise_trail(call: "_Call", permission: str, code: str) -> None:
    """Turn the ``ask`` the trail holds for a call answered in chat into the
    answer (tools.permission_policy.revise_call)."""
    from tools import permission_policy as policy
    policy.revise_call(call.tool, permission, code, fingerprint=call.fingerprint)


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
        refusal = self.guard.before(self.name, tool_input, self.description)
        if refusal is not None:
            return refusal
        return self.guard.after(self.name, tool_input, self.inner.run(tool_input))

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.pop("run_manager", None)
        tool_input = _merge_tool_input(args, kwargs)
        refusal = await self.guard.abefore(self.name, tool_input, self.description)
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
    """Wrap every action tool so hooks, the approval gate and the tool policy
    apply to it.

    Reasoning tools (and ``ask_user``, the agent's own way to reach the human)
    are left alone: they have no effect outside the run, and gating the question
    tool would need approval to ask for approval. Returns the list unchanged when
    the workspace has no hooks, no gate and no tool policy, and the agent sets no
    tool policy either, so an installation that uses none of them pays nothing
    but one config read per agent build.
    """
    from tools.approval import ALWAYS_GATED, NEVER_GATED

    guard = ToolGuard(agent_id=agent_id, spec=spec, workspace=workspace)
    always = any(getattr(t, "name", "") in ALWAYS_GATED for t in tools)
    if not always and not guard.hooks() and not guard.gate_enabled() and not guard.has_policy():
        # Sequence guardrails (guardrails/sequence.py) need the wrapper too.
        from guardrails.sequence import has_rules
        if not has_rules(guard.workspace, spec):
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
    "AFTER_RUN",
    "BEFORE_RUN",
    "DEFAULT_HOOK_TIMEOUT",
    "EVENTS",
    "EVENT_ALIASES",
    "GuardedTool",
    "clear_pending_approval",
    "HOOKS_FILENAME",
    "HookOutcome",
    "POST_TOOL_USE",
    "PRE_TOOL_USE",
    "RunHooks",
    "ToolGuard",
    "guard_action_tools",
    "load_hooks",
    "matches",
    "normalize_events",
    "pending_approval_for",
    "run_after_run",
    "run_before_run",
    "run_post_tool_use",
    "run_pre_tool_use",
]
