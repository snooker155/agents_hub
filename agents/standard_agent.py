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
from agents.callbacks.guards import AskUserGuard, AskUserSignal, ContextWindowGuard


def _awaiting_input_result(sig: AskUserSignal) -> AgentResult:
    """Build the AgentResult for a run paused by the ask_user tool."""
    return AgentResult(
        ok=True,
        status="awaiting_input",
        agent_output=sig.question,
        pending_question={"question": sig.question, "choices": sig.choices},
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

    def run(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent.

        ``history`` (keyword) carries the conversation before this turn as
        LangChain messages; see :meth:`_executor_input`.
        """
        guard = ToolRepetitionGuard(max_repeats=self.max_tool_repeats)
        try:
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

            config = {"callbacks": callbacks} if callbacks else None
            result = self.executor.invoke(
                self._executor_input(instruction, kwargs.get("history")), config=config)

            output = result.get("output", "") if isinstance(result, dict) else str(result)
            clean_text, response_obj = self._finalize_output(output)

            return AgentResult(
                ok=True,
                status="done",
                agent_output=clean_text,
                response=response_obj,
                steps=_collect_steps(result),
            )
        except AskUserSignal as sig:
            # The agent called ask_user: pause the run and hand the question back.
            return _awaiting_input_result(sig)
        except ToolRepetitionError as e:
            output = guard.last_llm_text or f"[Agent stopped: {e}]"
            return AgentResult(
                ok=True,
                status="stopped",
                agent_output=output,
            )
        except Exception as e:
            return AgentResult(
                ok=False,
                status="error",
                error=str(e),
            )

    async def arun(self, instruction: str, **kwargs) -> AgentResult:
        """Execute the agent asynchronously using ainvoke (no threads required).

        Takes the same ``history`` keyword as :meth:`run`.
        """
        import asyncio
        guard = ToolRepetitionGuard(max_repeats=self.max_tool_repeats)
        try:
            callbacks = [guard, AskUserGuard(), *list(kwargs.get("callbacks") or [])]
            ctx_guard = self._context_window_guard()
            if ctx_guard:
                callbacks.append(ctx_guard)
            config = {"callbacks": callbacks} if callbacks else None
            result = await self.executor.ainvoke(
                self._executor_input(instruction, kwargs.get("history")), config=config)
            output = result.get("output", "") if isinstance(result, dict) else str(result)
            clean_text, response_obj = self._finalize_output(output)
            return AgentResult(ok=True, status="done", agent_output=clean_text,
                               response=response_obj, steps=_collect_steps(result))
        except asyncio.CancelledError:
            raise  # propagate so the asyncio task is properly marked cancelled
        except AskUserSignal as sig:
            return _awaiting_input_result(sig)
        except ToolRepetitionError as e:
            output = guard.last_llm_text or f"[Agent stopped: {e}]"
            return AgentResult(ok=True, status="stopped", agent_output=output)
        except Exception as e:
            return AgentResult(ok=False, status="error", error=str(e))
