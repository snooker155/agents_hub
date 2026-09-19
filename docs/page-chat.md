# Page chat

The button in the bottom-right corner of every page. It opens a chat about the
page you are on, and what it is about changes as you move around the product.

## Two kinds of page

- **A page with a chat of its own** — a scenario, a loop, a team, an agent's
  definition, a memory pool, the service health page, the eval sets, the project
  registry, a world. That chat has its own agent, and that agent is the point of
  it, so the panel shows *that* conversation rather than a general one. Nothing
  about it changes: same thread, same agent, same history. Only where it is
  drawn does, and the page's own column or tab steps aside while the panel holds
  it. The page's chat button brings it back into the page.
- **Every other page** — one fixed assistant, the same everywhere, holding the
  records the page is showing. There is nothing to pick and nothing to
  configure.

## What the assistant is given

- **Where you are** — the page's title and its URL.
- **The records on the page** — sent as pointers (`kind` + `id`) and rendered
  server-side from the same catalog the chat composer's entity picker uses, so a
  task page hands it that task, a view page that view, and so on. A record that
  was deleted since the page loaded drops out instead of breaking the turn.
- **What the page is showing** — some pages describe their own state in words
  (the active filter, the period, the totals on screen). This is data about the
  screen, not instructions, and the prompt says so.
- **Its tools** — for anything the blocks above do not carry. The active
  workspace and project travel with the turn, so "the open tasks" means the ones
  you are looking at.

## One thread per page

The conversation is keyed on what the page is about: returning to the same task
reopens the same conversation, and moving to another one starts its own. Clear
starts a fresh session, exactly as it does in the other chats.

## Gotchas

- The assistant never creates, changes, runs or deletes anything without a clear
  yes in the conversation to that exact action.
- There is no button on the Chat page: that page is already a chat.
- Every turn is an ordinary run. It appears in [Messages](sessions-and-runs.md)
  with its log and its cost, like any other.
