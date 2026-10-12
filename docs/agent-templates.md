# Agent templates

A template is one ready agent to start from: a tuned prompt, a tool set that
passes the [capability guard](tools-and-capabilities.md), the connectors it
works best with, a default [outcome](outcomes.md) rubric and a few example
first messages. The model is the set of starter agents Anthropic offers for
Claude Managed Agents. Where a [kit](kits.md) installs a whole team as a
bundle, a template creates a single agent that you name and adjust first.

| Template | What it does | Connectors (optional in brackets) |
|---|---|---|
| `sre_agent`: SRE agent | Investigates an incident from logs, manifests and runbooks, names the root cause with evidence, proposes the fix as a pull request a person approves | none; a project with a git token |
| `ai_teammate`: AI teammate | Answers in the team chat from the team's docs and notes with sources, turns requests into tasks, remembers decisions | (slack, teams, discord, confluence, notion, google) |
| `github_issue_agent`: GitHub issue agent | Takes an issue synced as a task, finds the cause, writes a focused fix with a test, opens a pull request | none; a project with issue sync |
| `data_agent`: Data agent | Answers business questions from databases and sheets, shows every query, draws charts | databases (google) |
| `invoice_agent`: Invoice agent | Extracts invoice fields, checks arithmetic, duplicates and purchase orders, prepares records for approval | (databases, google) |
| `contract_review_agent`: Contract review agent | Reviews a contract against the team's playbook, flags clauses by severity, proposes redlines, writes a memo | (google, microsoft, confluence, notion) |
| `support_triage_agent`: Support triage agent | Classifies tickets by area, urgency and sentiment, finds the answer in the knowledge base, drafts a reply for a person to approve, escalates bugs as tasks | (confluence, notion, jira, linear) |
| `research_agent`: Research agent | Researches a question on the web and in files and memory, compares sources, writes a cited brief with confidence levels and open questions | none |
| `meeting_notes_agent`: Meeting notes agent | Turns a transcript into a summary, decisions, action items with owners and dates and open questions, creates tasks, remembers decisions | (google, microsoft, notion, confluence) |
| `recruiting_screener_agent`: Recruiting screener | Screens CVs against must-haves and nice-to-haves with evidence, flags gaps, ignores protected characteristics, writes a shortlist and interview questions | (google, microsoft) |
| `content_writer_agent`: Content writer | Drafts blog posts, newsletters and social posts in the brand voice from a brief, outline first, facts tied to your sources, headline variants | (google, notion, confluence) |
| `lead_research_agent`: Lead research agent | Researches an account or person from public sources and the CRM, writes an account brief and a personalised first outreach draft, never sends | (databases) |

## In the create wizard

Agents page, **Create agent**: the first choice, **Start from a template**,
is selected by default. The next step shows a card per template with its
connectors (green when connected in this workspace, amber when not). After
picking one, the identity step is filled from it: the id (the template id,
or `<id>_2` and up when that is taken), name and description. **Adjust the
prompt and tools** opens the full prompt and the tool list for editing. The
review step shows the default outcome rubric and example first messages.
Creating opens the new agent's page, and a notice repeats the template's
next steps (what to connect, what to write to memory).

The agent is an ordinary agent from then on: nothing links it back to the
template, so editing either one never changes the other.

## Over the API

- `GET /api/agent-templates?workspace=<ws>`: every template without its
  prompt, with each connector's configured status and a free agent id.
- `GET /api/agent-templates/{id}`: one template with its prompt.
- `POST /api/agent-templates/{id}/create`: body `{id, name, description,
  workspace, system_prompt, tools, capacity}`, every field optional. What is
  left out comes from the template. Requires the editor role in the
  workspace. The agent is created through the same handler as
  `POST /api/agents/create`, so the capability guard and workspace ownership
  apply unchanged, and then the template's rubric becomes its default
  outcome. The response carries `agent` and `next_steps`.

## Why some channels are left out

The research and lead research agents read web search results (untrusted)
and private data (files, memory, the CRM), so they get no tool that can send
data out: no `fetch_url`, where the agent picks the URL and a secret could
leave in it, and no notification or mail tools. The content writer cannot
open links for the same reason, so you paste or upload its sources. The
support triage agent drafts replies and never sends them.

A chat channel or a mailbox counts as untrusted input for the run. The SRE,
GitHub issue and invoice agents read private data and can send data out
(a push, a notification, a sheet append), so adding untrusted input would
form the lethal trifecta the guard refuses. Those templates name no chat
channel or mailbox for that reason; their approvals go through `ask_user`
and the hub's notifications.

## Writing a template

One file per template, `agents/templates/<id>.md`: YAML frontmatter, then
the system prompt as the body. Frontmatter fields: `id` (must match the file
name), `name`, `description`, `category`, `icon` (one plain word: siren,
users, github, database, receipt, scale, headset, telescope, notebook,
clipboard, pen, target; anything else gets a generic
glyph), `domain`, `tools`, `connectors: {required, optional}`, `outcome:
{rubric, max_iterations, threshold}`, `starters` and `next_steps`.
`agents.templates.validate_template` checks that every tool and connector
exists, that the tool set passes the guard and that the outcome is valid;
`tests/test_agent_templates.py` runs it over every shipped template. Add the
name and description in each language under `agentTemplates.items.<id>` in
the dashboard's locale files; the English text from the file is the
fallback.
