# Orchestrator

Файловый оркестратор задач и агентов. Позволяет:
- заводить задачи верхнего уровня и декомпозировать их на подзадачи LLM‑агентом;
- хранить задачи в одном JSON‑файле с безопасной блокировкой (file lock);
- управлять статусами задач (todo/in_progress/blocked/stopped/done);
- назначать и запускать исполняющих агентов для конкретных задач;
- следить за запусками агентов, останавливать их;
- работать через простой CLI и (опционально) через фоновый демон‑раннер.


## Требования и подготовка окружения

- Python: 3.10+
- Зависимости: см. requirements.txt в корне репозитория. Быстрый способ установить в активированном venv:
  pip install -r requirements.txt

Переменные окружения (используются orchestrator.config.Settings):
- OPENAI_API_KEY — ключ OpenAI (обязателен для LLM‑функций). Может храниться в .env.
- OPENAI_MODEL — модель по умолчанию (например gpt-4o-mini). Необязательно.
- LLM_TEMPERATURE — температура запроса (float). Необязательно.
- LLM_MAX_TOKENS — лимит токенов (int). Необязательно.

Переменные для демона‑раннера (orchestrator.runner):
- ORCH_POLL_INTERVAL — интервал опроса задач в секундах (float, по умолчанию 5.0).
- ORCH_LOG_LEVEL — уровень логирования (DEBUG|INFO|WARNING|ERROR), по умолчанию INFO.

Где хранится база задач:
- tasks.json хранится централизованно в каталоге tasks/: tasks/tasks.json.
- Хранилище — локальный файл с блокировкой через filelock (без БД).


## Быстрый старт (CLI)

Примеры команд:
- Создать задачу верхнего уровня:
  python -m orchestrator.cli add "Сделать X" --desc "Краткое описание"
  # По умолчанию после создания запускается LLM‑агент для декомпозиции. Чтобы отключить — добавьте --no-decompose

- Показать список задач:
  python -m orchestrator.cli list

- Остановить задачу:
  python -m orchestrator.cli stop <TASK_ID>

- Заблокировать задачу с причиной:
  python -m orchestrator.cli block <TASK_ID> --reason "Почему заблокирована"

- Создать последовательность выполнения для набора задач (в указанном порядке):
  python -m orchestrator.cli sequence my-seq <TASK_ID1> <TASK_ID2> <TASK_ID3>

Агенты и управление задачами с агентами:
- Список доступных агентов из реестра:
  python -m orchestrator.cli agents list

- Список задач с информацией об агенте и последнем запуске:
  python -m orchestrator.cli tasks list

- Назначить агент и сразу запустить его для задачи:
  python -m orchestrator.cli tasks assign <TASK_ID> <AGENT_ID> --params '{"k": "v"}'
  # --params — необязательный JSON‑объект с параметрами агента
  # --foreground — запустить в fg-режиме с выводом в консоль

- Посмотреть состояние/запуск для задачи:
  python -m orchestrator.cli tasks status <TASK_ID>

- Остановить последний активный запуск агента по задаче:
  python -m orchestrator.cli tasks stop <TASK_ID>

Фоновый демон (планировщик‑раннер):
- Запустить как модуль:
  python -m orchestrator.runner
- Или через тонкий входной скрипт:
  orchestrator/orchestratord


## Конфигурация и структура данных

Формат файла tasks/tasks.json — список объектов Task. Модель (см. tasks/storage.py):
- id: UUID
- title: str
- description: str
- status: one of [todo, in_progress, blocked, stopped, done]
- created_by: one of [user, orchestrator]
- parent_id: UUID | null (если это подзадача)
- sequence_id: str | null (идентификатор последовательности)
- order: int | null (порядковый номер в последовательности)
- blocked_reason: str | null
- assigned_agent_type: str | null (идентификатор агента)
- assigned_agent_params: dict | null
- assigned_agent_run_id: str | null (ID внешнего запуска)
- agent_state: one of [none, assigned, running, stopped, completed, failed]
- created_at: ISO‑время
- updated_at: ISO‑время

Пример записи в JSON:
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

Где лежит файл и как менять путь/конфиг:
- По умолчанию используется tasks/tasks.json. Путь можно переопределить через переменную окружения TASKS_FILE или параметр --tasks-file в CLI.
- Продвинутый сценарий: импортируйте TaskStore (tasks.storage.TaskStore) и создайте собственный экземпляр с путём, затем передавайте его в сервис‑функции (store=...).


## Архитектура и основные модули

- orchestrator/tasks_service.py — высокоуровневый сервис поверх хранилища: CRUD, подзадачи, статусы, последовательности, назначение агента и его состояние.
- orchestrator/tools/langchain_tools.py — LangChain‑инструменты для управления задачами (create_task, add_subtask, list/get/update/stop/block, create_sequence, а также операции с агентами). Эти инструменты доступны LLM‑агенту.
- orchestrator/agent.py — построение и однократный запуск LLM‑агента (LangChain + OpenAI) для декомпозиции задач.
- orchestrator/agents/* — реестр агентов, их описания и обёртки (registry.py, swe_runner.py, dummy_agent.py), менеджер запусков (run_manager.py).
- orchestrator/cli.py — пользовательский CLI для задач и управления агентами.
- orchestrator/runner.py — фоновый планировщик, подбирает новые задачи и запускает оркестратор‑агента.
- orchestrator/orchestratord — тонкая консольная обёртка для runner.
- orchestrator/config.py — настройки через pydantic BaseSettings (ENV или .env).


## Ограничения и планы

- Хранилище — локальный JSON‑файл; нет БД, нет сетевого доступа, одна машина.
- Конкурентный доступ защищён file lock, но это не распределённый сценарий.
- Агент зависит от OpenAI API; без OPENAI_API_KEY LLM‑часть недоступна (задачи всё равно можно создавать и редактировать).
- Путь к tasks.json в CLI пока не конфигурируется.

Планируемая интеграция с агентами и расширение возможностей — см. открытые задачи серий DEV-INP-6-x и DEV-INP-7-x в этом репозитории.


## Troubleshooting

- Ошибка: OPENAI_API_KEY is not set / Agent skipped / Agent prerequisites missing
  Решение: установите ключ в окружение:
    export OPENAI_API_KEY=sk-...          # macOS/Linux
    setx OPENAI_API_KEY "sk-..."          # Windows PowerShell
  Можно создать файл .env рядом с проектом — orchestrator.config читает его автоматически.

- Агент не запускается: ImportError langchain/openai
  Решение: установите зависимости из корневого requirements.txt (в venv):
    pip install -r requirements.txt

 - Задачи не сохраняются / Permission denied при записи tasks.json
  Решение: убедитесь, что у процесса есть права на запись в каталог tasks/; при необходимости измените расположение, задав TASKS_FILE или используя собственный TaskStore.

 - Неверный путь к tasks.json
  По умолчанию используется tasks/tasks.json (или путь из TASKS_FILE). Убедитесь, что директория существует — файл будет создан автоматически при первом сохранении.

- Повреждённый tasks.json (невалидный JSON)
  Решение: остановите процессы, сделайте резервную копию файла, вручную исправьте структуру или удалите файл — модуль создаст пустой список задач. Запись выполняется атомарно, но при ручном редактировании можно нарушить формат.

- Демон не обрабатывает задачи
  Проверьте переменные ORCH_LOG_LEVEL/ORCH_POLL_INTERVAL, логи и наличие OPENAI_API_KEY. Демон подбирает только верхнеуровневые задачи со статусом todo, созданные пользователем (created_by=user).


## Лицензия и вклад

Файл предназначен как внутренняя документация пакета orchestrator в рамках текущего репозитория. Предложения по улучшению — в виде PR/issue.
