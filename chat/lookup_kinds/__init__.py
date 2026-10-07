"""
The lookup kinds of the assistant plan's waves 2 and 3, one module per area,
each registering its kinds with ``chat.lookup.register`` and its one-step
actions with ``chat.actions.register_action`` when imported.

- runtime: instances, services, deployments, environments, browser sessions
- automation: watchers, proactive agents, evals, guardrails, tool policy
- catalog: connections, skills, MCP servers, widgets, the registry and
  marketplace, the person's own account
- admin: service-wide records for ``service_lookup`` only (users, groups,
  the audit trail, health and the doctor, containers, web log, settings,
  the cluster)
"""
from chat.lookup_kinds import admin, automation, catalog, runtime  # noqa: F401
