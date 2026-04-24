import argparse
import json
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def _log_event(
    event_type: str,
    content: str,
    node: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    input_text: Optional[str] = None,
    output_text: Optional[str] = None,
) -> None:
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
    if input_text is not None:
        payload["input"] = input_text
    if output_text is not None:
        payload["output"] = output_text
    append_log_json(payload)


# Maps factory-style agent IDs used in flow nodes to YAML definition IDs
_FACTORY_AGENT_MAP: Dict[str, str] = {
    "factory-pm": "pm_agent",
    "factory-ba": "ba_agent",
    "factory-sd": "sd_agent",
    "factory-tl": "tl_agent",
    "factory-be": "dev_agent",
    "factory-fe": "dev_agent",
    "factory-qa": "qa_agent",
    "factory-ops": "devops_agent",
}


def _run_agent(agent_id: str, workspace: str, prompt: str) -> str:
    """Create and run an agent with the given prompt. Returns the agent's text output."""
    from agents.agent_factory import create_agent

    yaml_id = _FACTORY_AGENT_MAP.get(agent_id, agent_id)
    agent = create_agent(yaml_id, workspace=workspace)
    result = agent.run(prompt)
    if result.ok:
        return result.agent_output or ""
    raise RuntimeError(result.error or f"Agent '{agent_id}' returned an error with no message")


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


def _build_predecessors(edges: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Return a mapping of node_id -> list of predecessor node_ids."""
    preds: Dict[str, List[str]] = {}
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            preds.setdefault(target, []).append(source)
    return preds


def _build_agent_input(
    shared_prompt: str,
    node_id: str,
    predecessors: Dict[str, List[str]],
    node_outputs: Dict[str, str],
) -> str:
    """
    Build the prompt for an agent.

    - First nodes (no predecessors) receive only the shared_prompt.
    - Subsequent nodes receive the shared_prompt plus the output(s) from their
      predecessor(s), clearly delimited so the agent knows what came before.
    """
    pred_ids = predecessors.get(node_id, [])
    pred_outputs = [(pid, node_outputs[pid]) for pid in pred_ids if pid in node_outputs]

    if not pred_outputs:
        return shared_prompt

    parts = [shared_prompt, ""]
    parts.append("=" * 60)
    parts.append("CONTEXT FROM PREVIOUS AGENTS IN THIS FLOW")
    parts.append("=" * 60)
    for pred_id, pred_out in pred_outputs:
        parts.append(f"\n[{pred_id}]:\n{pred_out}\n")
    parts.append("=" * 60)
    parts.append("\nContinue the work based on the above context.")
    return "\n".join(parts)


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

    predecessors = _build_predecessors(edges)
    # Stores the text output of each successfully completed node, keyed by node_id
    node_outputs: Dict[str, str] = {}

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
            _log_event(
                "factory_skip",
                f"Skipping node '{node_id}' because no agent is assigned.",
                node=node,
                status="skipped",
            )
            continue

        agent_input = _build_agent_input(shared_prompt, node_id, predecessors, node_outputs)
        agent_label = _node_value(node, "label", agent_id)

        _update_status(node_id)
        _log_event(
            "agent_start",
            f"Running {agent_label}",
            node=node,
            status="running",
            input_text=agent_input,
        )

        try:
            output = _run_agent(agent_id, args.workspace, agent_input)
            node_outputs[node_id] = output
            _log_event(
                "agent_finish",
                f"Completed {agent_label}",
                node=node,
                status="completed",
                input_text=agent_input,
                output_text=output,
            )
        except Exception as exc:
            error_msg = str(exc)
            _log_event(
                "agent_error",
                f"{agent_label} failed: {error_msg}",
                node=node,
                status="failed",
                input_text=agent_input,
                output_text=error_msg,
            )

    _update_status(None)
    _log_event(
        "factory_run",
        factory_task.get("title") or "Factory graph execution finished",
        status="completed",
    )


if __name__ == "__main__":
    main()
