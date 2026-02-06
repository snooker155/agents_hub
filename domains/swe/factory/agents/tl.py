import json, textwrap, os
from langchain_ollama import ChatOllama
from domains.swe.factory.common.config import Models, Settings, Paths
from domains.swe.factory.common.utils import ensure_dirs, write, read, load_json, save_json
from domains.swe.factory.common.tasks import load_tasks, save_tasks, Task, next_id, dump_tasks
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler()

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key

TL_SPLIT = """You are a team leader. Break it down into small JSON tasks:
{ 
    "backend": [{"id":"BE-1","title":"...","description":"...","files":["backend/src/app.py"],"depends_on":[]}], 
    "frontend": [{"id":"FE-1","title":"...","description":"...","files":["frontend/src/main.jsx"],"depends_on":[]}], 
    "devops": [{"id":"OPS-1","title":"...","description":"...","files":["ops/docker-compose.yml"],"depends_on":[]}] 
    "qa": [{"id":"QA-1","title":"...","description":"...","files":["tests/backend/test_api.py","tests/e2e/playwright.spec.ts"],"depends_on":[]}]
}
If a UI is not needed, frontend = [].
DevOps tasks: Docker-compose, CI pipeline, OpenAPI docs publication, env variables (.env.example),
deployment scripts (scripts/deploy.sh), infrastructure YAML/configs.
QA: API tests (via OpenAPI), e2e BE↔FE, data update/change verification.
Rule: tasks should be incremental and local (minimal changes and dependencies) so they can be quickly verified. Specify files relative to the out root: backend/... frontend/... ops/... tests/...
Consider OpenAPI and DDL when planning.
Return only valid JSON. Don't add any system information."""

def choose_stack() -> tuple[bool,str]:
    tech = (read(P.docs + "/SD_tech_choices.md","") + read(P.docs + "/SD_tech_spec.md","")).lower()
    need_ui = any(k in tech for k in ["ui","frontend","react","spa","interface"])
    if "python" in tech or "fastapi" in tech: back="python"
    elif "node" in tech or "express" in tech: back="nodejs"
    else: back = "python"
    # сохраним выбор
    write(P.plan + "/stack.json", json.dumps({"need_ui":need_ui, "backend_lang":back}, ensure_ascii=False, indent=2))
    return need_ui, back

def split_tasks():
    print("[TL] Generating a JSON list of small tasks.")
    print("[TL] Reading technical requirements documents.")
    brd  = read(P.docs + "/BRD.json")
    openapi = read(P.docs + "/SD_openapi.yaml","")
    ddl = read(P.docs + "/SD_data_model.sql","")
    stack = load_json(P.plan + "/stack.json", default={"need_ui":False,"backend_lang":"python"})
    print("[TL] Generating a JSON list.")
    # plan = ChatOllama(model=M.tl, temperature=0.1).invoke(
    plan = ChatOpenAI(model=M.tl, temperature=0.1, api_key=API_KEY).invoke(
      [{"role":"system","content":TL_SPLIT},{"role":"user","content":json.dumps({"brd":json.loads(brd),"openapi":openapi,"ddl":ddl,"need_ui":stack["need_ui"],"backend_lang":stack["backend_lang"]}, ensure_ascii=False, indent=2)}]
    , config={"callbacks": [langfuse_handler]}).content
    try: 
        plan_obj = json.loads(plan)
    except:
        print("[TL] The first JSON list is invalid. Fixing it.")
        # fix = ChatOllama(model=M.tl, temperature=0.0).invoke(
        fix = ChatOpenAI(model=M.tl, temperature=0.0, api_key=API_KEY).invoke(
            [{"role":"system","content":"Correct the JSON to make it valid. Return only JSON. Don't add any system information."},{"role":"user","content":plan}]
        , config={"callbacks": [langfuse_handler]}).content
        plan_obj = json.loads(fix)
    print("[TL] Adding new tasks to existing ones.")
    tasks = load_tasks()
    for cat, assignee in [("backend","BE"),("frontend","FE"),("devops","OPS"),("qa","QA")]:
        for item in plan_obj.get(cat, []):
            tid = next_id(tasks, f"TASK-{assignee}")
            tasks.append(Task(id=tid, title=f"{cat.upper()}: {item.get('title','')}",
                              assignee=assignee, artifacts=item.get("files",[]), description=item.get("description","")))
    print("[TL] Saving new tasks.")
    save_tasks(tasks)
    dump_tasks()
    print("[TL] Broke the project into smaller tasks and created cards.")

def scaffold_env():
    stack = load_json(P.plan + "/stack.json", default={"need_ui":False,"backend_lang":"python"})
    if stack["backend_lang"]=="python":
        req = "\n".join(["fastapi>=0.115.0","uvicorn[standard]>=0.30.0","pydantic>=2.7.0","python-multipart>=0.0.9","httpx>=0.27.0"])+"\n"
        write(P.code_be + "/requirements.txt", req)
        write(P.code_be + "/README.md", textwrap.dedent("""\
            # Backend (Python/FastAPI)
            python -m venv .venv && source .venv/bin/activate
            pip install -r requirements.txt
            uvicorn src.app:app --reload --port 8000
        """))
    else:
        pkg = json.dumps({"name":"service-backend","version":"0.1.0","type":"module","private":True,
            "scripts":{"start":"node src/index.js","dev":"node --watch src/index.js"},
            "dependencies":{"express":"^4.19.0","cors":"^2.8.5","dotenv":"^16.4.0","morgan":"^1.10.0","axios":"^1.7.0"}}, ensure_ascii=False, indent=2)
        write(P.code_be + "/package.json", pkg+"\n")
        write(P.code_be + "/README.md", "npm install\nnpm run dev\n")
    if stack["need_ui"]:
        fe_pkg = json.dumps({"name":"service-frontend","version":"0.1.0","private":True,"type":"module",
          "scripts":{"dev":"vite","build":"vite build","preview":"vite preview"},
          "dependencies":{"react":"^18.3.1","react-dom":"^18.3.1"},
          "devDependencies":{"vite":"^5.4.0","@vitejs/plugin-react":"^4.3.0"}}, ensure_ascii=False, indent=2)
        write(P.code_fe + "/package.json", fe_pkg+"\n")

def review_code(task: dict) -> dict:
    """Return JSON {'ok':bool,'remarks':[],'must_fix':[]} — simple «validation» TL."""
    from domains.swe.factory.common.utils import run_cmd
    files = task.get("payload",{}).get("files",[])
    ok=True; remarks=[]; must=[]
    # быстрая проверка backend
    if any(f.startswith("backend/") for f in files):
        if load_json(P.plan + "/stack.json", default={}).get("backend_lang")=="python":
            # попробуем py_compile
            import pathlib, sys, subprocess
            py_files = [str(pathlib.Path(P.code_be).joinpath(*f.split("/")[1:])) for f in files if f.endswith(".py")]
            for pf in py_files:
                code,out = run_cmd([sys.executable,"-m","py_compile",pf])
                if code!=0: ok=False; must.append(f"py_compile failed: {pf}\n{out}")
        else:
            code,out = run_cmd(["node","--version"])
            if code!=0: remarks.append("Node not found - skipping syntax check.")
    return {"ok":ok,"remarks":remarks,"must_fix":must}
