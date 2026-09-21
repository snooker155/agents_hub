# Connectors

Services this hub reaches out to: a Telegram bot, a GitHub or GitLab account,
a local Blender install. **Connect → Connectors** in the sidebar.

The other direction from [connections](connections.md), where something of yours
runs elsewhere and reports in. Both answer "how do I attach something that is
not defined in here", which is why they share a group; each page says which way
it points in its first line.

These used to be tabs on the Settings page, next to model keys and log levels,
which is not where anyone looked for them. Links to `/settings/telegram`,
`/settings/git` and `/settings/blender` redirect to the new page.

## Telegram

A bot token, the chats bound to it, and whether the poller is running. A bound
chat runs one agent in one workspace, and the reply goes back to the chat. The
full behaviour, including what binding a chat means for trust, is in
[telegram](telegram.md).

## GitHub and GitLab

A personal access token per provider, plus a base URL for a self-hosted GitLab.
Used to read and write issues and pull requests from a [project](projects.md).

## Blender

The geometry engine agents model 3D objects with. Point it at an installed
Blender (the usual install locations are found automatically), cap how many
engines may run at once, and see the ones running now, including those started
by agent processes rather than by the backend. Agents never run Blender Python;
they call a fixed set of geometry operations. See [views](views.md).

## Gotchas

- **Each connector saves itself.** There is no page-level save button: the
  sections write through their own endpoints as you use them.
- **A token set in the environment wins.** A value in `.env` is not editable
  from the page, and the page says so rather than silently failing to save.
- **Stopping Blender daemons stops work in flight.** The engines listed are
  shared, and an agent mid-render loses its engine.

Related: [connections](connections.md), [telegram](telegram.md), [projects](projects.md), [views](views.md), [settings](settings.md).
