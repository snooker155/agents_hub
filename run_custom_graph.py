import argparse
import json
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from common.utils import append_log_json


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_value(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    if key in node:
        return node.get(key)
    return data.get(key, default)


def _update_status(node_id: Optional[str]) -> None:
    import pathlib

    from common.config import Paths

    status_path = pathlib.Path(Paths().logs) / "graph_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as handle:
        json.dump({"active_node": node_id}, handle)


def _log_event(event_type: str, content: str, node: Optional[Dict[str, Any]] = None, status: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {
        "timestamp": _utc_now_iso(),
        "type": event_type,
        "content": content,
    }
    if node:
        payload["node_id"] = node.get("id")
        payload["agent_id"] = _node_value(node, "agent_id")
        payload["agent_name"] = _node_value(node, "agent_name") or _node_value(node, "label")
        payload["tag"] = _node_value(node, "domain") or "factory"
    if status:
        payload["status"] = status
    append_log_json(payload)


def _build_runner(agent_id: str, workspace: str, shared_prompt: str):
    from agents import ba, dev_backend, dev_frontend, dev_ops, pm, qa, sd, tl

    runners = {
        "factory-pm": lambda: pm.intake(shared_prompt, workspace=workspace),
        "factory-ba": lambda: ba.generate_brd(workspace=workspace),
        "factory-sd": lambda: sd.generate_all(workspace=workspace),
        "factory-tl": lambda: tl.split_tasks(workspace=workspace),
        "factory-be": lambda: dev_backend.run_backend(workspace=workspace),
        "factory-fe": lambda: dev_frontend.run_frontend(workspace=workspace),
        "factory-qa": lambda: qa.run_qa_agent(workspace=workspace),
        "factory-ops": lambda: dev_ops.run_ops_agent(workspace=workspace),
    }
    return runners.get(agent_id)


def _resolve_agent_id(node: Dict[str, Any]) -> Optional[str]:
    explicit = _node_value(node, "agent_id")
    if explicit:
        return explicit

    label = str(_node_value(node, "label", "")).lower()
    aliases = {
        "pm": "factory-pm",
        "product manager": "factory-pm",
        "ba": "factory-ba",
        "business analyst": "factory-ba",
        "sd": "factory-sd",
        "system designer": "factory-sd",
        "tl": "factory-tl",
        "team lead": "factory-tl",
        "be": "factory-be",
        "backend": "factory-be",
        "backend dev": "factory-be",
        "fe": "factory-fe",
        "frontend": "factory-fe",
        "frontend dev": "factory-fe",
        "qa": "factory-qa",
        "ops": "factory-ops",
        "devops": "factory-ops",
    }
    if label in aliases:
        return aliases[label]

    node_id = str(node.get("id", "")).lower()
    for alias, resolved in aliases.items():
        if node_id.startswith(alias.replace(" ", "-")):
            return resolved
    return None


def _topological_order(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[str]:
    adjacency = {node["id"]: [] for node in nodes}
    indegree = {node["id"]: 0 for node in nodes}

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source in adjacency and target in indegree:
            adjacency[source].append(target)
            indegree[target] += 1

    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    ordered: List[str] = []

    while queue:
        current = queue.popleft()
        ordered.append(current)
        for neighbor in adjacency.get(current, []):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    if len(ordered) != len(nodes):
        return [node["id"] for node in nodes]
    return ordered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", required=True, help="JSON string of graph structure")
    parser.add_argument("--desc", help="Initial description")
    parser.add_argument("--workspace", required=True, help="Path to workspace")
    args = parser.parse_args()

    if args.workspace:
        os.environ["WORKSPACE_ROOT"] = args.workspace

    load_dotenv()

    graph_data = json.loads(args.graph)
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    factory_task = graph_data.get("task", {}) if isinstance(graph_data.get("task"), dict) else {}
    shared_prompt = graph_data.get("shared_context") or factory_task.get("description") or args.desc or ""

    _log_event(
        "factory_run",
        factory_task.get("title") or "Starting factory graph execution",
        status="started",
    )

    for node_id in _topological_order(nodes, edges):
        node = next((item for item in nodes if item.get("id") == node_id), None)
        if not node:
            continue

        agent_id = _resolve_agent_id(node)
        if not agent_id:
            _log_event("factory_skip", f"Skipping node '{node_id}' because no agent is assigned.", node=node, status="skipped")
            continue

        runner = _build_runner(agent_id, args.workspace, shared_prompt)
        if not runner:
            _log_event("factory_skip", f"Skipping node '{node_id}' because agent '{agent_id}' is unsupported in graph mode.", node=node, status="skipped")
            continue

        _update_status(node_id)
        _log_event("agent_start", f"Running {_node_value(node, 'label', agent_id)}", node=node, status="running")

        try:
            runner()
            _log_event("agent_finish", f"Completed {_node_value(node, 'label', agent_id)}", node=node, status="completed")
        except Exception as exc:
            _log_event("agent_error", f"{_node_value(node, 'label', agent_id)} failed: {exc}", node=node, status="failed")

    _update_status(None)
    _log_event(
        "factory_run",
        factory_task.get("title") or "Factory graph execution finished",
        status="completed",
    )


if __name__ == "__main__":
    main()
