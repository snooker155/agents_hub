"""
Runtime: agent run orchestration and infrastructure.

Everything around *running* agents that is not agent definition/build/execution
itself:

- ``node_run``     — subprocess that runs inside an agent node
- ``http_server``  — HTTP server for agents running in containers
- ``agent_run``    — subprocess entry point that runs a single agent task
- ``flow_run``     — subprocess entry point that executes a flow (DAG)

Stateful lifecycle managers (``run_manager``, ``node_manager``,
``container_manager``) live in the ``managers`` package; the task-run launcher
(``agent_launcher``) lives in the ``agents`` package; external channel
integrations (Telegram) live under ``connectors``.

These depend on the ``agents`` package (factory, registry, base) but not the
reverse.
"""
