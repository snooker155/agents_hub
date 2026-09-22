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
Used to read issues from a [project](projects.md)'s repo, and to write a
branch, a commit and a pull or merge request back to it.

The write path is one tool, `git_publish`, plus the same action as a button on
the project page (`POST /api/projects/{id}/git/publish`), so an agent and a
person get the identical rules:

- **Branch naming.** Given no `branch`, it names one `agent/<task-key-or-slug>-
  <title-slug>`, so a run started from a tracked task lands on a branch that
  reads back to it. Given `branch`, that name is reused as-is, and an existing
  local branch of that name is checked out rather than recreated, so a retried
  publish after a partial failure lands on the same branch instead of piling
  up `agent/foo`, `agent/foo-2`, `agent/foo-3` for one task.
- **Never the default branch.** Publishing always goes through its own
  branch: a request that names the repo's default branch as `branch`, or asks
  for a pull/merge request from a branch onto itself, is refused before
  anything touches the repo.
- **Never a force push.** A push that the remote rejects (e.g. the branch
  moved) surfaces as an error instead of overwriting it.
- **Never these files.** `.env`, `*.pem`, `id_rsa*`, and anything the repo's
  own `.gitignore` already excludes are never staged by the commit, even if
  they fall inside the change being published.
- **The description, when left blank.** An empty `body` is built from the
  task the run belongs to: its title, description and result so far. It says
  plainly when there was no such task to draw from.
- **The approval gate.** `git_publish` is classified as a tool that can send
  data outside the system (a push leaves the workspace) and is always on the
  approval list, so a workspace with the approval gate turned on parks the
  task before the push runs, exactly as it would for a shell command or a
  destructive filesystem write.

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
