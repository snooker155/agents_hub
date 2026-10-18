"""
The lookup kinds of the assistant plan's waves 2 and 3, one module per area,
each registering its kinds with ``chat.lookup.register`` and its one-step
actions with ``chat.actions.register_action`` when imported.

- runtime: instances
- automation: guardrails, tool policy
- catalog: skills, MCP servers, the registry and marketplace
- admin: service-wide records for ``service_lookup`` only (health and the
  doctor, containers, web log, settings)
"""
from chat.lookup_kinds import admin, automation, catalog, runtime  # noqa: F401
