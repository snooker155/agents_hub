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

DEV_OPS = """You're a DevOps engineer.
Use the current project structure (below) to prepare working configs without breaking existing paths and names.
Required: docker-compose, CI pipeline, OpenAPI publishing, .env.example, deploy scripts, etc.
In context, you have the source code for the project files you're creating, if they've already been created.
Work incrementally if the required files already exist: don't rewrite the project, but add/change what's needed for the task.
DO NOT OUTPUT a string with the programming language in the response.
Output only the valid source code for each file being modified.
Output only the full source code for the files, not as an increment.
Output ONLY modified/new files strictly in JSON format:
{{
    "files": 
        [
            {{"path": path, "content": code}},
        ]
}}
Paths start with ops/
"""
# """
# example:
# ops/docker-compose.yml
# ops/.env.example
# ops/.github/workflows/ci.yml
# ops/scripts/deploy.sh
# ops/docs/openapi.html
# """

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
        print("[OPS] Nothing to change.") 
        return
    for file in arr['files']:
        header = file['path']
        # body = parts[i+1] if i+1 < len(parts) else ""
        body = file['content']
        if header.startswith("ops/"):
            write(P.out + "/" + header, body.strip())

def run_ops(task_id: str | None = None, title_query: str | None = None):
    """If task_id/title_query are not specified, OPS will process ALL Todo tasks."""
    print("[OPS] Starting work on operations tasks.")
    ensure_dirs()
    tasks = load_tasks()
    # llm = ChatOllama(model=M.dev, temperature=0.2)
    llm = ChatOpenAI(model=M.dev, temperature=0.2, api_key=API_KEY)

    repo_ctx = build_dev_context("OPS")
    
    targets = match_tasks(tasks, assignee="OPS", task_id=task_id, title_query=title_query, status_in=("Todo",))
    print(f"[OPS] Found all open tasks for development operations. Total: {len(targets)}.")
    for t in targets:
        print(f"[OPS] I'm working on a task '{t.id} / {t.title}'.")
        files = t.artifacts or []
        desc  = t.description or ""
        messages = [
            {"role":"system","content": DEV_OPS},
            {"role":"user","content": f"Task: {t.id} — {t.title}\nDescription:\n{desc}\nExpected files:\n" + "\n".join(files)},
            {"role":"user","content": "Repository context:\n" + repo_ctx}
        ]
        code = llm.invoke(messages, config={"callbacks": [langfuse_handler]}).content
        _write_blocks_to_fs(llm, code)
        t.status = "Review"
        print(f"[OPS] Task completed '{t.id} / {t.title}'.")
    save_tasks(tasks)
    from domains.swe.factory.common.tasks import dump_tasks
    dump_tasks()
    print("[OPS] Completed all open tasks for operations.")

if __name__=="__main__":
    run_ops(task_id="TASK-OPS-2", title_query="")