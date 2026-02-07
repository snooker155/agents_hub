import json
from langchain_ollama import ChatOllama
from domains.swe.factory.common.config import Models, Paths, Settings
from domains.swe.factory.common.utils import ensure_dirs, write, read
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

from langfuse.langchain import CallbackHandler
 
langfuse_handler = CallbackHandler()

M, S, P = Models(), Settings(), Paths()
API_KEY = S.openai_api_key

SD_SPECS = {
  "sd_spec_md": ("docs/SD_tech_spec.md", "You are a Solution Designer. Create a technical specification (Markdown)."),
  "sd_tech_md": ("docs/SD_tech_choices.md", "You are a Solution Designer. Select technologies with justification."),
  "sd_ddl_sql": ("docs/SD_data_model.sql", "You are a Solution Designer. SQL DDL (PostgreSQL) for a key model."),
  "sd_wf_md": ("docs/SD_workflows.md", "You are a Solution Designer. Mermaid diagrams of key workflows."),
  "sd_api_yaml": ("docs/SD_openapi.yaml", "You are a Solution Designer. OpenAPI 3.1 YAML with at least 3 endpoints. Return pure YAML."),
  "sd_arch_md": ("docs/SD_architecture.md", "You are a Solution Designer. Architectural Description + Mermaid Components.")
}

def generate_all():
    ensure_dirs()
    brd = read(P.docs + "/BRD.json")
    for key,(rel, sys_prompt) in SD_SPECS.items():
        print(f"[SD] Start generating ${key}.")
        # content = ChatOllama(model=M.sd, temperature=0.1).invoke(
        content = ChatOpenAI(model=M.sd, temperature=0.1, api_key=API_KEY).invoke(
            [{"role":"system","content":sys_prompt},{"role":"user","content":brd}], config={"callbacks": [langfuse_handler]}
        ).content
        write(P.out + "/" + rel, content)
        print(f"[SD] ${key} generated.")

    # Mark SD tasks as Done
    from domains.swe.factory.common.tasks import load_tasks, save_tasks, dump_tasks
    tasks = load_tasks()
    for t in tasks:
        if t.assignee == "SD" and t.status == "Todo":
            t.status = "Done"
    save_tasks(tasks)
    dump_tasks()