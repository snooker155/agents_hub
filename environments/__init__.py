"""Environments: named execution profiles a run, a node or a scheduled job
executes in (docs/environments.md).

An environment says *where* and *under what fence* an agent's process runs,
independently of *which* agent it is: the execution mode (the workspace's, or
pinned to local or docker), for docker the base image and extra pip packages,
the network policy (unrestricted, none, or limited to a list of hosts), the
container limits (memory, cpus, pids) and a few plain environment variables.
The agent definition stays about behaviour; the environment is the
infrastructure half, chosen per task, per node or per scheduled job, with a
default per workspace.

Modules:

- ``models``: the pydantic records and their validation.
- ``store``: the ``environments`` DocStore collection.
- ``service``: create, update, archive, delete, defaults, resolution, usage.
- ``launch``: what a launch gets from an environment (environment variables,
  the docker options, the execution mode); called by agents/agent_launcher.py
  and managers/node_manager.py.
- ``egress``: the optional egress proxy that holds a ``limited`` network to
  its allowlist for clients that honour the proxy variables.
"""
