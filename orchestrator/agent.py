"""
LangChain-powered Orchestrator Agent.

This agent is built on top of ChatOpenAI and connected to the local task
service via LangChain tools defined in `orchestrator.tools.langchain_tools`.

Responsibilities encoded in the system prompt:
- Create new tasks and update existing ones.
- Decompose high-level tasks into concrete subtasks and attach them to a parent task.
- When creating tasks/subtasks, set created_by=orchestrator.
- Stop or block tasks with clear reasons when appropriate.
- Form ordered execution sequences for a set of tasks.
- Manage execution agents: list available agents, assign/start an agent for a task,
  stop a running agent, and query agent status — with all changes persisted in tasks.

Public API:
- build_agent(...): returns an AgentExecutor ready to invoke.
- run_agent_once(text, ...): convenience wrapper to run the agent one-off.

Clear error is raised if OPENAI_API_KEY is not provided.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, List

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_tool_calling_agent
from dotenv import load_dotenv
from uuid import UUID

from common.agent_utils import build_chat_model
from .config import get_settings, require_openai_key
from .tools.langchain_tools import (
    create_task,
    add_subtask,
    get_task,
    list_tasks,
    update_task,
    stop_task,
    block_task,
    create_sequence,
    # Agent management tools
    list_agents_tool,
    assign_and_start_agent_tool,
    stop_agent_tool,
    get_agent_status_tool,
)

# Direct service imports for fallback behaviors
from .tasks_service import add_subtask as svc_add_subtask, list_tasks as svc_list_tasks

load_dotenv()


SYSTEM_PROMPT = (
    "Ты — оркестратор-разработчик. Твоя цель — поддерживать правильный жизненный "
    "цикл задач и помогать команде двигаться к результату. Действуй строго через инструменты.\n\n"
    "Что ты умеешь и должен делать: \n"
    "1) Создавать задачи (create_task) и подзадачи (add_subtask). При создании ВСЕГДА указывай created_by=orchestrator.\n"
    "   Для подзадач обязательно задавай parent_id.\n"
    "2) Останавливать задачи (stop_task) или блокировать их (block_task) с понятной причиной, когда это необходимо.\n"
    "3) Формировать последовательности выполнения (create_sequence) для набора задач, задавая порядок (order).\n"
    "4) Декомпозировать верхнеуровневые цели на небольшие, исполнимые подзадачи.\n"
    "5) Использовать list_tasks/get_task для ориентира в уже существующих задачах и update_task — для корректировок.\n"
    "6) Управлять ИСПОЛНИТЕЛЯМИ (агентами):\n"
    "   - list_agents_tool — посмотреть доступных агентов и их параметры;\n"
    "   - assign_and_start_agent_tool(task_id, agent_id, params_json) — назначить конкретного агента для задачи и запустить его;\n"
    "   - get_agent_status_tool(task_id) — проверить статус запуска;\n"
    "   - stop_agent_tool(task_id) — остановить выполнение агента для задачи.\n\n"
    "Правила: \n"
    "- Не выдумывай результат инструментов — дожидайся их ответа и используй возвращённые id/поля.\n"
    "- Создавай минимально необходимые, чёткие и проверяемые задачи.\n"
    "- created_by у новых задач должен быть orchestrator.\n"
    "- Если задача зависла, укажи причину блокировки.\n"
    "- Если задача готова к исполнению исполнителем, выбери подходящего агента (через list_agents_tool) и запусти его через assign_and_start_agent_tool.\n"
    "- В отчёте кратко перечисляй произведённые действия: созданные/обновлённые задачи (с id) и операции с агентами (какой agent_id, run_id, статус)."
)


def _collect_tools() -> Iterable[Any]:
    """Return the set of tools available to the orchestrator agent."""
    return [
        # Task tools
        create_task,
        add_subtask,
        get_task,
        list_tasks,
        update_task,
        stop_task,
        block_task,
        create_sequence,
        # Agent management tools
        list_agents_tool,
        assign_and_start_agent_tool,
        stop_agent_tool,
        get_agent_status_tool,
    ]


def build_agent(
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
) -> AgentExecutor:
    """Build and return an AgentExecutor wired with our tools and system prompt.

    Raises a clear error if OPENAI_API_KEY is missing.
    """
    st = get_settings()
    # Ensure key is present; raises a clear RuntimeError if missing
    api_key = require_openai_key(st)

    llm = build_chat_model(
        model=model or st.model,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
    )

    tools = list(_collect_tools())

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=verbose, handle_parsing_errors=True)
    return executor


def run_agent_once(
    text: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Convenience wrapper to build the agent and execute a single turn."""
    agent = build_agent(model=model, temperature=temperature, max_tokens=max_tokens, verbose=verbose)
    return agent.invoke({"input": text})


def run_decomposing_agent(
    task_id: str,
    task_title: str,
    task_description: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run the agent with a specific prompt for decomposing a task.

    If the agent fails or produces no subtasks, falls back to a simple
    deterministic decomposition to ensure progress.
    """
    prompt = (
        "Декомпозируй пользовательскую верхнеуровневую задачу на конкретные,"
        " небольшие и проверяемые подзадачи. Создавай подзадачи ТОЛЬКО через"
        f" инструмент add_subtask с parent_id={task_id}. Не создавай другие"
        " высокоуровневые задачи. При необходимости назначь последовательность"
        " выполнения (create_sequence) между созданными подзадачами.\\n\\n"
        f"Данные задачи:\\nID: {task_id}\\nTITLE: {task_title}\\nDESCRIPTION: {task_description}\\n\\n"
        "В конце выведи краткий отчёт о созданных подзадачах."
    )

    def _existing_subtasks() -> List[Dict[str, Any]]:
        try:
            pid = UUID(str(task_id))
        except Exception:
            return []
        items = []
        for t in svc_list_tasks():
            if getattr(t, "parent_id", None) == pid:
                d = t.model_dump() if hasattr(t, "model_dump") else t.dict()
                d["id"] = str(d.get("id"))
                if d.get("parent_id"):
                    d["parent_id"] = str(d.get("parent_id"))
                items.append(d)
        return items

    def _fallback_create() -> List[Dict[str, Any]]:
        """Create a minimal set of subtasks heuristically."""
        lines: List[str] = []
        for raw in (task_description or "").splitlines():
            s = raw.strip("-• *\t ")
            if s:
                lines.append(s)
        # If no bullet-like lines, synthesize a small plan
        if not lines:
            base = task_title.strip() or "Задача"
            lines = [
                f"Уточнить требования: {base}",
                f"Спланировать шаги для: {base}",
                f"Реализовать основную функциональность: {base}",
            ]
        created: List[Dict[str, Any]] = []
        for s in lines[:8]:  # cap to avoid spam
            try:
                st = svc_add_subtask(parent_id=UUID(str(task_id)), title=s, description="")
                d = st.model_dump() if hasattr(st, "model_dump") else st.dict()
                d["id"] = str(d.get("id"))
                if d.get("parent_id"):
                    d["parent_id"] = str(d.get("parent_id"))
                created.append(d)
            except Exception:
                continue
        return created

    try:
        result = run_agent_once(
            prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            verbose=verbose,
        )
    except Exception as e:
        # Agent unavailable; perform fallback decomposition
        created = _fallback_create()
        return {"output": f"fallback_decomposition_created={len(created)}", "created": created, "error": str(e)}

    # If the agent ran but produced no subtasks, ensure we create some
    subs = _existing_subtasks()
    if len(subs) == 0:
        created = _fallback_create()
        # Merge info into result
        if isinstance(result, dict):
            result = {**result, "fallback_created": len(created), "created": created}
    return result


__all__ = ["build_agent", "run_agent_once", "run_decomposing_agent", "SYSTEM_PROMPT"]
