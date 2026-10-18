"""
Issue tracker: Jira Cloud, as a credential connector
(connectors/credentials.py), a base URL and token the hub reaches out to,
with no inbound loop of its own.

Jira authenticates with HTTP basic auth (the account email plus an API
token). The ``test`` callback builds a provider client from the stored config
and calls its own ``test_connection()`` (connectors/trackers/providers.py),
so the "is this configured correctly" check and the real read path share one
piece of code.

Everything that actually talks to Jira's API lives in providers.py; this
module only wires the stored config (fields, secrets) to the generic
``/api/connectors/jira`` routes.
"""
from __future__ import annotations

from typing import Any

from connectors.credentials import CredentialSpec
from connectors.store import ConfigField, ConnectorStore

JIRA_STORE = ConnectorStore("jira", secret_fields=("api_token",))


def _jira_test() -> dict[str, Any]:
    from .providers import JiraProvider, TrackerError

    try:
        provider = JiraProvider.from_store()
        return provider.test_connection()
    except TrackerError as e:
        return {"ok": False, "error": str(e)}


JIRA = CredentialSpec(
    name="jira",
    store=JIRA_STORE,
    fields=[
        ConfigField("base_url", required=True, placeholder="https://acme.atlassian.net"),
        ConfigField("email", required=True, placeholder="you@company.com"),
        ConfigField("api_token", secret=True, kind="password", required=True),
    ],
    test=_jira_test,
    required=("base_url", "email", "api_token"),
)

CREDENTIALS_LIST = [JIRA]

__all__ = ["JIRA", "CREDENTIALS_LIST", "JIRA_STORE"]
