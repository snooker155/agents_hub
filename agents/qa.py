import re, json, os, sys, pathlib
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()


import os
import sys

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_PATH)


from common.config import Models, Paths, Settings
from common.utils import ensure_dirs, write, run_cmd, read
from common.tasks import load_tasks, save_tasks, match_tasks

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler()

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key

QA_PROMPT = """You're a tester (QA).
Prepare automated tests for the project:
- API tests using OpenAPI (positive and basic negative cases);
- e2e tests to check the frontend and backend functionality (if there is a frontend);
- data update/change tests.
tests/README.md with launch methods.
The tests should assume the backend server is accessible at http://localhost:8000
(Python) or http://localhost:3000
(Nodejs),
and the frontend at http://localhost:5173
.
If a file is inappropriate (for example, there is no frontend), you can omit it.
Use the current project structure (below) to prepare working configs without breaking existing paths and names.
In context, you have the code for the files you're creating, if they've already been created.
Work incrementally if the required files already exist: don't rewrite the project, but add/change what's needed for the task.
DO NOT output the programming language string in the response.
Output only the valid code for each file being modified.
Output only the full source code for the files, not as an increment.
Output ONLY modified/new files strictly in JSON format:
{{
    "files": 
        [
            {{"path": path, "content": only code}},
        ]
}}
"""

# """
# Recommended paths:

# tests/backend/test_api.py (pytest + httpx для FastAPI) OR tests/backend/test_api.spec.js (supertest for Express)

# tests/e2e/playwright.config.ts и tests/e2e/app.spec.ts (if there is frontend)

# tests/frontend/unit.spec.ts (minimum)
# """

def _write_blocks(llm, markdown_blocks: str):
    try:
        arr = json.loads(markdown_blocks)
    except:
        messages = [
            {"role":"system","content": """Correct the JSON to make it valid. Return only JSON. Do not add any system information. Strictly follow the JSON format.:"
            {{
                "files": 
                    [
                        {{"path": path, "content": only code}},
                    ]
            }}
             """},
            {"role":"user","content": markdown_blocks},
        ]
        fix = llm.invoke(messages, config={"callbacks": [langfuse_handler]}).content
        arr = json.loads(fix)
    # parts = re.split(r"```+", markdown_blocks)
    # if (parts[0] and len(parts[0])): resp = parts[0]
    # else: resp = parts[1].splitlines()[1:]
    # arr_str = ''.join(resp)
    # arr = json.loads(arr_str)
    if (not len(arr['files'])):
        print("[BE] Нечего менять.") 
        return
    for file in arr['files']:
        header = file['path']
        # body = parts[i+1] if i+1 < len(parts) else ""
        body = file['content']
        if header.startswith("tests/"):
            write(P.out + "/" + header, body.strip())

def _dry_syntax_check() -> list[str]:
    """Minor syntax checking of tests (if possible)."""
    problems = []
    # python tests
    for py in pathlib.Path(P.tests).rglob(".py"):
        code, out = run_cmd([sys.executable, "-m", "py_compile", str(py)])
        if code != 0: problems.append(f"py_compile: {py}: {out}")
            # node tests (ts/js) - только синтакс-проверка node при наличии
    code, _ = run_cmd(["node", "--version"])
    if code == 0:
        for js in list(pathlib.Path(P.tests).rglob(".js")) + list(pathlib.Path(P.tests).rglob("*.ts")):
            # node --check не проверяет TS; это бы делал tsc. Ограничимся JS.
            if js.suffix == ".js":
                c2, out2 = run_cmd(["node", "--check", str(js)])
                if c2 != 0: problems.append(f"node --check: {js}: {out2}")
    return problems

def run_qa(task_id: str | None = None, title_query: str | None = None, run_checks: bool = True):
    """If task_id/title_query are not specified, all Todo QA tasks will be processed.
    run_checks: whether to perform a "dry" syntax check of the generated tests."""
    print("[QA] Starting to work on test tasks.")
    ensure_dirs()
    tasks = load_tasks()
    # llm = ChatOllama(model=M.dev, temperature=0.2)
    llm = ChatOpenAI(model=M.dev, temperature=0.2, api_key=API_KEY)

    # контекст (BRD + OpenAPI + DDL), чтобы QA знал, что тестировать
    brd = read(P.docs + "/BRD.json", "")
    openapi = read(P.docs + "/SD_openapi.yaml", "")
    ddl = read(P.docs + "/SD_data_model.sql", "")
    has_front = (pathlib.Path(P.code_fe).exists() and any(pathlib.Path(P.code_fe).rglob("*")))

    targets = match_tasks(tasks, assignee="QA", task_id=task_id, title_query=title_query, status_in=("Todo",))
    print(f"[QA] Found all open tasks for test development. Total: {len(targets)}.")
    for t in targets:
        print(f"[QA] I'm working on a task '{t.id} / {t.title}'.")
        desc = t.description or ""
        files = t.artifacts or []
        code = llm.invoke([
            {"role":"system","content": QA_PROMPT},
            {"role":"user","content": json.dumps({
                "task": {"id": t.id, "title": t.title, "desc": desc, "files": files},
                "brd": json.loads(brd) if brd else {},
                "openapi": openapi,
                "ddl": ddl,
                "frontend_present": bool(has_front)
            }, ensure_ascii=False, indent=2)}
        ], config={"callbacks": [langfuse_handler]}).content
        _write_blocks(llm, code)
        t.status = "Review"
        print(f"[QA] Task completed '{t.id} / {t.title}'.")
    save_tasks(tasks)
    from common.tasks import dump_tasks
    dump_tasks()
    print("[QA] Completed all open tasks for testing..")

    if run_checks:
        problems = _dry_syntax_check()
        if problems:
            print("[QA] Syntax issues found in tests:\n- " + "\n- ".join(problems))
        else:
            print("[QA] Basic test check was successful.")

if __name__=="__main__":
    run_qa(task_id="TASK-QA-1", title_query="")