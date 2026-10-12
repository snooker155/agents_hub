"""
n8n as a credential connector (connectors/credentials.py): a base URL the
hub reaches out to, plus an optional API key and webhook header.

With the base URL alone a flow can run an n8n workflow through its Webhook
or Chat trigger (flow/entities/containers/run_n8n.py). The API key adds the
read side: list the instance's workflows and import one as a hub flow
(flow/n8n_import.py, POST /api/flows/import/n8n). The client is in
connectors/n8n/client.py.

Each lives in the workspace that defines it, the default workspace's
everywhere else (connectors/channels/store.py).
"""
from __future__ import annotations

from typing import Any, Optional

from connectors.channels.registry import ConfigField
from connectors.channels.store import ChannelStore, in_workspace
from connectors.credentials import CredentialSpec

N8N_STORE = ChannelStore("n8n", secret_fields=("api_key", "webhook_header_value"))


def n8n_client(workspace: Optional[str] = None):
    """An ``N8nClient`` with the n8n connector in effect in ``workspace`` (the
    running code's when None). Raises ``N8nError`` when none is configured."""
    from .client import N8nClient

    store = N8N_STORE
    if workspace is not None:
        store = N8N_STORE.for_workspace(in_workspace(workspace, N8N_STORE.effective_workspace))
    return N8nClient(
        store.get("base_url"), store.get("api_key"),
        header_name=store.get("webhook_header_name"),
        header_value=store.get("webhook_header_value"),
    )


def _test() -> dict[str, Any]:
    from .client import N8nError

    try:
        return n8n_client().test_connection()
    except N8nError as exc:
        return {"ok": False, "error": str(exc)}


CREDENTIALS = CredentialSpec(
    name="n8n",
    store=N8N_STORE,
    fields=[
        ConfigField("base_url", required=True, placeholder="http://localhost:5678"),
        ConfigField("api_key", secret=True, kind="password",
                    placeholder="Settings > n8n API in n8n; needed to list and import workflows"),
        ConfigField("webhook_header_name", placeholder="Only when the Webhook node uses Header Auth"),
        ConfigField("webhook_header_value", secret=True, kind="password"),
    ],
    test=_test,
    required=("base_url",),
)

__all__ = ["CREDENTIALS", "N8N_STORE", "n8n_client"]
