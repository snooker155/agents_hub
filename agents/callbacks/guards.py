"""
Run guards — callbacks that watch an agent run and abort it with a signal.

Each guard raises its own exception (with ``raise_error = True`` so LangChain
propagates it out of ``executor.invoke`` instead of swallowing it); the agent
runner catches the exception and turns it into the appropriate AgentResult.

- ``ToolRepetitionGuard`` (+ ``ToolRepetitionError``) — stops a run when the
  same tool is called too many times in a row.
- ``AskUserGuard`` (+ ``AskUserSignal``) — pauses a run when the agent calls
  the ``ask_user`` tool.
- ``ApprovalSignal`` — pauses a run when a tool call needs a human's approval
  before it may happen (raised by the gate in ``agents/hooks.py``, not by a
  callback).
- ``ContextWindowGuard`` (+ ``ContextWindowExceededError``) — stops a run once
  its prompt exceeds the model's context window.
- ``RunBudgetGuard`` (+ ``RunBudgetExceeded``, ``RunBudgetUnpriced``) — pauses a
  run once the task's runs have spent its money cap, or (fail_closed only) once
  a call cannot be priced at all (see ``common/run_budget.py``).
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional, Set, Tuple

from langchain_core.callbacks import BaseCallbackHandler

from agents.callbacks.run_statistics import (
    cached_input_tokens,
    extract_token_usage,
    normalize_usage,
)

# Reasoning scratchpad tools are exempt from the tool-repetition guard: calling
# them repeatedly is the agent reasoning more, not looping. Kept in sync with
# ``reasoning.think_gate._REASONING_TOOL_NAMES``.
_REASONING_TOOL_NAMES = {
    "think",
    "plan",
    "save_plan",
    "get_plan",
    "list_plans",
    "update_plan_status",
    "delete_plan",
    "assess_complexity",
}

# Graph-builder tools are exempt too: the Architect agent legitimately calls
# add_graph_node / add_graph_edge (and the delete variants) many times in a row
# to build a large graph — that is the intended behavior, not a runaway loop.
_GRAPH_TOOL_NAMES = {
    "add_graph_node",
    "add_graph_edge",
    "delete_graph_node",
    "delete_graph_edge",
}

# Read-only inspection tools are exempt too: an agent (e.g. the Code Reviewer)
# legitimately reads or searches many files in a row to understand a change.
# These calls have no side effects, so repeating them is intended work, not a
# runaway loop.
_READONLY_TOOL_NAMES = {
    "read_file",
    "list_files",
    "search_text",
    "read_memory",
}

# Tools that may be called any number of times consecutively without tripping
# the repetition guard.
_REPEAT_EXEMPT_TOOL_NAMES = (
    _REASONING_TOOL_NAMES | _GRAPH_TOOL_NAMES | _READONLY_TOOL_NAMES
)

# Pass this as ``max_tool_repeats`` to lift the ceiling: a special-purpose agent
# (the entity builders, the Architect, the Planner) may call the same tool any
# number of times in a row without being stopped.
UNLIMITED_TOOL_REPEATS = 0


# Sentinel key the ``ask_user`` tool puts in its JSON output so AskUserGuard can
# recognise the call and pause the run. Kept here so the tool and the guard agree
# on the contract without importing each other.
ASK_USER_SENTINEL = "__ask_user__"


class AskUserSignal(RuntimeError):
    """Raised to pause a run when the agent calls ``ask_user``.

    Carries the question (and optional discrete choices) so the runner can store
    it on the task, surface it to the user, and resume once answered. Like
    ToolRepetitionError it is raised from a callback with ``raise_error = True``
    so LangChain propagates it out of ``executor.invoke`` instead of swallowing it.
    """

    def __init__(self, question: str, choices: Optional[list] = None) -> None:
        super().__init__(question)
        self.question = question
        self.choices = list(choices or [])


class AskUserGuard(BaseCallbackHandler):
    """Stop a run the moment the agent calls the ``ask_user`` tool.

    The tool returns a JSON object tagged with ``ASK_USER_SENTINEL``; this guard
    inspects each tool's output, and when it sees that tag it raises
    ``AskUserSignal`` so the run ends cleanly with the question in hand. Detecting
    via the output (rather than the tool name + parsing its input) keeps the
    question/choices exactly as the tool validated them.
    """

    def __init__(self) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exception

    @staticmethod
    def _output_text(output: Any) -> str:
        if isinstance(output, str):
            return output
        content = getattr(output, "content", None)
        if isinstance(content, str):
            return content
        try:
            return str(output)
        except Exception:
            return ""

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        text = self._output_text(output)
        if not text or ASK_USER_SENTINEL not in text:
            return
        try:
            data = json.loads(text)
        except Exception:
            return
        if isinstance(data, dict) and data.get(ASK_USER_SENTINEL):
            raise AskUserSignal(
                str(data.get("question") or ""),
                data.get("choices") or [],
            )


class ApprovalSignal(RuntimeError):
    """Raised to pause a run when a tool call needs the user's approval.

    Modelled on ``AskUserSignal``: the run ends cleanly carrying what the user
    has to decide about, so the runner can park the task, show the call, and
    resume once answered. Unlike AskUserSignal it is raised from *inside the
    tool wrapper* rather than from a callback, because the decision has to
    happen before the call runs, not after it produced output.

    ``payload`` is the pending-approval record the task stores:
    ``{tool, input, reason, run_id, agent_id, hook, fingerprint}``.
    """

    def __init__(self, payload: dict) -> None:
        tool = str((payload or {}).get("tool") or "tool")
        super().__init__(f"Approval required before calling `{tool}`")
        self.payload = dict(payload or {})

    @property
    def tool(self) -> str:
        return str(self.payload.get("tool") or "")

    @property
    def tool_input(self) -> Any:
        return self.payload.get("input")

    @property
    def reason(self) -> str:
        return str(self.payload.get("reason") or "")


class ContextWindowExceededError(RuntimeError):
    """Raised when a run's prompt no longer fits the model's context window."""


class ContextWindowGuard(BaseCallbackHandler):
    """Abort a run once its prompt exceeds the model's context window.

    Without this, history keeps accumulating past the window: the backend
    silently truncates the head, the model loses what it already read, and the
    run degenerates into re-reading files for hours (each step also gets slower
    as the prompt grows). Checks the *per-call* prompt size reported by the
    backend after every LLM call and raises so the run fails with an explicit
    message instead of looping.

    ``raise_error = True`` is required so LangChain does not swallow the
    exception in its callback dispatch loop.

    When the run's loop compacts its context (agents/loop_ext/compaction.py),
    an overflow is first handed to compaction instead: the guard asks for a
    forced pass on the next model call (clear old tool results, fold the
    oldest steps) and lets the run go on. Only when that happens
    ``OVERFLOW_RETRIES`` times in a row, so compaction had its chance and the
    prompt still does not fit, is the run stopped as before.
    """

    #: Consecutive overflows handed to compaction before the run is stopped.
    #: One constant with agents.loop_ext.compaction.OVERFLOW_RETRIES.
    OVERFLOW_RETRIES = 2

    def __init__(self, context_window: int, model_name: str = "") -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exceptions
        self.context_window = int(context_window)
        self.model_name = model_name or "unknown"

    @classmethod
    def _compaction(cls) -> Optional[Dict[str, Any]]:
        """The compaction record of the running loop, when compaction is on.

        Read from the loop state of the run in this context (the state lives
        in a context variable the executor carries into its callbacks), so a
        guard that runs outside the agent loop, or on an agent without the
        extension, sees None and behaves as it always did.
        """
        try:
            from agents.agent_loop import current_state
            state = current_state()
        except ImportError:
            return None
        record = state.scratch.get("compaction") if state is not None else None
        return record if isinstance(record, dict) and record.get("active") else None

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if self.context_window <= 0:
            return
        p, _c, _t = normalize_usage(extract_token_usage(response))
        compaction = self._compaction()
        if p <= self.context_window:
            if compaction is not None and p > 0:
                compaction["overflows"] = 0
            return
        if compaction is not None:
            tries = int(compaction.get("overflows") or 0)
            if tries < self.OVERFLOW_RETRIES:
                compaction["overflows"] = tries + 1
                compaction["force"] = True
                compaction["overflow_tokens"] = int(p)
                return
        raise ContextWindowExceededError(
            f"Context window exceeded: the last prompt was {p} tokens but model "
            f"'{self.model_name}' accepts at most {self.context_window}. The run was "
            f"aborted because the accumulated history no longer fits the model. "
            f"Split the task into smaller pieces or use a model with a larger "
            f"context window."
        )


class ToolRepetitionError(RuntimeError):
    """Raised when the same tool is called too many times consecutively."""


class ToolRepetitionGuard(BaseCallbackHandler):
    """Stop agent execution when the same tool is called N times in a row.

    Accumulates every (tool, output) pair during the run so that the full
    work done by the agent can be returned when stopping early.

    ``max_repeats <= 0`` disables the limit entirely: the guard still watches
    the run (it keeps the last LLM text for the runner) but never stops it.
    Builder agents that legitimately hammer one tool for a whole run are built
    that way, see ``UNLIMITED_TOOL_REPEATS``.

    ``raise_error = True`` is required so LangChain does not swallow the
    exception in its callback dispatch loop.
    """

    def __init__(self, max_repeats: int = 3) -> None:
        super().__init__()
        self.raise_error = True  # tell LangChain to propagate our exceptions
        self.max_repeats = max_repeats
        self._last_tool: Optional[str] = None
        self._consecutive: int = 0
        self._should_stop: bool = False
        self._pending_tool: str = ""
        self.last_llm_text: str = ""  # last coherent text the LLM produced

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Capture the last text the LLM generated (its reasoning / partial answer)."""
        try:
            for grp in (getattr(response, "generations", []) or []):
                for g in grp:
                    msg = getattr(g, "message", None)
                    content = getattr(msg, "content", None) if msg else None
                    if isinstance(content, str) and content.strip():
                        self.last_llm_text = content.strip()
                    elif isinstance(content, list):
                        # Anthropic-style structured content blocks
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        ]
                        text = " ".join(t for t in texts if t).strip()
                        if text:
                            self.last_llm_text = text
        except Exception:
            pass

    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        name: str = (serialized.get("name", "") if isinstance(serialized, dict) else "") or ""
        self._pending_tool = name
        # No ceiling configured: the run is allowed to call one tool forever.
        if self.max_repeats <= 0:
            self._should_stop = False
            return
        # Reasoning scratchpads (think/plan) and graph-builder tools are exempt:
        # calling them repeatedly is intended work (more reasoning, or adding many
        # nodes/edges), not an infinite loop. Don't count them toward the limit.
        if name in _REPEAT_EXEMPT_TOOL_NAMES:
            self._should_stop = False
            return
        if name and name == self._last_tool:
            self._consecutive += 1
        else:
            self._last_tool = name
            self._consecutive = 1
        self._should_stop = self._consecutive > self.max_repeats

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self._should_stop:
            raise ToolRepetitionError(
                f"Tool '{self._last_tool}' was called {self._consecutive} times in a row "
                f"(limit={self.max_repeats}). Stopping agent to prevent infinite loop."
            )


# -------------------- Money cap --------------------

# Kept in step with common.run_budget (the launcher side); duplicated rather
# than imported so a run container needs nothing from ``common`` to read them.
RUN_BUDGET_ENV_LIMIT = "AGENTS_HUB_RUN_BUDGET_USD"
RUN_BUDGET_ENV_SPENT = "AGENTS_HUB_RUN_BUDGET_SPENT_USD"
RUN_BUDGET_ENV_PRICES = "AGENTS_HUB_RUN_PRICES"
RUN_BUDGET_ENV_FAIL_CLOSED = "AGENTS_HUB_RUN_BUDGET_FAIL_CLOSED"


class RunBudgetExceeded(RuntimeError):
    """Raised to pause a run that reached its task's money cap.

    Like ``AskUserSignal`` it is a pause, not a failure: the agent runner turns
    it into an ``awaiting_approval`` result with a ``kind: "budget"`` pending
    record, and the operator raises the cap or stops the task.
    """

    def __init__(self, spent_usd: float, limit_usd: float) -> None:
        self.spent_usd = float(spent_usd)
        self.limit_usd = float(limit_usd)
        super().__init__(
            f"The run reached its money cap of ${self.limit_usd:.2f} "
            f"(spent ${self.spent_usd:.2f})."
        )

    def pending(self, *, agent_id: str = "", run_id: str = "") -> Dict[str, Any]:
        """The pending-approval record a task parks with."""
        return {
            "kind": "budget",
            "spent_usd": round(self.spent_usd, 6),
            "limit_usd": round(self.limit_usd, 6),
            "tool": "",
            "input": {},
            "reason": (
                f"The run reached its money cap of ${self.limit_usd:.2f} "
                f"(spent ${self.spent_usd:.2f}). Raise the cap to continue or stop the task."
            ),
            "agent_id": agent_id,
            "run_id": run_id,
            "fingerprint": "",
            "hook": "",
        }


class RunBudgetUnpriced(RunBudgetExceeded):
    """Raised instead of :class:`RunBudgetExceeded` when a call's model has no
    known price and the workspace requires fail-closed enforcement.

    ``RunBudgetGuard`` normally counts an unpriced call as free (see its class
    docstring): the cap fails open rather than guessing. A workspace that opted
    into ``fail_closed`` (``common.budget``, carried here via
    ``AGENTS_HUB_RUN_BUDGET_FAIL_CLOSED``) would rather stop the run than let an
    unpriced model spend past a cap that can never see it coming, so this
    blocks the call before it runs instead of pricing it as ``$0``.
    """

    def __init__(self, spent_usd: float, limit_usd: float, model: str) -> None:
        self.model = model or "unknown"
        # Deliberately skip RunBudgetExceeded.__init__: its "reached its money
        # cap" message does not fit here, the cap was not necessarily met, the
        # call's cost just cannot be trusted.
        self.spent_usd = float(spent_usd)
        self.limit_usd = float(limit_usd)
        RuntimeError.__init__(
            self,
            f"Model '{self.model}' has no known price and this workspace requires "
            f"fail-closed budget enforcement, so the call was blocked before it ran "
            f"(spent ${self.spent_usd:.2f} of ${self.limit_usd:.2f})."
        )

    def pending(self, *, agent_id: str = "", run_id: str = "") -> Dict[str, Any]:
        return {
            "kind": "budget",
            "spent_usd": round(self.spent_usd, 6),
            "limit_usd": round(self.limit_usd, 6),
            "tool": "",
            "input": {},
            "reason": (
                f"Model '{self.model}' has no known price and this workspace requires "
                f"fail-closed budget enforcement. The call was blocked before it ran. "
                f"Price the model, raise the cap, or stop the task."
            ),
            "agent_id": agent_id,
            "run_id": run_id,
            "fingerprint": "",
            "hook": "",
        }


# What this process has spent on LLM calls, shared by every guard in it. A run
# process serves one task, but its agent may delegate to others in the same
# process (run_agent_tool), each building its own guard; sharing the total means
# a sub-agent's calls count against the task too, and deduplicating by the LLM
# call's run id keeps a call seen by two guards (callbacks inherited by a nested
# executor) from being charged twice.
_SPEND_LOCK = threading.Lock()
_PROCESS_SPEND: Dict[str, Any] = {"usd": 0.0, "seen": set()}


def reset_run_budget_spend() -> None:
    """Forget what this process has spent (tests, and a process reused for a
    new run)."""
    with _SPEND_LOCK:
        _PROCESS_SPEND["usd"] = 0.0
        _PROCESS_SPEND["seen"] = set()


def _parse_prices(raw: str) -> Dict[Tuple[str, str], Tuple[float, float, float]]:
    prices: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
    try:
        rows = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return prices
    if not isinstance(rows, list):
        return prices
    for row in rows:
        try:
            provider, model, i, o, c = row
            prices[(str(provider), str(model))] = (float(i or 0), float(o or 0), float(c or 0))
        except (TypeError, ValueError):
            continue
    return prices


def _response_model(response: Any) -> str:
    """The model name the provider reported for this call, if any."""
    try:
        out = getattr(response, "llm_output", None) or {}
        name = out.get("model_name") or out.get("model")
        if name:
            return str(name)
    except Exception:
        pass
    try:
        for grp in (getattr(response, "generations", []) or []):
            for g in grp:
                md = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                name = md.get("model_name") or md.get("model")
                if name:
                    return str(name)
    except Exception:
        pass
    return ""


class RunBudgetGuard(BaseCallbackHandler):
    """Pause a run once the task's runs have spent its money cap.

    Built from three environment variables the launcher sets
    (``common.run_budget.launch_env``): the cap, what the task's earlier runs
    already spent, and the catalog prices. Each finished LLM call is priced with
    the same formula as ``common.pricing.run_cost_usd`` (fresh input, cached
    input and output at their own rates) from the usage the provider reported.

    Two checkpoints:

    - ``on_llm_end`` raises when the call that just finished crossed the cap, so
      the run stops before acting on (and paying for the follow-up of) it.
    - ``on_llm_start`` / ``on_chat_model_start`` raise when the cap is already
      met, so a run launched over its cap parks before its first paid call.

    A model with no known price counts as free by default: the cap fails open
    rather than guessing. A workspace with ``fail_closed`` set changes that (see
    :class:`RunBudgetUnpriced`): an unpriced call blocks instead, since counting
    it as free would let the cap it is meant to enforce silently never trip.
    ``raise_error = True`` makes LangChain propagate the exception; put this
    guard last in the callback list so the stats callback records the call's
    tokens before it raises.
    """

    def __init__(
        self,
        limit_usd: float,
        prior_usd: float = 0.0,
        prices: Optional[Dict[Tuple[str, str], Tuple[float, float, float]]] = None,
        provider: str = "",
        model: str = "",
        fail_closed: bool = False,
    ) -> None:
        super().__init__()
        self.raise_error = True
        self.limit_usd = float(limit_usd)
        self.prior_usd = max(0.0, float(prior_usd or 0.0))
        self.prices = dict(prices or {})
        self.provider = provider or ""
        self.model = model or ""
        self.fail_closed = bool(fail_closed)

    @classmethod
    def from_env(cls, provider: str = "", model: str = "") -> Optional["RunBudgetGuard"]:
        """A guard for this process's run, or None when the run has no cap."""
        try:
            limit = float(os.environ.get(RUN_BUDGET_ENV_LIMIT) or 0.0)
        except ValueError:
            return None
        if limit <= 0:
            return None
        try:
            prior = float(os.environ.get(RUN_BUDGET_ENV_SPENT) or 0.0)
        except ValueError:
            prior = 0.0
        prices = _parse_prices(os.environ.get(RUN_BUDGET_ENV_PRICES) or "")
        fail_closed = os.environ.get(RUN_BUDGET_ENV_FAIL_CLOSED) not in (None, "", "0")
        return cls(limit, prior, prices, provider=provider, model=model, fail_closed=fail_closed)

    @property
    def spent_usd(self) -> float:
        """What the task has spent: earlier runs plus this process so far."""
        with _SPEND_LOCK:
            return self.prior_usd + float(_PROCESS_SPEND["usd"])

    def _price(self, model: str) -> Tuple[float, float, float]:
        for key in ((self.provider, model), (self.provider, self.model)):
            if key[1] and key in self.prices:
                return self.prices[key]
        # The provider reported a model under a name the agent's provider does
        # not list (a gateway, a dated alias): match by model id alone.
        for name in (model, self.model):
            if not name:
                continue
            for (_prov, mid), price in self.prices.items():
                if mid == name:
                    return price
        return (0.0, 0.0, 0.0)

    def _has_price(self, model: str) -> bool:
        """Whether ``model`` (or the guard's own configured model) matches a
        catalog entry, using the same lookup order as :meth:`_price`.

        Kept separate from ``_price`` because its ``(0.0, 0.0, 0.0)`` return
        means both "not found" and "found, and it really is free" (the catalog
        allows an explicit zero price) — ``fail_closed`` needs to tell those
        apart, which the returned tuple alone cannot.
        """
        for key in ((self.provider, model), (self.provider, self.model)):
            if key[1] and key in self.prices:
                return True
        for name in (model, self.model):
            if not name:
                continue
            if any(mid == name for _prov, mid in self.prices):
                return True
        return False

    def cost_of(self, response: Any) -> float:
        """USD cost of one LLM response, 0 when unpriced or unreported."""
        usage = extract_token_usage(response)
        prompt, completion, _total = normalize_usage(usage)
        cached = max(0, min(cached_input_tokens(usage), prompt))
        in_price, out_price, cached_price = self._price(_response_model(response))
        return (
            (prompt - cached) / 1_000_000 * in_price
            + cached / 1_000_000 * cached_price
            + completion / 1_000_000 * out_price
        )

    def _check(self) -> None:
        # Under fail_closed, a call about to run through a model this guard
        # cannot price is stopped here, before it happens: the "stop before it"
        # side of the same trade-off on_llm_end applies after the fact for a
        # call whose model only revealed itself in the response.
        if self.fail_closed and self.model and not self._has_price(self.model):
            raise RunBudgetUnpriced(self.spent_usd, self.limit_usd, self.model)
        spent = self.spent_usd
        if spent >= self.limit_usd:
            raise RunBudgetExceeded(spent, self.limit_usd)

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        self._check()

    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self._check()

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        call_id = str(kwargs.get("run_id") or "")
        model = ""
        try:
            model = _response_model(response)
            cost = self.cost_of(response)
        except Exception:  # noqa: BLE001 - an unreadable response counts as free (fail open)
            cost = 0.0
        with _SPEND_LOCK:
            seen: Set[str] = _PROCESS_SPEND["seen"]
            if not call_id or call_id not in seen:
                if call_id:
                    seen.add(call_id)
                _PROCESS_SPEND["usd"] = float(_PROCESS_SPEND["usd"]) + cost
        # The response can report a model this guard never saw before the call
        # (a gateway or dated alias _price/_has_price still cannot match): the
        # call already happened, but fail_closed still blocks the next one
        # rather than let every following call price as free too.
        if self.fail_closed and not self._has_price(model or self.model):
            raise RunBudgetUnpriced(self.spent_usd, self.limit_usd, model or self.model)
        self._check()
