from langchain_ollama import ChatOllama
from datetime import datetime
from domains.swe.factory.common.config import Models, Settings, Paths
from domains.swe.factory.common.utils import ensure_dirs, write, append_log_md, append_log_json, load_json, save_json
from domains.swe.factory.common.tasks import Task, load_tasks, save_tasks, next_id
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler()

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key


PM_Q = "You are the PM. Formulate up to 5 clarifying questions regarding the scope/users/integrations/NFR/risks. Keep it short and numbered."

def intake(description: str, mode_full: bool | None=None):
    ensure_dirs()
    mode_full = S.mode_full if mode_full is None else mode_full
    # лог исходника
    append_log_md("Original description", description)
    append_log_json({"timestamp": datetime.now().isoformat(), "type":"initial_description", "content":description})
    if not mode_full:
        return
    # llm = ChatOllama(model=M.pm, temperature=0.2)
    llm = ChatOpenAI(model=M.pm, temperature=0.2, api_key=API_KEY)
    qs = llm.invoke([{"role":"system","content":PM_Q},{"role":"user","content":description}], config={"callbacks": [langfuse_handler]}).content
    append_log_md("PM clarifying questions", qs)
    append_log_json({"timestamp": datetime.now().isoformat(), "type":"pm_questions", "content":qs})
    # спросим ответы
    q_only = [ln.lstrip("0123456789). ").strip("-• ").strip() for ln in qs.splitlines() if ln.strip()]
    ans_lines=[]

    if q_only:
        import time
        input_path = os.path.join(P.logs, "pending_input.json")
        waiting_path = os.path.join(P.logs, "waiting_for_input.json")

        # Write what we are waiting for
        write(waiting_path, json.dumps({"questions": q_only}, ensure_ascii=False))

        print(f"[PM] Waiting for user input via dashboard...")
        while not os.path.exists(input_path):
            time.sleep(2)

        answers = load_json(input_path)
        for q in q_only:
            a = answers.get(q, "N/A")
            ans_lines.append(f"- **{q}**\n  Answer: {a}")

        # Cleanup
        if os.path.exists(input_path): os.remove(input_path)
        if os.path.exists(waiting_path): os.remove(waiting_path)

    if ans_lines:
        payload = "\n".join(ans_lines)
        append_log_md("User's answers", payload)
        append_log_json({"timestamp": datetime.now().isoformat(), "type":"user_answers", "content":payload})

def create_task(assignee: str, title: str, artifact_key: str|None=None, parent_id: str|None=None, payload: dict|None=None) -> Task:
    tasks = load_tasks()
    tid = next_id(tasks, f"TASK-{assignee}")
    t = Task(id=tid, title=title, assignee=assignee, artifact_key=artifact_key, parent_id=parent_id, payload=payload or {})
    tasks.append(t); save_tasks(tasks); return t

def ask_approval(prompt: str) -> bool:
    if not S.mode_full: return True
    while True:
        a = input(f"{prompt} [y/n]: ").strip().lower()
        if a in ("y","yes"): return True
        if a in ("n","no"): return False
        print("Type 'y' or 'n'.")
