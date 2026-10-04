# Help

The **Help** button in the header, on every page. It opens a panel with the
**Support** agent, for when you are lost: you do not know what a feature is for,
where it lives, or what to do next.

## What it is for

- "What can I do here?" on any page.
- "What should I set up next?": it reads what is configured and what is missing
  and answers with the step that unblocks the rest.
- "How do I connect Slack?", "Where do I add an API key?", "What is a loop?"

Every answer ends with one to three next steps, each a link to the page where
it is done. A link in the panel opens that page in place; the panel stays open.
The **Take the tour** button in the panel starts the welcome tour, and the
agent can offer it as a link too.

## What the agent is given

- **Where you are**: the page's title, its URL and the active workspace.
- **The install as a newcomer sees it**: whether a model provider is
  configured, which models are enabled, how many agents there are and which are
  your own, workspaces, chats and tasks so far, channels, connections, database
  connections and watchers, and whether you took the tour. The server reads this
  at the moment you ask; only counts and names travel, never a key or a token.
- **The documentation**: the same corpus as the Docs page, which it searches
  before it answers.
- **Read only tools**: health and the doctor's checks, models, agents, tasks,
  schedules, flows, teams, loops, scenarios, projects and database connections.

The page and the snapshot are data, not instructions, and the prompt says so.

## How it differs from the page chat

The [page chat](page-chat.md) is the round button in the bottom right corner. It
is about the records the page is showing and keeps one thread per page. Help is
about the product, and keeps **one thread per user**: it follows you from page
to page. Clear starts a fresh session, as in every other chat.

## Gotchas

- Support changes nothing. It holds no tool that creates, edits, runs or
  deletes, so it tells you where to do it, or which agent does it: the Main
  Agent in [Chat](chat.md) does the work.
- Never paste an API key into it. Keys go on the Settings page.
- Every turn is an ordinary run. It appears in
  [Messages](sessions-and-runs.md) with its log and its cost.
- In the public demo the panel answers with a fixed reply: there is no model
  behind the demo.

Related: [system-agents](system-agents.md), [overview](overview.md).
