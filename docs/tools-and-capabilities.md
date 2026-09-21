# Tools and capabilities

Tools are granted to an agent by name. Nothing is granted by default, including
the calculator.

## The capability model

Every tool is classified by what it grants, not by what it is called:

- **`ingests_untrusted`** — pulls text the operator does not control into the
  agent's context (web fetches, page content, run logs, inbound chat messages).
- **`reads_private`** — can read data the operator would not want published
  (files, memory, tasks, run records).
- **`can_exfiltrate`** — can move agent-chosen bytes outside the system (a
  fetched URL carries its query string; a notification carries its payload).

An agent holding **all three** is a data-exfiltration primitive: text it ingests
can instruct it to collect secrets and send them out, with nothing in the loop to
stop it. That combination is **blocked**, not warned about.

Two of the three produce a warning instead. A web agent inherently ingests and
can exfiltrate; that is what a web agent is.

## Why capabilities and not tool names

`run_shell` alone is the whole trifecta: `curl` ingests and exfiltrates, `cat`
reads. A rule that only fires when three *named* tools co-occur would do nothing
against the single most dangerous tool.

## Consequences you will actually hit

- **A web agent cannot also read your files or memory.** Split the work: one
  agent fetches, another reasons over the result. That is exactly why the Web
  Search Agent and the Researcher Agent are separate.
- **What a web tool actually read is recorded.** Every `web_search` and
  `fetch_url` call, with the text handed back and the flags raised against it,
  lands in the [web request log](web-logs.md).
- **Log readers count as ingesting untrusted content.** A run log holds whatever
  that run handled, including pages it fetched. So the Service Agent, which
  reads every log, is given no outbound channel at all.
- **Adding a tool in the editor can be refused.** The message names the
  capabilities that collided.

There is an override for a combination you have deliberately accepted, and under
a stricter setting it is only honoured for container-isolated, network-free runs.

## Delegation counts as holding the capability

An agent that cannot itself exfiltrate, but can hand a request to an agent
that can, effectively can. `run_agent_tool`, `wait_for_agent_tool`,
`run_flow_tool`, `run_team_tool`, `run_loop_tool` and `run_scenario_tool` are
the edges of that graph: each one hands work (and, for the wait/poll tools,
eventually the result) to another agent whose own tools are not in the
caller's tool list at all.

So the guard evaluates the *effective* set: an agent's own grants, unioned
with the grants of every agent it can reach through one of those tools. An
agent's `delegates` field decides reach — a non-empty allowlist restricts it
to exactly those ids; empty or absent means unrestricted, and it can reach
every agent in the registry. `create_agent_tool` and `modify_agent_tool` are
not delegation edges: whatever they create or change is re-validated by this
same guard at save time, so there is nothing extra to add on top.

A combination that only closes through delegation is reported as a warning,
never a block: an agent whose own list is clean keeps saving and building, and
the editor and the audit show the path. Blocking it would refuse the seed
orchestrator, whose unrestricted `delegates` reaches the web searcher. To
remove the warning, narrow the agent's `delegates` list. A combination the
agent's own tools form still blocks as before. The report names the path, not
just the tools:

> Lethal trifecta: ingests untrusted content (via run_agent_tool -> swe_agent:
> run_shell) + reads private data (get_task) + can send data outside (via
> run_agent_tool -> swe_agent: run_shell).

The tables backing all of this live in `tools/capabilities.py`: `CAPABILITY_GRANTS`
(what a tool grants directly), `REVIEWED_NO_GRANT` (classified, grants nothing —
a write into the hub's own store, a control action, a piece of configuration),
and `DELEGATING_TOOLS` (the six edges above). Every catalog tool id ends up in
exactly one of them; a test enforces that, and `python -m agents.capability_guard`
reports zero unclassified tools alongside the current roster's violations.

## Group aliases

A tool list may name a group (`filesystem`, `task_management`, `service_ops`,
`entity_runs`, …) instead of its members. A group grants the union of what its
members grant.

## Approval gates, which are a different thing

Some tools refuse until `user_approved=True`: starting a flow, scenario, team or
loop, and every Service Agent action that stops something. That gate is about
spending money and destroying state, not about data. The refusal carries the
estimate or names what would be destroyed, so you approve with the number in
front of you.

That one is advisory: the agent carries the refusal to you and calls again once
you agree. A workspace can also *enforce* it, holding a destructive call until a
person answers on the task page, and run its own code around every tool call.
See [hooks](hooks.md).

Related: [agents](agents.md), [hooks](hooks.md), [system-agents](system-agents.md), [service-health](service-health.md), [web-logs](web-logs.md).
