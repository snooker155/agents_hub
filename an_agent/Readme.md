# AN-Agent (AN Agent по Sifakis) — скелет проекта

Этот репозиторий — инженерный каркас для реализации подхода **AN Agent** из статьи (arXiv: 2509.08312).
Архитектура строится вокруг центрального **Workflow Coordinator Runtime**, двух контуров поведения
(**Reactive** и **Proactive**), а также **гибридного представления знаний**:
- векторная память (FAISS/векторное хранилище),
- граф знаний (Neo4j/онтология),
- правила/ограничения (rule engine),
- RAG (retrieval-augmented generation) и (опционально) LLM-инструменты в *холодном контуре*.

Ключевая инженерная цель: поддержать **быстрый реактивный контур** (условно sub-10ms для online control),
а все тяжёлые операции (LLM, глубокие рассуждения, переобучения) вынести в **проактивный** и/или фоновые процессы.

---

## Возможности (на уровне каркаса)

- **Coordinator**: оркестрация событий, запуск/останов контуров, SLA/дедлайны.
- **Reactive Runtime**: perception → retrieve → predict → propose goals → plan → validate → act.
- **Proactive Runtime**: self-awareness → intent/meta-goals → choice-making → обновление режима/порогов/reward.
- **Knowledge**: долговременная память + правила + RAG-склейка контекста.
- **Models**: фильтры/прогнозы (Kalman/LSTM), policy (MLP/DQN), reward shaping.
- **Interfaces**: адаптер телеметрии и southbound управление (моки/плейсхолдеры).

---

## Быстрый старт

### Установка (dev)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"

---

## Запуск офлайн-эксперимента (плейсхолдер)
```bash
python -m experiments.ran_la_agent.run_offline --config experiments/ran_la_agent/configs/embb.yaml

### Как запустить (после того как файлы на месте)
```bash
python -m experiments.ran_la_agent.run_offline --config experiments/ran_la_agent/configs/embb.yaml --ticks 500
python -m experiments.ran_la_agent.run_offline --config experiments/ran_la_agent/configs/urllc.yaml --ticks 500
python -m experiments.ran_la_agent.evaluate --db ./data/an_agent.db

---

## Структура проекта
```bash
an_agent/
  README.md
  pyproject.toml

  config/
    default.yaml
    rag.yaml
    models.yaml
    safety.yaml

  an_agent_core/
    __init__.py
    types.py              # общие dataclass/типы: Observation, Goal, Action, WorldState, Decision...
    clock.py              # монотонные таймеры, дедлайны, профилирование latency
    events.py             # event-bus/очередь событий + типы событий
    coordinator.py        # Workflow Coordinator Runtime (центральный оркестратор)
    lifecycle.py          # запуск/останов, health, graceful shutdown

  runtimes/
    reactive/
      runtime.py          # Reactive Behavior Runtime: hot-loop decision making
      perception.py       # обогащение стимулов контекстом, "enriched observation"
      goal_generation.py  # генерация candidate goals (быстро)
      planner.py          # constraint-based planner для сборки action
      validator.py        # проверка действий на safety/constraints/predictions
    proactive/
      runtime.py          # Proactive Behavior Runtime: slow-loop / event-driven
      self_awareness.py   # режимы/eMBB vs URLLC, интенты из событий/LLM
      choice_making.py    # ранжирование целей (MLP) + DRL/DQN приоритеты
      intent_llm.py       # LLM few-shot prompting (опционально, в холодном контуре)
      scheduler.py        # периодические триггеры/cron/оконные проверки

  knowledge/
    long_term_memory/
      vector_store.py     # векторное хранилище (заглушка/совместимо с FAISS)
      graph_store.py      # граф знаний (заглушка/совместимо с Neo4j)
      relational_store.py # табличное хранилище KPI/логов (SQLite/Postgres)
      rag.py              # retrieval + сбор контекста (RAG)
    rules/
      rule_engine.py      # пороги/режимы/протокольные ограничения
      constraints.py      # формальные constraints: safety, timing, domain
      temporal_logic.py   # (опционально) LTL/CTL проверки
    telecom_ontology/
      schema.py           # доменные термины и структуры (CQI/SINR/BLER/MCS/...)
      mappings.py         # нормализация телеметрии в онтологические факты

  models/
    situation_awareness/
      filters.py          # сглаживание/фильтрация (в т.ч. Kalman SINR)
      bler_lstm.py        # LSTM прогноз BLER (плейсхолдер интерфейса)
    policy/
      goal_mlp.py         # MLP: генерация/ранжирование candidate goals
      dqn.py              # DQN: выбор действий
      reward_shaping.py   # reward функции/переключение режимов/penalties

  planning/
    mcts.py               # MCTS (опционально, как расширение)
    action_templates.py   # шаблоны действий: MCS up/down, coding rate, MIMO rank...
    conflict_resolution.py # reconcile goals: rules + ontology (+LLM опционально)

  interfaces/
    telemetry/
      ran_adapter.py      # получение CQI/SINR/ACK/NACK/TPT -> Observation
      kpi_logger.py       # логирование KPI
    southbound/
      e2.py               # southbound управление (заглушка)
      odi.py              # southbound управление (заглушка)
      tr069.py            # southbound управление (заглушка)
      mock.py             # мок-исполнитель действий (для тестов)

  experiments/
    ran_la_agent/
      run_online.py       # online контур (если есть стенд)
      run_offline.py      # прогон логов/симуляция
      evaluate.py         # метрики TPT/BLER и сравнение baseline
      baselines/
        olla.py
      configs/
        urllc.yaml
        embb.yaml

  tests/
    unit/
    integration/
    latency/
