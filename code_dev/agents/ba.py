import json
from langchain_ollama import ChatOllama
from common.config import Models, Settings, Paths
from common.utils import ensure_dirs, write, load_json, save_json, read
from datetime import datetime
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler()

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key

BA_JSON_PROMPT = """You're a business analyst. Below is the external context (user interaction).
Return STRICTLY valid JSON (BRD) according to the schema from the previous version. No comments outside the JSON."""
BA_MD_PROMPT = "You're a technical writer. Transform business requirements JSON into compact Markdown."

def generate_brd(mode_full: bool|None=None):
    print("[BA] Start generating BRD.")
    ensure_dirs()
    mode_full = S.mode_full if mode_full is None else mode_full
    ctx_md = read(P.logs + "/interaction_log.md", default="# Внешний контекст проекта\n")
    # llm_json = ChatOllama(model=M.ba_json, temperature=0.1, format=("json" if mode_full else None))
    llm_json = ChatOpenAI(model=M.ba_json, temperature=0.1, api_key=API_KEY)
    js = llm_json.invoke([{"role":"system","content":BA_JSON_PROMPT},{"role":"user","content":ctx_md}], config={"callbacks": [langfuse_handler]}).content
    try:
        brd = json.loads(js)
    except:
        if mode_full:
            # fixer = ChatOllama(model=M.ba_json, temperature=0.0)
            fixer = ChatOpenAI(model=M.ba_json, temperature=0.0, api_key=API_KEY)
            fixed = fixer.invoke([
                {"role":"system","content":"Correct the JSON to make it valid. Return only JSON."},
                {"role":"user","content":js}
            ], config={"callbacks": [langfuse_handler]}).content
            brd = json.loads(fixed)
        else:
            brd = {"project_name":"Draft","summary":"...", "objectives":[], "functional_requirements":[]}
    write(P.docs + "/BRD.json", json.dumps(brd, ensure_ascii=False, indent=2))
    # md = ChatOllama(model=M.ba_md, temperature=0.2).invoke([
    md = ChatOpenAI(model=M.ba_md, temperature=0.2, api_key=API_KEY).invoke([
        {"role":"system","content":BA_MD_PROMPT},
        {"role":"user","content":json.dumps(brd, ensure_ascii=False, indent=2)}
    ], config={"callbacks": [langfuse_handler]}).content
    write(P.docs + "/BRD.md", md)

    # Mark task as Done
    from common.tasks import load_tasks, save_tasks, dump_tasks
    tasks = load_tasks()
    for t in tasks:
        if t.assignee == "BA" and t.status == "Todo":
            t.status = "Done"
    save_tasks(tasks)
    dump_tasks()

    print("[BA] BRD generated.")
    return brd
