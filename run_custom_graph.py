import json
import os
import argparse
from typing import List, Dict, Any

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, help="JSON string of graph structure")
    ap.add_argument("--desc", help="Initial description")
    ap.add_argument("--workspace", required=True, help="Path to workspace")
    args = ap.parse_args()

    if args.workspace:
        os.environ["WORKSPACE_ROOT"] = args.workspace

    graph_data = json.loads(args.graph)
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    from common.utils import ensure_dirs
    from agents import pm, ba, sd, tl
    from agents import dev_backend, dev_frontend, dev_ops, qa
    from common.utils import load_json
    from common.config import Settings, Paths
    from dotenv import load_dotenv

    load_dotenv()

    def update_status(node_id: str):
        import pathlib, json
        P = Paths()
        status_path = pathlib.Path(P.logs) / "graph_status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        with open(status_path, "w") as f:
            json.dump({"active_node": node_id}, f)

    ws = args.workspace

    agent_map = {
        "pm": lambda: pm.intake(args.desc or "", workspace=ws),
        "ba": lambda: ba.generate_brd(workspace=ws),
        "sd": lambda: sd.generate_all(workspace=ws),
        "tl": lambda: tl.split_tasks(workspace=ws),
        "be": lambda: dev_backend.run_backend(workspace=ws),
        "fe": lambda: dev_frontend.run_frontend(workspace=ws),
        "qa": lambda: qa.run_qa_agent(workspace=ws),
        "ops": lambda: dev_ops.run_ops_agent(workspace=ws),
    }

    def get_agent_key(node):
        label = node.get("data", {}).get("label", "").lower()
        if label in agent_map: return label
        node_id = node.get("id", "").lower()
        for key in agent_map:
            if node_id.startswith(key):
                return key
        return None

    adj = {n["id"]: [] for n in nodes}
    in_degree = {n["id"]: 0 for n in nodes}
    for e in edges:
        if e["source"] in adj and e["target"] in in_degree:
            adj[e["source"]].append(e["target"])
            in_degree[e["target"]] += 1

    queue = [n["id"] for n in nodes if in_degree.get(n["id"], 0) == 0]

    while queue:
        curr_id = queue.pop(0)
        node = next((n for n in nodes if n["id"] == curr_id), None)
        if not node: continue

        agent_key = get_agent_key(node)

        if agent_key:
            print(f"Executing agent: {agent_key} (node: {curr_id})")
            update_status(curr_id)
            try:
                agent_map[agent_key]()
            except Exception as e:
                print(f"Error executing {agent_key}: {e}")

        for neighbor in adj.get(curr_id, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

if __name__ == "__main__":
    main()
