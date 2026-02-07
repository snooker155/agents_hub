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

    # Simple topological sort for a custom graph
    # For now, we assume it's small and manageable

    # Map node labels to execution functions
    agent_map = {
        "pm": lambda: pm.intake(args.desc or ""),
        "ba": lambda: ba.generate_brd(),
        "sd": lambda: sd.generate_all(),
        "tl": lambda: tl.split_tasks(),
        "be": lambda: dev_backend.run_backend(load_json(Paths().plan + "/stack.json", default={"backend_lang":"python"})["backend_lang"]),
        "fe": lambda: dev_frontend.run_frontend(),
        "qa": lambda: qa.run_qa(),
        "ops": lambda: dev_ops.run_ops(),
    }

    # Helper to find agent type from label or id
    def get_agent_key(node):
        label = node.get("data", {}).get("label", "").lower()
        if label in agent_map: return label
        # fallback to id prefix
        node_id = node.get("id", "").lower()
        for key in agent_map:
            if node_id.startswith(key):
                return key
        return None

    # Topological sort
    visited = set()
    order = []

    def sort_node(node_id):
        if node_id in visited: return
        # find outgoing edges (wait, for topo sort we need incoming)
        # actually, standard topo sort uses outgoing but in reverse
        pass

    # Simplified: just run in the order they appear if no clear structure
    # or just use the edges to determine dependency.
    # For a POC, let's just run them one by one based on edges.

    adj = {n["id"]: [] for n in nodes}
    in_degree = {n["id"]: 0 for n in nodes}
    for e in edges:
        adj[e["source"]].append(e["target"])
        in_degree[e["target"]] += 1

    queue = [n["id"] for n in nodes if in_degree[n["id"]] == 0]

    while queue:
        curr_id = queue.pop(0)
        node = next(n for n in nodes if n["id"] == curr_id)
        agent_key = get_agent_key(node)

        if agent_key:
            print(f"Executing agent: {agent_key} (node: {curr_id})")
            update_status(curr_id)
            try:
                agent_map[agent_key]()
            except Exception as e:
                print(f"Error executing {agent_key}: {e}")

        for neighbor in adj[curr_id]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

if __name__ == "__main__":
    main()
