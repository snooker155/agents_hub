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

Related: [agents](agents.md), [system-agents](system-agents.md), [service-health](service-health.md), [web-logs](web-logs.md).
