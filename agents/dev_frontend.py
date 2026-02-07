import re
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
import json

from dotenv import load_dotenv

load_dotenv()


import os
import sys

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_PATH)

from common.config import Models, Paths, Settings
from common.utils import ensure_dirs, write, build_dev_context
from common.tasks import load_tasks, save_tasks, match_tasks

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler()

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key

DEV_FE = """You're a front-end developer.
Use the project context (below): connect to the current backend/contracts, respecting the existing structure.
In the context, you have the source code for the files you're creating, if they already exist.
Work incrementally if the required files already exist: don't rewrite the project, but add/change what's needed for the task.
DO NOT output the programming language string in the response.
Output only the valid source code for each file being modified.
Output only the full source code for the files, not as an increment.
Output ONLY modified/new files strictly in JSON format:
{{
    "files": 
        [
            {{"path": path, "content": code}},
        ]
}}
"""

def _write_blocks_to_fs(llm, markdown_blocks: str):
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
        print("[FE] Nothing to change.") 
        return
    for file in arr['files']:
        header = file['path']
        # body = parts[i+1] if i+1 < len(parts) else ""
        body = file['content']
        if header.startswith("frontend/"):
            write(P.out + "/code/" + header, body.strip())

def run_frontend(task_id: str | None = None, title_query: str | None = None):
    print("[FE] Starting to work on frontend tasks.")
    ensure_dirs()
    tasks = load_tasks()
    # llm = ChatOllama(model=M.dev, temperature=0.2)
    llm = ChatOpenAI(model=M.dev, temperature=0.2, api_key=API_KEY)

    repo_ctx = build_dev_context("FE")

    targets = match_tasks(tasks, assignee="FE", task_id=task_id, title_query=title_query, status_in=("Todo",))
    print(f"[FE] Found all open issues for frontend development. Total: {len(targets)}.")
    for t in targets:
        print(f"[FE] I'm working on a task '{t.id} / {t.title}'.")
        files = t.artifacts or []
        desc  = t.description or ""
        messages = [
            {"role":"system","content": DEV_FE},
            {"role":"user","content": f"Task: {t.id} — {t.title}\nDescription:\n{desc}\nExpected files:\n" + "\n".join(files)},
            {"role":"user","content": "Repository context:\n" + repo_ctx}
        ]
        code = llm.invoke(messages, config={"callbacks": [langfuse_handler]}).content
        _write_blocks_to_fs(llm, code)
        t.status="Review"
        print(f"[FE] Task completed '{t.id} / {t.title}'.")
    save_tasks(tasks)
    from common.tasks import dump_tasks
    dump_tasks()
    print("[FE] Completed all open tasks for the frontend.")

if __name__=="__main__":
    run_frontend(task_id="TASK-FE-1", title_query="")