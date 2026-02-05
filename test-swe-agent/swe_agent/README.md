# swe_agent

LLM‑агент для правок кода в песочнице (sandbox) на базе LangChain.

Пакет предоставляет:
- CLI `swe-agent` с файловыми инструментами для работы в пределах workspace (без LLM) и запуском агента (с LLM).
- Python API высокого уровня (`run_text`, `run_task`) и низкоуровневый запуск агента с LangChain‑обёртками и файловыми тулзами.

Этот README описывает требования, установку, конфигурацию, примеры CLI и Python API, доступные файловые инструменты и архитектуру.

---

## Требования и установка

- Python >= 3.10
- Для LLM‑режима требуется ключ OpenAI: `OPENAI_API_KEY`

Установка из исходников (локально):

```bash
# из корня репозитория
cd swe_agent
pip install -e .
```

Основные зависимости (см. `pyproject.toml`):
- langchain>=0.2.10
- langchain-openai>=0.1.7
- pydantic>=2.5
- PyYAML>=6.0

Переменные окружения для LLM:
- OPENAI_API_KEY — ключ OpenAI
- OPENAI_MODEL — имя модели (по умолчанию `gpt-4o-mini`)
- LLM_TEMPERATURE — температура (по умолчанию `0.0`)
- LLM_MAX_TOKENS — лимит токенов (по умолчанию `20000`)

Эти значения подхватываются через pydantic‑настройки (см. orchestrator/config.py). Можно использовать `.env` в корне репозитория.

---

## Конфигурация sandbox и лимитов

Конфигурация файловых операций и sandbox находится в `swe_agent/config.py` (глобальная конфигурация процесса). Ключевые параметры:
- workspace_root: корень рабочей директории. Все операции над файлами обязаны оставаться внутри этого каталога. Если не задан — используется текущий CWD.
- max_read_bytes: максимальный размер файла для чтения/поиска (по умолчанию 1_000_000 байт).
- ignore_globs: паттерны путей, игнорируемые при листинге/поиске (по умолчанию включает .git, .venv, __pycache__, и т.п.).
- allow_delete: разрешать ли удаления при применении патчей (по умолчанию True).
- binary_threshold: число байт для эвристики определения бинарных файлов (по умолчанию 4096).

Из кода конфигурация обновляется через:
```python
from swe_agent.config import update_config
update_config(workspace_root="/abs/path/to/ws", max_read_bytes=2_000_000)
```

CLI автоматически выставляет workspace из флага `--workspace` для подкоманд `fs`.

Ограничения sandbox (важно):
- Любые пути нормализуются и проверяются, что они остаются внутри `workspace_root`. Выход за пределы — ошибка.
- Бинарные/не‑UTF8 файлы и слишком большие файлы пропускаются или вызывают ошибку в зависимости от операции.
- Запись выполняется атомарно.

---

## CLI

Запуск:
```bash
python -m swe_agent.cli --help
# или установленный entrypoint
swe-agent --help
```

Глобальные флаги:
- `--workspace` — корень workspace для `fs`‑подкоманд. Рекомендуется всегда указывать.

Доступные команды:

1) Агенто‑зависимые (требуют LLM):
- `run TASK_ID` — запустить SWE‑агента по задаче из tasks.yaml (ищется через топ‑левел модуль `tools.tasks_tool`).
  Флаги: `--model`, `--temperature`, `--max-tokens`, `-v/--verbose`.
- `text "..."` — запустить SWE‑агента на свободный текст. Те же флаги модели.

2) Файловые операции (без LLM), подкоманда `fs`:
- `fs list-files [--glob "**/*"]` — вывести список файлов (JSON) внутри workspace с учётом ignore‑паттернов.
- `fs read-file PATH` — прочитать текстовый файл (JSON: {path, content}).
- `fs write-file PATH CONTENT` — записать/создать файл (JSON: {path}).
- `fs create-file PATH CONTENT` — создать файл атомарно под workspace (JSON: {path}).
- `fs apply-diff (--diff-text TEXT | --diff-file FILE)` — применить unified diff (JSON: {applied, files:[{path,op}]}).

Коды возврата:
- 0 — успех; для `fs` команд вывод всегда JSON в stdout.
- 1 — ошибка; текст ошибки печатается в stderr.

Минимальный quickstart (без LLM):
```bash
# возьмём пример workspace из репозитория
swe-agent --workspace examples/workspace fs list-files --glob "**/*.txt"

swe-agent --workspace examples/workspace fs write-file sub/new.txt "New content\n"
swe-agent --workspace examples/workspace fs read-file sub/new.txt

# применим unified diff (из строки)
DIFF=$'--- a/README.txt\n+++ b/README.txt\n@@ -1,2 +1,3 @@\n-Example workspace for swe_agent CLI e2e test.\n+Example workspace for swe_agent CLI e2e test (updated).\n This directory contains a few simple text files.\n+Third line here.\n'
swe-agent --workspace examples/workspace fs apply-diff --diff-text "$DIFF"
```

Пример запуска агента (с LLM):
```bash
export OPENAI_API_KEY=...  # обязателен для LLM
# свободная задача
swe-agent text "Добавь README для пакета swe_agent" --model gpt-4o-mini -v
# задача из tasks.yaml (при наличии)
swe-agent run DEV-INP-9-1 --model gpt-4o-mini
```

Подробный пример `apply-diff` (из файла):
```bash
swe-agent --workspace /path/to/ws fs apply-diff --diff-file /tmp/patch.diff
```

---

## Python API

Высокоуровневый API (упрощённый доступ к агенту):
```python
from swe_agent import run_text, run_task

# Свободная инструкция (LLM)
res = run_text("Исправь тесты в проекте", model="gpt-4o-mini", temperature=0.0, verbose=True)
if res.ok:
    print(res.agent_output)
else:
    print("Error:", res.error)

# По id задачи из tasks.yaml
res = run_task("DEV-INP-9-1", model="gpt-4o-mini")
```

Низкоуровневый запуск агента с файловыми инструментами в конкретном workspace:
```python
from pathlib import Path
from swe_agent.agent import run_task as run_worker

ws = Path("/abs/path/to/ws")
result = run_worker(
    instruction="Сделай правку X и добавь файл Y",
    workspace=ws,
    max_tool_calls=30,
)
print(result.ok, result.changed_files)
```

Модели результатов доступны в `swe_agent.models` (`AgentResult`, `ToolResult`).

---

## Доступные файловые инструменты и ограничения

Реализация (без LangChain‑обёрток):
- `swe_agent.tools.fs`:
  - `read_file(path)` — чтение UTF‑8 файла с проверкой лимитов/бинарности.
  - `write_file(path, content, create_dirs=True)` — атомарная запись в пределах workspace; возвращает относительный путь.
  - `list_files(glob="**/*", ignore=None)` — список файлов по glob с учётом ignore‑паттернов из конфигурации.
  - `search_text(pattern, file_glob)` — поиск по regex с соблюдением лимитов чтения и бинарной фильтрации.
- `swe_agent.tools.patch`:
  - `apply_unified_diff(diff_text, workspace)` — применяет unified diff (поддержка `a/`/`b/` префиксов, операций add/modify/delete). Делает dry‑run, проверяет применимость, пишет атомарно, умеет откатывать при сбое. Удаление контролируется `allow_delete` в конфиге.
  - `create_file(path, content, workspace=None)` — атомарное создание файла в workspace.
- LangChain‑обёртки для инструментов — в `swe_agent.tools.langchain_tools` (те же операции доступны агенту как tools).

Ограничения и политика безопасности:
- Все пути должны оставаться внутри `workspace_root` (строгая проверка `resolve`/`relative_to`).
- Бинарные/не‑UTF8 файлы игнорируются для чтения/поиска/патчей.
- `max_read_bytes` ограничивает чтение больших файлов.
- Запись/патчи выполняются атомарно; при ошибке — откат из временной копии (для патчей).

---

## Архитектура

- Агент: LangChain `create_tool_calling_agent` + `AgentExecutor` с системным промптом и набором файловых тулзов. Провайдер LLM — `langchain_openai.ChatOpenAI`.
- Тулзы: функции из `swe_agent.tools.*` и их LangChain‑адаптеры в `swe_agent.tools.langchain_tools`.
- Sandbox: политика ограничений в `swe_agent.config`, резолв путей и проверки в `tools/fs.py` и `tools/patch.py`.
- Взаимодействие с оркестратором см. в пакете `orchestrator` (есть адаптер запуска через CLI подкоманду `_run`).
- Публичный API: `swe_agent.api` (простые вызовы `run_text`, `run_task`), а также ленивые реэкспорты в `swe_agent/__init__.py`.

Схема потоков:
- CLI (`swe_agent/cli.py`) вызывает локальные инструменты напрямую (для `fs`) или собирает агент и запускает его через `agent.py`/`api.py` (для `run`/`text`).
- Инструменты строго ограничены рамками workspace.

---

## Основные файлы и модули

- `swe_agent/cli.py` — CLI entrypoint `swe-agent` и `python -m swe_agent.cli`.
- `swe_agent/api.py` — высокоуровневые функции `run_task`, `run_text` (используют LangChain‑агента).
- `swe_agent/agent.py` — сборка и запуск агента, инструменты, сбор шагов и изменённых файлов.
- `swe_agent/tools/fs.py` — базовые файловые операции внутри workspace.
- `swe_agent/tools/patch.py` — парсинг и применение unified diff, атомарные правки и откат.
- `swe_agent/tools/langchain_tools.py` — LangChain‑тулы поверх файловых функций.
- `swe_agent/config.py` — конфигурация sandbox/лимитов.
- `swe_agent/models.py` — `AgentResult`, `ToolResult`.
- `swe_agent/pyproject.toml` — метаданные пакета, зависимости, entrypoints.

---

## Пример workspace и e2e‑тест CLI

В репозитории есть пример рабочего каталога:
- `examples/workspace/`

И интеграционный тест CLI (его можно воспринимать как сценарий e2e):
- `tests/test_cli_e2e.py`

Запуск локально:
```bash
# Сценарий из теста руками
swe-agent --workspace examples/workspace fs list-files --glob "**/*.txt"
swe-agent --workspace examples/workspace fs write-file sub/new.txt "New content\n"
swe-agent --workspace examples/workspace fs read-file sub/new.txt

DIFF=$'--- a/README.txt\n+++ b/README.txt\n@@ -1,2 +1,3 @@\n-Example workspace for swe_agent CLI e2e test.\n+Example workspace for swe_agent CLI e2e test (updated).\n This directory contains a few simple text files.\n+Third line here.\n--- /dev/null\n+++ b/sub/hello.txt\n@@ -0,0 +1,2 @@\n+Hello\n+World\n'
swe-agent --workspace examples/workspace fs apply-diff --diff-text "$DIFF"

# Или запустить сам тест (при установленном pytest)
pytest -q tests/test_cli_e2e.py -q
```

---

## Примечания

- Команды `fs` всегда печатают JSON в stdout. Ошибки печатаются в stderr с префиксом `Error:` и кодом возврата 1.
- Команды `run`/`text` выводят текст ответа агента в stdout при успехе.
- Для `run TASK_ID` требуется файл `tasks.yaml`, который ищется через модуль `tools.tasks_tool` в корне репозитория. При отсутствии используйте `text`.
- Если вы пишете интеграции, смотрите также `orchestrator` пакет в этом репозитории (есть примеры запуска агента и адаптеры под задачи).
