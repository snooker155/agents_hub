# Instances

An instance is a live copy of an agent: its own state, its own label, its own
history. Where an [agent](agents.md) is a definition and a
[node](nodes.md) is a process, an instance is the thing actually doing work.

## States

- **starting** — coming up
- **active** — running a turn right now
- **standby** — alive, waiting for the next message
- **finished** — completed its work
- **stopped** — ended deliberately
- **failed** — ended badly

`live` counts starting, active and standby together. That is the number to watch
when you want to know what is running.

## The timeline

An instance's timeline is what it has actually been doing: recent turns, which
tools it called, and how each run ended. This is the level at which "the agent
is stuck" becomes visible, because a stuck instance usually shows the same tool
call repeating.

## The inbox

Messages can be queued to an instance and are claimed one at a time. A message
sitting in the inbox of a standby instance means nothing picked it up, which is
usually a node problem rather than an agent problem.

Related: [nodes](nodes.md), [sessions-and-runs](sessions-and-runs.md), [service-health](service-health.md).
