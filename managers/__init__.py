"""
Managers: stateful lifecycle managers for runtime entities.

These own the shared state stores and coordinate the lifecycle of the entities
they manage:

- ``run_manager``       — shared run-state store, lifecycle tracking, stop coordination
- ``node_manager``      — manages long-running agent nodes (independent of tasks)
- ``container_manager`` — Docker container lifecycle

They depend on the ``agents`` package (factory, registry, base) but not the
reverse, and are consumed by ``runtime`` (runners/launchers) and the dashboard.
"""
