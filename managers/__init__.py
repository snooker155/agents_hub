"""
Managers: stateful lifecycle managers for runtime entities.

These own the shared state stores and coordinate the lifecycle of the entities
they manage:

- ``run_manager``       — shared run-state store, lifecycle tracking, stop coordination
- ``container_manager`` — Docker container lifecycle

A resident instance's own carrier, the process or container it runs in
(independent of tasks), is managed by ``instances.carrier`` instead of a
manager here.

They depend on the ``agents`` package (factory, registry, base) but not the
reverse, and are consumed by ``runtime`` (runners/launchers) and the dashboard.
"""
