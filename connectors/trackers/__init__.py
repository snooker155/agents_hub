"""
Issue trackers: Jira Cloud and Linear, as credential connectors
(connectors/credentials.py) — a base URL/token the hub reaches out to, with
no inbound loop of its own, the same shape connectors/git uses for GitHub
and GitLab but registered through CredentialSpec rather than ChannelSpec,
since there is no bot to run.

Jira authenticates with HTTP basic auth (the account email plus an API
token); Linear with a single API key sent as the raw Authorization header
(no "Bearer" prefix — that is how Linear's GraphQL API expects it). Both
``test`` callbacks build a provider client from the stored config and call
its own ``test_connection()`` (connectors/trackers/providers.py), so the
"is this configured correctly" check and the real read path share one piece
of code.

Everything that actually talks to Jira's or Linear's APIs lives in providers.py;
this module only wires the stored config (fields, secrets) to the generic
``/api/connectors/jira`` and ``/api/connectors/linear`` routes.
"""
from __future__ import annotations

from typing import Any

from connectors.channels.registry import ConfigField
from connectors.channels.store import ChannelStore
from connectors.credentials import CredentialSpec

JIRA_STORE = ChannelStore("jira", secret_fields=("api_token",))
LINEAR_STORE = ChannelStore("linear", secret_fields=("api_key",))


def _jira_test() -> dict[str, Any]:
    from .providers import JiraProvider, TrackerError

    try:
        provider = JiraProvider.from_store()
        return provider.test_connection()
    except TrackerError as e:
        return {"ok": False, "error": str(e)}


def _linear_test() -> dict[str, Any]:
    from .providers import LinearProvider, TrackerError

    try:
        provider = LinearProvider.from_store()
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

LINEAR = CredentialSpec(
    name="linear",
    store=LINEAR_STORE,
    fields=[
        ConfigField("api_key", secret=True, kind="password", required=True),
    ],
    test=_linear_test,
    required=("api_key",),
)

CREDENTIALS_LIST = [JIRA, LINEAR]

__all__ = ["JIRA", "LINEAR", "CREDENTIALS_LIST", "JIRA_STORE", "LINEAR_STORE"]
