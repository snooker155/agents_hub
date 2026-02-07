from langgraph.graph import StateGraph, END
from pydantic import BaseModel
import os
import argparse

class S(BaseModel):
    desc: str
    done: bool=False
    workspace: str

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desc", required=True)
    ap.add_argument("--mode", choices=["full","simple"], default="full")
    ap.add_argument("--workspace", help="Path to workspace")
    args = ap.parse_args()

    if args.workspace:
        os.environ["WORKSPACE_ROOT"] = args.workspace

    workspace_path = args.workspace or os.environ.get("WORKSPACE_ROOT", "./out")

    from common.utils import ensure_dirs
    from agents import pm, ba, sd, tl
    from agents import dev_backend, dev_frontend, dev_ops, qa
    from common.utils import load_json
    from common.config import Settings, Paths
    from dotenv import load_dotenv

    load_dotenv()

    def update_status(node: str):
        import pathlib, json
        P = Paths()
        status_path = pathlib.Path(P.logs) / "graph_status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        with open(status_path, "w") as f:
            json.dump({"active_node": node}, f)

    def node_pm_intake(s:S): update_status("pm_intake"); pm.intake(s.desc, workspace=s.workspace); return s
    def node_ba(s:S): update_status("ba_generate"); ba.generate_brd(workspace=s.workspace); return s
    def node_sd(s:S): update_status("sd_generate"); sd.generate_all(workspace=s.workspace); return s
    def node_tl(s:S): update_status("tl_planning"); tl.split_tasks(workspace=s.workspace); return s
    def node_devs(s:S):
        update_status("devs")
        dev_backend.run_backend(workspace=s.workspace)
        dev_frontend.run_frontend(workspace=s.workspace)
        dev_ops.run_ops_agent(workspace=s.workspace)
        qa.run_qa_agent(workspace=s.workspace)
        return s

    Settings.mode_full = (args.mode=="full")
    ensure_dirs()

    graph = StateGraph(S)
    graph.add_node("pm_intake", node_pm_intake)
    graph.add_node("ba_generate", node_ba)
    graph.add_node("sd_generate", node_sd)
    graph.add_node("tl_planning", node_tl)
    graph.add_node("devs", node_devs)

    graph.set_entry_point("pm_intake")
    graph.add_edge("pm_intake","ba_generate")
    graph.add_edge("ba_generate","sd_generate")
    graph.add_edge("sd_generate","tl_planning")
    graph.add_edge("tl_planning","devs")
    graph.add_edge("devs", END)

    app = graph.compile()
    app.invoke(S(desc=args.desc, workspace=workspace_path))

if __name__=="__main__":
    main()
