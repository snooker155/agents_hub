"""Container entity: run an n8n workflow and wait for its answer.

The workflow keeps running in n8n; this node starts it through the trigger it
already listens on (a Webhook or a Chat trigger) on the n8n connector in
effect for the run's workspace (connectors/n8n), and writes what it answered
into the output key. With ``workflow_id`` and an API key on the connector
the trigger is read from the workflow itself at run time, so renaming the
webhook path in n8n does not break the flow; ``webhook_path`` names it by
hand when there is no API key.

The node never takes a URL: the path is joined to the connector's base URL,
so a flow cannot point the hub at another host.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from flow.entities.containers import _nested
from flow.registry import FlowEntitySpec

SPEC = FlowEntitySpec(
    id="run_n8n",
    name="Run n8n workflow",
    category="container",
    group="n8n",
    entrypoint="flow.entities.containers.run_n8n:run",
    description=(
        "Start an n8n workflow through its Webhook or Chat trigger and wait for "
        "its answer. The answer lands in the output key."
    ),
    inputs=[],
    outputs=["result"],
    config_schema={
        "workflow_id": {
            "type": "str", "default": "", "source": "n8n_workflows",
            "description": "The n8n workflow. Its trigger is read through the API key.",
        },
        "webhook_path": {
            "type": "str", "default": "",
            "description": "The trigger path when there is no API key: my-hook, or <webhookId>/chat.",
        },
        "trigger": {
            "type": "str", "default": "auto",
            "description": "auto, webhook or chat.",
        },
        "method": {
            "type": "str", "default": "POST",
            "description": "HTTP method of the Webhook trigger.",
        },
        "goal": {
            "type": "str", "default": "",
            "description": "The message sent to the workflow. {state_key} is filled from flow state.",
        },
        "send_keys": {
            "type": "list", "default": [],
            "description": "State keys sent to a Webhook trigger as JSON.",
        },
    },
    icon="workflow",
)


def _resolve_trigger(client: Any, cfg: Dict[str, Any]) -> Dict[str, Any]:
    from connectors.n8n.client import N8nError, find_trigger

    wanted = str(cfg.get("trigger") or "auto").strip().lower()
    path = str(cfg.get("webhook_path") or "").strip()
    method = str(cfg.get("method") or "POST").strip().upper() or "POST"
    if path:
        kind = wanted if wanted in ("webhook", "chat") else ("chat" if path.endswith("/chat") else "webhook")
        return {"kind": kind, "path": path, "method": method}
    workflow_id = str(cfg.get("workflow_id") or "").strip()
    if not workflow_id:
        raise N8nError("The n8n node needs a workflow or a webhook path")
    workflow = client.get_workflow(workflow_id)
    trigger = find_trigger(workflow)
    if trigger is None:
        raise N8nError(
            f"n8n workflow '{workflow.get('name') or workflow_id}' has no Webhook or Chat "
            "trigger. Add one in n8n so the hub can start it."
        )
    if not workflow.get("active", True):
        raise N8nError(
            f"n8n workflow '{workflow.get('name') or workflow_id}' is not active. Activate it "
            "in n8n so its trigger listens."
        )
    return trigger


def run(state: Any, config: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    from connectors.n8n import n8n_client
    from connectors.n8n.client import N8nError, answer_of

    cfg = config or {}
    workspace = _nested.workspace_name(ctx)
    goal = _nested.fill(str(cfg.get("goal") or "").strip(), state)
    try:
        client = n8n_client(workspace or None)
        trigger = _resolve_trigger(client, cfg)
        payload: Dict[str, Any] = {"goal": goal}
        sent = _nested.seed_from(state, _nested.as_keys(cfg.get("send_keys")))
        if sent:
            payload["state"] = sent
        run_id = str(getattr(ctx, "run_id", "") or "")
        if run_id:
            payload["flow_run_id"] = run_id
        response = client.run(
            trigger["path"], kind=trigger["kind"], method=trigger.get("method") or "POST",
            payload=payload, message=goal or json.dumps(sent, default=str),
            session_id=run_id, timeout=_nested._timeout(cfg),
        )
    except N8nError as exc:
        raise _nested.NestedRunError(str(exc)) from exc
    return {
        "result": answer_of(response),
        "child_run_id": str(cfg.get("workflow_id") or trigger["path"]),
        "child_kind": "n8n",
    }
