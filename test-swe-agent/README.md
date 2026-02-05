# SWE Orchestrator & SWE Agent — монорепозиторий

Этот репозиторий содержит два тесно связанных пакета:
- orchestrator — минимальный оркестратор задач с CLI и долгоживущим раннером (демоном).
- swe_agent — SWE‑агент-исполнитель с инструментами работы с файловой системой и патчами; может запускаться как самостоятельный CLI или под управлением оркестратора.

Цель проекта — показать простой, воспроизводимый пайплайн: постановка верхнеуровневой задачи, её разбиение/планирование и исполнение в изолированном рабочем каталоге (workspace) с инструментами чтения/записи файлов и применения унифицированных патчей.


## Структура репозитория

- orchestrator/ — пакет оркестратора
  - cli.py — CLI утилита управления задачами и агентами (`python -m orchestrator.cli`)
  - runner.py — долгоживущий раннер-демон (`python -m orchestrator.runner` или сценарий orchestrator/orchestratord)
  - agents/ — реестр и адаптеры агентов (включая swe_agent)
    - agents.json — конфигурация реестра агентов
    - run_manager.py — менеджер запусков/процессов агентов
    - swe_runner.py — адаптер запуска swe_agent
  - tasks_service.py — сервис для задач (хранение — общий файл tasks/tasks.json)
  - README.md — подробности по пакету оркестратора
- swe_agent/ — пакет SWE‑агента
  - agent.py — «рабочий» агент с инструментами FS/patch, API `run_task(...)`
  - tools/ — инструменты чтения/записи, поиска по тексту, применения unified diff
  - cli.py — CLI агента (`swe-agent` или `python -m swe_agent.cli`)
  - README.md — подробности по пакету SWE‑агента
- dashboard/ — веб-интерфейс для управления задачами и агентами
  - backend/ — FastAPI сервер для API
  - frontend/ — React + Vite фронтенд

Полезные ссылки:
- Док по оркестратору: ./orchestrator/README.md
- Док по SWE‑агенту: ./swe_agent/README.md
- Примеры: ./examples/
- Тесты: ./tests/


## Требования и установка

- Поддерживаемая версия Python: 3.10+ (рекомендовано 3.10–3.13)
- ОС: Linux/macOS/Windows (на Windows поведение сигналов/демона отличается; см. ограничения)

Установка из исходников (рекомендуется для разработки):

1) Создайте виртуальное окружение и активируйте его
   - python -m venv .venv
   - source .venv/bin/activate  # PowerShell: .venv\Scripts\Activate.ps1

2) Установите зависимости оркестратора
   - pip install -r orchestrator/requirements.txt

3) Установите swe_agent в editable-режиме (чтобы его можно было импортировать из подпроцесса)
   - pip install -e ./swe_agent

После этого доступны:
- CLI оркестратора: python -m orchestrator.cli
- Раннер-демон: python -m orchestrator.runner (или сценарий orchestrator/orchestratord)
- CLI SWE‑агента: swe-agent (или python -m swe_agent.cli)


## Конфигурация

Переменные окружения (основные):
- OPENAI_API_KEY — ключ для LLM (обязателен для LLM‑функций: decomposition/планирование, запуск SWE‑агента с LLM)
- OPENAI_MODEL — модель по умолчанию (например, gpt-4o-mini)
- LLM_TEMPERATURE — температура по умолчанию (float)
- LLM_MAX_TOKENS — лимит токенов по умолчанию (int)
- ORCH_POLL_INTERVAL — интервал опроса раннера (сек, float; по умолчанию 5.0)
- ORCH_LOG_LEVEL — уровень логирования раннера (DEBUG|INFO|WARNING|ERROR; по умолчанию INFO)

.env файл поддерживается (см. orchestrator/config.py).

Конфиги агентов/раннера:
- Реестр агентов: orchestrator/agents/agents.json
- Хранилище задач: tasks/tasks.json (создаётся/обновляется автоматически; можно переопределить через TASKS_FILE или флаг --tasks-file)
- Параметры swe_agent (ограничения FS) можно переопределять через параметры назначаемого агента (например, workspace, max_read_bytes, ignore_globs, allow_delete) — см. orchestrator/agents/swe_runner.py


## Быстрый старт

Ниже — два сценария: с LLM (требуется OPENAI_API_KEY) и без него.

Подготовка workspace (общая для обоих):
- mkdir -p /tmp/my_ws
- cp -r examples/workspace/* /tmp/my_ws/

1) Запустить раннер (демон)
- ORCH_POLL_INTERVAL=0.5 ORCH_LOG_LEVEL=DEBUG python -m orchestrator.runner
  - Остановить: Ctrl+C (или сигнал SIGINT/SIGTERM)

2) Поставить верхнеуровневую задачу
- В отдельном терминале: python -m orchestrator.cli add "Добавить README проекта" --desc "Создать корневой README"  # добавит задачу и запустит агент-декомпозитор
- Или: python -m orchestrator.cli add "Сделать TODO list" --desc "Сделать простой планировщик задач" --no-decompose
  - Если нет OPENAI_API_KEY, используйте флаг: --no-agent (агент будет пропущен)

3) Посмотреть список задач и статусы
- python -m orchestrator.cli list
- python -m orchestrator.cli tasks list

4) (Опционально) Назначить SWE‑агента на существующую задачу и запустить его
- Узнайте ID задачи (UUID из вывода list/tasks list)
- Убедитесь, что swe_agent установлен (pip install -e ./swe_agent)
- Запустите:
  - python -m orchestrator.cli agents list
  - python -m orchestrator.cli tasks assign TASK_ID swe-fs --params '{"workspace":"/tmp/my_ws"}'
- Проверить статус:
  - python -m orchestrator.cli tasks status TASK_ID
- Остановить агента:
  - python -m orchestrator.cli tasks stop TASK_ID

5) Остановить раннер
- Вернитесь к терминалу с раннером и нажмите Ctrl+C

Примечание: если не задан OPENAI_API_KEY, раннер при попытке планирования пометит задачу как blocked (см. tests/test_runner_integration.py) — это штатно.

### Использование Дашборда

Дашборд позволяет управлять задачами и агентами через веб-интерфейс.

1) Запуск бэкенда:
   - pip install -r dashboard/backend/requirements.txt
   - python3 -m uvicorn dashboard.backend.main:app --reload

2) Запуск фронтенда:
   - cd dashboard/frontend
   - npm install
   - npm run dev

После запуска фронтенд доступен по адресу http://localhost:5173.


## Примеры

Папка ./examples/ содержит минимальный workspace (./examples/workspace/). Вы можете скопировать его куда‑либо и использовать как рабочую директорию для SWE‑агента (см. шаги выше). CLI SWE‑агента также позволяет выполнять операции без LLM над файлами workspace:
- swe-agent fs list-files --workspace /tmp/my_ws --glob "**/*"
- swe-agent fs read-file --workspace /tmp/my_ws README.txt
- swe-agent fs write-file --workspace /tmp/my_ws notes.txt "Hello"
- swe-agent fs apply-diff --workspace /tmp/my_ws --diff-file patch.diff

Минимальный LLM‑сценарий без оркестратора (требуется OPENAI_API_KEY):
- swe-agent text "Добавь файл STORY.md с кратким описанием" --verbose --max-tokens 200
  - По умолчанию workspace — текущая директория процесса; можно задать через API или запускать из нужной папки.

Подробнее см. README пакетов:
- Оркестратор: ./orchestrator/README.md
- SWE‑агент: ./swe_agent/README.md


## Тесты

Запуск всех тестов:
- pytest -q

Фильтрация e2e тестов:
- Только e2e: pytest -q -k e2e
- Исключить e2e: pytest -q -k "not e2e"

Некоторые e2e‑тесты запускают подпроцессы и работают с файлами в репозитории. Они не требуют OPENAI_API_KEY (агенты пропускаются/блокируются предсказуемо), но для локального прогона с LLM установите переменные окружения.


## Основные компоненты/файлы

- orchestrator/
  - cli.py — команды: add/list/stop/block/sequence и подкоманды tasks/agents
  - runner.py — циклический опрос задач и запуск планирующего агента; переменные окружения ORCH_POLL_INTERVAL/ORCH_LOG_LEVEL
  - tasks_service.py — бизнес‑операции над задачами
  - tools/langchain_tools.py — LangChain‑инструменты для управления задачами/агентами
  - agents/registry.py — загрузка реестра агентов из agents.json
  - agents/run_manager.py — запускает/останавливает подпроцессы агентов, отслеживает статусы
  - agents/swe_runner.py — формирует RunSpec для запуска swe_agent (через API или CLI)
- swe_agent/
  - agent.py — LLM‑агент с инструментами FS/patch, API run_task(...)
  - tasks_service.py — высокоуровневый сервис задач на базе общего TaskStore
  - tools/fs.py, tools/patch.py, tools/langchain_tools.py — инструменты работы с файлами и патчами
  - cli.py — CLI агента (подкоманды fs/... и LLM‑команды run/text)
  - orchestrator_adapter.py — вызов агента из «задачи» оркестратора

Ссылки:
- README оркестратора — ./orchestrator/README.md
- README SWE‑агента — ./swe_agent/README.md


## Известные ограничения

- Для LLM‑функций требуется OPENAI_API_KEY. Без него:
  - CLI оркестратора add запустит агент только при наличии ключа (иначе используйте --no-agent)
  - Раннер помечает новые задачи как blocked с понятной причиной
- Sandbox ограничен рабочей директорией (workspace), но destructive‑операции (удаление/перезапись) допускаются по умолчанию — контролируйте allow_delete и пути.
- На Windows обработка сигналов/завершения подпроцессов отличается; используйте tasks stop и/или ручное завершение процессов.
- Для запуска агента из оркестратора swe_agent должен быть импортируем (т.е. установлен: pip install -e ./swe_agent).
- Сеть/внешние вызовы LLM могут быть ограничены вашей средой (CI/sandbox) — учитывайте это при воспроизведении примеров.


## Лицензия и вклад

PR и улучшения приветствуются. Стиль кода — максимально простой и прозрачный для чтения; тесты — через pytest.
