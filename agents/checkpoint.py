"""
Checkpoints for the agent loop, so a run whose process died can carry on.

A flow run has always had this: ``FlowState`` is written after every node,
and the watchdog relaunches a dead flow from it. A plain agent run kept its
whole conversation in the memory of one process, so a crash, an OOM kill or
a host going away meant the run failed and every tool call it had made was
paid for nothing. This module gives the agent loop the same safety net.

The checkpoint is the tool trail so far: for every completed step the tool,
its input and its output; the step counter; the token counters; and, while a
tool is executing, the call in flight. :class:`RunCheckpointCallback` writes
it after every tool start and tool end through the run's state transport, so
it lands in ``run_payloads.checkpoint`` whether the run has the database or
only the HTTP relay.

Resuming (``runtime/agent_run.py --resume-checkpoint <run_id>``) turns the
trail back into the conversation the model saw: the original instruction,
then one assistant tool-call message and one tool result per step, so the
model continues from its last observation instead of starting over. The
resumed process keeps the same run id, and its own checkpoints replace the
old one as it goes.

A tool call that was in flight when the process died is the delicate case.
For an idempotent tool (reading a file, searching, listing) the call is simply
dropped from the trail and the model issues it again. For a tool with side
effects outside the run (``run_shell``, publishing to git, sending a message,
starting another agent) the trail gets a tool result that says the call was
interrupted and may or may not have happened, so the model checks before it
repeats it. The set is :data:`tools.capabilities.NON_IDEMPOTENT_TOOLS`.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger("agents.checkpoint")

#: Per-step output kept in the checkpoint. A tool that returns a whole file
#: is cut here; the model on resume sees the head of it and can re-read.
MAX_OUTPUT_CHARS = 20_000
#: Whole-checkpoint budget, oldest step outputs truncated first once past it.
MAX_TOTAL_CHARS = 400_000
#: Wording the resumed model sees in place of the result of an interrupted
#: side-effecting call.
INTERRUPTED_NOTE = (
    "[interrupted] The process running this agent stopped while this call was in "
    "flight. It may or may not have taken effect. Check the current state before "
    "repeating it."
)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} characters]"


def _tool_args(input_str: Any, inputs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The call's arguments as a dict: LangChain hands ``inputs`` when the
    tool takes structured arguments, a string otherwise."""
    if isinstance(inputs, dict) and inputs:
        return inputs
    if isinstance(input_str, dict):
        return input_str
    text = str(input_str)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"input": text}


class RunCheckpointCallback(BaseCallbackHandler):
    """Writes the tool trail after every tool boundary.

    ``save`` is called with the checkpoint dict; the run entrypoint passes its
    state transport's ``save_checkpoint``. Prior steps (a resume) seed the
    trail so the checkpoint after a resume still describes the whole run.
    """

    raise_error = False

    def __init__(self, run_id: str, save, *, instruction: str = "",
                 prior_steps: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__()
        self.run_id = run_id
        self._save = save
        self.instruction = instruction
        self.steps: List[Dict[str, Any]] = list(prior_steps or [])
        self.step = len(self.steps)
        self.pending: Optional[Dict[str, Any]] = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self._lock = threading.Lock()
        self.writes = 0

    # ── LangChain hooks ──────────────────────────────────────────────────────

    def on_tool_start(self, serialized, input_str, *, inputs=None, run_id=None, **_):
        name = (serialized or {}).get("name") if isinstance(serialized, dict) else "tool"
        with self._lock:
            self.step += 1
            self.pending = {
                "step": self.step,
                "tool": str(name or "tool"),
                "args": _tool_args(input_str, inputs),
                "call_id": str(run_id) if run_id else f"call_{self.step}",
                "started_at": _now_iso(),
            }
        self._write()

    def on_tool_end(self, output, **_):
        self._complete(_text(output))

    def on_tool_error(self, error, **_):
        self._complete(f"ERROR: {error}")

    def on_llm_end(self, response, **_):
        try:
            usage = getattr(response, "llm_output", None) or {}
            usage = usage.get("token_usage") or usage.get("usage") or {}
            self.prompt_tokens += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            self.completion_tokens += int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        except Exception:
            pass

    # ── the checkpoint ───────────────────────────────────────────────────────

    def _complete(self, output: str) -> None:
        with self._lock:
            if self.pending is None:
                return
            entry = dict(self.pending)
            entry["output"] = _clip(output)
            entry["finished_at"] = _now_iso()
            self.steps.append(entry)
            self.pending = None
        self._write()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            steps = [dict(s) for s in self.steps]
            pending = dict(self.pending) if self.pending else None
        total = sum(len(s.get("output") or "") for s in steps)
        for s in steps:
            if total <= MAX_TOTAL_CHARS:
                break
            out = s.get("output") or ""
            if len(out) > 2000:
                total -= len(out) - 2000
                s["output"] = out[:2000] + "\n... [truncated in checkpoint]"
        return {
            "version": 1,
            "run_id": self.run_id,
            "instruction": self.instruction,
            "step": self.step,
            "steps": steps,
            "pending": pending,
            "tokens": {"prompt": self.prompt_tokens, "completion": self.completion_tokens},
            "saved_at": _now_iso(),
        }

    def _write(self) -> None:
        try:
            self._save(self.run_id, self.snapshot())
            self.writes += 1
        except Exception:
            log.debug("checkpoint write failed for run %s", self.run_id, exc_info=True)


# ── persistence ──────────────────────────────────────────────────────────────

def save_checkpoint(run_id: str, checkpoint: Dict[str, Any]) -> None:
    """Store the checkpoint on the run's payload row and stamp the run."""
    from managers.runs import store
    store.save_run_checkpoint(run_id, checkpoint)


def load_checkpoint(run_id: str) -> Optional[Dict[str, Any]]:
    from managers.runs import store
    return store.load_run_checkpoint(run_id)


def clear_checkpoint(run_id: str) -> None:
    from managers.runs import store
    store.clear_run_checkpoint(run_id)


# ── resume ───────────────────────────────────────────────────────────────────

def is_idempotent(tool: str) -> bool:
    try:
        from tools.capabilities import NON_IDEMPOTENT_TOOLS
    except Exception:
        return False
    return tool not in NON_IDEMPOTENT_TOOLS and not tool.startswith("mcp__")


def resume_steps(checkpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The trail a resumed run continues from: every completed step, plus the
    interrupted call when repeating it blindly would be unsafe."""
    steps = [dict(s) for s in (checkpoint.get("steps") or [])]
    pending = checkpoint.get("pending")
    if pending and not is_idempotent(str(pending.get("tool") or "")):
        steps.append({**pending, "output": INTERRUPTED_NOTE, "interrupted": True})
    return steps


def history_messages(checkpoint: Dict[str, Any]) -> List[Any]:
    """The conversation the resumed model sees before its next turn."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    messages: List[Any] = []
    instruction = str(checkpoint.get("instruction") or "")
    if instruction:
        messages.append(HumanMessage(content=instruction))
    for step in resume_steps(checkpoint):
        call_id = str(step.get("call_id") or f"call_{step.get('step')}")
        name = str(step.get("tool") or "tool")
        args = step.get("args") if isinstance(step.get("args"), dict) else {"input": step.get("args")}
        messages.append(AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        ))
        messages.append(ToolMessage(content=str(step.get("output") or ""), tool_call_id=call_id,
                                    name=name))
    return messages


def resume_instruction(checkpoint: Dict[str, Any]) -> str:
    """The turn that restarts the loop."""
    steps = resume_steps(checkpoint)
    n = len(steps)
    note = ""
    if steps and steps[-1].get("interrupted"):
        note = (" The last call above was interrupted mid-flight; verify its effect "
                "before repeating it.")
    return (
        f"The process running you was interrupted after {n} tool call(s); their results "
        f"are above. Continue the task from where you left off and finish it.{note}"
    )


def elapsed_since(checkpoint: Dict[str, Any]) -> Optional[float]:
    try:
        from datetime import datetime
        saved = datetime.fromisoformat(str(checkpoint.get("saved_at")))
        return time.time() - saved.timestamp()
    except Exception:
        return None


__all__ = [
    "INTERRUPTED_NOTE", "RunCheckpointCallback", "clear_checkpoint", "history_messages",
    "is_idempotent", "load_checkpoint", "resume_instruction", "resume_steps", "save_checkpoint",
]
