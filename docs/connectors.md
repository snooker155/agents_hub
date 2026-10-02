# Connectors

Services this hub reaches out to. **Connect → Connectors** in the sidebar.

The other direction from [connections](connections.md), where something of yours
runs elsewhere and reports in. [Watchers](watchers.md) reach out too, but only
to look: they poll and wake a proactive agent when something changes. Both
answer "how do I attach something not defined in here", which is why they share
a group; each page says which way it points.

These used to be tabs on the Settings page, which is not where anyone looked
for them. Links to `/settings/telegram`, `/settings/git` and `/settings/blender`
redirect here.

## Chat channels

Slack, Discord, Microsoft Teams and mail are places where agents run in response
to inbound messages. Each has an enabled switch, config fields (secrets are
write-only), an allowlist of chat keys, bindings from chats to workspaces and
agents or flows, and a status card. See [channels](channels.md).

## Git

GitHub, GitLab, Bitbucket Cloud and Gitea. A personal access token per
provider, plus a base URL for self-hosted GitLab or Gitea. Used to read issues
from a [project](projects.md)'s repo, and to write a branch, commit and pull or
merge request back to it. Repos, issue import and publish work for Bitbucket
Cloud and Gitea the same as for GitHub.

The `git_publish` tool and the same action as a button on the project page
enforce: no force pushes, never the default branch, never `.env` or `id_rsa*`,
and a description built from the task when left blank. It is classified as able
to send data outside and is always on the approval list.

## Telegram

A bot token, bound chats and whether the poller is running. A bound chat runs
one agent in one workspace and the reply goes back. The full behaviour,
including trust implications, is in [telegram](telegram.md).

## Issue trackers

Jira and Linear are linked to a [project](projects.md)'s repo: a sync is
idempotent and issues become tasks. See [trackers](trackers.md).

## Integrations

Google Workspace (Drive, Docs, Sheets, Calendar), Microsoft Graph (Outlook
Calendar), Notion and Confluence pages, and read-only database connections.
See [integrations](integrations.md).

## Blender

The geometry engine agents model 3D objects with. Point it at an installed
Blender (the usual install locations are found automatically), cap how many
engines may run at once, and see the ones running now. Agents never run Blender
Python; they call a fixed set of geometry operations. See [views](views.md).

## Gotchas

- **Each connector saves itself.** There is no page-level save button: the
  sections write through their own endpoints as you use them.
- **A token set in the environment wins.** A value in `.env` is not editable
  from the page, and the page says so rather than silently failing to save.
- **Stopping Blender daemons stops work in flight.** The engines listed are
  shared, and an agent mid-render loses its engine.

Related: [connections](connections.md), [channels](channels.md), [telegram](telegram.md), [trackers](trackers.md), [integrations](integrations.md), [projects](projects.md), [views](views.md), [settings](settings.md), [mcp](mcp.md), [notifications](notifications.md), [watchers](watchers.md).
