from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json

from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable

from .config import get_settings
# Public result models
from .models import AgentResult, ToolResult
# Our LangChain tool adapters (filesystem-oriented)
from .tools.langchain_tools import get_default_tools
from common.agent_utils import build_chat_model, SharedProgressCallback

SYSTEM_PROMPT = (
    "Ты — SWE-агент-исполнитель.\n\n"
    "Твоя работа — исправить/дополнить код так, чтобы он удовлетворял условиям задачи.\n\n"
    "Правила:\n"
    "- Для чтения файлов используй инструмент read_file.\n"
    "- Для записи — write_file или apply_unified_diff (унифицированный патч).\n"
    "- Новые файлы создавай через create_file.\n"
    "- Работай ТОЛЬКО в пределах переданного workspace.\n"
    "- Генерируй полный текст файла при записи.\n"
    "- Заверши работу, когда считаешь, что задача выполнена.\n"
)


# -------------------- Helpers --------------------

def _collect_steps(intermediate_steps: Any) -> List[ToolResult]:
    steps: List[ToolResult] = []
    try:
        for s in intermediate_steps or []:
            action = None
            observation = None
            if isinstance(s, (tuple, list)) and len(s) >= 2:
                action, observation = s[0], s[1]
            else:
                action = getattr(s, "action", None) or getattr(s, "tool_input", None)
                observation = getattr(s, "observation", None)

            name = getattr(action, "tool", None) or getattr(action, "tool_name", None) or "?"
            args = getattr(action, "tool_input", None) or getattr(action, "args", None) or {}
            output = observation if isinstance(observation, str) else str(observation)
            steps.append(
                ToolResult(
                    name=str(name),
                    args=dict(args) if isinstance(args, dict) else {"input": args},
                    output=output,
                )
            )
    except Exception:
        pass
    return steps


def _extract_changed_files(steps: List[ToolResult]) -> List[str]:
    changed: List[str] = []
    for st in steps:
        try:
            data = json.loads(st.output)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        # write_file/create_file emit {path}
        if st.name in {"write_file", "create_file"}:
            p = data.get("path")
            if isinstance(p, str):
                changed.append(p)
        # apply_unified_diff emits {applied, files:[{path,op}]}
        if st.name == "apply_unified_diff":
            for fo in (data.get("files") or []):
                p = fo.get("path") if isinstance(fo, dict) else None
                if isinstance(p, str):
                    changed.append(p)
    # unique, keep order
    seen = set()
    uniq: List[str] = []
    for p in changed:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# -------------------- Build generic agent (legacy) --------------------
# Kept for compatibility with existing API wrappers.

def collect_tools() -> Iterable[Any]:
    """Deprecated in this module: kept for compatibility."""
    return get_default_tools()


def build_agent(
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
) -> Runnable:
    """Build a generic agent runnable with default tools (no fixed workspace)."""
    
    llm = build_chat_model(model=model, temperature=temperature, max_tokens=max_tokens)
    tools = list(get_default_tools())

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(
        llm,
        tools,
        prompt,
    )

    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=bool(verbose),
        handle_parsing_errors=True,
        max_iterations=40,
        return_intermediate_steps=True,
    )
    return executor


def run_agent_once(
    text: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    agent = build_agent(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        verbose=verbose,
    )
    return agent.invoke({"input": text})


# -------------------- Public worker entrypoint --------------------

def run_task(
    instruction: str,
    workspace: Path,
    system_prompt: Optional[str] = None,
    max_tool_calls: int = 30,
    verbose: bool = False,
) -> AgentResult:
    """Run a ReAct-like agent with filesystem tools in a given workspace."""
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    st = get_settings()

    llm = build_chat_model()
    tools = get_default_tools(workspace=str(ws))

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt or SYSTEM_PROMPT),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(
        llm,
        tools,
        prompt,
    )

    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=bool(verbose),
        handle_parsing_errors=True,
        max_iterations=int(max_tool_calls),
        return_intermediate_steps=True,
    )

    try:
        callbacks = [SharedProgressCallback(workspace=ws, model_name=st.model)]
        cfg: Dict[str, Any] = {"input": instruction}
        result = executor.invoke(cfg, config={"callbacks": callbacks} if callbacks else None)
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        steps = _collect_steps(result.get("intermediate_steps")) if isinstance(result, dict) else []
        changed_files = _extract_changed_files(steps)
        if verbose and changed_files:
            try:
                print("[SWE] Changed files:")
                for f in changed_files:
                    print(f"  - {f}")
            except Exception:
                pass
        return AgentResult(
            ok=True,
            status="done",
            agent_output=str(output),
            steps=steps,
            changed_files=changed_files,
        )
    except Exception as e:
        return AgentResult(ok=False, status="error", error=str(e))
