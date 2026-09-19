"""
Agent instances — the live counterpart to a run.

A *run* is one unit of work an agent performed; it is a journal entry. An
*instance* is the running copy of the agent that performed it: it has an
identity, a lifetime that spans many runs, its own session context, and a
mailbox. Nodes and containers are *carriers* of instances, not instances
themselves — they keep their own pages; an instance links to its carrier.

Modules:
- ``store``    — persistence and querying of the ``instances`` table
- ``registry`` — the single registration point every execution channel calls
- ``history``  — rebuilds an instance's conversation context server-side
- ``inbox``    — messages addressed to an instance
"""
