# Flow templates

A flow template is a ready flow to start from: a small pipeline of the hub's
own system agents (researcher, web searcher, analyst, writer, verifier,
decomposer, SWE agent, code reviewer, screener, sourcer), every step with its
task already written, plus six example requests in each interface language.
A flow made from one runs as soon as it is created. Where an
[agent template](agent-templates.md) is one agent, a flow template is a team
of steps with a fixed order.

| Template | Steps | What it does |
|---|---|---|
| `research_report`: Research report | researcher, writer, verifier | Researches a question in files, memory and on the web, writes a report with sources, checks every claim |
| `competitor_brief`: Competitor brief | researcher and web searcher in parallel, writer, verifier | What you know and the latest news, merged into a checked side by side brief |
| `metrics_report`: Metrics report | analyst, writer, verifier | Numbers from connected data, a report, figures recomputed |
| `fact_check`: Fact check | researcher and web searcher in parallel, verifier, writer | Claims rated against primary sources, a corrected text |
| `blog_post`: Blog post | researcher, writer, verifier, writer | Research, draft in your voice, fact check, final edit with headlines |
| `code_change`: Code change with review | decomposer, SWE agent, code reviewer | Plan, implement, review against the plan |
| `pr_review`: Pull request review | two code reviewers in parallel, writer | Correctness and security reviews merged into one comment |
| `incident_postmortem`: Incident postmortem | researcher and analyst in parallel, writer, verifier | Timeline and impact, a blameless postmortem with action items |
| `candidate_screening`: Candidate screening | screener, verifier, writer | CVs scored with evidence, scores checked, a shortlist with interview questions |
| `vendor_shortlist`: Vendor shortlist | sourcer, researcher, screener, writer | Vendors found, researched, scored, a recommendation memo |
| `job_search`: Job search | sourcer, screener, writer | Openings found, scored against your CV, a cover letter for the best |
| `product_spec`: Product spec | researcher, decomposer, writer | Context, requirements and work items, a spec |

## In the Create flow dialog

Agent Flows, **Create flow** opens a dialog in two steps. **Template**: a
**Blank canvas** card and a card per template, four across, each with its
steps in order; Next, or a double click on a card, goes on. **Name and
description**: filled from the template in the interface language, both can
be changed; going back to pick another template refills only what was not
edited. **Create from template** makes the flow and opens its run page, where
the empty chat offers the template's example requests. A blank canvas opens
in the editor as before.

Step labels are written in the interface language of the person creating the
flow; the steps' tasks are English, since they are instructions to a model.
The flow is an ordinary flow from then on: nothing links it back to the
template.

If this hub is missing one of the system agents a template runs, creating
registers it from `bootstrap/agents.json` first, the way installing a
[kit](kits.md) does.

## Example requests

Every flow can carry example requests (`examples` on the flow, by language:
`{en: [...], ru: [...], de: [...]}`; a plain list counts as English). The
empty chat with the flow shows them as buttons, on its run page and on the
Chat page in flow mode; a click puts the request in the message box to be
edited and sent. A template's examples are copied into the flow. The
editor's **Flow settings** has an **Example requests** field, one per line,
for the current interface language. A flow with none of its own shows the
examples of the agent its first step runs, since that step receives the
message.

## Over the API

- `GET /api/flow-templates?lang=<en|ru|de>`: every template with its steps,
  the agents it runs and its examples in that language.
- `GET /api/flow-templates/{id}?lang=`: one template.
- `POST /api/flow-templates/{id}/create`: body `{name, description,
  workspace, lang}`, every field optional. Requires the editor role in the
  workspace (default when none). Without `workspace` the flow is shared, like
  a blank one from the dialog. Returns the flow.
- `GET /api/flows/{id}/examples?lang=`: `{source, examples}`, where
  `source` is `flow`, `agent` (then `agent_id` names it) or null.
- `PUT /api/flows/{id}` accepts `examples`.

## Writing a template

One file per template, `flow/templates/<id>.yaml`: `id` (must match the file
name), `name`, `description`, `category`, `icon` (one plain word: search,
target, chart, check, pen, code, git, siren, users, store, briefcase,
clipboard; anything else gets a generic glyph), `examples` (six per
language), `nodes` (each `key`, `agent`, `label` by language and `task`) and
`edges` (`from`, `to` by key). Steps with no incoming edge start together.

Only a first step sees the person's message; later steps see their
predecessors' output. So every first step's task asks it to begin with a
"Request" section restating what was asked, which carries the request down
the flow.

`flow.templates.validate_template` checks that every agent is a shipped
system agent, every step has a task, the graph passes the engine's
preflight, and each language has six distinct examples and a label per step;
`tests/test_flow_templates.py` runs it over every shipped template. Add the
name and description in each language under `flowTemplates.items.<id>` in
the dashboard's locale files; the English text from the file is the
fallback.
