import re
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
import json

from dotenv import load_dotenv

load_dotenv()


import os
import sys

BASE_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.append(BASE_PATH)

from domains.swe.factory.common.config import Models, Paths, Settings
from domains.swe.factory.common.utils import ensure_dirs, write, build_dev_context
from domains.swe.factory.common.tasks import load_tasks, save_tasks, match_tasks

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler() 

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key

DEV_BE = """You're a backend developer.
In context, you have the source code for the project files you're creating, if they've already been created.
Work incrementally if the required files already exist: don't rewrite the project, but add/change what's needed for the task.
If {lang}==python, use FastAPI; if nodejs, use Express.
DO NOT output the programming language string in the response.
Output only the valid source code for each file being modified.
Output only the full source code for the files, not incrementally.
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
            {"role":"system","content": """Correct the JSON to make it valid. Return only JSON. Do not add any system information. Strictly follow the JSON format:"
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
        if header.startswith("backend/"):
            write(P.out + "/code/" + header, body.strip())

def run_backend(lang: str, task_id: str | None = None, title_query: str | None = None):
    print("[BE] Starting to work on backend tasks.")
    ensure_dirs()
    tasks = load_tasks()
    # llm = ChatOllama(model=M.dev, temperature=0.2, keep_alive=0)
    llm = ChatOpenAI(model=M.dev, temperature=0.2, api_key=API_KEY)

    repo_ctx = build_dev_context("BE")

    targets = match_tasks(tasks, assignee="BE", task_id=task_id, title_query=title_query, status_in=("Todo",))
    print(f"[BE] Found all open issues for backend development. Total: {len(targets)}.")
    for t in targets:
        print(f"[BE] Working on a task '{t.id} / {t.title}'.")
        files = t.artifacts or []
        desc  = t.description or ""
        messages = [
            {"role":"system","content": DEV_BE.format(lang=lang)},
            {"role":"user","content": f"Task: {t.id} — {t.title}\nDescription:\n{desc}\nExpected Files:\n" + "\n".join(files)},
            {"role":"user","content": "Repository context:\n" + repo_ctx}
        ]
        code = llm.invoke(messages, config={"callbacks": [langfuse_handler]}).content
        _write_blocks_to_fs(llm, code)
        print(f"[BE] Completed the task '{t.id} / {t.title}'.")
        t.status="Review"
    save_tasks(tasks)
    from domains.swe.factory.common.tasks import dump_tasks
    dump_tasks()
    print("[BE] Completed all open tasks for backend.")


if __name__=="__main__":
    run_backend("python", task_id="TASK-BE-5", title_query="")