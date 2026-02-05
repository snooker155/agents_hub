from langgraph.graph import StateGraph, END
from pydantic import BaseModel
from common.utils import ensure_dirs
from agents import pm, ba, sd, tl
from agents import dev_backend, dev_frontend, dev_ops, qa
from common.utils import load_json
from common.config import Settings, Paths
from common.tasks import load_tasks
from dotenv import load_dotenv

load_dotenv()

from langfuse import get_client
 
langfuse = get_client()
 
# Verify connection, do not use in production as this is a synchronous call
if langfuse.auth_check():
    print("Langfuse client is authenticated and ready!")
else:
    print("Authentication failed. Please check your credentials and host.")


class S(BaseModel):
    desc: str
    done: bool=False

def update_status(node: str):
    import pathlib, json
    P = Paths()
    status_path = pathlib.Path(P.logs) / "graph_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with open(status_path, "w") as f:
        json.dump({"active_node": node}, f)

def node_pm_intake(s:S): update_status("pm_intake"); pm.intake(s.desc, mode_full=Settings.mode_full); return s
def node_ba(s:S): update_status("ba_generate"); ba.generate_brd(mode_full=Settings.mode_full); return s
def node_sd(s:S): update_status("sd_generate"); sd.generate_all(); return s
def node_tl_stack(s:S): update_status("tl_choose"); tl.choose_stack(); return s
def node_tl_split(s:S): update_status("tl_split"); tl.split_tasks(); return s
def node_tl_scaffold(s:S): update_status("tl_scaffold"); tl.scaffold_env(); return s
def node_devs(s:S):
    update_status("devs")
    stack = load_json(Paths().plan + "/stack.json", default={"need_ui":False,"backend_lang":"python"})
    dev_backend.run_backend(stack["backend_lang"])
    if stack["need_ui"]:
        dev_frontend.run_frontend()
    dev_ops.run_ops()
    # сразу сгенерируем QA тесты (без проверок, например)
    qa.run_qa(run_checks=False)
    return s
def node_tl_review(s:S):
    update_status("tl_review")
    # перевод всех задач в Review уже сделан dev*; тут ничем не управляем — показано как узел-заглушка
    # tl.review_code()
    return s

graph = StateGraph(S)
graph.add_node("pm_intake", node_pm_intake)
graph.add_node("ba_generate", node_ba)
graph.add_node("sd_generate", node_sd)
graph.add_node("tl_choose", node_tl_stack)
graph.add_node("tl_split", node_tl_split)
graph.add_node("tl_scaffold", node_tl_scaffold)
graph.add_node("devs", node_devs)
graph.add_node("tl_review", node_tl_review)

graph.set_entry_point("pm_intake")
graph.add_edge("pm_intake","ba_generate")
graph.add_edge("ba_generate","sd_generate")
graph.add_edge("sd_generate","tl_choose")
graph.add_edge("tl_choose","tl_split")
graph.add_edge("tl_split","tl_scaffold")
graph.add_edge("tl_scaffold","devs")
graph.add_edge("devs","tl_review")
graph.add_edge("tl_review", END)

app = graph.compile()

if __name__=="__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--desc", required=True)
    ap.add_argument("--mode", choices=["full","simple"], default="full")
    args = ap.parse_args()
    Settings.mode_full = (args.mode=="full")
    ensure_dirs()
    app.invoke(S(desc=args.desc))
