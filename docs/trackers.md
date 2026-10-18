# Issue trackers

Jira, mirrored into a [project](projects.md)'s tasks the way GitLab issues
are. A sync is idempotent: an issue maps to one task through its external
source (provider, remote id, number, key, URL, state, status, labels). A
closed issue closes a task only while it is todo or ready; a reopened issue
reopens a done task. Nothing else moves a task someone is working on.
Credentials are saved on the **Connectors** page, **Trackers** tab, per
workspace: the default workspace's Jira works everywhere, and a workspace that
defines its own uses that one alone ([connectors](connectors.md)).

## Jira

Fields: `base_url` (the site, e.g. `https://acme.atlassian.net`; a host
outside `*.atlassian.net` is checked against private addresses), `email` and
`api_token` (REST v3). Test calls `myself` and shows the display name.

## Linking a project

On the project page, **Repository** tab, "Issue tracker" card: pick Jira and
enter the project key. A picker lists the keys from
`GET /api/trackers/jira/projects`. Save and click Sync. The sync is
idempotent: issues from the tracker become tasks on every sync, and the sync
runs in the project's workspace, whoever starts it.

API: `GET|PUT /api/trackers/projects/{id}` (`{provider, remote_id, url}`),
`POST /api/trackers/projects/{id}/sync`.

## Tools

- `tracker_list_issues(provider, remote_id, state="open", limit=50)` (reads
  private, ingests untrusted)
- `tracker_get_issue(provider, key)` (reads private, ingests untrusted)
- `tracker_sync(project)`: project id or unique name (reads private, ingests
  untrusted)
- `tracker_comment(provider, key, text)` (can exfiltrate)
- `tracker_transition(provider, key, status)` where status is matched against
  the tracker's own workflow states, case insensitive (can exfiltrate)
- `tracker_create_issue(provider, remote_id, title, body, labels)` (can
  exfiltrate)

`provider` is `jira`. Issue bodies returned to the agent are wrapped as
untrusted text. The three writing tools are classified as able to send data
out; the reading ones as ingesting untrusted text and reading private data.

## Gotchas

- **Closed means the status category, not the status name.** A Jira issue is
  closed when its status sits in the "Done" category, whatever the status is
  called. The tracker's own status name is kept on the task's external source.
- **A transition must exist in the workflow.** `tracker_transition` picks the
  transition whose target status matches the name you give; a status the
  issue cannot reach from where it is fails with the transitions it can take.

Related: [connectors](connectors.md), [projects](projects.md), [tasks](tasks.md), [tools-and-capabilities](tools-and-capabilities.md).
