"""
Turn an n8n workflow (its export JSON, or what the n8n API returns) into a
hub flow. Two ways, chosen by the person on the Flows page:

* ``run``: one ``run_n8n`` node bound to the workflow's Webhook or Chat
  trigger. The workflow keeps running in n8n; the hub starts it, waits for
  the answer and can put agents before and after it.
* ``convert``: the workflow's steps become hub nodes. An n8n step has no hub
  twin (an HTTP Request, a Gmail node and a Code node are not things the hub
  runs), so every step becomes the abstract agent node, an agent the person
  picks, with a task that carries what the step did: an AI step's prompt and
  system message, any other step's operation and settings. Triggers and
  sticky notes are dropped, No Op steps are wired through, a two way IF
  becomes a ``state_branch`` condition, and the models, tools and memories
  wired into an AI step are named in its task. The report says what became
  what, so nothing is lost silently.

Credentials never travel: n8n exports only name the credential a node uses,
and the importer keeps only that name.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from connectors.n8n.client import CHAT_TRIGGER_TYPE, WEBHOOK_TYPE, find_trigger

#: Node types that start a workflow and have no place inside a hub flow.
_TRIGGER_TYPES = {
    WEBHOOK_TYPE, CHAT_TRIGGER_TYPE, "n8n-nodes-base.manualTrigger", "n8n-nodes-base.start",
    "n8n-nodes-base.formTrigger", "n8n-nodes-base.executeWorkflowTrigger",
    "n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.cron", "n8n-nodes-base.interval",
}
_STICKY = "n8n-nodes-base.stickyNote"
_NOOP = "n8n-nodes-base.noOp"
_IF = "n8n-nodes-base.if"
_LANGCHAIN = "@n8n/n8n-nodes-langchain."

#: Where an AI step keeps its prompt and its system message, by parameter.
_PROMPT_KEYS = ("text", "prompt", "chatInput", "inputText")
_SYSTEM_KEYS = ("systemMessage", "system_message")

#: Settings that only matter inside n8n's editor, left out of a step's task.
_SKIP_PARAMS = {"options", "authentication", "nodeCredentialType", "notice", "jsCode", "pythonCode"}

#: n8n draws nodes about 220px apart; the hub's nodes are 90px wide.
_POSITION_SCALE = 0.6

_TASK_LIMIT = 1800


class N8nImportError(ValueError):
    """The input is not an n8n workflow, or cannot be run the way asked."""


def parse_workflow(raw: Any) -> Dict[str, Any]:
    """An n8n workflow from a dict or JSON text. A pasted export can be the
    workflow itself or n8n's ``{"data": workflow}`` / list wrapper."""
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise N8nImportError(f"Not valid JSON: {exc}") from exc
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("data"), dict) and "nodes" not in data:
        data = data["data"]
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise N8nImportError("This is not an n8n workflow: there is no nodes list")
    if not isinstance(data.get("connections") or {}, dict):
        raise N8nImportError("This is not an n8n workflow: connections is not an object")
    return data


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "step"


def _short_type(node_type: str) -> str:
    return (node_type or "").rsplit(".", 1)[-1]


def _is_trigger(node: Dict[str, Any]) -> bool:
    node_type = str(node.get("type") or "")
    return node_type in _TRIGGER_TYPES or node_type.lower().endswith("trigger")


def _position(node: Dict[str, Any]) -> Dict[str, float]:
    pos = node.get("position")
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        try:
            return {"x": float(pos[0]) * _POSITION_SCALE, "y": float(pos[1]) * _POSITION_SCALE}
        except (TypeError, ValueError):
            pass
    return {"x": 100.0, "y": 100.0}


def _plain(value: Any) -> str:
    """A parameter as text a person can read. An n8n expression (``=...``)
    keeps its body; one that is nothing but ``{{ ... }}`` carries no text of
    its own and comes back empty."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text.startswith("="):
        text = text[1:].strip()
        if re.fullmatch(r"\{\{.*\}\}", text, flags=re.S):
            return ""
    return text


def _find_param(params: Any, keys: Tuple[str, ...]) -> str:
    """The first non empty text under any of ``keys``, looking one level into
    ``options`` (where the AI Agent keeps its system message)."""
    if not isinstance(params, dict):
        return ""
    for key in keys:
        text = _plain(params.get(key))
        if text:
            return text
    return _find_param(params.get("options"), keys) if isinstance(params.get("options"), dict) else ""


def _messages(params: Dict[str, Any]) -> List[str]:
    """Prompt messages of an LLM chain / OpenAI node (``messages.values``)."""
    block = params.get("messages")
    values = block.get("values") if isinstance(block, dict) else block
    out: List[str] = []
    for item in values or []:
        if isinstance(item, dict):
            text = _plain(item.get("message") or item.get("content"))
            if text:
                out.append(text)
    return out


def _settings(params: Dict[str, Any]) -> str:
    kept = {k: v for k, v in params.items() if k not in _SKIP_PARAMS and v not in ("", None, {}, [])}
    if not kept:
        return ""
    text = json.dumps(kept, ensure_ascii=False, default=str)
    return text if len(text) <= 900 else text[:900] + " …"


def _credential_names(node: Dict[str, Any]) -> List[str]:
    creds = node.get("credentials")
    names: List[str] = []
    if isinstance(creds, dict):
        for kind, ref in creds.items():
            label = ref.get("name") if isinstance(ref, dict) else ""
            names.append(f"{label} ({kind})" if label else str(kind))
    return names


def _task_for(node: Dict[str, Any], helpers: List[str]) -> str:
    params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
    node_type = str(node.get("type") or "")
    lines: List[str] = []
    if node_type.startswith(_LANGCHAIN):
        system = _find_param(params, _SYSTEM_KEYS)
        prompt = _find_param(params, _PROMPT_KEYS)
        if system:
            lines.append(system)
        if prompt:
            lines.append(prompt)
        lines.extend(_messages(params))
        if not lines:
            lines.append(f"Do what the n8n step '{node.get('name')}' ({_short_type(node_type)}) did.")
    else:
        what = [str(params[k]) for k in ("resource", "operation") if isinstance(params.get(k), str)]
        lines.append(
            f"Do what the n8n step '{node.get('name')}' did: {_short_type(node_type)}"
            + (f" ({' '.join(what)})" if what else "") + "."
        )
        settings = _settings(params)
        if settings:
            lines.append(f"Its settings in n8n: {settings}")
        code = _plain(params.get("jsCode") or params.get("pythonCode"))
        if code:
            lines.append("Its code in n8n:\n" + code[:900])
    if helpers:
        lines.append("In n8n it used: " + ", ".join(helpers) + ".")
    creds = _credential_names(node)
    if creds:
        lines.append("Credentials it used in n8n (not imported): " + ", ".join(creds) + ".")
    if isinstance(node.get("notes"), str) and node["notes"].strip():
        lines.append("Note: " + node["notes"].strip())
    text = "\n\n".join(lines)
    return text if len(text) <= _TASK_LIMIT else text[:_TASK_LIMIT] + " …"


def _main_targets(connections: Dict[str, Any], source: str) -> List[List[str]]:
    """Target node names per output of ``source``'s main connections."""
    outputs = (connections.get(source) or {}).get("main") or []
    result: List[List[str]] = []
    for branch in outputs:
        names = [str(c.get("node")) for c in (branch or []) if isinstance(c, dict) and c.get("node")]
        result.append(names)
    return result


def convert(workflow: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(flow fields, report)`` for ``mode=convert``. The flow fields are
    name, description, nodes and edges in the shape the editor saves."""
    nodes_in = [n for n in workflow.get("nodes") or [] if isinstance(n, dict) and n.get("name")]
    connections = workflow.get("connections") or {}
    report: Dict[str, Any] = {"converted": [], "dropped": [], "warnings": []}
    notes: List[str] = []

    # Sub-nodes (a chat model, a tool, a memory) hang off an AI step through
    # an ai_* connection rather than main: they become words in that step's
    # task instead of nodes of their own.
    helpers: Dict[str, List[str]] = {}
    sub_nodes: set = set()
    for source, outs in connections.items():
        for conn_type, branches in (outs or {}).items():
            if conn_type == "main":
                continue
            for branch in branches or []:
                for c in branch or []:
                    if isinstance(c, dict) and c.get("node"):
                        sub_nodes.add(source)
                        label = conn_type.replace("ai_", "").replace("languageModel", "model")
                        helpers.setdefault(str(c["node"]), []).append(f"{source} ({label})")

    kept: Dict[str, Dict[str, Any]] = {}
    pass_through: set = set()
    used_ids: set = set()
    for node in nodes_in:
        name = str(node["name"])
        node_type = str(node.get("type") or "")
        if node.get("disabled"):
            report["dropped"].append({"name": name, "reason": "disabled in n8n"})
            continue
        if node_type == _STICKY:
            content = _plain((node.get("parameters") or {}).get("content"))
            if content:
                notes.append(content)
            continue
        if name in sub_nodes:
            report["dropped"].append({"name": name, "reason": "wired into an AI step; named in its task"})
            continue
        if _is_trigger(node):
            report["dropped"].append({"name": name, "reason": "trigger; the hub flow starts on its own"})
            continue
        if node_type == _NOOP:
            pass_through.add(name)
            report["dropped"].append({"name": name, "reason": "No Op; its neighbours are wired together"})
            continue
        base = _slug(name)
        node_id, n = base, 2
        while node_id in used_ids:
            node_id, n = f"{base}_{n}", n + 1
        used_ids.add(node_id)
        kept[name] = {"id": node_id, "node": node}

    def resolve(target: str, seen: Optional[set] = None) -> List[str]:
        """Kept node names ``target`` stands for, walking through No Op steps."""
        if target in kept:
            return [target]
        if target not in pass_through:
            return []
        seen = seen or set()
        if target in seen:
            return []
        seen.add(target)
        out: List[str] = []
        for branch in _main_targets(connections, target):
            for t in branch:
                out.extend(resolve(t, seen))
        return out

    nodes_out: List[Dict[str, Any]] = []
    edges_out: List[Dict[str, Any]] = []
    edge_keys: set = set()

    def add_edge(src_id: str, tgt_id: str) -> None:
        if src_id == tgt_id or (src_id, tgt_id) in edge_keys:
            return
        edge_keys.add((src_id, tgt_id))
        edges_out.append({"id": f"reactflow__edge-{src_id}-{tgt_id}", "source": src_id,
                          "target": tgt_id, "type": "smoothstep"})

    for name, entry in kept.items():
        node, node_id = entry["node"], entry["id"]
        node_type = str(node.get("type") or "")
        outputs = [[kept[t]["id"] for raw in branch for t in resolve(raw)]
                   for branch in _main_targets(connections, name)]
        for branch in outputs:
            for tgt in branch:
                add_edge(node_id, tgt)

        if node_type == _IF and len(outputs) == 2:
            flag = f"{_slug(name)}_ok"
            nodes_out.append({
                "id": node_id, "type": "flowNode", "position": _position(node),
                "data": {
                    "label": name, "description": f"n8n IF, routed on the state key {flag}",
                    "entity_id": "state_branch", "category": "condition", "domain": "n8n",
                    "input": [flag],
                    "config": {"key": flag, "if_true": (outputs[0] or [""])[0],
                               "if_false": (outputs[1] or [""])[0]},
                },
            })
            report["converted"].append({"name": name, "as": "condition"})
            report["warnings"].append(
                f"IF '{name}': its n8n condition does not carry over. The step before it has to "
                f"write true or false into the state key '{flag}'."
            )
            continue

        if node_type == _IF or len(outputs) > 1:
            report["warnings"].append(
                f"'{name}' chose between {len(outputs)} outputs in n8n; here every branch runs. "
                "Add a condition node if only one should."
            )
        nodes_out.append({
            "id": node_id, "type": "flowNode", "position": _position(node),
            "data": {
                "label": name,
                "description": f"From n8n: {_short_type(node_type)}",
                "agent_id": "", "category": "agent", "domain": "n8n",
                "nodeTask": _task_for(node, helpers.get(name, [])),
            },
        })
        report["converted"].append({
            "name": name, "as": "agent",
            "ai": node_type.startswith(_LANGCHAIN),
        })

    if not nodes_out:
        raise N8nImportError("The workflow has no steps to convert, only triggers and notes")
    trigger = find_trigger(workflow)
    if trigger:
        report["trigger"] = trigger
    description = str(workflow.get("description") or "").strip() or "\n\n".join(notes)[:1000]
    flow = {
        "name": str(workflow.get("name") or "n8n workflow").strip(),
        "description": description or "Imported from n8n",
        "nodes": nodes_out,
        "edges": edges_out,
    }
    return flow, report


def run_node_flow(workflow: Dict[str, Any], *, workflow_id: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(flow fields, report)`` for ``mode=run``: one ``run_n8n`` node.

    A workflow read from the instance (``workflow_id``) is named by id, so
    the node reads its trigger at run time. A pasted export is named by its
    trigger path, which needs no API key."""
    trigger = find_trigger(workflow)
    if trigger is None:
        raise N8nImportError(
            "This workflow has no Webhook or Chat trigger, so nothing outside n8n can start it. "
            "Add one in n8n, or convert the workflow to hub nodes instead."
        )
    name = str(workflow.get("name") or "n8n workflow").strip()
    config: Dict[str, Any] = {"trigger": trigger["kind"], "method": trigger.get("method") or "POST"}
    if workflow_id:
        config["workflow_id"] = str(workflow_id)
    else:
        config["webhook_path"] = trigger["path"]
        if workflow.get("id"):
            config["workflow_id"] = str(workflow["id"])
    node = {
        "id": "run_n8n", "type": "flowNode", "position": {"x": 160.0, "y": 140.0},
        "data": {
            "label": name, "description": "Runs in n8n", "entity_id": "run_n8n",
            "category": "container", "domain": "n8n", "output": ["result"], "config": config,
        },
    }
    report: Dict[str, Any] = {"trigger": trigger, "converted": [], "dropped": [], "warnings": []}
    if not workflow.get("active", True):
        report["warnings"].append("The workflow is not active in n8n. Activate it there before running the flow.")
    flow = {
        "name": name,
        "description": str(workflow.get("description") or "").strip() or "Runs the n8n workflow of the same name",
        "nodes": [node],
        "edges": [],
    }
    return flow, report


__all__ = ["N8nImportError", "parse_workflow", "convert", "run_node_flow"]
