# Unified AI Software Development Agency

This repository combines two powerful AI agent projects into a single, unified framework for automated software development. It features a sophisticated Orchestrator, specialized SWE agents for codebase modification, and a complete "Agent Factory" with a visual graph-based workflow.

## Key Features

- **Unified Orchestrator**: Manage complex tasks and subtasks with a centralized agent that coordinates specialized executors.
- **SWE Agent**: An expert developer agent equipped with filesystem and patching tools to solve specific engineering tasks.
- **Agent Factory**: A multi-agent system comprising PM, BA, SD, TL, Dev, QA, and Ops agents that can follow a full development lifecycle.
- **Visual Graph Editor**: Design and execute custom agent workflows via a React Flow-powered dashboard.
- **Workspace Management**: Isolated environments for each task where all artifacts (docs, code, logs) are stored.

## Repository Structure

- `orchestrator/`: Core task management and agent coordination logic.
- `swe_agent/`: Specialized SWE agent with filesystem and patching tools.
- `agents/`: Factory agents (PM, BA, SD, TL, Backend, Frontend, QA, DevOps).
- `dashboard/`: Unified FastAPI backend and React/Vite/Tailwind frontend.
- `tasks/`: Task data models and storage.
- `common/`: Shared utilities and configuration.
- `workspaces/`: (Generated) Isolated working directories for agent tasks.

## Setup

### Prerequisites

- Python 3.10+
- Node.js & npm
- OpenAI API Key (set as `OPENAI_API_KEY` environment variable)

### Installation

1. Install Python dependencies:
   ```bash
   pip install -r dashboard/backend/requirements.txt
   pip install -r requirements.txt
   ```

2. Install Frontend dependencies:
   ```bash
   cd dashboard/frontend
   npm install
   ```

## Running the Project

### 1. Start the Dashboard

Backend:
```bash
export PYTHONPATH=$PYTHONPATH:.
python3 dashboard/backend/main.py
```
The API will be available at http://localhost:8000.

Frontend:
```bash
cd dashboard/frontend
npm run dev
```
The dashboard will be available at http://localhost:5173.

### 2. CLI Usage

You can also run agents directly from the CLI:

SWE Agent:
```bash
python3 -m swe_agent.cli text "Add a README to the project" --workspace /path/to/ws
```

Factory Graph:
```bash
python3 run_graph.py --desc "Build a simple weather app" --workspace /path/to/ws
```

## Agent Factory Workflow

The default Factory Graph follows this process:
1. PM Intake: Clarifies project requirements.
2. BA Generate BRD: Produces a Business Requirements Document.
3. SD Generate Spec: Creates technical specifications and OpenAPI contracts.
4. TL Choose Stack & Split: Decisions on tech stack and task breakdown.
5. Dev Execution: Backend and Frontend implementation.
6. QA & Ops: Automated testing and deployment artifacts.

You can customize this flow in the Agent Factory tab of the dashboard.

---

## Orchestrator (RU)

Файловый оркестратор задач и агентов. Позволяет:
- заводить задачи верхнего уровня и декомпозировать их на подзадачи LLM‑агентом;
- хранить задачи в одном JSON‑файле с безопасной блокировкой (file lock);
- управлять статусами задач (todo/in_progress/blocked/stopped/done);
- назначать и запускать исполняющих агентов для конкретных задач;
- следить за запусками агентов, останавливать их;
- работать через простой CLI и (опционально) через фоновый демон‑раннер.

### Требования и подготовка окружения

- Python: 3.10+
- Зависимости: см. requirements.txt в корне репозитория. Быстрый способ установить в активированном venv:
  `pip install -r requirements.txt`

Переменные окружения (используются orchestrator.config.Settings):
- `OPENAI_API_KEY` — ключ OpenAI (обязателен для LLM‑функций). Может храниться в .env.
- `OPENAI_MODEL` — модель по умолчанию (например gpt-4o-mini). Необязательно.
- `LLM_TEMPERATURE` — температура запроса (float). Необязательно.
- `LLM_MAX_TOKENS` — лимит токенов (int). Необязательно.

Переменные для демона‑раннера (orchestrator.runner):
- `ORCH_POLL_INTERVAL` — интервал опроса задач в секундах (float, по умолчанию 5.0).
- `ORCH_LOG_LEVEL` — уровень логирования (DEBUG|INFO|WARNING|ERROR), по умолчанию INFO.

Где хранится база задач:
- tasks.json хранится централизованно в каталоге tasks/: `tasks/tasks.json`.
- Хранилище — локальный файл с блокировкой через filelock (без БД).

### Быстрый старт (CLI)

Примеры команд:
- Создать задачу верхнего уровня:
  `python -m orchestrator.cli add "Сделать X" --desc "Краткое описание"`
  (по умолчанию после создания запускается LLM‑агент для декомпозиции; чтобы отключить — добавьте `--no-decompose`)

- Показать список задач:
  `python -m orchestrator.cli list`

- Остановить задачу:
  `python -m orchestrator.cli stop <TASK_ID>`

- Заблокировать задачу с причиной:
  `python -m orchestrator.cli block <TASK_ID> --reason "Почему заблокирована"`

- Создать последовательность выполнения для набора задач (в указанном порядке):
  `python -m orchestrator.cli sequence my-seq <TASK_ID1> <TASK_ID2> <TASK_ID3>`

Агенты и управление задачами с агентами:
- Список доступных агентов из реестра:
  `python -m orchestrator.cli agents list`

- Список задач с информацией об агенте и последнем запуске:
  `python -m orchestrator.cli tasks list`

- Назначить агент и сразу запустить его для задачи:
  `python -m orchestrator.cli tasks assign <TASK_ID> <AGENT_ID> --params '{"k": "v"}'`
  (`--params` — необязательный JSON‑объект с параметрами агента; `--foreground` — запуск в fg-режиме)

- Посмотреть состояние/запуск для задачи:
  `python -m orchestrator.cli tasks status <TASK_ID>`

- Остановить последний активный запуск агента по задаче:
  `python -m orchestrator.cli tasks stop <TASK_ID>`

Фоновый демон (планировщик‑раннер):
- Запустить как модуль: `python -m orchestrator.runner`
- Или через тонкий входной скрипт: `orchestrator/orchestratord`

### Конфигурация и структура данных

Формат файла `tasks/tasks.json` — список объектов Task. Модель (см. `tasks/storage.py`):
- `id: UUID`
- `title: str`
- `description: str`
- `status: one of [todo, in_progress, blocked, stopped, done]`
- `created_by: one of [user, orchestrator]`
- `parent_id: UUID | null`
- `sequence_id: str | null`
- `order: int | null`
- `blocked_reason: str | null`
- `assigned_agent_type: str | null`
- `assigned_agent_params: dict | null`
- `assigned_agent_run_id: str | null`
- `agent_state: one of [none, assigned, running, stopped, completed, failed]`
- `created_at: ISO‑время`
- `updated_at: ISO‑время`

Пример записи в JSON:
```json
[
  {
    "id": "9b0a0a0a-1111-2222-3333-444444444444",
    "title": "Сделать X",
    "description": "Краткое описание",
    "status": "todo",
    "created_by": "user",
    "parent_id": null,
    "sequence_id": null,
    "order": null,
    "blocked_reason": null,
    "assigned_agent_type": null,
    "assigned_agent_params": null,
    "assigned_agent_run_id": null,
    "agent_state": "none",
    "created_at": "2025-01-01T10:00:00+00:00",
    "updated_at": "2025-01-01T10:00:00+00:00"
  }
]
```

Где лежит файл и как менять путь/конфиг:
- По умолчанию используется `tasks/tasks.json`. Путь можно переопределить через переменную окружения `TASKS_FILE` или параметр `--tasks-file` в CLI.
- Продвинутый сценарий: импортируйте `TaskStore` (`tasks.storage.TaskStore`) и создайте собственный экземпляр с путём, затем передавайте его в сервис‑функции (`store=...`).

### Архитектура и основные модули

- `orchestrator/tasks_service.py` — высокоуровневый сервис поверх хранилища: CRUD, подзадачи, статусы, последовательности, назначение агента и его состояние.
- `orchestrator/tools/langchain_tools.py` — LangChain‑инструменты для управления задачами (create_task, add_subtask, list/get/update/stop/block, create_sequence, а также операции с агентами). Эти инструменты доступны LLM‑агенту.
- `orchestrator/agent.py` — построение и однократный запуск LLM‑агента (LangChain + OpenAI) для декомпозиции задач.
- `orchestrator/agents/*` — реестр агентов, их описания и обёртки (registry.py, swe_runner.py, dummy_agent.py), менеджер запусков (run_manager.py).
- `orchestrator/cli.py` — пользовательский CLI для задач и управления агентами.
- `orchestrator/runner.py` — фоновый планировщик, подбирает новые задачи и запускает оркестратор‑агента.
- `orchestrator/orchestratord` — тонкая консольная обёртка для runner.
- `orchestrator/config.py` — настройки через pydantic BaseSettings (ENV или .env).

### Ограничения и планы

- Хранилище — локальный JSON‑файл; нет БД, нет сетевого доступа, одна машина.
- Конкурентный доступ защищён file lock, но это не распределённый сценарий.
- Агент зависит от OpenAI API; без `OPENAI_API_KEY` LLM‑часть недоступна (задачи всё равно можно создавать и редактировать).
- Путь к `tasks.json` в CLI пока не конфигурируется.

Планируемая интеграция с агентами и расширение возможностей — см. открытые задачи серий DEV-INP-6-x и DEV-INP-7-x в этом репозитории.

### Troubleshooting

- Ошибка: `OPENAI_API_KEY is not set` / Agent skipped / Agent prerequisites missing
  - Решение: установите ключ в окружение:
    - macOS/Linux: `export OPENAI_API_KEY=sk-...`
    - Windows PowerShell: `setx OPENAI_API_KEY "sk-..."`
  - Можно создать файл `.env` рядом с проектом — orchestrator.config читает его автоматически.

- Агент не запускается: `ImportError langchain/openai`
  - Решение: установите зависимости из корневого `requirements.txt` (в venv): `pip install -r requirements.txt`

- Задачи не сохраняются / `Permission denied` при записи `tasks.json`
  - Решение: убедитесь, что у процесса есть права на запись в каталог `tasks/`; при необходимости измените расположение, задав `TASKS_FILE` или используя собственный `TaskStore`.

- Неверный путь к `tasks.json`
  - По умолчанию используется `tasks/tasks.json` (или путь из `TASKS_FILE`). Убедитесь, что директория существует — файл будет создан автоматически при первом сохранении.

- Повреждённый `tasks.json` (невалидный JSON)
  - Решение: остановите процессы, сделайте резервную копию файла, вручную исправьте структуру или удалите файл — модуль создаст пустой список задач. Запись выполняется атомарно, но при ручном редактировании можно нарушить формат.

- Демон не обрабатывает задачи
  - Проверьте переменные `ORCH_LOG_LEVEL`/`ORCH_POLL_INTERVAL`, логи и наличие `OPENAI_API_KEY`. Демон подбирает только верхнеуровневые задачи со статусом `todo`, созданные пользователем (`created_by=user`).

### Лицензия и вклад

Файл объединяет документацию по проекту и оркестратору в одном месте. Предложения по улучшению — через PR/issue.
