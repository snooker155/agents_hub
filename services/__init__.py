"""
Services: an agent kept running as a set of replicas (docs/services.md).

A resident instance (instances/carrier.py) is one copy an operator starts by
hand. A service is the *desired state* instead: which agent, in which
workspace and environment, how many replicas at least and at most, how many
conversations each answers at once, whether they take tasks, when an idle
replica is stopped, a money cap per turn and a version pin. The supervisor
(services/supervisor.py) keeps resident instances matching it; those are the
service's replicas and carry its id. A service may also have a public
address of its own, served by the hub, that any replica answers.

A service with no agent is a **runner**: it answers any agent's chat turn, a
flow's or a team's, in a process of its own. Every workspace gets one on
demand (services/store.py ensure_runner) and that is where a chat turn goes
when the agent has no service of its own there (chat/routing.py), so the
backend process never runs an agent and the first message never waits for a
process to boot.
"""
