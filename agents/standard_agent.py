"""
StandardAgent — the default agent implementation.

A concrete ``AgentBase`` that executes a LangChain agent executor against any
tool set, with tool-repetition guarding and optional shared-progress reporting.
Instances are built by ``agents.agent_factory.AgentFactory``; this module holds
only the runtime class, keeping the factory focused on assembly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from agents.agent_base import AgentBase, AgentResult, ToolResult
from agents.agent_response import parse_agent_response
from agents.agent_utils import (
    SharedProgressCallback,
    ToolRepetitionError,
    ToolRepetitionGuard,
)
from agents.callbacks.guards import (
    AskUserGuard,
    AskUserSignal,
    ContextWindowGuard,
    RunBudgetExceeded,
    RunBudgetGuard,
)


def _awaiting_input_result(sig: AskUserSignal) -> AgentResult:
    """Build the AgentResult for a run paused by the ask_user tool."""
    return AgentResult(
        ok=True,
        status="awaiting_input",
        agent_output=sig.question,
        pending_question={"question": sig.question, "choices": sig.choices},
    )


def _budget_paused_result(exc: RunBudgetExceeded, agent_id: str, run_id: str = "") -> AgentResult:
    """Build the AgentResult for a run paused at its task's money cap.

    Same status as a tool call waiting for approval, so the runner parks the
    task through the one path it already has (``park_task_awaiting_approval``);
    the ``kind: "budget"`` on the pending record is what tells the dashboard and
    the approve route that the decision is about money, not about a call.
    """
    pending = exc.pending(agent_id=agent_id, run_id=run_id)
    return AgentResult(
        ok=True,
        status="awaiting_approval",
        agent_output=pending["reason"],
        pending_approval=pending,
    )


def _collect_steps(result: Any) -> List[ToolResult]:
    """Map the executor's ``intermediate_steps`` (AgentAction, observation)
    pairs onto AgentResult.steps so callers can inspect the tool trail."""
    steps: List[ToolResult] = []
    if not isinstance(result, dict):
        return steps
    for entry in result.get("intermediate_steps") or []:
        try:
            action, observation = entry
            tool_input = getattr(action, "tool_input", None)
            args = tool_input if isinstance(tool_input, dict) else {"input": tool_input}
            steps.append(ToolResult(
                name=str(getattr(action, "tool", "")),
                args=args,
                output=str(observation),
            ))
        except Exception:
            continue
    return steps


class StandardAgent(AgentBase):
    """Standard agent implementation that works with any tool set."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        system_prompt: str,
        tools: List[Any],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        verbose: bool = False,
        workspace: Optional[str] = None,
        streaming: bool = False,
        max_tool_repeats: int = 10,
        think_gate: Optional[Any] = None,
        thinking_level: Optional[str] = None,
        native_reasoning: bool = False,
        max_iterations: int = 60,
        spec: Optional[Any] = None,
    ):
        super().__init__(
            agent_id=agent_id,
            name=name,
            system_prompt=system_prompt,
            tools=tools,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
            verbose=verbose,
            streaming=streaming,
            max_tool_repeats=max_tool_repeats,
            think_gate=think_gate,
            thinking_level=thinking_level,
            max_iterations=max_iterations,
            spec=spec,
        )
        self.workspace = workspace
        # When native model reasoning is on (a positive thinking_level), the
        # reasoning (LM Studio/Ollama reasoning models, inline <think> tags) is
        # shown in the chat bubble by ChatStreamCallback; this flag strips it
        # from the final answer so it isn't repeated there. No gating — the
        # agent is never forced to think.
        self.native_reasoning = native_reasoning

    def _finalize_output(self, output):
        """Strip native-reasoning tags then split off any structured <<<ui>>> block.

        Returns ``(clean_text, response_obj)`` — ``clean_text`` is the plain-text
        fallback stored in ``agent_output``; ``response_obj`` is an AgentResponse
        when the agent emitted a UI block, else None.
        """
        if isinstance(output, list):
            # Block-list content (Anthropic thinking, OpenAI Responses API):
            # keep the answer text, drop reasoning/tool blocks.
            output = "".join(
                b.get("text", "") for b in output
                if isinstance(b, dict) and b.get("type") in (None, "text", "output_text")
            )
        if self.native_reasoning and output:
            # Native model reasoning is shown in the chat bubble; keep it out of
            # the final answer text so it isn't repeated there.
            from reasoning.native_reasoning import strip_think_tags
            output = strip_think_tags(output)
        return parse_agent_response(output or "")

    @staticmethod
    def _executor_input(instruction: str, history: Any = None) -> dict:
        """The executor payload for one run.

        ``history`` is the conversation before this turn as LangChain messages
        (HumanMessage / AIMessage; tool messages are never replayed). It fills
        the prompt's ``chat_history`` placeholder, so the model reads prior turns
        as a conversation instead of as text folded into this turn's message.
        Callers with no history (task runs, evals, delegation) pass none and the
        payload is what it always was.
        """
        payload: dict = {"input": instruction}
        if history:
            payload["chat_history"] = list(history)
        return payload

    def _context_window_guard(self) -> Optional[ContextWindowGuard]:
        """Build a context-window guard for this agent's model, or None when the
        window is unknown (no reliable limit to enforce)."""
        try:
            from providers.context_windows import get_model_context_window
            window = get_model_context_window(self.provider or "", self.model or "")
        except Exception:
            window = 0
        if window > 0:
            return ContextWindowGuard(window, model_name=self.model or "")
        return None

    def _run_budget_guard(self) -> Optional[RunBudgetGuard]:
        """The money-cap guard for this run, or None when the launcher set no
        cap (see common/run_budget.py). Never raises: a cap that cannot be read
        is no cap."""
        try:
            return RunBudgetGuard.from_env(provider=self.provider or "", model=self.model or "")
        except Exception:  # noqa: BLE001 - fails open, like the rest of the budget
            return None

    # ── per-run policies around the loop ─────────────────────────────────

    def _begin_loop(self, kwargs: dict):
        """A fresh LoopState for this run, installed for its context.

        Returns ``(state, token)``; :func:`agents.agent_loop.reset_state` takes
        the token back when the run ends.
        """
        from agents.agent_loop import new_state, set_state
        state = new_state(self, run_id=kwargs.get("run_id"),
                          workspace=kwargs.get("workspace") or self.workspace)
        return state, set_state(state)

    def _guardrail_trip(self, state: Any, stage: str, text: str) -> Optional[AgentResult]:
        """Run the workspace's guardrails on the run's input or output.

        Returns the result that ends the run when a guardrail tripped, else
        None. A guardrail module that is missing or fails is no guardrail: the
        check is logged by guardrails.runtime itself and the run goes on.
        """
        try:
            from guardrails import runtime as _guardrails
            check = _guardrails.check_input if stage == "input" else _guardrails.check_output
            trip = check(self, state, text)
        except Exception:  # noqa: BLE001 - see docstring
            return None
        if not trip:
            return None
        reason = str(trip.get("reason") or "guardrail tripped")
        name = str(trip.get("name") or trip.get("guardrail_id") or "guardrail")
        return AgentResult(
            ok=False,
            status="guardrail_tripped",
            error=f"Guardrail '{name}' stopped the run on its {stage}: {reason}",
            agent_output=str(trip.get("message") or ""),
            loop=state.summary(),
        )

    def _structured_output(self, state: Any, text: str) -> tuple:
        """Validate the final answer against the agent's output schema.

        Returns ``(text, error)``: the (possibly repaired) answer, and an error
        string when it still does not match after the allowed retries. Agents
        without a schema pass through unchanged.
        """
        try:
            from agents.loop_ext import structured as _structured
            return _structured.finalize_output(self, state, text)
        except Exception:  # noqa: BLE001 - validation machinery failing keeps the plain answer
            return text, None

    def _finish(self, state: Any, result: Any) -> AgentResult:
        """The AgentResult for a finished executor call, with the run's
        output checks applied: structured output first (so a guardrail sees the
        answer that will actually be returned), then the output guardrails."""
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        clean_text, response_obj = self._finalize_output(output)
        clean_text, schema_error = self._structured_output(state, clean_text)
        if schema_error:
            return AgentResult(ok=False, status="error", error=schema_error,
                               agent_output=clean_text, steps=_collect_steps(result),
                               loop=state.summary())
        tripped = self._guardrail_trip(state, "output", clean_text)
        if tripped is not None:
            tripped.steps = _collect_steps(result)
            return tripped
        return AgentResult(
            ok=True,
            status="done",
            agent_output=clean_text,
            response=response_obj,
            steps=_collect_steps(result),
            loop=state.summary(),
        )

    def run(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent.

        ``history`` (keyword) carries the conversation before this turn as
        LangChain messages; see :meth:`_executor_input`.
        """
        from agents.agent_loop import reset_state
        guard = ToolRepetitionGuard(max_repeats=self.max_tool_repeats)
        state, token = self._begin_loop(kwargs)
        try:
            tripped = self._guardrail_trip(state, "input", instruction)
            if tripped is not None:
                return tripped
            workspace = kwargs.get("workspace", self.workspace)
            callbacks = [guard, AskUserGuard()]
            ctx_guard = self._context_window_guard()
            if ctx_guard:
                callbacks.append(ctx_guard)
            if workspace:
                callbacks.append(SharedProgressCallback(
                    workspace=Path(workspace),
                    model_name=self.model
                ))
            # Auto-attach stop callback when running inside a managed run subprocess.
            # if run_id:
            #     callbacks.append(RunStopCallback(run_id))
            extra_callbacks = kwargs.get("callbacks") or []
            if isinstance(extra_callbacks, list):
                callbacks.extend(extra_callbacks)
            elif extra_callbacks:
                callbacks.append(extra_callbacks)
            # Last, so the stats callback has recorded the call's tokens before
            # the guard raises on it.
            budget_guard = self._run_budget_guard()
            if budget_guard:
                callbacks.append(budget_guard)

            config = {"callbacks": callbacks} if callbacks else None
            result = self.executor.invoke(
                self._executor_input(instruction, kwargs.get("history")), config=config)
            return self._finish(state, result)
        except AskUserSignal as sig:
            # The agent called ask_user: pause the run and hand the question back.
            return _awaiting_input_result(sig)
        except RunBudgetExceeded as exc:
            return _budget_paused_result(exc, self.agent_id, str(kwargs.get("run_id") or ""))
        except ToolRepetitionError as e:
            output = guard.last_llm_text or f"[Agent stopped: {e}]"
            return AgentResult(
                ok=True,
                status="stopped",
                agent_output=output,
                loop=state.summary(),
            )
        except Exception as e:
            return AgentResult(
                ok=False,
                status="error",
                error=str(e),
                loop=state.summary(),
            )
        finally:
            reset_state(token)

    async def arun(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent asynchronously using ainvoke (no threads required).

        Takes the same ``history`` keyword as :meth:`run`.
        """
        import asyncio
        from agents.agent_loop import reset_state
        guard = ToolRepetitionGuard(max_repeats=self.max_tool_repeats)
        state, token = self._begin_loop(kwargs)
        try:
            # A judge-model guardrail is a blocking call; off the event loop.
            tripped = await asyncio.to_thread(self._guardrail_trip, state, "input", instruction)
            if tripped is not None:
                return tripped
            callbacks = [guard, AskUserGuard(), *list(kwargs.get("callbacks") or [])]
            ctx_guard = self._context_window_guard()
            if ctx_guard:
                callbacks.append(ctx_guard)
            budget_guard = self._run_budget_guard()
            if budget_guard:
                callbacks.append(budget_guard)
            config = {"callbacks": callbacks} if callbacks else None
            result = await self.executor.ainvoke(
                self._executor_input(instruction, kwargs.get("history")), config=config)
            # The structured-output repair and the output guardrails may call a
            # model; keep them off the event loop.
            return await asyncio.to_thread(self._finish, state, result)
        except asyncio.CancelledError:
            raise  # propagate so the asyncio task is properly marked cancelled
        except AskUserSignal as sig:
            return _awaiting_input_result(sig)
        except RunBudgetExceeded as exc:
            return _budget_paused_result(exc, self.agent_id, str(kwargs.get("run_id") or ""))
        except ToolRepetitionError as e:
            output = guard.last_llm_text or f"[Agent stopped: {e}]"
            return AgentResult(ok=True, status="stopped", agent_output=output, loop=state.summary())
        except Exception as e:
            return AgentResult(ok=False, status="error", error=str(e), loop=state.summary())
        finally:
            reset_state(token)
