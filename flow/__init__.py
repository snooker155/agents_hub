"""
Flow execution & management.

Self-contained package for defining, storing, and running agent flows
(typed-node graphs with shared state and conditional branching):

- ``registry``  — federated catalog of flow-usable entities (agents,
  processors, conditions, transforms). Discovers code entities under
  ``flow/entities/<category>/`` and federates agents from the agent registry.
- ``store``     — per-flow YAML(logic)+JSON(visual) persistence and migration.
- ``state``     — ``FlowState`` shared bag with mutability enforcement, plus the
  ``NodeResult`` / ``RunContext`` node contract.
- ``dispatch``  — node-type dispatch (agent vs. inline entity execution).
- ``launcher``  — launches a flow run as a subprocess (parent side).

The subprocess entry point that executes the graph lives in ``runtime.flow_run``.

Data lives under ``.agents_hub/flows/`` and ``.agents_hub/flow_entities.json``
(unchanged by this package's location).
"""
