from __future__ import annotations
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
import json, pathlib, datetime as dt, subprocess, os, textwrap, re

from domains.swe.factory.common.config import settings
from domains.swe.factory.common.tasks import Task

# ========== Utils ==========

def update_graph_status(node_name: str, out_dir: str = "./out"):
    status_path = pathlib.Path(out_dir) / "logs" / "graph_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with open(status_path, "w") as f:
        json.dump({"active_node": node_name}, f)

def now_iso(): return dt.datetime.now().isoformat(timespec="seconds")

def ensure_dirs(root: pathlib.Path):
    for p in ["docs", "plan", "logs", "code/backend", "code/frontend"]:
        (root / p).mkdir(parents=True, exist_ok=True)

def write(path: pathlib.Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)

def run_cmd(cmd: List[str], cwd: pathlib.Path | None = None, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        return r.returncode, out.strip()
    except Exception as e:
        return 1, f"[exec error] {e}"

# ========== External Interaction Log ==========

def log_event(state: "FlowState", *, type_: str, content: str, extra: Dict[str, Any] | None = None):
    rec = {"timestamp": now_iso(), "type": type_, "content": content.strip()}
    if extra: rec.update(extra)
    state.interaction_log.append(rec)

def dump_interaction_log(state: "FlowState"):
    out = pathlib.Path(state.out_dir); ensure_dirs(out)
    write(out/"logs"/"interaction_log.json", json.dumps(state.interaction_log, ensure_ascii=False, indent=2))
    lines = ["# Внешний контекст проекта"]
    titles = {
        "initial_description":"Исходное описание",
        "pm_questions":"Уточняющие вопросы PM",
        "user_answers":"Ответы пользователя",
        "user_corrections":"Правки пользователя",
        "artifact_feedback":"Правки по артефакту",
        "dev_feedback":"Замечания TL по коду",
    }
    for e in state.interaction_log:
        hdr = titles.get(e["type"], e["type"])
        lines.append(f"## {hdr} ({e['timestamp']})")
        if "artifact_key" in e: lines.append(f"_Артефакт: `{e['artifact_key']}`_")
        if "task_ref" in e: lines.append(f"_Задача: `{e['task_ref']}`_")
        lines.append(e["content"])
    write(out/"logs"/"interaction_log.md", "\n\n".join(lines))

def external_context_md(state: "FlowState") -> str:
    lines = ["# Внешний контекст проекта"]
    for e in state.interaction_log:
        sec = [f"## {e['type']} ({e['timestamp']})"]
        if "artifact_key" in e: sec.append(f"_Артефакт: `{e['artifact_key']}`_")
        if "task_ref" in e: sec.append(f"_Задача: `{e['task_ref']}`_")
        sec.append(e["content"]); lines.append("\n".join(sec))
    return "\n\n".join(lines)

# ========== Task Registry ==========

def dump_tasks(state: "FlowState"):
    out = pathlib.Path(state.out_dir); ensure_dirs(out)
    write(out/"plan"/"tasks.json", json.dumps([t.model_dump() for t in state.tasks], ensure_ascii=False, indent=2))
    lines = ["# Реестр задач", "", "| ID | Title | Assignee | Status | Artifact | Parent |",
             "|---|---|---|---|---|---|"]
    for t in state.tasks:
        lines.append(f"| {t.id} | {t.title} | {t.assignee} | {t.status} | {t.artifact_key or ''} | {t.parent_id or ''} |")
    write(out/"plan"/"tasks.md", "\n".join(lines))

def next_id(state: "FlowState", prefix: str) -> str:
    n = 1 + sum(1 for t in state.tasks if t.id.startswith(prefix))
    return f"{prefix}-{n}"

# ========== State ==========

class FlowState(BaseModel):
    user_description: str
    out_dir: str = "./out"

    interaction_log: List[Dict[str, Any]] = Field(default_factory=list)

    # BA (BRD)
    brd_json: Dict[str, Any] = Field(default_factory=dict)
    brd_md: str = ""
    approved_brd: Optional[bool] = None

    # SD artifacts
    sd: Dict[str, str] = Field(default_factory=dict)          # key -> content
    sd_paths: Dict[str, str] = Field(default_factory=dict)    # key -> saved path
    sd_ready: bool = False

    # Dev stack decision
    need_ui: bool = False
    backend_lang: str = "python"  # python | nodejs

    # Tasks
    tasks: List[Task] = Field(default_factory=list)

# ========== LLM Clients ==========

MODEL_NAME = "gpt-oss:20b"
# MODEL_NAME = settings.model
# API_KEY = settings.openai_api_key

PM   = ChatOllama(model=MODEL_NAME, temperature=0.2)
BA_J = ChatOllama(model=MODEL_NAME, temperature=0.1, format="json")
BA_M = ChatOllama(model=MODEL_NAME, temperature=0.2)
SD_L = ChatOllama(model=MODEL_NAME, temperature=0.1)
DEV  = ChatOllama(model=MODEL_NAME, temperature=0.2)
TL_L = ChatOllama(model=MODEL_NAME, temperature=0.1)

# PM   = ChatOpenAI(model=MODEL_NAME, temperature=0.2, api_key=API_KEY)
# BA_J = ChatOpenAI(model=MODEL_NAME, temperature=0.1, format="json", api_key=API_KEY)
# BA_M = ChatOpenAI(model=MODEL_NAME, temperature=0.2, api_key=API_KEY)
# SD_L = ChatOpenAI(model=MODEL_NAME, temperature=0.1, api_key=API_KEY)
# DEV  = ChatOpenAI(model=MODEL_NAME, temperature=0.2, api_key=API_KEY)
# TL_L = ChatOpenAI(model=MODEL_NAME, temperature=0.1, api_key=API_KEY)

# ========== Prompts (кратко) ==========

# PM_Q = "Ты PM. По описанию задай до 5 уточняющих вопросов (scope, пользователи, интеграции, NFR, риски). Кратко, нумерованный список."
PM_Q = "Ты project manager."
BA_JSON_P = """Ты бизнес-аналитик. Используй ТОЛЬКО внешний контекст ниже. Верни СТРОГО валидный JSON бизнес-требований по заданной схеме без комментариев."""
BA_MD_P = "Ты техписатель. Преобразуй JSON бизнес-требований в компактный Markdown, сохраняя FR-ID."
SD_SPEC = "Ты solution designer. На основе BRD создай тех. спецификацию (Markdown): контекст, сценарии, NFR-реализация, границы."
SD_TECH = "Ты solution designer. На основе BRD создай выбор технологий (Markdown) с обоснованием и trade-offs."
SD_DDL  = "Ты solution designer. На основе BRD создай SQL DDL (PostgreSQL) для ключевой модели данных."
SD_WF   = "Ты solution designer. На основе BRD создай Markdown с диаграммами Mermaid (sequence/flowchart) минимум 2 ключевых воркфлоу."
SD_API  = "Ты solution designer. На основе BRD создай OpenAPI 3.1 YAML (мин. 3 endpoint'а, схемы, ошибки, security). Верни чистый YAML."
SD_ARCH = "Ты solution designer. На основе BRD создай архитектурное описание (Markdown) + Mermaid диаграмма компонентов."

TL_SPLIT = """Ты тимлид. Тебе дан BRD и выбор технологий. Разбей проект на МЕЛКИЕ задачи JSON-формата:
{
  "backend": [{"id":"BE-1","title":"...","description":"...","files":["path"],"depends_on":[]}, ...],
  "frontend": [{"id":"FE-1","title":"...","description":"...","files":["path"],"depends_on":[]}, ...],
  "fullstack": [{"id":"FS-1","title":"...","description":"...","files":["path"],"depends_on":[]}, ...]
}
Файлы указывай относительные: backend/src/... или frontend/src/....
Если UI не требуется, frontend массив сделай пустым. Учитывай OpenAPI/DDL.
"""

DEV_BE = """Ты бэкенд-разработчик. Напиши минимально ЖИВОЙ код {lang} для задачи (описание ниже).
Требования: запускаемый скелет сервиса + соответствующие файлы; если {lang}==python — используй FastAPI; если nodejs — Express.
Подготовь простой README с командами запуска. Строго пиши в указанные пути."""
DEV_FE = """Ты фронтенд-разработчик. Напиши минимально ЖИВОЙ фронтенд (Vite+React) в указанных файлах, который ходит к API по OpenAPI, простая страница и UI для 1-2 ключевых сценариев. Добавь README. Строго пиши в указанные пути."""
DEV_FS = """Ты фуллстэк-разработчик. По задаче поправь и бэкенд, и фронтенд (если есть), сохраняя договорённые пути. Короткий README."""

TL_REVIEW = """Ты тимлид. Проверь представленный код по задаче: соответствие заданию, читаемость, простые ошибки.
Верни JSON:
{"ok": true|false, "remarks": ["строка",...], "must_fix": ["строка",...]}
Если code не валидный/не запускается — ok=false и must_fix с конкретикой."""

# ========== Nodes: PM / BA / SD ==========

def node_pm_intake(state: FlowState) -> FlowState:
    update_graph_status("pm_intake", state.out_dir)
    out = pathlib.Path(state.out_dir); ensure_dirs(out)
    log_event(state, type_="initial_description", content=state.user_description)
    qs = PM.invoke([{"role":"system","content":PM_Q},{"role":"user","content":state.user_description}]).content
    # print("\n[PM] Вопросы:\n" + qs + "\n")
    # log_event(state, type_="pm_questions", content=qs)

    # q_only = [ln.lstrip("0123456789). ").strip("-• ").strip() for ln in qs.splitlines() if ln.strip()]
    # if q_only:
    #     ans = []
    #     for q in q_only:
    #         a = input(f"  ↳ Ответ на «{q}»: ").strip()
    #         ans.append(f"- **{q}**\n  Ответ: {a}")
    #     log_event(state, type_="user_answers", content="\n".join(ans))
    # dump_interaction_log(state)
    return state

def node_pm_assign_ba(state: FlowState) -> FlowState:
    update_graph_status("pm_assign_ba", state.out_dir)
    tid = next_id(state, "TASK-BRD")
    state.tasks.append(Task(id=tid, title="Подготовить BRD", assignee="BA"))
    dump_tasks(state)
    print(f"\n[PM] Задача на BA: {tid}")
    return state

def node_ba_generate(state: FlowState) -> FlowState:
    update_graph_status("ba_generate", state.out_dir)
    out = pathlib.Path(state.out_dir); ensure_dirs(out)
    ctx = external_context_md(state)
    # JSON BRD
    js = BA_J.invoke([{"role":"system","content":BA_JSON_P},{"role":"user","content":ctx}]).content
    try:
        brd = json.loads(js)
    except:
        fix = ChatOllama(model=MODEL_NAME, temperature=0.0).invoke(
            [{"role":"system","content":"Исправь JSON до валидного. Верни только JSON."},{"role":"user","content":js}]
        ).content
        brd = json.loads(fix)
    state.brd_json = brd
    write(out/"docs"/"BRD.json", json.dumps(brd, ensure_ascii=False, indent=2))
    # MD
    md = BA_M.invoke([{"role":"system","content":BA_MD_P},{"role":"user","content":json.dumps(brd, ensure_ascii=False, indent=2)}]).content
    state.brd_md = md
    write(out/"docs"/"BRD.md", md)
    # close BA task
    for t in state.tasks:
        if t.id.startswith("TASK-BRD") and t.status=="Todo": t.status="Done"
    dump_tasks(state)
    print("[BA] BRD готов: ./out/docs/BRD.json, BRD.md")
    return state

def node_pm_review_brd(state: FlowState) -> FlowState:
    update_graph_status("pm_review_brd", state.out_dir)
    print("\n[PM] Проверьте BRD: ./out/docs/BRD.md")
    while True:
        a = input("BRD корректен? [y/n]: ").strip().lower()
        if a in ("y","yes","д","да"): state.approved_brd=True; return state
        if a in ("n","no","н","нет"): state.approved_brd=False; break
        print("Введите 'y' или 'n'.")
    corr = input("Опишите правки к BRD: ").strip()
    log_event(state, type_="user_corrections", content=corr)
    dump_interaction_log(state)
    # перегенерим на следующем проходе
    return state

def node_pm_create_sd_tasks(state: FlowState) -> FlowState:
    update_graph_status("pm_create_sd_tasks", state.out_dir)
    # 6 артефактов SD
    defs = [
        ("TASK-SD-SPEC",  "Тех. спецификация",            "sd_spec_md",   SD_SPEC,  "docs/SD_tech_spec.md"),
        ("TASK-SD-TECH",  "Выбор технологий",             "sd_tech_md",   SD_TECH,  "docs/SD_tech_choices.md"),
        ("TASK-SD-DDL",   "DDL (модель данных)",          "sd_ddl_sql",   SD_DDL,   "docs/SD_data_model.sql"),
        ("TASK-SD-WF",    "Воркфлоу (Mermaid)",           "sd_wf_md",     SD_WF,    "docs/SD_workflows.md"),
        ("TASK-SD-API",   "OpenAPI контракт",             "sd_api_yaml",  SD_API,   "docs/SD_openapi.yaml"),
        ("TASK-SD-ARCH",  "Архитектурное описание",       "sd_arch_md",   SD_ARCH,  "docs/SD_architecture.md"),
    ]
    for tid, title, key, _, _ in defs:
        state.tasks.append(Task(id=tid, title=title, assignee="SD", artifact_key=key))
    state.sd["_defs"] = json.dumps(defs)  # сохраним определения для узла SD
    dump_tasks(state)
    print("\n[PM] Созданы SD-задачи (6 артефактов).")
    return state

def node_sd_generate_all(state: FlowState) -> FlowState:
    update_graph_status("sd_generate_all", state.out_dir)
    out = pathlib.Path(state.out_dir); ensure_dirs(out)
    defs = json.loads(state.sd["_defs"])
    for tid, title, key, prompt, relpath in defs:
        content = SD_L.invoke([
            {"role":"system","content": prompt},
            {"role":"user","content": json.dumps(state.brd_json, ensure_ascii=False, indent=2)}
        ]).content
        state.sd[key] = content
        path = out/relpath
        state.sd_paths[key] = write(path, content)
        # отметим как Done
        for t in state.tasks:
            if t.id == tid: t.status = "Done"
    state.sd_ready = True
    dump_tasks(state)
    print("[SD] Все артефакты сохранены в ./out/docs/")
    return state

def node_pm_review_sd(state: FlowState) -> FlowState:
    update_graph_status("pm_review_sd", state.out_dir)
    print("\n[PM] Проверьте SD-артефакты (папка ./out/docs).")
    any_changes = False
    for key, path in state.sd_paths.items():
        ans = input(f"Нужны правки для {key} ({path})? [y/n]: ").strip().lower()
        if ans in ("y","yes","д","да"):
            corr = input("Опишите правки: ").strip()
            log_event(state, type_="artifact_feedback", content=corr, extra={"artifact_key": key})
            rid = next_id(state, "TASK-SD-REV")
            state.tasks.append(Task(id=rid, title=f"Правки {key}", assignee="SD", status="Todo",
                                    artifact_key=key, parent_id=next((t.id for t in state.tasks if t.artifact_key==key), None),
                                    payload={"corrections": corr}))
            any_changes = True
    dump_tasks(state); dump_interaction_log(state)
    if not any_changes:
        print("[PM] Правок нет. Переходим к разработке.")
    return state

def node_sd_apply_revisions(state: FlowState) -> FlowState:
    update_graph_status("sd_apply_revisions", state.out_dir)
    out = pathlib.Path(state.out_dir)
    for t in state.tasks:
        if t.assignee=="SD" and t.status=="Todo" and t.id.startswith("TASK-SD-REV"):
            prev = state.sd.get(t.artifact_key, "")
            prompt = f"""Ты solution designer. Вот прошлый артефакт, и правки пользователя ниже. Примени правки и верни обновлённый артефакт ТОЛЬКО в нужном формате.
Правки:
{t.payload.get('corrections','')}
Текущий артефакт:
{prev}"""
            updated = SD_L.invoke([
                {"role":"system","content":prompt},
                {"role":"user","content": json.dumps(state.brd_json, ensure_ascii=False, indent=2)}
            ]).content
            state.sd[t.artifact_key] = updated
            write(pathlib.Path(state.sd_paths[t.artifact_key]), updated)
            t.status = "Done"
    dump_tasks(state)
    print("[SD] Все правки применены.")
    return state

# ========== Dev Planning: choose stack & split tasks ==========

def node_pm_tl_choose_stack(state: FlowState) -> FlowState:
    update_graph_status("pm_tl_choose_stack", state.out_dir)
    # эвристика по UI и языку из SD-техвыбора/спеки
    tech = (state.sd.get("sd_tech_md","") + "\n" + state.sd.get("sd_spec_md","")).lower()
    need_ui = any(k in tech for k in ["ui", "frontend", "react", "vue", "spa", "web app", "панель", "интерфейс"])
    state.need_ui = bool(need_ui)
    # язык бэкенда
    if "python" in tech or "fastapi" in tech or "django" in tech:
        state.backend_lang = "python"
    elif "node" in tech or "node.js" in tech or "express" in tech or "typescript" in tech or "nest" in tech:
        state.backend_lang = "nodejs"
    else:
        # дефолт: python
        state.backend_lang = "python"
    print(f"\n[PM+TL] Выбор стека: UI={'да' if state.need_ui else 'нет'}, backend={state.backend_lang}")
    return state

def node_tl_split_tasks(state: FlowState) -> FlowState:
    update_graph_status("tl_split_tasks", state.out_dir)
    # TL генерит JSON-список мелких задач
    ctx = {
        "brd": state.brd_json,
        "openapi": state.sd.get("sd_api_yaml",""),
        "ddl": state.sd.get("sd_ddl_sql",""),
        "need_ui": state.need_ui,
        "backend_lang": state.backend_lang
    }
    plan_json = TL_L.invoke([
        {"role":"system","content": TL_SPLIT},
        {"role":"user","content": json.dumps(ctx, ensure_ascii=False, indent=2)}
    ]).content
    try:
        plan = json.loads(plan_json)
    except:
        fix = ChatOllama(model=MODEL_NAME, temperature=0.0).invoke(
            [{"role":"system","content":"Исправь JSON до валидного. Верни только JSON."},{"role":"user","content":plan_json}]
        ).content
        plan = json.loads(fix)

    # Преобразуем в задачи
    for cat, assignee in [("Backend","BE"), ("Frontend","FE"), ("Fullstack","FS")]:
        for item in plan.get(cat, []):
            tid = next_id(state, f"TASK-{assignee}")
            title = f"{cat.upper()}: {item.get('title','')}"
            payload = {"description": item.get("description",""), "files": item.get("files",[])}
            state.tasks.append(Task(id=tid, title=title, assignee=assignee, status="Todo", payload=payload))
    # добавим задачи на ревью TL
    # ревью будут автоматически создаваться после разработки каждой задачи
    dump_tasks(state)
    print("[TL] Разбил проект на мелкие задачи и создал карточки.")
    return state

# === scaffolding helpers ===
def gen_requirements_txt() -> str:
    # Минимум для API на FastAPI
    return "\n".join([
        "fastapi>=0.115.0",
        "uvicorn[standard]>=0.30.0",
        "pydantic>=2.7.0",
        "python-multipart>=0.0.9",
        "httpx>=0.27.0",           # для простых исходящих запросов/тестов
    ]) + "\n"

def gen_backend_package_json_node() -> str:
    return json.dumps({
        "name": "service-backend",
        "version": "0.1.0",
        "type": "module",
        "private": True,
        "scripts": {
            "start": "node src/index.js",
            "dev": "node --watch src/index.js",
            "lint": "echo \"(add your linter)\""
        },
        "dependencies": {
            "express": "^4.19.0",
            "cors": "^2.8.5",
            "dotenv": "^16.4.0",
            "morgan": "^1.10.0",
            "axios": "^1.7.0"
        }
    }, ensure_ascii=False, indent=2) + "\n"

def gen_frontend_package_json_vite() -> str:
    return json.dumps({
        "name": "service-frontend",
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview"
        },
        "dependencies": {
            "react": "^18.3.1",
            "react-dom": "^18.3.1"
        },
        "devDependencies": {
            "vite": "^5.4.0",
            "@vitejs/plugin-react": "^4.3.0"
        }
    }, ensure_ascii=False, indent=2) + "\n"

def node_scaffold_env(state: FlowState) -> FlowState:
    """
    Создаёт стартовые файлы окружения:
      - backend/python: requirements.txt
      - backend/nodejs: package.json
      - frontend (если нужен UI): package.json
    """
    root = pathlib.Path(state.out_dir); ensure_dirs(root)

    # BACKEND
    if state.backend_lang == "python":
        write(root/"code/backend/requirements.txt", gen_requirements_txt())
        # маленький README со стартом сервера
        write(root/"code/backend/README.md", textwrap.dedent("""\
            # Backend (Python/FastAPI)
            ## Установка
            python -m venv .venv && source .venv/bin/activate
            pip install -r requirements.txt
            ## Запуск (если есть app)
            uvicorn src.app:app --reload --port 8000
        """))
    else:
        write(root/"code/backend/package.json", gen_backend_package_json_node())
        write(root/"code/backend/README.md", textwrap.dedent("""\
            # Backend (Node.js/Express)
            ## Установка
            npm install
            ## Запуск
            npm run dev
        """))

    # FRONTEND (если нужен UI)
    if state.need_ui:
        write(root/"code/frontend/package.json", gen_frontend_package_json_vite())
        write(root/"code/frontend/README.md", textwrap.dedent("""\
            # Frontend (Vite + React)
            ## Установка
            npm install
            ## Запуск
            npm run dev
        """))

    # Общий .gitignore (минимальный)
    write(root/".gitignore", textwrap.dedent("""\
        .venv/
        node_modules/
        dist/
        __pycache__/
        .pytest_cache/
        .DS_Store
    """))

    print("[PM+TL] Сгенерированы файлы окружения для проектов.")
    return state

# ========== Dev Implementation Nodes ==========

def _dev_write_files(base: pathlib.Path, files_to_content: Dict[str,str]):
    for rel, content in files_to_content.items():
        write(base/rel, content)

def node_dev_execute(state: FlowState) -> FlowState:
    """Выполняет все Todo задачи разработчиков (BE/FE/FS): пишет код в нужные файлы и ставит статус Review."""
    root = pathlib.Path(state.out_dir); ensure_dirs(root)
    for t in state.tasks:
        if t.status!="Todo" or t.assignee not in ("BE","FE","FS"): continue
        desc = t.payload.get("description","")
        files: List[str] = t.payload.get("files",[])
        if not files: 
            t.status = "Closed"
            continue

        if t.assignee=="BE":
            prompt = DEV_BE.format(lang=state.backend_lang)
        elif t.assignee=="FE":
            prompt = DEV_FE
        else:
            prompt = DEV_FS

        code = DEV.invoke([
            {"role":"system","content": prompt},
            {"role":"user","content": f"Задача: {t.title}\nОписание:\n{desc}\nФайлы:\n" + "\n".join(files)}
        ]).content

        # Простая стратегия: если есть несколько файлов, делим контент по разделителям
        # Ожидаемый формат от модели: блоки ```path\n<content>\n``` ... ; fallback — один файл
        parts = re.split(r"```+", code)
        files_map: Dict[str,str] = {}
        for i in range(1, len(parts), 2):
            header = parts[i].strip().splitlines()[0]
            body = parts[i+1] if i+1 < len(parts) else ""
            path_line = header
            # если указали язык после тройных кавычек, следующая строка может быть путем
            if "/" not in path_line and "\\" not in path_line and i+2 < len(parts):
                # попробуем взять первую строку body как путь
                bl = body.splitlines()
                if bl and ("/" in bl[0] or "\\" in bl[0]):
                    path_line = bl[0]; body = "\n".join(bl[1:])
            files_map[path_line.strip()] = body.strip()

        if not files_map and files:
            # fallback: если не смогли распарсить — запишем в первый файл весь content
            files_map[files[0]] = code

        base = root/"code"
        for f in list(files_map.keys()):
            if f.startswith("backend/"):   write(base/f, files_map[f])
            elif f.startswith("frontend/"): write(base/f, files_map[f])
            else:
                # если путь не префиксован — решим по типу задачи
                pref = "backend" if t.assignee in ("BE","FS") and state.backend_lang=="python" else ("backend" if t.assignee in ("BE","FS") else "frontend")
                write(base/(pref+"/src/"+f), files_map[f])

        t.status = "Review"
    dump_tasks(state)
    print("[DEV] Разработчики сгенерировали код. Задачи переведены в Review.")
    return state

# ========== TL Review & Validation ==========

def _validate_backend(state: FlowState, root: pathlib.Path) -> tuple[bool, str]:
    if state.backend_lang == "python":
        # попробуем откомпилировать все .py
        py_files = [p for p in (root/"code/backend").rglob("*.py")]
        errs = []
        for f in py_files:
            code, out = run_cmd([os.sys.executable, "-m", "py_compile", str(f)])
            if code != 0: errs.append(f"{f}: {out}")
        return (len(errs)==0, "\n".join(errs) if errs else "OK")
    else:
        # node --check для .js (если доступно)
        js_files = [p for p in (root/"code/backend").rglob("*.js")]
        errs = []
        for f in js_files:
            code, out = run_cmd(["node", "--check", str(f)])
            if code != 0: errs.append(f"{f}: {out}")
        return (len(errs)==0, "\n".join(errs) if errs else "OK")

def node_tl_review(state: FlowState) -> FlowState:
    root = pathlib.Path(state.out_dir); ensure_dirs(root)
    for t in state.tasks:
        if t.assignee in ("BE","FE","FS") and t.status=="Review":
            # TL проверяет содержимое (промптом) + базовая валидность бэка
            # Считаем, что TL читает файлы из payload["files"]
            files = t.payload.get("files",[])
            snapshot = []
            for rel in files:
                path = root/"code"/rel
                if path.exists():
                    try: snapshot.append(f"\n=== {rel} ===\n" + path.read_text(encoding="utf-8")[:6000])
                    except: snapshot.append(f"\n=== {rel} ===\n(binary or unreadable)")
            review = TL_L.invoke([
                {"role":"system","content": TL_REVIEW},
                {"role":"user","content": f"Задача: {t.title}\nОписание:\n{t.payload.get('description','')}\nКод:\n{''.join(snapshot)}"}
            ]).content
            # Попробуем распарсить JSON
            try: r = json.loads(review)
            except:
                r = {"ok": False, "remarks":[review], "must_fix":["Не удалось распарсить JSON с ревью"]}

            ok = bool(r.get("ok"))
            remarks = "\n".join(r.get("remarks",[]))
            must = "\n".join(r.get("must_fix",[]))

            # Доп. валидация back
            if any(rel.startswith("backend/") for rel in files):
                valid, msg = _validate_backend(state, root)
                if not valid:
                    ok = False
                    must += ("\n" if must else "") + f"[Автопроверка] Ошибки компиляции backend:\n{msg}"

            if ok:
                t.status = "Done"
            else:
                t.status = "Closed"
                # создаём задачу на доработку, с ссылкой на исходную
                rid = next_id(state, "TASK-REV")
                details = "Требуется доработка:\n" + (must or remarks or "Уточнить требования")
                state.tasks.append(Task(
                    id=rid, title=f"Доработка по {t.id}", assignee=("BE" if "backend" in "".join(files) else ("FE" if "frontend" in "".join(files) else "FS")),
                    status="Todo", parent_id=t.id, payload={"description": details, "files": files}
                ))
                # лог в интеракшн как внешний контекст (dev_feedback)
                log_event(state, type_="dev_feedback", content=details, extra={"task_ref": t.id})
    dump_tasks(state); dump_interaction_log(state)
    print("[TL] Провёл ревью. Замечания превращены в задачи на доработку.")
    return state

def node_dev_rework(state: FlowState) -> FlowState:
    """Исполнение всех задач TASK-REV-* (доработка) → снова Review."""
    root = pathlib.Path(state.out_dir)
    for t in state.tasks:
        if t.id.startswith("TASK-REV") and t.status=="Todo":
            desc = t.payload.get("description",""); files = t.payload.get("files",[])
            prompt = DEV_FS  # фуллстэк-правки по умолчанию
            code = DEV.invoke([
                {"role":"system","content": prompt},
                {"role":"user","content": f"Доработка для {t.parent_id}. Описание:\n{desc}\nФайлы:\n" + "\n".join(files)}
            ]).content
            # Пишем изменения (тупо перезаписываем те же файлы, что в payload)
            # По-хорошему нужно парсить блоки, но для conciseness — перезапись первых файлов
            if files:
                write(root/"code"/files[0], code)
            t.status = "Review"
    dump_tasks(state)
    print("[DEV] Выполнили доработки. Отдали в Review TL.")
    return state

# ========== Graph ==========

graph = StateGraph(FlowState)
graph.add_node("pm_intake",            node_pm_intake)
graph.add_node("pm_assign_ba",         node_pm_assign_ba)
graph.add_node("ba_generate",          node_ba_generate)
# graph.add_node("pm_review_brd",        node_pm_review_brd)
graph.add_node("pm_create_sd_tasks",   node_pm_create_sd_tasks)
graph.add_node("sd_generate_all",      node_sd_generate_all)
# graph.add_node("pm_review_sd",         node_pm_review_sd)
# graph.add_node("sd_apply_revisions",   node_sd_apply_revisions)
graph.add_node("pm_tl_choose_stack",   node_pm_tl_choose_stack)
graph.add_node("tl_split_tasks",       node_tl_split_tasks)
# graph.add_node("scaffold_env",         node_scaffold_env)
# graph.add_node("dev_execute",          node_dev_execute)
# graph.add_node("tl_review",            node_tl_review)
# graph.add_node("dev_rework",           node_dev_rework)

graph.set_entry_point("pm_intake")
graph.add_edge("pm_intake", "pm_assign_ba")
graph.add_edge("pm_assign_ba", "ba_generate")
# graph.add_edge("ba_generate", "pm_review_brd")
graph.add_edge("ba_generate", "pm_create_sd_tasks")

def after_brd(state: FlowState):
    return "pm_create_sd_tasks" if state.approved_brd else "ba_generate"
# graph.add_conditional_edges("pm_review_brd", after_brd,
#     {"pm_create_sd_tasks":"pm_create_sd_tasks", "ba_generate":"ba_generate"})

graph.add_edge("pm_create_sd_tasks", "sd_generate_all")
# graph.add_edge("sd_generate_all", "pm_review_sd")
graph.add_edge("sd_generate_all", "pm_tl_choose_stack")

def after_sd_review(state: FlowState):
    # если есть открытые ревизии SD — правим; иначе идём к dev планированию
    need = any(t.assignee=="SD" and t.status=="Todo" and t.id.startswith("TASK-SD-REV") for t in state.tasks)
    return "sd_apply_revisions" if need else "pm_tl_choose_stack"
# graph.add_conditional_edges("pm_review_sd", after_sd_review,
#     {"sd_apply_revisions":"sd_apply_revisions", "pm_tl_choose_stack":"pm_tl_choose_stack"})

# graph.add_edge("sd_apply_revisions", "pm_tl_choose_stack")
graph.add_edge("pm_tl_choose_stack", "tl_split_tasks")
# graph.add_edge("tl_split_tasks", "dev_execute")
# graph.add_edge("dev_execute", "tl_review")

def after_tl_review(state: FlowState):
    # если есть задачи на доработку (TASK-REV-*, Todo) — исполняем; если нет — конец
    need = any(t.id.startswith("TASK-REV") and t.status=="Todo" for t in state.tasks)
    return "dev_rework" if need else END
# graph.add_conditional_edges("tl_review", after_tl_review, {"dev_rework":"dev_rework", END:END})
# graph.add_edge("dev_rework", "tl_review")

app = graph.compile()

# ========== CLI ==========

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PM ↔ BA ↔ SD ↔ TL/Dev: BRD → SD → Dev план → Разработка с ревью/доработками")
    parser.add_argument("--desc", required=True, help="Изначальное описание проекта")
    parser.add_argument("--out", default="./out", help="Папка артефактов")
    args = parser.parse_args()

    state = FlowState(user_description=args.desc, out_dir=args.out)
    result = app.invoke(state)

    print("\n✔ Завершено.")
    print("  BRD:           ./out/docs/BRD.json, BRD.md")
    print("  SD артефакты:  SD_tech_spec.md, SD_tech_choices.md, SD_data_model.sql, SD_workflows.md, SD_openapi.yaml, SD_architecture.md")
    print("  Код:           ./out/code/backend, ./out/code/frontend")
    print("  Задачи:        ./out/plan/tasks.json, tasks.md")
    print("  Контекст:      ./out/logs/interaction_log.md (и .json)")
