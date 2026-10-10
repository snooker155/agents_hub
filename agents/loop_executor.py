"""The hub's own agent executor: the loop around the model call.

Replaces LangChain's ``AgentExecutor`` with the same contract, so nothing
above it changes: ``invoke`` / ``ainvoke`` take ``{"input", "chat_history"}``
and return ``{"output", "intermediate_steps"}``; the callbacks in ``config``
see the same events in the same order (``on_chain_start`` for the loop, the
model's own events through the agent runnable, ``on_tool_start`` /
``on_tool_end`` / ``on_tool_error`` from each tool, ``on_agent_action`` and
``on_agent_finish``, ``on_chain_end`` or ``on_chain_error``); a tool's
exception propagates out of ``invoke`` as before (that is how ``ask_user``
pauses a run and how the repetition and budget guards stop one).

What the loop does on each pass: ask the agent runnable
(:func:`agents.agent_loop.build_agent_runnable`) for the next actions, run
every tool it named (sequentially in :meth:`invoke`, concurrently in
:meth:`ainvoke`), append the ``(action, observation)`` pairs to the trail, and
stop on a final answer, on a single ``return_direct`` tool, or at
``max_iterations``. The agent runnable is streamed, so the token callbacks
fire while the model writes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from langchain_core.agents import AgentAction, AgentFinish, AgentStep
from langchain_core.callbacks import AsyncCallbackManagerForChainRun, CallbackManagerForChainRun
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import Runnable, RunnableConfig, RunnableSerializable
from langchain_core.runnables.config import (
    ensure_config, get_async_callback_manager_for_config, get_callback_manager_for_config,
)
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

log = logging.getLogger(__name__)

STOPPED_OUTPUT = "Agent stopped due to iteration limit or time limit."
PARSING_ERROR_OBSERVATION = "Invalid or incomplete response"

Plan = Union[AgentFinish, List[AgentAction]]


def _invalid_tool_observation(name: str, available: Sequence[str]) -> str:
    return f"{name} is not a valid tool, try one of [{', '.join(available)}]."


class LoopExecutor(RunnableSerializable[Dict[str, Any], Dict[str, Any]]):
    """The loop around an agent runnable and its tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    #: The planning runnable: inputs plus ``intermediate_steps`` in, a list of
    #: actions or an ``AgentFinish`` out.
    agent: Runnable
    tools: Sequence[BaseTool] = Field(default_factory=list)
    max_iterations: Optional[int] = 60
    return_intermediate_steps: bool = True
    #: A model answer the parser cannot read becomes an observation the model
    #: sees on the next pass instead of an exception.
    handle_parsing_errors: bool = True
    verbose: bool = False
    #: Stream the agent runnable so token callbacks fire while the model writes.
    stream_runnable: bool = True

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return False

    @property
    def _serialized(self) -> Dict[str, Any]:
        return {"lc": 1, "type": "not_implemented", "id": ["agents", "loop_executor", "LoopExecutor"],
                "name": "LoopExecutor"}

    # -- the public contract ---------------------------------------------------

    def invoke(self, input: Dict[str, Any], config: Optional[RunnableConfig] = None,  # noqa: A002
               **kwargs: Any) -> Dict[str, Any]:
        config = ensure_config(config)
        manager = get_callback_manager_for_config(config)
        run_manager = manager.on_chain_start(self._serialized, dict(input),
                                             name=config.get("run_name") or self.get_name())
        try:
            output = self._call(dict(input), run_manager)
        except BaseException as exc:
            run_manager.on_chain_error(exc)
            raise
        run_manager.on_chain_end(output)
        return output

    async def ainvoke(self, input: Dict[str, Any], config: Optional[RunnableConfig] = None,  # noqa: A002
                      **kwargs: Any) -> Dict[str, Any]:
        config = ensure_config(config)
        manager = get_async_callback_manager_for_config(config)
        run_manager = await manager.on_chain_start(self._serialized, dict(input),
                                                   name=config.get("run_name") or self.get_name())
        try:
            output = await self._acall(dict(input), run_manager)
        except BaseException as exc:
            await run_manager.on_chain_error(exc)
            raise
        await run_manager.on_chain_end(output)
        return output

    # -- planning --------------------------------------------------------------

    def _review(self, plan: Plan) -> Union[Plan, AgentStep]:
        """A hook between the model's answer and its execution: a subclass may
        turn a finish into a step with a synthetic observation
        (reasoning/think_gate.py)."""
        return plan

    def _parsing_error_step(self, exc: OutputParserException) -> AgentStep:
        if not self.handle_parsing_errors:
            raise ValueError(f"An output parsing error occurred: {exc}") from exc
        if exc.send_to_llm:
            observation, text = str(exc.observation), str(exc.llm_output)
        else:
            observation, text = PARSING_ERROR_OBSERVATION, str(exc)
        return AgentStep(action=AgentAction("_Exception", observation, text), observation=observation)

    def _plan(self, inputs: Dict[str, Any], steps: List[Any],
              run_manager: Optional[CallbackManagerForChainRun]) -> Union[Plan, AgentStep]:
        payload = {**inputs, "intermediate_steps": steps}
        config: RunnableConfig = {"callbacks": run_manager.get_child() if run_manager else None}
        try:
            if self.stream_runnable:
                plan: Any = None
                for chunk in self.agent.stream(payload, config=config):
                    plan = chunk if plan is None else plan + chunk
            else:
                plan = self.agent.invoke(payload, config=config)
        except OutputParserException as exc:
            return self._parsing_error_step(exc)
        return self._review(plan)

    async def _aplan(self, inputs: Dict[str, Any], steps: List[Any],
                     run_manager: Optional[AsyncCallbackManagerForChainRun]) -> Union[Plan, AgentStep]:
        payload = {**inputs, "intermediate_steps": steps}
        config: RunnableConfig = {"callbacks": run_manager.get_child() if run_manager else None}
        try:
            if self.stream_runnable:
                plan: Any = None
                async for chunk in self.agent.astream(payload, config=config):
                    plan = chunk if plan is None else plan + chunk
            else:
                plan = await self.agent.ainvoke(payload, config=config)
        except OutputParserException as exc:
            return self._parsing_error_step(exc)
        return self._review(plan)

    # -- tools -----------------------------------------------------------------

    def _tool_map(self) -> Dict[str, BaseTool]:
        return {t.name: t for t in self.tools}

    def _perform(self, by_name: Dict[str, BaseTool], action: AgentAction,
                 run_manager: Optional[CallbackManagerForChainRun]) -> AgentStep:
        if run_manager:
            run_manager.on_agent_action(action, color="green")
        tool = by_name.get(action.tool)
        if tool is None:
            observation: Any = _invalid_tool_observation(action.tool, list(by_name))
        else:
            observation = tool.run(action.tool_input, verbose=self.verbose, color=None,
                                   callbacks=run_manager.get_child() if run_manager else None)
        return AgentStep(action=action, observation=observation)

    async def _aperform(self, by_name: Dict[str, BaseTool], action: AgentAction,
                        run_manager: Optional[AsyncCallbackManagerForChainRun]) -> AgentStep:
        if run_manager:
            await run_manager.on_agent_action(action, color="green")
        tool = by_name.get(action.tool)
        if tool is None:
            observation: Any = _invalid_tool_observation(action.tool, list(by_name))
        else:
            observation = await tool.arun(action.tool_input, verbose=self.verbose, color=None,
                                          callbacks=run_manager.get_child() if run_manager else None)
        return AgentStep(action=action, observation=observation)

    # -- the loop --------------------------------------------------------------

    def _should_continue(self, iterations: int) -> bool:
        return self.max_iterations is None or iterations < self.max_iterations

    def _direct_return(self, by_name: Dict[str, BaseTool], new_steps: List[AgentStep]) -> Optional[AgentFinish]:
        """A single ``return_direct`` tool ends the turn with its output."""
        if len(new_steps) != 1:
            return None
        tool = by_name.get(new_steps[0].action.tool)
        if tool is not None and getattr(tool, "return_direct", False):
            return AgentFinish({"output": new_steps[0].observation}, "")
        return None

    def _result(self, finish: AgentFinish, steps: List[Any],
                run_manager: Optional[CallbackManagerForChainRun]) -> Dict[str, Any]:
        if run_manager:
            run_manager.on_agent_finish(finish, color="green", verbose=self.verbose)
        output = dict(finish.return_values)
        if self.return_intermediate_steps:
            output["intermediate_steps"] = steps
        return output

    async def _aresult(self, finish: AgentFinish, steps: List[Any],
                       run_manager: Optional[AsyncCallbackManagerForChainRun]) -> Dict[str, Any]:
        if run_manager:
            await run_manager.on_agent_finish(finish, color="green", verbose=self.verbose)
        output = dict(finish.return_values)
        if self.return_intermediate_steps:
            output["intermediate_steps"] = steps
        return output

    @staticmethod
    def _actions_of(plan: Any) -> List[AgentAction]:
        if isinstance(plan, AgentAction):
            return [plan]
        return list(plan or [])

    def _call(self, inputs: Dict[str, Any], run_manager: Optional[CallbackManagerForChainRun]) -> Dict[str, Any]:
        by_name = self._tool_map()
        steps: List[Any] = []
        iterations = 0
        while self._should_continue(iterations):
            plan = self._plan(inputs, steps, run_manager)
            if isinstance(plan, AgentFinish):
                return self._result(plan, steps, run_manager)
            if isinstance(plan, AgentStep):
                new_steps = [plan]
            else:
                new_steps = [self._perform(by_name, action, run_manager) for action in self._actions_of(plan)]
            steps.extend((s.action, s.observation) for s in new_steps)
            direct = self._direct_return(by_name, new_steps)
            if direct is not None:
                return self._result(direct, steps, run_manager)
            iterations += 1
        return self._result(AgentFinish({"output": STOPPED_OUTPUT}, ""), steps, run_manager)

    async def _acall(self, inputs: Dict[str, Any],
                     run_manager: Optional[AsyncCallbackManagerForChainRun]) -> Dict[str, Any]:
        by_name = self._tool_map()
        steps: List[Any] = []
        iterations = 0
        while self._should_continue(iterations):
            plan = await self._aplan(inputs, steps, run_manager)
            if isinstance(plan, AgentFinish):
                return await self._aresult(plan, steps, run_manager)
            if isinstance(plan, AgentStep):
                new_steps = [plan]
            else:
                new_steps = list(await asyncio.gather(
                    *[self._aperform(by_name, action, run_manager) for action in self._actions_of(plan)]))
            steps.extend((s.action, s.observation) for s in new_steps)
            direct = self._direct_return(by_name, new_steps)
            if direct is not None:
                return await self._aresult(direct, steps, run_manager)
            iterations += 1
        return await self._aresult(AgentFinish({"output": STOPPED_OUTPUT}, ""), steps, run_manager)


__all__ = ["LoopExecutor", "PARSING_ERROR_OBSERVATION", "STOPPED_OUTPUT"]
